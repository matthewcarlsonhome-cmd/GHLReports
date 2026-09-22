-- 0013: which accounts actually use MLH, and what the Forms tab needs.
--
-- 1. subaccounts.mlh_status: 'active' (the client's team works in MLH),
--    'ads_only' (SSP runs ads; leads route through the subaccount, nobody
--    works them there), 'not_in_mlh' (never onboarded / not in MLH),
--    'canceled'. Only 'active' accounts appear in the AM digest, the
--    default portfolio view, the Forms tab and the reports; the others are
--    still collected unless also set inactive. mlh_note keeps the reason.
--    Seeded from Lisa's list (2026-09-22). Luke Gell is canceled, so it is
--    also set inactive (no longer collected).
-- 2. form_health gains what the Forms tab shows per form: channel (website /
--    Google ad / Facebook ad / Facebook lead ad ...), page_url (page of the
--    newest real submission), subs_30d, and the weekly form check
--    (last_check_at, check_contact_ok). kind gains 'unlisted': form ids that
--    receive submissions but aren't in Sites > Forms (Facebook lead ads).
-- 3. v_portfolio exposes mlh_status / mlh_note (appended; security_invoker).
alter table public.subaccounts
  add column if not exists mlh_status text not null default 'active',
  add column if not exists mlh_note text;
alter table public.subaccounts drop constraint if exists subaccounts_mlh_status_check;
alter table public.subaccounts add constraint subaccounts_mlh_status_check
  check (mlh_status in ('active', 'ads_only', 'not_in_mlh', 'canceled'));

update public.subaccounts set mlh_status = 'ads_only', mlh_note = 'GA only'
 where location_id in ('bnhBYB6iicvfCdnvQInj',   -- All American Landscape and Stone
                       'SU0o0YxCPRas4d3yLFZO',   -- Beachfront Pools & Design
                       'HfmeeBccBd108Dyl7ti1',   -- Pla-mor Pools
                       'qE6xOlwFOYJESfD3lN3W');  -- Pristine Pools
update public.subaccounts set mlh_status = 'ads_only', mlh_note = 'GA/FB only'
 where location_id = '2kllua5NLPaIhszvBWTS';     -- G&S Custom Pools
update public.subaccounts set mlh_status = 'canceled', mlh_note = 'canceled', active = false
 where location_id = 'onOiJL4dBp2SgeZyBYVz';     -- Luke Gell Pools
update public.subaccounts set mlh_status = 'not_in_mlh', mlh_note = 'never onboarded'
 where location_id = 'KHrPHp1Pr9aKWYaU9Fm7';     -- Hamlin Pools
update public.subaccounts set mlh_status = 'not_in_mlh', mlh_note = 'not in MLH'
 where location_id = 'wBCf5ffXVA6RpbvvTtNs';     -- Kura Design Pools

alter table public.form_health
  add column if not exists channel text,
  add column if not exists page_url text,
  add column if not exists subs_30d int,
  add column if not exists last_check_at timestamptz,
  add column if not exists check_contact_ok boolean;
alter table public.form_health drop constraint if exists form_health_kind_check;
alter table public.form_health add constraint form_health_kind_check
  check (kind in ('form', 'survey', 'unlisted'));

create or replace view public.v_portfolio with (security_invoker = on) as
 with latest as (
   select distinct on (snapshots.location_id) snapshots.*
     from snapshots
    order by snapshots.location_id, snapshots.snapshot_date desc, snapshots.captured_at desc
 ), fl as (
   select f.location_id, f.snapshot_date,
      count(*) filter (where f.severity = 'red' and a.id is null) as red,
      count(*) filter (where f.severity = 'amber' and a.id is null) as amber,
      count(*) filter (where f.severity = 'info') as info,
      count(*) filter (where a.id is not null) as acked,
      coalesce(sum(case when a.id is not null then 0
                        when f.severity = 'red' then 3
                        when f.severity = 'amber' then 1
                        else 0 end), 0::bigint) as attention_score,
      (array_agg(f.action order by (a.id is not null),
         (case f.severity when 'red' then 0 when 'amber' then 1 else 2 end), f.id))[1] as top_action
     from flags f
     left join lateral ( select x.id from flag_acks x
            where x.location_id = f.location_id and x.code = f.code
              and x.snooze_until >= current_date
            order by x.acked_at desc limit 1) a on true
    group by f.location_id, f.snapshot_date
 )
 select s.location_id, s.name, s.slug, s.vertical, s.services, s.am_email, s.timezone,
    s.is_parent, s.mrr, s.contract_end, s.token_status, s.token_rotated_at,
    l.snapshot_date, l.captured_at, l.gate_passed, l.coverage, l.leads_new_7d,
    l.leads_trailing_avg, l.trailing_n, l.leads_delta_pct, l.peer_median_delta_pct,
    l.peer_n, l.leads_unassigned_7d, l.leads_missing_phone_pct_7d, l.form_submissions_7d,
    l.leads_uncontacted_24h, l.leads_no_human_touch_7d, l.speed_to_lead_median_min,
    l.speed_kind_known, l.convos_waiting, l.convos_waiting_max_hours, l.opps_open,
    l.opps_open_value, l.opps_stale, l.opps_stale_value, l.opps_missing_value,
    l.lead_to_opp_28d_pct, l.win_rate_90d, l.noshow_rate_28d, l.days_since_last_publish,
    l.social_accounts_expired, l.client_last_touch_days, l.client_next_appt_at,
    l.flags_new, l.flags_resolved,
    coalesce(fl.red, 0::bigint) as red,
    coalesce(fl.amber, 0::bigint) as amber,
    coalesce(fl.info, 0::bigint) as info,
    coalesce(fl.acked, 0::bigint) as acked,
    coalesce(fl.attention_score, 0::bigint) as attention_score,
    fl.top_action,
    case
      when s.token_status <> 'ok' then 'no_data'
      when l.snapshot_date is null or not l.gate_passed
           or l.captured_at < (now() - '36:00:00'::interval) then 'no_data'
      when coalesce(fl.red, 0::bigint) > 0 or coalesce(fl.amber, 0::bigint) >= 2 then 'attention'
      else 'steady'
    end as state,
    l.calls_missed_7d,
    ( select count(*)::integer from form_health fh
       where fh.location_id = s.location_id and fh.snapshot_date = l.snapshot_date
         and fh.status = 'silent') as forms_silent_ct,
    l.opps_moved_30d, l.bottleneck_stage, l.bottleneck_value_usd,
    s.am_name,
    l.client_users, l.ssp_users,
    s.mlh_status, s.mlh_note
   from subaccounts s
   left join latest l on l.location_id = s.location_id
   left join fl on fl.location_id = l.location_id and fl.snapshot_date = l.snapshot_date
  where s.active;

grant select on public.v_portfolio to authenticated;
