# SSP weekly owner report workflow and rollout

Implementation checkpoint: October 2, 2026. This supplements OWNER-WEEKLY-REPORT-SPEC.md and records actual setup status. Delivery remains off until the checks below are completed.

Release 606203e is pushed. Migration 0017 is now applied to ghl-health; read-back confirms nine tables with row security enabled/forced, mode off, zero pilot locations and zero queued sends. The claim and result functions are deployed as single-file bundles of the shared handler and corresponding entry point, with gateway JWT verification OFF for these two endpoints following Matthew's explicit approval. Owner mode remains default off. Both live endpoints reject GET (405) and empty POST (403). The owner screens are visible on Netlify, and Render confirms 33cbffc deployed; no new collector run has occurred. The ordered steps below describe the full rollout, including steps now completed; do not rerun the additive migration.

## What is built

The application contains weekly and monthly owner summaries, current queues, topic settings, versioned email rendering, account-bound saved report links, a separate dispatcher and protected send ledger. Default weekly delivery is Monday at 8 a.m. in the account timezone. Monthly email is not enabled.

The new GHL Draft is **SSP - Owner Report Template QA - v1** in SSP location `ZnckuEDPIcWu8fn72ppi`, workflow `623657b3-eabc-451f-b0a6-b8c34865d4a2`. Four connected action steps are saved: SSP test-envelope claim, allow_send equals true branch, Matthew-only internal email, and guarded action-result callback. Rejected claims end. Both Custom Code actions passed mapping/empty-input validation (two executions, no HTTP request and no email). The inbound trigger cannot yet be saved: GHL requires a received mapping sample, and none exists. Claim input remains the harmless mapping placeholder until that sample is selected. This is a saved Draft, not a usable delivery workflow.

The existing SSP embedded dashboard was inspected in Chrome on October 2 and contains a report checked at 5:30 a.m. Central. The earlier empty-report diagnosis is closed. This is evidence for existing reporting, not for new owner delivery.

## The two workflows

| Workflow | Accepts | Recipient | Schedule |
|---|---|---|---|
| SSP Owner Report Template QA v1 | Only an explicitly approved SSP `test` envelope | Verified Particular User Matthew Carlson (mcarlson@smallscreenproducer.com) | One test by reference, no recurring schedule |
| SSP Weekly Owner Report v1 | `test` acceptance and then `live` envelopes for the same account and bound workflow | One verified SSP report owner | Application dispatcher, Monday 8 a.m. local by default |

Both stay Draft until Matthew publishes. Build and verify the QA path first; create the weekly workflow from the verified structure, then bind its own identity. Neither workflow creates, searches for or modifies a contact. Existing MC Account Health Alerts remains separate.

## Exact GHL structure

1. **Inbound Webhook** trigger. Use `mapping-sample.json` for field mapping, with no contact email or phone. Mapping mode cannot send. Do not retain an automatically added Create Contact action. The operator transfers the inbound URL directly to the app's masked connection field; never include it in a document, screenshot, log or chat.
2. **Custom Code — Verify SSP test envelope and claim once.** One input named `envelope`, selected from the inbound sample's envelope field using the Custom Value picker. Use `claim-action.js`. The file pins SSP location, QA workflow ID and `test` mode. Its destination is the protected Supabase claim endpoint. Test mapping/malformed/wrong-account/live inputs: all return `allow_send = "false"`. This is a code test, not a test email.
3. **If / Else — Approved claim only.** Select the Custom Code action's **allow_send** output in GHL's picker. Compare to the literal string `true`. The unmatched branch ends. Never branch on an inbound allow_send value.
4. **Internal Notification — Email — Particular User.** The saved Draft selects only Matthew Carlson. SSP Users confirms mcarlson@smallscreenproducer.com and user ID 5jJfcIlQiy0jRxIvH039; the app is bound to that same identity. It uses the account default sender, because GHL requires a sender address when a custom From Name is entered. Confirm sender delivery during the approved inbox test. No Assigned User, All Users, CC, BCC or SMS.
5. **Subject and body.** Pick **subject** and **body_html** from the claim action output, not the inbound webhook. Only use body_html if GHL renders it as HTML in this specific action; otherwise use the reviewed scalar template mappings or plain-text body. Do not assume raw HTML merge fields render correctly. The rendered content chooses attention, healthy or limited styling from the same saved report shown in the dashboard. Keep the visible TEST SAMPLE DATA banner for tests.
6. **Custom Code — Record notification action.** Place after the email action. Use `result-action.js`; map **delivery_id** and **completion_credential** from the successful claim action. This records an action outcome, not proof of inbox delivery.
7. **End.** No repeat loop, waiting sequence or GHL weekly trigger. Never manually restart at the email action; doing so bypasses the one-use claim.

GHL's own Custom Code snippets confirm `customRequest.post(url, {data, headers})` and `{status, data}` responses. The actual allowed claim and contactless email path still require live verification. GHL identifies Custom Code as a premium action and mandates a successful code test before saving. Matthew approved up to ten no-email code validations capped at $1; two successful executions were used. No workflow test, email send or publication occurred. Cost charged was not shown.

## Operator step needed for the inbound sample

Repository policy forbids the agent from copying the private inbound URL. Matthew supplies it directly on his computer, never in chat. After approving one mapping request, open Add trigger > Inbound Webhook in the SSP QA Draft and run `docs/owner-workflow/send-mapping-sample.ps1` locally. It prompts invisibly for the URL, accepts only the SSP LeadConnector hook path, posts the credential-free mapping sample once, and neither saves nor prints the URL. Do not run with a real report credential. This request is separate from the already approved Custom Code validation runs.

Then choose Fetch sample requests > the mapping request > Save trigger. Open the first Custom Code action and replace its current literal mapping placeholder with Inbound Webhook > envelope using the picker. Save and leave Draft. The helper has been syntax checked, not executed against GHL.

## Field mapping reference

| GHL field | Source |
|---|---|
| Claim input envelope | Inbound Webhook → envelope |
| Allow branch | Claim action → allow_send equals string true |
| Notification subject | Claim action → subject |
| Notification HTML or text | Claim action → body_html or body_text, after renderer verification |
| Callback delivery_id | Claim action → delivery_id |
| Callback completion_credential | Claim action → completion_credential |
| Recipient | One verified Particular User: Matthew in SSP QA. Never supplied by the envelope |

Use the picker to obtain actual GHL merge syntax. These labels are a mapping specification, not invented merge expressions.

## Application deployment order

1. Run Python/web/database checks and build. Push the release with all owner sending off. Existing Render collection and Netlify hosting continue; no server migration is necessary.
2. Apply `supabase/migrations/0017_owner_reports.sql` to **ghl-health**, project `tpavdifpsevkrubplyrg`. Do not apply it to the separately open SSPAutomation project. This is an additive migration, tested in isolated PostgreSQL; it does not enable sending or grant client memberships.
3. Deploy `owner-report-claim` and `owner-report-result` with their `_shared/owner-workflow.ts` dependency. Keep `OWNER_REPORTS_MODE=off` initially. They authenticate each request using expiring one-use credentials checked against the database. GHL does not carry an app-user JWT, so deployment must explicitly configure gateway JWT handling for these two endpoints only after reviewing the credential-authentication design. This scoped change is now approved and applied for these two functions only. Do not change the existing prepare-client-access function's JWT policy.
4. Keep `CLIENT_REPORTS=on`. Run an SSP-only collection with existing AM sending disabled/dry and digest recipients not enabled. Owner publication uses the completed collection and migration; absent migration leaves existing reporting intact.
5. In Access settings, select SSP; confirm publication, freshness threshold and timezone. Assign an existing verified person the report Owner role. Save topics/schedule in Preview. Bind the actual GHL user ID/email and workflow ID/revision. The masked connection field stores the URL in Supabase Vault.
6. Finish the GHL claim/branch/template checks, record evidence, prepare one test and approve its specific reference. Set the dispatcher and endpoints to Preview. Run only that approved reference through `python -m collector.owner_delivery --approved-test <reference>`. Do not run the nightly collector as a substitute for this command.
7. Confirm the received message, recipient, format and authenticated saved-report link; record the inbox check. HTTP acceptance and a successful notification action alone are insufficient.
8. Bind WF-02 separately, using `production-claim-action.js` with its verified workflow ID. It accepts approved test mode and live mode, preserving the mode passed to the claim endpoint. The database requires the credential to match that exact mode and binding. Run and accept a NEW test on this production destination before enabling live. QA acceptance never transfers to a clone. This resolves the single-destination acceptance sequence without weakening replay or mode checks. WF-02 is not yet created or tested in GHL.
9. Once that gate is resolved and Matthew accepts the pilot, publish WF-02 and enable the one SSP schedule. Run the independent dispatcher every 15 minutes with a daily cap of 1 and SSP as the only pilot location. No automated monthly email.

## Limits and recovery

Response history is retained prospectively but a complete monthly response reconstruction is not yet implemented. Missing/capped historical data stays unavailable. No daily medians are averaged. Month-to-date lead comparisons use the same elapsed prior-month days only when source coverage supports them.

The application records rejected, accepted, claimed, unknown and action-recorded states distinctly. Timeout/ambiguous transport outcomes are not automatically resent. Pause owner delivery to stop pending work; already completed sends cannot be recalled. Reconcile unknown or claimed jobs individually. Retain report and send history; do not delete it during recovery.

## Release evidence

Local checks: Python regression suite, browser-independent website/edge-handler tests, production website build, and isolated PostgreSQL migration/RLS/claim tests. Tests cover saved synthetic report IDs, account freshness, unknown report IDs, negative trends, selected topics, PII exclusion, MFA, account separation, immutable snapshots, recipient revision changes and replay denial. Live allowed-claim/HTML/inbox proof is pending.
