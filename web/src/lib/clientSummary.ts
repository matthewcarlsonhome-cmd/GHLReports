// clientSummary.ts — builds the "Weekly client summary (copy-ready)" text block
// shown (and copied to the clipboard) on the Account page.
//
// Copy-ready weekly client summary (spec Tier 2): pure template fill from the
// snapshot — no LLM, client-facing tone, unknown data omitted rather than
// zero-guessed, and no internal flag/risk language.
//
// The guiding rule in every branch below: a null metric means "we don't know",
// so the whole line is skipped. Writing "0 appointments" when the data was
// simply unavailable would be a lie to the client.

import type { SnapshotRow, SubaccountRow } from "./database.types";

// Local formatting twins of lib/format.ts, but spelled out in words
// ("45 minutes", not "45m") because this text is pasted into client emails.
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

// Assembles the summary as an array of lines, then joins with newlines.
// Pure function: same subaccount + snapshot in, same string out.
export function buildClientSummary(sub: SubaccountRow, snapshot: SnapshotRow): string {
  const lines: string[] = [];
  const week = snapshot.snapshot_date
    ? new Date(snapshot.snapshot_date).toLocaleDateString("en-US", {
        month: "long", day: "numeric", year: "numeric",
      })
    : "";
  lines.push(`${sub.name} — weekly update${week ? ` (${week})` : ""}`);
  lines.push("");

  // Lead volume, with the trailing average as friendly context, plus the top
  // three sources sorted by count descending.
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

  // Appointments: this-week bookings, and the 4-week show/no-show tally only
  // when there was at least one appointment to talk about.
  if (snapshot.appts_booked_7d !== null && snapshot.appts_booked_7d > 0) {
    lines.push(`Appointments booked this week: ${snapshot.appts_booked_7d}`);
  }
  if ((snapshot.appts_showed_28d ?? 0) + (snapshot.appts_noshow_28d ?? 0) > 0) {
    lines.push(
      `Appointments over the last 4 weeks: ${snapshot.appts_showed_28d} showed, ${snapshot.appts_noshow_28d} missed`,
    );
  }

  // Pipeline: one composed sentence, growing clauses only for known values.
  if (snapshot.opps_open !== null) {
    let pipeline = `Open opportunities: ${snapshot.opps_open}`;
    if (snapshot.opps_open_value) pipeline += ` worth ${money(snapshot.opps_open_value)}`;
    if (snapshot.opps_won_7d) pipeline += `; ${snapshot.opps_won_7d} won this week`;
    lines.push(pipeline);
  }

  // Content delivered on the client's behalf (blogs / social), with manual
  // singular/plural handling.
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
