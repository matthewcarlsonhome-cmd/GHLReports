// Copy-ready weekly client summary (spec Tier 2): pure template fill from the
// snapshot — no LLM, client-facing tone, unknown data omitted rather than
// zero-guessed, and no internal flag/risk language.

import type { SnapshotRow, SubaccountRow } from "./database.types";

function money(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 0,
  });
}

function minutes(value: number): string {
  if (value < 60) return `${Math.round(value)} minutes`;
  if (value < 60 * 24) return `${(value / 60).toFixed(1)} hours`;
  return `${(value / (60 * 24)).toFixed(1)} days`;
}

export function buildClientSummary(sub: SubaccountRow, snapshot: SnapshotRow): string {
  const lines: string[] = [];
  const week = snapshot.snapshot_date
    ? new Date(snapshot.snapshot_date).toLocaleDateString("en-US", {
        month: "long", day: "numeric", year: "numeric",
      })
    : "";
  lines.push(`${sub.name} — weekly update${week ? ` (${week})` : ""}`);
  lines.push("");

  if (snapshot.leads_new_7d !== null) {
    let leads = `New inquiries this week: ${snapshot.leads_new_7d}`;
    if (snapshot.leads_trailing_avg !== null) {
      leads += ` (typical week: ${Math.round(snapshot.leads_trailing_avg)})`;
    }
    lines.push(leads);
    const sources = Object.entries(snapshot.leads_by_source_7d ?? {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3);
    if (sources.length) {
      lines.push(`Where they came from: ${sources.map(([s, n]) => `${s} (${n})`).join(", ")}`);
    }
  }

  if (snapshot.speed_to_lead_median_min !== null) {
    lines.push(`Median first response to a new inquiry: ${minutes(snapshot.speed_to_lead_median_min)}`);
  }

  if (snapshot.appts_booked_7d !== null && snapshot.appts_booked_7d > 0) {
    lines.push(`Appointments booked this week: ${snapshot.appts_booked_7d}`);
  }
  if ((snapshot.appts_showed_28d ?? 0) + (snapshot.appts_noshow_28d ?? 0) > 0) {
    lines.push(
      `Appointments over the last 4 weeks: ${snapshot.appts_showed_28d} showed, ${snapshot.appts_noshow_28d} missed`,
    );
  }

  if (snapshot.opps_open !== null) {
    let pipeline = `Open opportunities: ${snapshot.opps_open}`;
    if (snapshot.opps_open_value) pipeline += ` worth ${money(snapshot.opps_open_value)}`;
    if (snapshot.opps_won_7d) pipeline += `; ${snapshot.opps_won_7d} won this week`;
    lines.push(pipeline);
  }

  const published: string[] = [];
  if (snapshot.blogs_published_30d !== null && snapshot.blogs_published_30d > 0) {
    published.push(`${snapshot.blogs_published_30d} blog post${snapshot.blogs_published_30d === 1 ? "" : "s"} (last 30 days)`);
  }
  if (snapshot.social_published_7d !== null && snapshot.social_published_7d > 0) {
    published.push(`${snapshot.social_published_7d} social post${snapshot.social_published_7d === 1 ? "" : "s"} (this week)`);
  }
  if (published.length) lines.push(`Published for you: ${published.join(", ")}`);

  return lines.join("\n");
}
