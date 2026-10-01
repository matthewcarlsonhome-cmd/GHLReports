// Provision an invited identity without sending mail. Caller JWT is checked
// before the service client is used; account grants require verified acceptance.
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2.45.0';
const origin = Deno.env.get('DASHBOARD_ORIGIN') ?? 'https://mlhaccountreports.netlify.app';
const headers = {'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Headers':'authorization, apikey, content-type, x-client-info',
  'Access-Control-Allow-Methods':'POST, OPTIONS','Cache-Control':'no-store','Content-Type':'application/json','Vary':'Origin'};
Deno.serve(async (req: Request) => {
  const reply=(status:number,body:unknown)=>new Response(JSON.stringify(body),{status,headers});
  if(req.headers.get('origin') && req.headers.get('origin')!==origin) return reply(403,{error:'Not allowed'});
  if(req.method==='OPTIONS') return new Response(null,{status:204,headers});
  if(req.method!=='POST') return reply(405,{error:'Not allowed'});
  try {
    const url=Deno.env.get('SUPABASE_URL')!;
    const caller=createClient(url,Deno.env.get('SUPABASE_ANON_KEY')!,{global:{headers:{Authorization:req.headers.get('authorization') ?? ''}},auth:{persistSession:false}});
    const {data:user,error:authError}=await caller.auth.getUser();
    if(authError || !user.user) return reply(401,{error:'Sign in required'});
    const check=await caller.rpc('require_dashboard_admin');
    if(check.error) return reply(403,{error:'Administrator verification required'});
    const body=await req.json();
    if(Object.keys(body).some(k=>!['email','location_id','role'].includes(k)) || typeof body.location_id!=='string' || body.location_id.length>100 ||
      typeof body.email!=='string' || body.email.length>254 || !/^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/.test(body.email.trim()) || !['client_owner','client_viewer'].includes(body.role)) return reply(400,{error:'Check invitation details'});
    const email=body.email.trim().toLowerCase();
    const invite=await caller.rpc('dashboard_invite',{p_location:body.location_id,p_email:email,p_role:body.role});
    if(invite.error) return reply(400,{error:'Invitation unavailable'});
    const service=createClient(url,Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!,{auth:{persistSession:false}});
    const created=await service.auth.admin.createUser({email,email_confirm:false});
    if(created.error && !['email_exists','user_already_exists'].includes(created.error.code ?? '')) {
      await caller.rpc('dashboard_revoke_invite',{p_id:invite.data.id});
      return reply(502,{error:'Unable to prepare access'});
    }
    return reply(200,invite.data);
  } catch {return reply(500,{error:'Unable to prepare access'});}
});
