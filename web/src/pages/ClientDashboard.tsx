// Client-only aggregate report. Every fetch is independently checked by RLS/RPC.
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { supabase } from '../lib/supabase';
import { useAccess } from '../lib/useAccess';
import { displayDuration, displayNumber, leadTrend, periodLabel, staleReport, type ClientAccount, type ClientReport } from '../lib/dashboard';

export default function ClientDashboard() {
  const {locationId}=useParams();
  const [query,setQuery]=useSearchParams();
  const navigate=useNavigate();
  const {userId,admin}=useAccess();
  const [accounts,setAccounts]=useState<ClientAccount[]>([]);
  const [reports,setReports]=useState<ClientReport[]>([]);
  const [busy,setBusy]=useState(true);
  const [error,setError]=useState('');
  const [token,setToken]=useState('');
  const [notice,setNotice]=useState('');
  const [revision,setRevision]=useState(0);
  const requested=query.get('view') === 'week' ? 'week' : query.get('view') === 'month' ? 'month' : 'attention';
  useEffect(() => {
    let cancelled=false, sequence=0;
    setAccounts([]);setReports([]);setBusy(true);setError('');
    async function refresh() {
      const ticket=++sequence;
      try {
        const a=await supabase.rpc('dashboard_accounts');
        if(a.error) throw new Error('Reports are not available yet. Please contact SSP.');
        if(cancelled || ticket!==sequence) return;
        const allowed=(a.data ?? []) as ClientAccount[];
        setAccounts(allowed);
        if(!locationId && allowed.length===1) {
          navigate(`/client/accounts/${encodeURIComponent(allowed[0].location_id)}?${query}`,{replace:true});return;
        }
        if(locationId && !allowed.some(item=>item.location_id===locationId)) {
          setReports([]);throw new Error('This report is unavailable for your account.');
        }
        if(locationId) {
          const r=await supabase.rpc('dashboard_reports',{p_location:locationId});
          if(r.error) throw new Error('Unable to refresh this report. Please try again.');
          if(cancelled || ticket!==sequence) return;
          setReports((r.data ?? []).filter((item: ClientReport)=>item.data?.schema_version===1));
        }
        setError('');
      } catch(e) {
        if(!cancelled && ticket===sequence) {setReports([]);setAccounts([]);setError(e instanceof Error ? e.message : 'Report unavailable.');}
      } finally {if(!cancelled && ticket===sequence) setBusy(false);}
    }
    void refresh();
    const timer=window.setInterval(()=>void refresh(),30000);
    const focus=()=>void refresh();window.addEventListener('focus',focus);
    return()=>{cancelled=true;window.clearInterval(timer);window.removeEventListener('focus',focus);};
    // View/period controls never change the access scope or fetch identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[locationId,userId,revision,navigate]);
  const account=accounts.find(a=>a.location_id===locationId);
  const views=account?.enabled_views ?? ['attention','week','month'];
  const tab=views.includes(requested) ? requested : views[0];
  const options=reports.filter(r=>r.period_kind===tab).sort((a,b)=>b.period_start.localeCompare(a.period_start));
  const latest=options.find(r=>r.period_start===query.get('period')) ?? options[0];
  const selected=query.get('previous')==='1' && latest?.previous_complete ? latest.previous_complete : latest;
  const metric=selected?.data.metrics;
  const stale=selected && account && (tab==='attention' || selected.data.to_date) ? staleReport(selected,account.stale_hours) : false;
  const previousComplete=options.find(r=>r!==selected && r.generated_at<(selected?.generated_at ?? '') && r.data.coverage.contacts && r.data.coverage.responses && (tab!=='attention' || r.data.coverage.conversations));
  const trend=selected ? leadTrend(selected,options[options.indexOf(selected)+1]) : null;
  async function accept() {
    setNotice('');
    const {error}=await supabase.rpc('dashboard_accept_invite',{p_token:token});
    setToken('');setNotice(error ? 'Invitation unavailable. Check the code and signed-in email with SSP.' : 'Access accepted. Reports appear when SSP enables the dashboard.');
    if(!error) setRevision(x=>x+1);
  }
  function view(value:string) {const next=new URLSearchParams(query);next.set('view',value);next.delete('period');next.delete('previous');setQuery(next);}
  return <main className="mx-auto max-w-6xl px-4 py-6 sm:px-8">
    {admin && <p className="mb-4 rounded border border-blue-200 bg-blue-50 p-3 text-sm">Administrator preview · Read-only client report <Link className="underline" to="/access">Manage access</Link></p>}
    <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-xs uppercase tracking-widest text-muted">Account follow up</p><h1 className="mt-1 break-words text-2xl font-semibold">{account?.name ?? 'Your reports'}</h1><p className="mt-2 text-sm text-muted">A clear view of lead response and outstanding follow-up.</p></div>
      {accounts.length>1 && <label className="text-sm">Account<select className="ml-2 max-w-full rounded border p-2" value={locationId ?? ''} onChange={e=>navigate(`/client/accounts/${encodeURIComponent(e.target.value)}?embed=1`)}><option value="" disabled>Select an account</option>{accounts.map(a=><option key={a.location_id} value={a.location_id}>{a.name}</option>)}</select></label>}
    </header>
    {busy ? <p role="status">Loading your report…</p> : error ? <div role="alert" className="rounded border bg-white p-5">{error} <button className="underline" onClick={()=>setRevision(x=>x+1)}>Try again</button></div> : null}
    {!busy && !account && !error && <div className="rounded border bg-white p-5"><p>{accounts.length ? 'Select an account to open its report.' : 'No reports are assigned or enabled yet. Contact SSP for access.'}</p></div>}
    {!busy && (!account || query.get('join')==='1') && <details className="my-4 text-sm"><summary className="cursor-pointer text-muted">Have an invitation code?</summary><p className="my-2">Sign in with the email approved by SSP, then enter your one-time invitation code.</p><label>Invitation code<input type="password" className="mx-2 rounded border p-2" autoComplete="off" value={token} onChange={e=>setToken(e.target.value)}/></label><button disabled={!token} className="rounded border px-3 py-2 disabled:opacity-40" onClick={()=>void accept()}>Accept access</button><p role="status">{notice}</p></details>}
    {account && !error && <>
      <div className="mb-5 flex flex-wrap gap-2" aria-label="Report views">{(['attention','week','month'] as const).map((t,i)=>views.includes(t) && <button key={t} aria-pressed={tab===t} onClick={()=>view(t)} className={`rounded-full border px-4 py-2 text-sm ${tab===t?'bg-ink text-white':'bg-white'}`}>{['Needs attention','This week','This month'][i]}</button>)}</div>
      {options.length>0 && <label className="mb-4 block text-sm">{tab==='attention'?'Recorded window starting':'Reporting period'} <select className="ml-2 rounded border p-2" value={selected?.period_start ?? ''} onChange={e=>{const next=new URLSearchParams(query);next.set('period',e.target.value);next.delete('previous');setQuery(next);}}>{options.map(r=><option key={r.period_start} value={r.period_start}>{r.period_start}{r.data.to_date?' · to date':''}</option>)}</select></label>}
      {!selected ? <p className="rounded border bg-white p-5">A report has not been published for this view yet.</p> : <>
        <p className="mb-4 text-xs text-muted">{periodLabel(selected)} · {selected.data.timezone} · Last checked {new Date(selected.data.data_through).toLocaleString(undefined,{timeZone:selected.data.timezone})}{selected.data.to_date?' · Period to date':''}</p>
        {selected!==latest && <p role="status" className="mb-4 rounded border border-amber-300 bg-amber-50 p-4">Showing an earlier complete report. Its figures are from the last checked time above. <button className="underline" onClick={()=>{const next=new URLSearchParams(query);next.delete('previous');setQuery(next);}}>Return to the latest check</button></p>}
        {selected===latest && latest.previous_complete && (!latest.data.coverage.contacts || !latest.data.coverage.responses || tab==='attention' && !latest.data.coverage.conversations) && <p className="mb-4 text-sm"><button className="underline" onClick={()=>{const next=new URLSearchParams(query);next.set('previous','1');setQuery(next);}}>View the last complete version of this reporting period</button></p>}
        {stale && <p role="status" className="mb-4 rounded border border-amber-300 bg-amber-50 p-4">This report is stale. These are the last recorded figures; confirm current activity in GHL before acting.</p>}
        {(!selected.data.coverage.contacts || !selected.data.coverage.responses || tab==='attention' && !selected.data.coverage.conversations) && <p className="mb-4 rounded border bg-white p-4 text-sm">Some figures are unavailable because their source records are incomplete. Missing data does not mean no activity. {previousComplete && <button className="underline" onClick={()=>{const next=new URLSearchParams(query);next.set('period',previousComplete.period_start);next.delete('previous');setQuery(next);}}>View an earlier complete reporting window</button>}</p>}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card title="New leads" value={displayNumber(metric?.new_leads)} detail={trend===null?'Leads created in this reporting period':`${trend>0?'+':''}${trend}% versus the previous completed period`}/>
          <Card title="Speed to lead" value={displayDuration(metric?.speed_median ?? null)} detail={metric?.speed_samples==null?'Complete response history is not available for this period':`${metric.speed_human?'First human response':'First response, including automation'} · ${displayNumber(metric.speed_samples)} measured responses`}/>
          <Card title="Follow-up completion" value={displayNumber(metric?.completion_pct,'%')} detail={metric?.eligible==null?'Complete follow-up records are not available for this period':`${displayNumber(metric.contacted)} of ${displayNumber(metric.eligible)} leads aged at least 24 hours received outbound follow-up`}/>
          <Card title={tab==='attention'?'Waiting replies':'No outbound follow-up'} value={displayNumber(tab==='attention'?metric?.waiting:metric?.uncontacted)} detail={tab==='attention'?`Oldest wait: ${displayNumber(metric?.oldest_wait_hours,' hours')} · weekend-adjusted`:'Eligible leads without an outbound response at the period cutoff'}/>
        </div>
        <section className="mt-6 rounded-xl border bg-white p-5"><h2 className="text-lg font-semibold">{stale?'Last recorded follow-up':'Follow-up to review'}</h2>
          {(metric?.uncontacted ?? 0)>0 && <p className="mt-3"><strong>{metric?.uncontacted} leads</strong> have no recorded outbound follow-up after 24 hours. Review their next action and ownership in GHL.</p>}
          {tab==='attention' && (metric?.waiting ?? 0)>0 && <p className="mt-3"><strong>{metric?.waiting} conversations</strong> are waiting for a reply. Review the oldest conversations first.</p>}
          {(metric?.unassigned ?? 0)>0 && <p className="mt-3"><strong>{metric?.unassigned} leads</strong> have no assigned owner. Confirm who will follow up.</p>}
          {metric?.uncontacted===0 && (tab!=='attention' || metric?.waiting===0) && <p className="mt-3">No overdue follow-up recorded in the measured window{stale?' at the last check':''}.</p>}
          <a className="mt-4 inline-block rounded bg-ink px-4 py-2 text-sm text-white" href={`https://crm.smallscreenproducer.com/v2/location/${encodeURIComponent(account.location_id)}/contacts/smart_list/All`} target="_blank" rel="noreferrer">Open account in GHL ↗</a>
        </section>
        <details className="mt-5 rounded border p-4 text-sm"><summary className="cursor-pointer font-medium">How to read this report</summary><p className="mt-3">Calendar weeks start Monday. Completed periods use the same local cutoff for lead creation and response events. Today’s incomplete day is excluded. Speed uses responded leads only; unanswered leads are counted separately. Median and 90th percentile are calculated from individual response times, never daily averages.</p><p className="mt-2">90th percentile response: {displayDuration(metric?.speed_p90 ?? null)}. Human versus automated classification follows the existing collector rules and may be unavailable. Monthly response history is unavailable when the recent response scan cannot cover every lead.</p><p className="mt-2">Needs attention retains the existing seven-day new-lead window and fourteen-day conversation lookback. Counts can age out of those windows. Waiting replies are a current queue, not a monthly total. Ownership is observed at collection time. These figures do not indicate whether an SSP alert was sent.</p></details>
      </>}
    </>}
  </main>;
}
function Card({title,value,detail}:{title:string;value:string;detail:string}) {return <section className="rounded-xl border bg-white p-5"><h2 className="text-sm text-muted">{title}</h2><p className="my-3 text-3xl font-semibold tabular">{value}</p><p className="text-xs leading-relaxed text-muted">{detail}</p></section>;}
