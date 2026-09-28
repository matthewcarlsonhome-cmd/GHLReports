// Display the two core follow-up counts. These are work queues, not a new
// alert evaluator: pilot membership, thresholds and delivery stay server-side.
export interface FollowUpInput {
  gate_passed: boolean | null;
  leads_uncontacted_24h: number | null;
  convos_waiting: number | null;
}

export const CORE_FOLLOW_UP_CODES = new Set(["SLOW_RESPONSE", "CONVOS_WAITING"]);

export function followUpSummary(row: FollowUpInput | null) {
  const trusted = row?.gate_passed === true;
  const leads = trusted ? row.leads_uncontacted_24h : null;
  const replies = trusted ? row.convos_waiting : null;
  const incomplete = leads === null || replies === null;
  const needsFollowUp = (leads ?? 0) > 0 || (replies ?? 0) > 0;
  const status: "follow-up" | "check-data" | "clear" = needsFollowUp ? "follow-up" : incomplete ? "check-data" : "clear";
  const label = needsFollowUp ? "Follow-up needed" : incomplete ? "Check data" : "No follow-up outstanding";
  const action = (leads ?? 0) > 0 && (replies ?? 0) > 0
    ? "Ask the client to contact missed leads and reply to waiting customers."
    : (leads ?? 0) > 0 ? "Ask the client to contact leads still waiting for a first response."
    : (replies ?? 0) > 0 ? "Ask the client to reply to waiting customers."
    : incomplete ? "Review data coverage before drawing a conclusion."
    : "No overdue leads or waiting replies in this snapshot.";
  return { leads, replies, incomplete, status, label, action, rank: needsFollowUp ? 0 : incomplete ? 1 : 2 };
}
