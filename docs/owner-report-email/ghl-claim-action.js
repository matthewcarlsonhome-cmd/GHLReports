// Historical design reference, not a deployed workflow. Do not paste into GHL.
// The current verified GHL HTTP API and mappings are in ../owner-workflow/
// claim-action.js, production-claim-action.js and result-action.js.
// Configure fixed CLAIM_URL, LOCATION_ID, WORKFLOW_ID and MODE in input properties.
// Map only delivery_id, recipient_binding_version and claim_credential from inbound.
// Requires live proof that this action supports fetch and returned-field branching.
// Never console.log inputData or a request/response. Secrets are one-use.
const approved = inputData.CLAIM_URL;
if (!/^https:\/\/[a-z0-9]+\.supabase\.co\/functions\/v1\/owner-report-claim$/.test(approved)) {
  throw new Error("Approved claim endpoint required");
}
const response = await fetch(approved, {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({
    delivery_id: inputData.delivery_id,
    location_id: inputData.LOCATION_ID,
    workflow_binding: inputData.WORKFLOW_ID,
    recipient_binding_version: inputData.recipient_binding_version,
    mode: inputData.MODE,
    claim_credential: inputData.claim_credential
  })
});
if (!response.ok) throw new Error("Owner report claim unavailable");
const result = await response.json();
output = result.allow_send === "true" ? result : {allow_send:"false"};
