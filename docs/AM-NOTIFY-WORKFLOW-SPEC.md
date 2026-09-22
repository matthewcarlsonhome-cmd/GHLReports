# Spec: Account Health → Account Manager notifications

Status: ready to build. Written 2026-09-22 for a Codex build session (repo
access plus a Chrome connection to GoHighLevel). Owner: Matthew Carlson.

This replaces the per-flag "Phase 1 bridge" in `collector/automation.py`
(one POST per flag, no reminders, no cleanup). That bridge was never switched
on: `automation_sends` is empty and `AUTOMATION_WEBHOOKS` defaults to `off`.

---

## 0. Hard rules (read first, apply to every step)

1. **No writes to client subaccounts, ever.** The collector only reads client
   accounts. The only outbound call is one HTTPS POST to a workflow in
   **SSP's own GHL account** ("Small Screen Producer", the row flagged
   `is_parent` in `subaccounts`). The Chrome session works only inside SSP's own account.
2. **No email to anyone without Matthew's explicit go for that send.** Build
   everything in **Draft**. Do not publish the workflow. Do not click "Test
   workflow" on any path that emails a person. Matthew publishes.
3. **The webhook URL is a secret.** Never paste it into chat, a commit, a
   file, a log, a screenshot, or a ticket. Matthew copies it from GHL into
   Render's `AUTOMATION_WEBHOOK_URL` himself.
4. **No customer PII in the payload.** No lead or customer names, phones,
   emails, message text, or deal names (GHL deal names are customer names).
   Account (client business) names, counts, form names and stage names are
   fine. `collector/tests/test_pii.py` must keep passing.
5. **Recipients:** only `@smallscreenproducer.com` addresses. Michael's
   accounts are out of scope for the pilot (see §3.6).
6. **AM wording:** plain language, short, no tech terms (never "API", "PIT",
   "webhook", "flag", "payload"). The AM needs to know one thing: does this
   client need a call or meeting, and about what.

---

## 1. Recommendation in one paragraph

**One workflow, not one per subaccount.** All logic (what to say, when to
say it, when to stop saying it) lives in the collector, which already has
every account's history and is tested. GHL is a dumb messenger: one Inbound
Webhook workflow in SSP's account receives **one message per client account
per day at most**, already written in plain language, and emails it to that
account's AM. Suppression and cleanup are a small state table in Supabase
(`alert_state`), not GHL tags or tasks, so there is nothing in GHL to clean
up and nothing that can drift. Per-subaccount workflows would mean 20 copies
of the same logic that nobody can test or change in one place.

Why not keep state in GHL (tags, tasks, custom fields on a contact per
client)? It can work (Phase 2, §6), but it needs one SSP-side contact per
client account (none exist today: `ssp_client_contact_id` is empty for all
20 accounts), and GHL workflows cannot loop, compare dates well, or be unit
tested. Start without it.

---

## 2. What the AM receives

At most **one email per client account per day**, only when something
changed that the AM should act on. Example:

> **Subject:** Central Jersey Pool & Spas: 2 things to look at
>
> Hi Lauren,
>
> Two things need attention at Central Jersey Pool & Spas:
>
> 1. NEW: Leads are down 55% from their usual week while similar clients held
>    steady. Worth a call before they call you.
> 2. STILL OPEN (day 8): The "Pool Opening + Closing - FB Ad" form hasn't
>    had a lead since Jun 8. Check whether the ad is still running.
>
> Now fine: new leads have owners again.
>
> Full picture: https://mlhaccountreports.netlify.app/account/A6WeIeAP9Fi2CuCApyce
>
> To stop reminders about one of these, open the link and press
> "Acknowledge".

The Monday digest (`collector/digest.py`, SMTP) stays as the weekly summary.
This workflow is for the in-between days.

---

## 3. Part A: collector changes (repo, Python)

Build this first; it is testable without GHL. Files: `collector/automation.py`
(rewrite the send path), `collector/store.py`, a new migration, tests.

### 3.1 Which issues can message an AM

Only issues that mean "talk to the client" or "fix something before the
client notices". Everything else stays on the dashboard and in the Monday
digest.

| Code | Messages AM? | Min severity | Confirm runs | Remind every | Max reminders |
|---|---|---|---|---|---|
| INTEGRATION_SUSPECT | yes | red | 2 | 3 days | 3 |
| LEADS_ZERO | yes | red | 2 | 3 days | 3 |
| FORM_SILENT | yes | red | 2 | 3 days | 3 |
| SOCIAL_DISCONNECTED | yes | red | 1 | 7 days | 2 |
| LEADS_DROP | yes | red | 2 | 7 days | 2 |
| SOURCE_DROP | yes | red | 2 | 7 days | 1 |
| FORM_WENT_SILENT | yes | amber | 2 | 14 days | 1 |
| FORM_CHECK_MISSED | yes | amber | 2 | 7 days | 2 |
| SLOW_RESPONSE | yes | red | 2 | 7 days | 2 |
| PIPELINE_FROZEN | yes | red | 2 | 14 days | 1 |
| NO_CLIENT_TOUCH | yes | red | 1 | 7 days | 2 |
| RENEWAL_SOON | yes | amber | 1 | 14 days | 1 |
| NO_DELIVERY | yes (internal fix) | amber | 1 | 7 days | 2 |
| everything else (CONVOS_WAITING, UNASSIGNED_LEADS, PIPELINE_HYGIENE, STALE_PIPELINE, LEADS_UNREACHABLE, LEADS_DROP_SEASONAL, HIGH_NOSHOW, WORKFLOWS_NONE_PUBLISHED, all `info`) | no | | | | |

Keep this table as a frozen dict in `automation.py` (`NOTIFY_RULES`), same
reasoning as today's `DAILY_CODES`: widening what can email the team is a
reviewed commit.

Why "confirm runs = 2": in the last 28 days of stored flags, most episodes
lasted one day and cleared on their own (SOURCE_DROP 16 of 24 episodes,
CONVOS_WAITING 16 of 36, FORM_WENT_SILENT 14 of 34). Waiting one more run
removes most false alarms at the cost of a one-day delay.

Expected volume with the table above: roughly 1 to 3 emails per AM per
weekday, estimated from Aug 25 to Sep 22 flag history. Verify with the dry
run (§3.7) before going live.

### 3.2 Issue identity

An issue is `(location_id, code, entity_key)`. `entity_key` is
`entity_name` for form, survey and source codes (so two silent forms are two
issues), otherwise empty. Never use a person-shaped entity (contact,
conversation, opportunity) as a key or in text; `SAFE_ENTITY_TYPES` stays.

For FORM_WENT_SILENT and FORM_CHECK_MISSED, today's flag bundles several
forms in `detail`. Split per form using `details["form_health"]` in the run
result, so each form is its own issue.

### 3.3 State table (new migration `0014_alert_state.sql`)

```sql
create table public.alert_state (
  location_id    text not null references public.subaccounts(location_id) on delete cascade,
  code           text not null,
  entity_key     text not null default '',
  status         text not null check (status in ('pending','open','resolved')),
  severity       text not null,
  first_seen     date not null,     -- first run the flag appeared (this episode)
  last_seen      date not null,     -- latest run the flag was present
  seen_runs      int  not null default 1,   -- consecutive runs present
  absent_runs    int  not null default 0,   -- consecutive runs absent
  notified_at    date,              -- first AM message this episode
  last_notified  date,              -- latest AM message (new, reminder, escalation)
  reminders      int  not null default 0,
  notified_severity text,
  resolved_at    date,
  resolve_told   boolean not null default false,
  summary        text,              -- the plain-language line last used
  primary key (location_id, code, entity_key)
);
alter table public.alert_state enable row level security;
alter table public.alert_state force row level security;
create policy alert_state_staff_read on public.alert_state
  for select to authenticated using (public.is_staff());
grant select on public.alert_state to authenticated;
```

Writes happen only from the collector (service role). Keep
`automation_sends` as the audit log; change its unit from "one flag" to "one
account message": add columns `message_kind text`, `item_codes text[]`, and
relax the unique index to `(location_id, snapshot_date)` where live and sent.

### 3.4 The daily state machine (runs once per nightly run, per MLH account)

For each issue present today (passes §3.1 filter):
1. No row, or row `resolved` more than 7 days ago → insert `pending`,
   `seen_runs=1`, `first_seen=today`.
2. Row `resolved` within 7 days (flapping) → set back to `open`, keep
   `notified_at`, do **not** send "new" again; it can only message via
   escalation or the reminder clock.
3. Otherwise increment `seen_runs`, reset `absent_runs=0`, update
   `severity`, `last_seen`.
4. `pending` and `seen_runs >= confirm_runs` → `open`, and queue a **NEW**
   item.
5. `open`, and severity rose (amber → red) above `notified_severity` →
   queue **ESCALATED** (does not count as a reminder).
6. `open`, `today - last_notified >= remind_every` and
   `reminders < max_reminders` → queue **STILL OPEN (day N)**, N =
   `today - first_seen + 1`. After the last reminder the issue goes quiet
   and lives only on the dashboard and the Monday digest.

For each `pending` or `open` row NOT present today:
7. Increment `absent_runs`. `pending` rows are deleted at once (never
   confirmed, nobody was told).
8. `open` and `absent_runs >= 2` → `resolved`, `resolved_at=today`. If the
   AM had been told (`notified_at` not null), queue a **NOW FINE** line.
   Resolution lines never send an email alone; they ride along with the
   next message for that account, or with the next Monday digest, whichever
   comes first (`resolve_told` marks it delivered).

Suppression on top:
9. **Acknowledged on the dashboard:** if `flag_acks` has a row for
   `(location_id, code)` with `snooze_until >= today`, drop NEW, STILL OPEN
   and NOW FINE items for that code. ESCALATED still sends (red is new
   information).
10. **Account not using MLH** (`automation.uses_mlh` false, or the latest
    snapshot has `client_users == 0`): no messages. State still updates.
11. **First run after deploy:** seed `alert_state` from today's flags with
    `status='open'` and `notified_at=today`, send nothing (same intent as
    today's "first run is silent"). Result: only changes after go-live
    email anyone.
12. **Gate failed / account held** (`gate_passed` false): skip the account
    entirely that day, no state change. Bad data must not open or close
    issues.
13. **Caps:** at most 1 message per account per day; at most 8 messages per
    AM per day (the rest roll to tomorrow, oldest first); a run that would
    send more than 25 messages in total sends none and logs it (a GHL
    outage or bad deploy, not 25 real problems).

An account gets a message today only if it has at least one NEW,
ESCALATED or STILL OPEN item. NOW FINE lines never trigger a message on
their own.

### 3.5 Payload (one POST per account message)

Flat JSON: GHL maps `{{inboundWebhookRequest.<field>}}` and cannot read
nested objects or loop arrays, so pre-render everything.

```json
{
  "event": "am_account_health",
  "schema_version": "2",
  "is_test": "false",
  "send_id": "A6WeIeAP9Fi2CuCApyce-2026-09-23",
  "run_date": "2026-09-23",
  "account_name": "Central Jersey Pool & Spas",
  "location_id": "A6WeIeAP9Fi2CuCApyce",
  "am_email": "ldegner@smallscreenproducer.com",
  "am_first_name": "Lauren",
  "route": "lauren",
  "urgency": "high",
  "item_count": "2",
  "subject": "Central Jersey Pool & Spas: 2 things to look at",
  "body_text": "Hi Lauren,\n\nTwo things need attention at ... (full email, plain text)",
  "body_html": "<p>Hi Lauren,</p><ol><li>...</li></ol>...",
  "item_1": "NEW: Leads are down 55% from their usual week ...",
  "item_2": "STILL OPEN (day 8): The \"Pool Opening + Closing - FB Ad\" form ...",
  "item_3": "", "item_4": "", "item_5": "",
  "resolved_text": "Now fine: new leads have owners again.",
  "dashboard_url": "https://mlhaccountreports.netlify.app/account/A6WeIeAP9Fi2CuCApyce"
}
```

Field rules:
- All values are strings (GHL If/Else compares strings reliably; numbers
  as strings avoid type surprises).
- `route` is a fixed key the workflow branches on, derived from
  `am_email`: `lauren`, `lisa`, `matthew`, or `other`. Mapping lives in
  code (`AM_ROUTES`), so adding an AM is one code line plus one workflow
  branch.
- `urgency` is `high` when any item is red, else `normal`.
- `body_text` / `body_html` are the complete email; the workflow just drops
  them in. `item_1..5` exist so a future SMS or task can use short lines.
- `send_id` is `location_id-run_date`; the workflow does not need it, it is
  there for tracing a message back to `automation_sends`.
- `is_test` is `"true"` only from `--send-test`.

Item sentence templates (plain words, one sentence each, from the flag's
numbers, not its tech `action` text). Examples to implement per code:
- LEADS_DROP: "Leads are down {pct}% from their usual week while similar clients held steady. Worth a call before they call you."
- LEADS_ZERO / INTEGRATION_SUSPECT: "No new leads came in this week, which is unusual for them. Something may be broken (form, phone or ads); check before calling."
- FORM_WENT_SILENT: "The \"{form}\" form hasn't had a lead since {date}. Check whether the page or ad behind it is still running."
- FORM_SILENT: "None of their website forms sent a lead this week, even though other leads still arrive. A form is probably broken."
- FORM_CHECK_MISSED: "Our weekly test of the \"{form}\" form didn't arrive. Please submit it by hand to confirm it works."
- SOURCE_DROP: "Leads from {source} stopped (usually {avg} a week). Check that ad or channel."
- SLOW_RESPONSE: "{n} new leads have waited more than a day with no reply. Worth asking who is following up."
- PIPELINE_FROZEN: "None of their {n} open deals moved in 30 days. Good moment for a pipeline review meeting."
- SOCIAL_DISCONNECTED: "A social account got disconnected, so scheduled posts are not going out. Needs reconnecting."
- NO_DELIVERY: "We haven't published anything for them in {n} days."
- NO_CLIENT_TOUCH: "No contact with this client in {n} days and nothing booked. Time for a check-in."
- RENEWAL_SOON: "Their renewal is in {n} days. Book the review."

Keep the wording in one dict so Matthew can edit it without touching logic.

### 3.6 Recipient safety

- `AM_NOTIFY_ALLOWLIST` env var (comma list). Only these `am_email`s get
  messages; anything else is logged and skipped. Pilot value:
  `lhoffman@smallscreenproducer.com,ldegner@smallscreenproducer.com,mcarlson@smallscreenproducer.com`.
  Michael (`madams@`) is excluded until the pilot expands.
- `AM_NOTIFY_REDIRECT` env var (optional). When set, every message's
  `am_email`, `route` and greeting are rewritten to that address, with the
  real AM's name added to the subject ("[for Lauren]"). Used for the
  shadow week (§5.3).
- Reject any address not ending in `@smallscreenproducer.com` (reuse
  `digest.STAFF_DOMAIN`).

### 3.7 Modes and switches (keep the existing ones)

- `AUTOMATION_WEBHOOKS=off|dry|on` stays the kill switch. `dry` runs the
  full state machine, writes `alert_state`, logs every message that would
  have gone out (subject + items), and POSTs nothing.
- `--send-test` posts one sample with `is_test:"true"` and a fake account
  ("Sample Pool & Spa", location `SAMPLE`). It must never reach a person
  (the workflow's first step ends test runs, §4.3).
- New `--notify-preview` prints, for today's stored data, the messages
  each AM would get, without touching state. For Matthew's review.

### 3.8 Tests (must pass before any GHL work is published)

- confirm-2: a flag on one run only never messages.
- one message per account per day, bundling several issues.
- reminder clock and max reminders; escalation amber→red sends once.
- flapping within 7 days does not resend NEW.
- resolution after 2 absent runs; NOW FINE rides along, never alone.
- ack with snooze suppresses NEW/STILL OPEN/NOW FINE but not ESCALATED.
- gate-failed day changes nothing.
- first run seeds silently.
- caps (per account, per AM, 25-message breaker).
- allowlist and redirect; non-SSP domain rejected.
- payload: all string values, no nested objects, no PII (extend
  `test_pii.py`), `is_test` only from `--send-test`.

---

## 4. Part B: the GHL workflow (Chrome, SSP account only)

### 4.1 Before starting
- Log in to the SSP agency view and switch to the **Small Screen Producer**
  subaccount (SSP's own). Confirm the account name on screen before any
  change. If anything shows a client name, stop.
- Confirm these users exist in the SSP subaccount (Settings → My Staff):
  Lauren Degner, Lisa Hoffman, Matthew Carlson. Note their exact display
  names for the "Particular users" picker.

### 4.2 Create
Automation → Workflows → **+ Create Workflow → Start from scratch**.
Name: `Account Health → AM notice`. Folder: `Account Health` (create it).
Leave it in **Draft** for the whole build.

Settings tab: Allow re-entry **on** (the same webhook fires many times a
day for different accounts). Stop on response: off.

### 4.3 Trigger
Add Trigger → **Inbound Webhook**. Copy the URL and give it to Matthew by
the agreed private route (he pastes it into Render's
`AUTOMATION_WEBHOOK_URL`). Do not store it anywhere else.

Matthew then runs the collector once with `COLLECTOR_ARGS=--send-test` on
Render (Trigger Run), and clears it after. In GHL press **Fetch sample
requests**, pick the newest (it has `is_test = true`), save the trigger.
All fields in §3.5 must now appear under Inbound Webhook in the value
picker. If some are missing, stop and report which.

### 4.4 Steps, in order

1. **If/Else "Test?"**: branch A when `{{inboundWebhookRequest.is_test}}`
   is `true` → no actions (ends). Else → continue. This guard stays
   forever; it is what makes test sends safe.
2. **If/Else "Valid?"**: continue only when `event` is
   `am_account_health` and `schema_version` is `2`. Other branch → an
   **Internal Notification (In-app)** to Matthew only, text
   "Account Health sent something unexpected", then end.
3. **If/Else "Route"** on `{{inboundWebhookRequest.route}}` with branches
   `lauren`, `lisa`, `matthew`, and None (else).
4. In each named branch: **Internal Notification → Email**, recipient
   type **Particular users** → that one person.
   - From name: `Account Health`
   - Subject: `{{inboundWebhookRequest.subject}}`
   - Message: `{{inboundWebhookRequest.body_html}}` (use the plain-text
     field instead if the editor escapes HTML; check in the preview)
   - No CC/BCC.
5. None branch (unknown AM): Internal Notification (In-app) to Matthew:
   "Account Health: no AM route for {{inboundWebhookRequest.account_name}}".
   No email.
6. Optional, only if Matthew asks: in the `lauren`/`lisa` branches, a
   second Internal Notification (In-app) to the same user when
   `urgency` is `high`.

Nothing else. No contacts created, no tags, no tasks, no opportunities.

### 4.5 Build-time checks (things this spec could not verify)

GHL help pages confirm: Inbound Webhook accepts POST/PUT/GET with a JSON
body and exposes fields for mapping; Internal Notification email offers
All users / Particular users / Assigned user, and can run without a contact
as long as it does not use contact fields. Not verified, check in the UI
and report back before continuing:
- V1: inbound webhook fields render inside an Internal Notification email
  **with no contact** in the workflow. Check with the Preview inside the
  action (not a live test). If they do not render, use the fallback in §6.1.
- V2: HTML in a mapped field renders as HTML in the email body. If not,
  map `body_text`.
- V3: Fetch sample requests works while the workflow is in Draft.
- V4: Inbound Webhook is a premium trigger billed per execution in this
  account. Expected volume is well under 100 runs a month; note the price
  shown.

### 4.6 Hand back to Matthew
Screenshot of the canvas (no URL visible), the list of V1–V4 results, and
the exact user names picked in each branch. Leave the workflow in Draft.

---

## 5. Go-live sequence (Matthew)

1. Merge and deploy Part A. Apply migration 0014.
2. Set `AUTOMATION_WEBHOOKS=dry` for 3 to 5 nightly runs. Read the logs:
   the would-send list per AM. Tune §3.1 if it is too chatty.
3. **Shadow week:** set `AM_NOTIFY_REDIRECT=mcarlson@smallscreenproducer.com`,
   `AUTOMATION_WEBHOOKS=on`, publish the workflow. Only Matthew gets
   messages, labeled with the intended AM.
4. Tell Lisa and Lauren what is coming (short, plain note), then clear
   `AM_NOTIFY_REDIRECT`. The first real send is whatever the next nightly
   run produces; the first-run seeding means old issues do not flood them.
5. Kill switch at any time: `AUTOMATION_WEBHOOKS=off` in Render (no deploy
   needed), or set the workflow back to Draft.

---

## 6. Later options (not in the pilot)

### 6.1 Fallback if contactless email does not work (V1 fails)
Create one contact in the SSP account, "Account Health Alerts",
email `health-alerts@smallscreenproducer.com` (a group or alias, never a
client). Add **Find Contact** (match on that email, constant value) as the
first step after the Test guard; all later steps run on that contact.
Internal Notifications then have a contact in context. No other change.

### 6.2 Phase 2: account records in GHL
If the team wants the issues visible inside GHL (a "Client Health" pipeline
or tags on a per-client contact in SSP's account): create one contact per
client account with a custom field `Health Location ID`, store its id in
`subaccounts.ssp_client_contact_id`, and extend the payload with
`open_items_text` and `open_count`. The workflow does Find Contact by
`Health Location ID` → Update custom fields (overwrite, so resolved issues
disappear on their own) → add tag `health-needs-call` when `open_count > 0`,
remove it when `0`. The collector would then also send a message when an
account's last issue resolves, so the tag clears. Overwriting full state
every time is the cleanup mechanism: no per-issue delete step can be missed.

### 6.3 SMS for red issues
Add an Internal Notification SMS in each route branch when `urgency` is
`high`, if AMs want it.
