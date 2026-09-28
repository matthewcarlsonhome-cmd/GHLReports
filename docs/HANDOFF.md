# Handoff: GHL Account Health Dashboard

State as of **2026-09-28** (pilot workflow and Render settings updated after Matthew's sample run; earlier database checks at 16:10 to 16:20 UTC).
This is the file a new session starts from. Rules for agents are in
`/AGENTS.md`; read them first. Detail lives in `CODE-MAP.md` (how the Python
works), `AM-NOTIFY-WORKFLOW-SPEC.md` (AM notes), `SECURITY-SPEC.md`,
`ARCHITECTURE.md`, `GO-LIVE.md`, `FORM-MONITORING.md`, `ACCOUNT-USAGE.md`.

## 1. What this is

A nightly, **read-only** health check across Small Screen Producer's (SSP)
GoHighLevel client subaccounts. It collects each account's CRM data, flags
problems (leads left uncontacted, customers waiting for replies, lead flow
dropping, forms going quiet, pipelines stalling), and tells the account
managers (AMs) which clients need a call. Owner: Matthew Carlson
(mcarlson@smallscreenproducer.com). AMs: Lauren Degner (`ldegner@`), Lisa
Hoffman (`lhoffman@`), Michael (`madams@`, out of scope for the pilot).

## 2. Where things run

| Piece | Where | Status (2026-09-28) |
|---|---|---|
| Nightly collector | **Render** cron `ghl-health-collector`, 05:30 CT (`30 10 * * *` UTC), deploys from the default branch | **Live.** Runs 40 to 43 (Sep 25 to 28) all ok, 21 locations each, about 10 minutes. Run 44 was a manual Trigger Run at 10:58 CT, ok, and it **mailed the Monday digest to the AMs** (see §3.3). |
| GitHub Actions `collector.yml`, `tagchecker.yml`, `reports.yml` | GitHub | **Not configured.** No secrets, so the scheduled collector and tag checker runs fail every day (GitHub emails Matthew). The tag checker has never produced a row (`tag_checks` is empty). The cutover from Render never happened. |
| Database | Supabase `tpavdifpsevkrubplyrg` (Postgres, Vault for PITs, Auth) | Migrations 0001 to 0015 applied. No edge functions. |
| Dashboard | Netlify, https://mlhaccountreports.netlify.app (`/account/<location_id>`) | Live, deploys on every push. |
| Monday digest | the collector, over SMTP (`SMTP_USER` = Matthew's Workspace account, the sender) | Since commit `5c0a894`, goes to **nobody** unless `DIGEST_REDIRECT` or `DIGEST_ALLOWLIST` is set. |
| AM notes | the collector POSTs one message per client account to the GHL Inbound Webhook workflow "MC Account Health - Alerts" in SSP's own subaccount (`ZnckuEDPIcWu8fn72ppi`), which emails the AM | **Dry mode configured; sends nothing.** Workflow rebuilt and saved in Draft; rendering/contactless validation remains open (§3.9). |

## 3. Current state

### 3.1 Accounts (live, 16:18 UTC)
- 28 active rows: SSP's own parent account (Matthew), **20 client accounts
  collected nightly** (Lauren 10, Lisa 7, Michael 2, "Michael / Dada (FB)"
  1), and 7 marked not using MLH (`mlh_status`), which are not pulled from
  GHL (Lisa 2, Michael 4, Michael / Dada 1). 3 inactive rows.
- `subaccounts.am_email` points at the real AMs. **Keep it that way**
  (Matthew, 2026-09-28): who actually receives mail is decided by the Render
  settings in §3.4, so switching the AMs on later is a settings change, not a
  data change.

### 3.2 AM notes pilot
- Six accounts, all on **T2 leads going cold** and **T3 customers left
  waiting**, with `thresholds.slow_response_min = 2`:
  Lauren: Flohr Pools, Central Jersey Pool & Spas. Lisa: Pettis Pools &
  Patio, Liverpool Pool & Spa, McKinney Custom Pools, AAA Spa & Pool
  Services (her types are provisional until she picks).
- T4 website capture was dropped (migration 0015): form problems go to the
  GHL team, not AM notes, for now.
- Earlier live check after run 44: `alert_state` 0 rows, `automation_sends` 0 rows,
  no account has `alert_tracking_since`. So the notes have never run in dry
  or on mode: `AUTOMATION_WEBHOOKS` is off in Render, or its settings were
  rejected (the run log would then have a `notices: ...; nothing sent`
  line). Dry mode was configured later that day (§3.4); no subsequent
  nightly run or state-table result has been verified yet.
- Design in one paragraph: each issue (account, code, form) is a row in
  `alert_state`. It must show on 2 nightly runs (T2, T3) before anyone hears
  about it; then the AM is told once. After that they hear again only when
  it gets worse (severity up, or the number at least doubles and rises by 3
  or more) or when a bounded reminder is due. At most one note per client
  account per day, 8 per AM per day, and nothing at all if more than 25
  would go out in one run. A cleared issue is mentioned only alongside the
  next note or in the Monday digest, never alone. Details: spec build notes
  and `collector/automation.py` docstring.

### 3.3 The digest incident (2026-09-28)
Any run on a Monday built the digest and sent it to every account's
`am_email`. Matthew's manual Trigger Run at 10:58 CT on Monday Sep 28 (run
44) sent it to Lauren, Lisa and Michael before the pilot started. Whether
the 05:30 scheduled run also sent one is unknown (no record of digest sends
is kept). Fixed the same day in `5c0a894`: `digest.Recipients` sends
nothing unless a recipient is chosen (§3.4). A second run on the same
Monday still sends a second digest to whoever is chosen (next step C3).

### 3.4 Render settings: live status and intended test configuration

Chrome update and read-back on 2026-09-28: `AUTOMATION_WEBHOOKS=dry`,
`AM_NOTIFY_ALLOWLIST=ldegner@smallscreenproducer.com,lhoffman@smallscreenproducer.com`,
and `AM_NOTIFY_REDIRECT=mcarlson@smallscreenproducer.com`. Saved with
"Save and apply on next run"; no run was triggered by the agent.
`DIGEST_ALLOWLIST`, `DIGEST_REDIRECT`, and `COLLECTOR_ARGS` remain absent.
No linked environment groups are shown. Both email paths send to nobody:
notes only rehearse, and digest recipients remain unset. The Matthew-only
shadow week has **not** started. The SSP workflow is Saved / Draft with
the v2 guards and routes built; see §3.9 for remaining checks.

| Setting | Current value | Meaning |
|---|---|---|
| `DIGEST_REDIRECT` | absent | digest sending remains disabled with the empty allowlist |
| `DIGEST_ALLOWLIST` | empty | with no redirect, only listed AMs get their digest; empty = nobody |
| `DIGEST_CC` | empty | dropped under a redirect; otherwise each address must be allowlisted |
| `AUTOMATION_WEBHOOKS` | `dry` | logs proposed notes, sends nothing |
| `AM_NOTIFY_ALLOWLIST` | `ldegner@smallscreenproducer.com,lhoffman@smallscreenproducer.com` | whose notes are included in the rehearsal |
| `AM_NOTIFY_REDIRECT` | `mcarlson@smallscreenproducer.com` | every note goes to Matthew, labelled; the payload's `route` becomes `matthew` |
| `AUTOMATION_WEBHOOK_URL` | set by Matthew only | secret; never in chat, files or logs |
| `COLLECTOR_ARGS` | empty except during a one-off run | empty + Trigger Run = full nightly run (on Mondays, digest included) |

**Switching the AMs on after validation and the shadow week** (Matthew's call, not an
agent's): digest: clear `DIGEST_REDIRECT`, set `DIGEST_ALLOWLIST` to the
pilot AMs' addresses. Notes: clear `AM_NOTIFY_REDIRECT` (the allowlist
already names the pilot AMs). Both take effect on the next run, no deploy.

### 3.5 Code and tests
- 237 tests passing (`python3 -m pytest collector/tests/ tagchecker/tests/ -q`).
- Security: SEC-03, SEC-04, SEC-11 and the webhook parts of SEC-12/13 are
  fixed in code; the rest is open (`SECURITY-SPEC.md` Status).
- The repository is **public** (checked 16:0x UTC). Making it private is
  step A1.

### 3.6 Dashboard follow-up view (2026-09-28)

Matthew requested a simpler account list and account detail page, preserving
all existing code and reporting while emphasizing core follow-up work.
The default **Follow-up** view now shows uncontacted leads (>24 hours) and
waiting conversations, a plain-language next step, account-manager/search
filters, and account counts for follow-up, clear, and data review. The
existing table, wall, exports, advanced filters, charts, pipeline analysis,
forms and coverage reports remain under **Detailed reports** (`mode=reports`).
Saved URLs with advanced reporting filters continue to open reporting.

Account pages bring the two existing customer detail tables above the
additional reporting. The default priorities show SLOW_RESPONSE and
CONVOS_WAITING; other priorities remain in Detailed reports with a visible
link/count. Notes, acknowledgements, GHL links, and the copy-ready weekly
summary remain available. Account metadata is expandable. Calendar-date
formatting no longer shifts a snapshot/summary to the previous day in Central
time. Mobile navigation wraps and opening another page returns to the top.

This is presentation only: no collector rules, thresholds, pilot membership,
notification timing, recipient gates, database schema, or sending settings
changed. Counts are work queues, **not notification eligibility or send
receipts**. Unknown/held values never appear as zero/clear; acknowledged
issues do not erase the snapshot's counts. The account list retains the
existing default exclusion of SSP and non-MLH accounts; Detailed reports
still exposes those inclusion controls. No customer data or preview fixtures
are committed, and no email/webhook test was sent.

Validation: 237 Python tests passed (`python -X utf8 -m pytest
collector/tests/ tagchecker/tests/ -q`; Windows needs UTF-8 for an existing
CSV fixture); 7 new web tests passed (`cd web; npm test`); production build
passed. Chrome checks with synthetic data covered manager/search/status
filters, default vs detailed views, customer-table disclosure, missing/held
counts, corrected dates, and desktop/390px layouts. The existing bundle-size
warning remains. Local preview mocks live outside the repository.

### 3.7 Speed to lead restored to account overview (2026-09-28)

At Matthew's request, the default account Follow-up view now includes a
third summary card for speed to lead. It uses the existing snapshot median,
90th percentile, human-only vs automation-inclusive label, and duration
formatter. No measurement or trigger logic changed. Missing or held data
shows Unknown. The existing detailed speed reports and charts remain intact.
Validation: all 237 Python and 7 web tests pass; production build passes;
Chrome sample-data checks confirmed the three-card layout, existing duration
formatting, and held-data handling.

### 3.8 Core reporting additions and display coverage (2026-09-28)

Matthew approved restoring weekly lead volume, oldest waiting reply, and
unassigned leads requiring action. Account Follow-up now has four summary
cards: new leads (7 days, existing baseline and percentage comparison),
uncontacted leads, waiting replies (with oldest age), and speed to lead.
Small baselines retain the collector's suppressed percentage; absent
baselines show progress. The existing unassigned-lead table now appears
under Next steps when its verified count is positive, with an assignment
prompt. It remains available in Detailed reports when zero/unknown.

`followUp.sourceComplete` checks the relevant coverage entries, not just the
overall gate: contacts for volume/baseline/ownership; contacts plus
speed_to_lead for uncontacted counts/response times; conversations for
waiting replies/oldest age. Failed, partial/capped, skipped, missing or held
inputs display Unknown. Unrelated source failures do not hide good data.
The account's displayed snapshot is masked without mutating stored data;
its affected detailed KPI tiles and copy-ready summary use the same mask.
The portfolio Follow-up view also checks source coverage before declaring
an account clear. Existing per-customer examples remain accessible with
incomplete-data labels. This does not repair the collector's stored zeros
or change alert evaluation; those backend issues remain in CODE-MAP §6.

Existing reports are retained. Missed calls remain in Detailed reports;
no new email triggers, recipient changes, sends, database changes, or
collector calculation changes. Validation: 237 Python tests and 14 web
tests pass; production build passes. Chrome synthetic-data checks cover
four summary cards, positive-only unassigned leads, small baseline wording,
oldest reply age, and failed-contact-source masking. Preview data and
screenshots are kept outside the public repository.

### 3.9 GHL pilot draft rebuilt (2026-09-28)

Matthew confirmed he ran `COLLECTOR_ARGS=--send-test` and removed the setting.
The new sample was available in Draft (`is_test=true`, `schema_version=2`)
and all contract fields were verified in the mapping picker. The existing
Inbound Webhook trigger and its URL were retained.

Replaced legacy Create contact, PIPELINE_WEEKLY condition, weekly digest
notification, task, and alert notification with:
- **Test?**: `is_test` is `true` -> end; Live request -> Valid?.
- **Valid?**: `event` is `am_account_health` AND `schema_version` is `2`.
  Valid -> Route; unexpected -> Matthew-only in-app notification -> end.
- **Route**: `lauren`, `lisa`, `matthew` -> one Internal Notification email
  to **Lauren Degner**, **Lisa Hoffman**, **Matthew Carlson**, respectively.
  Unknown -> Matthew-only in-app notification -> end.
- Each email uses From name `Account Health`, subject
  `{{inboundWebhookRequest.subject}}`, body `{{inboundWebhookRequest.body_html}}`.
  GHL required a From email when a From name is set, so the configured
  sender is `mcarlson@smallscreenproducer.com`. Sender delivery is untested.
  Particular user only; no CC/BCC or follower notifications.
- No contact creation, tasks, tags, opportunities, or client-account writes.
  Allow re-entry is on; Stop on response is off. Saved / Draft confirmed.

**Validation is incomplete; do not publish yet.**
- V1 (contactless mapped email): unverified. This editor exposes Send test
  mail, but no non-sending Preview was found, including its expanded and
  source-code views. No email test was sent by the agent.
- V2 (mapped HTML rendering): unverified for the same reason. The HTML
  field is saved; do not claim the delivered layout was checked or switch
  to body_text without evidence that HTML escapes.
- V3: passed. New sample fetched/selected and saved while in Draft.
- V4: the trigger states it is premium and incurs additional charges per
  execution; no numeric rate was displayed. Exact price remains unverified.
- Both in-app actions required Redirect page; only Contact, Conversation,
  and Opportunity were offered. Contact was selected to save the draft.
  There is no contact context, so their delivery/deep link is unverified.
  Resolve this before publishing; use the spec §6.1 dedicated SSP contact
  fallback if validation proves it necessary.

Matthew subsequently approved exactly one test email through the webhook
and published the workflow himself, then confirmed approval. To preserve the
normal `--send-test` guard, the separate command
`--send-test-email-to mcarlson@smallscreenproducer.com` sends one synthetic
SAMPLE payload through the normal Matthew route (`is_test=false`), with a
TEST subject. It accepts only Matthew's exact address, does not collect live
data or send digests, and exits. It deliberately bypasses dry mode only for
this explicitly invoked test. Normal `--send-test` remains mapping-only.
Delivery execution is pending at this commit; authorization is for this one
send, not recurring test runs. Clear COLLECTOR_ARGS after the run and return
the workflow to Draft after checking execution. Do not turn normal delivery on.
Dry-run settings are saved (§3.4); 3 to 5 nightly rehearsals remain to be
observed. No workflow was published and no live delivery was enabled.

## 4. Next steps

Order: A before B. C to F can run in parallel with B. Items marked
**(go needed)** wait for Matthew's explicit approval.

### A. Owner actions (Matthew; an agent cannot do these)
- **A1. Make the repository private** (SEC-01). Then check that Render and
  Netlify still deploy (re-authorize their GitHub connection if not) and
  that Codex or any other agent still has access. Check: a logged-out
  browser gets 404 on the repo URL.
- **A2. Set the Render values in §3.4.** Wait for the `5c0a894` deploy (or
  later) to be live first.
- **A3. Pause the failing GitHub Actions schedules** (collector, tag
  checker), or decide to finish the cutover (see F1). Check: no failure mail
  for a week.
- **A4. Tell the three AMs** to ignore today's digest, if wanted.
- **A5. Settings checks the security review could not see** (SEC-08 to
  SEC-10): Supabase sign-ups off, inactivity timeout, leaked-password
  protection, exposed schemas; who has access to Render, Netlify, the
  Supabase org; a dedicated SMTP sender instead of Matthew's own mailbox.
  Date each result in this file.

### B. AM notes go-live (spec §4 and §5)
- **B1. Dry run (settings saved; observations pending).** `AUTOMATION_WEBHOOKS=dry`, allowlist and redirect as in
  §3.4. Run 3 to 5 nights. Check each morning: the Render log has a line
  `notices: 0 sent, N dry, ...` (N can be 0 on the first night, since an
  issue needs two nights), and the notes logged above it read well.
  `select code, status, count(*) from alert_state group by 1, 2;` shows
  rows. Each pilot account gets at most one note a night and the same issue
  does not repeat night to night.
- **B2. Validate the rebuilt "MC Account Health - Alerts" draft** (current
  findings in §3.9). Build steps below are retained as the procedure;
  steps 1 to 4 are complete. Do not rebuild again. (Part B, spec
  §4.4), in Draft only. Needs an agent with a browser connected to
  Matthew's logged-in Chrome, or Matthew by hand. Order matters:
  1. Confirm the account on screen is Small Screen Producer. Set the
     workflow to **Draft** before anything is posted to it: the old steps
     have no test guard.
  2. List the existing steps (names and types) for the hand-back, then
     leave the Inbound Webhook trigger as it is. Opening the trigger shows
     its URL: never copy, paste, repeat or screenshot it.
  3. Matthew runs `COLLECTOR_ARGS=--send-test` once on Render (his go for
     that one POST, which carries `is_test = true`; the workflow is in
     Draft, so nothing runs and nobody is emailed), then clears it. In
     GHL: open the trigger, Fetch sample requests, pick the newest
     (`schema_version` 2), save. If samples cannot be fetched while in
     Draft (V3), stop and report; do not publish.
  4. Build §4.4 steps 1 to 5: Test? guard, Valid? guard (event
     `am_account_health`, schema 2), Route on `route` with branches
     `lauren`, `lisa`, `matthew` and None; each branch one Internal
     Notification email to that one person, From name "Account Health",
     subject `{{inboundWebhookRequest.subject}}`, message
     `{{inboundWebhookRequest.body_html}}` (fall back to `body_text` if the
     preview shows raw tags); None branch an in-app note to Matthew.
  5. Check V1 to V4 (§4.5) with the in-action Preview only. Never press
     Test on a path that emails a person. If V1 fails, use the §6.1
     fallback (one SSP-side contact) and report.
  6. Leave it in Draft. Hand back: screenshot without the URL, the old
     step list, V1 to V4 results, the users picked per branch. Offer the
     option of pointing the `lauren` and `lisa` branches at Matthew for
     the shadow week as a second safety.
- **B3. Shadow week (go needed):** Matthew publishes the workflow and sets
  `AUTOMATION_WEBHOOKS=on` with the §3.4 redirect. Only Matthew gets notes.
- **B4. Live (go needed):** the switch-on in §3.4. Kill switch at any time:
  `AUTOMATION_WEBHOOKS=off`, or the workflow back to Draft.

### C. Agent tasks, safe to start now
- **C1. Security spec gap review.** Re-check `docs/SECURITY-SPEC.md` against
  the current code, workflows, migrations and docs. Deliver: an addendum
  "Review YYYY-MM-DD" in that file with new findings numbered SEC-21 on (same
  columns: evidence with file:line, severity, why it matters, fix, effort),
  corrections to existing items (fixed, wrong, stale line references), and
  an updated Status block. No behaviour changes in the same pass. Candidate
  gaps to check (not verified):
  - Webhook authenticity: anyone holding the webhook URL can make SSP's GHL
    email staff arbitrary HTML (`body_html`). A shared-secret field checked
    in the workflow's Valid? step may be worth it.
  - Deploy path: every push to the default branch deploys to Render and
    Netlify with no review, and agents hold push rights. That is the
    owner's chosen workflow; record it as an accepted risk with its
    compensating controls, or propose branch protection.
  - Digest duplicates on a same-day rerun (C3) and the digest incident's
    root cause (a calendar-triggered send with no allowlist).
  - What reaches Render logs in dry mode (the rendered notes: account names
    and counts).
  - GitHub settings a public repo relies on: secret scanning and push
    protection, Dependabot alerts, who can approve workflow runs from forks.
  - `web/` for any raw HTML rendering; Supabase RLS on tables added since the
    review (`alert_state`), and on the `automation_sends` columns.
- **C2. `AM_NOTIFY_ALLOWLIST` duplicates:** `automation.Settings` counts a
  repeated address as malformed and blocks every note. Fails closed, but
  the message is wrong. Mirror `digest.Recipients` (count invalid entries).
- **C3. Record digest sends.** Store one row per digest sent (run date,
  recipient, AM it was for, parts). Skip a Monday resend for the same date
  unless `--digest` is run on purpose. Check: two Monday runs in a row send
  once.
- **C4. Security code items from `SECURITY-SPEC.md` §4:** SEC-12 (refuse a
  PIT with control characters, redact any Bearer value), SEC-13 (neutralize
  CSV cells starting `=`, `+`, `-`, `@` in every CSV writer), SEC-14
  (crawler: https on the client's host only, no private IPs, time budget),
  SEC-16 (report-only CSP; `ExternalLink` http and https only), SEC-19
  (`.gitignore` `reports/` and `*.csv`; probe output to an ignored file),
  SEC-06 (hash-locked requirements), SEC-05 (tag checker browser without
  secrets). SEC-07 (retention) and SEC-17 (column grants) need a migration
  that Matthew applies.
- **C5. Form cleanup-candidate list for the GHL team:** a column in the form
  report (`--form-activity`) marking forms never used or quiet 60+ days,
  excluding standard snapshot forms (Unsubscribe, A2P opt-in, "How did we
  do?", booking). The team retires forms by hand (rename "ZZ-RETIRE", wait
  2 weeks, delete).

### D. Alert changes proposed, waiting for Matthew (go needed; do not build)
Evidence is from stored September snapshots.
- **D1. Widen T2** to count leads that got only automated replies.
  Liverpool: 88 to 93% of leads got no personal reply from Sep 22 and no
  warning fired, because `NOT_WORKING_IN_MLH` (needs 0 deals moved)
  suppresses the SLOW_RESPONSE ratio. Check: `--notify-preview 28` shows
  Liverpool T2 notes in late September.
- **D2. "Facebook leads stopped"** (needs per-source newest-lead dates
  stored; today only weekly counts per source exist). Facebook share of
  leads: AAA Spa 88%, Liverpool 80%, Flohr and McKinney about 60%. Open
  question: AM alert or GHL-team report?
- **D3. "Slow first personal reply"** in place of, or beside, T3: Central
  Jersey median 17 to 23 hours on 5 of 7 nights; McKinney 7 to 13 hours on
  4 of 7. Needs Lauren's OK.
- **D4. Drop form warnings from the AM Monday digest** (forms go to the GHL
  team).
- **D5. Swap Pettis:** it produced no notes in the September replay, so it
  tests nothing. Lisa's call, with her alert types.

### E. Data-quality fixes (`CODE-MAP.md` §6; each needs tests and a replay)
- **E1.** Counts read 0, not "unknown", when their fetch fails. Return None
  and let the gate and flags treat it as missing.
- **E2.** Busy accounts hit fetch caps, which mark sources "partial"; two
  partials hold a healthy account (G3). Count cap hits separately.
- **E3.** Problems age out of 7-day and 14-day windows with nobody acting.
  The AM notes' cleared line for T2/T3 checks the current count, so an
  aged-out backlog can read "cleared". Decide whether T2/T3 need a
  "resolved by action" signal before go-live.
- **E4.** `NO_CLIENT_TOUCH` never fires: `ssp_client_contact_id` is empty
  for every account. Matthew fills it with
  `collector/tools/find_client_contact.py`.
- **E5.** Win rate and days to close are unreliable where deals are created
  already won (Central Jersey 98.5%, Flohr 92.9%, about 0 days to close).
  Keep them out of AM messages.

### F. Backlog
- **F1. Scheduler cutover or retirement.** If GitHub Actions takes over:
  private repo first, a protected environment for secrets (SEC-02), pass
  `AUTOMATION_*`, `AM_NOTIFY_*`, `DIGEST_REDIRECT` and `DIGEST_ALLOWLIST`
  through (the workflow passes none today, which fails closed), then
  suspend Render. Otherwise delete the schedules.
- **F2. Daylight saving:** the cron is UTC, so from Nov 1 the run is at
  04:30 CT. Fine unless someone relies on 05:30.
- F3. 30 new subaccounts need PITs (worksheet delivered Sep 1), then AM
  mapping.
- F4. 7 dashboard accounts missing from the updated client list (All
  American, Aqua Pros, Beachfront, G&S, Luke Gell, Pla-Mor, Pristine):
  churned? Burkett's is Active on the list but paused here. Texas Pools
  returns no data (PIT probably on the wrong location). Dada has no address
  (Exquisite, G&S Facebook).
- F5. AAA Pools' pool-builder pages send leads under two form ids (one not
  in Sites > Forms). `cwf-<locationId>` form ids are probably the chat
  widget (unconfirmed). Backyard Oasis has no client users and is not
  marked non-MLH: ask Lisa.
- F6. Digest recipient decision for the pilot: which AMs, and whether the
  digest and the notes switch on together.

## 5. Decisions (dated, Matthew's unless noted)
- 2026-09-22: form history is read all-time (GHL's 30-day default is not a
  retention cap); accounts marked non-MLH are not pulled from GHL; the
  message source beats the user tag, and anything within 2 minutes of the
  lead counts as automated.
- 2026-09-28:
  - AM notes: one workflow in SSP's account; all logic in the collector;
    state in `alert_state`, not GHL tags. Told once, then only when worse or
    at a bounded reminder; cleared lines never sent alone.
  - Pilot: Lauren = Flohr, Central Jersey; Lisa = Pettis, Liverpool,
    McKinney, AAA Spa & Pool; T2 and T3. T4 dropped (forms to the GHL team).
  - The weekly pipeline webhook send is retired; the digest carries the
    pipeline read.
  - **No emails to the team yet.** Only Matthew receives test copies (digest
    and notes). AM addresses stay in the data for the pilot switch-on.
  - Render stays the scheduler for now.
  - Development continues in Codex; push straight to the default branch, no
    pull requests.

## 6. Findings not to re-derive
- September replay of the pilot on T2 and T3: 16 notes in 28 nights
  (Lauren 10, Lisa 6) against 95 for "one email per account per night while
  anything is open". T3 is a standing backlog at Central Jersey, Flohr and
  McKinney (15 to 40 waiting every night). Pettis gets none.
- Pipeline: about 94% of open deals are stale; deals are created
  automatically and advanced by hand, with no forced exits.
- Website capture (Aug 31 crawl of 42 sites): many sites do not send leads
  into GHL (Backyard Oasis uses Keap, Russo's SharpSpring, Juniper HubSpot,
  Fossil Creek monday.com; Pettis, Liverpool and most of Lisa's book use
  WordPress forms). GHL cannot automate leads it never receives.
- Professional Pools & Care's sitemap lists about 130 casino-spam posts:
  probably a hacked WordPress; tell their web team.
- AAA Pools' stored site `learn.aaapools.com` is a dead 404; Aqua Pros'
  canonical domain is `aquapoolspapros.com`.
- GHL's API never says where a form is embedded. Submissions carry the page
  URL (`form_urls.py`); silent forms need the site crawl (`find_embeds.py`).
  GHL's API cannot edit workflows.
- The Google Sheets used in the pilot (review sheet, pipeline analysis for
  Pam) are in Matthew's Drive; links are in this file's history before
  2026-09-28 and are deliberately not repeated in a public repo.

## 7. Where things are

| Thing | Location |
|---|---|
| Repo, default branch | `matthewcarlsonhome-cmd/GHLReports`, `claude/gohighlevel-reports-build-l6hlc7` (Render and Netlify deploy from it) |
| Collector entry and modes | `collector/main.py`: nightly (no args), `--digest [--dry-run]`, `--send-test`, `--notify-preview [DAYS]`, `--probe`, `--backfill N`, `--form-urls`, `--form-activity`, `--account-usage`, `--location <slug>`, `--include-non-mlh`, `--dry-run` (`--weekly-alerts` is retired) |
| Logic | `metrics.py` (math, gate), `flags.py` (flag rules), `automation.py` (AM notes), `digest.py` (Monday email, `Recipients`) |
| I/O | `store.py` (Supabase), `ghl_client.py` (GHL, read-only guard), `fetchers.py` (per endpoint, strips personal data) |
| Tools | `collector/tools/`: `pit.py`, `find_client_contact.py`, `form_urls.py`, `find_embeds.py`, `form_activity.py`, `account_usage.py` |
| Migrations | `supabase/migrations/0001` to `0015` (next: `0016`) |
| Tests | `collector/tests/` (fakes in `fakes.py`), `tagchecker/tests/` |
| Web app | `web/` (Vite, React, TypeScript) |

## 8. Tooling notes
- Supabase and client websites were unreachable over direct HTTPS from
  Claude's cloud sandbox; the Supabase MCP tool and FireCrawl worked. Codex
  may have neither: ask Matthew for read-only SQL results instead.
- The Google Drive connector cannot edit cell contents; to update a Sheet,
  upload a new CSV as a Sheet. Base64 xlsx uploads corrupt above about 10 KB.
- Gmail clips HTML email over about 102 KB; `digest.py` splits a digest into
  numbered parts under an 88 KB budget.
- The one-page design summary of the AM notes is a private claude.ai
  artifact in Matthew's account; other tools cannot open it. The spec and
  the `automation.py` docstring hold the same content.
