// Map delivery_id and completion_credential from the successful claim action.
// Place only after the fixed-recipient internal email action. No inbox-delivered claim.
output={recorded:"false"};
try {
 if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(inputData.delivery_id||"") && /^[A-Za-z0-9_-]{43,128}$/.test(inputData.completion_credential||"")) {
 const r=await customRequest.post("https://tpavdifpsevkrubplyrg.supabase.co/functions/v1/owner-report-result",{data:{delivery_id:inputData.delivery_id,completion_credential:inputData.completion_credential,result:"notification_action_recorded"},headers:{"Content-Type":"application/json"}});
 if(r.status===200 && r.data && r.data.recorded===true) output={recorded:"true"};
 }
} catch(_) { /* Review the execution; never resend from this callback. */ }
