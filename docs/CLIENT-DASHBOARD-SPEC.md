# Account Health Client Access and Follow Up Specification

Version 1.1 • October 1, 2026 • Prepared for Matthew Carlson and Small Screen Producer

Status: Requirements review. Approval of this document establishes the implementation scope; deployment and client access require the release gates below. Customer report and alert emails are deferred.

## 1 Purpose and recommendation

Extend the existing Account Health application into a secure, account-specific follow-up reporting service. Matthew must see the full portfolio. Each client team owner must see only reports for accounts explicitly assigned to that person. SSP account managers must retain an effective portfolio and escalation view. Clients should understand what needs attention today, how consistently their teams followed up this week, and whether performance improved this month.

Keep Python as the calculation engine, Supabase as the protected data store, and the existing React application as the reporting interface. Embed an authenticated client view in a GoHighLevel dashboard. Add explicit account permissions, sanitized client reports, and a clean dashboard layout. Customer report emails, alert emails, recipient subscriptions, and new delivery infrastructure are outside this release. Looker Studio is an optional later reporting surface; it does not replace calculation, permissions, or notification processing.

This specification preserves existing code and useful reporting. It does not authorize deleting functionality, changing metric formulas without review, sending client emails, creating client GHL contacts, or publishing workflows. Implementation should be additive, with new client features disabled until verified. The October 1 scope decision prioritizes reports inside the GHL sub-account dashboard. Existing SSP account-manager alerts remain in scope for preservation, without changing their live settings. Client acknowledgment controls are deferred to keep the first dashboard release read-only. Authentication invitations or one-time login codes, if needed, are access messages rather than scheduled customer reporting; their actual use must be agreed during onboarding.

## 2 Existing baseline and gaps

The implementation baseline is the GHLReports repository on branch `claude/gohighlevel-reports-build-l6hlc7`, last recorded commit `a6a4a13`. Operational settings below reflect the September 28 handoff and must be rechecked before implementation or activation. They are not a fresh production audit.

### Current application

- Python collects GHL data, calculates account metrics, and stores results in Supabase. The React application is hosted on Netlify.
- Login uses Supabase email authentication. The existing database rules recognize SSP staff by email domain; staff can read portfolio data across accounts. An authentication trigger rejects non-SSP users. There is no explicit client membership model.
- Existing iframe support simplifies navigation and accepts an email hint. It does not provide client authorization or trusted GHL single sign-on.
- Existing report data includes internal fields and potentially identifying lead details. Allowing a client to read an account row would not, by itself, hide the internal fields in that row.
- Speed to lead and follow-up reporting should remain available. Existing acknowledgments can snooze an account issue for 7, 14, or 30 days. The first client dashboard must not expose those acknowledgment or snooze controls.

### Current notification pilot

The last recorded pilot had six accounts: Flohr Pools and Central Jersey Pool & Spas assigned to Lauren; Pettis Pools & Patio, Liverpool Pool & Spa, McKinney Custom Pools, and AAA Spa & Pool Services assigned provisionally to Lisa. Michael was excluded. Verify assignments before use.

The collector was scheduled at 10:30 UTC daily, approximately 5:30 a.m. Central daylight time. This is a fixed UTC schedule and shifts relative to local time when daylight saving changes. Automation webhooks were in dry mode, with Lauren and Lisa allowed and delivery redirected to Matthew. Weekly digest delivery was disabled. No temporary collector arguments remained after testing.

Only two trigger categories were enabled: T2, at least two leads created within seven days with no outbound call, text, or email after 24 hours; and T3, at least five inbound conversations waiting four hours with weekend adjustment, with red severity at an oldest wait of 24 hours. Both require two consecutive nightly checks. Preserve the exact implemented counting and exclusion logic unless a separate change is approved.

Existing safeguards include a silent initial seed, daily limits of one notice per account and eight per account manager, a circuit breaker above 25 candidates, and seven-day reminders capped at two. Worsening severity or sufficient count growth can escalate an issue. Forms, speed to lead, lead volume, and unassigned leads are reporting context, not additional pilot triggers.

The SSP workflow was last left in Draft. One explicitly approved, synthetic test email to Matthew was successfully delivered and visually checked on September 28. Normal validation events terminate without email; the separate delivery-test path is tightly restricted. The previous test approval does not authorize future sends. Three to five nightly dry rehearsals and verification of the in-app fallback remained release checks.

### Gaps this project must close

The existing staff-only pilot is not ready for client access. It needs explicit account memberships, client-safe data exposure, weekly and monthly report periods, reliable GHL embedding, access auditing, and proof that every access path enforces isolation. Nightly collection also cannot support a promise of immediate alerts during the working day.

## 3 Required outcomes

The following requirements define completion. Details labeled “proposed” elsewhere in this document remain review decisions, not approved operating settings.

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| R01 | Preserve existing code, established metric logic, SSP reporting, and speed to lead | Regression checks and reviewed change list |
| R02 | Matthew can view and administer all authorized accounts | Verified administrator identity and portfolio test |
| R03 | Client users can access only explicitly assigned accounts | Cross-account UI, API, database, and export tests |
| R04 | Use invited, verified identities with revocable access | Invitation, expiry, revocation, and session tests |
| R05 | Provide a secure GHL dashboard view and a standalone fallback | Real browser tests with independent authorization |
| R06 | Provide daily action, weekly follow-up, and monthly review views | Approved report definitions and period fixtures |
| R07 | Keep speed to lead and supporting follow-up metrics understandable | Human versus automated labels and coverage checks |
| R08 | Defer customer report and alert email delivery | No customer subscriptions, new email jobs, or customer workflow sends enabled |
| R09 | Preserve existing SSP oversight and alert behavior | SSP routing, trigger, acknowledgment, and escalation regression checks |
| R10 | Make dashboard issues explain the impact and next action | Reviewed dashboard examples at desktop and mobile sizes |
| R11 | Audit permissions, reports, and failures while preserving SSP audit behavior | Searchable audit records without secrets or lead PII |
| R12 | Default new client access off and support safe rollback | Feature controls and a rehearsed rollback |
| R13 | Keep calculation independent of the presentation tool | Versioned report data contract; optional Looker adapter |
| R14 | Never present missing data as healthy or current | Staleness, coverage, and failed-collection tests |

## 4 Roles and access rules

Use explicit identities and memberships. An SSP email address alone must no longer grant portfolio access. A GHL user ID, iframe parameter, report URL, or account name is not proof of authorization.

| Role | Read access | Allowed actions |
| --- | --- | --- |
| SSP administrator | All active accounts and internal reports | Manage memberships, owners, report settings, and audit records |
| SSP account manager | Assigned accounts by proposed default | Review internal reports, add internal notes, use existing SSP acknowledgments |
| Client owner | Assigned account client reports only | Read reports and follow approved GHL links; no alert or permission changes |
| Client viewer | Assigned account client reports only | Read reports; no permissions or shared alert changes |
| Collector service | Required ingestion and reporting resources | Background processing only; never a browser identity |

Matthew is the initial administrator, established using a verified authentication user UUID. Whether SSP account managers should see all accounts or only assigned accounts is decision D02. Do not restrict their current access before the assignment roster is reviewed.

A person may belong to multiple accounts, but each membership must be explicit. The account selector lists only those memberships. Client owners cannot invite colleagues, change account ownership, or grant roles in the first release; SSP administrators do that. Customer notification recipients and subscriptions are deferred; approved portal access does not enroll anyone in report or alert emails.

All account-bound requests must check active membership at request time. Removing access must deny the next protected request even if a JWT has not expired. Clear account data caches on logout, identity change, and membership refresh. Previously viewed or downloaded content cannot be recalled; minimize downloadable identifying data accordingly.

## 5 System design

Use this processing sequence:

1. The existing collector reads GHL and records run status and source coverage.
2. Existing calculations produce internal snapshots and issue state.
3. A reporting step produces a versioned, client-safe projection for each account and period.
4. The authenticated application reads only the data permitted for the current user.
5. The GHL dashboard embeds the authenticated report for the intended account.
6. Existing SSP notification processing continues under its current approved controls; this release adds no customer delivery path.

The client projection must use an allowlist of fields. Do not copy entire snapshot JSON, internal notes, raw lead events, credentials, or provider responses. Retain existing internal tables for SSP use. If identifiable lead lists become necessary later, review their fields and permissions separately; the initial client portal uses aggregate reporting and approved GHL links.

Prefer one reusable reporting contract for the client interface, weekly and monthly dashboard views, and a future Looker source. Include account ID, period start and end, account timezone, generation time, data-through time, source coverage, formula version, report version, metric values, denominator counts, issue summaries, and comparison eligibility. A successful collector run alone is not evidence that every source was complete.

## 6 Data model and permission implementation

Names below are proposed and may be adapted to repository conventions. Every account-bound table needs a non-null account identifier, foreign keys, suitable indexes, and integrity checks that prevent references crossing accounts.

| Record | Essential fields and purpose |
| --- | --- |
| User profile and admin grant | Authentication UUID, active status, verified administrator grant; no user-editable role source |
| Account membership | Account, user UUID, role, active status, grantor, creation and revocation timestamps |
| Invitation | Account, intended role, normalized email, expiry, one-time token hash, status, inviter |
| Client report | Account, period type and boundaries, timezone, version, coverage, safe metrics, publication status |
| Issue state | Existing issue identity plus observation times, severity, count, coverage, resolution evidence |
| Audit event | Actor, account, action, target, timestamp, outcome, safe before and after values, correlation ID |

Use unique constraints for memberships and report identities. Prevent any issue or report reference from crossing accounts. Report corrections create a new version while retaining a traceable relationship to the original. Do not build client subscriptions, client acknowledgment state, or a client delivery outbox in this release; preserve existing SSP notification records and code.

### Authentication migration

Implement invite-only onboarding through a trusted server endpoint. Verify the authenticated administrator before accepting an invitation request. Normalize and validate the intended address. Bind acceptance to the verified identity for that exact address, assigned account, intended role, and unexpired one-time invitation. Reject forwarded, replayed, expired, or account-modified invitations.

The current authentication trigger rejects external domains. Replace that restriction only when the new invitation and membership policies are ready. Creating an authentication user must never itself grant account access. Retain non-enumerating login responses, rate limits, approved redirect destinations, and existing session handling where appropriate. Require MFA for administrators as a proposed release requirement; verify the chosen Supabase configuration supports the intended flow.

### Database enforcement

Inventory every existing policy, grant, view, function, and storage rule. Replace broad staff-domain access with explicit administrator or membership checks. PostgreSQL permissive policies can combine with OR, so merely adding narrower policies alongside old broad policies does not restrict access.

Enable and enforce row-level security on account-bound resources. Clients must not read internal snapshots, raw lead details, internal notes, collector runs, or internal delivery logs. Use separate safe tables or carefully secured projections; row isolation does not provide field isolation. Use invoker-security views where appropriate and verify underlying grants. Do not grant direct access to a source table merely to make a client view work.

If a narrowly scoped security-definer function is necessary, use an explicit safe search path, minimal ownership and execute grants, and membership checks that avoid recursive policies. Never trust roles from user-editable metadata. Treat service-role access as a privileged bypass: backend code using it must perform its own account checks, and the key must remain server-side.

Test with real non-admin sessions against the database interface, not only mocked frontend checks. Apply the same isolation to downloads, joined queries, aggregate totals, storage objects, report generation, and any existing SSP notification paths affected by the change. Responses and caches must not mix users or accounts.

## 7 Application behavior and interface

### Client account page

Use three primary views: Needs attention, This week, and This month. Show the account name, selected period, account timezone, and a clear data freshness indicator at the top. A user with one account should land directly on that account. A user with multiple approved accounts gets a short account selector.

Needs attention displays the primary issue in plain language, affected count, oldest relevant age where reliable, observation time, and a concrete next action. Include an authenticated “Open report” or approved “Open in GHL” action. Keep the client view read-only. Show current issue status and observation time; do not show acknowledgment, snooze, or email reminder controls.

This week and This month show a small summary of lead volume, speed to lead, follow-up completion, and outstanding follow-up. Place definitions and deeper breakdowns behind an expandable explanation. Preserve existing detailed SSP reports. Client views must not expose portfolio totals, internal notes, staff assignment discussions, raw diagnostic data, or internal navigation.

Use readable typography, sufficient contrast, mobile layouts, keyboard access, descriptive loading and error states, and accessible labels. Distinguish no activity, incomplete data, stale data, and no permission. Never display a zero or a green healthy badge for an unavailable metric.

### SSP administration

Provide account settings for assigned SSP manager, client owner, other approved viewers, timezone, enabled report views, dashboard URL, and access status. Show the assigned accounts and roles before saving. Include a read-only “Preview client view” that is clearly labeled and audited. Preview must not send notifications or silently impersonate a client for writes.

Show report generation status, source coverage, and the last successful refresh. Preserve existing SSP delivery history where already available; do not build a new customer delivery dashboard. Separate data-collection problems from client follow-up problems so a broken integration does not falsely accuse the client team of failing to act.

### Protected operations

Implement routes or equivalent service functions for: listing authorized accounts; reading an account report and available periods; and administrator-only invitation, revocation, membership, and report configuration changes. Client sessions have no issue-state or notification mutation endpoints. All accept only allowlisted fields and validate account ownership server-side. Derive the actor from the authenticated session, never from a supplied email or user ID.

Return a non-revealing denial for accounts outside the user's scope. Apply CSRF protection where cookie-based writes are used and restrict cross-origin requests. Avoid shared caching of authenticated responses. Exports must be generated after authorization, contain only permitted fields, and use short-lived private access if stored. Omit export capability from the first client release if these protections are not implemented.

## 8 Reporting and metric requirements

Preserve established formulas first. Document their actual behavior from code and fixtures before changing any labels. In particular, distinguish the existing no-outbound threshold from a human-response metric: an automated outbound event may satisfy one definition without demonstrating personal follow-up.

| Metric | Required presentation and calculation safeguards |
| --- | --- |
| New leads | Count and precise cohort window; preserve source filters and deduplication |
| Speed to lead | First qualifying response definition; separate human and automated measures when supported; median and sample size |
| Follow-up completion | Eligible leads with qualifying follow-up divided by eligible leads; show numerator and denominator |
| No outbound follow-up | Eligible lead count beyond the approved threshold; distinguish current backlog from period cohort |
| Inbound waiting | Count and oldest age using the existing conversation and weekend rules |
| Unassigned leads | Report context only, with a clear owner-assignment definition |
| Trend | Equivalent prior period comparison, coverage status, and meaningful sample sizes |

Weekly and monthly reports must specify whether a metric describes leads created in the period, events occurring in the period, or backlog at period close. Do not blend those populations. Leads still within a follow-up allowance must not count as overdue. Define how late-arriving events revise a period and retain the formula version used.

Proposed periods are Monday through Sunday for a week and calendar month for a month, using the account's IANA timezone and half-open boundaries. Store UTC timestamps, calculate local boundaries correctly across daylight saving changes, and display the local reporting period. Current-week and current-month views are explicitly labeled “to date.” Publish completed-period dashboard summaries only after successful finalization. Do not email them.

Compute a weekly or monthly median from the underlying eligible response durations or a proven equivalent aggregate; never average daily medians. Reuse existing raw data only if retention and granularity support the calculation. If a historical metric cannot be reconstructed, mark it unavailable and start accurate collection prospectively. Do not fabricate history from daily summary values.

Track missing, skipped, errored, and incomplete source coverage through calculation, display, and notification eligibility. Block trigger conclusions whose required sources are incomplete. Keep the last valid report available with a stale label. A data outage must not resolve an issue merely because the affected count temporarily disappears.

The initial client release is a daily service based on the nightly collector. Proposed freshness limit: 30 hours before a daily dashboard snapshot is labeled stale, subject to D04. This does not alter existing SSP alert eligibility. More frequent collection is a separate phase requiring API cost and rate-limit review, overlap control, revised freshness targets, and elapsed-time confirmation rules. Do not turn “two nightly checks” into two rapid checks unintentionally.

## 9 Customer email deferral and SSP alerts

Customer report and alert emails are deferred by the October 1 scope decision. Do not implement or enable customer recipient subscriptions, scheduled email digests, reminder emails, customer escalation emails, a transactional sender, or a new customer delivery workflow for this release. Existing code must be retained. Dashboard report access must not automatically enroll a user in any notification.

Keep the existing SSP workflow, staff-only recipient validation, allowlist, redirect, dry mode, trigger thresholds, confirmation rules, reminder limits, and acknowledgment behavior unchanged unless separately approved. Dashboard data changes must pass regression checks against those existing paths. No new customer delivery infrastructure is needed to embed the report.

Authentication invitations and user-requested login codes remain a possible access mechanism, not reporting or marketing messages. Confirm the onboarding method before activating client access; this scope update does not authorize sending invitations or test emails now.

If customer notifications are resumed later, define verified recipients, audience-specific state, safe templates, idempotency, delivery tracking, and separate approval gates then. They are not prerequisites for dashboard release.

## 10 GHL dashboard integration

### Dashboard embedding

GHL supports an Embed element using a URL or iframe. Configure it within the intended sub-account dashboard: open dashboard editing, select Elements, add Embed, and enter the approved application URL. A proposed route is `/client/accounts/{account_id}?embed=1`; the route is an account selection hint and must always enforce the current session's membership.

Use the official supported GHL user and sub-account properties only after checking their actual syntax in that location. Dynamic user email can prefill login, but it cannot authenticate a visitor or grant access. Never put a service key, collector key, webhook URL, or permanent access token in the widget.

Limit the application's frame-ancestors policy to approved GHL and SSP domains. Review existing broad wildcards and test the final domains. GHL dashboard sharing and app authorization are separate controls; both must be configured correctly. Test Chrome and Safari, blocked third-party storage, embedded login, expired sessions, and mobile. Provide “Open secure report in a new tab” if embedded login is blocked. Do not weaken authentication to make iframe login seamless.

### Dashboard presentation and pilot proof

Use a clearly named Account Follow Up report element in the pilot sub-account. Land on that account's report without an SSP portfolio navigation bar. Put Needs attention first, with This week and This month as simple view choices. Keep speed to lead, outstanding follow-up, and data freshness visible without a long introductory panel. Use a period selector to reach completed weeks and months.

Set the embed height and responsive layout after testing in the actual GHL dashboard. Avoid nested horizontal scrolling, clipped menus, and duplicate navigation. Test narrow screens, empty states, long account names, slow loading, expired login, and a user assigned to the wrong account. A standalone view is a fallback; acceptance still requires the report to work usefully inside the intended sub-account dashboard.

Start with one approved sub-account and synthetic or authorized report data. Record the dashboard placement, authorized roles, tested browsers, expected update cadence, and access behavior. No changes to client GHL contacts or customer email workflows are required. Actual dashboard configuration and production access follow the release gates; this specification revision changes no live settings.

## 11 Security and operational requirements

Use HTTPS, verified invitations, least-privilege service credentials, explicit database permissions, and audit records. Frontend assets may contain only the intended public Supabase key, never service-role or collector credentials. Review application bundles, source maps, environment variables, logs, and exception paths for secrets. Do not copy the inbound webhook URL into documentation or screenshots.

Before client launch, recheck repository visibility, exposed credentials, privileged database functions, existing insert grants, and remaining security findings. Confirm whether any credential rotation is necessary from evidence. Retain HTML escaping and safe link generation. If exports are added, prevent spreadsheet formula injection. Review dependencies and deploy a restrictive browser content policy compatible with the tested login and embed flows.

Keep internal notes private. Log access and changes without storing full report payloads or lead-level PII unnecessarily. Proposed retention for review: client aggregate reports 13 months, delivery and security audit metadata 12 months, and minimized raw lead data 90 days if compatible with existing calculations and obligations. D06 must reconcile these values with current retention, contracts, backup retention, and historical reporting needs before any deletion policy is implemented. This project does not authorize deleting existing data or code.

Monitor collection freshness, source coverage, report finalization, embedded loading, authentication failures, and cross-account denials. Preserve existing SSP delivery monitoring. Assign Matthew or a designated SSP operator to operational exceptions. Define an incident procedure to disable client access and report publication, preserve audit evidence, assess affected accounts, and restore only after verification.

## 12 Implementation sequence and code map

### Phase 1 Confirm the baseline

Read repository instructions and the existing security specification. Confirm the current branch, deployed version, migrations, actual database grants, Render settings, workflow state, account assignments, and unresolved dry-run checks. Produce a short baseline record without secrets. Review D01 through D08 with Matthew. Do not infer production state solely from the September 28 handoff.

### Phase 2 Build the access boundary

Add migrations for identities, memberships, invitations, safe client reporting, and audit records. Verify the next migration number rather than assuming it; the reviewed series ended at 0015. Create and verify the initial administrator before replacing broad staff access. Prepare reviewed AM memberships, then replace policies and the authentication-domain trigger in a controlled sequence. Make migration failures fail closed without locking the verified administrator out of recovery.

Implement protected administration and invitation endpoints. Test two synthetic client accounts, separate users, a multi-account user, an unassigned SSP user, an AM, and an administrator. Include direct database access and stale sessions. Gate client access on passing the isolation suite.

### Phase 3 Produce trustworthy client reports

Extract reusable reporting logic without removing current features. Implement the allowlisted report projection and period finalization. Add metric definitions, coverage metadata, freshness rules, and report versions. Confirm that speed-to-lead aggregates can be computed correctly for historical periods. Backfill only where reliable source data exists.

### Phase 4 Add the client experience

Build the client landing page, authorized account selector, three read-only report views, and standalone fallback. Add SSP account access and report settings. Preserve existing detailed reports behind the SSP permissions. Test the actual GHL embed with authenticated and unauthorized users.

### Phase 5 Prove the GHL dashboard experience

Configure the report embed in one approved test sub-account. Verify account selection, sign-in, layout, period navigation, speed to lead, source freshness, and account isolation inside GHL. Test the standalone fallback without accepting it as a substitute for a usable embed. Confirm no customer email jobs are created and existing SSP alert behavior is unchanged.

### Phase 6 Release gradually

Complete the dashboard acceptance matrix, then enable one approved account and its approved viewers. Confirm permissions and sign-in first, report accuracy second, and dashboard usability third. Observe successful refreshes and stale-data behavior before expanding. Document actual production settings and rollback controls. Existing SSP dry-run or delivery checks remain a separate pilot track and do not justify adding customer email work to this release.

| Area | Existing locations to review | Required work |
| --- | --- | --- |
| Database | `supabase/migrations`; existing staff policies and auth trigger | Add memberships and safe client data; replace broad policies |
| Authentication | `web/src/lib/useSession.ts`, `supabase.ts`, `pages/Login.tsx` | Invite-aware access, membership refresh, clear account caches |
| Embed and browser policy | `web/src/lib/embed.ts`, `netlify.toml` | Secure client route, approved frame origins, login fallback |
| Reports | `pages/Account.tsx`, `Portfolio.tsx`, `FollowUp.tsx`; `lib/followUp.ts` | Client report views and preserved coverage safeguards |
| Client writes | `web/src/lib/writes.ts`; new protected server operations | Read-only clients; admin-only membership and report settings |
| Notifications | `collector/automation.py`, `collector/digest.py`; existing message composition | Preserve SSP behavior; no customer delivery additions |
| Operations | Repository handoff, deployment configuration, security specification | Reviewed settings, evidence, recovery procedure |

File names describe the reviewed architecture, not an instruction to bypass current repository conventions. Add focused tests for security boundaries and calculations; avoid tests that merely mirror presentation markup.

## 13 Acceptance and verification matrix

Every release-critical row must have recorded pass evidence. Isolation failures, unsupported metric claims, an unusable embed, secret exposure, or unintended customer email behavior block release. IDs are retained for review traceability; their scope below reflects version 1.1.

| Test | Verification and expected outcome | Requirements |
| --- | --- | --- |
| A01 | Client A attempts Client B URLs, IDs, database filters, joins, and any enabled exports; no unauthorized data leaks | R03 R04 |
| A02 | Tampered iframe email, account ID, role, and user metadata grant no additional access | R03 R05 |
| A03 | Uninvited users, expired invitations, wrong verified email, and replayed invites obtain no membership | R04 |
| A04 | Revoke membership during an active session; next protected request fails and caches clear on refresh | R03 R04 |
| A05 | Matthew sees all accounts; AM and multi-account client see exactly approved memberships | R02 R03 |
| A06 | Client cannot retrieve internal notes, raw details, collector logs, credentials, or staff-only fields | R03 R11 |
| A07 | Client cannot grant roles, change settings, acknowledge issues, or suppress SSP state | R03 R09 |
| A08 | Human and automated response fixtures, exclusions, duplicates, and denominators match approved formulas | R01 R07 |
| A09 | Weekly and monthly medians, daylight saving, and period boundaries are correct | R06 R07 |
| A10 | Failed, partial, skipped, and stale data is clearly labeled and does not appear healthy | R14 |
| A11 | No leads, low samples, no responses, late events, and unavailable history produce honest labels | R06 R07 R14 |
| A12 | Existing SSP allowlist, redirect, dry mode, routing, and recipient restrictions remain intact | R08 R09 |
| A13 | Report refreshes and corrections create no customer email jobs or subscriptions | R08 R11 |
| A14 | Client access and dashboard use do not enroll recipients or call customer delivery providers | R04 R08 |
| A15 | Existing SSP thresholds, confirmations, reminders, acknowledgments, and escalation pass regression checks | R09 |
| A16 | Dashboard issues show account, count, data-through time, explanation, and useful next action at desktop and mobile sizes | R10 R11 |
| A17 | Embedded login and period navigation work in tested browsers; blocked storage has a secure fallback | R05 |
| A18 | Forwarded report links require the viewer's own authorized identity; any exports stay private | R03 R05 |
| A19 | No reporting test emails or invitations are sent by the rollout without their required approval; existing SSP test restrictions remain | R08 R12 |
| A20 | Disable client access and publication while preserving SSP reporting, isolation, and audit evidence | R01 R12 |
| A21 | Existing Python and web suites pass; speed to lead and SSP reports remain available | R01 R07 R09 |
| A22 | Actual pilot GHL dashboard loads the correct account, has usable sizing and navigation, and exposes no other account data | R03 R05 R10 |

## 14 Release gates and rollback

Client access cannot launch until the permission migration, isolation tests, secure onboarding, safe report projection, and embedded dashboard experience have passed. Approve the pilot account and viewer roster, report definitions, refresh expectations, and final layout. Customer email setup, provider selection, reminder rules, and delivery tests are not dashboard release gates.

Use separate controls for client access and client report publication. Keep existing SSP delivery controls intact. Access suspension must be enforced at the database or trusted server boundary, not just hidden in the interface. Document who can operate each control and where it is configured.

Rollback order: disable affected client access at the authorization boundary; pause affected report publication; remove or hide the dashboard embed if appropriate; retain SSP collection and reporting if safe; preserve audit evidence; diagnose and correct. Removing the widget alone does not revoke access. Do not restore broad staff-domain policies or destroy historical data to roll back.

Completion means one approved sub-account has a usable, secure dashboard report, approved viewers see only their accounts, speed to lead and follow-up reporting are correct, successful refreshes and stale states are demonstrated, customer emails remain deferred, SSP behavior is preserved, and actual settings and operational ownership are recorded.

## 15 Looker Studio option

The Python calculations are compatible with Looker Studio when their results are published as tables or approved views. Looker does not need to reproduce all of the Python logic. Use it later for visual trend reporting if it adds value; the existing app is the preferred first interface for reporting, permissions, and existing SSP oversight.

If adopted, use a dedicated restricted, read-only reporting connection and precomputed aggregates. The PostgreSQL connector uses its own database credentials and does not inherit the portal viewer's Supabase session. Keep internal tables and collector credentials inaccessible through that connection.

Private embedded reports require Google sign-in. Viewer-email filtering requires the viewer's Google identity and consent, an explicit email-to-account mapping, and correct filtering on every contributing data source. URL filters and hidden controls are not authorization. Validate report sharing, downloads, copied reports, and owner credentials against the chosen design. Use the generated report embed URL and test GHL embedding separately.

Looker Studio integration is outside the required first release. Do not confuse its sharing model with signed embedding in the separate Looker product. Reassess connector limits, report costs, and data exposure before adding it.

## 16 Decisions for Matthew

Confirmed October 1: defer customer report and alert emails and prioritize the report inside each GHL sub-account dashboard. The first release is proposed as read-only for client users; client acknowledgment and notification controls are deferred. Requirement IDs remain stable, with R08 explicitly enforcing the email deferral.

| ID | Decision | Recommended starting point |
| --- | --- | --- |
| D01 | First sub-account and approved viewer roster | One sub-account and one verified client owner, with additional viewers explicitly assigned |
| D02 | SSP AM visibility and administration | Assigned accounts; Matthew all accounts; no client self-invites |
| D03 | Customer delivery provider and sender | Deferred; no provider selection or delivery build in this release |
| D04 | Refresh expectations and stale-data limit | Nightly refresh; proposed 30-hour stale label; existing SSP trigger settings unchanged |
| D05 | Client acknowledgment and reminder controls | Deferred; read-only dashboard first; preserve SSP controls |
| D06 | Data retention and client detail exposure | Aggregate-only client reports; review retention against metric needs and contracts |
| D07 | Period and metric definitions | Account-local weeks and months; preserve formulas and label human response clearly |
| D08 | Dashboard placement, embed domains, and authentication | Approved GHL hosts; test invited login inside embed; standalone fallback and administrator MFA |

Approval record: reviewer, date, accepted decisions, requested changes, approved pilot account and viewers, and deferred requirements. Document review does not itself authorize production access, invitations, or live workflow changes.

## 17 Reference material

Repository and implementation evidence: GHLReports at commit `a6a4a13`; repository instructions, `docs/SECURITY-SPEC.md`, database migrations, collector automation and digest modules, and the web files listed in Section 12. Operational baseline comes from the September 28 handoff and must be reconfirmed.

- [GHLReports repository](https://github.com/matthewcarlsonhome-cmd/GHLReports)
- [HighLevel dashboard embed widgets](https://help.gohighlevel.com/support/solutions/articles/155000001627-embed-content-on-dashboards-with-our-embed-widgets)
- [HighLevel user and sub-account properties in dashboard iframes](https://help.gohighlevel.com/support/solutions/articles/155000001977-how-to-inject-user-sub-account-properties-in-iframes-on-sub-account-dashboards)
- [Supabase row level security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Google reporting PostgreSQL connector](https://docs.cloud.google.com/data-studio/connect-to-postgresql)
- [Google reporting embed documentation](https://docs.cloud.google.com/data-studio/embed-a-report)
- [Google reporting viewer email filtering](https://docs.cloud.google.com/data-studio/filter-by-email-address)

Product documentation was consulted September 29, 2026. Verify current provider behavior during implementation, particularly embedded authentication and dashboard capabilities. The October 1 revision changes scope; it is not a new production or provider audit.
