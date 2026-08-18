# VERIFICATION.md — GHL API field verification log

Spec rule (v3): every field or endpoint shape marked VERIFY came from
documentation and was not live-tested; `python -m collector.main --probe
--location <id|slug>` calls each endpoint once, prints redacted samples
(phones, emails, message bodies, and the Authorization header are never
shown), and **appends its report to this file**. Fix `collector/fetchers.py`
/ `collector/metrics.py` field paths from that output, rerun `--probe`, then
proceed to the first live run.

## Status at build time (no live GHL access in the build environment)

The build environment held no PIT, so nothing below is live-confirmed yet.
Every fetcher parses tolerantly (alternate envelope keys, ISO/epoch
timestamps) and degrades to a coverage note instead of failing the run, so a
wrong field name costs a column, not the night's data.

| Endpoint | Implemented as (spec section 3) | Status |
|---|---|---|
| `GET /locations/{id}` | `location{...}` or top-level; `id`, `name`, `timezone` | UNVERIFIED |
| `GET /users/` | `users[]`; id → display name only (emails never stored) | UNVERIFIED |
| `GET /opportunities/pipelines` | `pipelines[{id,name,stages[{id,name}]}]` | UNVERIFIED |
| `GET /opportunities/search` | snake `location_id`; per-status queries (`open` all pages; `won`/`lost` newest-first stopped at the 90d cutoff); `getTasks`/`getCalendarEvents`; timestamps `lastStatusChangeAt`, `lastStageChangeAt`, `lastActionDate` | UNVERIFIED — timestamp names, tasks/events population, and whether `date`/`endDate` filter on `lastStatusChangeAt` (probe issues a date-param test call) |
| `POST /contacts/search` | range filter on `dateAdded`; `attributionSource{sessionSource,utmSource}` used as source fallback; `phone`/`email` used for presence checks only and dropped at the fetch boundary | UNVERIFIED — filter grammar and `attributionSource` shape |
| `POST /contacts/search` (tag) | `filters[{field:tags,operator:eq,value}]` | UNVERIFIED |
| `GET /forms/submissions` | `locationId, startAt/endAt (YYYY-MM-DD), limit, page` → `submissions[{id,formId,contactId,createdAt}]` | UNVERIFIED — whole envelope is a spec assumption; metrics degrade to null when unavailable |
| `GET /conversations/search` | `conversations[]`; `lastMessageDate` (ms), `lastMessageDirection`, `startAfterDate` cursor; `lastMessageBody` dropped at the boundary | UNVERIFIED — cursor param name |
| `GET /conversations/{id}/messages` | nested `messages.messages[]` (flat also handled); `direction`, `dateAdded`, `source`, `userId`; `body` dropped at the boundary | UNVERIFIED — the `source` vocabulary decides human-vs-automation; if it can't be established, kinds stay `unknown` and the UI labels the metric "automation included" |
| `GET /calendars/` | `calendars[{id,name}]` | UNVERIFIED |
| `GET /calendars/events` | needs `calendarId`; `startTime`/`endTime` epoch ms; `appointmentStatus` (expected `new, confirmed, showed, noshow, cancelled, invalid`); created field `dateAdded` → `createdAt` fallback | UNVERIFIED — status vocabulary and created-field name drive the appointment metrics |
| `GET /invoices/` | `altId`/`altType=location`, offset paging; `status`, `dueDate`, `amountDue`→`total` fallback, `contactDetails.id` | UNVERIFIED — live status vocabulary |
| `GET /blogs/site/all` | `data[]` (also `sites`/`blogs` keys handled) | UNVERIFIED |
| `GET /blogs/posts/all` | `blogs[]`/`posts[]`; `publishedAt` | UNVERIFIED |
| `POST /social-media-posting/{loc}/posts/list` | `results.posts[]` or flat `posts[]`; `publishedAt`→`createdAt` fallback | UNVERIFIED |
| `GET /social-media-posting/{loc}/accounts` | `results.accounts[]`; expired detection: `isExpired` → `expired` → `status in {expired, disconnected, invalid}` | UNVERIFIED — the disconnected/expired field name is the whole signal |
| `GET /contacts/{id}/tasks` | `tasks[]` (fallback only) | UNVERIFIED |
| Missed calls (Tier 2) | message-level `TYPE_CALL` with `meta.call.status` (fallback `meta.callStatus`); missed vocabulary assumed `no-answer / noanswer / no_answer / missed / busy / failed / voicemail` | UNVERIFIED — update `MISSED_CALL_STATUSES` in `collector/metrics.py` from probe output |

Deep links (spec section 3): contact link is high-confidence; the
conversation and opportunity URL shapes are VERIFY — both fall back to the
contact link when ids are missing (`metrics.link_conversation` /
`link_opportunity`); if the URL *shape* is wrong, fix those two functions.

Assumed rate limit ~100 req/10 s per location; client runs at 8 req/s with
retry/backoff and halves on repeated 429 — verify against live
`X-RateLimit-*` headers during the first full run.

## Deviations from spec (build-time)

1. **Reference implementation unavailable.** `C:\Users\matth\Desktop\SSP\GHL\ghl-am-brief\ghl_am_brief.py`
   is on Matthew's local machine; the remote build environment could not
   reach it (Drive was searched — not there either). The Coverage tracker,
   retrying client, weekend wait rule, whole-word exclusion matcher, gate
   logic, and mock harness were implemented from the spec text; the spec's
   7.6 test list passes (56 tests, including `test_pii.py`). See
   `collector/reference/README.md` for how to add the file for future
   reconciliation.
2. **Repository**: built in `matthewcarlsonhome-cmd/GHLReports` on branch
   `claude/gohighlevel-reports-build-l6hlc7` (the session's designated
   branch) instead of creating `smallscreenproducer/ghl-health`.
3. **Peer median band → its own chart panel.** Peer median is a percentage;
   the weekly leads chart is a count. Rendering the band on the leads chart
   would require a second y-axis (a forbidden dual-axis chart), so the
   drilldown shows a separate "Delta vs baseline (%)" panel (account line +
   peer-median line + zero reference) next to the stacked-by-source chart.
4. **Peer pass uses gate-passed snapshots only.** Deltas from held snapshots
   are untrusted by definition (they are usually null anyway), so held rows
   are excluded from the peer median.
5. **Earliest-contact probe for `trailing_n`.** The live baseline needs the
   account's first contact date; the collector issues one extra
   `contacts/search` (pageLimit 1, sorted ascending). If that call fails,
   the account is assumed older than the baseline window (trailing_n = 4).
6. **`lead_to_opp_28d_pct` cohort fetch.** The cohort (contacts created
   14–42 days ago) is covered by widening the single history fetch to 42
   days rather than issuing a separate range query.

## `--probe` checklist (from spec 7.4 v3 — work through this on first live run)

- [ ] contacts/search range filter accepted (HTTP 200, filtered results)?
- [ ] `attributionSource` present on contacts?
- [ ] opportunity timestamp fields present (`lastStatusChangeAt`, `lastStageChangeAt`, `lastActionDate`)?
- [ ] `tasks` / `calendarEvents` populated on opportunities/search?
- [ ] do `date`/`endDate` filter opportunities on `lastStatusChangeAt`?
- [ ] message `source` / `userId` values seen (list them; update the
      HUMAN_SOURCES / AUTOMATION_SOURCES sets in `collector/metrics.py`)?
- [ ] invoice `status` values seen?
- [ ] forms/submissions envelope as parsed?
- [ ] calendar event created field (`dateAdded` vs `createdAt`) and
      `appointmentStatus` values (update SHOWED/NOSHOW sets in metrics.py)?
- [ ] blog / social envelope keys as parsed?
- [ ] social accounts expired/disconnected field name?
- [ ] call messages: `meta.call.status` present, and which values mean "nobody answered"?
- [ ] deep links: conversation and opportunity URL shapes open the right records?
- [ ] no token, phone, email, or message body anywhere in probe output?

---

Probe runs append below this line.
