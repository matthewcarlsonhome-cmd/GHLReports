// grade.ts — one health grade (A-F) per account, computed from plain rules.
//
// The problem this solves: nobody can weigh twelve metric columns per row at
// 8am. The grade collapses an account's state into a single sortable letter,
// and stays a GLASS box: gradeAccount returns the exact list of deductions
// that produced the number, which the UI shows on hover. No model, no
// weights anyone can't recite from this file.
//
// Scoring: start at 100, subtract per problem, clamp at 0.
//   unacknowledged red flag ....... -25 each  (act today)
//   unacknowledged amber flag ..... -10 each  (worth attention)
//   leads down 40%+ vs baseline ...  -10      (trend, beyond any flag)
//   one or more silent forms ......  -5       (quiet leak)
// Letters: A >= 90, B >= 75, C >= 60, D >= 40, F below. No data = "–".
import type { PortfolioRow } from "./database.types";

export interface Grade {
  letter: "A" | "B" | "C" | "D" | "F" | "–";
  score: number; // 0-100; no-data rows get -1 so they sort after F
  reasons: string[]; // human-readable deductions for the tooltip
}

export function gradeAccount(row: PortfolioRow): Grade {
  if (row.state === "no_data") {
    return { letter: "–", score: -1, reasons: ["No trustworthy data yet"] };
  }
  let score = 100;
  const reasons: string[] = [];
  if (row.red > 0) {
    score -= 25 * row.red;
    reasons.push(`${row.red} red flag${row.red > 1 ? "s" : ""} (-${25 * row.red})`);
  }
  if (row.amber > 0) {
    score -= 10 * row.amber;
    reasons.push(`${row.amber} amber flag${row.amber > 1 ? "s" : ""} (-${10 * row.amber})`);
  }
  if (row.leads_delta_pct !== null && row.leads_delta_pct <= -40) {
    score -= 10;
    reasons.push(`leads down ${Math.abs(row.leads_delta_pct).toFixed(0)}% vs baseline (-10)`);
  }
  if ((row.forms_silent_ct ?? 0) > 0) {
    score -= 5;
    reasons.push(`${row.forms_silent_ct} silent form${row.forms_silent_ct! > 1 ? "s" : ""} (-5)`);
  }
  score = Math.max(0, score);
  const letter = score >= 90 ? "A" : score >= 75 ? "B" : score >= 60 ? "C" : score >= 40 ? "D" : "F";
  if (reasons.length === 0) reasons.push("No open issues");
  return { letter, score, reasons };
}

// Triage bands for the header chips. Distinct from `state` (which the
// database computes) only in splitting "steady" into healthy vs watch.
export type Band = "attention" | "watch" | "healthy" | "no_data";

export function bandOf(row: PortfolioRow): Band {
  if (row.state === "no_data") return "no_data";
  if (row.state === "attention") return "attention";
  return row.amber > 0 || (row.forms_silent_ct ?? 0) > 0 ? "watch" : "healthy";
}

// "MISSED_CALLS" -> "missed calls" — flag codes in plain words for the
// since-yesterday strip and tile badges.
export function humanizeCode(code: string): string {
  return code.toLowerCase().replace(/_/g, " ");
}
