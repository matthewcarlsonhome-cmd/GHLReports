// MFA-protected owner delivery setup. Live sends remain independently gated.
import {useEffect,useState} from 'react';
import {Link} from 'react-router-dom';
import {supabase} from '../lib/supabase';
import {TOPICS} from '../lib/ownerReports';

type State={settings:null|{topics:string[];weekday:number;local_time:string;delivery_mode:string};
 recipient:null|{user_id:string;ghl_user_id:string;email:string;verified_at:string|null};
 destination:null|{workflow_id:string;workflow_version:string;secret_configured:boolean;adapter_verified_at:string|null};
 control:{mode:string;heartbeat_at:string|null;pilot_locations:string[]};
 members:{user_id:string;email:string}[];
 history:{id:string;period_start:string;mode:string;status:string;reason:string|null;due_at:string;inbox_verified_at:string|null}[];
 readiness:{publication:boolean;access:boolean;owner:boolean;workflow:boolean;accepted_test:boolean;fresh_report:boolean};
 reports:{id:string;period_start:string;state:string;generated_at:string}[]};
export default function OwnerReportSettings({location}:{location:string}){
 const [state,setState]=useState<State|null>(null),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const [topics,setTopics]=useState(Object.keys(TOPICS).filter(x=>x!=='pipeline')),[day,setDay]=useState(0),[clock,setClock]=useState('08:00'),[mode,setMode]=useState('off');
 const [user,setUser]=useState(''),[ghlUser,setGhlUser]=useState(''),[email,setEmail]=useState(''),[workflow,setWorkflow]=useState(''),[version,setVersion]=useState('v1'),[secret,setSecret]=useState(''),[evidence,setEvidence]=useState('');
 const [verified,setVerified]=useState(false);
 async function refresh(){
  const r=await supabase.rpc('owner_admin_state',{p_location:location});
  if(r.error){setState(null);setNotice('Owner reporting setup is unavailable until its database update is applied and administrator verification is current.');return;}
  const s=r.data as State;setState(s);
  setTopics(s.settings?.topics ?? Object.keys(TOPICS).filter(x=>x!=='pipeline'));setDay(s.settings?.weekday ?? 0);setClock(s.settings?.local_time.slice(0,5) ?? '08:00');setMode(s.settings?.delivery_mode ?? 'off');
  setUser(s.recipient?.user_id ?? '');setEmail(s.recipient?.email ?? '');setGhlUser(s.recipient?.ghl_user_id ?? '');setWorkflow(s.destination?.workflow_id ?? '');setVersion(s.destination?.workflow_version ?? 'v1');
 }
 useEffect(()=>{setState(null);setSecret('');setVerified(false);setEvidence('');void refresh();},[location]);
 async function act(call:()=>PromiseLike<{data?:unknown;error:unknown}>,message='Saved. No email was sent.'){
  setBusy(true);setNotice('');
  try{const r=await call();if(r.error)throw Error();await refresh();setNotice(message);}
  catch{setNotice('Unable to save. Check administrator verification, owner membership and required setup.');}
  finally{setBusy(false);}
 }
 const input='mt-1 w-full rounded border p-2';
 return <section className="my-5 rounded-xl border bg-white p-5">
  <h2 className="text-xl font-semibold">Weekly owner report</h2>
  <p className="mt-2 text-sm text-muted">One account, one verified owner, one weekly summary. Existing SSP account-manager notifications are separate.</p>
  <p role="status" className="my-3 text-sm">{notice}</p>
  {state&&<>
   <p className="my-4 rounded bg-paper p-3 text-sm">Global delivery: <strong>{state.control.mode}</strong>. Account delivery: <strong>{state.settings?.delivery_mode ?? 'off'}</strong>. {state.control.mode==='off'?'No owner emails can be sent.':''}</p>
   <ul className="my-3 text-sm">{Object.entries({publication:'Report publication',access:'Owner viewing access',owner:'Verified report owner',workflow:'Verified workflow connection',accepted_test:'Approved inbox test',fresh_report:'Fresh weekly report'}).map(([key,label])=><li key={key}>{state.readiness[key as keyof typeof state.readiness]?'Ready':'Needs setup'} — {label}</li>)}</ul>
   <button disabled={busy||!state.settings} className="rounded border px-4 py-2" onClick={()=>void act(()=>supabase.rpc('owner_save_settings',{p_location:location,p_topics:state.settings!.topics,p_weekday:state.settings!.weekday,p_time:state.settings!.local_time,p_mode:'off'}),'Owner delivery paused. Already completed sends cannot be recalled.')}>Pause owner delivery</button>
   <h3 className="mt-5 font-semibold">1. Report topics and schedule</h3>
   <div className="my-3 flex flex-wrap gap-4">{Object.entries(TOPICS).map(([key,label])=><label className="text-sm" key={key}><input type="checkbox" checked={topics.includes(key)} onChange={e=>setTopics(e.target.checked?[...topics,key]:topics.filter(x=>x!==key))}/> {label}{key==='pipeline'?' (optional)':''}</label>)}</div>
   <div className="grid gap-4 sm:grid-cols-3">
    <label className="text-sm">Weekly day<select className={input} value={day} onChange={e=>setDay(Number(e.target.value))}>{['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'].map((d,i)=><option key={d} value={i}>{d}</option>)}</select></label>
    <label className="text-sm">Local send time<input className={input} type="time" value={clock} onChange={e=>setClock(e.target.value)}/></label>
    <label className="text-sm">Delivery mode<select className={input} value={mode} onChange={e=>setMode(e.target.value)}><option value="off">Off</option><option value="preview">Preview — no scheduled sending</option><option value="live" disabled={!Object.values(state.readiness).every(Boolean)}>Live — verification required</option></select></label>
   </div>
   <p className="my-3 text-xs text-muted">Uses the account timezone above. Sends the previous completed Monday–Sunday; the monthly dashboard does not enable monthly email. Changes pause unsent jobs.</p>
   <button disabled={busy||!topics.length} className="rounded border px-4 py-2" onClick={()=>void act(()=>supabase.rpc('owner_save_settings',{p_location:location,p_topics:topics,p_weekday:day,p_time:clock,p_mode:mode}))}>Save owner report settings</button>
   <h3 className="mt-6 font-semibold">2. Owner and GHL workflow</h3>
   {!state.members.length&&<p className="my-2 text-sm">First grant this account's verified person the Owner role under Assigned viewers. A GHL user and a dashboard membership are separate requirements.</p>}
   <div className="my-3 grid gap-4 sm:grid-cols-2">
    <label className="text-sm">Dashboard owner<select className={input} value={user} onChange={e=>{setUser(e.target.value);setEmail(state.members.find(m=>m.user_id===e.target.value)?.email ?? '');}}><option value="">Select verified owner</option>{state.members.map(m=><option value={m.user_id} key={m.user_id}>{m.email}</option>)}</select></label>
    <label className="text-sm">GHL owner user ID<input className={input} value={ghlUser} onChange={e=>setGhlUser(e.target.value)} maxLength={100}/></label>
    <label className="text-sm">Matching GHL email<input className={input} type="email" value={email} onChange={e=>setEmail(e.target.value)}/></label>
    <label className="text-sm">GHL workflow ID<input className={input} value={workflow} onChange={e=>setWorkflow(e.target.value)} maxLength={100}/></label>
    <label className="text-sm">Workflow template revision<input className={input} value={version} onChange={e=>setVersion(e.target.value)} maxLength={100}/></label>
   </div>
   <button disabled={busy||!user||!ghlUser||!email||!workflow} className="rounded border px-4 py-2" onClick={()=>void act(()=>supabase.rpc('owner_save_binding',{p_location:location,p_user:user,p_ghl_user:ghlUser,p_email:email,p_workflow:workflow,p_version:version}))}>Save binding and pause delivery</button>
   <h3 className="mt-6 font-semibold">3. Secure workflow connection</h3>
   <p className="my-2 text-sm">Destination: {state.destination?.secret_configured?'Configured — value hidden':'Not configured'}. Store only the approved account-specific GHL inbound endpoint.</p>
   <label className="text-sm">New webhook URL<input className={input} type="password" autoComplete="new-password" value={secret} onChange={e=>setSecret(e.target.value)}/></label>
   <button disabled={busy||!secret||!state.destination} className="mt-3 rounded border px-4 py-2" onClick={()=>{const value=secret;setSecret('');void act(()=>supabase.rpc('owner_save_destination',{p_location:location,p_url:value}));}}>Save securely</button>
   <h3 className="mt-6 font-semibold">4. Verify the actual workflow</h3>
   <p className="my-2 text-sm">Verify the selected GHL user's account/email, contactless claim response, denied replay and template mapping in Draft. This records evidence; it does not perform those tests.</p>
   <label className="block text-sm">Verification evidence<textarea className={input} rows={3} value={evidence} maxLength={500} onChange={e=>setEvidence(e.target.value)} placeholder="Record the workflow revision, selected user and verification results. Never paste secrets."/></label>
   <label className="my-3 block text-sm"><input type="checkbox" checked={verified} onChange={e=>setVerified(e.target.checked)}/> I verified the actual recipient and the claim/rendering behavior.</label>
   <button disabled={busy||!verified||evidence.trim().length<20||!state.destination?.secret_configured} className="rounded border px-4 py-2" onClick={()=>void act(()=>supabase.rpc('owner_verify_binding',{p_location:location,p_evidence:evidence}))}>Record verification</button>
   <h3 className="mt-6 font-semibold">5. Preview, test and delivery history</h3>
   <Link className="my-3 block underline" to={`/client/accounts/${encodeURIComponent(location)}?view=week`}>Preview weekly owner report</Link>
   <button disabled={busy||!state.destination?.adapter_verified_at} className="rounded border px-4 py-2" onClick={()=>void act(()=>supabase.rpc('owner_prepare_test',{p_location:location}),'Synthetic SSP test prepared. Review and approve it before the operator dispatches that specific test.')}>Prepare SSP test — no send</button>
   <p className="my-3 text-xs text-muted">Test sending is restricted to Matthew in SSP. Client activation also requires a recorded account-specific acceptance test under the rollout runbook. GHL acceptance does not confirm inbox delivery.</p>
   {state.history.map(h=><div key={h.id} className="my-3 rounded border p-3 text-sm"><p>{h.period_start} · {h.mode} · <strong>{h.status.replaceAll('_',' ')}</strong></p><p>{h.reason}</p><p className="mt-1 break-all text-xs text-muted">Reference: {h.id}</p>{h.mode==='test'&&h.status==='notification_action_recorded'&&!h.inbox_verified_at&&<button className="mt-2 underline" disabled={busy} onClick={()=>{if(window.confirm('Confirm you received this specific test, checked its recipient, formatting and saved report link?'))void act(()=>supabase.rpc('owner_accept_test',{p_id:h.id}),'Inbox verification recorded. Weekly delivery remains a separate activation.');}}>Record successful inbox and link check</button>}{h.mode==='test'&&h.status==='prepared'&&<button className="mt-2 underline" disabled={busy} onClick={()=>{if(window.confirm('Approve this specific synthetic test to mcarlson@smallscreenproducer.com? The operator must then dispatch its reference.'))void act(()=>supabase.rpc('owner_approve_test',{p_id:h.id}),'Specific test approved for operator dispatch. No email was sent by this screen.');}}>Approve this specific Matthew test</button>}</div>)}
  </>}
 </section>;
}
