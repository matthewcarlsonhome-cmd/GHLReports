-- Client dashboard access. No email, GHL writes, or automatic client activation.
-- Existing confirmed staff are captured once as explicit grants so applying
-- this migration does not silently remove their current portfolio access.
begin;
create table public.staff_access (
  user_id uuid primary key references auth.users(id) on delete cascade,
  active boolean not null default true,
  is_admin boolean not null default false,
  all_accounts boolean not null default false
);
insert into public.staff_access(user_id, all_accounts)
select id, true from auth.users
where email_confirmed_at is not null and lower(email) like '%@smallscreenproducer.com';

create table public.account_memberships (
  location_id text not null references public.subaccounts(location_id),
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null check (role in ('am','client_owner','client_viewer')),
  active boolean not null default true,
  granted_by uuid references auth.users(id),
  updated_at timestamptz not null default now(),
  primary key(location_id,user_id)
);
create index account_memberships_user on public.account_memberships(user_id,location_id);
create table public.client_report_settings (
  location_id text primary key references public.subaccounts(location_id),
  access_enabled boolean not null default false,
  publication_enabled boolean not null default false,
  enabled_views text[] not null default array['attention','week','month']::text[]
    check(enabled_views <@ array['attention','week','month']::text[] and cardinality(enabled_views)>0),
  stale_hours integer not null default 30 check(stale_hours between 1 and 168)
);
create table public.client_invitations (
  id uuid primary key default gen_random_uuid(),
  location_id text not null references public.subaccounts(location_id),
  email text not null check(email = lower(trim(email)) and email ~ '^[^[:space:]@,;<>]+@[^[:space:]@,;<>]+\.[^[:space:]@,;<>]+$'),
  role text not null check(role in ('client_owner','client_viewer')),
  token_hash text not null unique,
  expires_at timestamptz not null,
  accepted_at timestamptz,
  revoked_at timestamptz,
  invited_by uuid not null references auth.users(id)
);
create table public.client_reports (
  location_id text not null references public.subaccounts(location_id),
  period_kind text not null check(period_kind in ('attention','week','month')),
  period_start date not null,
  generated_at timestamptz not null,
  data jsonb not null check(jsonb_typeof(data) = 'object'),
  primary key(location_id,period_kind,period_start,generated_at)
);
create table public.access_audit (
  id bigint generated always as identity primary key,
  actor uuid,
  location_id text,
  action text not null,
  target_id text,
  at timestamptz not null default now()
);

-- Empty search paths and fully qualified references protect privileged helpers.
create or replace function public.is_staff() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from public.staff_access where user_id=auth.uid() and active)
$$;
create function public.dashboard_admin() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from public.staff_access where user_id=auth.uid() and active and is_admin)
$$;
create function public.staff_account(p_location text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from public.staff_access s where s.user_id=auth.uid() and s.active
    and (s.is_admin or s.all_accounts or exists(select 1 from public.account_memberships m
      where m.user_id=s.user_id and m.location_id=p_location and m.active and m.role='am')))
$$;
create function public.client_account(p_location text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from public.subaccounts a
    join public.client_report_settings c using(location_id)
    join public.account_memberships m using(location_id)
    where a.location_id=p_location and a.active and c.access_enabled and c.publication_enabled
      and m.user_id=auth.uid() and m.active and m.role in ('client_owner','client_viewer'))
$$;
create function public.require_dashboard_admin() returns void
language plpgsql stable security definer set search_path = '' as $$
begin
  if not public.dashboard_admin() or coalesce(auth.jwt()->>'aal','') <> 'aal2' then
    raise exception 'Administrator verification required' using errcode='42501';
  end if;
end $$;

-- Restrictive policies also constrain the existing permissive staff policies.
do $$ declare t text; begin
  foreach t in array array['subaccounts','snapshots','flags','lead_events','lead_history',
    'flag_acks','account_notes','form_health','tag_checks','automation_sends','alert_state'] loop
    execute format('create policy explicit_account_scope on public.%I as restrictive for all to authenticated using (public.staff_account(location_id)) with check (public.staff_account(location_id))',t);
  end loop;
end $$;
create policy explicit_run_scope on public.collector_runs as restrictive for select to authenticated
using (public.dashboard_admin() or exists(select 1 from public.staff_access where user_id=auth.uid() and active and all_accounts));

do $$ declare t text; begin
  foreach t in array array['staff_access','account_memberships','client_report_settings','client_invitations','client_reports','access_audit'] loop
    execute format('alter table public.%I enable row level security',t);
    execute format('alter table public.%I force row level security',t);
    execute format('revoke all on public.%I from public, anon, authenticated',t);
    execute format('grant all on public.%I to service_role',t);
  end loop;
end $$;
grant usage, select on sequence public.access_audit_id_seq to service_role;
create policy staff_self on public.staff_access for select to authenticated using(user_id=auth.uid());
grant select on public.staff_access to authenticated;
create policy read_client_reports on public.client_reports for select to authenticated
using(public.client_account(location_id) or public.dashboard_admin());
grant select on public.client_reports to authenticated;

-- Returning only safe business metadata avoids giving clients subaccounts access.
create function public.dashboard_accounts() returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object('location_id',a.location_id,'name',a.name,
    'timezone',a.timezone,'stale_hours',coalesce(c.stale_hours,30),
    'enabled_views',coalesce(c.enabled_views,array['attention','week','month']::text[])) order by a.name),'[]'::jsonb)
  from public.subaccounts a left join public.client_report_settings c using(location_id)
  where a.active and (public.client_account(a.location_id) or public.dashboard_admin())
$$;
create function public.dashboard_identity() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('staff',public.is_staff(),'admin',public.dashboard_admin())
$$;
create function public.dashboard_reports(p_location text) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  if not (public.client_account(p_location) or public.dashboard_admin()) then
    return '[]'::jsonb;
  end if;
  insert into public.access_audit(actor,location_id,action) values(auth.uid(),p_location,
    case when public.dashboard_admin() then 'preview_client' else 'read_client_report' end);
  return (select coalesce(jsonb_agg(to_jsonb(r) || jsonb_build_object('previous_complete',(
    select to_jsonb(v) from (
      select period_kind,period_start,generated_at,data from public.client_reports old
      where old.location_id=p_location and old.period_kind=r.period_kind and old.period_start=r.period_start
        and old.generated_at<r.generated_at
        and old.data#>>'{coverage,contacts}'='true' and old.data#>>'{coverage,responses}'='true'
        and (old.period_kind<>'attention' or old.data#>>'{coverage,conversations}'='true')
      order by generated_at desc limit 1
    ) v
  ))),'[]') from (
    select distinct on(period_kind,period_start) period_kind,period_start,generated_at,data
    from public.client_reports where location_id=p_location
    order by period_kind,period_start,generated_at desc) r);
end $$;

-- Bootstrap requires the operator to supply a verified UUID; it never trusts a
-- browser email hint. API roles cannot call this function.
create function public.bootstrap_dashboard_admin(p_user uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not exists(select 1 from auth.users where id=p_user and email_confirmed_at is not null
      and lower(email)='mcarlson@smallscreenproducer.com') then
    raise exception 'Verified owner identity required';
  end if;
  insert into public.staff_access values(p_user,true,true,true)
  on conflict(user_id) do update set active=true,is_admin=true,all_accounts=true;
  insert into public.access_audit(actor,action,target_id) values(p_user,'bootstrap_admin',p_user::text);
end $$;

create function public.dashboard_settings(p_location text, p_access boolean, p_publish boolean, p_stale integer) returns void
language plpgsql security definer set search_path = '' as $$
begin
  perform public.require_dashboard_admin();
  insert into public.client_report_settings(location_id,access_enabled,publication_enabled,stale_hours) values(p_location,p_access,p_publish,p_stale)
  on conflict(location_id) do update set access_enabled=excluded.access_enabled,
    publication_enabled=excluded.publication_enabled,stale_hours=excluded.stale_hours;
  insert into public.access_audit(actor,location_id,action,target_id)
    values(auth.uid(),p_location,'report_settings',concat(p_access,':',p_publish,':',p_stale));
end $$;
create function public.dashboard_report_preferences(p_location text,p_timezone text,p_views text[]) returns void
language plpgsql security definer set search_path = '' as $$
begin
  perform public.require_dashboard_admin();
  if not exists(select 1 from pg_catalog.pg_timezone_names where name=p_timezone) then
    raise exception 'Choose a valid account timezone';
  end if;
  insert into public.client_report_settings(location_id,enabled_views) values(p_location,p_views)
  on conflict(location_id) do update set enabled_views=excluded.enabled_views;
  update public.subaccounts set timezone=p_timezone where location_id=p_location;
  insert into public.access_audit(actor,location_id,action,target_id)
    values(auth.uid(),p_location,'report_preferences',concat(p_timezone,':',array_to_string(p_views,',')));
end $$;
create function public.dashboard_membership(p_location text,p_user uuid,p_role text,p_active boolean) returns void
language plpgsql security definer set search_path = '' as $$
begin
  perform public.require_dashboard_admin();
  if p_role='am' and not exists(select 1 from public.staff_access where user_id=p_user and active) then
    raise exception 'Explicit staff grant required';
  end if;
  if p_active and not exists(select 1 from auth.users where id=p_user and email_confirmed_at is not null) then
    raise exception 'Verified identity required';
  end if;
  insert into public.account_memberships values(p_location,p_user,p_role,p_active,auth.uid(),now())
  on conflict(location_id,user_id) do update set role=excluded.role,active=excluded.active,
    granted_by=auth.uid(),updated_at=now();
  insert into public.access_audit(actor,location_id,action,target_id)
    values(auth.uid(),p_location,case when p_active then 'grant_membership' else 'revoke_membership' end,p_user::text);
end $$;
create function public.dashboard_invite(p_location text,p_email text,p_role text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare token text := gen_random_uuid()::text || gen_random_uuid()::text; invitation uuid;
begin
  perform public.require_dashboard_admin();
  insert into public.client_invitations(location_id,email,role,token_hash,expires_at,invited_by)
    values(p_location,lower(trim(p_email)),p_role,encode(sha256(convert_to(token,'UTF8')),'hex'),now()+interval '7 days',auth.uid()) returning id into invitation;
  insert into public.access_audit(actor,location_id,action,target_id) values(auth.uid(),p_location,'create_invitation',invitation::text);
  return jsonb_build_object('id',invitation,'token',token);
end $$;
create function public.dashboard_revoke_invite(p_id uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
  perform public.require_dashboard_admin();
  update public.client_invitations set revoked_at=now() where id=p_id;
  insert into public.access_audit(actor,action,target_id) values(auth.uid(),'revoke_invitation',p_id::text);
end $$;
create function public.dashboard_accept_invite(p_token text) returns void
language plpgsql security definer set search_path = '' as $$
declare i public.client_invitations;
begin
  select * into i from public.client_invitations where token_hash=encode(sha256(convert_to(p_token,'UTF8')),'hex')
    and expires_at>now() and accepted_at is null and revoked_at is null for update;
  if i.id is null or not exists(select 1 from auth.users where id=auth.uid()
      and email_confirmed_at is not null and lower(email)=i.email) then
    raise exception 'Invitation unavailable' using errcode='42501';
  end if;
  insert into public.account_memberships values(i.location_id,auth.uid(),i.role,true,i.invited_by,now())
  on conflict(location_id,user_id) do update set role=excluded.role,active=true,granted_by=excluded.granted_by,updated_at=now();
  update public.client_invitations set accepted_at=now() where id=i.id;
  insert into public.access_audit(actor,location_id,action,target_id) values(auth.uid(),i.location_id,'accept_invitation',i.id::text);
end $$;
-- Permit only an explicitly invited external identity to be provisioned.
-- Public signup must remain disabled in Auth; provisioning is a server operation.
create or replace function public.enforce_staff_domain() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if exists(select 1 from public.staff_access where user_id=new.id and active)
      and lower(new.email) like '%@smallscreenproducer.com' then return new; end if;
  if exists(select 1 from public.client_invitations where email=lower(new.email)
      and expires_at>now() and accepted_at is null and revoked_at is null) then return new; end if;
  raise exception 'Approved invitation required';
end $$;
create function public.dashboard_admin_state() returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  perform public.require_dashboard_admin();
  return jsonb_build_object(
    'staff',(select coalesce(jsonb_agg(to_jsonb(s)||jsonb_build_object('email',u.email)),'[]') from public.staff_access s join auth.users u on u.id=s.user_id),
    'settings',(select coalesce(jsonb_agg(to_jsonb(c)),'[]') from public.client_report_settings c),
    'reports',(select coalesce(jsonb_agg(to_jsonb(r)),'[]') from (
      select distinct on(location_id) location_id,generated_at,data->'coverage' as coverage
      from public.client_reports where period_kind='attention' order by location_id,generated_at desc) r),
    'memberships',(select coalesce(jsonb_agg(to_jsonb(m)||jsonb_build_object('email',u.email)),'[]') from public.account_memberships m join auth.users u on u.id=m.user_id),
    'invitations',(select coalesce(jsonb_agg(jsonb_build_object('id',i.id,'location_id',i.location_id,'email',i.email,'expires_at',i.expires_at,'accepted_at',i.accepted_at,'revoked_at',i.revoked_at)),'[]') from public.client_invitations i),
    'audit',(select coalesce(jsonb_agg(to_jsonb(a)),'[]') from (select * from public.access_audit order by at desc limit 100) a));
end $$;
create function public.dashboard_staff(p_user uuid,p_active boolean,p_all boolean) returns void
language plpgsql security definer set search_path = '' as $$
begin
  perform public.require_dashboard_admin();
  if exists(select 1 from public.staff_access where user_id=p_user and is_admin) then
    raise exception 'Owner grants require operator review';
  end if;
  if not exists(select 1 from auth.users where id=p_user and email_confirmed_at is not null
    and lower(email) like '%@smallscreenproducer.com') then raise exception 'Verified staff identity required'; end if;
  insert into public.staff_access(user_id,active,all_accounts) values(p_user,p_active,p_all)
  on conflict(user_id) do update set active=excluded.active,all_accounts=excluded.all_accounts;
  insert into public.access_audit(actor,action,target_id) values(auth.uid(),'staff_scope',p_user::text);
end $$;
create function public.dashboard_preview(p_location text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  perform public.require_dashboard_admin();
  insert into public.access_audit(actor,location_id,action) values(auth.uid(),p_location,'preview_client');
end $$;

-- New functions are closed by default in this project. Spell out each grant.
do $$ declare f record; begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and (p.proname like 'dashboard_%' or p.proname in
      ('staff_account','client_account','require_dashboard_admin','bootstrap_dashboard_admin','is_staff','enforce_staff_domain')) loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    execute format('grant execute on function %s to service_role',f.signature);
    if f.signature::text not like '%bootstrap_dashboard_admin%' and f.signature::text not like '%enforce_staff_domain%' then
      execute format('grant execute on function %s to authenticated',f.signature);
    end if;
  end loop;
end $$;
commit;
