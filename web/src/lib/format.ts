// format.ts — tiny pure formatting helpers shared by every page.
//
// One convention rules them all: a null/undefined metric renders as "Unknown",
// never as 0. The collector distinguishes "we measured zero" from "we couldn't
// measure", and the UI must preserve that honesty (spec 9.2). Each helper is a
// pure function: same input, same output, no state — safe to call in render.
import type { Coverage, PortfolioRow } from "./database.types";

export const UNKNOWN = "Unknown";

// GHL lets clients decorate pipeline/stage names with emoji ("🔥 New Lead ⚡").
// Strip pictographs (plus stray variation selectors / zero-width joiners) so
// axis labels and table cells read as plain text.
export function stripDecor(value: string | null | undefined): string {
  if (!value) return "";
  return value
    .replace(/[\p{Extended_Pictographic}\u{FE0F}\u{200D}]/gu, "")
    .replace(/\s+/g, " ")
    .trim();
}

// Plain integer with thousands separators ("1,234").
export function fmtNum(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  return value.toLocaleString("en-US");
}

// Whole-dollar currency ("$12,500") — cents are noise at dashboard scale.
export function fmtMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

// Signed percentage ("+12%", "-40%") — the explicit "+" makes deltas readable.
export function fmtPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(0)}%`;
}

// Duration given in hours: "36h" up to two days, then whole days ("3d").
export function fmtHours(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  if (value >= 48) return `${Math.round(value / 24)}d`;
  return `${Math.round(value)}h`;
}

// Duration given in minutes, escalating units: "45m" → "2.5h" → "1.2d".
export function fmtMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  if (value < 60) return `${Math.round(value)}m`;
  if (value < 60 * 24) return `${(value / 60).toFixed(1)}h`;
  return `${(value / (60 * 24)).toFixed(1)}d`;
}

// Short date ("Aug 18"). An unparseable string also collapses to Unknown.
export function fmtDate(value: string | null | undefined): string {
  if (!value) return UNKNOWN;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return UNKNOWN;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// Short date + time ("Aug 18, 5:31 AM"), in the viewer's local timezone.
export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return UNKNOWN;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return UNKNOWN;
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

// How much to trust a snapshot's numbers, worst first:
// "no data"  — no snapshot at all
// "held"     — the data-quality gate rejected the snapshot (don't trust it)
// "partial"  — some API sources failed or were truncated
// "complete" — every source came back clean
export type DataQuality = "complete" | "partial" | "held" | "no data";

// Quality for a portfolio row (which may not have a snapshot yet).
export function dataQuality(row: Pick<PortfolioRow, "gate_passed" | "coverage" | "snapshot_date">): DataQuality {
  if (!row.snapshot_date) return "no data";
  if (row.gate_passed === false) return "held";
  return coverageQuality(row.coverage);
}

// Quality from a coverage object alone (used when gate status is known-good).
export function coverageQuality(coverage: Coverage | null | undefined): DataQuality {
  if (!coverage?.summary) return "no data";
  if (coverage.summary.unavailable > 0 || coverage.summary.partial > 0) return "partial";
  return "complete";
}
