# Client dashboard rollout — October 1, 2026

Implements `CLIENT-DASHBOARD-SPEC.md` v1.1. This document separates tested code
from live acceptance. The first account is Small Screen Producer; Matthew's
verified `mcarlson@smallscreenproducer.com` identity is the sole pilot identity.
Customer report emails, invitation sends, acknowledgments, and retention deletion
are not enabled by this release.

## Verified baseline and current gate

- Matthew applied migration 0016 and the verified-owner bootstrap on October 1.
  Live read-back: active owner administrator, eight active staff grants, six new
  protected tables, zero client memberships, zero enabled/publishing accounts,
  and zero client report rows. SSP is active in America/Chicago.
- The Netlify client route and Render commit `204e192` are deployed. Supabase
  `prepare-client-access` is deployed with Verify JWT enabled. No invitation was
  created and the function has not been exercised with live onboarding.
- Render `CLIENT_REPORTS=on` is saved for the next run, without triggering one.
  Account publication remains off pending administrator verification. Existing
  `AUTOMATION_WEBHOOKS=dry` is confirmed unchanged; digest recipient settings and
  `COLLECTOR_ARGS` are absent. No email or invitation was sent.
- Matthew must complete authenticator verification himself at `/access`. GHL tab
  control is currently timing out; the existing Account Report widget is unchanged.
  SSP publication, first report, production-equivalent non-admin session tests,
  authenticated embedding and browser acceptance remain pending. Do not describe
  this pilot as launched.

## Permission migration and deployment

1. Apply `supabase/migrations/0016_client_dashboard.sql` in the authenticated
   Supabase SQL editor as one transaction. It creates explicit staff grants,
   memberships, invitations, safe reports, settings and audit records. It preserves
   the eight existing confirmed staff grants once, while removing domain-based
   automatic authorization for future users. New client access defaults off.
2. After the required live permission confirmation, bootstrap only the verified
   owner using the query below. No arbitrary UUID or browser email hint is trusted.
   Verify exactly one matching confirmed identity before running it.

   ```sql
   select public.bootstrap_dashboard_admin(id)
   from auth.users
   where lower(email) = 'mcarlson@smallscreenproducer.com'
     and email_confirmed_at is not null;
   ```

3. Deploy `supabase/functions/prepare-client-access/index.ts` as
   `prepare-client-access` with JWT verification enabled. Supabase supplies its
   built-in server credentials; never copy service credentials into Netlify or
   browser code. `DASHBOARD_ORIGIN` defaults to the live Netlify origin.
4. Push the tested default branch per AGENTS.md. Netlify and Render deploy from it.
   Before migration, existing staff pages use the existing server `is_staff()`
   check; client routes stay unavailable. Failed client calculation/publication
   does not interrupt SSP snapshots or delivery processing.
5. Matthew signs in and enrolls/verifies his own authenticator at `/access`.
   Membership/settings/invitation changes require an explicit administrator grant
   and `aal2`. The agent must not enter or capture authenticator secrets or codes.
6. Confirm Auth public signup is disabled, OTP/session/rate-limit settings, exposed
   schemas and storage permissions. Inventory production grants/views/functions
   against the migration tests; check remaining SECURITY-SPEC findings. No exposed
   credential has been identified by this change; rotation is evidence-driven.

## Pilot publication and onboarding

- Keep client access off while previewing. Enable report publication for SSP only
  in `/access`; set `CLIENT_REPORTS=on` on the existing Render collector only after
  migration. This is independent of all digest and AM-notification settings.
- Let the approved nightly collection publish a safe report. If a manual run is
  needed, Matthew performs it on Render per AGENTS.md. Empty `COLLECTOR_ARGS` runs
  the full collector; Mondays also run the existing digest path. Do not trigger a
  run or change mail controls as a shortcut for dashboard testing.
- Verify SSP appears in the collector's active scope and a report was published;
  an empty report is not successful end-to-end acceptance.
- Existing verified Matthew needs no customer invitation or test email. New
  external identities must be prepared by a verified administrator. Preparation
  creates an unconfirmed Auth identity and an expiring invitation; it sends no
  email. Agree how the one-time code is delivered before using it. A user-requested
  email login code verifies the exact identity; accepting the invitation then
  creates membership. Signup alone never grants report access.
- Before external viewers, complete synthetic two-account REST/session tests on
  production-equivalent Supabase. Local PostgreSQL isolation tests are necessary
  but do not verify the live Auth gateway or all dashboard settings.
- Review the AM assignment roster before reducing any existing staff member from
  all accounts to assigned accounts. Access assignments do not change `am_email`
  or existing notification routing.

## Dashboard placement and verification

In SSP's own dashboard, configure a clearly named **Account Follow Up** embed:

`https://mlhaccountreports.netlify.app/client/accounts/ZnckuEDPIcWu8fn72ppi?embed=1`

The location ID selects a report; it is not a credential. No secret, permanent
token, or email authentication parameter belongs in the widget. Frame ancestors
are restricted to the Netlify origin itself, `crm.smallscreenproducer.com`, and
`app.gohighlevel.com`. Use the actual approved host for final testing.

The embedded view displays only that URL's account and omits account switching
and administration links, even for Matthew. Opening the report outside the frame
restores the authorized account selector. A widget URL without an account ID is
a configuration error. This does not remove administrator permissions or grant
client access: assigned client roles and the access switch remain the security
boundary. Test actual client isolation with a client identity, not Matthew's
administrator session.

Verify embedded sign-in, three views, completed periods, empty/partial/stale states,
wrong account, logout, session expiry, revoked membership, narrow layout and a long
account name. Test Chrome and Safari and blocked third-party storage. The new-tab
link retains the requested account. Do not weaken authentication to make an embed
work. Live GHL, Safari and live OTP/MFA acceptance are still outstanding.

Only after those checks should the approved account's access switch and explicit
viewer membership be enabled. No customer notification subscriptions exist.

## Report definitions and limits

- Current action queues and attention speed retain existing snapshot formulas.
  Calendar speed is calculated from individual response records, never averages
  of daily medians. Human versus automated classification is unchanged.
- Weeks begin Monday in the account timezone; month boundaries are local. Today's
  incomplete day is excluded. Completion uses leads at least 24 hours old at the
  period cutoff and observed outbound activity before that cutoff.
- Contact history currently covers 42 days; response scanning covers 14 days and
  has the existing 100-target cap. Missing, skipped, capped or failed coverage is
  unavailable. Older monthly speed will often be unavailable. No synthetic
  historical backfill or collection-window expansion is included.
- Reports retain versions. Readers receive the latest plus a previous complete
  version of the same period when available. Earlier versions are clearly labeled;
  current views turn stale after the configured limit (default 30 hours). Saved
  historical reports keep their original timezone and period definitions.
- View checkboxes control presentation, not a separate authorization tier. A
  member's permission is to that account's safe aggregate history. Clients cannot
  read internal notes, lead records, diagnostics or staff-only reports.
- Authenticated report RPC reads and admin changes are audited. Direct safe-table
  reads remain RLS-protected and rely on platform request logs rather than the
  application read audit. Failed RPC transactions also require platform logs.
  No new automated incident notification or retention deletion job is introduced.

## Validation evidence and acceptance status

- Python: 252 tests pass, including new PII canary, calendar/DST, partial coverage,
  missing history, cutoff and preserved attention-speed tests.
- Web: 21 tests pass; production TypeScript/Vite build passes. The existing bundle
  size warning remains; it is not a build failure.
- PostgreSQL: every migration executes in isolated PGlite with real roles/RLS.
  Tests cover two accounts, multi-account clients, assigned/revoked staff, spoofed
  claims, direct raw-table denial, admin MFA, settings validation, anonymous denial,
  invitation binding/expiry/replay/revocation/unverified identity, publication and
  access kill switches, and previous complete report retrieval.
- Edge-handler tests verify denied callers cannot reach service credentials,
  invalid inputs/origins fail, and provisioning does not verify email or send mail.
- Local Chrome desktop and 390px embedded previews use synthetic aggregates only. Live GHL embed acceptance,
  production REST isolation, live onboarding, Safari, storage blocking and observed
  successful nightly refresh are release gates, not claimed passes.

Run local database tests with `node supabase/tests/client_access.mjs` and an
isolated `@electric-sql/pglite` install (or `PGLITE_MODULE` module URL); no secrets
or network are required. Run standard Python/web commands in AGENTS.md as well.

## Recovery and ownership

Matthew operates the pilot. First disable affected client access in `/access`,
then pause report publication. Both controls are checked server-side; removing a
widget alone does not revoke access. An operator recovery query, preserving data:

```sql
update public.client_report_settings
set access_enabled=false, publication_enabled=false
where location_id='ZnckuEDPIcWu8fn72ppi';
insert into public.access_audit(action,location_id,target_id)
values('operator_suspend','ZnckuEDPIcWu8fn72ppi','access and publication disabled');
```

Set global `CLIENT_REPORTS=off` to stop all client publishing while retaining SSP
collection. Preserve audits and report versions. Do not restore domain-wide grants
or delete historical data. Investigate collection logs, latest report coverage,
Supabase request/auth logs and access changes before re-enabling the pilot.
