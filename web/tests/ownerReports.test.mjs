import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
async function moduleAt(path,transform=x=>x){
 const s=transform(await readFile(new URL(path,import.meta.url),'utf8'));
 const {outputText}=ts.transpileModule(s,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
 return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
}
const {chooseReport,ownerFresh}=await moduleAt('../src/lib/ownerReports.ts');
const {handle}=await moduleAt('../../supabase/functions/_shared/owner-workflow.ts',s=>s.replace(/^import .*;$/m,'const createClient=()=>{throw Error("No real client")};'));
const saved={id:'saved',period_kind:'week',period_start:'2026-09-21',data:{to_date:false,current_as_of:'2026-10-02T12:00:00Z'}};
const current={...saved,id:'current',period_start:'2026-09-28',data:{...saved.data,to_date:true}};
test('owner report selection keeps exact IDs and defaults to completed weeks',()=>{
 assert.equal(chooseReport([current,saved],'week',null,null).id,'saved');
 assert.equal(chooseReport([current,saved],'week',null,'current').id,'current');
 assert.equal(chooseReport([current,saved],'week',null,'unknown'),undefined);
 assert.equal(chooseReport([current,saved],'week','2025-01-01',null),undefined);
 assert.equal(ownerFresh(saved,30,Date.parse('2026-10-02T14:00:00Z')),true);
 assert.equal(ownerFresh(saved,1,Date.parse('2026-10-02T14:00:00Z')),false);
});
let sequence=0,calls=[];
const env=k=>({OWNER_REPORTS_MODE:'preview',SUPABASE_URL:'https://fixture.invalid',SUPABASE_SERVICE_ROLE_KEY:'fixture'}[k]);
const client=()=>({rpc:async(name,args)=>{calls.push({name,args});return {data:{allow_send:'false'}};}});
const body={delivery_id:'00000000-0000-0000-0000-000000000001',location_id:'fixture-a',workflow_binding:'fixture',recipient_binding_version:'1',mode:'test',claim_credential:'a'.repeat(43)};
const request=b=>new Request('https://fixture.invalid',{method:'POST',headers:{'content-type':'application/json','x-forwarded-for':'fixture-'+sequence++},body:JSON.stringify(b)});
test('owner edge refuses malformed, unapproved and oversized claims before privileged calls',async()=>{
 calls=[];
 for(const bad of [{...body,mode:'live'},{...body,email:'someone@example.invalid'},{...body,workflow_binding:''},{...body,claim_credential:'bad'},null,[],{...body,extra:'x'.repeat(5000)}]){
  const r=await handle(request(bad),'claim',env,client);assert.ok(r.status>=400);
 }
 assert.equal(calls.length,0);
 assert.equal((await handle(request(body),'claim',()=>undefined,client)).status,403);
});
test('owner edge hashes credentials and returns denial without logging or echoing secrets',async()=>{
 calls=[];const r=await handle(request(body),'claim',env,client);
 assert.equal(r.status,200);assert.equal(r.headers.get('cache-control'),'no-store');
 assert.deepEqual(await r.json(),{allow_send:'false'});
 assert.equal(calls.length,1);assert.match(calls[0].args.p_hash,/^[a-f0-9]{64}$/);
 assert.equal(JSON.stringify(calls).includes(body.claim_credential),false);
});
const action=await readFile(new URL('../../docs/owner-workflow/claim-action.js',import.meta.url),'utf8');
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
const execute=new AsyncFunction('inputData','customRequest',`let output;${action};return output;`);
test('GHL guard rejects mapping live wrong-account and malformed envelopes without HTTP',async()=>{
 const transport={post:()=>assert.fail('No HTTP for denied input')};
 for(const e of [{mode:'mapping'},{...body,event:'owner_followup_weekly',schema_version:'1',mode:'live'},{...body,event:'owner_followup_weekly',schema_version:'1'},null]){
  assert.equal((await execute({envelope:JSON.stringify(e)},transport)).allow_send,'false');
 }
 assert.equal((await execute({envelope:'not-json'},transport)).allow_send,'false');
});
test('GHL guard uses canonical response only and fails closed on provider errors',async()=>{
 const e={...body,event:'owner_followup_weekly',schema_version:'1',location_id:'ZnckuEDPIcWu8fn72ppi',workflow_binding:'623657b3-eabc-451f-b0a6-b8c34865d4a2',subject:'Untrusted input',completion_credential:'b'.repeat(43)};
 const c={allow_send:'true',subject:'Canonical',body_html:'<p>Safe</p>',body_text:'Safe',report_state:'healthy'};
 const out=await execute({envelope:JSON.stringify(e)},{post:async()=>({status:200,data:c})});
 assert.equal(out.allow_send,'true');assert.equal(out.subject,'Canonical');
 assert.equal((await execute({envelope:JSON.stringify(e)},{post:async()=>{throw Error('private');}})).allow_send,'false');
});

test('GHL completion callback makes no request for missing or malformed inputs',async()=>{
 const code=await readFile(new URL('../../docs/owner-workflow/result-action.js',import.meta.url),'utf8');
 const run=new AsyncFunction('inputData','customRequest',`let output;${code};return output;`);
 for(const input of [{},{delivery_id:'bad',completion_credential:'x'.repeat(43)},{delivery_id:body.delivery_id,completion_credential:''}]){
  assert.deepEqual(await run(input,{post:()=>assert.fail('Must not call callback')}),{recorded:'false'});
 }
 let sent;
 const result=await run({delivery_id:body.delivery_id,completion_credential:'x'.repeat(43)},{post:async(url,request)=>{sent=request.data;return {status:200,data:{recorded:true}};}});
 assert.deepEqual(result,{recorded:'true'});
 assert.equal(sent.result,'notification_action_recorded');
});

test('production GHL adapter tests its own binding and preserves credential mode',async()=>{
 const code=(await readFile(new URL('../../docs/owner-workflow/production-claim-action.js',import.meta.url),'utf8')).replace('SET_VERIFIED_SSP_PRODUCTION_WORKFLOW_ID','production-fixture');
 const run=new AsyncFunction('inputData','customRequest',`let output;${code};return output;`);
 const envelope={...body,event:'owner_followup_weekly',schema_version:'1',location_id:'ZnckuEDPIcWu8fn72ppi',workflow_binding:'production-fixture'};
 for(const mode of ['test','live']){
  let sent;
  await run({envelope:JSON.stringify({...envelope,mode})},{post:async(url,request)=>{sent=request.data;return {status:200,data:{allow_send:'false'}};}});
  assert.equal(sent.mode,mode);
  assert.equal(sent.workflow_binding,'production-fixture');
 }
 for(const bad of [{...envelope,mode:'mapping'},{...envelope,workflow_binding:'qa-workflow'},{...envelope,location_id:'other-account'}]){
  assert.equal((await run({envelope:JSON.stringify(bad)},{post:()=>assert.fail('Wrong binding or mode')})).allow_send,'false');
 }
});
