// Execute the edge handler with fake auth/provider boundaries. No network/mail.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
let handler,allowed=true,signedIn=true,calls=[];
globalThis.__edgeDeno={env:{get:key=>({SUPABASE_URL:'https://fixture.invalid',SUPABASE_ANON_KEY:'public',SUPABASE_SERVICE_ROLE_KEY:'private'}[key])},serve:fn=>{handler=fn;}};
globalThis.__edgeClient=(_url,key)=> key==='private'?{auth:{admin:{createUser:async args=>{calls.push(args);return {error:null};}}}}:{
  auth:{getUser:async()=>({data:{user:signedIn?{id:'fixture'}:null},error:null})},
  rpc:async name=>name==='require_dashboard_admin'?{error:allowed?null:{}}:{data:{id:'id',token:'one-use'},error:null}
};
let source=await readFile(new URL('../../supabase/functions/prepare-client-access/index.ts',import.meta.url),'utf8');
source=source.replace(/^import .*;$/m,'const createClient=globalThis.__edgeClient; const Deno=globalThis.__edgeDeno;');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const req=(body,origin='https://mlhaccountreports.netlify.app')=>new Request('https://fixture.invalid',{method:'POST',headers:{origin,'content-type':'application/json'},body:JSON.stringify(body)});
const body={email:'viewer@example.invalid',location_id:'fixture-a',role:'client_viewer'};
test('edge rejects unauthorized callers before service credentials are used',async()=>{
  calls=[];allowed=false;assert.equal((await handler(req(body))).status,403);assert.equal(calls.length,0);
  allowed=true;signedIn=false;assert.equal((await handler(req(body))).status,401);assert.equal(calls.length,0);signedIn=true;
});
test('edge rejects unapproved origin, roles, fields and recipient injection',async()=>{
  calls=[];
  assert.equal((await handler(req(body,'https://unapproved.invalid'))).status,403);
  for(const bad of [{...body,role:'admin'},{...body,email:'x@a.invalid, y@b.invalid'},{...body,send_email:true}]) assert.equal((await handler(req(bad))).status,400);
  assert.equal(calls.length,0);
});
test('provisioning does not confirm email or send an invitation',async()=>{
  calls=[];const response=await handler(req(body));assert.equal(response.status,200);
  assert.deepEqual(calls,[{email:body.email,email_confirm:false}]);
  assert.equal(response.headers.get('cache-control'),'no-store');
});
