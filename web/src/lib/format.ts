import type { Coverage, PortfolioRow } from "./database.types";

export const UNKNOWN = "Unknown";

export function fmtNum(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  return value.toLocaleString("en-US");
}

export function fmtMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

export function fmtPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(0)}%`;
}

export function fmtHours(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  if (value >= 48) return `${Math.round(value / 24)}d`;
  return `${Math.round(value)}h`;
}

export function fmtMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  if (value < 60) return `${Math.round(value)}m`;
  if (value < 60 * 24) return `${(value / 60).toFixed(1)}h`;
  return `${(value / (60 * 24)).toFixed(1)}d`;
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return UNKNOWN;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return UNKNOWN;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

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

export type DataQuality = "complete" | "partial" | "held" | "no data";

export function dataQuality(row: Pick<PortfolioRow, "gate_passed" | "coverage" | "snapshot_date">): DataQuality {
  if (!row.snapshot_date) return "no data";
  if (row.gate_passed === false) return "held";
  return coverageQuality(row.coverage);
}

export function coverageQuality(coverage: Coverage | null | undefined): DataQuality {
  if (!coverage?.summary) return "no data";
  if (coverage.summary.unavailable > 0 || coverage.summary.partial > 0) return "partial";
  return "complete";
}
