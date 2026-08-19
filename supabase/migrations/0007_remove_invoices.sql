-- Invoices/payments are out of scope for this product (decision 2026-08-19):
-- the dashboard tracks marketing/CRM health only, never billing. Drop the two
-- snapshot columns, rebuild v_portfolio without them, and purge historical
-- PAST_DUE flags plus any invoice detail rows already stored.
--
-- Dropping columns from a view requires drop + create (create or replace can
-- only append). 0001 revoked default privileges, so the select grant to
-- authenticated must be re-issued on the recreated view.

drop view if exists public.v_portfolio;

alter table public.snapshots drop column if exists invoices_past_due;
alter table public.snapshots drop column if exists invoices_past_due_amount;

-- Historical flag rows and acks for the retired PAST_DUE code.
delete from public.flag_acks where code = 'PAST_DUE';
delete from public.flags where code = 'PAST_DUE';

-- Invoice line items stored inside old snapshots' details blobs.
update public.snapshots
   set details = details - 'past_due_invoices'
 where details ? 'past_due_invoices';

create view public.v_portfolio with (security_invoker = on) as
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
       and fh.status = 'silent') as forms_silent_ct,
  l.opps_moved_30d,
  l.bottleneck_stage,
  l.bottleneck_value_usd
from public.subaccounts s
left join latest l on l.location_id = s.location_id
left join fl on fl.location_id = l.location_id and fl.snapshot_date = l.snapshot_date
where s.active;

grant select on public.v_portfolio to authenticated;
