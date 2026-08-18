# Go-Live Guide — everything in the browser, live today

This is the complete, in-order setup to take the Account Health dashboard
live. **Every step happens in a web dashboard — no terminal, no local
installs.** The app runs on Netlify, the Python collector runs on Render, and
the database runs on Supabase.

Estimated hands-on time: **60–90 minutes**, plus DNS propagation (minutes to
an hour) running in the background.

## Already done (nothing to do here)

The Supabase leg is live — provisioned and verified from the build session:

| Item | Value |
|---|---|
| Project | `ghl-health` (org: matthewcarlsonhome-cmd's Org), region us-east-2 |
| Dashboard | https://supabase.com/dashboard/project/tpavdifpsevkrubplyrg |
| Project URL | `https://tpavdifpsevkrubplyrg.supabase.co` |
| Anon (public) key | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRwYXZkaWZwc2V2a3J1YnBseXJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcwNzYyMzksImV4cCI6MjEwMjY1MjIzOX0.fIdD33VdjstbB9zyQGynErXCj1KX-Cm1FByR6BZlbNs` |

Both migrations are applied (all tables, views, RLS, Vault functions), the
SSP parent row is seeded, the security checks passed, and the security
advisors are clean. The anon key above is *designed* to be public — row-level
security is what protects the data — so it is safe to paste into Netlify.

The GitHub repo is `matthewcarlsonhome-cmd/GHLReports`, branch
`claude/gohighlevel-reports-build-l6hlc7` (the repo's default branch).

---

## Part 1 — Supabase auth settings (~15 min)

Open https://supabase.com/dashboard/project/tpavdifpsevkrubplyrg and work
down this list:

1. **Authentication → URL Configuration**
   - Site URL: `https://health.smallscreenproducer.com`
   - Additional Redirect URLs: `http://localhost:5173` (harmless; used only if
     a developer ever runs the app locally)
2. **Authentication → Sign In / Providers**
   - Email: **enabled**. Every third-party provider (Google, GitHub, …):
     **disabled**.
   - **Allow new users to sign up: OFF.** (Staff are pre-created in step 4;
     nobody else can ever make an account. The database trigger that rejects
     non-`@smallscreenproducer.com` emails is already installed as a backup.)
   - Sessions → Inactivity timeout: **30 days**.
3. **Authentication → Email (settings)**
   - OTP expiry: **600 seconds** (the sign-in code lives 10 minutes).
4. **Authentication → Users → Add user** — one per staff member (~11):
   - Their work email (must end `@smallscreenproducer.com`)
   - Toggle **Auto Confirm User** ON, no password needed (they sign in with
     emailed codes).
5. **Resend (email delivery) — do this now, finish DNS in Part 5**
   - Create a free account at https://resend.com → **Domains → Add Domain**
     → `smallscreenproducer.com`. Resend shows 3–4 DNS records (DKIM/SPF).
     **Keep this tab open** — you'll publish those records together with the
     Netlify record in Part 5.
   - Resend → **API Keys → Create** — copy the key somewhere safe for a
     moment (used twice below).
6. **Supabase → Authentication → Email → SMTP Settings** (custom SMTP):
   - Host `smtp.resend.com` · Port `465` · Username `resend` ·
     Password = the Resend API key · Sender `health@smallscreenproducer.com`
   - Without this, Supabase's built-in mailer only sends a few emails per
     hour — not enough for 11 people signing in.
7. **Authentication → Email Templates** — edit **both** "Magic Link" AND
   "Confirm signup" so the body contains the token, e.g.:
   > `Your sign-in code is {{ .Token }}. It expires in 10 minutes.`
   If either template lacks `{{ .Token }}`, the email arrives with only a
   link and the 6-digit input on the login page has nothing to accept.
8. Quick sanity check: **Settings → API → Exposed schemas** should list only
   `public` (and `graphql_public`) — `vault` must NOT be listed. (Verified
   at provisioning time; just confirm nothing changed.)

---

## Part 2 — Secrets into the Vault (~10 min)

Still in the Supabase dashboard:

1. **Make a collector key.** Any long random string (32+ characters, no
   spaces or quotes). Browser-only options: your password manager's
   generator, or https://bitwarden.com/password-generator/ set to 40
   characters. Copy it — it's needed in two places (here and Render).
2. **Project Settings → Vault → Add new secret**
   - Name: `collector_key` (exactly)
   - Secret: the random string from step 1
3. **Create the GHL token for the SSP account.** In GoHighLevel, open the
   **Small Screen Producer** subaccount → Settings → **Private Integrations**
   → Create new Integration:
   - Name: `ssp-health-readonly`
   - Scopes — **read-only/view only**, check these modules: locations,
     users, contacts, conversations (+ messages), opportunities, calendars
     (+ events), invoices, forms, blogs (list + posts), social planner
     (posts + accounts). **Never** any write scope, workflows, campaigns,
     payments, saas, or snapshots.
   - Copy the token (it is shown once).
4. Back in Supabase **Vault → Add new secret**:
   - Name: `ghl_pit_ZnckuEDPIcWu8fn72ppi` (exactly — that is the SSP
     location ID; the `ghl_pit_<location_id>` naming is the contract the
     collector looks up)
   - Secret: the PIT you just copied. Close the GHL tab — the token now
     exists in exactly one place.
5. Optional sanity check, **SQL Editor** → run (paste your real key):
   ```sql
   select public.get_pit('ZnckuEDPIcWu8fn72ppi', '<your collector_key>') is not null;
   ```
   Should return `true`.

Pilot client subaccounts repeat steps 3–4 later (Part 8) — SSP alone is
enough to go live.

---

## Part 3 — Render: the Python collector (~10 min)

1. https://dashboard.render.com → **New → Blueprint** → **Connect GitHub**
   (authorize access to `matthewcarlsonhome-cmd/GHLReports`) → select the
   repo. Render reads `render.yaml` and proposes the cron job
   `ghl-health-collector`. (Important: use **Blueprint**, not "New → Cron
   Job" — a manually created job ignores `render.yaml`.)
2. When prompted for environment variables, enter the three secrets:
   - `SUPABASE_URL` = `https://tpavdifpsevkrubplyrg.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY` = Supabase dashboard → **Settings → API →
     `service_role`** key (this one is secret — it lives only here)
   - `COLLECTOR_KEY` = the random string from Part 2
   Click **Apply / Create**. The schedule (`30 10 * * *` UTC = 5:30am
   Central) and everything else come from the repo.
3. Open the service → **Settings → Notifications** → turn **failure
   notifications ON** (your dead-man's switch for "no run happened").
4. **First run — verify the API field names (the "probe").** The GHL API
   shapes were implemented from documentation; the probe confirms them
   against reality:
   - Service → **Environment** → add `COLLECTOR_ARGS` = `--probe`
   - Click **Trigger Run** (top right). When it finishes, open **Logs** —
     the full probe report prints there, ending with a checklist. Phones,
     emails, and message bodies are redacted automatically.
   - If every endpoint shows HTTP 200, you're good. If anything looks off
     (a 401/403 means a missing scope on the Private Integration — edit its
     scopes in GHL, no new token needed), or the checklist items look
     wrong, paste the log into a Claude session against this repo and ask
     it to reconcile `VERIFICATION.md`.
5. **Backfill the charts** (12 weeks of history from the CRM):
   - Change `COLLECTOR_ARGS` to `--backfill 12` → **Trigger Run** → logs
     should say "backfilled 12 lead_history weeks".
6. **First full collection:**
   - **Delete/clear `COLLECTOR_ARGS`** → **Trigger Run**. Logs should end
     with something like `done: 1 ok, 0 held, 0 failed, ...`.
   - From now on it runs itself daily at 5:30am CT.

---

## Part 4 — Netlify: the web app (~10 min)

1. https://app.netlify.com → **Add new site → Import an existing project**
   → **GitHub** → authorize → pick `matthewcarlsonhome-cmd/GHLReports`.
2. Build settings auto-fill from `netlify.toml` — confirm they show:
   Base directory `web` · Build command `npm ci && npm run build` ·
   Publish directory `dist`. Branch: the repo default.
3. **Environment variables** (Site configuration → Environment variables):
   - `VITE_SUPABASE_URL` = `https://tpavdifpsevkrubplyrg.supabase.co`
   - `VITE_SUPABASE_ANON_KEY` = the anon key from the top of this guide
4. **Deploy.** When the first deploy finishes you get a
   `https://<something>.netlify.app` URL — the app already works there for a
   smoke test, but **don't stop at the netlify.app URL**: sign-in inside the
   GHL iframe depends on the custom domain.
5. **Domain management → Add a domain → `health.smallscreenproducer.com`.**
   Netlify shows the DNS target (your `<site>.netlify.app` host) and
   provisions the TLS certificate automatically once DNS resolves.

---

## Part 5 — DNS (~5 min, then wait)

Wherever `smallscreenproducer.com` DNS is hosted (registrar or Cloudflare),
add:

| Type | Name | Value |
|---|---|---|
| CNAME | `health` | `<your-site>.netlify.app` (from Part 4.5) |
| TXT/CNAME × 3–4 | per Resend | the DKIM/SPF records from Part 1.5 |

Propagation is usually minutes. When done: Netlify shows the domain as
secured (TLS issued), and Resend shows the domain as **Verified** — go back
to Supabase SMTP (Part 1.6) only if you skipped it waiting on this.

---

## Part 6 — Put it inside GoHighLevel (~5 min)

GHL **Agency** view → Settings → **Custom Menu Links** → **+ Create New**:

- Title: `Account Health` · icon: any dashboard/pulse icon
- Link URL: `https://health.smallscreenproducer.com/?embed=1&hint={{user.email}}`
- Open mode: **Open in an Embedded Page (iFrame)**
- Show in: **Sub-account sidebar**; Show to sub-accounts: **Selected** →
  Small Screen Producer only; Roles: All (or Admin)
- Save. Optionally add the same URL as an **Agency** sidebar link too.

Test as an AM at **crm.smallscreenproducer.com** (the white-label domain —
same-site with the app, so one sign-in per device covers both): enter the
SSP subaccount → click Account Health → enter your work email → type the
6-digit code from the email. If the iframe is blank, check the browser
console for a `frame-ancestors` violation (extend the CSP list in
`netlify.toml`); worst case, set the link's open mode to "New tab".

---

## Part 7 — End-to-end verification checklist

- [ ] `https://health.smallscreenproducer.com` loads over TLS
- [ ] You can sign in with an email code; an address NOT pre-created in
      Part 1.4 gets "Not a registered staff account"
- [ ] The banner reads "Data as of …" (proves a collector run landed)
- [ ] Portfolio: tick **Include SSP** — the SSP row shows real numbers, not
      "no data"
- [ ] `/runs` shows the Render run with per-location detail
- [ ] Click into SSP → deep links open the right records in GHL
- [ ] Acknowledge a flag with a note → it drops out of "needs attention"
- [ ] Add an account note → it appears newest-first
- [ ] Inside GHL: the iframe renders and you're already signed in

## Part 8 — Onboard each pilot client (~5 min each)

1. In that client's GHL subaccount: create the `ssp-health-readonly`
   Private Integration (same read-only scopes as Part 2.3); copy the token.
2. Supabase Vault → Add secret `ghl_pit_<that_location_id>`.
3. Supabase **SQL Editor** → insert its row (edit every value):
   ```sql
   insert into public.subaccounts
     (location_id, name, slug, vertical, services, am_email,
      ssp_client_contact_id, mrr, contract_end)
   values
     ('<location_id>', '<Client Name>', '<shortslug>', 'pool_builder',
      '{content,social,ads}', '<am>@smallscreenproducer.com',
      '<their contact id in the SSP account>', 2500, '2027-01-31');
   ```
   (`ssp_client_contact_id`: in the SSP subaccount open Contacts → the
   client's record → the ID is the last part of the URL
   `/contacts/detail/<id>`. `mrr`/`contract_end` may be NULL.)
4. Next nightly run picks it up automatically — or trigger one now from
   Render (**Trigger Run**, no `COLLECTOR_ARGS` needed).

## Optional: Monday digest emails

Render → Environment → add `RESEND_API_KEY` (from Part 1.5) and
`DIGEST_FROM` = `health@smallscreenproducer.com`. Every Monday run then
emails each AM their book. To preview without sending: `COLLECTOR_ARGS` =
`--digest --dry-run` → Trigger Run → read the logs.

## Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| Login email never arrives | SMTP not configured (Part 1.6) or Resend domain unverified (Part 5) |
| Email arrives with a link but no code | `{{ .Token }}` missing from a template (Part 1.7) |
| Account shows "no data — token" | Vault secret name typo — must be `ghl_pit_<location_id>` exactly |
| A source shows SOURCE UNAVAILABLE / 403 in `/runs` | The PIT lacks that scope — edit the Private Integration's scopes in GHL (no new token) and re-run |
| Collector exits 1 "COLLECTOR_KEY rejected" | Render `COLLECTOR_KEY` ≠ the Vault `collector_key` value |
| Blank iframe in GHL | `frame-ancestors` CSP (check browser console) or you opened GHL at app.gohighlevel.com — use crm.smallscreenproducer.com |
| Numbers look wrong after first run | Read the probe report in Render logs; field-name drift goes to `VERIFICATION.md` — hand the log to Claude to fix the fetchers |
