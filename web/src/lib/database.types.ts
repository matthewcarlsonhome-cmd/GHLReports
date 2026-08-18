// Hand-written to match supabase/migrations/0001_init.sql (spec v3 section 6).
// Regenerate with `supabase gen types typescript` once the project exists, or
// keep this in sync by hand — the SQL migration is the source of truth.

import type { Details } from "./details";

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

export interface FlagAckRow {
  id: number;
  location_id: string;
  code: string;
  acked_by: string;
  note: string | null;
  acked_at: string;
  snooze_until: string;
}

export interface AccountNoteRow {
  id: number;
  location_id: string;
  author: string;
  body: string;
  created_at: string;
}

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

export interface LeadHistoryRow {
  location_id: string;
  week_start: string;
  leads: number;
  leads_by_source: Record<string, number>;
  form_submissions: number | null;
}

export type RunLocationDetail = {
  status?: string;
  gate?: string[];
  requests?: number;
  rate_limited?: number;
  seconds?: number;
  error?: string | null;
};

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

export type PortfolioState = "no_data" | "attention" | "steady";

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
}

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
