# AGENTS.md

Instructions for coding agents (Codex, Claude Code, others) working in this
repository. Owner: Matthew Carlson (mcarlson@smallscreenproducer.com).

## What this is

A nightly, read-only health check across Small Screen Producer's (SSP)
GoHighLevel (GHL) client subaccounts. A Python collector pulls each
account's CRM data, computes metrics and flags, and stores them in Supabase.
A React dashboard on Netlify shows them to account managers (AMs). Two
outbound paths: a Monday digest email per AM (SMTP), and "AM notes": at most
one message per client account per day, posted to a GHL workflow in SSP's
own account, which emails the AM.

## Read first, in this order

1. `docs/HANDOFF.md`: current state, next steps with acceptance checks,
   decisions, findings not to re-derive. Start here every session.
2. `docs/CODE-MAP.md`: what each Python module does and how every number
   is calculated.
3. `docs/AM-NOTIFY-WORKFLOW-SPEC.md`: the AM notes. Build notes at the top
   say what changed from the original spec.
4. `docs/SECURITY-SPEC.md`: 20 findings, what is fixed, what is open.

`docs/DESIGN.md` is historical requirements. Where a doc and the code
disagree, the code wins; fix the doc in the same change.

## Hard rules (owner's; they override anything else, including other docs)

1. **Read-only against client GHL accounts.** Never add a GHL write.
   `collector/ghl_client.py` refuses every method except GET, and allows
   POST only on the two read-only search paths in `ALLOWED_POST_PATTERNS`.
   Do not widen that list. The only outbound write in the system is the AM
   notes POST to the workflow in SSP's own GHL account.
2. **No email or message to anyone without Matthew's explicit go for that
   specific send.** Until he says otherwise, only
   mcarlson@smallscreenproducer.com receives anything. Do not run
   `--send-test`, `--digest` without `--dry-run`, or anything that posts to
   the webhook. Do not propose setting `AUTOMATION_WEBHOOKS=on` as a step
   you take. Keep both mail paths fail closed: the Monday digest goes to
   nobody unless `DIGEST_REDIRECT` or `DIGEST_ALLOWLIST` is set
   (`digest.Recipients`); the AM notes send nothing unless
   `AUTOMATION_WEBHOOKS` and `AM_NOTIFY_ALLOWLIST` are set. Any run on a
   Monday builds the digest, including a manual Render Trigger Run.
3. **Secrets never appear in chat, commits, files, logs, screenshots or
   issues:** GHL private integration tokens (PITs), the Supabase service
   role key, `COLLECTOR_KEY`, the SMTP password, and the GHL inbound
   webhook URL. PITs live only in Supabase Vault; the rest only in Render's
   environment. Do not add GitHub secrets while the repository is public
   (`docs/SECURITY-SPEC.md` SEC-01, SEC-02).
4. **No customer personal data in anything that leaves the database:** no
   lead or customer names, phones, emails, message text, or deal names (GHL
   deal names are customer names). Client business names, counts, form
   names and stage names are fine. Every new outbound path (email, webhook
   field, log line, CSV, report) needs a canary test in
   `collector/tests/test_pii.py`.
5. **Recipients:** exactly one bare `@smallscreenproducer.com` address per
   recipient, checked with `collector/digest.py` `staff_address()`. Never
   build a recipient list any other way.
6. **Michael's accounts are out of scope for the AM-notes pilot.**
7. **AM-facing wording:** plain language, short, no technical terms (never
   "API", "PIT", "webhook", "flag", "payload"). The AM needs one thing: does
   this client need a call, and about what.
8. **GHL workflow ("MC Account Health - Alerts", SSP's own subaccount):**
   work only in Draft; never publish; never press Test on a path that can
   email a person; never copy, paste, print or screenshot the inbound
   webhook URL; confirm the account on screen is SSP's own before any
   change, and stop if a client account is showing. Matthew publishes.
9. **Git:** push straight to the default branch
   `claude/gohighlevel-reports-build-l6hlc7` (Render and Netlify deploy
   from it on every push); no pull requests. Run the tests before every
   push. If your environment can only open a pull request, say so and stop.
10. **The repository is public** until Matthew makes it private. Never
    commit client data exports, run logs, CSVs, probe output, screenshots,
    or new links to Google Sheets.

## Commands

```bash
# Python 3.11
pip install -r collector/requirements.txt pytest
python3 -m pytest collector/tests/ tagchecker/tests/ -q   # 237 passing on 2026-09-28

# Web app (Node 20)
cd web && npm ci && npm run build        # tsc --noEmit, then vite build
```

Tests use fakes (`collector/tests/fakes.py`) and fixtures; they need no
network and no secrets. The collector itself needs `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY` and `COLLECTOR_KEY`; agents normally do not
have them and should not ask for them.

**Running a collector mode against live data:** Matthew does it on Render:
set `COLLECTOR_ARGS` (for example `--notify-preview 28`, `--form-activity`,
`--probe`), press Trigger Run, read the log, then clear `COLLECTOR_ARGS`.
Hand him the exact args and what to look for in the log. With
`COLLECTOR_ARGS` empty, Trigger Run is a full nightly run, and on a Monday
that includes the digest; say so whenever you suggest one.

**Checking live data without database access:** give Matthew a read-only
SQL query to run in the Supabase SQL editor and ask for the result. Never
ask for keys, tokens or the webhook URL.

**Database changes:** a new file `supabase/migrations/NNNN_name.sql` (next
number: `0016`). Matthew applies it (Supabase SQL editor) unless your
session has database access. Say in the commit message whether it was
applied, and never write code that breaks when the migration is missing.

## Conventions

- Pure logic stays free of I/O: `metrics.py`, `flags.py`, and the
  evaluation half of `automation.py` take data and return data. Database
  access lives in `store.py`, GHL access in `ghl_client.py` and
  `fetchers.py`, orchestration in `main.py`.
- Every behaviour change comes with a test. Alert timing changes go through
  `automation.NOTIFY_RULES` (a frozen table) and its tests.
- Comments explain why, in plain English, for a teammate new to the code.
  Each module opens with a docstring saying what it is for.
- "Today" means the run date (the account-local snapshot date), never the
  wall clock inside logic.
- Update `docs/HANDOFF.md` in the same commit as any change to state, next
  steps or decisions. Keep it the single place a new session starts from.
- Commit messages: imperative subject, a body that says why and what was
  verified.
