-- Forms & surveys health (docs/FORMS-INTEGRATION.md Phase 1).
-- One row per form/survey per location per nightly run: the collector lists
-- every form and survey in the subaccount and classifies each one. Keeping
-- a row per snapshot_date (not just "latest") makes "when did this form go
-- silent" answerable from history — the thing the ad-hoc checker couldn't do.
create table public.form_health (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  snapshot_date date not null,
  kind text not null check (kind in ('form','survey')),
  form_id text not null,
  name text not null,
  -- active   = received a submission within the silence threshold
  -- silent   = had submissions before, nothing within the threshold
  -- no_leads = never received a submission (and not newly created)
  -- new      = created in the last 30 days, no submissions yet
  -- unknown  = submissions endpoint not accessible (missing scope)
  status text not null check (status in ('active','silent','no_leads','new','unknown')),
  submissions_total int,
  last_submission_at timestamptz,
  form_created_at timestamptz,
  unique (location_id, kind, form_id, snapshot_date)
);

-- Same read-boundary as every other table: staff (pre-provisioned
-- @smallscreenproducer.com users) can read, nobody else; the collector
-- writes with the service role, which bypasses RLS.
alter table public.form_health enable row level security;
alter table public.form_health force row level security;
create policy staff_read_form_health on public.form_health
  for select to authenticated using (public.is_staff());
grant select on public.form_health to authenticated;

-- v_portfolio: forms_silent_ct appended at the end (create or replace view
-- only allows adding columns at the end). Counts silent forms AND surveys
-- for the same snapshot the row is showing.
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
  end as state,
  l.calls_missed_7d,
  (select count(*)::int from public.form_health fh
     where fh.location_id = s.location_id
       and fh.snapshot_date = l.snapshot_date
       and fh.status = 'silent') as forms_silent_ct
from public.subaccounts s
left join latest l on l.location_id = s.location_id
left join fl on fl.location_id = l.location_id and fl.snapshot_date = l.snapshot_date
where s.active;
