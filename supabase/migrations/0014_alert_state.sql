-- 0014: account manager notices (docs/AM-NOTIFY-WORKFLOW-SPEC.md, Part A).
--
-- 1. alert_state: one row per open (or recently closed) issue per account,
--    keyed (location_id, code, entity_key). The collector's state machine
--    (collector/automation.py) is the only writer (service role). It is what
--    stops an AM getting the same email every day: an issue is announced
--    once, then only again when it gets worse, when a spaced reminder is
--    due (bounded), or as a "cleared" line when it goes away.
--    Beyond the spec's columns:
--      trigger        the AM-facing alert type (T2, T3, T4 ...)
--      notified_to    who was told: the recipient address, prefixed 'dry:'
--                     for dry runs. An issue counts as "told" only for that
--                     audience, so a dry run or a shadow week never stops
--                     the real AM from hearing about it once.
--      told_count     the number when last told (T2 leads, T3 customers
--                     waiting); a doubling counts as "worse"
--      last_count     the latest number seen
--      entity_label   what the entity is called (form name, lead source)
--      backlog        already open on the first night this account was
--                     tracked, so its real start date is unknown
--      cleared_text   the "cleared" line waiting to ride along with the
--                     account's next notice
--      evaluated_on   last run date that counted this row (rerun safety)
-- 2. subaccounts.alert_triggers: which alert types an account is piloting
--    ('{T2,T3,T4}'); empty = no notices for that account. The state is
--    tracked for every collected account either way, so opting in later
--    starts from real history. alert_tracking_since marks the first night
--    the state machine saw the account (issues open that night = backlog).
-- 3. automation_sends changes unit from "one flag" to "one account notice":
--    flag_code = 'AM_NOTICE' (the existing unique index then allows one
--    delivered notice per account per day), message_kind, item_codes.
-- 4. Pilot seed (2026-09-28): Lauren picked Flohr and Central Jersey with
--    T2 leads going cold, T3 customers left waiting, T4 website capture
--    broken. Lisa picked Pettis, Liverpool, McKinney and AAA Spa & Pool; her
--    alert types are not chosen yet, so they start on the same three
--    (provisional; change with one update). T2 means "2 or more leads", so
--    these accounts' slow-response threshold drops from 3 to 2 and the
--    dashboard shows the same issue the email names (and its Acknowledge
--    button exists).

create table if not exists public.alert_state (
  location_id       text not null references public.subaccounts(location_id) on delete cascade,
  code              text not null,
  entity_key        text not null default '',
  trigger           text not null,
  status            text not null check (status in ('pending', 'open', 'resolved')),
  severity          text not null,
  first_seen        date not null,
  last_seen         date not null,
  seen_runs         int  not null default 1,
  absent_runs       int  not null default 0,
  notified_at       date,
  last_notified     date,
  reminders         int  not null default 0,
  notified_severity text,
  notified_to       text,
  told_count        int,
  last_count        int,
  entity_label      text,
  backlog           boolean not null default false,
  resolved_at       date,
  resolve_told      boolean not null default false,
  cleared_text      text,
  summary           text,
  evaluated_on      date,
  updated_at        timestamptz not null default now(),
  primary key (location_id, code, entity_key)
);
create index if not exists alert_state_status on public.alert_state (status, location_id);

alter table public.alert_state enable row level security;
alter table public.alert_state force row level security;
drop policy if exists alert_state_staff_read on public.alert_state;
create policy alert_state_staff_read on public.alert_state
  for select to authenticated using (public.is_staff());
grant select on public.alert_state to authenticated;

alter table public.subaccounts
  add column if not exists alert_triggers text[] not null default '{}',
  add column if not exists alert_tracking_since date;
alter table public.subaccounts drop constraint if exists subaccounts_alert_triggers_check;
alter table public.subaccounts add constraint subaccounts_alert_triggers_check
  check (alert_triggers <@ array['T1','T2','T3','T4','T5','T6','T7','T8']::text[]);

alter table public.automation_sends
  add column if not exists message_kind text,
  add column if not exists item_codes text[];

-- Pilot seed. `thresholds || jsonb` keeps any other per-account overrides.
update public.subaccounts
   set alert_triggers = '{T2,T3,T4}',
       thresholds = coalesce(thresholds, '{}'::jsonb) || '{"slow_response_min": 2}'::jsonb
 where location_id in ('Y4vvMyoOjARnCsb1nEYM',   -- Flohr Pools (Lauren)
                       'A6WeIeAP9Fi2CuCApyce',   -- Central Jersey Pool & Spas (Lauren)
                       'fXNH1f1mo1FxawSUrF4v',   -- Pettis Pools & Patio (Lisa, provisional)
                       'WeNxQrw1VO4dRpzEMn7T',   -- Liverpool Pool & Spa (Lisa, provisional)
                       'UCq30gKSRj3012SOQVxg',   -- McKinney Custom Pools (Lisa, provisional)
                       'aDg77jK5Z8u9NLRVCpHT');  -- AAA Spa & Pool Services (Lisa, provisional)
