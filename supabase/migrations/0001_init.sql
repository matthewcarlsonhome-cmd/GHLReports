-- supabase/migrations/0001_init.sql
create extension if not exists pgcrypto;

create table public.subaccounts (
  location_id text primary key,
  name text not null,
  slug text unique not null,                  -- short handle for CLI and URLs; vault secret name is ghl_pit_<location_id>
  vertical text,                              -- pool_builder | pool_service | hot_tub | spa_retail | other
  services text[] not null default '{}',      -- content, social, ads, seo, web, crm
  am_email text,
  timezone text not null default 'America/Chicago',
  ssp_client_contact_id text,                 -- this client's contact ID inside the SSP parent account
  is_parent boolean not null default false,
  active boolean not null default true,
  thresholds jsonb not null default '{}'::jsonb,
  token_status text not null default 'none' check (token_status in ('none','ok','invalid')),
  token_rotated_at date,
  last_token_error text,
  created_at timestamptz not null default now()
);

create table public.snapshots (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  snapshot_date date not null,
  captured_at timestamptz not null default now(),
  gate_passed boolean not null,
  coverage jsonb not null default '{}'::jsonb,
  leads_new_7d int, leads_trailing_avg numeric, trailing_n int, leads_delta_pct numeric,
  peer_median_delta_pct numeric, peer_n int,
  convos_active_7d int, opps_created_7d int,
  leads_uncontacted_24h int, leads_no_human_touch_7d int,
  speed_to_lead_median_min numeric, speed_to_lead_p90_min numeric, speed_kind_known boolean,
  excluded_count int,
  convos_waiting int, convos_waiting_max_hours numeric,
  opps_open int, opps_open_value numeric, opps_stale int, opps_stale_value numeric,
  opps_stuck int, opps_missing_value int, opps_no_next_step int,
  opps_won_7d int, opps_lost_7d int,
  blogs_published_30d int, social_published_7d int, days_since_last_publish int,
  invoices_past_due int, invoices_past_due_amount numeric,
  client_last_touch_days int, client_next_appt_at timestamptz,
  review_asks_stale int, review_ask_gap int,
  details jsonb not null default '{}'::jsonb,
  unique (location_id, snapshot_date)
);
create index snapshots_loc_date on public.snapshots (location_id, snapshot_date desc);

create table public.flags (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  snapshot_date date not null,
  code text not null,
  severity text not null check (severity in ('red','amber','info')),
  title text not null,
  detail text,
  action text,
  entity_type text, entity_id text, entity_name text, deep_link text,
  created_at timestamptz not null default now()
);
create index flags_loc_date on public.flags (location_id, snapshot_date);

create table public.lead_events (
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  contact_id text not null,
  contact_name text, source text,
  created_at timestamptz not null,
  first_outbound_at timestamptz, first_outbound_kind text,   -- human | automation | unknown
  first_human_touch_at timestamptz,
  first_touch_minutes numeric, first_human_touch_minutes numeric,
  updated_at timestamptz not null default now(),
  primary key (location_id, contact_id)
);
create index lead_events_loc_created on public.lead_events (location_id, created_at desc);

create table public.collector_runs (
  id bigserial primary key,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text,                                 -- ok | partial | failed
  locations_ok int default 0, locations_held int default 0, locations_failed int default 0,
  requests_made int, rate_limited int,
  error text
);

-- Portfolio view: latest snapshot per subaccount + flag rollup + state. Explicit columns (no s.*, l.*).
create or replace view public.v_portfolio with (security_invoker = on) as
with latest as (
  select distinct on (location_id) *
  from public.snapshots
  order by location_id, snapshot_date desc, captured_at desc
),
fl as (
  select location_id, snapshot_date,
         count(*) filter (where severity = 'red')   as red,
         count(*) filter (where severity = 'amber') as amber,
         count(*) filter (where severity = 'info')  as info,
         coalesce(sum(case severity when 'red' then 3 when 'amber' then 1 else 0 end), 0) as attention_score,
         (array_agg(action order by case severity when 'red' then 0 when 'amber' then 1 else 2 end, id))[1] as top_action
  from public.flags group by location_id, snapshot_date
)
select
  s.location_id, s.name, s.slug, s.vertical, s.services, s.am_email, s.timezone, s.is_parent,
  s.token_status, s.token_rotated_at,
  l.snapshot_date, l.captured_at, l.gate_passed, l.coverage,
  l.leads_new_7d, l.leads_trailing_avg, l.trailing_n, l.leads_delta_pct, l.peer_median_delta_pct, l.peer_n,
  l.leads_uncontacted_24h, l.leads_no_human_touch_7d, l.speed_to_lead_median_min, l.speed_kind_known,
  l.convos_waiting, l.convos_waiting_max_hours,
  l.opps_open, l.opps_open_value, l.opps_stale, l.opps_stale_value, l.opps_missing_value,
  l.days_since_last_publish, l.invoices_past_due, l.invoices_past_due_amount,
  l.client_last_touch_days, l.client_next_appt_at,
  coalesce(fl.red,0) as red, coalesce(fl.amber,0) as amber, coalesce(fl.info,0) as info,
  coalesce(fl.attention_score,0) as attention_score, fl.top_action,
  case
    when s.token_status <> 'ok' then 'no_data'
    when l.snapshot_date is null or not l.gate_passed or l.captured_at < now() - interval '36 hours' then 'no_data'
    when coalesce(fl.red,0) > 0 or coalesce(fl.amber,0) >= 2 then 'attention'
    else 'steady'
  end as state
from public.subaccounts s
left join latest l on l.location_id = s.location_id
left join fl on fl.location_id = l.location_id and fl.snapshot_date = l.snapshot_date
where s.active;

-- History for charts (84 days)
create or replace view public.v_history with (security_invoker = on) as
select location_id, snapshot_date, gate_passed, leads_new_7d, leads_trailing_avg, leads_delta_pct,
       peer_median_delta_pct, convos_waiting, opps_stale, opps_open_value, days_since_last_publish
from public.snapshots
where snapshot_date >= current_date - 84;

-- RLS: staff read, nobody writes through the API (collector uses service role, which bypasses RLS)
alter table public.subaccounts    enable row level security;
alter table public.snapshots      enable row level security;
alter table public.flags          enable row level security;
alter table public.lead_events    enable row level security;
alter table public.collector_runs enable row level security;

create or replace function public.is_staff() returns boolean language sql stable as $$
  select coalesce((auth.jwt() ->> 'email') ilike '%@smallscreenproducer.com', false)
$$;

create policy staff_read_subaccounts on public.subaccounts    for select to authenticated using (public.is_staff());
create policy staff_read_snapshots   on public.snapshots      for select to authenticated using (public.is_staff());
create policy staff_read_flags       on public.flags          for select to authenticated using (public.is_staff());
create policy staff_read_lead_events on public.lead_events    for select to authenticated using (public.is_staff());
create policy staff_read_runs        on public.collector_runs for select to authenticated using (public.is_staff());

-- Refuse non-domain sign-ups at the source (applies to any auth method, including email OTP)
create or replace function public.enforce_staff_domain() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.email is null or new.email not ilike '%@smallscreenproducer.com' then
    raise exception 'Sign-in restricted to smallscreenproducer.com accounts';
  end if;
  return new;
end $$;
drop trigger if exists enforce_staff_domain_trg on auth.users;
create trigger enforce_staff_domain_trg before insert on auth.users
  for each row execute function public.enforce_staff_domain();

grant usage on schema public to anon, authenticated;
grant select on public.v_portfolio, public.v_history to authenticated;

-- Audit of token writes and reads (never stores the token). No RLS policies: invisible to API roles.
create table public.pit_audit (
  id bigserial primary key,
  location_id text,
  action text not null,           -- set | rotate | delete | read | read_denied
  actor text,                     -- 'collector' | 'cli' | null
  at timestamptz not null default now()
);
alter table public.pit_audit enable row level security;

-- One-time bootstrap: the collector's second factor. Generate 32+ random chars locally
-- (python -c "import secrets;print(secrets.token_urlsafe(32))") and paste it here once, then put the
-- same value in Render as COLLECTOR_KEY. Do not commit the value.
-- select vault.create_secret('<paste>', 'collector_key', 'second factor for PIT retrieval');

-- Key check. Returns boolean instead of raising so that a denied attempt can be audited
-- (an insert followed by raise exception would be rolled back with the transaction).
create or replace function public.pit_key_ok(p_key text) returns boolean
language plpgsql security definer set search_path = public, vault as $$
declare v text;
begin
  select decrypted_secret into v from vault.decrypted_secrets where name = 'collector_key';
  if v is null or p_key is distinct from v then
    insert into public.pit_audit(action, actor) values ('read_denied', 'unknown');
    return false;
  end if;
  return true;
end $$;
revoke all on function public.pit_key_ok(text) from public, anon, authenticated;
grant execute on function public.pit_key_ok(text) to service_role;

-- Returns the token, or null when the key is wrong OR no token is stored. Callers distinguish the two
-- by calling pit_key_ok first if they need to (the collector does, once per run).
create or replace function public.get_pit(p_location_id text, p_collector_key text) returns text
language plpgsql security definer set search_path = public, vault as $$
declare v_token text;
begin
  if not public.pit_key_ok(p_collector_key) then return null; end if;
  select decrypted_secret into v_token from vault.decrypted_secrets where name = 'ghl_pit_' || p_location_id;
  insert into public.pit_audit(location_id, action, actor) values (p_location_id, 'read', 'collector');
  return v_token;
end $$;
revoke all on function public.get_pit(text, text) from public, anon, authenticated;
grant execute on function public.get_pit(text, text) to service_role;

create or replace function public.set_pit(p_location_id text, p_token text, p_collector_key text) returns boolean
language plpgsql security definer set search_path = public, vault as $$
declare v_id uuid;
begin
  if not public.pit_key_ok(p_collector_key) then return false; end if;   -- false, not raise, so the read_denied audit row commits
  if p_token is null or length(p_token) < 20 then raise exception 'token looks wrong'; end if;
  select id into v_id from vault.secrets where name = 'ghl_pit_' || p_location_id;
  if v_id is null then
    perform vault.create_secret(p_token, 'ghl_pit_' || p_location_id, 'GHL read-only PIT');
    insert into public.pit_audit(location_id, action, actor) values (p_location_id, 'set', 'cli');
  else
    perform vault.update_secret(v_id, p_token);
    insert into public.pit_audit(location_id, action, actor) values (p_location_id, 'rotate', 'cli');
  end if;
  update public.subaccounts set token_rotated_at = current_date, token_status = 'ok' where location_id = p_location_id;
  return true;
end $$;
revoke all on function public.set_pit(text, text, text) from public, anon, authenticated;
grant execute on function public.set_pit(text, text, text) to service_role;

create or replace function public.delete_pit(p_location_id text, p_collector_key text) returns boolean
language plpgsql security definer set search_path = public, vault as $$
begin
  if not public.pit_key_ok(p_collector_key) then return false; end if;
  delete from vault.secrets where name = 'ghl_pit_' || p_location_id;
  insert into public.pit_audit(location_id, action, actor) values (p_location_id, 'delete', 'cli');
  update public.subaccounts set token_status = 'none' where location_id = p_location_id;
  return true;
end $$;
revoke all on function public.delete_pit(text, text) from public, anon, authenticated;
grant execute on function public.delete_pit(text, text) to service_role;
