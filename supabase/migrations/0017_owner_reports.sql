-- Weekly owner reporting. All delivery defaults off; AM paths are unchanged.
begin;
create table public.owner_report_control (
  singleton boolean primary key default true check(singleton),
  mode text not null default 'off' check(mode in ('off','preview','live')),
  pilot_locations text[] not null default '{}',
  daily_cap integer not null default 1 check(daily_cap between 1 and 100),
  heartbeat_at timestamptz
);
insert into public.owner_report_control(singleton) values(true);
create table public.owner_report_settings (
  location_id text primary key references public.subaccounts(location_id),
  topics text[] not null default array['follow_up','waiting','speed','ownership','lead_flow']
    check(cardinality(topics)>0 and topics <@ array['follow_up','waiting','speed','ownership','lead_flow','pipeline']::text[]),
  weekday integer not null default 0 check(weekday between 0 and 6),
  local_time time not null default '08:00',
  delivery_mode text not null default 'off' check(delivery_mode in ('off','preview','live')),
  revision integer not null default 1,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id)
);
create table public.owner_report_recipients (
  location_id text primary key references public.owner_report_settings(location_id),
  user_id uuid not null references auth.users(id),
  ghl_user_id text not null check(length(ghl_user_id) between 1 and 100),
  email text not null check(email=lower(trim(email)) and email ~ '^[^[:space:]@,;<>]+@[^[:space:]@,;<>]+\.[^[:space:]@,;<>]+$'),
  version integer not null default 1,
  verified_at timestamptz,
  verified_by uuid references auth.users(id)
);
create table public.owner_report_destinations (
  location_id text primary key references public.owner_report_settings(location_id),
  workflow_id text not null check(length(workflow_id) between 1 and 100),
  workflow_version text not null check(length(workflow_version) between 1 and 100),
  secret_name text,
  version integer not null default 1,
  adapter_verified_at timestamptz,
  verified_by uuid references auth.users(id),
  evidence text check(length(evidence)<=500)
);
create table public.owner_issue_observations (
  location_id text not null references public.subaccounts(location_id),
  observed_on date not null,
  topic text not null check(topic in ('follow_up','waiting','ownership','lead_flow','pipeline')),
  valid boolean not null, present boolean not null, qualifying boolean not null,
  severity text not null check(severity in ('red','amber')), count numeric,
  primary key(location_id,observed_on,topic)
);
-- Safe timestamps retained prospectively. No lead names/message bodies.
create table public.owner_response_history (
  location_id text not null references public.subaccounts(location_id), contact_id text not null,
  created_at timestamptz not null, first_outbound_at timestamptz, first_human_touch_at timestamptz,
  first_outbound_kind text, first_touch_minutes numeric, first_human_touch_minutes numeric,
  observed_through timestamptz not null, source_complete boolean not null,
  primary key(location_id,contact_id)
);
create table public.owner_report_snapshots (
  id uuid primary key default gen_random_uuid(),
  location_id text not null references public.subaccounts(location_id),
  period_kind text not null check(period_kind in ('attention','week','month')),
  period_start date not null, period_end timestamptz not null,
  generated_at timestamptz not null, data jsonb not null check(jsonb_typeof(data)='object'),
  unique(location_id,period_kind,period_start,generated_at)
);
create table public.owner_report_outbox (
  id uuid primary key default gen_random_uuid(),
  location_id text not null references public.owner_report_settings(location_id),
  report_id uuid references public.owner_report_snapshots(id),
  period_start date not null, period_end timestamptz not null, timezone text not null,
  mode text not null check(mode in ('test','live')),
  due_at timestamptz not null, expires_at timestamptz not null,
  recipient_version integer not null, destination_version integer not null, settings_revision integer not null,
  status text not null default 'queued' check(status in ('prepared','queued','held','dispatching','accepted','claimed','notification_action_recorded','failed','unknown','skipped','paused')),
  reason text, content jsonb, content_hash text,
  claim_hash text, completion_hash text,
  dispatch_at timestamptz, claimed_at timestamptz, completed_at timestamptz,
  approved_at timestamptz, approved_by uuid references auth.users(id),
  inbox_verified_at timestamptz,
  created_at timestamptz not null default now()
);
-- No second weekly email on recipient, destination, report or timezone edits.
create unique index owner_live_week_once on public.owner_report_outbox(location_id,period_start) where mode='live';
create table public.owner_report_attempts (
  id bigint generated always as identity primary key,
  delivery_id uuid not null references public.owner_report_outbox(id),
  at timestamptz not null default now(), status text not null, http_status integer,
  reason text not null
);

do $$ declare t text; begin
 foreach t in array array['owner_report_control','owner_report_settings','owner_report_recipients',
 'owner_report_destinations','owner_issue_observations','owner_response_history','owner_report_snapshots',
 'owner_report_outbox','owner_report_attempts'] loop
  execute format('alter table public.%I enable row level security',t);
  execute format('alter table public.%I force row level security',t);
  execute format('revoke all on public.%I from public,anon,authenticated',t);
  execute format('grant all on public.%I to service_role',t);
 end loop;
end $$;
grant usage,select on sequence public.owner_report_attempts_id_seq to service_role;
grant select on public.owner_report_snapshots to authenticated;
create policy owner_safe_reports on public.owner_report_snapshots for select to authenticated
using(public.client_account(location_id) or public.dashboard_admin());

create function public.owner_reports(p_location text,p_report uuid default null) returns jsonb
language plpgsql security definer set search_path='' as $$
begin
 if not (public.client_account(p_location) or public.dashboard_admin()) then return '[]'; end if;
 insert into public.access_audit(actor,location_id,action) values(auth.uid(),p_location,'read_owner_report');
 if p_report is not null then
  return (select coalesce(jsonb_agg(to_jsonb(r)),'[]') from public.owner_report_snapshots r where location_id=p_location and id=p_report);
 end if;
 return (select coalesce(jsonb_agg(to_jsonb(r) order by r.period_start desc),'[]') from
  (select distinct on(period_kind,period_start) * from public.owner_report_snapshots
   where location_id=p_location and coalesce((data->>'sample')::boolean,false)=false order by period_kind,period_start,generated_at desc) r);
end $$;

create function public.owner_admin_state(p_location text) returns jsonb
language plpgsql security definer set search_path='' as $$
begin
 perform public.require_dashboard_admin();
 return jsonb_build_object(
 'settings',(select to_jsonb(s) from public.owner_report_settings s where location_id=p_location),
 'recipient',(select to_jsonb(r) from public.owner_report_recipients r where location_id=p_location),
 'destination',(select to_jsonb(d)-'secret_name'||jsonb_build_object('secret_configured',d.secret_name is not null)
                from public.owner_report_destinations d where location_id=p_location),
 'control',(select to_jsonb(c) from public.owner_report_control c),
 'readiness',jsonb_build_object(
   'publication',exists(select 1 from public.client_report_settings where location_id=p_location and publication_enabled),
   'access',exists(select 1 from public.client_report_settings where location_id=p_location and access_enabled),
   'owner',exists(select 1 from public.owner_report_recipients r join public.account_memberships m using(location_id,user_id)
       join auth.users u on u.id=r.user_id where r.location_id=p_location and r.verified_at is not null and m.active
       and m.role='client_owner' and u.email_confirmed_at is not null and lower(u.email)=r.email),
   'workflow',exists(select 1 from public.owner_report_destinations where location_id=p_location and adapter_verified_at is not null and secret_name is not null),
   'fresh_report',exists(select 1 from public.owner_report_snapshots r join public.client_report_settings c using(location_id)
       where r.location_id=p_location and r.period_kind='week' and coalesce((r.data->>'sample')::boolean,false)=false
       and (r.data->>'current_as_of')::timestamptz >= now()-make_interval(hours=>c.stale_hours)),
   'accepted_test',exists(select 1 from public.owner_report_outbox q join public.owner_report_recipients r using(location_id)
       join public.owner_report_destinations d using(location_id) where q.location_id=p_location and q.mode='test'
       and q.inbox_verified_at is not null and q.status='notification_action_recorded'
       and q.recipient_version=r.version and q.destination_version=d.version)),
 'history',(select coalesce(jsonb_agg(to_jsonb(q)),'[]') from (select id,period_start,mode,status,reason,due_at,dispatch_at,completed_at,inbox_verified_at
        from public.owner_report_outbox where location_id=p_location order by created_at desc limit 30) q),
 'members',(select coalesce(jsonb_agg(jsonb_build_object('user_id',m.user_id,'email',u.email)),'[]')
     from public.account_memberships m join auth.users u on u.id=m.user_id
     where m.location_id=p_location and m.active and m.role='client_owner' and u.email_confirmed_at is not null),
 'reports',(select coalesce(jsonb_agg(to_jsonb(x)),'[]') from (select id,period_start,data->>'state' as state,generated_at
       from public.owner_report_snapshots where location_id=p_location and period_kind='week' order by generated_at desc limit 5) x));
end $$;

create function public.owner_save_settings(p_location text,p_topics text[],p_weekday integer,p_time time,p_mode text) returns void
language plpgsql security definer set search_path='' as $$
begin
 perform public.require_dashboard_admin();
 if p_mode='live' and not (
   exists(select 1 from public.owner_report_recipients where location_id=p_location and verified_at is not null)
   and exists(select 1 from public.owner_report_destinations where location_id=p_location and adapter_verified_at is not null and secret_name is not null)
   and exists(select 1 from public.client_report_settings where location_id=p_location and access_enabled and publication_enabled)
   and exists(select 1 from public.owner_report_outbox q
     join public.owner_report_recipients r using(location_id)
     join public.owner_report_destinations d using(location_id)
     where q.location_id=p_location and q.mode='test' and q.status='notification_action_recorded'
       and q.inbox_verified_at is not null and q.recipient_version=r.version and q.destination_version=d.version)
 ) then raise exception 'Complete recipient, workflow and approved test verification first'; end if;
 insert into public.owner_report_settings(location_id,topics,weekday,local_time,delivery_mode,updated_by)
 values(p_location,p_topics,p_weekday,p_time,p_mode,auth.uid())
 on conflict(location_id) do update set topics=excluded.topics,weekday=excluded.weekday,local_time=excluded.local_time,
 delivery_mode=excluded.delivery_mode,revision=owner_report_settings.revision+1,updated_at=now(),updated_by=auth.uid();
 update public.owner_report_outbox set status='paused',reason='Settings changed'
 where location_id=p_location and status in ('prepared','queued','held');
 insert into public.access_audit(actor,location_id,action) values(auth.uid(),p_location,'owner_settings');
end $$;

create function public.owner_save_binding(p_location text,p_user uuid,p_ghl_user text,p_email text,p_workflow text,p_version text) returns void
language plpgsql security definer set search_path='' as $$
begin
 perform public.require_dashboard_admin();
 if not exists(select 1 from public.account_memberships m join auth.users u on u.id=m.user_id
   where m.location_id=p_location and m.user_id=p_user and m.active and m.role='client_owner'
   and u.email_confirmed_at is not null and lower(u.email)=lower(trim(p_email))) then
   raise exception 'Verified client-owner membership and matching email required';
 end if;
 insert into public.owner_report_settings(location_id) values(p_location) on conflict do nothing;
 insert into public.owner_report_recipients(location_id,user_id,ghl_user_id,email)
 values(p_location,p_user,trim(p_ghl_user),lower(trim(p_email)))
 on conflict(location_id) do update set user_id=excluded.user_id,ghl_user_id=excluded.ghl_user_id,email=excluded.email,
 version=owner_report_recipients.version+1,verified_at=null,verified_by=null;
 insert into public.owner_report_destinations(location_id,workflow_id,workflow_version)
 values(p_location,trim(p_workflow),trim(p_version))
 on conflict(location_id) do update set workflow_id=excluded.workflow_id,workflow_version=excluded.workflow_version,
 version=owner_report_destinations.version+1,adapter_verified_at=null,verified_by=null,evidence=null,secret_name=null;
 update public.owner_report_settings set delivery_mode='preview',revision=revision+1 where location_id=p_location;
 update public.owner_report_outbox set status='paused',reason='Recipient or workflow changed'
 where location_id=p_location and status in ('prepared','queued','held');
 insert into public.access_audit(actor,location_id,action) values(auth.uid(),p_location,'owner_binding_changed');
end $$;

-- Protected write-only secret operation. Never return a stored URL.
create function public.owner_save_destination(p_location text,p_url text) returns void
language plpgsql security definer set search_path='' as $$
declare v_name text; secret_id uuid;
begin
 perform public.require_dashboard_admin();
 if p_url !~ '^https://services[.]leadconnectorhq[.]com/hooks/[A-Za-z0-9_-]+/webhook-trigger/[A-Za-z0-9_-]+$'
 then raise exception 'Use the approved GHL inbound webhook endpoint'; end if;
 if not exists(select 1 from public.owner_report_destinations where location_id=p_location) then raise exception 'Save account binding first'; end if;
 v_name:='owner_report_'||encode(sha256(convert_to(p_location,'UTF8')),'hex');
 select id into secret_id from vault.secrets where name=v_name;
 if secret_id is null then perform vault.create_secret(p_url,v_name,'Owner report destination');
 else perform vault.update_secret(secret_id,p_url); end if;
 update public.owner_report_destinations set secret_name=v_name,version=version+1,adapter_verified_at=null where location_id=p_location;
 update public.owner_report_settings set delivery_mode='preview',revision=revision+1 where location_id=p_location;
 insert into public.access_audit(actor,location_id,action) values(auth.uid(),p_location,'owner_destination_changed');
end $$;

create function public.owner_verify_binding(p_location text,p_evidence text) returns void
language plpgsql security definer set search_path='' as $$
begin
 perform public.require_dashboard_admin();
 if length(trim(p_evidence))<20 or length(p_evidence)>500 then raise exception 'Record account, selected user and claim/render verification evidence'; end if;
 update public.owner_report_recipients set verified_at=now(),verified_by=auth.uid() where location_id=p_location;
 update public.owner_report_destinations set adapter_verified_at=now(),verified_by=auth.uid(),evidence=p_evidence
 where location_id=p_location and secret_name is not null;
 insert into public.access_audit(actor,location_id,action) values(auth.uid(),p_location,'owner_adapter_verified');
end $$;

-- Only the SSP test recipient can be prepared; this does not send anything.
create function public.owner_prepare_test(p_location text) returns uuid
language plpgsql security definer set search_path='' as $$
declare result uuid; r public.owner_report_recipients; d public.owner_report_destinations; s public.owner_report_settings;
begin
 perform public.require_dashboard_admin();
 select * into r from public.owner_report_recipients where location_id=p_location;
 select * into d from public.owner_report_destinations where location_id=p_location;
 select * into s from public.owner_report_settings where location_id=p_location;
 if not exists(select 1 from public.subaccounts where location_id=p_location and is_parent)
 or r.email is distinct from 'mcarlson@smallscreenproducer.com'
 or r.verified_at is null or d.adapter_verified_at is null then raise exception 'Verified SSP test route required'; end if;
 insert into public.owner_report_outbox(location_id,period_start,period_end,timezone,mode,due_at,expires_at,
 recipient_version,destination_version,settings_revision,status)
 values(p_location,current_date,now(),'America/Chicago','test',now(),now()+interval '24 hours',
 r.version,d.version,s.revision,'prepared') returning id into result;
 insert into public.access_audit(actor,location_id,action,target_id) values(auth.uid(),p_location,'owner_test_prepared',result::text);
 return result;
end $$;

create function public.owner_approve_test(p_id uuid) returns void
language plpgsql security definer set search_path='' as $$
begin
 perform public.require_dashboard_admin();
 update public.owner_report_outbox set status='queued',approved_at=now(),approved_by=auth.uid()
 where id=p_id and mode='test' and status='prepared' and expires_at>now();
 if not found then raise exception 'Prepare a fresh test first'; end if;
 insert into public.access_audit(actor,action,target_id) values(auth.uid(),'owner_test_approved',p_id::text);
end $$;

create function public.owner_route_ready(p_location text,p_test boolean default false) returns boolean
language sql stable security definer set search_path='' as $$
 select exists(select 1 from public.owner_report_settings s
 join public.owner_report_recipients r using(location_id)
 join public.owner_report_destinations d using(location_id)
 join public.client_report_settings c using(location_id)
 join public.subaccounts a using(location_id)
 join auth.users u on u.id=r.user_id
 join public.account_memberships m on m.location_id=s.location_id and m.user_id=r.user_id
 where s.location_id=p_location and a.active and c.publication_enabled
 and (p_test or c.access_enabled) and s.delivery_mode<>'off' and (p_test or s.delivery_mode='live')
 and r.verified_at is not null and d.adapter_verified_at is not null and d.secret_name is not null
 and m.active and m.role='client_owner' and u.email_confirmed_at is not null and lower(u.email)=r.email
 and (not p_test or (a.is_parent and r.email='mcarlson@smallscreenproducer.com')))
$$;

create function public.owner_accept_test(p_id uuid) returns void
language plpgsql security definer set search_path='' as $$
begin
 perform public.require_dashboard_admin();
 update public.owner_report_outbox set inbox_verified_at=now()
 where id=p_id and mode='test' and status='notification_action_recorded';
 if not found then raise exception 'Completed approved test required'; end if;
 insert into public.access_audit(actor,action,target_id) values(auth.uid(),'owner_inbox_verified',p_id::text);
end $$;

-- Worker begins with an operator-selected mode and a short runtime lease.
create function public.owner_heartbeat(p_mode text,p_pilots text[],p_cap integer) returns void
language sql security definer set search_path='' as $$
 update public.owner_report_control set mode=p_mode,pilot_locations=p_pilots,daily_cap=p_cap,heartbeat_at=now()
$$;

create function public.owner_enqueue(p_row jsonb) returns uuid
language plpgsql security definer set search_path='' as $$
declare result uuid;
begin
 insert into public.owner_report_outbox(location_id,period_start,period_end,timezone,mode,due_at,expires_at,
 recipient_version,destination_version,settings_revision)
 values(p_row->>'location_id',(p_row->>'period_start')::date,(p_row->>'period_end')::timestamptz,
 p_row->>'timezone','live',(p_row->>'due_at')::timestamptz,(p_row->>'expires_at')::timestamptz,
 (p_row->>'recipient_version')::integer,(p_row->>'destination_version')::integer,(p_row->>'settings_revision')::integer)
 on conflict(location_id,period_start) where mode='live' do nothing returning id into result;
 return result;
end $$;

create function public.owner_dispatch(p_id uuid,p_claim_hash text,p_completion_hash text,p_content jsonb,p_hash text,p_report uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare report public.owner_report_snapshots; q public.owner_report_outbox; c public.owner_report_control; r public.owner_report_recipients; d public.owner_report_destinations; s public.owner_report_settings; target text;
begin
 select * into c from public.owner_report_control where singleton for update;
 select * into q from public.owner_report_outbox where id=p_id for update;
 if q.id is null or q.status not in ('queued','held') or q.expires_at<=now() or q.due_at>now()
 or c.mode='off' or c.heartbeat_at is null or c.heartbeat_at<now()-interval '20 minutes'
 or not public.owner_route_ready(q.location_id,q.mode='test')
 or (q.mode='live' and (c.mode<>'live' or not(q.location_id=any(c.pilot_locations))))
 or (q.mode='test' and q.approved_at is null)
 then return null; end if;
 select * into r from public.owner_report_recipients where location_id=q.location_id;
 select * into d from public.owner_report_destinations where location_id=q.location_id;
 select * into s from public.owner_report_settings where location_id=q.location_id;
 if r.version<>q.recipient_version or d.version<>q.destination_version or s.revision<>q.settings_revision then
  update public.owner_report_outbox set status='paused',reason='Binding changed' where id=p_id; return null;
 end if;
 if (select count(*) from public.owner_report_outbox where dispatch_at>=date_trunc('day',now()))>=c.daily_cap then return null; end if;
 select * into report from public.owner_report_snapshots where id=p_report and location_id=q.location_id;
 if report.id is null or report.period_kind<>'week'
 or (q.mode='live' and (report.period_start<>q.period_start or report.period_end<>q.period_end
      or coalesce((report.data->>'sample')::boolean,false) or coalesce((report.data->>'to_date')::boolean,true)))
 or (q.mode='test' and coalesce((report.data->>'sample')::boolean,false)=false)
 then return null; end if;
 if p_claim_hash !~ '^[a-f0-9]{64}$' or p_completion_hash !~ '^[a-f0-9]{64}$'
 or octet_length(p_content::text)>96000 then raise exception 'Invalid envelope'; end if;
 select decrypted_secret into target from vault.decrypted_secrets where name=d.secret_name;
 if target is null then return null; end if;
 update public.owner_report_outbox set status='dispatching',report_id=p_report,dispatch_at=now(),content=p_content,content_hash=p_hash,
 claim_hash=p_claim_hash,completion_hash=p_completion_hash where id=p_id;
 return jsonb_build_object('url',target,'workflow',d.workflow_id,'recipient_version',r.version);
end $$;

create function public.owner_claim(p_id uuid,p_location text,p_workflow text,p_recipient integer,p_mode text,p_hash text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare q public.owner_report_outbox; r public.owner_report_recipients; d public.owner_report_destinations; s public.owner_report_settings; c public.owner_report_control;
begin
 select * into q from public.owner_report_outbox where id=p_id for update;
 select * into c from public.owner_report_control where singleton;
 select * into r from public.owner_report_recipients where location_id=p_location;
 select * into d from public.owner_report_destinations where location_id=p_location;
 select * into s from public.owner_report_settings where location_id=p_location;
 if q.id is null or q.location_id<>p_location or q.mode<>p_mode or q.claim_hash is distinct from p_hash
 or q.status not in ('dispatching','accepted','unknown') or q.claimed_at is not null or q.expires_at<=now()
 or d.workflow_id is distinct from p_workflow or r.version is distinct from p_recipient
 or r.version<>q.recipient_version or d.version<>q.destination_version or s.revision<>q.settings_revision
 or c.mode='off' or c.heartbeat_at is null or c.heartbeat_at<now()-interval '20 minutes'
 or (q.mode='live' and (c.mode<>'live' or not(p_location=any(c.pilot_locations))))
 or (q.mode='test' and q.approved_at is null)
 or not public.owner_route_ready(p_location,q.mode='test') then return jsonb_build_object('allow_send','false'); end if;
 update public.owner_report_outbox set claimed_at=now(),status='claimed',claim_hash=null where id=p_id;
 insert into public.owner_report_attempts(delivery_id,status,reason) values(p_id,'claimed','One-use claim consumed');
 return q.content||jsonb_build_object('allow_send','true');
end $$;

create function public.owner_result(p_id uuid,p_hash text,p_result text) returns boolean
language plpgsql security definer set search_path='' as $$
begin
 if p_result not in ('notification_action_recorded','failed') then return false; end if;
 update public.owner_report_outbox set status=p_result,completed_at=now(),completion_hash=null
 where id=p_id and status='claimed' and completion_hash=p_hash and expires_at>now();
 if not found then return false; end if;
 insert into public.owner_report_attempts(delivery_id,status,reason) values(p_id,p_result,'Workflow action outcome; not inbox delivery proof');
 return true;
end $$;

create function public.owner_dispatch_result(p_id uuid,p_status text,p_http integer) returns void
language plpgsql security definer set search_path='' as $$
begin
 if p_status not in ('accepted','unknown','failed') then raise exception 'Invalid result'; end if;
 update public.owner_report_outbox set status=p_status,reason=case when p_status='unknown' then 'Needs review; do not automatically resend' else null end
 where id=p_id and status='dispatching';
 insert into public.owner_report_attempts(delivery_id,status,http_status,reason)
 values(p_id,p_status,p_http,'Transport outcome; not inbox delivery proof');
end $$;

-- Published versions are append-only even for worker credentials.
create function public.owner_snapshot_immutable() returns trigger
language plpgsql set search_path='' as $$
begin raise exception 'Published owner reports are immutable; insert a new version'; end $$;
create trigger owner_snapshot_immutable before update or delete on public.owner_report_snapshots
for each row execute function public.owner_snapshot_immutable();

do $$ declare f record; begin
 for f in select p.oid::regprocedure as signature,p.proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace
 where n.nspname='public' and p.proname like 'owner_%' loop
  execute format('revoke all on function %s from public,anon,authenticated',f.signature);
  execute format('grant execute on function %s to service_role',f.signature);
  if f.proname in ('owner_reports','owner_admin_state','owner_save_settings','owner_save_binding','owner_save_destination',
    'owner_verify_binding','owner_prepare_test','owner_approve_test','owner_accept_test') then
   execute format('grant execute on function %s to authenticated',f.signature);
  end if;
 end loop;
end $$;
commit;
