// mlh.ts: which accounts count as "using MyLeadHub (GHL)" for the default
// views. An account is left out when the team marked it non-MLH
// (subaccounts.mlh_status: ads only, never onboarded, canceled) or when it
// has no client staff users at all (nobody at the client can log in). The
// SSP parent always counts. See docs/ACCOUNT-USAGE.md.
import type { PortfolioRow } from "./database.types";

export function usesMlh(row: Pick<PortfolioRow, "is_parent" | "mlh_status" | "client_users">): boolean {
  if (row.is_parent) return true;
  if (row.mlh_status && row.mlh_status !== "active") return false;
  return row.client_users !== 0;
}

// Plain-words reason an account is filtered out, for tooltips and banners.
export function nonMlhReason(row: Pick<PortfolioRow, "mlh_status" | "mlh_note" | "client_users">): string {
  const labels: Record<string, string> = {
    ads_only: "ads only",
    not_in_mlh: "not in MLH",
    canceled: "canceled",
  };
  if (row.mlh_status && row.mlh_status !== "active") {
    const label = labels[row.mlh_status] ?? row.mlh_status;
    return row.mlh_note && row.mlh_note !== label ? `${label} (${row.mlh_note})` : label;
  }
  if (row.client_users === 0) return "no client users";
  return "";
}
