-- supabase/migrations/0001_init.sql
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------
create table public.subaccounts (
  location_id text primary key,
  name text not null,
  slug text unique not null,                  -- short handle for URLs; vault secret name is ghl_pit_<location_id>
  vertical text,                              -- pool_builder | pool_service | hot_tub | spa_retail | other
  services text[] not null default '{}',      -- content, social, ads, seo, web, crm
  am_email text,                              -- staff email; the only email address stored anywhere
  timezone text not null default 'America/Chicago',
  ssp_client_contact_id text,                 -- this client's contact ID inside the SSP parent account
  is_parent boolean not null default false,
  active boolean not null default true,
  thresholds jsonb not null default '{}'::jsonb,
  mrr numeric,                                -- manual; monthly recurring revenue from this client
  contract_end date,                          -- manual
  token_status text not null default 'none' check (token_status in ('none','ok','invalid')),
  token_rotated_at date,
  last_token_error text,                      -- sanitized; never contains a token or header
  created_at timestamptz not null default now()
);

create table public.snapshots (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  snapshot_date date not null,
  captured_at timestamptz not null default now(),
  gate_passed boolean not null,
  coverage jsonb not null default '{}'::jsonb,
  -- leads and baseline
  leads_new_7d int, leads_trailing_avg numeric, trailing_n int, leads_delta_pct numeric,
  peer_median_delta_pct numeric, peer_n int,
  leads_by_source_7d jsonb, leads_by_source_trailing jsonb,
  leads_unassigned_7d int, leads_missing_phone_pct_7d numeric,
  form_submissions_7d int, form_submissions_trailing_avg numeric,
  convos_active_7d int, opps_created_7d int,
  -- speed to lead
  leads_uncontacted_24h int, leads_no_human_touch_7d int,
  speed_to_lead_median_min numeric, speed_to_lead_p90_min numeric, speed_kind_known boolean,
  excluded_count int,
  -- conversations
  convos_waiting int, convos_waiting_max_hours numeric,
  -- pipeline
  opps_open int, opps_open_value numeric, opps_stale int, opps_stale_value numeric,
  opps_stuck int, opps_missing_value int, opps_no_next_step int,
  opps_won_7d int, opps_lost_7d int,
  lead_to_opp_28d_pct numeric, win_rate_90d numeric, median_days_to_close_90d numeric,
  -- appointments
  appts_booked_7d int, appts_showed_28d int, appts_noshow_28d int, noshow_rate_28d numeric,
  -- delivery
  blogs_published_30d int, social_published_7d int, days_since_last_publish int,
  social_accounts_total int, social_accounts_expired int,
  -- relationship (from parent)
  invoices_past_due int, invoices_past_due_amount numeric,
  client_last_touch_days int, client_next_appt_at timestamptz,
  -- reviews proxies
  review_asks_stale int, review_ask_gap int,
  -- change tracking
  flags_new jsonb not null default '[]'::jsonb, flags_resolved jsonb not null default '[]'::jsonb,
  -- drilldown lists (section 7.5): names, IDs, timestamps, amounts, deep links only
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
  contact_name text, source text,             -- name only; never phone or email
  created_at timestamptz not null,
  first_outbound_at timestamptz, first_outbound_kind text,   -- human | automation | unknown
  first_human_touch_at timestamptz,
  first_touch_minutes numeric, first_human_touch_minutes numeric,
  updated_at timestamptz not null default now(),
  primary key (location_id, contact_id)
);
create index lead_events_loc_created on public.lead_events (location_id, created_at desc);

create table public.lead_history (
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  week_start date not null,                   -- Monday
  leads int not null,
  leads_by_source jsonb not null default '{}'::jsonb,
  form_submissions int,
  primary key (location_id, week_start)
);

create table public.flag_acks (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  code text not null,
  acked_by text not null,                     -- staff email, enforced equal to the JWT email by RLS
  note text,
  acked_at timestamptz not null default now(),
  snooze_until date not null default current_date + 7,
  constraint flag_acks_snooze_max check (snooze_until <= current_date + 90)   -- upper bound only; a lower bound on current_date would fail on dump/restore of expired rows
);
create index flag_acks_lookup on public.flag_acks (location_id, code, snooze_until desc);

create table public.account_notes (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  author text not null,                       -- staff email, enforced equal to the JWT email by RLS
  body text not null check (length(body) <= 4000),
  created_at timestamptz not null default now()
);
create index account_notes_loc on public.account_notes (location_id, created_at desc);

create table public.collector_runs (
  id bigserial primary key,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text,                                 -- ok | partial | failed
  locations_ok int default 0, locations_held int default 0, locations_failed int default 0,
  requests_made int, rate_limited int,
  details jsonb not null default '{}'::jsonb,  -- {location_id: {status, gate: [...], requests, rate_limited, seconds, error}} sanitized
  error text
);

-- ---------------------------------------------------------------------------
-- Views (security_invoker so RLS applies to the caller)
-- ---------------------------------------------------------------------------
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
  end as state
from public.subaccounts s
left join latest l on l.location_id = s.location_id
left join fl on fl.location_id = l.location_id and fl.snapshot_date = l.snapshot_date
where s.active;

create or replace view public.v_history with (security_invoker = on) as
select location_id, snapshot_date, gate_passed, leads_new_7d, leads_trailing_avg, leads_delta_pct,
       peer_median_delta_pct, convos_waiting, opps_stale, opps_open_value, days_since_last_publish,
       leads_uncontacted_24h, speed_to_lead_median_min
from public.snapshots
where snapshot_date >= current_date - 84;

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------
alter table public.subaccounts    enable row level security;
alter table public.snapshots      enable row level security;
alter table public.flags          enable row level security;
alter table public.lead_events    enable row level security;
alter table public.lead_history   enable row level security;
alter table public.flag_acks      enable row level security;
alter table public.account_notes  enable row level security;
alter table public.collector_runs enable row level security;
-- FORCE guards any future non-BYPASSRLS owner. On Supabase the postgres role has BYPASSRLS, which is why seed.sql
-- and the audit inserts inside the security-definer vault functions work without insert policies. service_role bypasses by design.
alter table public.subaccounts    force row level security;
alter table public.snapshots      force row level security;
alter table public.flags          force row level security;
alter table public.lead_events    force row level security;
alter table public.lead_history   force row level security;
alter table public.flag_acks      force row level security;
alter table public.account_notes  force row level security;
alter table public.collector_runs force row level security;

create or replace function public.is_staff() returns boolean
language sql stable security invoker set search_path = public as $$
  select coalesce((auth.jwt() ->> 'email') ilike '%@smallscreenproducer.com', false)
     and coalesce((auth.jwt() ->> 'role') = 'authenticated', false)
$$;

create policy staff_read_subaccounts on public.subaccounts    for select to authenticated using (public.is_staff());
create policy staff_read_snapshots   on public.snapshots      for select to authenticated using (public.is_staff());
create policy staff_read_flags       on public.flags          for select to authenticated using (public.is_staff());
create policy staff_read_lead_events on public.lead_events    for select to authenticated using (public.is_staff());
create policy staff_read_lead_hist   on public.lead_history   for select to authenticated using (public.is_staff());
create policy staff_read_acks        on public.flag_acks      for select to authenticated using (public.is_staff());
create policy staff_read_notes       on public.account_notes  for select to authenticated using (public.is_staff());
create policy staff_read_runs        on public.collector_runs for select to authenticated using (public.is_staff());
create policy staff_insert_acks  on public.flag_acks     for insert to authenticated
  with check (public.is_staff() and acked_by = (auth.jwt() ->> 'email'));
create policy staff_insert_notes on public.account_notes for insert to authenticated
  with check (public.is_staff() and author = (auth.jwt() ->> 'email'));
-- No update/delete policies anywhere for authenticated: acks and notes are append-only history.
-- No policies at all for anon: anon sees nothing.

-- ---------------------------------------------------------------------------
-- Auth hardening: staff domain enforced at the source (belt-and-braces; sign-up is also disabled in Auth settings).
-- This migration is run once; it is not idempotent (create table without if not exists). Re-running requires a fresh project or manual drops.
-- ---------------------------------------------------------------------------
create or replace function public.enforce_staff_domain() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.email is null or new.email not ilike '%@smallscreenproducer.com' then
    raise exception 'Sign-in restricted to smallscreenproducer.com accounts';
  end if;
  return new;
end $$;
create trigger enforce_staff_domain_ins before insert on auth.users
  for each row execute function public.enforce_staff_domain();
create trigger enforce_staff_domain_upd before update of email on auth.users
  for each row execute function public.enforce_staff_domain();

-- ---------------------------------------------------------------------------
-- Vault-backed PIT storage (section 8). Vault is enabled by default on Supabase projects;
-- if not: Dashboard → Project Settings → Vault → enable, then re-run this block.
-- ---------------------------------------------------------------------------
create table public.pit_audit (
  id bigserial primary key,
  location_id text,
  action text not null,           -- set | rotate | delete | read | read_denied
  actor text,                     -- 'collector' | 'cli' | null
  at timestamptz not null default now()
);
alter table public.pit_audit enable row level security;   -- no policies: invisible to API roles
alter table public.pit_audit force row level security;

-- collector_key bootstrap is done in the Vault UI (section 12): Name 'collector_key', Secret = generated string.

-- Key check. Returns boolean instead of raising so that a denied attempt can be audited.
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

-- Returns the token, or null when the key is wrong OR no token is stored.
create or replace function public.get_pit(p_location_id text, p_collector_key text) returns text
language plpgsql security definer set search_path = public, vault as $$
declare v_token text;
begin
  if not public.pit_key_ok(p_collector_key) then return null; end if;
  select decrypted_secret into v_token from vault.decrypted_secrets where name = 'ghl_pit_' || p_location_id;
  insert into public.pit_audit(location_id, action, actor) values (p_location_id, 'read', 'collector');
  return v_token;
end $$;

-- When was this token last set or rotated (works no matter how it was entered: Vault UI, SQL, or CLI)
create or replace function public.pit_updated_at(p_location_id text, p_collector_key text) returns timestamptz
language plpgsql security definer set search_path = public, vault as $$
declare v timestamptz;
begin
  if not public.pit_key_ok(p_collector_key) then return null; end if;
  select coalesce(updated_at, created_at) into v from vault.secrets where name = 'ghl_pit_' || p_location_id;
  return v;
end $$;

-- Optional programmatic set/rotate/delete for the CLI; the Vault UI is the primary path.
create or replace function public.set_pit(p_location_id text, p_token text, p_collector_key text) returns boolean
language plpgsql security definer set search_path = public, vault as $$
declare v_id uuid;
begin
  if not public.pit_key_ok(p_collector_key) then return false; end if;
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

create or replace function public.delete_pit(p_location_id text, p_collector_key text) returns boolean
language plpgsql security definer set search_path = public, vault as $$
begin
  if not public.pit_key_ok(p_collector_key) then return false; end if;
  delete from vault.secrets where name = 'ghl_pit_' || p_location_id;
  insert into public.pit_audit(location_id, action, actor) values (p_location_id, 'delete', 'cli');
  update public.subaccounts set token_status = 'none' where location_id = p_location_id;
  return true;
end $$;

-- ---------------------------------------------------------------------------
-- Least privilege: strip Supabase's default broad grants from the API roles, then grant back exactly what
-- the SPA needs. Runs last so it covers every object above. service_role is untouched (it bypasses RLS by design
-- and is used only by the collector).
-- ---------------------------------------------------------------------------
revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from anon, authenticated;
revoke execute on all functions in schema public from public;          -- Postgres grants EXECUTE to PUBLIC by default; remove it
alter default privileges in schema public revoke all on tables    from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke all on functions from anon, authenticated;
alter default privileges in schema public revoke execute on functions from public;  -- future functions start private

grant usage on schema public to anon, authenticated;   -- schema visibility only; anon holds no object privileges
grant select on public.subaccounts, public.snapshots, public.flags, public.lead_events, public.lead_history,
                public.flag_acks, public.account_notes, public.collector_runs,
                public.v_portfolio, public.v_history to authenticated;
grant insert on public.flag_acks, public.account_notes to authenticated;
grant usage, select on sequence public.flag_acks_id_seq, public.account_notes_id_seq to authenticated;
grant execute on function public.is_staff() to authenticated;

-- Vault RPCs: service_role only. (Redundant with the revoke-all above; explicit for readers.)
revoke all on function public.pit_key_ok(text) from public, anon, authenticated;
revoke all on function public.get_pit(text, text) from public, anon, authenticated;
revoke all on function public.pit_updated_at(text, text) from public, anon, authenticated;
revoke all on function public.set_pit(text, text, text) from public, anon, authenticated;
revoke all on function public.delete_pit(text, text) from public, anon, authenticated;
grant execute on function public.pit_key_ok(text) to service_role;
grant execute on function public.get_pit(text, text) to service_role;
grant execute on function public.pit_updated_at(text, text) to service_role;
grant execute on function public.set_pit(text, text, text) to service_role;
grant execute on function public.delete_pit(text, text) to service_role;

-- vault schema must never be reachable by API roles (already the Supabase default; explicit and tolerant if Vault is off)
do $$ begin
  execute 'revoke all on schema vault from anon, authenticated';
exception when others then null; end $$;

-- ---------------------------------------------------------------------------
-- Optional retention job. Run ONLY after enabling pg_cron (Dashboard → Integrations → Cron).
-- Kept commented so this migration runs cleanly without it.
-- ---------------------------------------------------------------------------
-- select cron.schedule('ghl-health-retention', '0 9 * * 0', $cron$
--   delete from public.snapshots      where snapshot_date < current_date - 400;
--   delete from public.flags          where snapshot_date < current_date - 400;
--   delete from public.lead_events    where created_at < now() - interval '180 days';
--   delete from public.collector_runs where started_at < now() - interval '180 days';
-- $cron$);
