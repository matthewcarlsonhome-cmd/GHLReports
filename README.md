# GHL Account Health Dashboard

A read-only account health dashboard for Small Screen Producer's GoHighLevel
subaccounts. A Python collector runs nightly, pulls CRM data from the GHL
API v2 into Supabase Postgres, computes health metrics, per-form/survey
health, pipeline intelligence, and flags; a separate daily tag checker
verifies each client website's tracking pixels actually fire. A React SPA
(on Netlify; custom domain + in-GHL embed as a later phase) gives account
managers a triage header, a plain-English team report, and per-account
drilldowns with charts — which accounts need a call and why. AMs can
acknowledge flags (with a note and a snooze) and keep append-only account
notes; those are the only writes in the whole system.

> **Going live? Start here → [`docs/GO-LIVE.md`](docs/GO-LIVE.md)** — the
> complete browser-only setup (Supabase ✓ already provisioned, Netlify,
> GitHub Actions, GHL). **No terminal or local install is needed to deploy
> or operate this system**: the app deploys from GitHub via Netlify, the
> Python collector and tag checker run as GitHub Actions workflows, and
> one-off operations (probe / backfill) are triggered from the Actions tab
> (the args box feeds `COLLECTOR_ARGS`). Render (`render.yaml`) remains a
> documented alternative scheduler.

- **Go-live setup guide (browser-only):** [`docs/GO-LIVE.md`](docs/GO-LIVE.md)
- **Design spec (historical requirements):** [`docs/DESIGN.md`](docs/DESIGN.md) (build spec v3.0)
- **As-built architecture (current):** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Forms/tags integration review + design:** [`docs/FORMS-INTEGRATION.md`](docs/FORMS-INTEGRATION.md)
- **API verification log:** [`VERIFICATION.md`](VERIFICATION.md) — the probe appends here

Hard guarantees: the collector never writes to GHL; no LLM touches any
number; every count carries a coverage label; missing data reads "Unknown"
or "no data", never zero; the database stores **no phone numbers, emails
(other than staff), or message text** — ever; Private Integration Tokens
live only in Supabase Vault.

## Repository layout

| Path | What it is |
|---|---|
| `supabase/migrations/` | Complete schema (0001) + additive migrations 0002–0006 (missed calls, form_health, tag_checks, pipeline movement, bottleneck) — all applied to the live project |
| `supabase/seed.sql` | Parent + pilot subaccount rows (edit the placeholders first) |
| `collector/` | Python 3.11 collector, tools, and test suite (incl. the PII boundary test) |
| `tagchecker/` | Playwright tag/pixel checker (no GHL tokens; own requirements.txt) |
| `.github/workflows/` | The schedulers: nightly collector + daily tag checker (manual dispatch too) |
| `web/` | Vite + React + TypeScript + Tailwind + Recharts SPA |
| `render.yaml` | OPTIONAL alternative scheduler (GitHub Actions is primary) |
| `netlify.toml` | Netlify build config + iframe CSP for the SPA |
| `docs/` | Go-live guide, design spec, as-built architecture, forms/tags integration |

---

## 1. Install — developer machines (OPTIONAL — not needed to deploy or operate)

Deployment and daily operation are 100% browser-based (see
[`docs/GO-LIVE.md`](docs/GO-LIVE.md)); this section exists only for someone
who wants to *develop* the code locally — run the test suite, hack on the
UI with hot reload, or dry-run the collector against fixtures.

Two runtimes are used: **Python 3.11+** (collector) and **Node.js 20+**
(web app). Install both, clone, then set up each part.

### Windows 10/11

```powershell
# with winget (built into Windows 11 / current Windows 10)
winget install Python.Python.3.11
winget install OpenJS.NodeJS.LTS
winget install Git.Git
# reopen the terminal so PATH updates apply
python --version   # 3.11.x
node --version     # v20+ (LTS)

git clone https://github.com/matthewcarlsonhome-cmd/GHLReports.git
cd GHLReports

# Collector (virtual environment recommended)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r collector\requirements.txt
pip install pytest
pytest collector\tests -q        # 56 tests should pass with no network

# Web app
cd web
npm install
cd ..
```

If `Activate.ps1` is blocked, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then retry.

### macOS

```bash
# with Homebrew (https://brew.sh)
brew install python@3.11 node@20 git
brew link node@20   # if brew tells you to

git clone https://github.com/matthewcarlsonhome-cmd/GHLReports.git
cd GHLReports

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r collector/requirements.txt
pip install pytest
pytest collector/tests -q

cd web && npm install && cd ..
```

### Linux (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip git curl
# Node 20 LTS via NodeSource (apt's node is usually too old)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

git clone https://github.com/matthewcarlsonhome-cmd/GHLReports.git
cd GHLReports

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r collector/requirements.txt
pip install pytest
pytest collector/tests -q

cd web && npm install && cd ..
```

(Any distro works — the requirements are Python ≥3.11 with `venv` and Node
≥20. On Fedora: `sudo dnf install python3.11 nodejs20 git`.)

### Environment files

```bash
cp .env.example .env          # collector: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, COLLECTOR_KEY
cp web/.env.example web/.env  # web: VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY
```

Fill the values from the Supabase project (section 2 below). Both `.env`
files are git-ignored. Exactly three collector secrets, ever — PITs never
live in env vars or files.

---

## 2. Cloud setup — the exact console steps

These are the only parts that need a human in a browser (spec section 12).
Do them in order.

### Supabase

1. Create a **new project** (region us-central or east). Copy the Project
   URL, anon key, and service role key. Organization settings → **enforce
   MFA for all members**; keep the member list minimal.
2. **SQL editor** → paste the entire `supabase/migrations/0001_init.sql`,
   run it. Then edit and run `supabase/seed.sql` (real location IDs, names,
   slugs, verticals, services, AM emails, each client's contact ID in the
   SSP account, and `mrr` / `contract_end` where known). Then run the
   post-migration checks (end of spec section 6):
   - Settings → API → Exposed schemas lists only `public` (+ `graphql_public`); `vault` is not exposed.
   - `select public.pit_key_ok('wrong')` returns false and leaves a `read_denied` row in `pit_audit`.
   - Database → Replication: no table from this schema is in `supabase_realtime`.
   - An anon-key REST call to `v_portfolio` is denied, not rows.
3. **Authentication → URL Configuration**: Site URL = wherever the SPA is
   served — your `https://<site>.netlify.app` URL at first, swapped to
   `https://health.smallscreenproducer.com` once the custom domain is live
   (see [`docs/GO-LIVE.md`](docs/GO-LIVE.md) for that ordering); Additional
   Redirect URLs: `http://localhost:5173` (dev only).
4. **Authentication → Providers**: Email only; every third-party provider
   (Google, GitHub, etc.) disabled. **Authentication → Settings: Allow new
   users to sign up: OFF.** Sessions: Inactivity timeout 30 days.
5. **Authentication → Users → Add user** for each of the ~11 staff (work
   email, "Auto Confirm User" on). This is the allowlist; the domain trigger
   is the backstop.
6. **Authentication → Email**: set **OTP expiry to 600 seconds**. Two things
   make the code flow work (the first at team scale, the second always):
   - **(a) Email delivery** — Supabase's built-in mailer only delivers to
     members of your Supabase organization and only a few emails per hour:
     fine for one admin (invite your work email under Organization → Team),
     not for a staff rollout. For the team, set custom SMTP to your own
     Google Workspace: host `smtp.gmail.com`, port 465, username = a real
     mailbox (e.g. `health@smallscreenproducer.com`), password = a Google
     App Password (myaccount.google.com/apppasswords, needs 2-Step
     Verification), sender = the same mailbox. No third-party email
     service.
   - **(b) Email Templates → Magic Link AND Confirm signup** — both bodies
     must include `{{ .Token }}` (e.g. `Your sign-in code is {{ .Token }}.
     It expires in 10 minutes.`); if either template lacks the token the
     email carries only a link and the 6-digit input has nothing to accept.
7. Optional later: Integrations → Cron → enable pg_cron, then run the
   commented retention block at the end of the migration.

### Collector key + PITs (all in the Supabase Vault UI)

1. Generate `COLLECTOR_KEY` locally:
   `python -c "import secrets;print(secrets.token_urlsafe(32))"`.
   Supabase Dashboard → Project Settings → **Vault** → Add new secret:
   Name `collector_key`, Secret = that value. Put the same value in your
   local git-ignored `.env` and later in Render. Nowhere else.
2. In GHL, create the PIT in the SSP subaccount and in each pilot subaccount
   — inside that subaccount: Settings → Private Integrations → Create new
   Integration, name `ssp-health-readonly`, **read-only scopes only**:
   `locations`, `users`, `contacts`, `conversations` (+ messages),
   `opportunities`, `pipelines`, `calendars` (+ events),
   `forms`, `surveys`, `workflows` (view), `blogs` (list + posts),
   `social planner` (posts + accounts). Never any write scope, and nothing
   billing-related — no invoices, payments, campaigns, saas, or snapshots.
   For each: Vault → Add new secret: Name **exactly**
   `ghl_pit_<location_id>` (the GHL location ID, not the slug), Secret =
   the PIT, Description = the client name. Close the GHL tab — the token
   now exists in exactly one place.
3. Sanity check in the SQL editor:
   `select public.get_pit('<location_id>', '<collector_key>') is not null;`
   should return true for each.

The naming convention is the contract: `get_pit` looks up
`ghl_pit_<location_id>` and nothing else; a typo reads as a missing token
and `/runs` will say which location returned none. The optional CLI
(`python -m collector.tools.pit set|rotate|delete|status`) wraps the same
RPCs for anyone who prefers a terminal.

### First run — verify fields before trusting anything

```bash
python -m collector.main --probe --location ssp   # appends to VERIFICATION.md
# fix any field-name drift in collector/fetchers.py, rerun --probe, then:
python -m collector.main --location ssp --max-pages 2   # small live run
python -m collector.main                                 # full run, all locations
python -m collector.main --backfill 12                   # 12 weeks of lead_history for charts
```

Budget 30–60 minutes on `--probe`; it is where the schedule risk lives
(spec 7.4). Exit codes: 0 all ok, 2 any location held or failed, 1 crash.
Acceptance for the first full run: snapshots with `gate_passed=true`,
coverage complete for ≥ 8 sources, and
`select details::text from snapshots` contains no `@` or phone-shaped
strings (the PII boundary, also enforced by `tests/test_pii.py`).

### Render (collector cron)

New → **Blueprint** → select this repo; Render reads `render.yaml` and
creates the cron job (a manually created Cron Job ignores `render.yaml`).
Add env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `COLLECTOR_KEY`
(mark secret). That is all. Trigger a manual run and check `/runs` in the
dashboard. **Turn on failure notifications** for the service — that is the
dead-man's switch for "no run happened." Team MFA on. The schedule
`30 10 * * *` UTC is 05:30 Central during CDT — revisit the first week of
November.

Zero-cost alternative: a GitHub Actions `schedule:` workflow with the same
three repository secrets (best-effort timing; fine for daily).

### Netlify (SPA)

Add new site → import this repo → base `web`, build `npm ci && npm run
build`, publish `dist` (already in `netlify.toml`). Env:
`VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (the anon key is public by
design; RLS is the boundary). Domain management → add
`health.smallscreenproducer.com`. Netlify provisions TLS after DNS
resolves. Do NOT set `X-Frame-Options` anywhere — the `frame-ancestors` CSP
in `netlify.toml` is the frame policy.

### DNS (wherever smallscreenproducer.com is hosted)

- `CNAME health → <your-site>.netlify.app`

Propagation takes minutes to an hour.

### GHL Custom Menu Link (so nobody leaves GHL)

Agency view → Settings → Custom Menu Links → Create New:

- Title `Account Health`, URL
  `https://health.smallscreenproducer.com/?embed=1&hint={{user.email}}`,
  open in an **Embedded Page (iFrame)**, show in the sub-account sidebar for
  the **Small Screen Producer** subaccount only.
- Optional per-client link: title `Account Health (this account)`, URL
  `https://health.smallscreenproducer.com/account/{{location.id}}?embed=1&hint={{user.email}}`,
  Admin role only.
- Test as an AM at `crm.smallscreenproducer.com` (the white-label domain —
  same-site with the app, so one sign-in per device covers both). If the
  iframe stays blank, check the console for a `frame-ancestors` violation
  and extend the CSP host list in `netlify.toml`. Fallback: set the link's
  open mode to New tab — same URL, same auth.

---

## 3. Day-to-day usage

**Operations happen in the browser.** The collector runs itself nightly via
GitHub Actions; one-off modes run from the repo's **Actions tab → Nightly
collector → Run workflow**, typing the args in the box (e.g. `--probe`,
`--backfill 12`, `--location <slug>`) and reading the run's log. The tag
checker runs daily the same way. (On the optional Render alternative, the
same modes go through the `COLLECTOR_ARGS` environment variable + Trigger
Run.) Token health lives on the app's `/runs` page; tokens are added and
rotated in the Supabase Vault UI.

For developers with the optional local setup:

```bash
# local dev
cd web && npm run dev                      # SPA at http://localhost:5173
python -m collector.main --dry-run --location pilot1   # fetch + compute, write nothing

# the same modes the Render dashboard triggers
python -m collector.main                   # full daily run (what the cron does)
python -m collector.main --backfill 12     # (re)build lead_history charts
python -m collector.main --digest --dry-run  # preview the per-AM digest emails

# tests
pytest collector/tests -q                  # collector suite (no network)
cd web && npm run build                    # typecheck + production build
```

**Onboarding a new client is three steps** (no deploys): create the
read-only PIT in that subaccount → add Vault secret
`ghl_pit_<location_id>` → insert one `subaccounts` row (location_id, name,
slug, vertical, services, am_email, ssp_client_contact_id, mrr,
contract_end).

**Finding `ssp_client_contact_id`**: in the SSP subaccount, open Contacts,
search the client's company, open the record — the ID is the last path
segment of the URL (`/contacts/detail/<id>`), or run
`python -m collector.tools.find_client_contact --q "Company"`.

**Rotation (every 90 days)**: create the new PIT in GHL first (old + new
stay valid for 7 days), then replace the value of the same Vault secret in
the UI. The collector reads the Vault's `updated_at` every run, so
`token_rotated_at` and the 80-day warning stay correct automatically.

**Where is each form embedded?** GHL doesn't store form placement, but
every submission records the page it came from, and two commands turn that
into a CSV mapping each form to the page URLs it was submitted from — the
input the uptime browser checks need:

- **On Render (no local setup)**: set `COLLECTOR_ARGS` to `--form-urls`
  (optionally plus `--location <slug>` or `--form-urls-days 60`), trigger a
  manual run, and copy the CSV block printed in the log between the
  `===== form-urls.csv BEGIN/END =====` markers. Remember to clear
  `COLLECTOR_ARGS` afterward or the nightly run repeats it.
- **Locally**: `python -m collector.tools.form_urls` (options:
  `--location <slug>`, `--days 30`, `--out form-urls.csv`).

Forms with no submissions in the window show as `silent`: their placement
exists nowhere in GHL, so get the page from the client team or by crawling
the site for `/widget/form/<id>` iframes.

## 4. Security model (short version)

- PITs are read-only, one per subaccount, hand-loaded into Supabase Vault
  (encrypted at rest), retrievable only via `security definer` RPCs that
  require both the service role **and** `COLLECTOR_KEY`; every access is
  audited in `pit_audit`.
- The database stores names, IDs, timestamps, amounts, and deep links —
  never a contact's phone, email, or message text. The fetch layer strips
  them before anything touches a row, and `test_pii.py` enforces it.
- `anon` holds zero object privileges. `authenticated` staff can read, and
  can insert (never update or delete) acknowledgements and notes, with RLS
  pinning the author to the JWT email. Sign-ups are disabled; staff are
  pre-provisioned; the `auth.users` triggers are the backstop.
- Residual exposure, stated plainly: Supabase dashboard members (postgres
  role) and the service role key can read everything including decrypted
  Vault secrets. Mitigation is organizational — minimal members, MFA
  enforced on the Supabase org and Render team. If the service role key
  leaks: rotate it in Supabase and regenerate `COLLECTOR_KEY`; PITs do not
  need reissuing because the leaked key alone could not read them.

## 5. Definition of done (v1)

An AM opens GHL → SSP subaccount → Account Health, sees their accounts
ranked, clicks into one, reads the top action, clicks a stale opportunity,
lands on that record in GHL, comes back and acknowledges the flag with a
note. Data is at most 24 h old, and every number they see either has a
coverage label of complete or is visibly marked otherwise.
