-- form_health.status gains 'dormant' (docs/FORM-MONITORING.md §1).
--
-- The collector now asks GHL for ALL-TIME submission history (explicit
-- startAt/endAt). Without explicit dates GHL defaults to the last month,
-- which is why every form quiet for 30+ days used to read as 'no_leads'
-- ("never had a lead"). Those forms now carry their real last-submission
-- date and the status 'dormant': had submissions once, none in over 30
-- days. 'silent' keeps its meaning (went quiet within the last 30 days) and
-- stays the only status that flags, so the digest sees no new noise.
--
-- Safe to apply before or after the collector deploy: it only widens the
-- allowed values, and v_portfolio.forms_silent_ct counts 'silent' only.
alter table public.form_health drop constraint if exists form_health_status_check;
alter table public.form_health add constraint form_health_status_check
  check (status in ('active', 'silent', 'dormant', 'no_leads', 'new', 'unknown'));

comment on column public.form_health.submissions_total is
  'All-time real submissions (form-check tests excluded). Rows before 2026-09-23 hold GHL''s default ~30-day window count.';
comment on column public.form_health.last_submission_at is
  'Newest real submission, all-time. Rows before 2026-09-23 only saw the last ~30 days.';
