// Weekly/monthly owner view; the same immutable snapshot feeds the email.
import {useEffect,useState} from 'react';
import {useSearchParams} from 'react-router-dom';
import {supabase} from '../lib/supabase';
import {displayDuration,displayNumber} from '../lib/dashboard';
import {chooseReport,ownerFresh,TOPICS,type OwnerSnapshot} from '../lib/ownerReports';

export default function OwnerReportPanel({location,staleHours,enabledViews,onAvailability}:{location:string;staleHours:number;enabledViews:string[];onAvailability:(available:boolean)=>void}) {
 const [params,setParams]=useSearchParams();
 const [rows,setRows]=useState<OwnerSnapshot[]>([]),[error,setError]=useState(''),[ready,setReady]=useState(false),[expanded,setExpanded]=useState(false);
 const exact=params.get('report'),requested=params.get('view')==='month'?'month':params.get('view')==='attention'?'attention':'week';
 const kind=enabledViews.includes(requested)?requested:enabledViews[0];
 useEffect(()=>{
  let stopped=false;
  setRows([]);setReady(false);setError('');onAvailability(!!exact);
  async function refresh(){
   const r=await supabase.rpc('owner_reports',{p_location:location,p_report:exact});
   if(stopped)return;
   setReady(true);
   if(r.error){setError(exact?'This saved report is unavailable. Check your access with SSP.':'');onAvailability(!!exact);return;}
   const values=(r.data ?? []) as OwnerSnapshot[];
   setRows(values);onAvailability(!!exact || values.length>0);
   setError(exact && !values.some(v=>v.id===exact)?'This saved report is unavailable for this account.':'');
  }
  void refresh();const timer=window.setInterval(()=>void refresh(),30000);
  return()=>{stopped=true;window.clearInterval(timer);};
 },[location,exact,onAvailability]);
 if(error)return <p role="alert" className="rounded border bg-white p-5">{error}</p>;
 if(!ready || !rows.length)return exact?<p role="status">Loading saved report…</p>:null;
 const availableRows=rows.filter(r=>enabledViews.includes(r.period_kind));
 const selected=chooseReport(availableRows,kind,params.get('period'),exact);
 const options=rows.filter(r=>r.period_kind===kind).sort((a,b)=>b.period_start.localeCompare(a.period_start));
 function change(k:string,value:string){const next=new URLSearchParams(params);next.delete('report');next.delete('period');next.set(k,value);setParams(next);}
 const n=(value:unknown)=>displayNumber(typeof value==='number'?value:null);
 const time=(value:string|null)=>value?new Date(value).toLocaleString(undefined,{timeZone:selected?.data.timezone}):'Unavailable';
 return <section>
  <div className="mb-4 flex flex-wrap gap-2">{['week','month','attention'].map((view,i)=>enabledViews.includes(view)&&<button key={view} className={`rounded-full border px-4 py-2 text-sm ${view===kind?'bg-ink text-white':'bg-white'}`} aria-pressed={view===kind} onClick={()=>change('view',view)}>{['Weekly','Monthly','Current queue'][i]}</button>)}</div>
  {exact?<p className="mb-4 rounded border bg-blue-50 p-3 text-sm">Saved report shared in your email. <button className="underline" onClick={()=>change('view',kind)}>Open latest report</button></p>:<label className="mb-4 block text-sm">Reporting period <select className="ml-2 rounded border p-2" value={selected?.period_start ?? ''} onChange={e=>change('period',e.target.value)}>{options.map(r=><option key={r.id} value={r.period_start}>{r.period_start}{r.data.to_date?' · so far':' · completed window'}</option>)}</select></label>}
  {!selected?<p className="rounded border p-4">No report exists for this period.</p>:(()=>{
   const d=selected.data,m=d.metrics,q=d.queue,fresh=ownerFresh(selected,staleHours);
   const limited=d.state==='limited'||(!exact&&!fresh);
   const topics=d.topics ?? [];
   const actions=expanded?d.actions:d.actions.slice(0,3);
   return <>
    {d.sample&&<p className="my-3 rounded border p-3 font-semibold">TEST — FICTIONAL DATA. This is a template demonstration.</p>}<p className="mb-4 text-xs text-muted">{time(d.period_start)} – {time(new Date(Date.parse(d.period_end)-1).toISOString())} · {d.timezone}</p>
    <div className={`mb-5 rounded-xl border p-5 ${limited?'border-blue-200 bg-blue-50':d.state==='healthy'?'border-green-200 bg-green-50':'border-amber-200 bg-amber-50'}`}>
      <h2 className="text-lg font-semibold">{limited?'Some data needs another check':d.state==='healthy'?'No issues found in the selected topics':'What needs correction'}</h2>
      <p className="mt-2 text-sm">{d.data_note}</p>{!fresh&&<p className="mt-2 text-sm">{exact?'This is the saved queue as observed when the report was prepared. Open the latest report for current work.':'The current queue is stale. Confirm activity in GHL before acting.'}</p>}
    </div>
    <div className="grid gap-4 md:grid-cols-3">{actions.map(a=><article key={a.topic} className="rounded-xl border bg-white p-5">
      <p className="text-xs font-semibold uppercase text-muted">{a.status}</p><h3 className="mt-2 text-lg font-semibold">{a.title}</h3><p className="mt-2 text-sm">{a.detail}</p><p className="mt-4 rounded bg-paper p-3 text-sm"><strong>Next step:</strong> {a.next_step}</p><a className="mt-3 inline-block text-sm underline" target="_blank" rel="noreferrer" href={`https://crm.smallscreenproducer.com/v2/location/${encodeURIComponent(location)}/${a.topic==='waiting'?'conversations/conversations':a.topic==='pipeline'?'opportunities/list':'contacts/smart_list/All'}`}>Review in GHL</a>
    </article>)}</div>
    {d.actions.length>3&&<button className="my-3 underline" onClick={()=>setExpanded(!expanded)}>{expanded?'Show top three':`Show all ${d.actions.length} priorities`}</button>}
    {!d.actions.length&&<p className="mb-5 text-sm">{limited?'Missing data prevents an all-clear conclusion.':'Keep ownership and follow-up routines in place.'}</p>}
    <h2 className="mb-3 mt-6 text-lg font-semibold">Reporting period results</h2>
    <div className="grid gap-4 sm:grid-cols-3">
     {topics.includes('lead_flow')&&<Card title="New leads" value={n(m.new_leads)} detail={d.comparison?`${n(d.comparison.previous)} ${d.comparison.label ?? 'prior period'} · ${d.comparison.change===null?'No comparable percentage':d.comparison.change+'% change'}`:'No comparable prior period'}/>}
     {topics.includes('follow_up')&&<Card title="Follow-up recorded" value={m.eligible===null?'Unavailable':`${n(m.contacted)} of ${n(m.eligible)}`} detail="Eligible leads aged 24 hours with outbound activity by cutoff. This is not a response-within-24-hours rate."/>}
     {topics.includes('speed')&&<Card title={m.speed_human?'Typical first human response':'Typical first outbound response'} value={displayDuration(typeof m.speed_median==='number'?m.speed_median:null)} detail={`90th percentile: ${displayDuration(typeof m.speed_p90==='number'?m.speed_p90:null)} · ${n(m.speed_samples)} measured responses`}/>}
    </div>
    <section className="my-6 rounded-xl border bg-white p-5"><h2 className="text-lg font-semibold">Current follow-up queue</h2><p className="mt-1 text-xs text-muted">Observed {time(d.current_as_of)} · Latest check, not a weekly or monthly total.</p><div className="mt-4 grid gap-3 sm:grid-cols-3">{topics.includes('follow_up')&&<p><strong>{n(q.uncontacted)}</strong> recent leads overdue</p>}{topics.includes('waiting')&&<p><strong>{n(q.waiting)}</strong> waiting replies · oldest {n(q.oldest_wait_hours)} hours</p>}{topics.includes('ownership')&&<p><strong>{n(q.unassigned)}</strong> unassigned leads</p>}</div></section>
    <section className="my-6 rounded-xl border bg-white p-5"><h2 className="text-lg font-semibold">Patterns to correct</h2>{d.patterns.length?d.patterns.map(p=><p className="mt-3 text-sm" key={p.topic}>{TOPICS[p.topic]}: observed on {p.observed_checks} of {p.valid_checks} valid checks across {p.weeks} weeks{p.recurring?' · recurring':''}.</p>):<p className="mt-3 text-sm">No pattern established in the available checks. Missing checks do not prove an issue was resolved.</p>}</section>
    <a className="inline-block rounded bg-ink px-4 py-3 text-white" target="_blank" rel="noreferrer" href={`https://crm.smallscreenproducer.com/v2/location/${encodeURIComponent(location)}/contacts/smart_list/All`}>Open account in GHL</a>
    <details className="mt-5 rounded border p-4 text-sm"><summary>How to read this report</summary><p className="mt-3">Period metrics use a consistent local cutoff and exclude today's incomplete day. Speed uses responded leads only. Current queues use their existing rolling windows; a smaller queue does not prove leads were contacted. Monthly history can be incomplete. Report {selected.id} · {d.template_version}.</p></details>
   </>;
  })()}
 </section>;
}
function Card({title,value,detail}:{title:string;value:string;detail:string}){return <article className="rounded-xl border bg-white p-5"><h3 className="text-sm text-muted">{title}</h3><p className="my-3 text-2xl font-semibold">{value}</p><p className="text-xs text-muted">{detail}</p></article>;}
