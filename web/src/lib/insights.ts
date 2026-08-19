// insights.ts — the "team report": one plain-English line per account,
// built from the same numbers the table shows. Pure template fill over the
// portfolio row (no model, no scoring) so every sentence is traceable to a
// metric, and two people reading the same data always see the same words.
import type { PortfolioRow } from "./database.types";
import { fmtHours, fmtMoney } from "./format";

export interface AccountInsight {
  row: PortfolioRow;
  headline: string;   // the leads-this-week sentence
  issues: string[];   // notable problems, worst first, capped
  action: string | null; // the worst unacked flag's suggested next step
  steady: boolean;    // nothing open — rolls up into one quiet line
}

const MAX_ISSUES = 4; // per account; more than this stops being a "key" insight

export function buildAccountInsight(row: PortfolioRow): AccountInsight {
  // Headline: lead volume in context of the account's own baseline.
  let headline: string;
  if (row.leads_new_7d === null) {
    headline = "lead count unknown this week";
  } else {
    headline = `${row.leads_new_7d} lead${row.leads_new_7d === 1 ? "" : "s"} this week`;
    if (row.leads_delta_pct !== null) {
      if (row.leads_delta_pct <= -15) headline += `, down ${Math.abs(row.leads_delta_pct).toFixed(0)}% vs its usual`;
      else if (row.leads_delta_pct >= 15) headline += `, up ${row.leads_delta_pct.toFixed(0)}% vs its usual`;
      else headline += ", in line with its usual";
      if (row.leads_delta_pct <= -40 && row.peer_median_delta_pct !== null && row.peer_median_delta_pct > -20) {
        headline += " while peers held steady";
      }
    } else if (row.leads_trailing_avg === null) {
      headline += ` (baseline still building, ${row.trailing_n ?? 0}/4 weeks)`;
    }
  }

  // Issues: concrete, numeric, priority-ordered. Only measured problems make
  // the list — unknown (null) never reads as fine OR as broken.
  const issues: string[] = [];
  const push = (condition: boolean, text: string) => {
    if (condition && issues.length < MAX_ISSUES) issues.push(text);
  };
  push((row.leads_uncontacted_24h ?? 0) > 0,
    `${row.leads_uncontacted_24h} lead${row.leads_uncontacted_24h === 1 ? "" : "s"} uncontacted 24h+`);
  push((row.convos_waiting ?? 0) > 0,
    `${row.convos_waiting} conversation${row.convos_waiting === 1 ? "" : "s"} waiting`
    + (row.convos_waiting_max_hours ? ` (longest ${fmtHours(row.convos_waiting_max_hours)})` : ""));
  push((row.calls_missed_7d ?? 0) > 0,
    `${row.calls_missed_7d} missed call${row.calls_missed_7d === 1 ? "" : "s"}`);
  push((row.forms_silent_ct ?? 0) > 0,
    `${row.forms_silent_ct} form${row.forms_silent_ct === 1 ? "" : "s"} went silent`);
  push(row.opps_moved_30d === 0 && (row.opps_open ?? 0) >= 10,
    `pipeline frozen — none of ${row.opps_open} open deals moved in 30 days`);
  push(row.bottleneck_stage !== null && (row.bottleneck_value_usd ?? 0) >= 10000,
    `${fmtMoney(row.bottleneck_value_usd)} idle in "${row.bottleneck_stage}"`);
  push((row.opps_stale ?? 0) > 0,
    `${row.opps_stale} stale deal${row.opps_stale === 1 ? "" : "s"}`
    + (row.opps_stale_value ? ` (${fmtMoney(row.opps_stale_value)})` : ""));
  push((row.invoices_past_due ?? 0) > 0,
    `past due ${fmtMoney(row.invoices_past_due_amount)}`);
  push((row.leads_unassigned_7d ?? 0) > 0,
    `${row.leads_unassigned_7d} unassigned lead${row.leads_unassigned_7d === 1 ? "" : "s"}`);
  push((row.social_accounts_expired ?? 0) > 0,
    `${row.social_accounts_expired} social account${row.social_accounts_expired === 1 ? "" : "s"} disconnected`);

  const steady = issues.length === 0 && row.red === 0 && row.amber === 0;
  return { row, headline, issues, action: steady ? null : row.top_action, steady };
}
