// Real PostgreSQL RLS/function tests in an isolated PGlite database. No network.
// PGLITE_MODULE may point to a separately installed @electric-sql/pglite module.
import {readFile,readdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_MODULE || '@electric-sql/pglite');
const db=new PGlite();
const owner='00000000-0000-0000-0000-000000000001', a='00000000-0000-0000-0000-000000000002', b='00000000-0000-0000-0000-000000000003', staff='00000000-0000-0000-0000-000000000004';
await db.exec(`create role anon;create role authenticated;create role service_role bypassrls;
create schema auth;create table auth.users(id uuid primary key,email text,email_confirmed_at timestamptz);
create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
create function auth.jwt() returns jsonb language sql stable as $$select coalesce(nullif(current_setting('request.jwt.claims',true),''),'{}')::jsonb$$;
grant usage on schema auth to authenticated,anon,service_role;
grant execute on all functions in schema auth to authenticated,anon,service_role;
insert into auth.users values('${owner}','mcarlson@smallscreenproducer.com',now()),('${a}','a@example.invalid',now()),('${b}','b@example.invalid',now()),('${staff}','staff@smallscreenproducer.com',now());`);
const folder=new URL('../migrations/',import.meta.url);
for(const name of (await readdir(folder)).filter(n=>n.endsWith('.sql')).sort()) {
  // PGlite has core gen_random_uuid/sha256 but no pgcrypto extension bundle.
  const sql=(await readFile(new URL(name,folder),'utf8')).replace('create extension if not exists pgcrypto;','');
  try{await db.exec(sql);}catch(e){console.error('Migration failed:',name,e.message);process.exit(1);}
}
async function asUser(id,aal='aal1',email='a@example.invalid') {
  await db.exec('reset role');
  await db.query("select set_config('request.jwt.claim.sub',$1,false),set_config('request.jwt.claims',$2,false)",[id,JSON.stringify({sub:id,role:'authenticated',email,aal})]);
  await db.exec('set role authenticated');
}
async function scalar(sql,params=[]) {const r=await db.query(sql,params);return Object.values(r.rows[0])[0];}
async function denied(sql,params=[]) {await assert.rejects(()=>db.query(sql,params));}
await db.exec(`insert into public.subaccounts(location_id,name,slug) values('fixture-a','Fixture A','fixture-a'),('fixture-b','Fixture B','fixture-b');
select public.bootstrap_dashboard_admin('${owner}');
insert into public.snapshots(location_id,snapshot_date,gate_passed,details) values('fixture-a',current_date,true,'{"private":"SECRET_LEAD_CANARY"}'),('fixture-b',current_date,true,'{}');
insert into public.client_reports values('fixture-a','week',current_date,now(),'{"schema_version":1}'),('fixture-b','week',current_date,now(),'{"schema_version":1}');`);
await asUser(owner);
await denied("select public.dashboard_settings('fixture-a',true,true,30)");
await asUser(owner,'aal2');
await db.exec(`select public.dashboard_settings('fixture-a',true,true,30);select public.dashboard_settings('fixture-b',true,true,30);
select public.dashboard_membership('fixture-a','${a}','client_owner',true);select public.dashboard_membership('fixture-b','${b}','client_viewer',true);`);
await asUser(a);
assert.equal((await scalar('select public.dashboard_accounts()')).length,1);
await denied("select public.dashboard_report_preferences('fixture-a','America/Chicago',array['week'])");
assert.equal(await scalar('select count(*) from public.client_reports'),1);
assert.equal(await scalar('select count(*) from public.snapshots'),0);
assert.equal(await scalar('select count(*) from public.subaccounts'),0);
assert.deepEqual(await scalar("select public.dashboard_reports('fixture-b')"),[]);
await denied("insert into public.account_notes(location_id,author,body) values('fixture-a','a@example.invalid','not allowed')");
await denied("select public.dashboard_settings('fixture-a',true,true,30)");
await denied('update public.staff_access set is_admin=true');
await denied("select public.bootstrap_dashboard_admin($1)",[a]);
await denied("select public.dashboard_membership('fixture-b',$1,'client_owner',true)",[a]);
// A spoofed staff email and aal2 in a valid client JWT still has no grant.
await asUser(a,'aal2','mcarlson@smallscreenproducer.com');
assert.equal(await scalar('select public.is_staff()'),false);
await denied('select public.dashboard_admin_state()');
await asUser(owner,'aal2');
await db.query("select public.dashboard_membership('fixture-a',$1,'client_owner',false)",[a]);
await asUser(a);
assert.equal(await scalar('select count(*) from public.client_reports'),0);
await asUser(owner,'aal2');
await db.query("select public.dashboard_membership('fixture-a',$1,'client_owner',true)",[a]);
await db.exec("select public.dashboard_settings('fixture-a',false,true,30)");
await asUser(a);
assert.deepEqual(await scalar('select public.dashboard_accounts()'),[]);
await asUser(staff);
assert.equal(await scalar('select count(*) from public.snapshots'),2);
await db.exec('reset role');await db.query('update public.staff_access set all_accounts=false where user_id=$1',[staff]);
await asUser(owner,'aal2');await db.query("select public.dashboard_membership('fixture-a',$1,'am',true)",[staff]);
await asUser(staff);assert.equal(await scalar('select count(*) from public.snapshots'),1);
assert.equal(await scalar('select count(*) from public.collector_runs'),0);
await db.exec('reset role');await db.query('update public.staff_access set active=false where user_id=$1',[staff]);
await asUser(staff);assert.equal(await scalar('select count(*) from public.snapshots'),0);
// Tokens are one-use, bound to a verified email; never stored in plaintext.
await asUser(owner,'aal2');
const invitation=await scalar("select public.dashboard_invite('fixture-a','a@example.invalid','client_viewer')");
await asUser(b);await denied('select public.dashboard_accept_invite($1)',[invitation.token]);
await asUser(a);await db.query('select public.dashboard_accept_invite($1)',[invitation.token]);
await denied('select public.dashboard_accept_invite($1)',[invitation.token]);
await asUser(owner,'aal2');
const revoked=await scalar("select public.dashboard_invite('fixture-a','a@example.invalid','client_viewer')");
await db.query('select public.dashboard_revoke_invite($1)',[revoked.id]);
await asUser(a);await denied('select public.dashboard_accept_invite($1)',[revoked.token]);
await asUser(owner,'aal2');
const expired=await scalar("select public.dashboard_invite('fixture-a','a@example.invalid','client_viewer')");
await denied("select public.dashboard_report_preferences('fixture-a','not/a/timezone',array['week'])");
await denied("select public.dashboard_report_preferences('fixture-a','America/Chicago',array['unknown'])");
await db.exec("select public.dashboard_report_preferences('fixture-a','America/Chicago',array['attention','week','month'])");
await db.exec('reset role');await db.query("update public.client_invitations set expires_at=now()-interval '1 second' where id=$1",[expired.id]);
await asUser(a);await denied('select public.dashboard_accept_invite($1)',[expired.token]);
await asUser(owner,'aal2');
const unverified=await scalar("select public.dashboard_invite('fixture-a','a@example.invalid','client_viewer')");
await db.exec('reset role');await db.query('update auth.users set email_confirmed_at=null where id=$1',[a]);
await asUser(a);await denied('select public.dashboard_accept_invite($1)',[unverified.token]);
await asUser(owner,'aal2');await denied("select public.dashboard_membership('fixture-a',$1,'client_owner',true)",[a]);
await db.exec('reset role');await db.query('update auth.users set email_confirmed_at=now() where id=$1',[a]);
await asUser(owner,'aal2');
await db.exec(`select public.dashboard_settings('fixture-a',true,true,30);
select public.dashboard_membership('fixture-b','${a}','client_viewer',true);
select public.dashboard_staff('${staff}',true,false);`);
await asUser(a);assert.equal((await scalar('select public.dashboard_accounts()')).length,2);
await asUser(owner,'aal2');await db.exec("select public.dashboard_settings('fixture-b',true,false,30)");
await asUser(a);assert.equal((await scalar('select public.dashboard_accounts()')).length,1);
await asUser(staff);assert.equal(await scalar('select count(*) from public.snapshots'),1);
await denied('select public.dashboard_staff($1,true,true)',[staff]);
await asUser(owner,'aal2');await denied('select public.dashboard_staff($1,false,false)',[owner]);
// A failed refresh preserves the last complete version of the same period.
await db.exec('reset role');
await db.exec(`insert into public.client_reports values
('fixture-a','month',current_date,now()-interval '2 hours','{"schema_version":1,"coverage":{"contacts":true,"responses":true}}'),
('fixture-a','month',current_date,now(),' {"schema_version":1,"coverage":{"contacts":true,"responses":false}}');`);
await asUser(a);
const history=(await scalar("select public.dashboard_reports('fixture-a')")).find(r=>r.period_kind==='month');
assert.equal(history.data.coverage.responses,false);
assert.equal(history.previous_complete.data.coverage.responses,true);
assert.ok(history.previous_complete.generated_at<history.generated_at);
await db.exec('reset role; set role anon');await denied('select * from public.client_reports');await denied('select public.dashboard_accounts()');
await db.exec('reset role');
const policies=await db.query("select tablename from pg_tables where schemaname='public' and tablename in ('staff_access','account_memberships','client_report_settings','client_invitations','client_reports','access_audit') and rowsecurity");
assert.equal(policies.rows.length,6);
console.log('PASS: all migrations; tenant and field isolation; spoofed claims; admin MFA; revoke and kill switches; assigned AM and multi-account client; no client writes; invitation binding/replay/revocation/expiry/verification; last complete report; anonymous denial.');
await (await import('./owner_reports.mjs')).default({db,asUser,scalar,denied,owner,a,b});
await db.close();
