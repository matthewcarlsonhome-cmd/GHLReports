-- Starter rows for a brand-new project: SSP's own (parent) account plus two
-- placeholder pilot clients. Replace every <...> value before running; the
-- live project's rows were entered and updated by hand and by later
-- migrations, so do not re-run this against it. Only staff addresses belong
-- in am_email (the digest refuses others). No tokens here: PITs go into
-- Vault as ghl_pit_<location_id> (docs/GO-LIVE.md).
insert into public.subaccounts (location_id, name, slug, is_parent, timezone, am_email) values
('ZnckuEDPIcWu8fn72ppi', 'Small Screen Producer', 'ssp', true, 'America/Chicago', 'matthew@smallscreenproducer.com');
insert into public.subaccounts (location_id, name, slug, vertical, services, am_email, ssp_client_contact_id, mrr, contract_end) values
('<pilot1_location_id>', '<Pilot One Pools>', 'pilot1', 'pool_builder', '{content,social,ads}', 'lisa@smallscreenproducer.com',   '<contact id in SSP account>', 2500, '2027-01-31'),
('<pilot2_location_id>', '<Pilot Two Spas>',  'pilot2', 'hot_tub',      '{ads,seo}',            'lauren@smallscreenproducer.com', '<contact id in SSP account>', null, null);
