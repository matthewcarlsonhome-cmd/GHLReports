-- 0008 — Insight-to-Workflow Bridge (Phase 1) audit trail.
--
-- One row per alert the collector attempts to push to the SSP GoHighLevel
-- workflow, including dry-run rehearsals and sends skipped by the rate cap.
-- Kept forever: this table is the only record of what was pushed outward,
-- and "did the team get told?" is a question that gets asked months later.
--
-- Nothing here is a secret. The webhook URL lives in an environment
-- variable and is never written to a row; `payload` stores exactly what was
-- POSTed, which by construction carries no PII (see collector/automation.py).

create table public.automation_sends (
  id            bigserial primary key,
  run_id        bigint references public.collector_runs(id) on delete set null,
  location_id   text not null references public.subaccounts(location_id) on delete cascade,
  snapshot_date date not null,
  flag_code     text not null,
  severity      text,
  entity_type   text,
  entity_name   text,
  mode          text not null check (mode in ('live','dry')),
  status        text not null check (status in ('sent','failed','skipped_cap')),
  http_status   int,
  error         text,
  payload       jsonb not null default '{}'::jsonb,
  sent_at       timestamptz not null default now()
);

create index automation_sends_loc_date on public.automation_sends (location_id, snapshot_date);
create index automation_sends_sent_at  on public.automation_sends (sent_at desc);

-- Belt-and-braces against a double alert: a rerun of the same day cannot
-- re-send a live alert it already delivered. Dry rows are exempt so a
-- rehearsal week can be run as many times as needed. coalesce() keeps the
-- index usable for account-level codes, which carry no entity name.
create unique index automation_sends_once
  on public.automation_sends (location_id, snapshot_date, flag_code, coalesce(entity_name, ''))
  where mode = 'live' and status = 'sent';

alter table public.automation_sends enable row level security;
alter table public.automation_sends force row level security;
create policy automation_sends_staff_read on public.automation_sends
  for select to authenticated using (public.is_staff());
grant select on public.automation_sends to authenticated;
