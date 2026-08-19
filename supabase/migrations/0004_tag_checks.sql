-- Tag/pixel monitoring (docs/FORMS-INTEGRATION.md Phase 2).
--
-- tag_config on subaccounts holds each client's monitoring setup:
--   { "website": "https://…",              -- page the tags should fire on
--     "consent_click": "css selector"|null, -- cookie-banner accept, if any
--     "expect": [ {"type":"ga4","id":"G-XXXX"}, {"type":"gtm_container"} ] }
-- Accounts with no "expect" entries are simply skipped by the checker —
-- storing just the website costs nothing and is ready for later.
alter table public.subaccounts add column tag_config jsonb not null default '{}'::jsonb;

-- One row per account per check run, written by the tagchecker service
-- (service role). results holds per-expected-tag outcomes:
--   [ {"type":"gtm_container","id":"GTM-XXXX","fired":true}, … ]
create table public.tag_checks (
  id bigserial primary key,
  location_id text not null references public.subaccounts(location_id) on delete cascade,
  checked_at timestamptz not null default now(),
  url text not null,
  -- ok = every expected tag fired; alert = at least one didn't;
  -- error = the page itself could not be checked (load failure, timeout)
  status text not null check (status in ('ok','alert','error')),
  results jsonb not null default '[]'::jsonb,
  error text
);
create index tag_checks_loc_time on public.tag_checks (location_id, checked_at desc);

alter table public.tag_checks enable row level security;
alter table public.tag_checks force row level security;
create policy staff_read_tag_checks on public.tag_checks
  for select to authenticated using (public.is_staff());
grant select on public.tag_checks to authenticated;
