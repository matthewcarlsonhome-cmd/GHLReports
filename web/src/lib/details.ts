// The snapshots.details jsonb contract, verbatim from build spec v3 section
// 7.5. The collector caps each list at 50; names, IDs, timestamps, amounts,
// and deep links only — never phone, email, or message text.

export type Details = {
  users: Record<string, string>; // userId -> display name
  pipelines: Record<string, { name: string; stages: Record<string, string> }>;
  leads_by_source: Record<string, number>; // 7d
  funnel_28d: {
    form_submissions: number | null;
    leads: number;
    opps_created: number;
    appts_booked: number | null;
    won: number;
  };
  uncontacted_leads: {
    contact_id: string;
    name: string;
    source: string | null;
    created_at: string;
    hours_since: number;
    deep_link: string;
  }[];
  unassigned_leads: {
    contact_id: string;
    name: string;
    source: string | null;
    created_at: string;
    deep_link: string;
  }[];
  waiting_convos: {
    conversation_id: string;
    contact_id: string | null;
    contact: string;
    channel: string | null;
    hours: number;
    last_inbound_at: string;
    deep_link: string;
  }[];
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
  social_accounts: {
    id: string;
    platform: string | null;
    name: string | null;
    expired: boolean | null;
  }[];
  review_asks_stale: {
    contact_id: string;
    name: string;
    days_quiet: number;
    deep_link: string;
  }[];
  review_ask_gap: {
    opp_id: string;
    name: string;
    won_at: string;
    contact_id: string | null;
    deep_link: string;
  }[];
  client: {
    contact_id: string;
    last_touch_at: string | null;
    next_appt_at: string | null;
    deep_link: string;
  } | null;
  lost_reasons_90d: Record<string, number>;
  speed_to_lead: {
    contact_id: string;
    name: string;
    created_at: string;
    first_touch_minutes: number | null;
    first_human_touch_minutes: number | null;
    kind: "human" | "automation" | "unknown" | null;
  }[];
  changed: { new: string[]; resolved: string[] }; // flag codes
  missed_calls?: {
    conversation_id: string | null;
    contact_id: string | null;
    contact: string;
    at: string | null;
    status: string;
    deep_link: string;
  }[]; // Tier 2 — absent on snapshots collected before it shipped
  ghl_dashboard_url: string; // deep link to the native GHL dashboard for ads
};
