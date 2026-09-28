// Display the two core follow-up counts. These are work queues, not a new
// alert evaluator: pilot membership, thresholds and delivery stay server-side.
import type { Coverage, SnapshotRow } from "./database.types";

export interface FollowUpInput {
  gate_passed: boolean | null;
  coverage: Coverage | null;
  leads_uncontacted_24h: number | null;
  convos_waiting: number | null;
}

export const CORE_FOLLOW_UP_CODES = new Set(["SLOW_RESPONSE", "CONVOS_WAITING"]);

// A passed overall gate can still contain one failed or capped source.
// Require complete inputs before presenting a measured total or duration.
export function sourceComplete(row: Pick<FollowUpInput, "gate_passed" | "coverage"> | null, ...sources: string[]) {
  return row?.gate_passed === true && sources.every((source) => {
    const entry = row.coverage?.sources?.[source];
    return entry?.status === "complete" && !entry.error && entry.exhausted === true && !entry.skipped;
  });
}

export function reportingMetrics(row: SnapshotRow) {
  const contacts = sourceComplete(row, "contacts");
  const speed = sourceComplete(row, "contacts", "speed_to_lead");
  const replies = sourceComplete(row, "conversations");
  return {
    leads_new_7d: contacts ? row.leads_new_7d : null,
    leads_trailing_avg: contacts ? row.leads_trailing_avg : null,
    leads_delta_pct: contacts ? row.leads_delta_pct : null,
    leads_by_source_7d: contacts ? row.leads_by_source_7d : null,
    leads_unassigned_7d: contacts ? row.leads_unassigned_7d : null,
    leads_uncontacted_24h: speed ? row.leads_uncontacted_24h : null,
    speed_to_lead_median_min: speed ? row.speed_to_lead_median_min : null,
    speed_to_lead_p90_min: speed ? row.speed_to_lead_p90_min : null,
    convos_waiting: replies ? row.convos_waiting : null,
    convos_waiting_max_hours: replies ? row.convos_waiting_max_hours : null,
  };
}

export function followUpSummary(row: FollowUpInput | null) {
  const leads = row && sourceComplete(row, "contacts", "speed_to_lead") ? row.leads_uncontacted_24h : null;
  const replies = row && sourceComplete(row, "conversations") ? row.convos_waiting : null;
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
