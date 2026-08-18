// database.types.ts — TypeScript mirrors of every table and view this app reads.
//
// Hand-written to match supabase/migrations/0001_init.sql (spec v3 section 6).
// Regenerate with `supabase gen types typescript` once the project exists, or
// keep this in sync by hand — the SQL migration is the source of truth.
//
// Reading tips for newcomers:
// - These are compile-time-only declarations; nothing here runs in the browser.
//   Query results come back as untyped JSON and get cast to these interfaces.
// - "X | null" mirrors a NULLable SQL column. Null consistently means "the
//   collector could not measure this" — distinct from 0 (see lib/format.ts).
// - Suffixes encode the measurement window: _7d = last 7 days, _28d = 28 days,
//   _90d = 90 days, _30d = 30 days.

import type { Details } from "./details";

// Per-snapshot record of how completely each GHL API source was fetched.
// The UI turns this into the complete/partial quality badge and the
// "Coverage and caveats" table on the Account page.
export type Coverage = {
  sources: Record<
    string,
    {
      retrieved: number;
      exhausted: boolean;
      error: string | null;
      note: string | null;
      skipped: boolean;
      status: "complete" | "partial" | "unavailable" | "skipped" | "missing";
    }
  >;
  summary: { complete: number; partial: number; unavailable: number; skipped: number };
};

// One row per GHL sub-account (client) we track: identity, ownership (AM =
// account manager), commercial data (MRR, contract end), and API-token health.
// `is_parent` marks SSP's own agency account, filtered out of most views.
export interface SubaccountRow {
  location_id: string;
  name: string;
  slug: string;
  vertical: string | null;
  services: string[];
  am_email: string | null;
  timezone: string;
  ssp_client_contact_id: string | null;
  is_parent: boolean;
  active: boolean;
  thresholds: Record<string, number>;
  mrr: number | null;
  contract_end: string | null;
  token_status: "none" | "ok" | "invalid";
  token_rotated_at: string | null;
  last_token_error: string | null;
  created_at: string;
}

// One daily collector capture for one account: every metric the dashboard
// shows, frozen at capture time. `gate_passed` = false means the data-quality
// gate rejected the numbers; `details` is the big drill-down blob (details.ts).
export interface SnapshotRow {
  id: number;
  location_id: string;
  snapshot_date: string;
  captured_at: string;
  gate_passed: boolean;
  coverage: Coverage;
  leads_new_7d: number | null;
  leads_trailing_avg: number | null;
  trailing_n: number | null;
  leads_delta_pct: number | null;
  peer_median_delta_pct: number | null;
  peer_n: number | null;
  leads_by_source_7d: Record<string, number> | null;
  leads_by_source_trailing: Record<string, number> | null;
  leads_unassigned_7d: number | null;
  leads_missing_phone_pct_7d: number | null;
  form_submissions_7d: number | null;
  form_submissions_trailing_avg: number | null;
  convos_active_7d: number | null;
  opps_created_7d: number | null;
  leads_uncontacted_24h: number | null;
  leads_no_human_touch_7d: number | null;
  speed_to_lead_median_min: number | null;
  speed_to_lead_p90_min: number | null;
  speed_kind_known: boolean | null;
  excluded_count: number | null;
  convos_waiting: number | null;
  convos_waiting_max_hours: number | null;
  calls_missed_7d: number | null;
  opps_open: number | null;
  opps_open_value: number | null;
  opps_stale: number | null;
  opps_stale_value: number | null;
  opps_stuck: number | null;
  opps_missing_value: number | null;
  opps_no_next_step: number | null;
  opps_won_7d: number | null;
  opps_lost_7d: number | null;
  lead_to_opp_28d_pct: number | null;
  win_rate_90d: number | null;
  median_days_to_close_90d: number | null;
  appts_booked_7d: number | null;
  appts_showed_28d: number | null;
  appts_noshow_28d: number | null;
  noshow_rate_28d: number | null;
  blogs_published_30d: number | null;
  social_published_7d: number | null;
  days_since_last_publish: number | null;
  social_accounts_total: number | null;
  social_accounts_expired: number | null;
  invoices_past_due: number | null;
  invoices_past_due_amount: number | null;
  client_last_touch_days: number | null;
  client_next_appt_at: string | null;
  review_asks_stale: number | null;
  review_ask_gap: number | null;
  flags_new: string[];
  flags_resolved: string[];
  details: Details;
}

// One raised issue on one snapshot: a stable machine `code` (e.g.
// SLOW_RESPONSE), a severity, human-readable text, and an optional deep link
// to the offending record in GHL.
export interface FlagRow {
  id: number;
  location_id: string;
  snapshot_date: string;
  code: string;
  severity: "red" | "amber" | "info";
  title: string;
  detail: string | null;
  action: string | null;
  entity_type: string | null;
  entity_id: string | null;
  entity_name: string | null;
  deep_link: string | null;
  created_at: string;
}

// An acknowledgement: "person X saw flag `code` on this account and snoozed it
// until snooze_until". One of the two insert-only write paths (lib/writes.ts).
export interface FlagAckRow {
  id: number;
  location_id: string;
  code: string;
  acked_by: string;
  note: string | null;
  acked_at: string;
  snooze_until: string;
}

// A free-text note on an account — the other insert-only write path.
export interface AccountNoteRow {
  id: number;
  location_id: string;
  author: string;
  body: string;
  created_at: string;
}

// One row per new lead, tracking response timing: when it arrived, when the
// first outbound touch happened, and whether that touch was a human or an
// automation. Feeds the speed-to-lead histogram and the after-hours heatmap.
export interface LeadEventRow {
  location_id: string;
  contact_id: string;
  contact_name: string | null;
  source: string | null;
  created_at: string;
  first_outbound_at: string | null;
  first_outbound_kind: "human" | "automation" | "unknown" | null;
  first_human_touch_at: string | null;
  first_touch_minutes: number | null;
  first_human_touch_minutes: number | null;
  updated_at: string;
}

// Weekly lead totals per account — the series behind the sparklines and the
// stacked leads-by-source chart.
export interface LeadHistoryRow {
  location_id: string;
  week_start: string;
  leads: number;
  leads_by_source: Record<string, number>;
  form_submissions: number | null;
}

// Per-location stats nested inside a collector run's `details` JSON.
export type RunLocationDetail = {
  status?: string;
  gate?: string[];
  requests?: number;
  rate_limited?: number;
  seconds?: number;
  error?: string | null;
};

// One nightly collector execution: counts of accounts that succeeded / were
// gate-held / failed, API request totals, and per-location detail (Runs page).
export interface CollectorRunRow {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: string | null;
  locations_ok: number;
  locations_held: number;
  locations_failed: number;
  requests_made: number | null;
  rate_limited: number | null;
  details: Record<string, RunLocationDetail>;
  error: string | null;
}

// The three portfolio buckets an account can land in.
export type PortfolioState = "no_data" | "attention" | "steady";

// One row of the v_portfolio database VIEW — subaccount + latest snapshot +
// flag counts pre-joined server-side, so the Portfolio page needs one query.
// `attention_score` ranks accounts; `state` buckets them into page sections.
export interface PortfolioRow {
  location_id: string;
  name: string;
  slug: string;
  vertical: string | null;
  services: string[];
  am_email: string | null;
  timezone: string;
  is_parent: boolean;
  mrr: number | null;
  contract_end: string | null;
  token_status: "none" | "ok" | "invalid";
  token_rotated_at: string | null;
  snapshot_date: string | null;
  captured_at: string | null;
  gate_passed: boolean | null;
  coverage: Coverage | null;
  leads_new_7d: number | null;
  leads_trailing_avg: number | null;
  trailing_n: number | null;
  leads_delta_pct: number | null;
  peer_median_delta_pct: number | null;
  peer_n: number | null;
  leads_unassigned_7d: number | null;
  leads_missing_phone_pct_7d: number | null;
  form_submissions_7d: number | null;
  leads_uncontacted_24h: number | null;
  leads_no_human_touch_7d: number | null;
  speed_to_lead_median_min: number | null;
  speed_kind_known: boolean | null;
  convos_waiting: number | null;
  convos_waiting_max_hours: number | null;
  opps_open: number | null;
  opps_open_value: number | null;
  opps_stale: number | null;
  opps_stale_value: number | null;
  opps_missing_value: number | null;
  lead_to_opp_28d_pct: number | null;
  win_rate_90d: number | null;
  noshow_rate_28d: number | null;
  days_since_last_publish: number | null;
  social_accounts_expired: number | null;
  invoices_past_due: number | null;
  invoices_past_due_amount: number | null;
  client_last_touch_days: number | null;
  client_next_appt_at: string | null;
  flags_new: string[] | null;
  flags_resolved: string[] | null;
  red: number;
  amber: number;
  info: number;
  acked: number;
  attention_score: number;
  top_action: string | null;
  state: PortfolioState;
  calls_missed_7d: number | null;
}

// One row of the v_history VIEW: a slim per-day slice of past snapshots used
// for the Account page's trend charts (delta vs baseline, etc.).
export interface HistoryRow {
  location_id: string;
  snapshot_date: string;
  gate_passed: boolean;
  leads_new_7d: number | null;
  leads_trailing_avg: number | null;
  leads_delta_pct: number | null;
  peer_median_delta_pct: number | null;
  convos_waiting: number | null;
  opps_stale: number | null;
  opps_open_value: number | null;
  days_since_last_publish: number | null;
  leads_uncontacted_24h: number | null;
  speed_to_lead_median_min: number | null;
}
