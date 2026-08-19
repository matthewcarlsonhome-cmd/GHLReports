# Go-Live Guide — everything in the browser, live today

This is the complete, in-order setup to take the Account Health dashboard
live. **Every step happens in a web dashboard — no terminal, no local
installs.** The app runs on Netlify, the Python collector runs on GitHub Actions, and
the database runs on Supabase. Sign-in emails come straight from Supabase's
built-in mailer — no third-party email service to configure.

**Today's finish line is your free Netlify URL** —
`https://<your-site>.netlify.app`. That address is the dashboard until you
choose to add the custom domain. Everything tied to
`smallscreenproducer.com` — the `health.` subdomain, DNS records, and
embedding the app inside GoHighLevel — is deliberately parked in the
**Later phase** at the end of this guide. Nothing in Parts 1–6 touches DNS
or GHL's menu settings.

(One clarification so nothing gets skipped by accident: Part 3 has you create
a **read-only API token** inside GoHighLevel. That token is how the collector
reads the data — it is required today and has nothing to do with the
"embed the dashboard in GHL" integration, which is the Later phase.)

Estimated hands-on time today: **45–60 minutes**, no DNS wait.

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

## Part 1 — Netlify: deploy the web app first (~10 min)

Deploying first means you have the site's URL in hand when Supabase asks for
it in Part 2.

1. https://app.netlify.com → **Add new site → Import an existing project**
   → **GitHub** → authorize → pick `matthewcarlsonhome-cmd/GHLReports`.
2. Build settings auto-fill from `netlify.toml` — confirm they show:
   Base directory `web` · Build command `npm ci && npm run build` ·
   Publish directory `dist`. Branch: the repo default.
3. **Environment variables** (Site configuration → Environment variables):
   - `VITE_SUPABASE_URL` = `https://tpavdifpsevkrubplyrg.supabase.co`
   - `VITE_SUPABASE_ANON_KEY` = the anon key from the top of this guide
4. **Deploy.** When the first deploy finishes, Netlify gives you a
   `https://<something>.netlify.app` URL with TLS already working. (Site
   configuration → Site details → **Change site name** if you want a nicer
   name like `ssp-account-health.netlify.app` — do that *now*, before you
   paste the URL anywhere else.)
5. **Copy that URL.** It is the dashboard's address for today — you'll paste
   it into Supabase in the next part. The page will load already (you can't
   sign in yet — that's Part 2).

---

## Part 2 — Supabase auth settings (~15 min)

Open https://supabase.com/dashboard/project/tpavdifpsevkrubplyrg and work
down this list:

1. **Authentication → URL Configuration**
   - Site URL: your Netlify URL from Part 1, e.g.
     `https://<your-site>.netlify.app`
   - Additional Redirect URLs: `http://localhost:5173` (harmless; used only if
     a developer ever runs the app locally)
   - (When the custom domain goes live in the Later phase, you'll come back
     and swap the Site URL to `https://health.smallscreenproducer.com`.)
2. **Authentication → Sign In / Providers**
   - Email: **enabled**. Every third-party provider (Google, GitHub, …):
     **disabled**.
   - **Allow new users to sign up: OFF.** (Staff are pre-created in step 4;
     nobody else can ever make an account. The database trigger that rejects
     non-`@smallscreenproducer.com` emails is already installed as a backup.)
   - Sessions → Inactivity timeout: **30 days**.
3. **Authentication → Sign In / Providers → click the "Email" provider row**
   - Email OTP Expiration: **600 seconds** (the sign-in code lives 10
     minutes).
4. **Authentication → Users → Add user** — today, just **yourself**:
   - Your work email (must end `@smallscreenproducer.com`), toggle
     **Auto Confirm User** ON, no password needed — sign-in is by emailed
     code.
   - **Make the sign-in email actually arrive:** Supabase's built-in mailer
     only delivers to members of your Supabase **organization** (and only a
     few emails per hour). So also do: dashboard top-left → your
     organization → **Team → Invite member** → the same work email → accept
     the invite from that inbox. Two clicks, and now your login codes get
     delivered.
   - The rest of the staff get added in the Later phase — the built-in
     mailer's limits make it a one-person setup today, and the Later phase
     covers team-scale email using your own Google Workspace (still no
     third-party service).
5. **Authentication → Emails (under NOTIFICATIONS) → Templates tab** — edit
   **both** "Magic Link" AND "Confirm signup" so the body contains the
   token, e.g.:
   > `Your sign-in code is {{ .Token }}. It expires in 10 minutes.`
   If either template lacks `{{ .Token }}`, the email arrives with only a
   link and the 6-digit input on the login page has nothing to accept.
6. Quick sanity check: **Settings → API → Exposed schemas** should list only
   `public` (and `graphql_public`) — `vault` must NOT be listed. (Verified
   at provisioning time; just confirm nothing changed.)

---

## Part 3 — Secrets into the Vault (~10 min)

Still in the Supabase dashboard:

1. **Make a collector key.** Any long random string (32+ characters, no
   spaces or quotes). Browser-only options: your password manager's
   generator, or https://bitwarden.com/password-generator/ set to 40
   characters. Copy it — it's needed in two places (here and a GitHub
   secret in Part 4).

   *Why this exists:* the collector has to hold a Supabase admin key to write
   snapshots. The GHL tokens are far more sensitive than the snapshots, so
   they sit behind a second lock — the Vault function that returns a GHL
   token demands this collector key too, and logs every attempt (right or
   wrong) to `pit_audit`. If the Supabase admin key ever leaks, the leaker
   still can't read your clients' CRM tokens, and you'd see the failed
   attempts in the audit log.
2. **Project Settings → Vault → Add new secret**
   - Name: `collector_key` (exactly)
   - Secret: the random string from step 1
3. **Create the GHL token for the SSP account.** (This is the read-only API
   token mentioned in the intro — required for data collection; it does not
   embed anything in GHL.) In GoHighLevel, open the **Small Screen
   Producer** subaccount → Settings → **Private Integrations** → Create new
   Integration:
   - Name: `ssp-health-readonly`
   - Scopes — **read-only/view only**, check these modules: locations,
     users, contacts, conversations (+ messages), opportunities,
     **pipelines**, calendars (+ events), invoices, forms, **surveys**,
     **workflows (view)**, blogs (list + posts), social planner (posts +
     accounts). **Never** any write scope, campaigns, payments, saas, or
     snapshots. (Surveys and Workflows view power the per-form health
     checks; Pipelines view guarantees the stage-structure fetch; a token
     missing any of these still works — the affected checks just show
     "scope not yet granted".)
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

Pilot client subaccounts repeat steps 3–4 later (Part 6) — SSP alone is
enough to go live.

---

## Part 4 — GitHub Actions: the Python collector (~10 min)

The nightly data pull runs as a **GitHub Actions workflow** — no extra
hosting account, it lives right in the repo
(`.github/workflows/collector.yml`) and runs on GitHub's machines.

1. **Add the three secrets.** GitHub repo
   (`matthewcarlsonhome-cmd/GHLReports`) → **Settings → Secrets and
   variables → Actions → New repository secret**, three times:
   - `SUPABASE_URL` = `https://tpavdifpsevkrubplyrg.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY` = Supabase dashboard → **Settings → API →
     `service_role`** key (this one is secret — it lives only here)
   - `COLLECTOR_KEY` = the random string from Part 3
2. **Enable workflows** if prompted: repo → **Actions** tab → if GitHub
   shows an "enable workflows" banner, click it. You should see two
   workflows listed: **Nightly collector** and **Tag checker**.
3. **First run — verify the API field names (the "probe").** The GHL API
   shapes were implemented from documentation; the probe confirms them
   against reality:
   - Actions → **Nightly collector** → **Run workflow** → type `--probe`
     in the args box → green **Run workflow** button.
   - When it finishes, click the run → the **Run collector** step — the
     full probe report prints there, ending with a checklist. Phones,
     emails, and message bodies are redacted automatically.
   - If every endpoint shows HTTP 200, you're good. (A 401/403 on Surveys
     or Workflows just means that scope isn't granted yet — Part 3.3.) If
     anything else looks off, paste the log into a Claude session against
     this repo and ask it to reconcile `VERIFICATION.md`.
4. **Backfill the charts**: Run workflow again with args `--backfill 12` —
   the log should say "backfilled 12 lead_history weeks".
5. **First full collection**: Run workflow once more with the args box
   **empty**. The log should end `done: N ok, 0 held, 0 failed, ...`
   (accounts still awaiting tokens are listed separately and don't fail
   the run). From now on it runs itself daily at 5:30am CT, and GitHub
   emails you if a scheduled run fails — that's the dead-man's switch.
6. **If you previously created the Render cron job, suspend or delete it**
   (Render dashboard → the service → Settings → Suspend). Two schedulers
   would collect twice a night. `render.yaml` stays in the repo as an
   optional alternative.

The **Tag checker** workflow needs no extra setup — it reuses the same
secrets and runs daily at 8am CT, checking that each configured client
website actually fires its tracking tags (GA4/GTM/Meta pixel). It does
nothing for accounts until tag expectations are configured in the database.

---

## Part 5 — End-to-end verification checklist (today)

All of this happens at your `https://<your-site>.netlify.app` URL:

- [ ] The Netlify URL loads over TLS
- [ ] You can sign in with an email code; an address NOT pre-created in
      Part 2.4 gets "Not a registered staff account"
- [ ] The banner reads "Data as of …" (proves a collector run landed)
- [ ] Portfolio: tick **Include SSP** — the SSP row shows real numbers, not
      "no data"
- [ ] `/runs` shows the collector run with per-location detail
- [ ] Click into SSP → deep links open the right records in GHL
- [ ] Acknowledge a flag with a note → it drops out of "needs attention"
- [ ] Add an account note → it appears newest-first

That's live. The Later phase below adds the custom domain, the rest of the
staff, and the in-GHL embed whenever you're ready.

## Part 6 — Onboard each pilot client (~5 min each)

1. In that client's GHL subaccount: create the `ssp-health-readonly`
   Private Integration (same read-only scopes as Part 3.3); copy the token.
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
   GitHub (**Actions → Nightly collector → Run workflow**, args empty).

---

## Later phase — custom domain, full staff, and the GHL embed

Do this whenever you're ready to roll the dashboard out to the whole team
and put it inside GoHighLevel. It's one sitting (~30 min hands-on) plus DNS
propagation (minutes to an hour). Until then, the Netlify URL keeps working
— nothing breaks by waiting.

### L1 — Netlify: add the custom domain (~2 min)

**Domain management → Add a domain → `health.smallscreenproducer.com`.**
Netlify shows the DNS target (your `<site>.netlify.app` host) and
provisions the TLS certificate automatically once DNS resolves.

### L2 — DNS (~2 min, then wait)

Wherever `smallscreenproducer.com` DNS is hosted (registrar or Cloudflare),
add one record:

| Type | Name | Value |
|---|---|---|
| CNAME | `health` | `<your-site>.netlify.app` (from L1) |

Propagation is usually minutes. When done, Netlify shows the domain as
secured (TLS issued).

### L3 — Supabase: swap the Site URL and add the rest of the staff

1. **Authentication → URL Configuration** → Site URL:
   `https://health.smallscreenproducer.com`.
2. **Team-scale sign-in emails, using your own email account** (the
   built-in mailer won't deliver to staff who aren't Supabase org members):
   Supabase → **Authentication → Emails → SMTP Settings tab** and point it
   at your existing Google Workspace —
   - Host `smtp.gmail.com` · Port `465` · Username = a real mailbox you
     control (e.g. `health@smallscreenproducer.com` or your own address) ·
     Password = a **Google App Password** for that mailbox (create at
     https://myaccount.google.com/apppasswords — requires 2-Step
     Verification to be on) · Sender = the same mailbox.
   - No new services, no DNS records — sign-in codes now come from your own
     Google account, which is already authorized to send as your domain.
3. **Authentication → Users → Add user** for the remaining staff (~11
   total, work emails, Auto Confirm ON).

### L4 — Put it inside GoHighLevel (~5 min)

Only do this after L1–L2: sign-in **inside the GHL iframe** depends on the
custom domain being same-site with `crm.smallscreenproducer.com`.

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

### Later-phase checklist

- [ ] `https://health.smallscreenproducer.com` loads over TLS
- [ ] A sign-in email arrives promptly, sent from your own mailbox
- [ ] Every staff member is pre-created and can sign in
- [ ] Inside GHL: the iframe renders and you're already signed in

## Parked: Monday digest emails

The collector contains an optional Monday-morning email digest per account
manager. It is **off** and stays off until an email-sending decision is
made — it does nothing unless explicitly configured, and no setup step in
this guide enables it. The **Weekly summary** button on each account page
covers the same need with zero email plumbing: it builds the copy-ready
client update in the browser.

## Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| Netlify deploy failed / built old code | **Deploys → Trigger deploy → Clear cache and deploy site** — this rebuilds the latest commit of the tracked branch. Check the deploy row shows the newest commit hash, and Site configuration → Build & deploy → Branches has Production branch = the repo default. Every future `git push` deploys automatically. Still failing → open the failed deploy's log and read the first red error |
| Login email never arrives | The built-in mailer only delivers to Supabase **organization members** — accept the Team invite from Part 2.4; it also sends only a few emails per hour, so wait a bit and check spam. Team-scale fix: the Google Workspace SMTP step (L3) |
| Email arrives with a link but no code | `{{ .Token }}` missing from a template (Part 2.5) |
| Account shows "no data — token" | Vault secret name typo — must be `ghl_pit_<location_id>` exactly |
| A source shows SOURCE UNAVAILABLE / 403 in `/runs` | The PIT lacks that scope — edit the Private Integration's scopes in GHL (no new token) and re-run |
| Collector exits 1 "COLLECTOR_KEY rejected" | The `COLLECTOR_KEY` GitHub secret ≠ the Vault `collector_key` value |
| A check shows "scope not yet granted" | Add the Surveys / Workflows **view** scope to that account's Private Integration (Part 3.3) — no new token needed |
| Blank iframe in GHL | `frame-ancestors` CSP (check browser console) or you opened GHL at app.gohighlevel.com — use crm.smallscreenproducer.com |
| Numbers look wrong after first run | Read the probe report in the Actions run log; field-name drift goes to `VERIFICATION.md` — hand the log to Claude to fix the fetchers |
