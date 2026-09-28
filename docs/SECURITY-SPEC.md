# Security update specification

Owner: Matthew Carlson. Review date: 2026-09-28.

Method: a read of every file in this repository; read-only catalog queries
and the security and performance advisors on the live Supabase project
(`tpavdifpsevkrubplyrg`); read-only GitHub metadata (visibility,
collaborators, workflow runs); `npm audit` on the web lockfile; small offline
tests with fake values. Nothing was changed anywhere, and no GHL endpoint or
client site was called.

**Confirmed** = seen in code or live metadata, or reproduced offline.
**Needs check** = depends on a setting this review cannot see (Supabase Auth,
Render, Netlify, GHL, Google Workspace).

## 1. Scope and what we protect

Scope: `collector/` (nightly job, reports, tools), `tagchecker/`, `web/`,
`.github/workflows/`, `supabase/migrations/`, `netlify.toml`, `render.yaml`,
docs.

| Asset | Where it lives | What a leak gives away |
|---|---|---|
| GHL private integration tokens (PITs) | Supabase Vault, `ghl_pit_<location_id>` | That client's CRM: every lead's name, phone, email, messages |
| Supabase service role key | Render, GitHub secrets, local `.env` files | Read and write on every table; skips RLS |
| COLLECTOR_KEY | Vault (`collector_key`), Render, GitHub secrets, local `.env` | With the service role key: every PIT |
| GHL webhook URL | Render env `AUTOMATION_WEBHOOK_URL` | Anyone can fire SSP's alert workflow |
| SMTP app password | Render env, Supabase Auth SMTP settings | Mail as that Google account; IMAP access to its mailbox |
| Client lead data | `lead_events`, `snapshots.details`, `flags`, `account_notes` | Customer names, deal names, appointment titles |

## 2. How data and secrets flow today

1. Render cron (05:30 CT) runs the collector with the service role key and
   COLLECTOR_KEY. For each client, the `get_pit` RPC checks COLLECTOR_KEY,
   writes a `pit_audit` row and returns the PIT.
2. The collector reads GHL (GET plus two search POSTs). `fetchers.py` drops
   phones, emails, message bodies and form answers; customer names are kept
   for the drill-down lists. Results go to Supabase.
3. The dashboard (Netlify) holds only the anon key. Staff sign in with an
   emailed code; RLS lets any signed-in `@smallscreenproducer.com` user read
   everything and insert acknowledgements and notes.
4. Outbound: the Monday digest by SMTP to each `am_email`, and, when
   `AUTOMATION_WEBHOOKS=on`, a JSON POST to a workflow in SSP's GHL account.
5. GitHub Actions has three workflows (collector, tag checker, reports). The
   repository is public, so their logs and artifacts are public. The tag
   checker opens client sites in headless Chromium; reports can crawl them.

## 3. Findings, ranked

| ID | Title | Severity, status | Evidence | Why it matters | Recommended fix | Effort |
|---|---|---|---|---|---|---|
| SEC-01 | Repository is public | High, Confirmed | GitHub metadata `visibility: public`. Client roster in `supabase/migrations/0009_am_name.sql:13-27`, `0013_mlh_status_and_form_types.sql:24-36`; staff emails, sheet links and client notes in `docs/HANDOFF.md:138-187`; reports print and upload CSVs (`collector/main.py:1360-1366`, `.github/workflows/reports.yml:105-113`). | Client list, notes and every Actions log and artifact are world-readable. No customer data is out yet only because secrets are unset (both Reports runs did "Run reports" in 0 s, no artifact). Adding secrets would publish report CSVs and `--probe` or `--dry-run` output. | Make the repo private before adding any secret. Restrict sharing on the linked Google Sheets. | S |
| SEC-02 | Any branch push can run Reports with production secrets | Medium, Confirmed | `reports.yml:40-42` push trigger on any branch; `:93-97` service key plus COLLECTOR_KEY; no `environment:`. | A branch push that edits the workflow gets a key pair that unlocks every PIT (Claude sessions push branches here). Latent until `REPORTS_*` are set. | GitHub Environment limited to the default branch with a required reviewer; drop the push trigger or limit it to the default branch. | S |
| SEC-03 | Staff-only email rule can be bypassed | Medium, Confirmed | `collector/digest.py:414`, `:515-518`, `:561` test `endswith("@smallscreenproducer.com")` on the raw string; `:572-574` put it in the headers. Offline test: `x@evil.example, y@smallscreenproducer.com` passes and both addresses get the mail. `collector/automation.py:134` sends `am_email` with no check. | One typo or extra address in `am_email` or `DIGEST_CC` mails a client book outside the company. | One helper using `email.utils.getaddresses`: exactly one address, domain equal to `smallscreenproducer.com`. Use it for To, CC and the webhook. Add tests with the bypass strings. | S |
| SEC-04 | Customer names leave the database | Medium, Confirmed | `collector/flags.py:333-334` puts a contact name in the CONVOS_WAITING action, printed by `digest.py:125`, `:144`, `:297`. `main.py:937` probe redaction skips name, title and custom-field keys; `main.py:1569-1578` `--dry-run` prints entity names. `tests/test_pii.py:23-29` checks stored rows only, not outbound text, `form_health` or `automation_sends`. | Names reach mailboxes and logs (public, SEC-01), against the team's own rule. | Drop names from action text (keep `entity_name` for the dashboard link); redact names in `_redact`; no entity names in `--dry-run`; name-canary tests for digest, payload and probe. | S |
| SEC-05 | Tag checker's browser runs next to the service role key | Medium, Confirmed | `tagchecker/main.py:192` Chromium with Playwright defaults (no Chromium sandbox, full environment); `:176-180` same process holds the service key. It loads about 30 client sites, one reportedly hacked. | A browser exploit on a client site would reach a key that can read and change every table. | A browser job with no secrets that outputs JSON, plus a small write job. Meanwhile pass a minimal `env` to `launch()` and set `chromium_sandbox=True`. | M |
| SEC-06 | Python dependencies float | Medium, Confirmed | `collector/requirements.txt:1-4`, `tagchecker/requirements.txt:1-2` use `>=`; installed fresh by every workflow and by Render. | The newest releases run where the service key and COLLECTOR_KEY live; one bad release could read every PIT. | `pip-compile --generate-hashes`, install with `--require-hashes`, update through Dependabot. | S |
| SEC-07 | Lead and deal names are kept forever | Medium, Confirmed | `supabase/migrations/0001_init.sql:379-388` retention block commented out; the live database has no `pg_cron`. | Every lead name since go-live accumulates; a leak exposes years, not weeks. | Schedule retention (for example `lead_events` 180 days, name lists out of `snapshots.details` after 90 days). Document how to remove a note that holds personal data. | S |
| SEC-08 | Dashboard access rests on the email domain and long sessions | Medium, Confirmed and Needs check | `is_staff()` checks only the email domain (`0001_init.sql:223-227`); sessions persist and refresh (`web/src/lib/supabase.ts:25-28`); advisor: leaked password protection off. Not visible: sign-ups off, the 30-day inactivity timeout (`docs/GO-LIVE.md:87`), OTP limits, staff passwords. | Access lasts until the Supabase user is deleted, even after the Google account is suspended. A shared mailbox (such as the planned form-check address) given a user would open the dashboard to its whole group. | A `staff` table that `is_staff()` checks; a same-day offboarding step; confirm the auth settings. | M |
| SEC-09 | The service role can read Vault directly | Medium, Confirmed (grants), Needs check (API setting) | Live: `service_role` has USAGE on `vault` and SELECT on `vault.decrypted_secrets`. The claim that a leaked service key cannot read PITs (`docs/ARCHITECTURE.md:57`, `collector/store.py:26-30`) holds only while `vault` stays unexposed (`docs/GO-LIVE.md:111`). | One settings change, or SQL run as the service role, removes the COLLECTOR_KEY layer. Org members can read Vault in the dashboard. | Confirm exposed schemas; test revoking service role access to `vault` on a branch project; alert on `pit_audit` `read_denied`; review org members. | S |
| SEC-10 | SMTP app password may open a whole mailbox | Medium, Needs check | `collector/digest.py:7-9`, `render.yaml:28-31`: the digest reuses the Supabase Auth mailer's Google app password. | An app password opens the whole account over IMAP; a person's mailbox would be exposed. | Dedicated sender account with no other mail and IMAP off, its own app password; revoke the old one. | S |
| SEC-11 | Read-only guard allows any POST under `/social-media-posting/` | Low, Confirmed | `collector/ghl_client.py:61`, `:189` (prefix match). | A POST that creates a social post would pass; only the PIT's scopes stop it. | Exact path patterns (`^/contacts/search$`, `^/social-media-posting/[^/]+/posts/list$`); confirm read-only PIT scopes. | S |
| SEC-12 | Secrets can reach logs through unusual error text | Low, Confirmed | `ghl_client.py:155-163`, `:211-217`: a PIT with a control character is echoed escaped and `sanitize()` misses it (offline test, fake token); `store.py:155` strips the ends only. `automation.py:202-214`, `:362`, `:439`, `main.py:1610`: a malformed webhook URL lands in the error, the log and `automation_sends.error` (offline test). | A bad paste puts a secret where every staff member can read it. | Reject a PIT that is not one printable word; redact any `Bearer` value; validate the webhook URL at startup; log only the error type and HTTP status. | S |
| SEC-13 | Untrusted text reaches spreadsheets and alert emails | Low, Confirmed and Needs check | No CSV writer neutralizes cells starting with `=`, `+`, `-`, `@` (`collector/tools/form_activity.py:563`, `tools/account_usage.py:270`, `tools/form_urls.py:198`, `tools/find_embeds.py:354`, `web/src/pages/Portfolio.tsx:112`); page titles and sources come from site visitors (`collector/form_history.py:191`). Webhook `flag_title` and `action` go out unescaped (`automation.py:130-131`). | A visitor can plant a formula that runs when a CSV is opened in Sheets or Excel. GHL may render payload text as HTML (Needs check). | Prefix risky cells with `'`; HTML-escape `body_html` in the AM-notify rewrite. | S |
| SEC-14 | Crawler follows untrusted URLs without limits | Low, Confirmed | `collector/tools/find_embeds.py:95-105` follows redirects to any host, 15 s per read, no total limit; `:131`, `:148` sitemap URLs are not host-checked; `tools/form_activity.py:318-319` refetches submission page URLs that a visitor can forge (`form_history.py:190`). | Blind requests to arbitrary or internal addresses from the runner or a laptop; a slow site can stall the job. | https on the client's host only, no private IPs, no cross-host redirects, a time budget per site. | S |
| SEC-15 | GitHub Actions hygiene | Low, Confirmed | No `permissions:` blocks; actions pinned by tag; `reports.yml:105` puts a step output in the shell (validated at `:83-89`); `collector.yml:29-34`, `:57` accept any flag, including `--digest`. Every scheduled collector and tag checker run from 2026-09-14 to 2026-09-27 failed (secrets unset). | Wider token rights than needed; a moved tag runs new code next to secrets; daily red runs hide real failures, and the tag checker never runs. | `permissions: contents: read`; pin actions to commit SHAs; pass flags through `env`; allow-list dispatch flags; pause both schedules until the cutover. | S |
| SEC-16 | Browser hardening is thin | Low, Confirmed | `netlify.toml:14` CSP has only `frame-ancestors`, with `*.gohighlevel.com`, `*.leadconnectorhq.com` and `*.msgsndr.com` wildcards; `web/src/components/ui.tsx:196-206` renders any `href`. | No backstop if script injection ever appears (the session sits in localStorage); any GHL-hosted page can frame the app. Today's risk is low. | Report-only CSP, then enforce; exact frame hosts; http and https only in `ExternalLink`; fail fast if the web key is not the anon key. | S |
| SEC-17 | Dashboard inserts are broader than needed | Low, Confirmed | Live: table-level INSERT on `flag_acks` and `account_notes` (`0001_init.sql:358-359`), so users can set `id`, `acked_at`, `created_at`; `flag_acks.note` has no length limit; `web/src/pages/Portfolio.tsx:307-340` the `a` key acks for 7 days with no confirm. | Backdated acks, blocked inserts, huge notes; a stray key hides a red flag from the digest for a week. | Column-level grants, server defaults for `acked_by` and `author`, a note length check, a confirm on the shortcut. | S |
| SEC-18 | Web dependencies with known advisories | Low, Confirmed | `npm audit`: 1 high and 1 moderate in the Vite and esbuild dev server (development only); 2 moderate in `react-router` 6.30, not reachable with today's internal links. | Mostly a laptop risk during `npm run dev`. | A patched Vite; plan the react-router v7 move. | M |
| SEC-19 | Repo hygiene | Low, Confirmed and Needs check | `.gitignore` misses `reports/` and `*.csv`; `--probe` appends to the tracked `VERIFICATION.md` (`collector/main.py:1317`); `docs/GO-LIVE.md:33` holds a JWT the doc labels as the anon key (Needs check); `.env.example:2` "exactly three secrets" is out of date. A history scan (61 commits, all branches) found no PIT, webhook URL, service key, COLLECTOR_KEY or SMTP password. | Local runs can commit names into a public repo. | Ignore outputs; send probe output to an ignored file; use a placeholder in GO-LIVE; list every secret in `.env.example`. | S |
| SEC-20 | Definer functions search `public` first | Info, Confirmed | Live: the six SECURITY DEFINER functions pin `search_path` to `public, vault` or `public`; no API role can create objects in `public`. | Safe today; would matter if an API role ever got CREATE on `public`. | `set search_path = ''` and qualified names when next edited. | S |

## 4. Update plan

`collector/digest.py`, `flags.py`, `automation.py`, `store.py`, `main.py` and
the tests are being rewritten for the AM-notify work
(`docs/AM-NOTIFY-WORKFLOW-SPEC.md`). Land SEC-03, SEC-04, SEC-12 and SEC-13
inside that work or right after it.

### Do now (this week)

1. **Make the repo private** (SEC-01). Check: a logged-out browser gets 404
   for the repo and an Actions run URL; the linked Sheets are restricted.
2. **Fence the secrets** (SEC-02). Environment `production`, default branch
   only, Matthew as reviewer; `REPORTS_*` moved into it; push trigger removed
   or limited. Check: a run from another branch cannot read the secrets.
3. **One strict recipient check** (SEC-03). Check: tests with
   `a@x.com, b@smallscreenproducer.com`, a `;` form and a display-name form
   fail today and pass after; the digest and the webhook both use the helper.
4. **Keep names out of mail and logs** (SEC-04). Check: a name canary in
   `test_pii.py` is absent from digest text and HTML, from the payload built
   for every flag code, and from probe and `--dry-run` output.
5. **Verify what this review could not see** (SEC-08 to SEC-11) and date
   each item in `docs/HANDOFF.md`: exposed schemas; sign-ups off; inactivity
   timeout; leaked password protection; no staff passwords; Netlify holds the
   anon key; Render's `AUTOMATION_WEBHOOKS` value; read-only PIT scopes; the
   `SMTP_USER` account; members of the Supabase org and Render and Netlify
   teams.
6. **Stop the daily red runs** (SEC-15). Pause the collector and tag checker
   schedules. Check: no failure mail for a week; re-enable after steps 1-2.

### Next (this month)

7. **Lock Python dependencies** (SEC-06). Check: every install uses
   `--require-hashes`; Dependabot opens update PRs.
8. **Isolate the tag checker** (SEC-05). Check: the job running Chromium has
   no Supabase variable in its environment.
9. **Turn on retention** (SEC-07). Check: the job appears in `cron.job`; a
   week later no `lead_events` row is past the limit.
10. **Staff allow-list and offboarding** (SEC-08). Check: deleting a `staff`
    row makes that user's next query return nothing; the step is written down.
11. **Close the direct Vault path** (SEC-09). Check, after a branch-project
    test: `has_table_privilege('service_role', 'vault.decrypted_secrets',
    'select')` is false and the nightly run still gets every PIT.
12. **Tighten the GHL client** (SEC-11, SEC-12). Check: tests refuse a POST
    to `/social-media-posting/x/posts` and reject a PIT with a newline before
    any request, with no token text in the error.
13. **Harden the webhook in the rewrite** (SEC-12, SEC-13). Check: tests show
    a bad URL fails at startup without echoing it and HTML fields are escaped.
14. **Neutralize CSV cells** (SEC-13). Check: a page title of `=HYPERLINK("x")`
    comes out as `'=HYPERLINK("x")` from every CSV writer.

### Later

15. Crawler limits (SEC-14). Check: tests skip off-host sitemap URLs, private
    IPs and cross-host redirects.
16. CSP and link allow-list (SEC-16). Check: a week of report-only with no
    violations, then enforce.
17. Narrow dashboard inserts (SEC-17). Check: an insert that sets `acked_at`
    or `author` is refused.
18. Web dependency upgrades (SEC-18). Check: `npm audit --omit=dev` is clean.
19. One Supabase secret key per service (collector, reports, tag checker) so
    a leak can be revoked alone, plus a rotation runbook for the service key,
    COLLECTOR_KEY, SMTP password, webhook URL and PITs.
20. SEC-19, SEC-20 and SHA-pinned actions.

## 5. Already done well (do not undo)

- PITs live only in Vault and come out only through SECURITY DEFINER RPCs
  that require COLLECTOR_KEY and log every read and denial. The tag checker
  never receives COLLECTOR_KEY.
- Every table has RLS enabled and forced; anon has no table grants; both
  views run `security_invoker` (live check, after the 0012 fix); dashboard
  users have no UPDATE or DELETE; RLS pins `acked_by` and `author` to the
  signed-in email; new functions start with no execute rights for API roles.
- A trigger on `auth.users` rejects non-staff emails behind the sign-up
  switch, and the login page never creates users.
- The web app knows only the anon key; React escapes text; external links
  use `rel="noreferrer"`; the site is `noindex`.
- `fetchers.py` whitelists every record, so phones, emails, message bodies
  and form answers never leave the fetch layer; page URLs lose their query
  strings; canary tests prove it.
- The GHL client refuses non-read methods and redacts the token in errors.
- The webhook is off by default behind one kill switch, drops person-type
  entity names, caps and dedupes sends, and audits each one.
- Digest HTML is escaped, SMTP uses TLS with certificate checks, recipients
  are filtered to the staff domain (make it strict with SEC-03).
- `reports.yml` allow-lists dispatch input and passes it through `env`; every
  job has a timeout; artifacts expire after 14 days.

## 6. How to re-run this review

1. Supabase advisors, security and performance. Expected today: `pit_audit`
   "RLS enabled, no policy" (intended) and leaked password protection.
2. Read-only catalog queries:
   - `select c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity, c.reloptions from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind in ('r', 'v');`
     Expect true and true on tables, `security_invoker=on` on views.
   - `select grantee, table_name, string_agg(privilege_type, ',') from information_schema.role_table_grants where table_schema = 'public' and grantee in ('anon', 'authenticated') group by 1, 2;`
     Expect no anon rows; INSERT only on `flag_acks` and `account_notes`.
   - `select tablename, policyname, cmd, roles, qual, with_check from pg_policies where schemaname = 'public';`
   - `select proname, prosecdef, proconfig, has_function_privilege('anon', oid, 'execute'), has_function_privilege('authenticated', oid, 'execute') from pg_proc where pronamespace = 'public'::regnamespace;`
     Expect a fixed `search_path` on definer functions; only `is_staff` for
     authenticated.
   - `select has_table_privilege('service_role', 'vault.decrypted_secrets', 'select');`
     and `select extname from pg_extension;`
3. Code: `python3 -m pytest collector/tests/ tagchecker/tests/ -q`, then
   `cd web && npm ci && npx tsc --noEmit && npm audit`; search for new
   outbound paths (`urlopen`, `requests.`, `smtplib`, `print(`) and new
   `endswith(` recipient checks.
4. GitHub: visibility, workflow permissions, environments, and a spot check
   of recent logs and artifacts for customer data.
5. Walk the settings list in plan step 5.
6. Secrets scan with a redacting tool such as `gitleaks detect --redact`.
   Never paste a match; report the file and line only.
