// GHL Custom Code action. Replace PINNED_WORKFLOW after verifying the saved workflow ID.
// Input property envelope must be mapped using GHL's Inbound Webhook custom-value picker.
// SSP production adapter: test the exact production binding before enabling live.
// Preserve the envelope mode; the database binds credentials to that mode.
// Never log inputData or credentials. The placeholder workflow refuses all real bindings.
const LOCATION = "ZnckuEDPIcWu8fn72ppi";
const WORKFLOW = "SET_VERIFIED_SSP_PRODUCTION_WORKFLOW_ID";

output={allow_send:"false",subject:"",body_html:"",body_text:"",report_state:"",delivery_id:"",completion_credential:""};
try {
 const e=JSON.parse(inputData.envelope||"{}");
 if(e.event==="owner_followup_weekly" && e.schema_version==="1" && ["test","live"].includes(e.mode) && e.location_id===LOCATION && e.workflow_binding===WORKFLOW && /^[A-Za-z0-9_-]{43,128}$/.test(e.claim_credential||"")) {
  const r=await customRequest.post("https://tpavdifpsevkrubplyrg.supabase.co/functions/v1/owner-report-claim",{data:{delivery_id:e.delivery_id,location_id:LOCATION,workflow_binding:WORKFLOW,recipient_binding_version:e.recipient_binding_version,mode:e.mode,claim_credential:e.claim_credential},headers:{"Content-Type":"application/json"}});
  const c=r.data;
  if(r.status===200 && c && c.allow_send==="true" && ["attention","healthy","limited"].includes(c.report_state)) output={allow_send:"true",subject:c.subject,body_html:c.body_html,body_text:c.body_text,report_state:c.report_state,delivery_id:e.delivery_id,completion_credential:e.completion_credential};
 }
} catch(_) { /* Fail closed; do not retry or log provider errors. */ }
