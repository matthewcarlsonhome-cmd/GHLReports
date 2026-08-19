// details.ts — the TypeScript shape of one snapshot's `details` JSON blob.
//
// The snapshots.details jsonb contract, verbatim from build spec v3 section
// 7.5. The collector caps each list at 50; names, IDs, timestamps, amounts,
// and deep links only — never phone, email, or message text.
//
// How to read this file: it is one big type declaration, no runtime code. Each
// property matches a drill-down table on the Account page (uncontacted leads,
// stale opps, past-due invoices, ...). "deep_link" fields are URLs straight
// into the matching record in GoHighLevel, so every table row is clickable.
// Timestamps are ISO strings; durations are pre-computed by the collector
// (hours_since, days_idle, ...) so the UI never re-derives them.

export type Details = {
  users: Record<string, string>; // userId -> display name
  pipelines: Record<string, { name: string; stages: Record<string, string> }>;
  leads_by_source: Record<string, number>; // 7d
  // Counts for the 5-step funnel strip; null = the source was unavailable,
  // which the UI renders as "n/a" rather than zero.
  funnel_28d: {
    form_submissions: number | null;
    leads: number;
    opps_created: number;
    appts_booked: number | null;
    won: number;
  };
  // Leads created >24h ago that nobody has reached out to yet.
  uncontacted_leads: {
    contact_id: string;
    name: string;
    source: string | null;
    created_at: string;
    hours_since: number;
    deep_link: string;
  }[];
  // Leads with no owner assigned in GHL.
  unassigned_leads: {
    contact_id: string;
    name: string;
    source: string | null;
    created_at: string;
    deep_link: string;
  }[];
  // Inbound conversations awaiting a reply for 4h or more.
  waiting_convos: {
    conversation_id: string;
    contact_id: string | null;
    contact: string;
    channel: string | null;
    hours: number;
    last_inbound_at: string;
    deep_link: string;
  }[];
  // Open deals idle 14+ days. "next_step" says whether a follow-up task or
  // calendar event exists — "none" is the actionable case.
  stale_opps: {
    opp_id: string;
    name: string;
    pipeline: string;
    stage: string;
    days_idle: number | null;
    idle_source_field: string | null;
    days_in_stage: number | null;
    value: number | null;
    owner_id: string | null;
    owner: string;
    next_step: "task" | "event" | "none" | "unknown";
    deep_link: string;
  }[];
  missing_value_opps: { opp_id: string; name: string; deep_link: string }[];
  // Invoices SSP has issued to this client that are past their due date.
  past_due_invoices: {
    invoice_id: string;
    number: string | null;
    amount_due: number | null;
    due_date: string;
    days_over: number;
    status: string;
  }[];
  appts_next_7d: {
    id: string;
    title: string;
    start: string;
    contact_id: string | null;
    status: string | null;
  }[];
  recent_publishes: { kind: "blog" | "social"; title: string | null; published_at: string }[];
  // Connected social profiles; expired === true means the OAuth link broke
  // and posts silently stop going out.
  social_accounts: {
    id: string;
    platform: string | null;
    name: string | null;
    expired: boolean | null;
  }[];
  // Review-request conversations that went quiet without a reply.
  review_asks_stale: {
    contact_id: string;
    name: string;
    days_quiet: number;
    deep_link: string;
  }[];
  // Recently-won deals whose contact never got a review ask at all.
  review_ask_gap: {
    opp_id: string;
    name: string;
    won_at: string;
    contact_id: string | null;
    deep_link: string;
  }[];
  // The client's own contact record inside SSP's CRM (relationship health).
  client: {
    contact_id: string;
    last_touch_at: string | null;
    next_appt_at: string | null;
    deep_link: string;
  } | null;
  lost_reasons_90d: Record<string, number>;
  // Per-lead response timing samples feeding the histogram and heatmap.
  // "kind" says whether the first outbound touch was a human or an automation.
  speed_to_lead: {
    contact_id: string;
    name: string;
    created_at: string;
    first_touch_minutes: number | null;
    first_human_touch_minutes: number | null;
    kind: "human" | "automation" | "unknown" | null;
  }[];
  changed: { new: string[]; resolved: string[] }; // flag codes
  // Optional ("?") because old rows in the database predate this field —
  // readers must handle undefined, not just an empty array.
  missed_calls?: {
    conversation_id: string | null;
    contact_id: string | null;
    contact: string;
    at: string | null;
    status: string;
    deep_link: string;
  }[]; // Tier 2 — absent on snapshots collected before it shipped
  // Per-form/survey health summary (silent lists feed the flags; the full
  // rows live in the form_health table). Absent on older snapshots.
  form_health?: {
    forms_total: number;
    surveys_total: number;
    forms_silent: { form_id: string; name: string; last_submission_at: string | null }[];
    surveys_silent: { form_id: string; name: string; last_submission_at: string | null }[];
    workflows: { total: number; published: number } | null;
  };
  // Chart aggregates — computed over the FULL opportunity set (never the
  // capped example lists above). All absent on older snapshots.
  pipeline_stages?: {
    pipeline: string; stage: string; count: number; stale_count: number; value: number;
    stale_value: number;                  // idle dollars in the stage (bottleneck signal)
    median_days_in_stage: number | null;  // velocity signal; null = no dated deals
    orphan: boolean;                       // stage no longer exists in the pipeline
  }[];
  deal_aging?: { bucket: string; count: number; value: number }[];
  closed_weekly?: { week_start: string; won: number; lost: number }[];
  // Structural hygiene of the pipeline setup itself.
  pipeline_setup?: { pipelines_total: number; empty_pipelines: string[]; orphan_deals: number };
  // Won/lost (90d) split per pipeline; win_rate_pct null under 5 closed deals.
  win_rate_by_pipeline?: { pipeline: string; won_90d: number; lost_90d: number; win_rate_pct: number | null }[];
  // The stage holding the most idle dollars (null when nothing is idle).
  pipeline_bottleneck?: { stage: string; stale_value: number; stale_count: number } | null;
  ghl_dashboard_url: string; // deep link to the native GHL dashboard for ads
};
