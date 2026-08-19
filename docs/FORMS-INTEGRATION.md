# Forms Checker Integration — Review & Design (for Matthew's review)

Date: 2026-08-19 · Status: **PROPOSED — awaiting review, nothing implemented yet**

This doc covers (1) a code review of Jeorge's "MLH Forms Health Dashboard"
(`mlh-dashboard-main`, Node.js single-file server, ~2,900 lines) against the
GoHighLevel API v2, and (2) a concrete design to fold its capabilities into
the Account Health dashboard so there is one system, one login, one database,
and one nightly collection run.

---

## 1. What the reviewed tool does

A self-hosted Node.js web app (Render + GHL Custom Menu Link, shared-secret
`?key=` access) that, on demand (button click, live progress via
server-sent events):

- Lists every **form** per subaccount and, per form, fetches submission count
  + last-submission date; classifies each form **Active / Went Silent (3+
  days) / No Leads Yet / New (≤30 days)**.
- Same for **surveys**.
- Quick **CRM freshness checks**: newest contact, newest opportunity, newest
  conversation (alert if >72h old), workflow inventory (alert if leads are
  flowing but zero published workflows), and a **Facebook-lead heuristic**
  (sniffs attribution text on the latest 50 contacts).
- **Tag/pixel firing check**: loads the client's website in headless Chromium
  (Playwright) and verifies GA4 / GTM / Meta pixel / Google Ads / TikTok
  requests actually fire, optionally with the exact measurement ID.
- Acknowledgments (3-day auto-expiring, stored in a local JSON file), Excel /
  PDF / CSV exports (all hand-rolled, zero dependencies), and email reports
  via Resend or Gmail SMTP.

## 2. Review findings

### 2.1 API accuracy — what checks out

| Call | Verdict |
|---|---|
| Base URL `services.leadconnectorhq.com`, `Version: 2021-07-28`, Bearer PIT | Correct — matches ours |
| `GET /forms/?locationId=&limit=50&skip=` | Correct endpoint & paging |
| `GET /forms/submissions?locationId=&formId=&limit=1` (count from `meta.total`) | Correct — and a smart trick: 1 request per form gets total + latest date |
| `GET /surveys/?locationId=` (`surveys.readonly`) | Correct |
| `GET /surveys/submissions?locationId=&surveyId=` | Exists in v2; his code treats failure as "unknown" rather than 0 — good honest handling |
| `GET /opportunities/search?location_id=` (snake_case!) | Correct — he caught a real API quirk |
| `GET /conversations/search?locationId=&limit=1` | Correct |
| `GET /workflows/?locationId=` (`workflows.readonly`) | Correct endpoint — but see the scope conflict in §2.3 |
| Tolerant response parsing (`data.forms || data.data || …`) | Same defensive style as our fetchers — good |

Overall: the GHL API usage is **accurate**. The endpoints, headers, casing
quirks, and pagination match both the documentation and what our collector
has verified against the live API.

### 2.2 Issues found (ranked)

1. **No rate-limit handling at all.** Scans 5 accounts × 8 concurrent
   fetches with no 429 retry/backoff. GHL allows ~100 requests/10s per
   location; an account with many forms can trip this and the failed fetches
   silently classify forms as unknown/0. Our collector's token bucket +
   Retry-After handling solves this class of bug — one more reason to run
   these checks inside our collector.
2. **The Facebook regex matches "metal".** `/facebook|fb_|meta|lead ?ad|…/`
   is tested against joined attribution text; any contact whose fields
   contain "metal", "metadata", etc. counts as a Facebook lead. Needs word
   boundaries.
3. **Fixed 3-day silence threshold, no weekend awareness.** Every
   Monday-morning scan will false-alarm forms that simply don't get weekend
   submissions. (Our metrics already carry weekend-aware clock logic.)
4. **Everything durable is ephemeral.** Scan results and acknowledgments
   live in JSON files on Render's disk — wiped on every deploy; acks also
   auto-expire after 3 days. No history, no trends.
5. **All client tokens in one env var** (`GHL_TOKENS` JSON blob), plus an
   optional `secrets.json` on disk. Works, but one leak = every client's CRM.
   Our Vault design (per-token storage, second key, audit log) is strictly
   stronger.
6. **Shared-secret access** (`?key=` in the menu-link URL). Anyone with the
   link has full access, no per-user audit. Our email-code login is stronger.
7. Minor: a fallback URL `/forms/{formId}/submissions` is not a real GHL
   endpoint (dead code, harmless); PDF text placement uses approximate
   character widths (cosmetic); Resend "sandbox mode" hardcodes Jeorge's
   email as the only recipient.

### 2.3 One policy conflict to decide

The workflows check needs the **`workflows.readonly`** scope — which our
spec's scope checklist currently forbids ("never workflows"). That rule was
written to keep tokens minimal; `workflows.readonly` is view-only and low
risk. **Recommendation: allow `workflows.readonly`** on client PITs so we
can port the "leads flowing but zero published workflows" alert (it's a
genuinely good catch for onboarding gaps). Alternative: skip the workflows
check entirely. Your call — the design below marks it optional.

### 2.4 Hidden treasure

His `SUB_ACCOUNTS` list is a ready-made roster of **36 client subaccounts
with their real GHL location IDs** — most of the onboarding legwork for the
whole client book. §6 turns it into a paste-ready SQL seed.

## 3. Overlap map — what merges where

| MLH dashboard feature | In our dashboard today | Plan |
|---|---|---|
| Per-form status table | Only aggregate (`form_submissions_7d` + FORM_SILENT flag) | **Absorb — Phase 1 core** |
| Surveys | Not tracked | **Absorb — Phase 1** |
| Contact/opp/conversation freshness | Covered better (baselines vs fixed 72h) | Keep ours |
| Facebook-lead recency | We already store `leads_by_source` (PII-free) | Derive from existing data — no new fetch, no contact sniffing |
| Workflows published check | Not tracked | Absorb **if** scope approved (§2.3) |
| Tag/pixel firing (Playwright) | Not tracked | **Phase 2** — separate service, needs bigger instance + per-client config upkeep |
| Acks | Ours are durable, noted, snoozable ≤90d | Keep ours |
| Excel/PDF/email reports | CSV export + copy-ready weekly summary | Keep ours |
| On-demand scan w/ live progress | Nightly run + Render "Trigger Run" | Keep ours (simpler, rate-limit-safe) |
| Shared-key auth, env-blob tokens, Resend | Email-OTP, Vault, no third-party email | Keep ours |

## 4. Integration design (Phase 1 — per-subaccount form & survey health)

### 4.1 Database (one new table + one migration)

```sql
-- 0003_form_health.sql
create table public.form_health (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  snapshot_date date not null,
  kind text not null check (kind in ('form','survey')),
  form_id text not null,
  name text not null,
  status text not null check (status in ('active','silent','no_leads','new','unknown')),
  submissions_total int,
  last_submission_at timestamptz,
  form_created_at timestamptz,
  unique (location_id, kind, form_id, snapshot_date)
);
-- RLS: same staff-read-only pattern as snapshots (FORCE RLS, is_staff() select policy).
```

Nightly upsert per form keeps history, so "when did this form go silent"
becomes answerable — something the MLH tool cannot do.

### 4.2 Collector (extends the existing nightly run)

- `fetchers.py`: add `fetch_form_inventory()` — we already list forms; add
  the 1-request-per-form submissions count/latest (his trick, but through
  our rate limiter), and `fetch_surveys()` + per-survey submissions.
  Coverage source names: `form_inventory`, `surveys` (plugs into the
  existing gate/coverage machinery automatically).
- `metrics.py`: `classify_form(count, last_sub, created_at, today)` — port
  of his `getStatus()` with two fixes: weekend-aware silence clock (Fri–Sun
  gaps don't count against the 3 days) and threshold read from the
  account's `thresholds` JSON (default 3 business days).
- `flags.py`: new codes `FORM_WENT_SILENT` (form was receiving submissions,
  none in threshold — detail names the forms, worst first) and
  `SURVEY_WENT_SILENT`. They ride the existing flag/ack/snooze machinery.
  Ack granularity is per-code per-account (one ack covers all silent forms
  on that account) — simpler than his per-form acks; revisit only if the
  team asks.
- Optional (per §2.3): `WORKFLOWS_NONE_PUBLISHED` — fires only when the
  account had ≥1 form submission in 7d AND zero published workflows.
- Request budget: +1 request per form/survey per account per night. A
  50-form account adds ~55 requests ≈ 7 seconds at our rate limit. Fine.

### 4.3 Dashboard UI

- **Account page**: new "Forms & surveys" card — table (name, status chip,
  submissions, last submission, days quiet), silent-first sort, wired into
  the existing detail-fetch and CSV export.
- **Portfolio**: new sortable column "Silent forms" (count), included in the
  column chooser + CSV.
- `v_portfolio`: append `forms_silent_ct` (CREATE OR REPLACE VIEW — new
  column goes last, same constraint we handled in migration 0002).

### 4.4 Scopes & guide updates

- PIT checklist gains **Surveys (view)** — and **Workflows (view)** if
  approved. GO-LIVE.md Part 3 + Part 6 updated; existing tokens just get
  the scopes added in GHL (no new tokens).

### 4.5 What happens to the MLH tool

Once Phase 1 ships and the team confirms parity, Jeorge's Render service can
be retired (or kept read-only during a two-week overlap). The Playwright tag
checker is the one thing it does that we don't — that's Phase 2, ported as a
separate small Render cron writing to a `tag_checks` table, so its Chromium
RAM needs never touch the collector.

## 5. Step-by-step implementation plan

| # | Step | Where | Est. |
|---|---|---|---|
| 1 | Migration `0003_form_health.sql` (+ RLS, + `v_portfolio` column) — apply via MCP | Supabase | 20 min |
| 2 | `fetch_form_inventory` + `fetch_surveys` fetchers with coverage entries | collector | 45 min |
| 3 | `classify_form` metric + threshold plumbing | collector | 30 min |
| 4 | `FORM_WENT_SILENT` / `SURVEY_WENT_SILENT` flags (+ optional workflows flag) | collector | 30 min |
| 5 | Store upserts + tests (fixtures: healthy, silent, weekend-edge, no-scope) | collector | 45 min |
| 6 | Account-page card + portfolio column + CSV | web | 60 min |
| 7 | GO-LIVE/README scope-checklist updates | docs | 10 min |
| 8 | Add Surveys (view) [+ Workflows (view)?] to the 3 existing PITs in GHL, Trigger Run, verify in `/runs` + UI | you | 10 min |
| 9 | Seed remaining clients from §6 as PITs are created (any order, any pace) | you/team | 5 min each |

Total build: roughly half a day. Steps 1–7 are mine; 8–9 are yours.

**Decision points before I start:** (a) allow `workflows.readonly`? (b) is
account-level ack granularity for silent forms acceptable for v1? (c) Phase
2 tag checker — park it, or scope it right after?

## 6. Client roster seed (from the MLH `SUB_ACCOUNTS` list)

36 subaccounts with real location IDs, ready to insert as each client's PIT
gets created (rows appear as "no data" until then — harmless). Names below
are Jeorge's; the identity gate verifies each against GHL on first run.
Already onboarded, excluded here: AAA Pools, AAA Spa & Pool Services, SSP.

```sql
insert into public.subaccounts (location_id, name, slug, vertical, services, am_email) values
('tjKimKRzmCpOawvCUQus', 'Aqua Leisure Pools',                   'aqualeisure',   'pool_builder', '{}', null),
('8vR4kFBo1roWEibNMnYA', 'Absolute Pool & Spa Care',             'absolutepool',  'pool_service', '{}', null),
('bnhBYB6iicvfCdnvQInj', 'All American Landscape and Stone',     'allamerican',   'outdoor_living', '{}', null),
('7cs0vRlAMQTD72aJyqHj', 'Bi-State Pool & Spa',                  'bistate',       'pool_builder', '{}', null),
('1yLdh8081DEfLDR2E6VC', 'Aqua Pool & Spa Pros',                 'aquapros',      'pool_builder', '{}', null),
('NY11Fqpu6qZwNUFw289V', 'Artisan Pools and Spas',               'artisan',       'pool_builder', '{}', null),
('EsBPzqhea0wuEOy6B86T', 'Aurora Pools and Spas',                'aurora',        'pool_builder', '{}', null),
('SU0o0YxCPRas4d3yLFZO', 'Beachfront Pools & Design',            'beachfront',    'pool_builder', '{}', null),
('qCYyv1M0CElwOQqrtFA8', 'Backyard Oasis Pools & Spas',          'backyardoasis', 'pool_builder', '{}', null),
('IbeewftCXyIiUbPDwe6M', 'Campbell''s Pool and Spa Construction','campbells',     'pool_builder', '{}', null),
('D0Bs8ff4y9r3SFFIyfOd', 'Carecraft, Inc',                       'carecraft',     'other',        '{}', null),
('A6WeIeAP9Fi2CuCApyce', 'Central Jersey Pool & Spas',           'centraljersey', 'pool_builder', '{}', null),
('Ou5tPguuozSnr70wbgoE', 'Cypress Custom Pools',                 'cypress',       'pool_builder', '{}', null),
('t5df8eV63knlkSm0jRC1', 'Dolphin Pools and Spas',               'dolphin',       'pool_builder', '{}', null),
('wULbsMhqPnyaolN445by', 'Exquisite Pool & Spa',                 'exquisite',     'pool_builder', '{}', null),
('d0K3AIa7LdPh7noAeJgy', 'Fossil Creek Pools',                   'fossilcreek',   'pool_builder', '{}', null),
('Y4vvMyoOjARnCsb1nEYM', 'Flohr Pools',                          'flohr',         'pool_builder', '{}', null),
('2kllua5NLPaIhszvBWTS', 'G&S Custom Pools',                     'gscustom',      'pool_builder', '{}', null),
('KHrPHp1Pr9aKWYaU9Fm7', 'Hamlin Pools',                         'hamlin',        'pool_builder', '{}', null),
('SE3tInqKUjgOyHrh2Cnp', 'Hobert Pools & Spas',                  'hobert',        'pool_builder', '{}', null),
('wBCf5ffXVA6RpbvvTtNs', 'Kura Design Pools',                    'kura',          'pool_builder', '{}', null),
('WeNxQrw1VO4dRpzEMn7T', 'Liverpool Pool & Spa',                 'liverpool',     'pool_builder', '{}', null),
('9EQh3ifbViE7qgHsM24z', 'Magnolia Custom Pools',                'magnolia',      'pool_builder', '{}', null),
('UCq30gKSRj3012SOQVxg', 'McKinney Custom Pools',                'mckinney',      'pool_builder', '{}', null),
('kCO2VPN4H0NQaB5Qmi6a', 'M.E.H. Pool Services, Inc.',           'meh',           'pool_service', '{}', null),
('1GNGEbyMFnKwEURSGI3n', 'Patio Pleasures',                      'patiopleasures','outdoor_living', '{}', null),
('wuevr9VCRLgkwjJgPxYH', 'Olympic Pools Inc.',                   'olympic',       'pool_builder', '{}', null),
('fXNH1f1mo1FxawSUrF4v', 'Pettis Pools & Patio',                 'pettis',        'pool_builder', '{}', null),
('HfmeeBccBd108Dyl7ti1', 'Pla-mor Pools',                        'plamor',        'pool_builder', '{}', null),
('qE6xOlwFOYJESfD3lN3W', 'Pristine Pools',                       'pristine',      'pool_builder', '{}', null),
('9mgUI03IMO2APeWbzRWd', 'Russo''s Pool & Spa Inc.',             'russos',        'pool_builder', '{}', null),
('X4t6FYVqidpIXhzjpKjk', 'Softub Express',                       'softub',        'hot_tub',      '{}', null),
('HMly8fKjRtDSa9Dy3Itc', 'Texas Pools & Patios',                 'texaspools',    'pool_builder', '{}', null),
('RaGq9gLfXRLLQnkcmTvN', 'Texas Swim Academy',                   'texasswim',     'other',        '{}', null);
```

(Fill `vertical`, `services`, `am_email`, `mrr`, `contract_end` per client as
the team confirms them — all editable later with simple UPDATEs.)
