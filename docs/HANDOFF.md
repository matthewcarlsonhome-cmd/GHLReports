# Handoff — GHL Account Health Dashboard

State as of **2026-09-22 (evening)**. Read this first in a new thread; the other
docs (`DESIGN.md`, `ARCHITECTURE.md`, `GO-LIVE.md`, `FORMS-INTEGRATION.md`,
`FORM-MONITORING.md`, `ACCOUNT-USAGE.md`) hold the detail.

## Update 2026-09-22 evening (branch `claude/lucid-gauss-vz1y98`)

- **The "30-day limit" was wrong.** GHL's `/forms/submissions` defaults to the
  last month when `startAt`/`endAt` are omitted, per GHL's own API spec; it is
  not a retention cap. The nightly now reads all-time history
  (`collector/form_history.py`) with a new `dormant` status. The Aug 25
  `SSPFormInventory` workbook's "CONFIRMED: 30-day window" note is wrong.
  Details: `FORM-MONITORING.md` §1.
- **New read-only reports** (`--form-activity`, `--account-usage`; GitHub
  workflow **Reports**, which needs repository secrets
  `REPORTS_SUPABASE_URL`, `REPORTS_SUPABASE_SERVICE_ROLE_KEY` and
  `REPORTS_COLLECTOR_KEY`):
  - per form: channel (website / Google ad / Facebook ad / Facebook lead ad),
    last real submission, days inactive, page URLs, Datadog candidates;
  - per account: are client staff working leads in MLH, or is it ads +
    automation only.
- **Weekly form test design** (Datadog plus GHL workflow guard, and a
  collector-side check that the test landed as a contact, with the
  `FORM_CHECK_MISSED` flag): `FORM-MONITORING.md` §4. Needs the
  `formcheck@smallscreenproducer.com` mailbox or group first.
- **Applied live:**
  - migration 0011 (`dormant` status);
  - migration 0012: `snapshots.client_users`/`ssp_users`, plus a
    **`v_portfolio` security fix**. 0010 had dropped `security_invoker`, so the
    view skipped the staff row policies; it is restored.
  - Lisa's dashboard login `lhoffman@` (Lauren and Pam already had accounts,
    never used).
- **Filter live in data, code on this branch:** accounts with no client-staff
  users (All American, Backyard Oasis, Beachfront, G&S, Luke Gell, Pla-Mor,
  Pristine) drop out of the AM digest and hide behind a portfolio toggle.
  They are still collected.
- **Forms tab + MLH filter (branch, 2026-09-22 late):**
  - `/forms` lists every form per client account that uses MLH, with form
    ID, type (website / Google ad / Facebook ad / Facebook lead ad), status,
    days quiet (all-time) and page.
  - The nightly now stores channel, page_url, subs_30d and the weekly-test
    result per form, plus Facebook lead-ad form ids (`kind = 'unlisted'`).
    Migration 0013 was applied live.
  - Lisa's list is marked in `subaccounts.mlh_status` and filtered from the
    digest, alerts, reports and default views. Luke Gell (canceled) is set
    inactive.
  - Answer on scaling the weekly test: GHL's API can't edit workflows. See
    `FORM-MONITORING.md` §4.6.
- **Human-reply fix applied (on the branch):**
  - The message source beats the user tag.
  - Anything sent within 2 minutes of the lead arriving or writing counts as
    automated.
  - "Deals moved" no longer counts deals that merely arrived.
  - Accounts where nobody replies in person and no deal moves get one info
    note, `NOT_WORKING_IN_MLH`, in place of red SLOW_RESPONSE /
    PIPELINE_FROZEN alarms.
  - Expect speed-to-lead to show "—" on ads-only accounts and "Deals moved"
    to drop sharply on the first run after deploy (a one-time step change in
    the week-over-week comparison).

## What this is

A nightly, **read-only** health check across Small Screen Producer's (SSP)
GoHighLevel client subaccounts. It collects each account's data, flags
problems (lead flow dropping, leads left uncontacted, forms going silent,
pipelines stalling), and surfaces them to account managers (AMs) so they can
call the client and book a review. Owner: Matthew Carlson
(mcarlson@smallscreenproducer.com).

## Rules that must survive every thread

- **Read-only against client GHL accounts.** No writes, ever. ("We're not
  doing writes through this tool.")
- **Never send email to staff or clients without an explicit request for that
  specific send.** No test alerts to anyone.
- **Never put a GHL token (PIT) or the GHL webhook URL in chat.** PITs live in
  Supabase Vault only; the webhook URL is an env var only.
- **No customer PII in outbound payloads** — no contact names, phones, emails,
  or deal names (GHL deal names are customer names).
- Digest recipients must end in `@smallscreenproducer.com` (enforced in code).
- **Michael's accounts are out of scope for the pilot.**
- Communication to AMs: plain language, short, no tech terms (no "API",
  "PIT", "webhook"). AMs just need to know when a client needs a meeting.

## Architecture (one paragraph)

Python collector (`collector/`) runs nightly at 05:30 CT, reads each
subaccount's PIT from Supabase Vault, pulls GHL API v2 data, computes metrics
and flags, and writes to Supabase (`snapshots`, `flags`, `form_health`, …).
A React dashboard (`web/`, Netlify) reads it. Two outbound paths:
**(1) Monday digest** — HTML email per AM via SMTP, grouped by
`subaccounts.am_email`; **(2) daily alert bridge** — POSTs a flat payload to a
GHL inbound-webhook workflow, gated by `AUTOMATION_WEBHOOKS` (off/dry/on).
Scheduler: Render cron is what's actually been used; a GitHub Actions workflow
also exists (`.github/workflows/collector.yml`) — cutover still pending.

## Where things are

| Thing | Location |
|---|---|
| Repo / branch | `matthewcarlsonhome-cmd/GHLReports`, `claude/gohighlevel-reports-build-l6hlc7` (head `6aedabc`) |
| Supabase project | `tpavdifpsevkrubplyrg` (query via the Supabase MCP tool; direct HTTPS from the sandbox is blocked) |
| Dashboard | https://mlhaccountreports.netlify.app — account pages are `/account/<location_id>` |
| Collector entry | `collector/main.py` — modes: nightly, `--digest`, `--weekly-alerts`, `--send-test`, `--probe`, `--form-urls` |
| Flags / digest / alerts | `collector/flags.py`, `collector/digest.py`, `collector/automation.py` |
| Tools | `collector/tools/` — `pit.py`, `find_client_contact.py`, `form_urls.py` (form → page URL from submissions), `find_embeds.py` (crawl client sites for GHL embeds) |
| Migrations | `supabase/migrations/0001`–`0013` (0009 = AM names, 0010 = `v_portfolio.am_name`, 0011 = form `dormant`, 0012 = client/SSP user counts + view security fix, 0013 = `mlh_status` + form type columns); all applied live |
| Tests | `python3 -m pytest collector/tests/ -q` → **192 passing** (branch `claude/lucid-gauss-vz1y98`) |
| Reports | `collector/tools/form_activity.py`, `account_usage.py`; `.github/workflows/reports.yml` |

## Current state

- **29 active subaccounts** in the dashboard, all collecting nightly.
- **AM mapping is live** (`subaccounts.am_name` + `am_email`):
  Lauren `ldegner@` — 10 · Lisa `lhoffman@` — 9 · Michael `madams@` — 9
  (Exquisite + G&S are "Michael / Dada (FB)"; Dada has no address) ·
  Matthew `mcarlson@` — SSP.
- Digest header/greeting names the AM; dashboard shows AM names.
- `AUTOMATION_WEBHOOKS` defaults to `off` in `render.yaml` — **live Render
  value not verified.**

## ⚠ Decide before Monday Sep 28

Until today every `am_email` was Matthew's, so all mail went to him. **It now
points at the real AMs.** The Monday run auto-sends the digest (SMTP) to each
`am_email`, and the daily alert payload carries `am_email` too. Unless changed,
**Lisa, Lauren and Michael receive their first digest Mon Sep 28 ~05:30 CT** —
before the intro email and pilot picks. Options: (a) let it go, (b) point
`am_email` back to Matthew and keep `am_name` until the pilot starts (one SQL
update), or (c) add a recipient allowlist in `digest.py`/`automation.py` so
only pilot accounts' AMs get mail. Matthew's call.

## The AM alert pilot (where we are)

- **Goal:** one pilot account each for Lauren and Lisa; each alert = one
  plain-English email to that AM → AM books a client review.
- **Candidates** (ranked on Sep 22 data by how hard daily alerts fire):
  Lauren — Flohr, Central Jersey, AAA Pools (all have working GHL website
  forms). Lisa — AAA Spa & Pool, McKinney, Hamlin (all capture website leads
  outside GHL; Campbell's is the swap if a GHL-capturing site is wanted).
- **8 alert types** offered to AMs (T1 lead flow stopped · T2 leads going
  cold · T3 customers left waiting · T4 website capture broken · T5 emails
  not delivering · T6 social disconnected · T7 pipeline frozen (weekly) ·
  T8 deal money parked (weekly)); cap 5/day, only new-or-worse.
- **Review sheet:** https://docs.google.com/spreadsheets/d/1BLTqKBXPyHHaFjdhXymHJRI2fM7WkNAdTzSv72iBXZM/edit
- **Intro email** to Lisa, Lauren, Pam: drafted (short, plain-language), for
  Matthew to send himself. Not sent by us.
- **Not built yet:** per-account trigger rules from AM picks, and the T1–T8
  mapping onto existing flag codes in `automation.py`. v4 spec (change-based
  ranking, no health grade, acute vs. chronic backlog):
  https://claude.ai/code/artifact/c05835c5-97e7-41e7-b917-af64cc19b4d2

## Findings not to re-derive

- **Pipeline:** ~94% of open deals stale; mechanism is auto-create /
  manual-advance with no forced exits. Analysis for Pam:
  https://docs.google.com/spreadsheets/d/1Am602BJNCiWeV8-CG5eKAG-MaAl2vYpQKXE_-mtR5GQ/edit
- **Website capture (Aug 31 crawl, 42 sites):** many sites don't send leads
  into GHL — Backyard Oasis (Keap), Russo's (SharpSpring), Juniper (HubSpot),
  Fossil Creek (monday.com), Pettis/Liverpool/most of Lisa's book (WordPress
  forms). GHL can't automate leads it never receives.
- **Professional Pools & Care** site has ~130 casino-spam posts in its sitemap
  (`/post-sitemap.xml`) — likely a hacked WordPress; tell their web team.
- AAA Pools' stored site `learn.aaapools.com` is a dead 404; Aqua Pros'
  canonical domain is `aquapoolspapros.com`.
- GHL API never exposes where a form is embedded; submissions carry
  `others.eventData.page.url` (`form_urls.py`), and silent forms need the
  site crawl (`find_embeds.py`).

## Open items

1. **Monday send decision** (above).
2. Lisa/Lauren pick pilot accounts → build pilot trigger rules → turn on for
   those two only.
3. **30 new subaccounts need PITs** (worksheet delivered Sep 1:
   `New-Subaccount-PIT-Worksheet.xlsx`). Then map their AMs the same way.
4. 7 dashboard accounts missing from the updated client list (All American,
   Aqua Pros, Beachfront, G&S, Luke Gell, Pla-Mor, Pristine) — churned?
5. Burkett's is Active on the list but paused in the dashboard.
6. Texas Pools returns no data — PIT likely wired to the wrong location.
7. Dada's address (for Exquisite / G&S Facebook).
8. Scheduler: finish GitHub Actions cutover; cron shifts to 04:30 CT when DST
   ends in November.

## Tooling gotchas

- Google Drive connector **cannot edit cell contents** — only title/folder.
  To "update" a Sheet, create a new one from CSV (`create_file`,
  `text/csv`) and trash/rename the old.
- Uploading xlsx to Drive as base64 corrupts above ~10KB — use CSV→Sheet or
  send the file directly.
- Supabase and client websites are unreachable over direct HTTPS from the
  sandbox; use the Supabase MCP tool and FireCrawl.
- Gmail clips HTML email over ~102KB; `digest.py` splits into numbered parts
  under an 88KB budget.
