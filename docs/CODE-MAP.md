# Code map: what the Python does

Plain-language map of the collector for anyone new to the code: the nightly
sequence, what each module is for, every metric and how it is calculated,
the sanity gate, the flags, and where the numbers can mislead. Written
2026-09-28 from a full read of the code (file:line references are to that
day's main). The code wins if this ever disagrees with it.

## 1. The nightly run in 8 steps

Runs as `python -m collector.main` at 05:30 Central on the Render cron (`render.yaml`). The GitHub Actions copy (`.github/workflows/collector.yml`) exists but fails daily until its secrets are set.

1. **Start-up:** checks COLLECTOR_KEY against Vault (wrong key = exit 1), loads active accounts, skips those marked as not using MLH, opens a "running" row in collector_runs (main.py:1304, 1343, 1464).
2. **Parent first:** pulls SSP's own account's meetings from 60 days back to 60 ahead, filed by client contact id (main.py:133-166).
3. **Per-account fetch:** token from Vault (none = "awaiting onboarding", not a failure), then read-only pulls: 42 days of contacts, open deals plus deals closed in 90 days, 14 days of conversations, form submissions, calendar, blogs/social if sold, form/survey inventory, workflows, and messages for the 100 newest leads.
4. **Metrics:** pure arithmetic in metrics.py.
5. **Gate and write:** gate G1 to G4, then save the daily snapshot (held ones too), per-lead speed rows, weekly lead totals, per-form rows (main.py:1570-1586).
6. **Peer pass and flags:** once all accounts are saved, median lead change per vertical, then flags, compared with the flags saved exactly 7 days earlier to mark "new" and "resolved" (main.py:1640).
7. **AM notes:** at most one note per client per day, only for new, worse, or due-a-reminder items, posted to SSP's GHL webhook. Off unless AUTOMATION_WEBHOOKS is dry or on (a Render setting; render.yaml defaults it to off).
8. **Monday digest and bookkeeping:** on Mondays, email each AM (if SMTP is set); close the run row with ok/held/failed counts. Exit 0 = all good, 2 = any held or failed, 1 = crash.

## 2. Modules

| Module | Job | Reads/writes | Lines |
|---|---|---|---|
| collector/main.py | Command line; runs the nightly sequence and one-off modes | all, via the modules below | 1,725 |
| collector/ghl_client.py | The one door to GHL: rate limit, retries, hides the token, refuses writes | GHL read-only | 294 |
| collector/fetchers.py | One function per endpoint; strips personal data; records scan completeness ("coverage") | GHL read-only | 1,024 |
| collector/form_history.py | All-time submissions per form, weekly test submissions set apart | GHL read-only | 342 |
| collector/lead_channels.py | Labels each form's channel | none | 163 |
| collector/metrics.py | All the math, windows, and the gate | none | 1,414 |
| collector/flags.py | Compares metrics with thresholds, emits flags | none | 593 |
| collector/store.py | Every database read/write and Vault token call | Supabase | 446 |
| collector/digest.py | Monday email per AM | email (staff domain only) | 629 |
| collector/automation.py | AM notes: who hears what, when; remembers what was said | webhook; Supabase | 1,452 |
| tools/form_activity.py | On-demand per-form report, optional site crawl | GHL read-only, public sites; CSV | 613 |
| tools/account_usage.py | Verdict per account: Working in MLH, Light use, Ads + automation only, Idle, No client logins | GHL read-only; CSV | 303 |
| tools/form_urls.py | Older form-to-page report | GHL read-only; CSV | 242 |
| tools/find_embeds.py | Crawls client sites for GHL form embeds | public sites; CSV | 364 |
| tools/pit.py | Manage tokens from a terminal | Supabase Vault | 131 |
| tools/find_client_contact.py | Find a client's contact id in the parent account | GHL read-only; Supabase | 83 |
| tagchecker/main.py | Loads client sites in headless Chrome; checks GA4, GTM, Meta, Google Ads, TikTok tags fire | public sites; Supabase | 224 |

## 3. What gets calculated

"This week" = the 7 full days ending last midnight, account timezone (metrics.py:113). "Baseline" = the 4 weeks before.

**Leads**
- leads_new_7d: new contact records this week. Imports count too. Excluded: first or last name with the whole word test, testing, demo, or sample, or tag internal-staff; excluded_count says how many (metrics.py:280-293).
- leads_trailing_avg: average per baseline week. A week counts only if the account had a contact before it ended (trailing_n = weeks counted; needs 2, else None).
- leads_delta_pct: (this week minus average) / average x 100. None if the average is under 3.
- peer_median_delta_pct / peer_n: median delta of today's gate-passed accounts in the same vertical (blank = "other"), needing 4; else the whole book; else None. Includes the account's own delta (main.py:892-914).
- leads_by_source_7d / _trailing: leads per source (source field, else web session or UTM source, else "unknown") and each source's baseline weekly average.
- leads_unassigned_7d: this week's leads with no owner.
- leads_missing_phone_pct_7d: percent with no phone; None under 5 leads.

**Response**
- Each outbound message, in order (metrics.py:471-493): source workflow/campaign/bulk/api = automation; sent within 2 minutes after the lead arrived or last wrote = automation, even with a user attached; has a user or source app/manual/user = human; else unknown.
- speed_to_lead_median_min / _p90_min: minutes from lead creation to first human reply, this week's leads. If no first reply is classifiable, any first reply.
- leads_uncontacted_24h: this week's leads 24h+ old with no outbound message of any kind.
- leads_no_human_touch_7d: those aged leads that got replies but none from a person; None if humans can't be told from automation.
- convos_waiting: customer wrote last, within the last 14 days, waiting 4h+. Weekend rule: arriving Friday 5 pm or later, Saturday, or Sunday starts the clock Monday 9 am (metrics.py:600-617). convos_waiting_max_hours = longest.
- calls_missed_7d: inbound calls this week marked no-answer, missed, busy, failed, or voicemail, from at most 30 call conversations.

**Pipeline** (open deals, plus won/lost in 90 days)
- opps_open / _value: count and dollars.
- opps_stale / _value: open deals whose newest of lastActionDate, lastStatusChangeAt, updatedAt is 14+ days old.
- opps_stuck: same stage 30+ days.
- opps_moved_30d: open deals that changed stage in 30 days (a change within 10 minutes of creation is the automatic placement, ignored) plus deals won or lost in 30 days. New deals don't count (metrics.py:954-976).
- opps_won_7d / opps_lost_7d: status changed to won/lost this week.
- win_rate_90d: won / (won + lost), 90 days; None under 5.
- median_days_to_close_90d: median of (status-change date minus created date), deals won in 90 days.
- bottleneck_stage / _value_usd: the stage whose idle open deals hold the most dollars.
- lead_to_opp_28d_pct: contacts created 14 to 42 days ago (needs 5) that later got a deal.

**Appointments**
- appts_booked_7d: events created this week. Only events starting 28 days back to 7 days ahead are fetched, so bookings further out are missed (main.py:465-468).
- appts_showed_28d / appts_noshow_28d: events started in 28 days marked showed / no-show.
- noshow_rate_28d: no-shows / (showed + no-shows); None under 5.

**Delivery** (content or social buyers only; else None)
- blogs_published_30d, social_published_7d: posts dated in the window (publish date, else updated or created). Social post status is not checked.
- days_since_last_publish: newest blog or social post; 90 when none found.
- social_accounts_expired: accounts marked expired, disconnected, or invalid.

**Relationship** (parent account, via ssp_client_contact_id)
- client_last_touch_days: days since the latest message with the client contact (any direction, automated too) or past meeting within 60 days.
- client_next_appt_at: earliest meeting in the next 60 days.

**Reviews**
- review_ask_gap: deals won in 30 days whose contact lacks the review-request tag.
- review_asks_stale: tagged contacts not updated in 7+ days (no flag uses it).

**Forms**
- form_submissions_7d / _trailing_avg: real submissions this week and per baseline week, weekly tests removed; None if the fetch failed.
- Status per form or survey, from all-time real submissions (metrics.py:177-209): unknown = count unavailable; dormant = last over 30 calendar days ago (never flagged); active = under 3 business days (Mon to Fri) since the last; silent = 3+ business days, within 30 days; new = none ever, created within 30 days; no_leads = none ever, older.
- Channel (lead_channels.py:112-163), first match wins: name says Facebook/Instagram/Meta = Facebook ad form; name says Google/GA/PPC = Google ad form; name says website, or most submissions from the client's own domain = Website form; half or more via GHL's hosted link = Standalone form link; another subdomain or site = Facebook or Google ad form if half the recent submissions carry that ad's click markers, else Landing page form; nothing to go on = Unknown. Form ids with submissions but missing from Sites > Forms = Facebook lead ad (no page, or Facebook attribution) or Unlisted form.

**Users**
- client_users / ssp_users: GHL role type "agency" = SSP; anyone else, even untyped, = client. None if the users fetch failed. Zero client users keeps the account out of the digest and AM notes.

## 4. The sanity gate (metrics.py:1304-1342)
- G1: the account record's id matches, and its name contains the configured name (case, curly apostrophes, "&"/"and", spacing ignored).
- G2: 2+ sources unavailable (error, nothing retrieved).
- G3: 2+ sources partial (error mid-scan, or not fully read, including a built-in cap).
- G4: leads, active conversations, and new deals all zero, unless the 3 previous snapshots were all zero too. Held ones count (store.py:381-399).

"Held" = still saved, with gate_passed false. The run exits 2; the digest lists it under "No data"; peer medians and AM notes skip it. Its flags are still computed and saved. A rejected token (401/403) is "failed", with no snapshot.

## 5. Flags (defaults; accounts can override)

| Code | Fires when | Severity |
|---|---|---|
| INTEGRATION_SUSPECT | 0 leads, 0 active conversations, 0 new deals, forms 0 or unknown; baseline 3+/week | red |
| LEADS_ZERO | 0 leads; baseline 3+/week | red |
| FORM_SILENT | 0 submissions, form baseline 3+/week, leads still arriving | red |
| LEADS_DROP_SEASONAL | delta -40% or worse, peers -20% or worse | amber |
| LEADS_DROP | delta -40% or worse, peers held | red; amber if no peer data |
| SOURCE_DROP | a source averaging 3+/week and 25%+ of volume falls to 40% of normal or less (max 3) | amber; red at 0 |
| UNASSIGNED_LEADS | 3+ ownerless leads, or 30%+ of the week's | amber |
| LEADS_UNREACHABLE | 30%+ without a phone | amber |
| SLOW_RESPONSE | 3+ uncontacted 24h, or 30%+ of 5+ leads got only automatic replies | amber; red at 8+ or 50%+ |
| CONVOS_WAITING | any conversation waiting 4h+ | amber; red if longest 24h+ |
| PIPELINE_HYGIENE | 50+ stale deals and 60%+ of open | amber |
| STALE_PIPELINE | stale at least max(3, 30% of open), or $25k+ stale (not with HYGIENE) | amber; red at $25k+ |
| PIPELINE_FROZEN | 10+ open deals, under 5% moved in 30 days | amber; red at 0 moved |
| NOT_WORKING_IN_MLH | no human reply to this week's leads and 0 moved; silences PIPELINE_FROZEN and the SLOW_RESPONSE ratio | info |
| PIPELINE_BOTTLENECK | one stage holds $10k+ idle | info |
| HIGH_NOSHOW | no-show rate 30%+ | amber |
| NO_DELIVERY | 14+ days since a publish (content/social buyers) | amber; red at 30+ |
| SOCIAL_DISCONNECTED | any expired social account (social buyers) | red |
| NO_CLIENT_TOUCH | last touch 30+ days, nothing booked | amber; red at 45+ |
| RENEWAL_SOON | contract ends within 60 days | info; amber if any red |
| REVIEW_ASK_GAP | 2+ recent wins without the review tag | info |
| FORM_WENT_SILENT / SURVEY_WENT_SILENT | any form / survey silent | amber |
| FORM_CHECK_MISSED | weekly test older than 8 days, or no contact created | amber |
| WORKFLOWS_NONE_PUBLISHED | workflows exist, none published, leads arriving | amber |

## 6. Where the numbers can mislead
- **Problems age out.** Uncontacted and no-human counts only cover leads from the last 7 full days, so an ignored lead stops counting on day 8 (metrics.py:567-576). convos_waiting drops anyone whose last message is older than the 14-day lookback (metrics.py:642). A silent form turns "dormant" after 30 days and is never flagged (metrics.py:202). Numbers can improve with nobody acting.
- **0 is not always measured.** leads_new_7d, convos_waiting, opps_open, calls_missed_7d and leads_uncontacted_24h read 0 when their fetch fails; only coverage records the error (fetchers.py:489-493, 782-785), despite metrics.py:5 promising None. The gate catches it only if 2+ sources fail or everything reads zero. Rates go None below their minimum sample, so small accounts never trip those flags.
- **Caps hold busy accounts.** Speed-to-lead reads only the 100 newest leads from 14 days; the call scan, 30 conversations (main.py:84, 89). Busy accounts get partial uncontacted counts while the SLOW_RESPONSE ratio divides by all leads (flags.py:322-324). Each cap hit marks its source "partial"; two partials trip G3, so a healthy busy account can show "No data". I confirmed this in memory.
- **Speed counts only the answered.** Median and p90 use leads that got a human reply; unanswered leads don't make it worse (metrics.py:580-585).
- **Win rate and days to close** rest on lastStatusChangeAt. A deal created already won counts as won in about 0 days if GHL stamps that date at creation, or is skipped if it's empty; the code can't tell which. Closed-deal paging also stops at the first page with a close older than 90 days, with no sort requested, so it assumes newest-first order (fetchers.py:422-448).
- **Relationship blind spot.** Parent meetings are fetched only 60 days back (main.py:149). With no recent meeting and no conversation, client_last_touch_days is None and NO_CLIENT_TOUCH can't fire: the most neglected client shows "unknown", not red.
