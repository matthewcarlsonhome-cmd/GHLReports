-- AM ownership per subaccount (roster supplied by Matthew Carlson, 2026-09-22).
--
-- am_name is the human name shown in the dashboard and digest; am_email stays
-- the routing address. They are separate because we learn who owns an account
-- before we learn their mailbox: when this roster arrived we had first names
-- only, so a row could carry am_name while am_email was still a fallback.
-- Filling the address in flips routing with no other change.
alter table public.subaccounts add column if not exists am_name text;

comment on column public.subaccounts.am_name is
  'Account manager display name. am_email is the routing address; a row may have am_name set while am_email is still the fallback.';

update public.subaccounts set am_name = 'Lauren', am_email = 'lgegner@smallscreenproducer.com'
 where slug in ('aaapools','aqualeisure','backyardoasis','centraljersey','cypress',
                'flohr','magnolia','olympic','texaspools','texasswim','burketts');

update public.subaccounts set am_name = 'Lisa', am_email = 'lhoffman@smallscreenproducer.com'
 where slug in ('aaapoolsspas','absolutepool','campbells','hamlin','kura','liverpool',
                'mckinney','pettis','russos','softub');

update public.subaccounts set am_name = 'Michael', am_email = 'madams@smallscreenproducer.com'
 where slug in ('allamerican','aquapros','beachfront','bistate','lukegell','plamor','pristine');

-- Exquisite and G&S are Michael's, with Dada covering Facebook.
update public.subaccounts set am_name = 'Michael / Dada (FB)',
                              am_email = 'madams@smallscreenproducer.com'
 where slug in ('exquisite','gscustom');

-- The agency's own subaccount stays with Matthew.
update public.subaccounts set am_name = 'Matthew'
 where is_parent;
