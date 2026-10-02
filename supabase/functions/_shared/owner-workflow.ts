// Bounded, credential-authenticated contactless workflow endpoints. No request logging.
import {createClient} from 'https://esm.sh/@supabase/supabase-js@2.45.0';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const TOKEN=/^[A-Za-z0-9_-]{43,128}$/;
const buckets=new Map<string,{at:number;count:number}>();
export function allowedRate(key:string,now=Date.now()) {
  if(buckets.size>1000) {for(const [k,v] of buckets) if(now-v.at>60000) buckets.delete(k);if(buckets.size>1000)return false;}
  const old=buckets.get(key);
  if(!old || now-old.at>60000){buckets.set(key,{at:now,count:1});return true;}
  return ++old.count<=20;
}
export async function handle(req:Request,kind:'claim'|'result',env:(key:string)=>string|undefined,clientFactory=createClient) {
  const reply=(status:number,value:unknown)=>new Response(JSON.stringify(value),{status,headers:{
    'Content-Type':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
  if(req.method!=='POST')return reply(405,{error:'Not allowed'});
  if(!allowedRate(req.headers.get('x-forwarded-for')?.split(',')[0] ?? 'unknown'))return reply(429,{error:'Try later'});
  if(!['preview','live'].includes(env('OWNER_REPORTS_MODE') ?? 'off'))return reply(403,{error:'Unavailable'});
  if(Number(req.headers.get('content-length') || 0)>4096)return reply(413,{error:'Request too large'});
  try{
    const reader=req.body?.getReader();if(!reader)return reply(400,{error:'Invalid request'});
    const chunks:Uint8Array[]=[];let size=0;
    while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>4096){await reader.cancel();return reply(413,{error:'Request too large'});}chunks.push(value);}
    const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
    const body=JSON.parse(new TextDecoder().decode(bytes));
    const fields=kind==='claim'?['delivery_id','location_id','workflow_binding','recipient_binding_version','mode','claim_credential']:
      ['delivery_id','completion_credential','result'];
    if(!body || typeof body!=='object' || Array.isArray(body) || Object.keys(body).some(k=>!fields.includes(k)) ||
      !UUID.test(body.delivery_id ?? ''))return reply(400,{error:'Invalid request'});
    const credential=kind==='claim'?body.claim_credential:body.completion_credential;
    if(typeof credential!=='string' || !TOKEN.test(credential))return reply(400,{error:'Invalid request'});
    if(kind==='claim' && (!['test','live'].includes(body.mode) ||
      typeof body.location_id!=='string' || !/^[A-Za-z0-9_-]{1,100}$/.test(body.location_id) ||
      typeof body.workflow_binding!=='string' || !/^[A-Za-z0-9_-]{1,100}$/.test(body.workflow_binding) ||
      !/^[1-9][0-9]{0,8}$/.test(String(body.recipient_binding_version))))return reply(400,{error:'Invalid request'});
    if(kind==='claim' && body.mode==='live' && env('OWNER_REPORTS_MODE')!=='live')return reply(403,{error:'Unavailable'});
    if(kind==='result' && !['notification_action_recorded','failed'].includes(body.result))return reply(400,{error:'Invalid result'});
    const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(credential));
    const hash=Array.from(new Uint8Array(digest)).map(x=>x.toString(16).padStart(2,'0')).join('');
    const service=clientFactory(env('SUPABASE_URL')!,env('SUPABASE_SERVICE_ROLE_KEY')!,{auth:{persistSession:false}});
    const result=kind==='claim'?await service.rpc('owner_claim',{p_id:body.delivery_id,p_location:body.location_id,
      p_workflow:body.workflow_binding,p_recipient:Number(body.recipient_binding_version),p_mode:body.mode,p_hash:hash}):
      await service.rpc('owner_result',{p_id:body.delivery_id,p_hash:hash,p_result:body.result});
    if(result.error)return reply(403,{error:'Unavailable'});
    return reply(200,kind==='claim'?result.data:{recorded:result.data===true});
  }catch{return reply(400,{error:'Unavailable'});}
}
