# Owner Follow-Up Reporting — Implementation and GHL Workflow Specification

Version 1.0 • October 2, 2026 • Prepared for Matthew Carlson / Small Screen Producer
Status: implementation proposal and review artifacts. No delivery enabled.

## 1. Outcome and scope

Each participating client owner receives one intelligible weekly lead-handling report for one subaccount. The report explains what happened last week, what needs correction now, and where to act. The embedded GHL dashboard provides the same account-specific facts and weekly/monthly issue history.

Keep nightly collection. Add a separate delivery scheduler and owner-report pipeline. Preserve existing SSP account-manager notes, Monday digest, calculations, staff views and code. Do not repurpose AM delivery counters or silently change AM notification settings.

This proposal supersedes the earlier deferral of owner-email *implementation planning*. It does not itself authorize a production send, a workflow publication, adding GHL users, or broadening their permissions. Matthew approves pilot recipients and activation; agents prepare workflows in Draft.

**Recommended delivery:** one versioned owner workflow in each enrolled GHL subaccount, addressed to one explicitly selected existing GHL user. An owner with only an external email and no appropriate GHL user is not eligible for this route. Do not quietly create a contact, assign an unrelated lead, use CC/BCC as a workaround, or grant broad GHL access. A contact-based or separate email-provider route requires an additional scoped design.

**Explicit policy change to implement:** current repository rules permit notification POSTs only into SSP's account and only staff recipients. Before implementing owner sends, revise those rules to allow the separately gated, account-bound owner-notification destination. Keep the GHL data-reading client read-only and the old staff-only recipient validator unchanged. Owner delivery is a new notification adapter, not a new general GHL write capability.

## 2. Current capabilities and gaps

| Already implemented | Required update |
|---|---|
| Nightly GHL collection; T1/T2/T3/T4/T6/T7 issue logic | Separate owner-safe issue presentation and selection |
| Safe weekly/monthly reports, explicit memberships, admin MFA | Issue summaries, comparable trends, exact historical report links |
| Account publication/access switches and view selection | Weekly schedule, verified owner binding, workflow connection, topic selection, delivery status |
| AM HTML/plain-text composer and SSP webhook | Owner template family and dedicated transport |
| AM notification state and limits | Owner-only outbox, scheduling, deduplication and reconciliation |
| Account-specific embed with no account selector | Weekly/monthly issue-first layout and current-backlog section |

Limitations to preserve visibly: the existing response scan covers 14 days and has a target cap; contact history covers 42 days. Monthly response statistics are often unavailable. Passing a job does not prove full source coverage. Current weekly completion measures outbound activity by cutoff; it is not the percentage contacted within 24 hours.

## 3. Requirements and acceptance map

| ID | Requirement | Acceptance evidence |
|---|---|---|
| O01 | One scheduled summary per account/recipient/week | Concurrent-worker and rerun tests; independent owner send ledger |
| O02 | Tenant isolation on reports, previews, settings and delivery | Two-account client/API tests including guessed IDs |
| O03 | Email and dashboard use the same report version | Matching report ID, period, formulas and numbers |
| O04 | Separate historical results from current queues | Fixtures show distinct labels and timestamps |
| O05 | Unknown values never appear as zero or all-clear | Partial, stale and zero-denominator fixtures |
| O06 | Fixed, verified account-owner routing | Wrong account, user, recipient version and test-mode rejection |
| O07 | Weekly report arrives even when selected topics have no issues | Healthy variant; bounded freshness requirements |
| O08 | Preserve existing trigger calculations and AM behavior | Existing regression suite plus owner-path isolation tests |
| O09 | Useful issue-first weekly/monthly dashboard | Responsive acceptance for 0, 1, 3 and more issues |
| O10 | Safe templates and plain-text equivalents | Escaping/PII tests, local previews and approved inbox proof |
| O11 | Observable pause, failure and retry behavior | Ambiguous delivery and failed-workflow exercises |
| O12 | No unintended recipient or customer-record writes | Adapter allowlist and GHL workflow inspection |
| O13 | Supported timezones, DST and catch-up limits | Clock-driven scheduling fixtures |
| O14 | Secure secret handling and auditable activation | No readback of secrets; admin-MFA mutation tests |
| O15 | Small, reversible pilot rollout | SSP pilot accepted before additional subaccounts |

## 4. Owner content contract

### 4.1 Recommended topics

| Topic | Email/dashboard treatment | Default action and limits |
|---|---|---|
| T2 leads going cold | Weekly aged cohort without outbound activity at cutoff; separate latest backlog count | Assign follow-up and call/text/email as appropriate. Preserve existing pilot trigger threshold of 2 and two distinct nightly confirmations. |
| T3 customers waiting | Latest eligible unanswered conversations and oldest weekend-adjusted wait | Answer oldest conversations first. Preserve 5-conversation trigger threshold, current four-hour qualification/weekend rules, and 24-hour red threshold. |
| Speed to lead | Median plus 90th percentile, sample count, human vs any recorded outbound label | Compare only compatible periods. Describe outbound activity, never imply a successful conversation. |
| Unassigned leads | Count and observed-at timestamp | Confirm who owns follow-up. Supporting measure, not a newly invented T-code. |
| T1 lead flow | New-lead count; prior comparable period; confirmed unusual drop where reliable | Owner reviews with SSP. Never claim a collection failure proves advertising failure. |
| T7 pipeline inactivity | Optional, only for accounts using a reviewed sales pipeline | Review next steps and update opportunities. Existing frozen-pipeline rule is no movement for 30 days; do not relabel it a seven-day rule. |
| T4 forms / T6 social | Excluded from default owner delivery | Remain SSP operational topics. A separate reviewed owner topic may be introduced later. |
| T5 delivery / T8 parked revenue | Not offered as operational owner triggers | Rules/data are not implemented sufficiently; no invented email-delivery or revenue claims. |

Show positive backlog counts even below a trigger's notification threshold, labeled “Follow-up to review,” not a confirmed alert. Trigger confirmation affects the severity badge and recurring-issue history; it must not hide a known count.

Default top-three action ranking: confirmed critical follow-up, confirmed waiting replies, other overdue follow-up, unassigned leads, other waiting replies, reviewed pipeline inactivity, lead-flow review. Preserve stable ordering for ties; surface the full list in the dashboard. Deduplicate closely related issue statements.

Owner wording addresses the owner directly: “Assign the five leads and complete their next follow-up.” Do not reuse AM copy such as “Call the client.” No scores or revenue-at-risk amounts without defined evidence.

### 4.2 Period and formula rules

- Weekly email covers the last completed Monday–Sunday in the account timezone, using an exclusive Monday-midnight end. Default delivery: Monday 08:00 local.
- Weekly dashboard defaults to the last completed week, with a clearly labeled “This week so far” option.
- Monthly dashboard defaults to month-to-date through the last completed local day; allow completed months. Compare month-to-date only with the same elapsed number of days of the previous month, capped to available days and labeled explicitly.
- Weekly totals compare with the prior complete week. A zero prior denominator displays “No comparable percentage,” with absolute values.
- New leads are unique eligible contacts created inside the cohort using current exclusions.
- “Follow-up recorded” = eligible leads at least 24 hours old at cutoff with an outbound event before cutoff, divided by all such eligible leads. Show numerator/denominator. A response on day three still counts here; this is not a 24-hour service-level percentage.
- “No follow-up by period end” = those eligible leads without qualifying outbound before cutoff.
- Speed = median/p90 of underlying qualifying durations, preserving human classification rules. Show sample size and exclude unanswered leads from speed while reporting them separately. Never average daily medians.
- Current waiting, current overdue leads and current ownership are observed queues, separately timestamped. Do not sum daily snapshots or present them as historical weekly/monthly totals.
- Changes in a current queue may reflect window expiry, deletion, exclusion or collection gaps. Say “No longer observed” unless resolution is supported; never infer a lead was contacted from a lower count alone.
- Repeat issues show “Observed on N of M valid checks” and coverage. An issue is recurring in a month if observed in at least two distinct reporting weeks with valid checks. “Still open” requires current qualifying evidence.
- Real-time alerts are outside scope; the latest queue reflects nightly collection.

### 4.3 Data completeness and history

Extend the safe report schema with per-metric availability and reasons, eligible/sample counts, period bounds, observed-at timestamps, comparison eligibility, issue facts, formula version and template version.

Persist sufficient response-event metadata prospectively to cover at least 13 completed weeks plus the current week (100 days is the proposed initial window). Retain only required identifiers/timestamps/classification and coverage metadata in protected tables, not message bodies or customer names. Define retention ownership and privacy review before any new purge; automated deletion is a separate release task.

Do not claim past coverage by merely extending retention. Implement bounded, checkpointed retrieval for eligible uncovered contacts with rate/page budgets, or retain unavailable values. Validate monthly completeness at the event/cohort level. Store immutable published aggregates so source-window expiry does not erase previously complete reports.

## 5. Dashboard specification

Keep staff reporting intact. Update only the client report experience and explicit owner settings.

Order:
1. Account, weekly/monthly period selector, freshness and coverage.
2. One-sentence status: needs attention / no issues found in selected topics / some data unavailable.
3. “What needs correction” — up to three action cards; full list expandable.
4. Compact period scorecard: leads, follow-up recorded, typical response time; p90/sample/comparison as secondary text.
5. “Current follow-up queue” — overdue new leads, waiting replies, unassigned leads, latest-check timestamp.
6. “Patterns to correct” — repeated issue categories, valid observation counts and comparable trends.
7. Report history and definitions.

Each action: plain-language finding, count/window, status text, “Next step,” and safe account-specific GHL destination. No customer identifiers or raw diagnostics. Use account Contacts/Conversations/Opportunities destinations where verified; do not invent filtered deep links.

Add exact report-version support to authenticated URLs, e.g. /client/accounts/{location_id}?view=week&period=YYYY-MM-DD&report={report_id}. Implement server-side account binding. An unknown or unauthorized ID returns a clear error, never silently falls back to a different period. Email links must reopen the sent version and offer a separate latest-report link.

Existing embed=1 remains a display option. An email link grants no access. Hiding topics/views is presentation, not a separate authorization layer.

## 6. Settings screen specification

Create six simple sections per account. Only SSP administrators with current MFA may change routing, schedules, publication or access. Owners may view their subscription and request a change; self-service recipient changes are out of scope.

| Section | Fields and behavior |
|---|---|
| Account | Account identity, timezone, collection status, last successful source check |
| People | Report owner identity, existing GHL user ID/location, verified email, dashboard membership; separate SSP account manager |
| Report | Enabled topics, optional pipeline topic, weekly/monthly views, stale threshold; definitions beside controls |
| Delivery | Off / Preview / Live; weekly day/time; timezone inherited; next due time; global pause banner |
| GHL connection | Workflow identity/version, secret status (“configured”), recipient-binding verification, test status |
| Preview & history | Frozen preview, approved test action, last acceptance/result, next delivery, pause and recovery actions |

Required recipient checks: exact account binding, active verified app identity/membership, active selected GHL user, exact verified GHL email, approved recipient version. Changing owner/email/workflow invalidates verification, pauses live delivery and requires a new test. Do not derive the recipient from a contact owner, email domain alone, or inbound payload.

Optional topic changes need a preview; no configurable trigger thresholds in v1. Preserve existing account-specific values. Changing timezone or schedule affects future unsent periods; previously sent periods retain their original bounds.

Buttons: “Save settings,” “Preview weekly report,” “Prepare test,” “Pause delivery,” and “Enable weekly delivery.” Prepare test is not send. Enable is disabled until every gate is green. A confirmed test send must show account, actual recipient, period and mode; no default CC/BCC.

Plain-language diagnostics:
- “Collection has not completed.”
- “Report publication is off.”
- “Owner access is not ready.”
- “GHL recipient needs verification.”
- “Report data is incomplete.”
- “Accepted by GHL; email delivery not confirmed.”
- “Delivery needs review.”

## 7. Backend and database changes

Proposed new migrations start at 0017 after checking the repository's next unused number. Additive and backward compatible; absence of the new migration keeps owner delivery off.

| Entity | Minimum fields |
|---|---|
| owner_report_settings | location_id PK/FK, enabled topics, weekly day/time, timezone policy, delivery mode default off, owner recipient reference, destination reference, template version, updated_by/at, verification revision |
| owner_report_recipients | id, location_id, app user ID, GHL user ID, verified email, active, verified_at/by, version |
| owner_report_destinations | id, location_id, workflow ID/version, Vault secret reference, verified recipient version, verified_at/by, active |
| owner_report_snapshots | immutable owner report ID, location_id, cohort report ID, current-queue report ID, comparison IDs, selected topics, formula/template versions, safe composed metrics/issues and observation timestamps |
| owner_report_outbox | id, location_id, recipient version, period kind/start/end/timezone, immutable report ID, destination version, mode, due_at, expiry, status, content hash, dispatch/claim timestamps |
| owner_report_attempts | outbox ID, attempt, response class, sanitized reason, GHL execution reference if available, timestamps, operator reconciliation |
| owner_issue_observations | safe issue category, location/date, severity, rule version, valid source status, observed count; no customer entities |

Unique scheduled-message identity: location + stable recipient identity + weekly period bounds/timezone + mode. Recipient edits or workflow versions must NOT create a second automatic message for an already-sent week. Keep original frozen recipient/destination versions on the row. Explicit correction/resend uses a separate approved operation and audit, never a scheduler loophole.

Reports may be versioned; a new report version does not create a second scheduled email. Separate test identities/keys from live.

The emailed report_id identifies the composed owner_report_snapshots record,
not merely the older cohort row. This freezes both the period totals and the
current queue shown in that email. Add an account-authorized RPC to read that
snapshot and retain it with safe report history; later queue changes must not
rewrite an already-sent report. Store rendered previews privately or render them
from this safe snapshot; never publish account-specific preview files publicly.

RLS: clients may read only authorized safe reports and their own safe subscription status. They cannot read raw routes, other recipients, webhook references, outbox contents or privileged logs. Admin mutations require MFA and audit. Server workers alone claim jobs and read the necessary Vault secret. No service credentials in React.

Proposed application modules:
- owner_reports.py: pure owner summary and display values.
- owner_report_templates.py: escaped HTML/plain text from a typed report.
- owner_report_scheduler.py: due-period calculation and outbox enqueue.
- owner_report_delivery.py: destination validation, safe dispatch and reconciliation.
- Separate delivery entry point, e.g. python -m collector.owner_delivery.
- Add focused store/RPC methods, schema types and settings/report components.
- Preserve automation.py NOTIFY_RULES, staff_address() and existing AM state.

Proposed globals: OWNER_REPORTS_MODE=off|preview|live, OWNER_REPORTS_PILOT_LOCATIONS, OWNER_REPORTS_DAILY_CAP (initially 1 for SSP pilot). Per-account live can never override global off/preview. Keep CLIENT_REPORTS as the publication control. Do not use AUTOMATION_WEBHOOKS or DIGEST_* to enable owner sends.

## 8. Scheduler, quality gates and delivery semantics

The nightly collector publishes reports and safe issue observations. A separate dispatcher checks due jobs every 15 minutes; it does not recollect GHL data. Only one dispatcher schedule is active. Database claims prevent overlap if workers collide.

At due time:
1. Recheck global/account mode, active recipient/destination/membership and publication/access gates.
2. Select the last completed week and compatible report/queue versions.
3. Enforce freshness (default 30 hours) and metric coverage.
4. Freeze one envelope with immutable report/version and rendered content.
5. Claim/send according to the idempotency rules below.

Data policy:
- Complete evidence with no selected issues: send the healthy variant.
- Some valid metrics but missing sources: send a limited-data variant containing only trustworthy numbers and no all-clear claim.
- No useful current evidence, publication failure, missing authorized recipient, or stale source: hold; recheck within a 24-hour grace window.
- After grace, mark missed/needs review. Do not silently mail old weeks later. Show the reason to SSP; any separate operations notification needs an approved recipient/channel.
- No recurring monthly email in v1. Monthly dashboard is included; a monthly template is supplied for a later separately enabled cadence.

Daily scheduler retries must not duplicate sends. An HTTP 2xx proves webhook acceptance, not an email arriving. States must distinguish queued, held, dispatching, accepted, claimed, notification_action_recorded, failed, unknown, skipped and paused. Never label delivery “delivered” without appropriate provider evidence.

Do not reuse the AM sender's automatic 5xx retry policy. A timeout or ambiguous response may occur after GHL accepted the request. Without a verified downstream claim gate, mark unknown and require reconciliation; never resend automatically. Confirmed pre-dispatch failures may retry within grace.

### 8.1 Replay protection gate

Implement an atomic, account-bound claim endpoint before owner live rollout. Its purpose is to let only one GHL execution continue for a scheduled envelope.

Proposed endpoints:
- POST /owner-report-claim: accepts delivery ID, expiry, per-dispatch opaque credential and pinned workflow/account identity; checks the stored envelope, mode, recipient/destination revision and pause state; atomically consumes the single-use claim; returns allow_send plus the canonical display fields and completion credential.
- POST /owner-report-result: authenticated, bounded callback recording action outcome against that claim. It cannot create a recipient, resend a report or mark an inbox delivery as proven.

Store credential hashes, never raw secrets in application logs; expire unused claim credentials after the 24-hour delivery window. Return no data for invalid claims. Rate-limit and reject arbitrary IDs/cross-account claims. Workflow credentials exist only in protected execution/configuration surfaces, not email bodies or report URLs.

GHL must demonstrate a supported request/action that branches on the claim response in a contactless execution. Validate this in a synthetic Draft workflow before committing to its exact action implementation. Do not assume the standard outbound Webhook action exposes usable response fields. Custom Code can transform input/output, but HTTP capability and returned-field mapping need live proof.

If that capability cannot be verified, block automated live owner delivery and resolve the adapter design; do not omit the gate to meet a date.

After a claim is consumed, a failure can prevent the email from being sent. Do not automatically release/replay it; reconcile first. The system reduces duplicate risk but cannot promise end-to-end exactly-once email across separate services. Manual re-running a GHL email action bypasses claim checks; document and restrict that operation.

## 9. Webhook contract

Use a new event owner_followup_weekly and schema_version 1. Keep all GHL boundary values flat strings; typed numbers/booleans live in the internal schema. No lead names, phones, emails, message bodies, deal names or contact IDs. Owner recipient email is stored in the routing record and fixed in GHL, not accepted from the message.

Transport fields: event, schema_version, mode (mapping|test|live), delivery_id, location_id, workflow_binding, recipient_binding_version, period_start, period_end, timezone, report_id, template_version, claim_expires_at, claim_credential. Credentials must be injected at dispatch only and redacted everywhere.

Canonical claim-response content:
- allow_send, report_state (attention|healthy|limited), subject, preheader.
- account_name, greeting, period_label, current_as_of, period_as_of, headline, introduction.
- metric_1_label/value/detail through metric_3_label/value/detail.
- current_queue_text.
- action_1_title/detail/next_step through action_3_title/detail/next_step; absent actions omitted by renderer.
- more_issues_text, improvement_text, data_note, report_url, footer_text.
- body_html and body_text generated from exactly those fields; body_html is optional on the GHL delivery path until tested.

The complete display-token inventory (including scorecard titles, period notes,
CTA labels and report reference) is in owner-report-email/README.md. The renderer
adds these deterministic fields from the same snapshot. CSS colors and optional
HTML slots are trusted renderer outputs, never arbitrary inbound values.

Sample mapping requests contain synthetic content only, mode=mapping, no live claim credential and cannot pass the send gate. Test credentials are bound to the SSP test workflow and approved test user; live credentials cannot be used there.

All dynamic text is escaped; URLs are constructed on the server from allowlisted dashboard/GHL origins. Reject credential-bearing URLs, newline/header injection, executable schemes and untrusted HTML. Enforce field and total-size limits (initial proposal: subject 150 characters, text fields 2,000, HTML 60 KB, total request 96 KB), then validate against actual GHL limits.

## 10. GHL workflow specifications

Two new workflow definitions are required. The production owner workflow is cloned/configured per enrolled subaccount. The existing MC Account Health – Alerts remains separate.

### WF-01: SSP — Owner Report Template QA — v1

Location: SSP only. Purpose: mapping, formatting, and explicitly approved tests to Matthew. Initially Draft.

Steps:
1. Inbound Webhook; load a synthetic mapping sample. Do not include contact email/phone. Remove any auto-added Create/Update Contact step.
2. Validate event, schema version, SSP test binding and mode.
3. mode=mapping: end with no notification. Unknown/missing values: end.
4. mode=test: require a valid test-only claim; reject live claims. Branch only on allow_send=true.
5. Render/select attention, healthy or limited template from canonical returned fields.
6. Internal Notification → Email → Matthew only. The SSP QA Draft uses a fixed Custom email value mcarlson@smallscreenproducer.com; verify it against the application binding before sending. Production uses one verified Particular User. No Assigned User, All Users, roles, CC or BCC.
7. Record action outcome through the bounded callback, if supported and validated.
8. End. No contacts, tags, tasks, opportunity changes or recurring wait loops.

The approved email target is mcarlson@smallscreenproducer.com. Verify the selected GHL user's actual configured email before each authorized test. A prior approval for a historical test is not approval to send this new template.

### WF-02: Account Health — Weekly Owner Report — v1

Location: each enrolled client's GHL subaccount. Initially Draft; Matthew or an authorized operator publishes after acceptance.

Steps:
1. Inbound Webhook; accept only owner_followup_weekly, version 1, mode=test or mode=live and this exact location/workflow binding. A test must be specifically approved and claimed in test mode on this same production binding before live activation; QA acceptance cannot transfer to a clone.
2. Reject mapping/missing/unsupported events without a send. Preserve the envelope mode in the claim request; the database credential is bound to that mode. Use no contact creation/search/assignment actions.
3. Claim the delivery using the approved adapter. Fail closed on timeout, invalid/expired credential, pause, recipient mismatch or allow_send=false.
4. Use only canonical claim-response fields; ignore untrusted input message bodies or recipient values.
5. Branch report_state into attention / healthy / limited. Unsupported value: end.
6. Internal Notification → Email → Particular Users → one explicitly selected, verified owner user in this account.
7. Sender name: “SSP Account Health”; sender address and reply handling: an already approved, authenticated account configuration. Confirm reply ownership; do not invent a sender address.
8. Subject and message from the corresponding versioned template. No CC/BCC, SMS or automatic follow-up series.
9. Record notification-action outcome if possible. An executed step is not proof of inbox delivery.
10. End. No weekly scheduling or delay loop inside GHL; the app controls cadence.

Document workflow ID/version, account, selected user ID/email, template revision and verified claim mapping in the health-app connection record. Duplicating the workflow must require rebinding; copied credentials/recipient selections must not silently work in another account.

GHL internal notifications use selected account users and can operate contactlessly; an Assigned User path may skip without a contact. This is why user verification is a prerequisite. [Official recipient behavior](https://help.gohighlevel.com/support/solutions/articles/155000003202)

Inbound sample mapping and custom values are supported; removing contact-dependent actions enables a contactless path. Validate each mapping in the actual workflow. [Official inbound webhook guide](https://help.gohighlevel.com/support/solutions/articles/48001237383-how-to-use-the-inbound-webhook-workflow-premium-trigger)

### WF-03: Monthly owner email — deferred

No additional live workflow required for v1. The supplied monthly variant can later reuse WF-02's structure with a distinct event, period identity, schedule and activation gate. Do not start monthly emails merely because the dashboard has a monthly tab.

### Workflow publishing and policy boundary

Configuring approved owner notification workflows in client accounts is a new, narrow exception to the previous SSP-only workflow rule. It does not permit modifying lead workflows or records. Record the revised policy and account-specific authorization before live setup. Keep all workflow edits Draft and have Matthew publish. This specification work sends nothing.

## 11. Email design and templates

### 11.1 Shared design

Match the existing AM report family: Arial/Helvetica; dark ink #1d2b32; muted text #5b6a70; page #eef2f1; blue CTA #2a78d6; subtle borders #e3eae9. Use amber for attention, green only for supported healthy status, and neutral blue/gray for unavailable data.

Use a centered 600px maximum single column, nested presentation tables, inline CSS, readable 16px body, generous spacing and mobile stacking. No external fonts, scripts, tracking pixels, charts requiring images, or image-only text. Status always has words, not color alone. One primary report button; definitions and assistance below. No lead PII or broad portfolio links.

Fixed reading order:
1. SSP Account Health / account / reporting period.
2. Status headline and one-sentence interpretation.
3. Three period metrics with denominators/qualifiers.
4. Separately dated current queue.
5. Up to three numbered corrective actions, each with finding and next step.
6. Optional verified improvement; never fabricated to fill space.
7. Data quality note.
8. Account-specific report button.
9. Assistance, cadence explanation and report reference.

Email is an immutable snapshot. Its primary link opens the exact authorized report version; dashboard offers latest separately.

### 11.2 Template family and exact copy rules

| Template | Subject | Headline and behavior |
|---|---|---|
| OWNER-WEEKLY-ATTENTION-v1 | {Account}: weekly lead follow-up — {N} priorities | “Your team's next steps”; show up to three actions; total issue count may exceed displayed actions |
| OWNER-WEEKLY-HEALTHY-v1 | {Account}: weekly lead follow-up — no issues found | “No follow-up issues found in the selected topics”; only if all required sources are complete and no selected corrective items exist |
| OWNER-WEEKLY-LIMITED-v1 | {Account}: weekly lead follow-up — some data unavailable | “Some results need another check”; show trustworthy values, unavailable reasons and any independently valid actions |
| OWNER-MONTHLY-REVIEW-v1 | {Account}: {Month} lead follow-up review | “Patterns to improve”; recurring categories, period metrics and priorities; no delivery schedule in v1 |
| OWNER-TEST-v1 | [TEST — SAMPLE DATA] {normal subject} | Prominent sample-data banner, synthetic account, test-only destination |

Preheader: one short fact plus the action, e.g. “5 leads need follow-up; 7 conversations are waiting. Start with the oldest.” It must describe the separately dated current queue, not pretend those are weekly totals.

Greeting: “Hello {verified owner first name},” or “Hello,” when absent. Do not use a lead/contact merge field.

Attention action patterns:
- “5 recent leads still need follow-up.” Next step: “Assign these leads and record the next call, text or email in GHL.”
- “7 conversations are waiting for a reply.” Next step: “Start with the oldest unanswered messages; close conversations that no longer need a reply.”
- “2 leads have no assigned owner.” Next step: “Assign a team member so each lead has a clear next action.”

Healthy next step: “Keep your team's follow-up routine in place and check the dashboard for new activity.” Do not claim every lead converted or every customer was reached.

Limited next step: “Review the available follow-up items below. SSP needs to review the missing data before you use the unavailable figures to judge performance.”

Footer: “You receive this weekly summary as the designated report owner. Contact your SSP account manager to change the recipient or pause reports. The dashboard requires sign-in.” No fake unsubscribe button. A future pause endpoint must be authenticated or narrowly signed and must not change account access.

### 11.3 Template assets and GHL mapping procedure

Review assets are in owner-report-email/:
- owner-weekly-template.html: portable design-source template using %%field%% placeholders.
- owner-weekly-template.txt: equivalent plain-text source.
- preview-attention.html, preview-healthy.html, preview-limited.html: synthetic rendered examples.
- preview-monthly.html: optional monthly design example, not an enabled send.
- README.md: token mapping and review instructions.

These placeholders are NOT GHL merge syntax. During workflow setup, load the mapping fixture and insert each exact field using GHL's custom-value picker. Keep a field-to-picker mapping record. Do not guess a {{...}} path.

Preferred import: use the approved static template with mapped scalar values. Render optional actions in the backend and prove whether trusted body_html is rendered or escaped by the chosen GHL action. If supported, use the single generated fragment. Otherwise use fixed template branches for 0/1/2/3 actions with scalar mappings; do not insert a serialized HTML string into a rich-text field and assume it renders.

Use the plain-text body as a tested alternate, not as a claim that GHL automatically creates MIME multipart alternatives. Preview all three states in GHL and actual approved inboxes before release.

GHL offers custom HTML template tooling, but email-client compatibility needs testing. [Email code element](https://help.gohighlevel.com/support/solutions/articles/155000006827-code-element-in-email-builder)
Saved templates may be copied into workflow steps; inspect/reapply version changes in every deployed workflow. [Template update behavior](https://help.gohighlevel.com/support/solutions/articles/155000007942-email-template-update-api)

## 12. Per-subaccount onboarding checklist

1. Confirm active collection, timezone and a populated safe report.
2. Identify the designated owner and verify existing GHL user/account/email.
3. Grant only that account's client-owner membership; verify sign-in.
4. Enable safe publication; enable client access only after isolation testing.
5. Choose topics; leave pipeline optional and technical SSP topics off.
6. Save weekly day/time, preview mode and reviewed sender/reply configuration.
7. Create/rebind WF-02 in the correct account, still Draft; store its URL in the approved secret store through a write-only admin operation.
8. Bind exact recipient and versions; verify contactless mapping and replay gate.
9. Preview period/queue values and email variants; obtain approval for the named test send.
10. Complete the approved test, verify recipient/rendered body/report link, and record evidence.
11. Matthew publishes; administrator enables this account's live weekly delivery within the global pilot allowlist.
12. Observe the first scheduled result, then expand accounts individually.

Account changes do not touch the old AM send settings. An owner belongs to one subaccount by default; multiple explicit memberships do not justify portfolio links in email.

## 13. Testing, release gates and rollback

### Automated tests
- Formula fixtures: Monday boundaries, DST, 24-hour eligibility, delayed first outbound, empty cohorts, zero baseline, human/automation differences, insufficient monthly history.
- Queue/history fixtures: current vs cutoff, expiry vs actual resolution, partial source days, stable priority ordering.
- Privacy: lead-name/email/phone/message/deal canaries across HTML, text, JSON, logs and callbacks.
- Security: unverified owner, disabled account, cross-account IDs, guessed report/version, client settings mutation, secret readback, invalid destination/redirect, SSRF and escaped content.
- Delivery: concurrent claims, retries, missed schedules, global pause, account pause after enqueue, recipient changes, stale reports, mode mismatch, expired credentials, callback replay and ambiguous responses.
- Rendering: long names, long actions, missing greeting, 0/1/3/many issues, special characters, all template states.
- Regression: full existing Python/web suites, production build and expanded database RLS suite.

### Live acceptance
Use SSP first with Matthew as the explicitly approved test recipient. Confirm the actual GHL user's email. Test malformed/mapping events with send paths disabled. Prove claim response mapping and contactless template behavior before publishing.

Approved inbox checks: Gmail desktop/mobile and Outlook; verify widths, dates, text contrast, links and no raw HTML/placeholders. Browser previews alone are not email compatibility evidence. For another recipient's mailbox, obtain authorization; do not send a test merely to broaden client coverage.

Verify authenticated GHL embed in Chrome and Safari, with wrong-account and expired-session behavior. Inspect sender/reply configuration and platform usage costs before rollout. Customer data must not be sent to public webhook-inspection services.

Release order:
1. Schema/report contract and offline renderer.
2. Dashboard/settings with all delivery off.
3. Outbox/dispatcher/claim endpoint in preview mode.
4. SSP Draft QA workflow and approved test.
5. One live SSP weekly schedule, accepted by Matthew.
6. First client owner through full onboarding; expand gradually.

Current no-report pilot diagnosis must be closed first: confirm an actual collection and published SSP report, not just a successful build.

Rollback: set OWNER_REPORTS_MODE=off; pause destinations and pending jobs; disable the affected owner workflow. Retain report history, grants and send evidence. Leave existing AM paths unchanged. Never “retry all” after recovery; reconcile unknown/claimed executions individually.

## 14. Open decisions and implementation deliverables

Proposed defaults for review: weekly Monday 08:00 account-local; one owner; no CC/BCC; send healthy summaries; current queue plus last complete week; no monthly email; one SSP pilot account; 30-hour freshness and 24-hour delivery grace.

Before live implementation confirm:
- Each owner is an appropriate existing GHL user, or scope a separate external-recipient design.
- Sender/reply address for each subaccount and who manages pauses.
- Approval of the narrow client-workflow notification exception.
- Supported GHL claim-response branching and rendering mode.
- Prospective history retention and operational monitoring ownership.

Deliverables: migrations and protected RPCs/functions; owner report schema and formulas; tested templates; scheduler/outbox/adapter; settings and dashboard updates; versioned Draft GHL templates/workflows; mapping and acceptance evidence; revised runbook/policies. No hosting migration is required for this feature. The worker and endpoints should remain portable to the proposed SSP hosting architecture.

## 15. Sources and evidence boundaries

Implementation baseline: collector/automation.py, collector/client_reports.py, web/src/pages/AccessSettings.tsx, web/src/pages/ClientDashboard.tsx, migration 0016, and existing rollout/security documents.

GHL references above were checked October 2, 2026. They establish available building blocks; the exact contactless claim adapter, HTML merge behavior, selected-user delivery and cloned-workflow configuration remain live acceptance requirements. Custom Code's input/output contract is documented [here](https://help.gohighlevel.com/support/solutions/articles/155000003362-workflow-action-custom-code); the proposed HTTP integration is not claimed tested.

Preparation changed documentation and local preview assets only. No email, webhook, user grant, production setting or GHL workflow was changed.
