// Owner snapshots contain only safe aggregate data. Routing stays administrator-only.
export type OwnerAction={topic:string;title:string;detail:string;next_step:string;status:string;severity:string};
export type OwnerSnapshot={id:string;period_start:string;period_kind:'attention'|'week'|'month';generated_at:string;data:{
 account_name:string;location_id:string;timezone:string;period_start:string;period_end:string;current_as_of:string|null;
 sample?:boolean;to_date:boolean;state:'attention'|'healthy'|'limited';stale:boolean;topics:string[];
 metrics:Record<string,number|boolean|null>;queue:Record<string,number|boolean|null>;
 actions:OwnerAction[];patterns:{topic:string;observed_checks:number;valid_checks:number;weeks:number;recurring:boolean}[];
 comparison:{previous:number;change:number|null;label?:string}|null;data_note:string;template_version:string;
}};
export const TOPICS:Record<string,string>={follow_up:'Overdue follow-up',waiting:'Waiting replies',speed:'Speed to lead',ownership:'Unassigned leads',lead_flow:'Lead flow',pipeline:'Pipeline inactivity'};
export function chooseReport(rows:OwnerSnapshot[],kind:string,period:string|null,exact:string|null) {
 if(exact)return rows.find(r=>r.id===exact);
 const options=rows.filter(r=>r.period_kind===kind).sort((a,b)=>b.period_start.localeCompare(a.period_start));
 if(period)return options.find(r=>r.period_start===period);
 return kind==='week'?(options.find(r=>!r.data.to_date) ?? options[0]):options[0];
}
export function ownerFresh(row:OwnerSnapshot,hours:number,now=Date.now()) {
 const value=Date.parse(row.data.current_as_of ?? '');
 return Number.isFinite(value) && now-value>=-300000 && now-value<=hours*3600000;
}
