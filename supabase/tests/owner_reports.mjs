// PostgreSQL owner settings, tenant isolation, atomic claims and recipient revisions.
export default async function ownerTests({db,asUser,scalar,denied,owner,a,b}) {
 const assert=(await import('node:assert/strict')).default;
 await db.exec('reset role');
 await db.exec(`create schema if not exists vault;
 create table if not exists vault.secrets(id uuid primary key default gen_random_uuid(),name text unique,secret text);
 create or replace view vault.decrypted_secrets as select id,name,secret as decrypted_secret from vault.secrets;
 create function vault.create_secret(value text,key text,description text) returns uuid language plpgsql as $$
 declare result uuid;begin insert into vault.secrets(name,secret) values(key,value) returning id into result;return result;end $$;
 create function vault.update_secret(secret_id uuid,value text) returns void language sql as $$update vault.secrets set secret=value where id=secret_id$$;
 update public.subaccounts set is_parent=true where location_id='fixture-a';`);
 await asUser(a,'aal2');
 for(const sql of ["select public.owner_admin_state('fixture-a')","select public.owner_save_settings('fixture-a',array['waiting'],0,'08:00','live')",
   "select * from public.owner_report_recipients","select * from public.owner_report_outbox","select public.owner_heartbeat('live',array['fixture-a'],10)"])await denied(sql);
 await asUser(owner,'aal1');await denied("select public.owner_admin_state('fixture-a')");
 await asUser(owner,'aal2');
 await db.exec(`select public.dashboard_membership('fixture-a','${owner}','client_owner',true);
 select public.dashboard_membership('fixture-a','${a}','client_owner',true);
 select public.owner_save_settings('fixture-a',array['follow_up','waiting'],0,'08:00','preview');`);
 await denied(`select public.owner_save_binding('fixture-a','${b}','fake-user','b@example.invalid','fake-workflow','v1')`);
 await denied("select public.owner_save_settings('fixture-a',array['invented'],0,'08:00','off')");
 await db.exec(`select public.owner_save_binding('fixture-a','${owner}','fake-ghl-user','mcarlson@smallscreenproducer.com','fake-workflow','v1')`);
 await denied("select public.owner_save_destination('fixture-a','https://127.0.0.1/private')");
 await db.exec("select public.owner_save_destination('fixture-a','https://services.leadconnectorhq.com/hooks/FIXTURE/webhook-trigger/FAKE')");
 const state=await scalar("select public.owner_admin_state('fixture-a')");
 assert.equal(state.destination.secret_configured,true);
 assert.equal(JSON.stringify(state).includes('webhook-trigger'),false);
 assert.equal(JSON.stringify(state).includes('secret_name'),false);
 await denied("select public.owner_save_settings('fixture-a',array['waiting'],0,'08:00','live')");
 await db.exec("select public.owner_verify_binding('fixture-a','Synthetic fixture: selected owner and claim/render behavior verified')");
 const id=await scalar("select public.owner_prepare_test('fixture-a')");
 await db.query('select public.owner_approve_test($1)',[id]);
 await db.exec('reset role;set role service_role');
 await db.exec("select public.owner_heartbeat('preview',array['fixture-a'],1)");
 const snapshot=await scalar(`insert into public.owner_report_snapshots(location_id,period_kind,period_start,period_end,generated_at,data)
 values('fixture-a','week',current_date,now(),now(),'{"sample":true}') returning id`);
 const hash='a'.repeat(64),completion='b'.repeat(64);
 const route=await scalar("select public.owner_dispatch($1,$2,$3,$4::jsonb,$5,$6)",[id,hash,completion,JSON.stringify({subject:'Safe synthetic report',report_state:'healthy'}),'c'.repeat(64),snapshot]);
 assert.equal(route.workflow,'fake-workflow');
 assert.equal(await scalar("select public.owner_dispatch($1,$2,$3,'{}'::jsonb,$4,$5)",[id,hash,completion,'c'.repeat(64),snapshot]),null);
 assert.equal(await scalar('select report_id from public.owner_report_outbox where id=$1',[id]),snapshot);
 await denied("update public.owner_report_snapshots set data='{}' where id=$1",[snapshot]);
 const args=[id,'fixture-a','fake-workflow',1,'test',hash];
 assert.deepEqual(await scalar('select public.owner_claim($1,$2,$3,$4,$5,$6)',[id,'fixture-b',...args.slice(2)]),{allow_send:'false'});
 assert.deepEqual(await scalar('select public.owner_claim($1,$2,$3,$4,$5,$6)',[...args.slice(0,5),'d'.repeat(64)]),{allow_send:'false'});
 const claim=await scalar('select public.owner_claim($1,$2,$3,$4,$5,$6)',args);
 assert.equal(claim.allow_send,'true');assert.equal(claim.subject,'Safe synthetic report');
 assert.deepEqual(await scalar('select public.owner_claim($1,$2,$3,$4,$5,$6)',args),{allow_send:'false'});
 assert.equal(await scalar("select public.owner_result($1,$2,'notification_action_recorded')",[id,'bad']),false);
 assert.equal(await scalar("select public.owner_result($1,$2,'notification_action_recorded')",[id,completion]),true);
 assert.equal(await scalar("select public.owner_result($1,$2,'notification_action_recorded')",[id,completion]),false);
 await asUser(owner,'aal2');
 await denied("select public.owner_save_settings('fixture-a',array['waiting'],0,'08:00','live')");
 await db.query('select public.owner_accept_test($1)',[id]);
 await db.exec("select public.owner_save_settings('fixture-a',array['waiting'],0,'08:00','live')");
 // Changing a recipient invalidates the old test and connection.
 await db.exec(`select public.owner_save_binding('fixture-a','${a}','changed-ghl-user','a@example.invalid','fake-workflow','v1')`);
 await denied("select public.owner_save_settings('fixture-a',array['waiting'],0,'08:00','live')");
 await db.exec('reset role');
 const report=await scalar(`insert into public.owner_report_snapshots(location_id,period_kind,period_start,period_end,generated_at,data)
 values('fixture-a','week',current_date,now(),now(),'{"safe":true}') returning id`);
 await asUser(b);assert.deepEqual(await scalar('select public.owner_reports($1,$2)',['fixture-a',report]),[]);
 await asUser(a);assert.equal((await scalar('select public.owner_reports($1,$2)',['fixture-a',report])).length,1);
 assert.deepEqual(await scalar('select public.owner_reports($1,$2)',['fixture-b',report]),[]);
 assert.equal((await scalar("select public.owner_reports('fixture-a','ffffffff-ffff-ffff-ffff-ffffffffffff')")).length,0);
 await denied("select public.owner_claim($1,'fixture-a','fake-workflow',1,'test',$2)",[id,hash]);
 await denied("update public.owner_report_snapshots set data='{}'");
 await db.exec('reset role;set role service_role');
 const row={location_id:'fixture-a',period_start:'2026-09-28',period_end:'2026-10-05T00:00:00Z',
 timezone:'America/Chicago',due_at:'2026-10-05T13:00:00Z',expires_at:'2026-10-06T13:00:00Z',
 recipient_version:2,destination_version:3,settings_revision:3};
 assert.ok(await scalar('select public.owner_enqueue($1)',[row]));
 assert.equal(await scalar('select public.owner_enqueue($1)',[{...row,recipient_version:100}]),null);
 await db.exec('reset role;set role anon');await denied("select public.owner_reports('fixture-a',null)");
 await db.exec('reset role');
 console.log('PASS: owner admin MFA, private configuration, binding verification, SSRF rejection, test approval, one-use claim/result, correct recipient versions, immutable period identity, and cross-account report isolation.');
}
