# GHL Account Health Dashboard: Build Spec v3.0 (single-shot, consolidated)

Owner: Matthew Carlson, Small Screen Producer (SSP)
Audience: Claude Code (implementer). Users: SSP account managers.
Date: August 18, 2026. Supersedes Spec v2.x, Addendum v2.1, and the token-strategy note; everything from them is folded in here. This is the only document to paste.

---

## 0. Instructions to the implementer

You are building a read-only account health dashboard for a pool and spa marketing agency that runs many GoHighLevel (GHL) subaccounts. This document is the complete spec. Build it end to end without stopping to ask design questions; every decision below is final unless the GHL API contradicts it at runtime, in which case reality wins and you log the difference in `VERIFICATION.md`.

Deliverables, in build order: (1) `supabase/migrations/0001_init.sql` runnable as-is, (2) Python collector with `--probe`, `--dry-run`, `--location`, `--backfill`, mock tests passing, (3) Vite React SPA with portfolio + drilldown + email-code auth (no OAuth, no Google) that works standalone and inside a GHL iframe, (4) `render.yaml`, `netlify.toml`, `README.md` with the console steps in section 12, (5) `VERIFICATION.md` listing every API field you confirmed or changed.

Hard rules:
- Never write to GHL. The collector has no code path that mutates anything there.
- No LLM in the metric path. Every number is arithmetic on API responses.
- Every count carries coverage (complete / partial / unavailable). Never print a bare total from a partial scan.
- Private Integration Tokens (PITs) live only in Supabase Vault, loaded by hand through the Vault UI, retrievable only by the collector via a service-role RPC that also requires `COLLECTOR_KEY`. Never in a plaintext table, never in the SPA bundle, never in git, never in logs. Section 8 is the design.
- PII minimization: the database stores names, IDs, timestamps, amounts, and deep links. It never stores a contact's phone number, email address, or message text. Not in `snapshots.details`, not in `lead_events`, not in `flags`, not in `coverage` error strings, not in `collector_runs.details`. If a fetcher returns it, drop it before it touches a row.
- Missing data reads "Unknown" or "no data," never zero, never a guess.
- No third-party identity provider. Auth is Supabase email codes only, for pre-provisioned staff accounts; public sign-up is disabled; nothing is registered at or routed through Google.
- The only user-initiated writes in the whole system are flag acknowledgements and account notes (section 9.5), insert-only, RLS-scoped to staff. Everything else the SPA does is read.
- The dashboard MUST be hosted at a subdomain of `smallscreenproducer.com` (target: `health.smallscreenproducer.com`). Same-site with the GHL white-label `crm.smallscreenproducer.com` is what makes the iframe session behave (section 9.1). Do not deploy production to `*.netlify.app` and call it done.

Reference implementation to port from (copy into `collector/reference/` before starting): `C:\Users\matth\Desktop\SSP\GHL\ghl-am-brief\ghl_am_brief.py` and its `test_mock.py`. They contain a working `Coverage` tracker, GHL client with retries, weekend wait rule, whole-word exclusion matcher, gate logic, and mock harness. Reuse; do not rewrite from zero.

Where this spec says VERIFY, the field or endpoint shape came from documentation and was not live-tested. Run `--probe` first (section 7.4), fix names, move on. Tolerant parsing everywhere: `obj.get("a") or obj.get("b")`, never `obj["a"]`.

---

## 1. What this is and who uses it

~11 staff. Account managers (AMs) each own a set of client subaccounts. Weekly question: "Which of my accounts need a call, and why?" Daily question: "Which leads and conversations are sitting uncontacted right now?"

Two questions get called "account health." Keep them visibly separate:
1. Is this account at risk (client disengaging, we are not visibly delivering, a bad call is coming)? Weekly. The money question.
2. Is the client's marketing working (leads, conversion, pipeline)? Monthly. Include enough to have the conversation, not a full analytics suite.

GHL cannot do this natively: its dashboards are per-subaccount, cannot compute intervals (speed to lead), cannot express deviation from baseline or from peer accounts, and keep no history. Confirmed by Matthew: agency-level PITs are restricted (locations, snapshots, little else); subaccount PITs are robust. Design is one read-only PIT per subaccount plus one for SSP's own account, loaded by hand into the Vault.

**Two data domains. Do not mix them:**
- **Client subaccount**: the client's leads, conversations, pipeline, published content, appointments. Signals about our delivery and about whether the client works the leads we send.
- **SSP parent subaccount** (SSP's own GHL location, `ZnckuEDPIcWu8fn72ppi`, white-label `crm.smallscreenproducer.com`): the client is a *contact* here. SSP-to-client invoices, appointments with the client, conversations with the client. Relationship signals come from HERE, keyed by `subaccounts.ssp_client_contact_id`. Past-due invoices inside a client's subaccount are the client's own receivables from their customers, a different signal; do not confuse the two.

---

## 2. Data points, prioritized

### Tier 1 (build now)

| Signal | Answers | Domain | Endpoints | Confidence |
|---|---|---|---|---|
| New leads 7d, trailing 4wk avg computed live from CRM history, delta % | Is the value story collapsing | Client | `POST /contacts/search` two date ranges | High |
| Leads by source, and per-source drop | Which channel broke | Client | same contacts, `source` field | High on `source`; VERIFY `attributionSource` |
| Form submissions 7d and trailing | Did the website generate anything; is the form dead | Client | `GET /forms/submissions` | Medium: envelope VERIFY |
| Unassigned new leads; leads with no phone | Why response is slow; are leads reachable | Client | same contacts | High |
| Speed to first human touch; uncontacted leads >24h | Is the client working the leads we send | Client | contacts + `GET /conversations/search?contactId` + `GET /conversations/{id}/messages` | Medium: human vs automated outbound needs VERIFY |
| Conversations waiting on team | Daily ops | Client | `GET /conversations/search` | High |
| Pipeline: open, stale (idle 14d), stuck (stage 30d), no next step, missing value, won/lost 7d, lost reasons 90d, lead-to-opp rate, win rate, days to close | Converting or piling up; is marketing working | Client | `GET /opportunities/search` with `getTasks=true&getCalendarEvents=true`, `GET /opportunities/pipelines` | High on counts; Medium on timestamp field names |
| Appointments booked 7d, showed / no-show 28d | Is the client's consult step working | Client | `GET /calendars/events` | Medium: `appointmentStatus` vocabulary VERIFY |
| Delivery: blogs 30d, social 7d, days since last publish, disconnected social accounts | Did we visibly do work; are posts silently failing | Client | `GET /blogs/site/all`, `GET /blogs/posts/all`, `POST /social-media-posting/{loc}/posts/list`, `GET /social-media-posting/{loc}/accounts` | Medium: shapes VERIFY |
| SSP-to-client past-due invoices | Billing/relationship risk | Parent, by client contact | `GET /invoices/` | High |
| Client last touch, next appointment | Is the client still showing up | Parent, by client contact | `GET /conversations/search?contactId`, `GET /calendars/events` | Medium |
| Peer benchmark: this account's lead delta vs same-vertical median this week | Seasonal or real | Computed across today's snapshots | n/a | High once ≥4 same-vertical accounts have tokens |
| What changed since last week (new / resolved flags) | Deltas, not levels | Computed | n/a | High |
| MRR and contract end (manual columns), renewal within 60d | Dollar-weighted triage | `subaccounts` | n/a | Manual data |
| Acknowledge / snooze flags with a note; account notes | Stop alert fatigue; keep context | SPA insert-only | n/a | High |
| Data quality: per-source coverage, gate, last run, per-location run detail | Can I trust this row | Collector | n/a | High |
| Users map (assignedTo → name) | Owner names instead of IDs | Client | `GET /users/?locationId=` | High |

### Tier 2 (after two weeks of AM use; do not scaffold beyond what is stated)
Missed inbound calls (message-level `TYPE_CALL` metadata, VERIFY `meta.call.status`). After-hours arrival vs response heatmap (pure UI on `lead_events`). Own-history z-score anomalies once ≥8 weeks exist. Client's own AR from the client subaccount, labeled distinctly. Monday digest email per AM via Resend from the collector. Copy-ready client summary (template fill, no LLM). Threshold editor UI behind an admin allowlist. Supabase Auth MFA (TOTP) for staff.

### Tier 3 (separate integrations, do not block)
Google Business Profile reviews (verified NOT in the GHL public API: the API v2 table of contents lists 32 domains and none is Reviews or Reputation; MCP "reviews" refers to Store product reviews). Google Ads / Meta / GA4 (not in the API; GHL's native per-subaccount dashboard has a Google Ads widget, link to it from the drilldown).

### Not building
Composite health score (attention sort plus MRR sort give prioritization without a weights argument). Any GHL write. Real-time anything (daily is right). Conversation sentiment (needs an LLM in the metric path). Client-facing login (different audience; if ever wanted it is a separate curated route, not a role toggle).

---

## 3. GHL API reference

Base: `https://services.leadconnectorhq.com`
Headers: `Authorization: Bearer <PIT>`, `Version: 2021-07-28`, `Accept: application/json`, `Content-Type: application/json` on POST.
Rate limit (assumption; public docs are thin): ~100 requests per 10 s per location. Token bucket at 8 req/s per token; on 429 sleep per `Retry-After` or backoff 2^n capped 30 s; on repeated 429 halve the rate for the rest of that location. Count 429s into `collector_runs.rate_limited`.

Response envelopes (VERIFY each with `--probe`; parse tolerantly):

| Purpose | Call | Params | Envelope → fields | Scope |
|---|---|---|---|---|
| Location, name, timezone | `GET /locations/{id}` | | `location{id,name,timezone}` or top-level | `locations.readonly` |
| Users map | `GET /users/` | `locationId` | `users[{id,name,firstName,lastName,email}]` (store id → display name only) | `users.readonly` |
| Pipelines, stages | `GET /opportunities/pipelines` | `locationId` | `pipelines[{id,name,stages[{id,name}]}]` | `opportunities.readonly` |
| Opportunities | `GET /opportunities/search` | `location_id, limit=100, page, status=open|won|lost, getTasks=true, getCalendarEvents=true, getNotes=false` (VERIFY whether `date`/`endDate` filter on `lastStatusChangeAt`) | `opportunities[{id,name,monetaryValue,pipelineId,pipelineStageId,assignedTo,status(open|won|lost|abandoned),source,createdAt,updatedAt,lastStatusChangeAt,lastStageChangeAt,lastActionDate,lostReasonId,contact{id,name,tags},tasks[{id,title,dueDate,completed}],calendarEvents[{id,startTime,title}]}], meta{total,nextPage}` | `opportunities.readonly` |
| Contacts by date range | `POST /contacts/search` | body `{"locationId","page","pageLimit":100,"filters":[{"field":"dateAdded","operator":"range","value":{"gte":ISO,"lte":ISO}}],"sort":[{"field":"dateAdded","direction":"desc"}]}` | `contacts[{id,firstName,lastName,companyName,phone,email,source,attributionSource{sessionSource,medium,utmSource}?,tags[],dateAdded,dateUpdated,assignedTo}], total` (VERIFY `attributionSource`; `phone`/`email` are used only for presence checks and never stored) | `contacts.readonly` |
| Contacts by tag | `POST /contacts/search` | filter `{"field":"tags","operator":"eq","value":"review-request"}` | same | `contacts.readonly` |
| Form submissions | `GET /forms/submissions` | `locationId, startAt=YYYY-MM-DD, endAt=YYYY-MM-DD, limit=100, page` | `submissions[{id,formId,contactId,createdAt}], meta{total}` (VERIFY) | `forms.readonly` |
| Conversations recent | `GET /conversations/search` | `locationId, limit=100, sortBy=last_message_date, sort=desc, startAfterDate=<ms or ISO of last item>` | `conversations[{id,contactId,contactName|fullName,lastMessageDate,lastMessageDirection(inbound|outbound),lastMessageType,unreadCount,type,assignedTo}], total` (never store `lastMessageBody`) | `conversations.readonly` |
| Conversations for contact | `GET /conversations/search` | `locationId, contactId` | same | `conversations.readonly` |
| Messages | `GET /conversations/{id}/messages` | `limit=100` | `messages{lastMessageId,nextPage,messages[{id,direction,dateAdded,type,source,userId,status}]}` NOTE nested `messages.messages`; handle flat too; never store `body` | `conversations/message.readonly` |
| Calendars | `GET /calendars/` | `locationId` | `calendars[{id,name}]` | `calendars.readonly` |
| Events | `GET /calendars/events` | `locationId, calendarId, startTime(ms), endTime(ms)` | `events[{id,title,startTime,endTime,contactId,appointmentStatus,calendarId,dateAdded|createdAt}]` (VERIFY status vocabulary: expected `new, confirmed, showed, noshow, cancelled, invalid`; VERIFY the created field) | `calendars/events.readonly` |
| Invoices | `GET /invoices/` | `altId=<loc>, altType=location, limit=100, offset` | `invoices[{_id|id,invoiceNumber,status,dueDate,total,amountDue,contactDetails{id,name}}], total` | `invoices.readonly` |
| Blog sites | `GET /blogs/site/all` | `locationId, limit=50, skip=0` | `data[{_id|id,name}]` | `blogs/list.readonly` |
| Blog posts | `GET /blogs/posts/all` | `locationId, blogId, limit=50, offset=0, status=PUBLISHED` | `blogs|posts[{_id,title,status,publishedAt,updatedAt}]` | `blogs/post.readonly` |
| Social posts | `POST /social-media-posting/{loc}/posts/list` | body `{"type":"all","skip":0,"limit":50,"fromDate":ISO,"toDate":ISO,"includeUsers":false}` | `results{posts[{_id,status,publishedAt|createdAt,accountIds}]}` or `posts[]` | `socialplanner/post.readonly` |
| Social accounts | `GET /social-media-posting/{loc}/accounts` | | `results{accounts[{id,platform,name,isExpired|expired|status}]}` (VERIFY the disconnected/expired field) | `socialplanner/account.readonly` |
| Contact tasks (fallback) | `GET /contacts/{id}/tasks` | | `tasks[{id,title,dueDate,completed,assignedTo}]` | `contacts.readonly` |

Deep links (white-label host, config `GHL_APP_BASE=https://crm.smallscreenproducer.com`):
- Contact: `{base}/v2/location/{loc}/contacts/detail/{contactId}` (high confidence)
- Conversation: `{base}/v2/location/{loc}/conversations/conversations/{convId}` (VERIFY; fall back to contact link)
- Opportunity: `{base}/v2/location/{loc}/opportunities/list?opportunityId={id}` (VERIFY; fall back to contact link)
- Subaccount home / native dashboard: `{base}/v2/location/{loc}/dashboard`

---

## 4. Metric definitions

Timezone: from `/locations/{id}.timezone`, fallback `America/Chicago`. "Now" = run start. Window `7d` = 7 full local days ending yesterday 23:59:59. Baseline window = the 28 days before that (`[now-35d, now-7d)`), bucketed into four 7-day weeks.

Exclusions: contacts whose first or last name contains a whole word in `["test","testing","demo","sample"]`, or tagged `internal-staff`. Count and report `excluded_count`. Whole word: "Testani" stays in.

### 4.1 Leads and baseline (computed live from CRM history; no waiting period)
- `leads_new_7d`: contacts with `dateAdded` in the 7d window after exclusions.
- `leads_trailing_avg`: mean of the four weekly counts in the baseline window (second `contacts/search` range query). `trailing_n` = number of baseline weeks that fall after the location's earliest contact `dateAdded` (4 for any account older than 5 weeks). `leads_trailing_avg` is null if `trailing_n < 2`. UI shows "baseline building ({trailing_n}/4 weeks)" while `trailing_n < 4`.
- `leads_delta_pct`: `(leads_new_7d - trailing_avg)/trailing_avg*100`; null if `trailing_avg` null or < 3.
- `leads_by_source_7d` (jsonb `{source: count}`), `leads_by_source_trailing` (jsonb `{source: weekly avg}`). Source = `contact.source`; if empty, `attributionSource.sessionSource` or `utmSource` when present (VERIFY); else `"unknown"`.
- `leads_unassigned_7d`: new contacts with no `assignedTo`.
- `leads_missing_phone_pct_7d`: percent of new contacts with no `phone` (presence check only; the number is never stored); null if `leads_new_7d < 5`.
- `form_submissions_7d`, `form_submissions_trailing_avg`: from `forms/submissions` for the same two windows. Null with coverage note if the endpoint is unavailable.
- `lead_history` (table): one row per location per ISO week with `leads`, `leads_by_source`, `form_submissions`. The collector upserts the current and previous week every run; `--backfill N` writes N weeks from `dateAdded` on first run so charts are populated on day one.
- `peer_median_delta_pct`, `peer_n`: after all locations are collected, group today's client snapshots by `subaccounts.vertical`; median of non-null `leads_delta_pct`; require `peer_n >= 4`, else fall back to the whole active book; null if still < 4. Written onto each snapshot row.
- Activity counters for the gate: `convos_active_7d` = conversations with any message (either direction) in the 7d window; `opps_created_7d` = opportunities with `createdAt` in the window.

### 4.2 Speed to lead
For contacts created in the last 14 days (cap 100 per location, newest first, cap recorded in coverage): fetch that contact's conversations, then messages. `first_outbound_at` = earliest outbound message. `first_outbound_kind` = `human` if `userId` present or `source` in `{app, manual, user}`; `automation` if `source` in `{workflow, campaign, bulk_actions, api}`; else `unknown` (VERIFY the `source` vocabulary; if it cannot be established, kind = `unknown` for all and the UI labels the metric "first response, automation included"). `first_human_touch_at` = earliest outbound with kind human. Minutes from `dateAdded`. Upsert into `lead_events` (name, IDs, timestamps only).
- `leads_uncontacted_24h`: created ≥24h ago within 7d, no outbound at all.
- `leads_no_human_touch_7d`: created ≥24h ago within 7d, outbound exists but none human (only when kinds are known).
- `speed_to_lead_median_min`, `speed_to_lead_p90_min`: over 7d contacts that got a human touch (or any touch if kinds unknown). `speed_kind_known` boolean.

### 4.3 Conversations
`convos_waiting`, `convos_waiting_max_hours`: conversations with `lastMessageDirection == inbound` and wait ≥ 4h. Wait = wall-clock hours since `lastMessageDate`, with the weekend rule: inbound landing Friday ≥17:00 local, or Saturday/Sunday, starts the clock Monday 09:00 local. Scan window: last 14 days of conversations.

### 4.4 Pipeline
Pull `status=open` (all pages) plus `status=won` and `status=lost` for the last 90 days (use date params if `--probe` confirms they filter on `lastStatusChangeAt`; else page newest-first and stop once `lastStatusChangeAt` passes the cutoff).
- Open: `opps_open`, `opps_open_value`; `days_idle` = days since the newest of (`lastActionDate`, `lastStatusChangeAt`, `updatedAt`) that exists, record `idle_source_field`; `opps_stale` = idle ≥ 14; `opps_stuck` = days since `lastStageChangeAt` ≥ 30 (Unknown if absent); `opps_missing_value` = null/0; `opps_no_next_step` = no incomplete task and no future calendar event on the opp when `getTasks/getCalendarEvents` populate (else null and coverage note "next-step check unavailable").
- Closed: `opps_won_7d`, `opps_lost_7d` by `lastStatusChangeAt`; `lost_reasons_90d` count by `lostReasonId`; `win_rate_90d = won/(won+lost)` over 90d, null if `won+lost < 5`; `median_days_to_close_90d` over won opps as `lastStatusChangeAt - createdAt`.
- `lead_to_opp_28d_pct`: of contacts created 14 to 42 days ago, the percent with any opportunity whose `contact.id` matches and `createdAt ≥ contact.dateAdded`; null if the cohort < 5.

### 4.5 Appointments (client subaccount calendars, events from -28d to +7d)
`appts_booked_7d` = events whose created field (VERIFY `dateAdded` vs `createdAt`) falls in the 7d window; `appts_showed_28d`, `appts_noshow_28d` from `appointmentStatus` on events with `startTime` in the last 28 days; `noshow_rate_28d = noshow/(showed+noshow)`, null if denominator < 5. `appts_next_7d` list goes into details.

### 4.6 Delivery
If `services` ∩ `{content, social}` is empty, all delivery metrics are null and no delivery flag fires. Otherwise: `blogs_published_30d`, `social_published_7d`; `days_since_last_publish` = fetch the newest blog post regardless of window (first page, newest first) and social posts with `fromDate = now-90d`, take the most recent publish across both; if none found in 90 days write `90` and add coverage note "no publish found in 90d". `social_accounts_total`, `social_accounts_expired` from the social accounts endpoint (only when `social` in services).

### 4.7 Relationship (from the SSP parent, only if `ssp_client_contact_id` set)
`invoices_past_due`, `invoices_past_due_amount` (status not in {paid, void, draft, deleted}; `dueDate < today`; use `amountDue` else `total`); `client_last_touch_days` = days since the latest of (last message in any conversation with that contact, last calendar event with that contact); `client_next_appt_at` = earliest future event with that contact. Parent data is fetched once per run and indexed by contact ID.

### 4.8 Review proxies
`review_asks_stale` (tag `review-request`, `dateUpdated` ≥ 7d), `review_ask_gap` (opps won in 30d whose contact lacks the tag). Direct review data is not in the API; say so in the UI.

### 4.9 Change tracking
`flags_new` = flag codes present today and absent in this location's flags from 7 days ago; `flags_resolved` = the reverse. Read last week's flags before writing today's.

### 4.10 Coverage and gate
Coverage per source: `{retrieved, exhausted, error, note}` (error strings are sanitized: no headers, no record contents). Gate passes iff G1 `/locations` returned `id == location_id` and `name` contains `subaccounts.name` (case-insensitive); G2 fewer than 2 sources unavailable; G3 fewer than 2 partial scans; G4 not (`leads_new_7d == 0` and `convos_active_7d == 0` and `opps_created_7d == 0`) unless the previous 3 gate-passed snapshots show the same (a dormant account is allowed to be dormant; a sudden all-zero is more likely a failed read). Held snapshots are stored with `gate_passed=false`; the UI shows "no data," never zeros.

---

## 5. Flag catalog

Severity weights for the attention sort: red 3, amber 1, info 0; a flag with an active acknowledgement (section 9.5) weighs 0 and is not counted as red/amber. Thresholds default below, overridable per row in `subaccounts.thresholds` (keys named after the numbers here, e.g. `{"opp_idle_days":21,"stale_value_usd":50000,"lead_drop_pct":-50}`). Starting guesses; tune after 4 weeks.

| Code | Fires when | Sev | Action text |
|---|---|---|---|
| `INTEGRATION_SUSPECT` | leads_new_7d 0, convos_active_7d 0, opps_created_7d 0, and (form_submissions_7d 0 or forms unavailable), and trailing_avg ≥ 3 | red | Nothing flowing at all. Likely a broken form, phone, or webhook, not a quiet week. |
| `FORM_SILENT` | form_submissions_7d == 0 and form_submissions_trailing_avg ≥ 3 and leads_new_7d > 0 | red | Forms silent while other channels are alive. Form or webhook is likely broken; check before the client notices. |
| `LEADS_ZERO` | leads_new_7d == 0 and trailing_avg ≥ 3, and INTEGRATION_SUSPECT did not fire | red | Zero leads vs {avg} average. Check integration before calling. |
| `LEADS_DROP` | delta ≤ -40 and (peer null or peer > -20) | red if peer > -20; amber if peer null | Leads down {pct}% vs baseline while peers held. Call before they call you. |
| `LEADS_DROP_SEASONAL` | delta ≤ -40 and peer ≤ -20 | amber | Leads down {pct}%, peers down {peer}%. Likely seasonal; mention proactively. |
| `SOURCE_DROP` | for any source with trailing weekly avg ≥ 3 and share ≥ 25%: current ≤ 0.4 × avg | amber; red if current == 0 | Leads from {source} went from {avg}/wk to {n}. Check that channel specifically (form, ad account, phone routing). |
| `UNASSIGNED_LEADS` | leads_unassigned_7d ≥ 3 or ≥ 30% of leads_new_7d | amber | {n} new leads have no owner. Nobody will call them. Fix assignment rules. |
| `LEADS_UNREACHABLE` | leads_missing_phone_pct_7d ≥ 30 (n ≥ 5) | amber | {pct}% of new leads have no phone number. Check form fields. |
| `SLOW_RESPONSE` | uncontacted_24h ≥ 3, or no_human_touch/leads ≥ 0.3 with leads ≥ 5 | amber; red if ratio ≥ 0.5 or uncontacted ≥ 8 | {n} leads uncontacted >24h. Send the response-time report; it reframes "bad leads." |
| `CONVOS_WAITING` | any waiting ≥ 4h | amber; red if max ≥ 24h | {n} inbound waiting, longest {h}h. Oldest: {contact}. |
| `STALE_PIPELINE` | stale ≥ max(3, 0.3×open) or stale_value ≥ 25000 | amber; red on value | {n} deals idle 14d+ worth ${v}. Re-engage; Q3 spring-origin inquiries are re-engage, not close-lost. |
| `HIGH_NOSHOW` | noshow_rate_28d ≥ 30 (denominator ≥ 5) | amber | {pct}% no-show over 28 days. Confirmation and reminder flow needs attention. |
| `NO_DELIVERY` | services has content/social and days_since_last_publish ≥ 14 | amber; red ≥ 30 | Nothing published in {d} days. Our gap; fix before the client notices. |
| `SOCIAL_DISCONNECTED` | services has social and social_accounts_expired ≥ 1 | red | {n} social account(s) disconnected; scheduled posts are silently failing. Reconnect in Social Planner. |
| `PAST_DUE` | invoices_past_due ≥ 1 | amber; red if any > 30d or amount ≥ 2500 | ${amt} past due, oldest {d}d. Coordinate with billing before the next call. |
| `NO_CLIENT_TOUCH` | last_touch ≥ 30 and no next appt | amber; red ≥ 45 | No client contact in {d} days, nothing scheduled. Book a check-in. |
| `RENEWAL_SOON` | contract_end within 60 days | info; amber if the account also has any red | Renewal in {d} days. Book the review now. |
| `REVIEW_ASK_GAP` | review_ask_gap ≥ 2 | info | {n} recent wins never got a review ask. |

States: `no_data` (token missing/invalid, gate failed, or snapshot older than 36h), `attention` (any unacked red or ≥2 unacked amber), else `steady`. Sort attention by `attention_score` desc then `leads_delta_pct` asc; alternative sort "by MRR at risk" (accounts with `attention_score > 0` first, then `mrr` desc).

---

## 6. Database (Supabase Postgres). Complete migration, security model included.

Security model in one paragraph: `anon` can reach nothing in `public` except the schema itself (no table, view, or function grants). `authenticated` can `select` every table and the two views, and can `insert` (never update or delete) into `flag_acks` and `account_notes`, and every one of those grants is additionally gated by RLS on `is_staff()` (JWT email ends in `@smallscreenproducer.com`). Public sign-up is disabled at the Supabase Auth level and users are pre-provisioned by Matthew; the `auth.users` triggers are belt-and-braces. Supabase's default "grant all to anon/authenticated" and Postgres's default EXECUTE-to-PUBLIC on functions are explicitly revoked, and default privileges are changed so future tables and functions start locked. Views run with `security_invoker` so RLS applies through them. Tokens live in the `vault` schema, which is not API-exposed; the only doors are `security definer` RPCs executable solely by `service_role` and requiring `COLLECTOR_KEY`. The database stores no phone numbers, emails (other than staff `am_email`), or message text.

```sql
-- supabase/migrations/0001_init.sql
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------
create table public.subaccounts (
  location_id text primary key,
  name text not null,
  slug text unique not null,                  -- short handle for URLs; vault secret name is ghl_pit_<location_id>
  vertical text,                              -- pool_builder | pool_service | hot_tub | spa_retail | other
  services text[] not null default '{}',      -- content, social, ads, seo, web, crm
  am_email text,                              -- staff email; the only email address stored anywhere
  timezone text not null default 'America/Chicago',
  ssp_client_contact_id text,                 -- this client's contact ID inside the SSP parent account
  is_parent boolean not null default false,
  active boolean not null default true,
  thresholds jsonb not null default '{}'::jsonb,
  mrr numeric,                                -- manual; monthly recurring revenue from this client
  contract_end date,                          -- manual
  token_status text not null default 'none' check (token_status in ('none','ok','invalid')),
  token_rotated_at date,
  last_token_error text,                      -- sanitized; never contains a token or header
  created_at timestamptz not null default now()
);

create table public.snapshots (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  snapshot_date date not null,
  captured_at timestamptz not null default now(),
  gate_passed boolean not null,
  coverage jsonb not null default '{}'::jsonb,
  -- leads and baseline
  leads_new_7d int, leads_trailing_avg numeric, trailing_n int, leads_delta_pct numeric,
  peer_median_delta_pct numeric, peer_n int,
  leads_by_source_7d jsonb, leads_by_source_trailing jsonb,
  leads_unassigned_7d int, leads_missing_phone_pct_7d numeric,
  form_submissions_7d int, form_submissions_trailing_avg numeric,
  convos_active_7d int, opps_created_7d int,
  -- speed to lead
  leads_uncontacted_24h int, leads_no_human_touch_7d int,
  speed_to_lead_median_min numeric, speed_to_lead_p90_min numeric, speed_kind_known boolean,
  excluded_count int,
  -- conversations
  convos_waiting int, convos_waiting_max_hours numeric,
  -- pipeline
  opps_open int, opps_open_value numeric, opps_stale int, opps_stale_value numeric,
  opps_stuck int, opps_missing_value int, opps_no_next_step int,
  opps_won_7d int, opps_lost_7d int,
  lead_to_opp_28d_pct numeric, win_rate_90d numeric, median_days_to_close_90d numeric,
  -- appointments
  appts_booked_7d int, appts_showed_28d int, appts_noshow_28d int, noshow_rate_28d numeric,
  -- delivery
  blogs_published_30d int, social_published_7d int, days_since_last_publish int,
  social_accounts_total int, social_accounts_expired int,
  -- relationship (from parent)
  invoices_past_due int, invoices_past_due_amount numeric,
  client_last_touch_days int, client_next_appt_at timestamptz,
  -- reviews proxies
  review_asks_stale int, review_ask_gap int,
  -- change tracking
  flags_new jsonb not null default '[]'::jsonb, flags_resolved jsonb not null default '[]'::jsonb,
  -- drilldown lists (section 7.5): names, IDs, timestamps, amounts, deep links only
  details jsonb not null default '{}'::jsonb,
  unique (location_id, snapshot_date)
);
create index snapshots_loc_date on public.snapshots (location_id, snapshot_date desc);

create table public.flags (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  snapshot_date date not null,
  code text not null,
  severity text not null check (severity in ('red','amber','info')),
  title text not null,
  detail text,
  action text,
  entity_type text, entity_id text, entity_name text, deep_link text,
  created_at timestamptz not null default now()
);
create index flags_loc_date on public.flags (location_id, snapshot_date);

create table public.lead_events (
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  contact_id text not null,
  contact_name text, source text,             -- name only; never phone or email
  created_at timestamptz not null,
  first_outbound_at timestamptz, first_outbound_kind text,   -- human | automation | unknown
  first_human_touch_at timestamptz,
  first_touch_minutes numeric, first_human_touch_minutes numeric,
  updated_at timestamptz not null default now(),
  primary key (location_id, contact_id)
);
create index lead_events_loc_created on public.lead_events (location_id, created_at desc);

create table public.lead_history (
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  week_start date not null,                   -- Monday
  leads int not null,
  leads_by_source jsonb not null default '{}'::jsonb,
  form_submissions int,
  primary key (location_id, week_start)
);

create table public.flag_acks (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  code text not null,
  acked_by text not null,                     -- staff email, enforced equal to the JWT email by RLS
  note text,
  acked_at timestamptz not null default now(),
  snooze_until date not null default current_date + 7,
  constraint flag_acks_snooze_max check (snooze_until <= current_date + 90)   -- upper bound only; a lower bound on current_date would fail on dump/restore of expired rows
);
create index flag_acks_lookup on public.flag_acks (location_id, code, snooze_until desc);

create table public.account_notes (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  author text not null,                       -- staff email, enforced equal to the JWT email by RLS
  body text not null check (length(body) <= 4000),
  created_at timestamptz not null default now()
);
create index account_notes_loc on public.account_notes (location_id, created_at desc);

create table public.collector_runs (
  id bigserial primary key,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text,                                 -- ok | partial | failed
  locations_ok int default 0, locations_held int default 0, locations_failed int default 0,
  requests_made int, rate_limited int,
  details jsonb not null default '{}'::jsonb,  -- {location_id: {status, gate: [...], requests, rate_limited, seconds, error}} sanitized
  error text
);

-- ---------------------------------------------------------------------------
-- Views (security_invoker so RLS applies to the caller)
-- ---------------------------------------------------------------------------
create or replace view public.v_portfolio with (security_invoker = on) as
with latest as (
  select distinct on (location_id) *
  from public.snapshots
  order by location_id, snapshot_date desc, captured_at desc
),
fl as (
  select f.location_id, f.snapshot_date,
         count(*) filter (where f.severity = 'red'   and a.id is null) as red,
         count(*) filter (where f.severity = 'amber' and a.id is null) as amber,
         count(*) filter (where f.severity = 'info')                    as info,
         count(*) filter (where a.id is not null)                        as acked,
         coalesce(sum(case when a.id is not null then 0
                           when f.severity = 'red' then 3
                           when f.severity = 'amber' then 1 else 0 end), 0) as attention_score,
         (array_agg(f.action order by (a.id is not null),
                    case f.severity when 'red' then 0 when 'amber' then 1 else 2 end, f.id))[1] as top_action
  from public.flags f
  left join lateral (
    select x.id from public.flag_acks x
    where x.location_id = f.location_id and x.code = f.code and x.snooze_until >= current_date
    order by x.acked_at desc limit 1
  ) a on true
  group by f.location_id, f.snapshot_date
)
select
  s.location_id, s.name, s.slug, s.vertical, s.services, s.am_email, s.timezone, s.is_parent,
  s.mrr, s.contract_end, s.token_status, s.token_rotated_at,
  l.snapshot_date, l.captured_at, l.gate_passed, l.coverage,
  l.leads_new_7d, l.leads_trailing_avg, l.trailing_n, l.leads_delta_pct, l.peer_median_delta_pct, l.peer_n,
  l.leads_unassigned_7d, l.leads_missing_phone_pct_7d, l.form_submissions_7d,
  l.leads_uncontacted_24h, l.leads_no_human_touch_7d, l.speed_to_lead_median_min, l.speed_kind_known,
  l.convos_waiting, l.convos_waiting_max_hours,
  l.opps_open, l.opps_open_value, l.opps_stale, l.opps_stale_value, l.opps_missing_value,
  l.lead_to_opp_28d_pct, l.win_rate_90d, l.noshow_rate_28d,
  l.days_since_last_publish, l.social_accounts_expired,
  l.invoices_past_due, l.invoices_past_due_amount,
  l.client_last_touch_days, l.client_next_appt_at,
  l.flags_new, l.flags_resolved,
  coalesce(fl.red,0) as red, coalesce(fl.amber,0) as amber, coalesce(fl.info,0) as info,
  coalesce(fl.acked,0) as acked,
  coalesce(fl.attention_score,0) as attention_score, fl.top_action,
  case
    when s.token_status <> 'ok' then 'no_data'
    when l.snapshot_date is null or not l.gate_passed or l.captured_at < now() - interval '36 hours' then 'no_data'
    when coalesce(fl.red,0) > 0 or coalesce(fl.amber,0) >= 2 then 'attention'
    else 'steady'
  end as state
from public.subaccounts s
left join latest l on l.location_id = s.location_id
left join fl on fl.location_id = l.location_id and fl.snapshot_date = l.snapshot_date
where s.active;

create or replace view public.v_history with (security_invoker = on) as
select location_id, snapshot_date, gate_passed, leads_new_7d, leads_trailing_avg, leads_delta_pct,
       peer_median_delta_pct, convos_waiting, opps_stale, opps_open_value, days_since_last_publish,
       leads_uncontacted_24h, speed_to_lead_median_min
from public.snapshots
where snapshot_date >= current_date - 84;

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------
alter table public.subaccounts    enable row level security;
alter table public.snapshots      enable row level security;
alter table public.flags          enable row level security;
alter table public.lead_events    enable row level security;
alter table public.lead_history   enable row level security;
alter table public.flag_acks      enable row level security;
alter table public.account_notes  enable row level security;
alter table public.collector_runs enable row level security;
-- FORCE guards any future non-BYPASSRLS owner. On Supabase the postgres role has BYPASSRLS, which is why seed.sql
-- and the audit inserts inside the security-definer vault functions work without insert policies. service_role bypasses by design.
alter table public.subaccounts    force row level security;
alter table public.snapshots      force row level security;
alter table public.flags          force row level security;
alter table public.lead_events    force row level security;
alter table public.lead_history   force row level security;
alter table public.flag_acks      force row level security;
alter table public.account_notes  force row level security;
alter table public.collector_runs force row level security;

create or replace function public.is_staff() returns boolean
language sql stable security invoker set search_path = public as $$
  select coalesce((auth.jwt() ->> 'email') ilike '%@smallscreenproducer.com', false)
     and coalesce((auth.jwt() ->> 'role') = 'authenticated', false)
$$;

create policy staff_read_subaccounts on public.subaccounts    for select to authenticated using (public.is_staff());
create policy staff_read_snapshots   on public.snapshots      for select to authenticated using (public.is_staff());
create policy staff_read_flags       on public.flags          for select to authenticated using (public.is_staff());
create policy staff_read_lead_events on public.lead_events    for select to authenticated using (public.is_staff());
create policy staff_read_lead_hist   on public.lead_history   for select to authenticated using (public.is_staff());
create policy staff_read_acks        on public.flag_acks      for select to authenticated using (public.is_staff());
create policy staff_read_notes       on public.account_notes  for select to authenticated using (public.is_staff());
create policy staff_read_runs        on public.collector_runs for select to authenticated using (public.is_staff());
create policy staff_insert_acks  on public.flag_acks     for insert to authenticated
  with check (public.is_staff() and acked_by = (auth.jwt() ->> 'email'));
create policy staff_insert_notes on public.account_notes for insert to authenticated
  with check (public.is_staff() and author = (auth.jwt() ->> 'email'));
-- No update/delete policies anywhere for authenticated: acks and notes are append-only history.
-- No policies at all for anon: anon sees nothing.

-- ---------------------------------------------------------------------------
-- Auth hardening: staff domain enforced at the source (belt-and-braces; sign-up is also disabled in Auth settings).
-- This migration is run once; it is not idempotent (create table without if not exists). Re-running requires a fresh project or manual drops.
-- ---------------------------------------------------------------------------
create or replace function public.enforce_staff_domain() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.email is null or new.email not ilike '%@smallscreenproducer.com' then
    raise exception 'Sign-in restricted to smallscreenproducer.com accounts';
  end if;
  return new;
end $$;
create trigger enforce_staff_domain_ins before insert on auth.users
  for each row execute function public.enforce_staff_domain();
create trigger enforce_staff_domain_upd before update of email on auth.users
  for each row execute function public.enforce_staff_domain();

-- ---------------------------------------------------------------------------
-- Vault-backed PIT storage (section 8). Vault is enabled by default on Supabase projects;
-- if not: Dashboard → Project Settings → Vault → enable, then re-run this block.
-- ---------------------------------------------------------------------------
create table public.pit_audit (
  id bigserial primary key,
  location_id text,
  action text not null,           -- set | rotate | delete | read | read_denied
  actor text,                     -- 'collector' | 'cli' | null
  at timestamptz not null default now()
);
alter table public.pit_audit enable row level security;   -- no policies: invisible to API roles
alter table public.pit_audit force row level security;

-- collector_key bootstrap is done in the Vault UI (section 12): Name 'collector_key', Secret = generated string.

-- Key check. Returns boolean instead of raising so that a denied attempt can be audited.
create or replace function public.pit_key_ok(p_key text) returns boolean
language plpgsql security definer set search_path = public, vault as $$
declare v text;
begin
  select decrypted_secret into v from vault.decrypted_secrets where name = 'collector_key';
  if v is null or p_key is distinct from v then
    insert into public.pit_audit(action, actor) values ('read_denied', 'unknown');
    return false;
  end if;
  return true;
end $$;

-- Returns the token, or null when the key is wrong OR no token is stored.
create or replace function public.get_pit(p_location_id text, p_collector_key text) returns text
language plpgsql security definer set search_path = public, vault as $$
declare v_token text;
begin
  if not public.pit_key_ok(p_collector_key) then return null; end if;
  select decrypted_secret into v_token from vault.decrypted_secrets where name = 'ghl_pit_' || p_location_id;
  insert into public.pit_audit(location_id, action, actor) values (p_location_id, 'read', 'collector');
  return v_token;
end $$;

-- When was this token last set or rotated (works no matter how it was entered: Vault UI, SQL, or CLI)
create or replace function public.pit_updated_at(p_location_id text, p_collector_key text) returns timestamptz
language plpgsql security definer set search_path = public, vault as $$
declare v timestamptz;
begin
  if not public.pit_key_ok(p_collector_key) then return null; end if;
  select coalesce(updated_at, created_at) into v from vault.secrets where name = 'ghl_pit_' || p_location_id;
  return v;
end $$;

-- Optional programmatic set/rotate/delete for the CLI; the Vault UI is the primary path.
create or replace function public.set_pit(p_location_id text, p_token text, p_collector_key text) returns boolean
language plpgsql security definer set search_path = public, vault as $$
declare v_id uuid;
begin
  if not public.pit_key_ok(p_collector_key) then return false; end if;
  if p_token is null or length(p_token) < 20 then raise exception 'token looks wrong'; end if;
  select id into v_id from vault.secrets where name = 'ghl_pit_' || p_location_id;
  if v_id is null then
    perform vault.create_secret(p_token, 'ghl_pit_' || p_location_id, 'GHL read-only PIT');
    insert into public.pit_audit(location_id, action, actor) values (p_location_id, 'set', 'cli');
  else
    perform vault.update_secret(v_id, p_token);
    insert into public.pit_audit(location_id, action, actor) values (p_location_id, 'rotate', 'cli');
  end if;
  update public.subaccounts set token_rotated_at = current_date, token_status = 'ok' where location_id = p_location_id;
  return true;
end $$;

create or replace function public.delete_pit(p_location_id text, p_collector_key text) returns boolean
language plpgsql security definer set search_path = public, vault as $$
begin
  if not public.pit_key_ok(p_collector_key) then return false; end if;
  delete from vault.secrets where name = 'ghl_pit_' || p_location_id;
  insert into public.pit_audit(location_id, action, actor) values (p_location_id, 'delete', 'cli');
  update public.subaccounts set token_status = 'none' where location_id = p_location_id;
  return true;
end $$;

-- ---------------------------------------------------------------------------
-- Least privilege: strip Supabase's default broad grants from the API roles, then grant back exactly what
-- the SPA needs. Runs last so it covers every object above. service_role is untouched (it bypasses RLS by design
-- and is used only by the collector).
-- ---------------------------------------------------------------------------
revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from anon, authenticated;
revoke execute on all functions in schema public from public;          -- Postgres grants EXECUTE to PUBLIC by default; remove it
alter default privileges in schema public revoke all on tables    from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke all on functions from anon, authenticated;
alter default privileges in schema public revoke execute on functions from public;  -- future functions start private

grant usage on schema public to anon, authenticated;   -- schema visibility only; anon holds no object privileges
grant select on public.subaccounts, public.snapshots, public.flags, public.lead_events, public.lead_history,
                public.flag_acks, public.account_notes, public.collector_runs,
                public.v_portfolio, public.v_history to authenticated;
grant insert on public.flag_acks, public.account_notes to authenticated;
grant usage, select on sequence public.flag_acks_id_seq, public.account_notes_id_seq to authenticated;
grant execute on function public.is_staff() to authenticated;

-- Vault RPCs: service_role only. (Redundant with the revoke-all above; explicit for readers.)
revoke all on function public.pit_key_ok(text) from public, anon, authenticated;
revoke all on function public.get_pit(text, text) from public, anon, authenticated;
revoke all on function public.pit_updated_at(text, text) from public, anon, authenticated;
revoke all on function public.set_pit(text, text, text) from public, anon, authenticated;
revoke all on function public.delete_pit(text, text) from public, anon, authenticated;
grant execute on function public.pit_key_ok(text) to service_role;
grant execute on function public.get_pit(text, text) to service_role;
grant execute on function public.pit_updated_at(text, text) to service_role;
grant execute on function public.set_pit(text, text, text) to service_role;
grant execute on function public.delete_pit(text, text) to service_role;

-- vault schema must never be reachable by API roles (already the Supabase default; explicit and tolerant if Vault is off)
do $$ begin
  execute 'revoke all on schema vault from anon, authenticated';
exception when others then null; end $$;

-- ---------------------------------------------------------------------------
-- Optional retention job. Run ONLY after enabling pg_cron (Dashboard → Integrations → Cron).
-- Kept commented so this migration runs cleanly without it.
-- ---------------------------------------------------------------------------
-- select cron.schedule('ghl-health-retention', '0 9 * * 0', $cron$
--   delete from public.snapshots      where snapshot_date < current_date - 400;
--   delete from public.flags          where snapshot_date < current_date - 400;
--   delete from public.lead_events    where created_at < now() - interval '180 days';
--   delete from public.collector_runs where started_at < now() - interval '180 days';
-- $cron$);
```

Seed (`supabase/seed.sql`), edit values:
```sql
insert into public.subaccounts (location_id, name, slug, is_parent, timezone, am_email) values
('ZnckuEDPIcWu8fn72ppi', 'Small Screen Producer', 'ssp', true, 'America/Chicago', 'matthew@smallscreenproducer.com');
insert into public.subaccounts (location_id, name, slug, vertical, services, am_email, ssp_client_contact_id, mrr, contract_end) values
('<pilot1_location_id>', '<Pilot One Pools>', 'pilot1', 'pool_builder', '{content,social,ads}', 'lisa@smallscreenproducer.com',   '<contact id in SSP account>', 2500, '2027-01-31'),
('<pilot2_location_id>', '<Pilot Two Spas>',  'pilot2', 'hot_tub',      '{ads,seo}',            'lauren@smallscreenproducer.com', '<contact id in SSP account>', null, null);
```
Finding `ssp_client_contact_id`: in the SSP subaccount, open Contacts, search the client's company, open the record; the ID is the last path segment of the URL `/contacts/detail/<id>`. Optional helper `collector/tools/find_client_contact.py --q "<company>"` calls `POST /contacts/search` on the parent with `"query"` and prints candidates.

Post-migration checks (all in the SQL editor unless noted):
- Settings → API → Exposed schemas lists only `public` (and `graphql_public`); `vault` is not exposed.
- `select public.pit_key_ok('wrong')` returns false and leaves a `read_denied` row in `pit_audit`.
- Database → Replication: no table from this schema is in the `supabase_realtime` publication (Realtime off).
- With the anon key via REST (`curl -H "apikey: <anon>" .../rest/v1/v_portfolio`): 401/permission denied, not rows.
- With a staff JWT: `v_portfolio` returns rows; `insert` into `flag_acks` with a different `acked_by` is rejected; `update`/`delete` on anything is rejected.

Residual exposure, stated: anyone with Supabase dashboard access (the `postgres` role in the SQL editor) can read `vault.decrypted_secrets` and every table. Mitigation is organizational: minimal dashboard members, MFA enforced on the Supabase org, and the audit table for collector reads. The service role key grants the same; it lives only in Render and Matthew's local `.env`.

---

## 7. Collector (Python 3.11)

### 7.1 Layout
```
collector/
  __init__.py
  main.py          CLI: --probe | --dry-run | --location <id|slug> | --max-pages N | --date YYYY-MM-DD | --backfill N
  ghl_client.py    session, headers, token bucket 8 rps, retries, PermissionError on 401/403 with scope hint, header redaction
  fetchers.py      one function per endpoint in section 3; each records Coverage; none raise past its boundary; strips phone/email/body before returning
  metrics.py       pure functions for section 4; no I/O
  flags.py         pure functions for section 5; inputs: metrics dict, thresholds, peer stats, subaccount row
  store.py         Supabase reads/writes (supabase-py, service role); get_pit(), pit_updated_at() RPC wrappers
  reference/       ghl_am_brief.py, test_mock.py (copied in, read-only reference)
  tools/pit.py     optional: set | rotate | delete | status  (Vault UI is the primary path, section 8.4)
  tools/find_client_contact.py
  tests/           test_metrics.py, test_flags.py, test_mock_run.py, test_pii.py, fixtures/*.json
  requirements.txt requests, supabase, python-dateutil, tzdata
```

### 7.2 Run sequence (`main.py`)
1. `run = store.start_run()`. `store.rpc("pit_key_ok")` must return true, else finish the run as `failed` and exit 1.
2. `subs = store.load_subaccounts(active=True)`; if `--location`, filter to it plus the parent (relationship metrics need it).
3. **Parent first.** Token via `store.get_pit(parent_location_id)`. With it: invoices (all pages), calendars + events for `[-60d, +60d]`, users. Index invoices by `contactDetails.id`, events by `contactId`. Also collect the parent's own operational metrics (SSP's own leads/pipeline/convos) and store its snapshot like any other location; portfolio hides `is_parent` by default.
4. **Each client subaccount** (sequential, or `ThreadPoolExecutor(max_workers=4)` across locations once there are more than ~30; each token has its own bucket): token via `store.get_pit(location_id)` (null → `token_status='none'`, mark failed, continue; 401/403 on `/locations` → `token_status='invalid'`, sanitized `last_token_error`, continue). Verify location (G1); on success set `token_status='ok'` and `token_rotated_at = pit_updated_at(location_id)::date`. Fetch users, pipelines, opportunities (open all pages; won and lost last 90d), recent conversations (14d), contacts in two ranges (7d window; baseline `[now-35d, now-7d)`), tagged contacts, form submissions (both windows), calendars + events `[-28d, +7d]`, blogs, social posts, social accounts (the last three only if services warrant). Speed-to-lead per new contact (cap 100). Relationship metrics from the parent index by `ssp_client_contact_id`, plus `GET /conversations/search?locationId=<parent>&contactId=<id>` on the parent for last touch. Compute metrics (section 4). Read last week's flags for change tracking. Write snapshot (gate result included), `lead_events`, and `lead_history` (current and previous week). Hold flags until the peer pass.
5. **Peer pass.** Group today's client snapshots by vertical; compute `peer_median_delta_pct`, `peer_n`; update rows. Then compute flags for every location, compute `flags_new`/`flags_resolved` against last week, write flags (`delete from flags where location_id=? and snapshot_date=?` then insert) and update the two jsonb columns.
6. `store.finish_run(status, counts, requests_made, rate_limited, details, error)` where `details[location_id] = {status, gate:[...], requests, rate_limited, seconds, error}`. Exit 0 all ok, 2 any held or failed, 1 crash.

`--backfill N`: for each location, query contacts by `dateAdded` over the last N ISO weeks (and form submissions if available), and upsert `lead_history` rows. Run once after the first successful live run; safe to rerun.

`--location <id>` reruns one location (plus the parent), refreshes its `lead_history`, and recomputes peer stats for its vertical.

PII boundary: `fetchers.py` returns records already stripped of `phone`, `email`, `lastMessageBody`, message `body`, and any address fields; presence booleans (`has_phone`) replace the values. `test_pii.py` asserts that no fixture record's phone/email string appears anywhere in the produced snapshot, flags, `lead_events`, or run details.

### 7.3 Store contracts (supabase-py)
- `upsert("snapshots", row, on_conflict="location_id,snapshot_date")`
- `upsert("lead_events", rows, on_conflict="location_id,contact_id")`
- `upsert("lead_history", rows, on_conflict="location_id,week_start")`
- flags: delete-then-insert per (location, date)
- `read_flags(location_id, date)`: codes for a given snapshot date (used for `flags_new`/`flags_resolved` against `date - 7`)
- `read_prev_dead(location_id, date)`: last 3 gate-passed snapshots' `(leads_new_7d, convos_active_7d, opps_created_7d)` for G4
- `update_subaccount(location_id, {token_status, token_rotated_at, last_token_error})`
- `rpc("get_pit")`, `rpc("pit_updated_at")`, `rpc("pit_key_ok")`

### 7.4 `--probe` mode (run this first; it is the field-verification tool)
For the given location: call each endpoint in section 3 once with `limit=2`, print the raw JSON of the first record for each with phones, emails, message bodies, and the Authorization header redacted, print HTTP status per call, and write everything to `VERIFICATION.md` with a checklist: contacts/search range filter accepted? `attributionSource` present? opportunity timestamp fields present? `tasks`/`calendarEvents` populated? do `date`/`endDate` filter opportunities? message `source`/`userId` values seen? invoice status values? forms/submissions envelope? calendar event created field and `appointmentStatus` values? blog/social envelope keys? social accounts expired field? Fix `fetchers.py` field paths from that output, rerun `--probe`, then proceed. Budget 30 to 60 minutes here; it is where the schedule risk lives.

### 7.5 `details` jsonb contract (frontend depends on these exact keys; cap each list at 50; names, IDs, timestamps, amounts, deep links only)
```ts
type Details = {
  users: Record<string, string>;                              // userId -> display name
  pipelines: Record<string, { name: string; stages: Record<string, string> }>;
  leads_by_source: Record<string, number>;                    // 7d
  funnel_28d: { form_submissions: number|null; leads: number; opps_created: number; appts_booked: number|null; won: number };
  uncontacted_leads: { contact_id: string; name: string; source: string|null; created_at: string; hours_since: number; deep_link: string }[];
  unassigned_leads:  { contact_id: string; name: string; source: string|null; created_at: string; deep_link: string }[];
  waiting_convos: { conversation_id: string; contact_id: string|null; contact: string; channel: string|null; hours: number; last_inbound_at: string; deep_link: string }[];
  stale_opps: { opp_id: string; name: string; pipeline: string; stage: string; days_idle: number|null; idle_source_field: string|null; days_in_stage: number|null; value: number|null; owner_id: string|null; owner: string; next_step: 'task'|'event'|'none'|'unknown'; deep_link: string }[];
  missing_value_opps: { opp_id: string; name: string; deep_link: string }[];
  past_due_invoices: { invoice_id: string; number: string|null; amount_due: number|null; due_date: string; days_over: number; status: string }[];
  appts_next_7d: { id: string; title: string; start: string; contact_id: string|null; status: string|null }[];
  recent_publishes: { kind: 'blog'|'social'; title: string|null; published_at: string }[];
  social_accounts: { id: string; platform: string|null; name: string|null; expired: boolean|null }[];
  review_asks_stale: { contact_id: string; name: string; days_quiet: number; deep_link: string }[];
  review_ask_gap: { opp_id: string; name: string; won_at: string; contact_id: string|null; deep_link: string }[];
  client: { contact_id: string; last_touch_at: string|null; next_appt_at: string|null; deep_link: string } | null;
  lost_reasons_90d: Record<string, number>;
  speed_to_lead: { contact_id: string; name: string; created_at: string; first_touch_minutes: number|null; first_human_touch_minutes: number|null; kind: 'human'|'automation'|'unknown'|null }[];
  changed: { new: string[]; resolved: string[] };            // flag codes
  ghl_dashboard_url: string;                                  // deep link to the native GHL dashboard for ads
};
```

### 7.6 Tests (no network)
`tests/fixtures/*.json` hold canned responses per endpoint. `test_mock_run.py` monkeypatches `GHLClient.request` and asserts: stale/stuck flags fire on the right opp; weekend wait rule (Fri 18:30 CT inbound observed Mon 12:00 CT = 3.0h); whole-word exclusion; speed-to-lead human vs automation classification; baseline from the second contacts range (`trailing_n` and `leads_trailing_avg`); `SOURCE_DROP` fires for a source that went to zero and not for a minor source; `FORM_SILENT` vs `INTEGRATION_SUSPECT` precedence; `flags_new`/`flags_resolved` against a seeded prior week; peer median with n≥4 and n<4; gate pass; gate fail on 2 sources 403; gate fail on wrong location name; G4 dormant-account exemption; `--backfill` writes the expected `lead_history` rows. `test_pii.py` as in 7.2. Run `pytest -q` green before first live run.

### 7.7 Env vars (collector only)
Secrets, exactly three: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `COLLECTOR_KEY`. Non-secret: `GHL_APP_BASE=https://crm.smallscreenproducer.com`, `TZ=America/Chicago`. Optional Tier 2: `RESEND_API_KEY`, `DIGEST_FROM`. No `GHL_PIT_*` variables anywhere; tokens come from Vault.

### 7.8 `render.yaml`
```yaml
services:
  - type: cron
    name: ghl-health-collector
    runtime: python
    plan: starter
    schedule: "30 10 * * *"        # 05:30 America/Chicago during CDT; revisit in November
    buildCommand: pip install -r collector/requirements.txt
    startCommand: python -m collector.main
    envVars:
      - key: PYTHON_VERSION
        value: "3.11"
      - key: SUPABASE_URL
        sync: false
      - key: SUPABASE_SERVICE_ROLE_KEY
        sync: false
      - key: COLLECTOR_KEY
        sync: false
      - key: GHL_APP_BASE
        value: https://crm.smallscreenproducer.com
      - key: TZ
        value: America/Chicago
      # That is the complete secret set. PITs are in Supabase Vault, not here.
```
Idempotent: rerunning today overwrites today's rows. Enable Render's cron failure notifications to Matthew's email; that is the dead-man's switch for "no run happened." Zero-cost alternative: GitHub Actions `schedule:` with repository secrets (best-effort timing; fine for daily).

---

## 8. Private Integration Tokens: permissions and secure storage

Decision (Matthew, Aug 18): one read-only PIT per subaccount, created in GHL and pasted into the Supabase Vault by hand. No agency token, no OAuth app. The collector reads tokens from the Vault at run time. That is the whole token strategy.

### 8.1 Creating them
One PIT per subaccount, created inside that subaccount: Settings → Private Integrations → Create new Integration. Name `ssp-health-readonly`. The token is shown once. Rotate every 90 days (GHL keeps old and new valid for 7 days). Agency-level PITs are confirmed too restricted; do not use one.

Read-only scopes only. The UI groups by module with view-style checkboxes; the underlying scopes you should end up with:

Required: `locations.readonly`, `users.readonly`, `contacts.readonly`, `conversations.readonly`, `conversations/message.readonly`, `opportunities.readonly`, `calendars.readonly`, `calendars/events.readonly`, `invoices.readonly`, `forms.readonly`, `blogs/list.readonly`, `blogs/post.readonly`, `socialplanner/post.readonly`, `socialplanner/account.readonly`.
Optional: `locations/tags.readonly`, `locations/customFields.readonly`.
Never: any `.write`, workflows, campaigns, payments write, saas, snapshots.

The SSP parent gets the same set. If a source shows `SOURCE UNAVAILABLE` on first run, edit the integration's scopes (no new token) and rerun.

### 8.2 What a leaked PIT can do
Read-only access to one client's CRM: contacts with phone and email, full conversation history, pipeline, invoices. No writes, no money movement, no account changes. That is still client PII, so treat these as secrets, but the blast radius is bounded per token and each is revocable in one click inside GHL. Design accordingly: prevent bulk exposure, make rotation cheap, keep an audit trail.

### 8.3 Storage: Supabase Vault, with Render holding exactly three secrets
Tokens are stored as Vault secrets, encrypted at rest with libsodium; the key material is held by Supabase outside the database, so a database dump, backup, or read replica yields ciphertext only. Retrieval goes through a `security definer` function that (1) is executable only by `service_role`, never by `anon` or `authenticated`, and (2) requires a second secret, `COLLECTOR_KEY`, so a leaked service role key alone cannot read PITs. Every collector read and every set/rotate/delete lands in `pit_audit`. Render holds only `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `COLLECTOR_KEY`, forever, regardless of how many subaccounts exist. Local dev pulls tokens the same way, so no laptop ever holds a token file.

Threat summary: token in git, safe (never there); SPA/anon, safe (`vault` not API-exposed, RPCs revoked from anon/authenticated); DB dump, ciphertext only; service role key alone, safe (needs `COLLECTOR_KEY`); Render environment compromised, all tokens exposed (irreducible floor for any job that must hold credentials); Supabase dashboard member with SQL access can decrypt (mitigation: minimal members, MFA on the org). Rejected alternative: one Render env var per PIT (fine at 3 tokens; env sprawl, no audit, manual rotation at SSP scale).

The SQL for `pit_audit`, `pit_key_ok`, `get_pit`, `pit_updated_at`, `set_pit`, `delete_pit` is in the migration (section 6).

### 8.4 Loading tokens: Supabase Vault UI (decided), CLI optional
Per subaccount: create the PIT in that subaccount (8.1) → Supabase Dashboard → Project Settings → Vault (enable the Vault integration if prompted) → **Add new secret** → Name exactly `ghl_pit_<location_id>` (the GHL location ID, not the slug), Secret = the PIT, Description = client name → Save. Close the GHL tab. The `collector_key` bootstrap is the same screen: Name `collector_key`, Secret = the generated string from section 12.

Rotation: open the same secret in the Vault UI, replace the value, keep the name. The collector reads `pit_updated_at` each run, so `token_rotated_at` and the 80-day warning stay correct without any manual bookkeeping. Deleting the secret in the UI makes that account `no_data` on the next run with reason "token missing."

The naming convention is the contract: `get_pit` looks up `ghl_pit_<location_id>` and nothing else. A typo in the name is indistinguishable from a missing token; the `/runs` page will say which locations returned no token.

Optional CLI, `collector/tools/pit.py` (`set | rotate | delete | status`), wraps the same RPCs with a hidden `getpass` prompt for anyone who prefers a terminal or wants to script onboarding. `status` lists every subaccount with `token_status`, `token_rotated_at`, and flags anything older than 80 days. Not required for anything.

### 8.5 Collector retrieval
At run start: `rpc("pit_key_ok", {"p_key": COLLECTOR_KEY})` must return true, else abort with a clear error (exit 1). Per location: `token = rpc("get_pit", {"p_location_id": loc, "p_collector_key": COLLECTOR_KEY})`. Held in memory for that location's fetch only. Never logged: `ghl_client.py` strips the `Authorization` header from any exception text, coverage error strings, and `--probe` output. Null → `token_status='none'`, continue. After successful G1: `token_status='ok'`, `token_rotated_at = pit_updated_at(loc)::date`.

### 8.6 Operational rules
- Rotate every 90 days: rotate in GHL first, then replace the secret's value in the Vault UI, no downtime. The collector warns per location at 80 days; `/runs` shows it.
- MFA enforced on the Supabase org and the Render team. Minimal members on both. These two dashboards are the residual exposure.
- Rotate through the Vault UI (or the optional CLI), never by editing `vault.secrets` rows with SQL.
- The service role key never leaves Render and Matthew's local `.env`. If it leaks, rotate it in Supabase (JWT secret rotation) and regenerate `COLLECTOR_KEY`; PITs do not need to be reissued because the leaked key alone could not read them.
- Onboarding a new client is three steps: create the PIT in that subaccount, add `ghl_pit_<location_id>` in the Vault UI, insert one `subaccounts` row (`ssp_client_contact_id`, `vertical`, `services`, `am_email`, `mrr`, `contract_end`). No Render change, no redeploy.

Fallback only if the Vault extension cannot be enabled at build step 2: `GHL_PIT_<SLUG>` env vars, and `get_pit` becomes an `os.environ` lookup behind the same function signature. Do not implement this path speculatively.

---

## 9. Frontend (Vite + React + TypeScript + Tailwind + Recharts on Netlify)

Reads Supabase directly with the anon key; RLS is the boundary. No custom read API. Two insert paths (acks, notes), also through the anon key under RLS.

### 9.1 Auth: email code only, pre-provisioned staff, no OAuth, no Google
Decision: sign-in is a 6-digit email code (Supabase email OTP) for accounts Matthew has created in advance. Public sign-up is disabled. No third-party identity provider, no Google Cloud project, no OAuth client, no popup, no callback route. It works identically standalone, inside a same-site iframe, and inside a cross-site iframe (it never leaves the frame); nothing is registered at Google; with Supabase's refresh-token session it is a once-per-device event.

Why hosting at `health.smallscreenproducer.com` still matters: the GHL white-label is `crm.smallscreenproducer.com`, so the iframe is same-site and its localStorage is not partitioned (Chrome, Firefox dFPI, Safari ITP all key on eTLD+1). A session established in a top-level tab is therefore also present inside the iframe, and vice versa. If an AM opens GHL at `app.gohighlevel.com`, the iframe is third-party and gets its own partitioned storage; login still works there, it is just a separate session that persists on its own.

Client setup: `createClient(url, anonKey, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false } })`.

`/login` flow, entirely inside whatever frame it renders in:
1. Email field, prefilled from `?hint=` when present (GHL passes `{{user.email}}`; convenience only, never trusted). Show "smallscreenproducer.com staff accounts only."
2. `await supabase.auth.signInWithOtp({ email, options: { shouldCreateUser: false } })`. On an error mentioning "Signups not allowed" or "restricted", show "Not a registered staff account. Ask Matthew to add you." Do not reveal whether the address exists beyond that.
3. Six-digit input; `await supabase.auth.verifyOtp({ email, token, type: 'email' })`. On success, navigate to the originally requested route.
4. Resend-code link with a 30 s cooldown; codes expire in 10 minutes (set Auth → Email → OTP expiry to 600).

Requirements (section 12): custom SMTP via Resend, `{{ .Token }}` in both the Magic Link and Confirm signup templates, "Allow new users to sign up" off, staff pre-created.

Session: access tokens refresh silently; refresh tokens do not expire by default, so a device stays signed in as long as the tool is opened occasionally. Set Auth → Sessions → Inactivity timeout to 30 days as a hard bound. Sign-out button in the top nav clears the session on that device.

Embedding detection (UI only): `const embedded = window.self !== window.top || new URLSearchParams(location.search).has('embed')`. When embedded: hide the top nav, show an "Open full view" link (`target=_blank`). No auth logic branches on it.

### 9.2 Global chrome
- **Snapshot age banner** on every page: "Data as of Tue 05:31 CT. Next run 05:30." From the latest `collector_runs.finished_at`. Trust depends on knowing freshness.
- **State rendering**: icon plus color, never color alone (red filled circle, amber triangle, grey dash, green check for steady). Color-only fails accessibility and prints badly.
- **Keyboard**: `j`/`k` move selection, `Enter` opens, `a` acknowledges the selected account's top flag, `/` focuses search.
- Auto-refresh queries every 15 min. Filter and sort state in the URL query. Loading skeletons and explicit empty states everywhere.

### 9.3 Routes
- `/login`: section 9.1.
- `/` **Portfolio** (query `v_portfolio`; sparklines from `lead_history` last 8 weeks per location):
  - Filters: "My accounts" (default; `am_email == session email`) / "All"; "Include SSP" checkbox (`is_parent`, off by default); text search on account name; vertical; state; flag code. Sort: "by attention" (default: `attention_score` desc, `leads_delta_pct` asc) or "by MRR at risk" (`attention_score > 0` first, then `mrr` desc).
  - Optional **Group by AM** with a team header row per AM: "Lisa: 12 accounts, 3 attention, $18.4k MRR in attention." Header tile above the table: MRR in attention (sum of `mrr` where state = attention, with "n accounts without MRR set").
  - Three sections in order: **Needs attention**, **Steady**, **No data**; each with an explicit empty state ("No accounts need attention").
  - **Compact columns (default)**: Account (link), AM, State icon + unacked flag chips (red/amber counts, muted "acked n"), New (count of `flags_new`, tooltip lists codes), Leads 7d with delta % vs baseline (small peer note) and 8-week sparkline, Uncontacted >24h, Convos waiting (max h), Top action (`top_action`).
  - **Expanded row / column chooser** (persisted in `localStorage`): stale opps (n, $), past due ($), days since publish, client last touch (d), unassigned leads, no-show %, lead→opp %, win rate, social disconnected, MRR, contract end, data quality badge (complete / partial / held from `coverage`), snapshot date.
  - Row click opens the drilldown. CSV export of the current filter (compact + expanded columns).
- `/account/:locationId` **Drilldown** (reads `subaccounts` row, latest `snapshots` row, its `flags`, active `flag_acks`, `account_notes`, `lead_history` 12 weeks, `v_history` 84d, `lead_events` 30d), in this order:
  1. Header: name, AM, vertical, services chips, MRR and contract end (or "not set"), deep links to the GHL subaccount and to its native dashboard (Google Ads lives there), data quality + snapshot time.
  2. **Do next**: top 3 unacked flags with action text, deep link, and an Acknowledge button (section 9.5). Acked flags render muted below with "acked by Lisa, 3d ago: called them, waiting on budget."
  3. **Changed this week**: `flags_new` and `flags_resolved` as chips.
  4. KPI tiles: leads 7d vs baseline (with peer note or "baseline building n/4"), speed to lead median/p90 (label "automation included" if `!speed_kind_known`), uncontacted 24h, convos waiting, open pipeline $, stale $, past due $, days since publish, client last touch, no-show %, lead→opp %, win rate, median days to close.
  5. **Funnel strip (28d)**: form submissions → new leads → opps created → appointments booked → won, from `details.funnel_28d`; nulls render as "n/a".
  6. Charts: leads by week for 12 weeks stacked by source (top 5 + other) from `lead_history` with the trailing avg line and peer median band; speed-to-lead histogram from `lead_events`.
  7. Tables from `details`, each row linking into GHL, collapsed by default except those tied to a currently firing flag: uncontacted leads, unassigned leads, waiting conversations, stale opps (owner name via `details.users`, next step), missing-value opps, past-due invoices, next 7d appointments, recent publishes, social accounts (with expired marker), review-ask gaps, lost reasons 90d.
  8. Collapsible **Coverage and caveats** rendering `coverage` verbatim: keep it visible; it is the honesty layer.
  9. **Notes**: append-only list plus an input (section 9.5).
- `/runs`: last 30 `collector_runs`, expandable per-location detail from `details` (status, gate results, requests, 429s, seconds, error), plus a token health list (accounts with `token_status <> 'ok'` or `token_rotated_at` older than 80 days).

Design: dense, table-first, neutral palette, color only for state (with icons). Mobile: portfolio rows become cards; drilldown stacks. No hero charts.

### 9.4 `netlify.toml`
```toml
[build]
  base = "web"
  command = "npm ci && npm run build"
  publish = "dist"

[[redirects]]
  from = "/*"
  to = "/index.html"
  status = 200

[[headers]]
  for = "/*"
  [headers.values]
    Content-Security-Policy = "frame-ancestors 'self' https://crm.smallscreenproducer.com https://app.gohighlevel.com https://*.gohighlevel.com https://*.leadconnectorhq.com https://*.msgsndr.com"
    X-Content-Type-Options = "nosniff"
    Referrer-Policy = "strict-origin-when-cross-origin"
```
Do NOT set `X-Frame-Options` anywhere. Env: `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (the anon key is public by design; RLS is the boundary).

### 9.5 The two writes: acknowledgements and notes
- Acknowledge: `insert into flag_acks {location_id, code, acked_by: session email, note (optional), snooze_until: today + 7|14|30}` via `supabase.from('flag_acks').insert(...)`. RLS requires `acked_by` to equal the JWT email; the check constraint bounds `snooze_until` to 90 days. `v_portfolio` already excludes acked flags from red/amber and the attention score; the drilldown mutes them. A red that returns after the snooze expires is a genuinely stale problem, which is the point.
- Notes: `insert into account_notes {location_id, author: session email, body}` (≤ 4000 chars). Rendered newest first. No edit, no delete (append-only history by design; RLS has no update/delete policies and the role has no update/delete grants).

### 9.6 Types
Generate with `supabase gen types typescript --project-id <ref> > web/src/lib/database.types.ts` after the migration, or hand-write to match section 6. `Details` from 7.5 lives in `web/src/lib/details.ts`.

---

## 10. GHL menu integration (so nobody leaves GHL)

GHL Custom Menu Links can open a URL inside the GHL sidebar as an embedded page (iframe), scoped to specific sub-accounts and roles, with `{{location.id}}`, `{{user.email}}` and `{{custom_values.*}}` substituted at click time.

Steps (agency admin; labels may vary slightly by GHL release):
1. Switch to the **Agency** view → **Settings** → **Custom Menu Links** → **+ Create New** (or "Add Custom Menu Link").
2. **Link 1: portfolio, on the SSP main subaccount**
   - Title: `Account Health`
   - Icon: any dashboard/pulse icon
   - Link URL: `https://health.smallscreenproducer.com/?embed=1&hint={{user.email}}`
   - Open mode: **Open in an Embedded Page (iFrame)**
   - Show in: **Sub-account sidebar** (not agency)
   - Show to sub-accounts: **Selected sub-accounts** → choose **Small Screen Producer** only
   - Show to user roles: **All** (or Admin only if you want to restrict)
   - Position: near the top of the sidebar
   - Save.
3. **Link 2 (optional): per-client drilldown inside each client subaccount**
   - Title: `Account Health (this account)`
   - Link URL: `https://health.smallscreenproducer.com/account/{{location.id}}?embed=1&hint={{user.email}}`
   - Open mode: iFrame; Show in: Sub-account sidebar; Show to sub-accounts: Selected → your client subaccounts (or All); Roles: **Admin** only, so client-side users do not see a login wall they cannot pass. Skip in v1 if you would rather AMs drill from the portfolio.
4. Test as an AM: open GHL at `crm.smallscreenproducer.com`, enter the SSP subaccount, click **Account Health**. First time on that device: enter your work email, type the 6-digit code from the email. Subsequent visits: already signed in.
5. If the iframe stays blank: check the browser console for a `frame-ancestors` violation (fix the CSP host list in 9.4, e.g. add the exact GHL asset host you see) or a Netlify `X-Frame-Options` header (must not exist). If you are asked to sign in again inside GHL after signing in standalone, you opened GHL at `app.gohighlevel.com` (cross-site, separate storage); use `crm.smallscreenproducer.com` or sign in once more, it persists.
6. Fallback if embedding is fought at every turn: set Open mode to **New tab**. Same URL, same auth, one click more.

Also worth adding a Custom Menu Link in the **Agency** sidebar to the same portfolio URL for Pam/Matthew, since agency view is where the account overview mentally lives.

---

## 11. Build order for a single session

Timeboxes are targets. Total: low 6 h / expected 10 h / high 2.5 days; the spread is `--probe` field drift and DNS/SMTP console work.

| # | Step | Accept when | Est |
|---|---|---|---|
| 1 | Repo scaffold; `gh repo create smallscreenproducer/ghl-health --private --source . --push` (or Matthew's GitHub org of choice; if `gh` is not authenticated, create the private repo in the browser and push); copy reference collector into `collector/reference/` | tree matches section 7.1; remote exists; `.gitignore` covers `.env`, `web/dist`, `__pycache__` | 15 m |
| 2 | Migration + seed in Supabase SQL editor; Matthew adds `collector_key` in the Vault UI; post-migration checks | `select * from v_portfolio` returns seeded rows with `state = 'no_data'`; `vault` not in API exposed schemas; `select public.pit_key_ok('wrong')` returns false and leaves a `read_denied` audit row; `pit_key_ok('<real key>')` returns true; anon REST call to `v_portfolio` is denied | 35 m |
| 3 | `ghl_client.py`, `store.py` RPC wrappers; Matthew adds `ghl_pit_ZnckuEDPIcWu8fn72ppi` in the Vault UI; then `--probe` | `select public.get_pit('ZnckuEDPIcWu8fn72ppi','<collector_key>') is not null` in the SQL editor; `VERIFICATION.md` written for SSP with per-endpoint status and sample fields; no token, phone, email, or message body appears anywhere in output | 50 m |
| 4 | `fetchers.py`, `metrics.py`, `flags.py`, rest of `store.py`, tests; Matthew adds the 2 pilot PITs in the Vault UI | `pytest -q` green including `test_pii.py`; mock run produces expected flags and gate outcomes; three `ghl_pit_*` secrets exist | 120 m |
| 5 | Live run, SSP + 2 pilots, `--max-pages 2` then full; then `--backfill 12` | 3 snapshot rows, `gate_passed=true`, coverage complete for ≥ 8 sources; `lead_history` has 12 weeks per location; 5 flagged items hand-verified in GHL UI; `select details::text from snapshots` contains no `@` or phone-shaped strings; revoke two required scopes on a pilot (e.g. `contacts.readonly` and `opportunities.readonly`) and rerun → gate G2 fails and that row shows `no_data`; restore the scopes | 75 m |
| 6 | Render cron deployed via Blueprint; failure notifications on | manual trigger ok; env vars set; next scheduled run ok | 20 m |
| 7 | SPA: login (email code), portfolio (compact + expanded, group by AM, filters, keyboard, CSV), drilldown (do next, changed, KPIs, funnel, charts, tables, coverage, notes), acks + notes inserts, runs | Matthew signs in, sees 3 accounts in correct states, acknowledges a flag and watches it leave the attention count, adds a note, deep links open the right GHL records; an insert with a forged `acked_by` is rejected | 180 m |
| 8 | Netlify deploy, custom domain, CSP; Supabase auth: sign-ups off, staff pre-created, SMTP + `{{ .Token }}` templates, OTP expiry, inactivity timeout | `https://health.smallscreenproducer.com` serves over TLS; email-code login works standalone and inside the iframe; an unregistered address gets the "not a registered staff account" message and no code | 40 m |
| 9 | GHL Custom Menu Link (section 10) | Portfolio renders inside GHL for an AM after one sign-in | 20 m |
| 10 | Pilot week: onboard remaining subaccounts (3 steps each), log false positives per flag code, tune `thresholds`, set `mrr`/`contract_end` | | ongoing |

Definition of done for v1: an AM opens GHL → SSP subaccount → Account Health, sees their accounts ranked, clicks into one, reads the top action, clicks a stale opportunity, lands on that record in GHL, comes back and acknowledges the flag with a note. Data is at most 24 h old, and every number they see either has a coverage label of complete or is visibly marked otherwise.

---

## 12. Console steps for Matthew (parallel human track)

Do these while Claude Code builds; they are the only parts that need a human in a browser.

**Supabase**
1. New project (region us-central or east). Copy Project URL, anon key, service role key. Organization settings → enforce MFA for all members; keep the member list minimal.
2. SQL editor → paste `0001_init.sql`, run. Then `seed.sql`. Run the post-migration checks at the end of section 6.
3. Authentication → URL Configuration: Site URL `https://health.smallscreenproducer.com`; Additional Redirect URLs: `http://localhost:5173` (dev only).
4. Authentication → Providers: Email only; every third-party provider (Google, GitHub, etc.) disabled. Authentication → Settings: **Allow new users to sign up: OFF**. Sessions: Inactivity timeout 30 days.
5. Authentication → Users → **Add user** for each of the 11 staff (work email, "Auto Confirm User" on). This is the allowlist; the domain trigger is the backstop.
6. Authentication → Email: set OTP expiry to 600 seconds. Two things are required for the code flow: (a) **Custom SMTP**: create a Resend account, add and verify `smallscreenproducer.com` (Resend shows the DKIM/SPF TXT records to publish in DNS), then in Supabase set SMTP host `smtp.resend.com`, port 465, user `resend`, password = Resend API key, sender `health@smallscreenproducer.com`. Without custom SMTP the built-in mailer allows only a few emails per hour. (b) **Email Templates → Magic Link AND Confirm signup**: both bodies must include `{{ .Token }}` (e.g. `Your sign-in code is {{ .Token }}. It expires in 10 minutes.`); if either template lacks the token the email carries only a link and the 6-digit input has nothing to accept.
7. Optional later: Integrations → Cron → enable pg_cron, then run the commented retention block from the migration.

**Collector key + PITs** (section 8, all in the Supabase Vault UI):
1. Generate `COLLECTOR_KEY` locally: `python -c "import secrets;print(secrets.token_urlsafe(32))"` (or any 32+ character random string). Supabase Dashboard → Project Settings → Vault → Add new secret: Name `collector_key`, Secret = that value. Put the same value in your local git-ignored `.env` and later in Render. Nowhere else.
2. In GHL, create the PIT in the SSP subaccount and in each pilot subaccount (8.1, read-only scopes). For each: Vault → Add new secret: Name `ghl_pit_<location_id>`, Secret = the PIT. Close the GHL tab; the token now exists in exactly one place.
3. Sanity check in the SQL editor: `select public.get_pit('<location_id>', '<collector_key>') is not null;` should return true for each of the three.

**Render**: New → **Blueprint** → select the repo; Render reads `render.yaml` and creates the cron job (a manually created Cron Job ignores `render.yaml`). Add env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `COLLECTOR_KEY` (mark secret). That is all. Trigger a manual run. Turn on failure notifications for the service. Team MFA on.

**Netlify**: Add new site → import repo → base `web`, build `npm ci && npm run build`, publish `dist`. Env: `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`. Domain management → add `health.smallscreenproducer.com`. Netlify provisions TLS after DNS resolves.

**DNS** (wherever `smallscreenproducer.com` is hosted): `CNAME health → <your-site>.netlify.app`, plus the Resend DKIM/SPF TXT records from step 6a. Propagation minutes to an hour.

**GHL**: section 10.

---

## 13. Risks, stated plainly

- Field drift on first live run is the schedule risk. `--probe` exists to make it a 30-minute problem instead of a day. Every fetcher degrades to Unknown + coverage note; a wrong field costs a column, not the run. Highest-risk VERIFY items: `contacts/search` range filter, opportunity timestamp fields, `getTasks`/`getCalendarEvents` population, message `source`/`userId`, `forms/submissions` envelope, calendar `appointmentStatus` vocabulary and created field, social accounts expired field, blog/social envelopes.
- Speed to lead's value depends on separating human from automated outbound. If GHL's message `source`/`userId` do not make that clean, the metric is "first response including automation" and is labeled that way; auto-responders make everyone look instant.
- "No next step" is strong only if `getTasks/getCalendarEvents` populate on `opportunities/search`; else it is null and out of the attention score.
- Baseline is live from CRM history on day one, but the peer benchmark needs ≥4 same-vertical accounts with tokens; until then `LEADS_DROP` is amber and the UI says "peers not yet available."
- Thresholds are uncalibrated guesses. Loose in month one; acknowledgements absorb most of the noise while you tune.
- Iframe: the email-code login works inside any frame. The remaining embed risk is GHL's frame host list vs the CSP in 9.4; if it fights back, the menu link opens in a new tab and nothing else changes.
- Reviews and ad platforms are not in the GHL API; the UI says so instead of pretending.
- Security residual: Supabase dashboard members and the service role key can read everything, including decrypted Vault secrets. Organizational controls (minimal members, MFA, key hygiene) are the mitigation; there is no technical control that removes it short of a separate secrets service.
- Render cron is UTC; adjust the first week of November.

---

## 14. If the web app stalls
Same collector, target a Google Sheet, Looker Studio on top (SSP already runs a Looker monthly report). Loses deep links, drilldown polish, and acks; keeps all metric logic; migrates back to Supabase later without touching the collector's math.
