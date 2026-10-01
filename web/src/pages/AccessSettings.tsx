// Administrators manage access through checked database functions, never tables.
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { supabase } from '../lib/supabase';
import type { ClientAccount } from '../lib/dashboard';
type Settings={location_id:string;access_enabled:boolean;publication_enabled:boolean;stale_hours:number;enabled_views:string[]};
type Member={location_id:string;user_id:string;email:string;role:string;active:boolean};
type Invitation={id:string;location_id:string;email:string;expires_at:string;accepted_at:string|null;revoked_at:string|null};
type Audit={id:number;at:string;action:string;location_id:string|null};
type Staff={user_id:string;email:string;active:boolean;all_accounts:boolean;is_admin:boolean};
export default function AccessSettings() {
  const [accounts,setAccounts]=useState<ClientAccount[]>([]);
  const [settings,setSettings]=useState<Settings[]>([]);
  const [members,setMembers]=useState<Member[]>([]);
  const [invitations,setInvitations]=useState<Invitation[]>([]);
  const [audit,setAudit]=useState<Audit[]>([]);
  const [staff,setStaff]=useState<Staff[]>([]),[manager,setManager]=useState('');
  const [location,setLocation]=useState('');
  const [timezone,setTimezone]=useState('America/Chicago'),[views,setViews]=useState<string[]>(['attention','week','month']);
  const [reports,setReports]=useState<{location_id:string;generated_at:string;coverage:{contacts:boolean;responses:boolean;conversations:boolean}}[]>([]);
  const [access,setAccess]=useState(false),[publish,setPublish]=useState(false),[hours,setHours]=useState(30);
  const [email,setEmail]=useState(''),[role,setRole]=useState('client_viewer');
  const [notice,setNotice]=useState(''),[invite,setInvite]=useState(''),[busy,setBusy]=useState(false);
  const [verified,setVerified]=useState(false),[factor,setFactor]=useState(''),[qr,setQr]=useState(''),[code,setCode]=useState('');
  async function refresh() {
    const a=await supabase.rpc('dashboard_accounts');setAccounts(a.data ?? []);
    const r=await supabase.rpc('dashboard_admin_state');
    if(r.error) {setVerified(false);setNotice('Verify your authenticator before managing client access.');return;}
    setVerified(true);setStaff(r.data.staff);setSettings(r.data.settings);setMembers(r.data.memberships);setInvitations(r.data.invitations);setAudit(r.data.audit);setReports(r.data.reports ?? []);
  }
  useEffect(()=>{void refresh();},[]);
  useEffect(()=>{const s=settings.find(x=>x.location_id===location);setAccess(s?.access_enabled ?? false);setPublish(s?.publication_enabled ?? false);setHours(s?.stale_hours ?? 30);},[settings,location]);
  useEffect(()=>{setTimezone(accounts.find(a=>a.location_id===location)?.timezone ?? 'America/Chicago');setViews(settings.find(s=>s.location_id===location)?.enabled_views ?? ['attention','week','month']);},[accounts,settings,location]);
  async function action(operation:()=>PromiseLike<{error:unknown}>) {
    setBusy(true);setNotice('');
    try {const r=await operation();if(r.error) throw new Error();await refresh();setNotice('Saved. Customer report emails remain disabled.');}
    catch {setNotice('Unable to save. Check your administrator verification and try again.');}
    finally {setBusy(false);}
  }
  async function prepareMfa() {
    setNotice('');const list=await supabase.auth.mfa.listFactors();
    const existing=list.data?.totp.find(f=>f.status==='verified');
    if(existing) {setFactor(existing.id);return;}
    const enrolled=await supabase.auth.mfa.enroll({factorType:'totp',friendlyName:'Account Health administrator'});
    if(enrolled.error) {setNotice('Unable to prepare authenticator.');return;}
    setFactor(enrolled.data.id);setQr(enrolled.data.totp.qr_code);
  }
  async function verifyMfa() {
    const r=await supabase.auth.mfa.challengeAndVerify({factorId:factor,code});setCode('');
    if(r.error) {setNotice('Authenticator code not accepted.');return;}
    setQr('');await refresh();
  }
  async function prepareInvite() {
    setBusy(true);setNotice('');setInvite('');
    try {
      const r=await supabase.functions.invoke('prepare-client-access',{body:{location_id:location,email,role}});
      if(r.error || !r.data?.token) throw new Error();
      setInvite(r.data.token);setEmail('');await refresh();setNotice('Access prepared. No email was sent. The code expires after seven days and is shown only here.');
    } catch {setNotice('Unable to prepare access. Verify the secure access function is deployed and administrator verification is current.');}
    finally{setBusy(false);}
  }
  return <main className="mx-auto max-w-5xl p-6"><h1 className="text-2xl font-semibold">Client dashboard access</h1><p className="my-3 text-sm text-muted">Account reports only. Customer emails and client acknowledgment controls are deferred.</p>
    <p role="status" className="my-3">{notice}</p>
    {!verified ? <section className="rounded border bg-white p-5"><h2 className="font-semibold">Administrator verification</h2><p className="my-2 text-sm">Use an authenticator app to protect membership and dashboard settings.</p><button className="rounded border px-3 py-2" onClick={()=>void prepareMfa()}>Set up or use authenticator</button>{qr && <img alt="Scan to set up your authenticator" src={qr} className="my-4 h-48 w-48"/>}{factor && <div className="mt-3"><label>Authenticator code <input className="rounded border p-2" inputMode="numeric" value={code} onChange={e=>setCode(e.target.value)}/></label><button className="ml-3 underline" onClick={()=>void verifyMfa()}>Verify</button></div>}</section> : <>
      <label className="block">Account <select className="my-3 max-w-full rounded border p-2" value={location} onChange={e=>{setLocation(e.target.value);setInvite('');}}><option value="">Choose an account</option>{accounts.map(a=><option key={a.location_id} value={a.location_id}>{a.name}</option>)}</select></label>
      {location && <>
        <section className="my-4 rounded border bg-white p-5"><h2 className="font-semibold">Report preferences and refresh</h2><label className="my-3 block">Account timezone <input className="rounded border p-2" value={timezone} onChange={e=>setTimezone(e.target.value)}/></label><p className="mb-3 text-sm">Use a timezone such as America/Chicago. Changes affect future collection periods; saved reports keep their original timezone.</p>{['attention','week','month'].map((v,i)=><label key={v} className="mr-4 inline-block"><input type="checkbox" checked={views.includes(v)} onChange={e=>setViews(e.target.checked?[...views,v]:views.filter(x=>x!==v))}/> {['Needs attention','This week','This month'][i]}</label>)}<button disabled={busy || !views.length} className="mt-3 rounded border px-3 py-2" onClick={()=>void action(()=>supabase.rpc('dashboard_report_preferences',{p_location:location,p_timezone:timezone,p_views:views}))}>Save preferences</button><p className="mt-3 text-sm">Views control presentation; assigned viewers remain authorized for this account's safe report history.</p>{reports.filter(r=>r.location_id===location).map(r=><p key={r.location_id} className="mt-3 text-sm">Latest published check: {new Date(r.generated_at).toLocaleString()} · Source records: {r.coverage.contacts && r.coverage.responses && r.coverage.conversations?'Complete':'Incomplete — review the report before interpreting counts'}</p>)}{!reports.some(r=>r.location_id===location) && <p className="mt-3 text-sm">No client report has been published yet.</p>}</section>
        <section className="my-4 rounded border bg-white p-5"><h2 className="font-semibold">SSP account access</h2><p className="my-2 text-sm">Assign an account manager here before restricting their portfolio access below. This does not change email routing.</p><label>Account manager <select className="m-2 rounded border p-2" value={manager} onChange={e=>setManager(e.target.value)}><option value="">Choose staff member</option>{staff.filter(s=>s.active).map(s=><option key={s.user_id} value={s.user_id}>{s.email}</option>)}</select></label><button className="rounded border px-3 py-2" disabled={busy || !manager} onClick={()=>void action(()=>supabase.rpc('dashboard_membership',{p_location:location,p_user:manager,p_role:'am',p_active:true}))}>Assign account access</button></section>
        <section className="my-4 rounded border bg-white p-5"><h2 className="font-semibold">Report controls</h2><label className="my-3 block"><input type="checkbox" checked={publish} onChange={e=>setPublish(e.target.checked)}/> Publish safe reports after collection</label><label className="my-3 block"><input type="checkbox" checked={access} onChange={e=>setAccess(e.target.checked)}/> Allow assigned clients to view reports</label><label>Mark stale after <input className="w-20 rounded border p-2" type="number" min="1" max="168" value={hours} onChange={e=>setHours(Number(e.target.value))}/> hours</label><button disabled={busy} className="ml-3 rounded bg-ink px-4 py-2 text-white" onClick={()=>void action(()=>supabase.rpc('dashboard_settings',{p_location:location,p_access:access,p_publish:publish,p_stale:hours}))}>Save controls</button><p className="mt-4 text-sm">Collector report publication must also be enabled by the operator. Access checks remain enforced even when embedded.</p><Link className="mt-3 block underline" to={`/client/accounts/${encodeURIComponent(location)}?embed=1`}>Preview client report</Link><label className="mt-3 block text-sm">Dashboard embed URL<input readOnly className="mt-1 w-full rounded border p-2" value={`${window.location.origin}/client/accounts/${encodeURIComponent(location)}?embed=1`}/></label></section>
        <section className="my-4 rounded border bg-white p-5"><h2 className="font-semibold">Assigned viewers</h2>{members.filter(m=>m.location_id===location).map(m=><div key={m.user_id} className="my-3 flex flex-wrap items-center gap-3 text-sm"><span>{m.email} · {m.role} · {m.active?'Active':'Revoked'}</span><button disabled={busy} className="underline" onClick={()=>void action(()=>supabase.rpc('dashboard_membership',{p_location:location,p_user:m.user_id,p_role:m.role,p_active:!m.active}))}>{m.active?'Revoke access':'Restore access'}</button></div>)}<h3 className="mt-5 font-medium">Prepare an invitation</h3><p className="my-2 text-sm">Creates an unverified sign-in identity and a one-time access code. It does not email the customer. Share access only after the pilot roster is approved.</p><label>Email <input type="email" className="m-2 rounded border p-2" value={email} onChange={e=>setEmail(e.target.value)}/></label><label>Role <select className="m-2 rounded border p-2" value={role} onChange={e=>setRole(e.target.value)}><option value="client_viewer">Viewer</option><option value="client_owner">Owner</option></select></label><button disabled={busy || !email} className="rounded border px-3 py-2" onClick={()=>void prepareInvite()}>Prepare access without sending</button>{invite && <label className="mt-3 block">One-time invitation code<input readOnly type="text" className="mt-1 w-full rounded border p-2 font-mono" value={invite}/></label>}
        {invitations.filter(i=>i.location_id===location && !i.accepted_at && !i.revoked_at).map(i=><p key={i.id} className="mt-3 text-sm">{i.email} · expires {i.expires_at.slice(0,10)} <button className="underline" disabled={busy} onClick={()=>void action(()=>supabase.rpc('dashboard_revoke_invite',{p_id:i.id}))}>Revoke invitation</button></p>)}</section>
      </>}
      <details className="my-5 rounded border p-4"><summary>SSP staff permissions</summary><p className="my-2 text-sm">Existing staff retain portfolio access until their assigned-account roster is reviewed.</p>{staff.map(s=><div key={s.user_id} className="my-3 flex flex-wrap gap-3 text-sm"><span>{s.email} · {s.is_admin?'Administrator':s.active?(s.all_accounts?'All accounts':'Assigned accounts'):'Revoked'}</span>{!s.is_admin && <><button className="underline" disabled={busy} onClick={()=>void action(()=>supabase.rpc('dashboard_staff',{p_user:s.user_id,p_active:s.active,p_all:!s.all_accounts}))}>{s.all_accounts?'Use assigned accounts':'Allow all accounts'}</button><button className="underline" disabled={busy} onClick={()=>void action(()=>supabase.rpc('dashboard_staff',{p_user:s.user_id,p_active:!s.active,p_all:s.all_accounts}))}>{s.active?'Revoke staff access':'Restore staff access'}</button></>}</div>)}</details>
      <details className="my-5 rounded border p-4"><summary>Recent access activity</summary>{audit.map(a=><p key={a.id} className="mt-2 text-xs">{a.at} · {a.action} · {accounts.find(x=>x.location_id===a.location_id)?.name ?? 'Administration'}</p>)}</details>
    </>}
  </main>;
}
