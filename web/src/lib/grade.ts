// grade.ts — triage banding + small text helpers for the portfolio.
// (An A-F letter grade lived here briefly; it was removed by request —
// young accounts carry many un-acknowledged flags, so the scale read as
// judgment rather than information. The triage bands below keep the useful
// part: a four-way split the header chips can count and filter by.)
import type { PortfolioRow } from "./database.types";

// Triage bands for the header chips. Distinct from `state` (which the
// database computes) only in splitting "steady" into healthy vs watch.
export type Band = "attention" | "watch" | "healthy" | "no_data";

export function bandOf(row: PortfolioRow): Band {
  if (row.state === "no_data") return "no_data";
  if (row.state === "attention") return "attention";
  return row.amber > 0 || (row.forms_silent_ct ?? 0) > 0 ? "watch" : "healthy";
}

// "MISSED_CALLS" -> "missed calls" — flag codes in plain words for the
// new-this-week strip and tile badges.
export function humanizeCode(code: string): string {
  return code.toLowerCase().replace(/_/g, " ");
}
