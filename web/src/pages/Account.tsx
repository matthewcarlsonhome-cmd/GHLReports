// Account.tsx — the single-account drilldown page, and the largest file in the
// app. Reading order mirrors the numbered layout comments below: header,
// "Do next" (flags + acknowledgements), flag changes, KPI tiles, funnel,
// charts (stacked sources, delta-vs-baseline, heatmap, histogram), the
// drill-down detail tables, coverage, the copy-ready client summary, notes.
//
// Key ideas needed to read this file:
// - Data loading: one useCallback'd `load()` grabs the subaccount, then its
//   latest snapshot, then runs SIX further queries concurrently via
//   Promise.all (flags, acks, notes, weekly history, snapshot history, lead
//   events) and stores everything in a single `Loaded` state object. After a
//   write (ack or note) we simply call load() again to refresh.
// - Acknowledgement flow: clicking "Acknowledge" on a flag opens an inline
//   note + snooze-length picker; submitAck inserts a row via lib/writes.ts
//   and reloads. Acked flags drop out of "Do next" into a quiet gray list.
// - Chart data is computed with useMemo (recompute only when `data` changes)
//   and rendered with Recharts, a React charting library whose components
//   (<ComposedChart>, <Bar>, <Line>...) are declared as JSX.
import { ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { AfterHoursHeatmap } from "../components/Heatmap";
import {
  DetailTable,
  EmptyState,
  ExternalLink,
  QualityBadge,
  Section,
  SeverityChip,
  Skeleton,
  StatTile,
} from "../components/ui";
import { buildClientSummary } from "../lib/clientSummary";
import type {
  AccountNoteRow,
  FlagAckRow,
  FlagRow,
  FormHealthRow,
  HistoryRow,
  LeadEventRow,
  LeadHistoryRow,
  SnapshotRow,
  SubaccountRow,
  TagCheckRow,
} from "../lib/database.types";
import {
  coverageQuality,
  fmtDate,
  fmtDateTime,
  fmtHours,
  fmtMinutes,
  fmtMoney,
  fmtNum,
  fmtPct,
  UNKNOWN,
} from "../lib/format";
import { supabase } from "../lib/supabase";
import { useSession } from "../lib/useSession";
import { acknowledgeFlag, addNote } from "../lib/writes";

// Shared chart colors, matching the Tailwind palette in tailwind.config.js.
const SERIES = "#2a78d6";
const BASELINE = "#898781";
const GRID = "#e1e0d9";
const INK2 = "#52514e";
// Categorical slots for the stacked source chart (validated palette order)
const SOURCE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#c3c2b7"];

// Response-time buckets for the speed-to-lead histogram. Buckets widen as
// times grow (log-ish scale) because the difference between 5m and 15m
// matters far more than between 25h and 30h.
const HISTOGRAM_BINS = [
  { label: "≤5m", max: 5 },
  { label: "5–15m", max: 15 },
  { label: "15–30m", max: 30 },
  { label: "30–60m", max: 60 },
  { label: "1–4h", max: 240 },
  { label: "4–24h", max: 1440 },
  { label: ">24h", max: Number.POSITIVE_INFINITY },
];

// Which detail table each flag code justifies opening by default (spec 9.3.7)
const FLAG_TABLE: Record<string, string> = {
  SLOW_RESPONSE: "uncontacted",
  UNASSIGNED_LEADS: "unassigned",
  CONVOS_WAITING: "waiting",
  STALE_PIPELINE: "stale",
  PAST_DUE: "past_due",
  SOCIAL_DISCONNECTED: "social",
  REVIEW_ASK_GAP: "reviews",
  HIGH_NOSHOW: "appts",
  NO_DELIVERY: "publishes",
};

// Everything the page loads, bundled so a single setData() swap keeps all
// pieces consistent with each other (no half-updated renders).
type Loaded = {
  sub: SubaccountRow;
  snapshot: SnapshotRow | null;
  flags: FlagRow[];
  acks: FlagAckRow[];
  notes: AccountNoteRow[];
  weekly: LeadHistoryRow[];
  history: HistoryRow[];
  leadEvents: LeadEventRow[];
  formHealth: FormHealthRow[];
  tagCheck: TagCheckRow | null;
};

// Expand/collapse wrapper for the detail tables. `defaultOpen` is only the
// *initial* value (used to auto-open tables tied to firing flags); after
// mount the user's clicks own the state.
// Drill-down section titles show the TRUE metric count. The row list under
// them is capped at 50 for row-size reasons, so an account with 300 stale
// deals must read "(300 — top 50 shown)", never a misleading "(50)".
function cappedTitle(label: string, total: number | null | undefined, shown: number): string {
  if (total === null || total === undefined || total <= shown) return `${label} (${shown})`;
  return `${label} (${total} — top ${shown} shown)`;
}

function Collapsible({ title, defaultOpen, children }: {
  title: string; defaultOpen: boolean; children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="mb-4">
      <button onClick={() => setOpen((v) => !v)}
              className="mb-2 flex w-full items-baseline justify-between text-left">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        <span className="text-xxs text-ink-2 underline underline-offset-2">{open ? "collapse" : "expand"}</span>
      </button>
      {open ? children : null}
    </section>
  );
}

export default function Account() {
  // :locationId from the /account/:locationId route (see App.tsx).
  const { locationId } = useParams<{ locationId: string }>();
  const { session } = useSession();
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Ack-form state: which flag's inline form is open, its note text, errors.
  const [ackFor, setAckFor] = useState<string | null>(null);
  const [ackNote, setAckNote] = useState("");
  const [ackError, setAckError] = useState<string | null>(null);
  // Note-form state, plus the transient "Copied ✓" indicator.
  const [noteBody, setNoteBody] = useState("");
  const [noteError, setNoteError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Load everything. First two queries are sequential (the flags query needs
  // the snapshot's date); the remaining six run concurrently via Promise.all.
  const load = useCallback(async () => {
    if (!locationId) return;
    const { data: subRows, error: subErr } = await supabase
      .from("subaccounts").select("*").eq("location_id", locationId).limit(1);
    if (subErr || !subRows?.length) {
      // RLS quirk: a row you can't see and a row that doesn't exist both come
      // back empty — hence the "or you don't have access" wording.
      setError(subErr?.message ?? "Account not found (or you don't have access).");
      return;
    }
    // Latest snapshot: newest date, and newest capture within that date.
    const { data: snapRows } = await supabase
      .from("snapshots").select("*").eq("location_id", locationId)
      .order("snapshot_date", { ascending: false })
      .order("captured_at", { ascending: false }).limit(1);
    const snapshot = (snapRows?.[0] ?? null) as SnapshotRow | null;

    const twelveWeeksAgo = new Date(Date.now() - 12 * 7 * 86400 * 1000).toISOString().slice(0, 10);
    const [flagsRes, acksRes, notesRes, weeklyRes, historyRes, eventsRes, formHealthRes, tagRes] = await Promise.all([
      // flags for the current snapshot only (no snapshot -> empty stub kept
      // Promise-shaped so destructuring stays uniform)
      snapshot
        ? supabase.from("flags").select("*").eq("location_id", locationId)
            .eq("snapshot_date", snapshot.snapshot_date)
        : Promise.resolve({ data: [] as FlagRow[] }),
      // only acks whose snooze window is still active (snooze_until >= today)
      supabase.from("flag_acks").select("*").eq("location_id", locationId)
        .gte("snooze_until", new Date().toISOString().slice(0, 10))
        .order("acked_at", { ascending: false }),
      supabase.from("account_notes").select("*").eq("location_id", locationId)
        .order("created_at", { ascending: false }).limit(100),
      supabase.from("lead_history").select("*").eq("location_id", locationId)
        .gte("week_start", twelveWeeksAgo).order("week_start", { ascending: true }),
      supabase.from("v_history").select("*").eq("location_id", locationId)
        .order("snapshot_date", { ascending: true }),
      // last 30 days of lead events for the heatmap + histogram, capped at 500
      supabase.from("lead_events").select("*").eq("location_id", locationId)
        .gte("created_at", new Date(Date.now() - 30 * 86400 * 1000).toISOString())
        .order("created_at", { ascending: false }).limit(500),
      // per-form / per-survey health for the latest snapshot date
      snapshot
        ? supabase.from("form_health").select("*").eq("location_id", locationId)
            .eq("snapshot_date", snapshot.snapshot_date)
        : Promise.resolve({ data: [] as FormHealthRow[] }),
      // newest tag/pixel check (empty until the tag checker has run for
      // this account — the card hides itself in that case)
      supabase.from("tag_checks").select("*").eq("location_id", locationId)
        .order("checked_at", { ascending: false }).limit(1),
    ]);

    setData({
      sub: subRows[0] as SubaccountRow,
      snapshot,
      flags: (flagsRes.data ?? []) as FlagRow[],
      acks: (acksRes.data ?? []) as FlagAckRow[],
      notes: (notesRes.data ?? []) as AccountNoteRow[],
      weekly: (weeklyRes.data ?? []) as LeadHistoryRow[],
      history: (historyRes.data ?? []) as HistoryRow[],
      leadEvents: (eventsRes.data ?? []) as LeadEventRow[],
      formHealth: (formHealthRes.data ?? []) as FormHealthRow[],
      tagCheck: ((tagRes.data ?? []) as TagCheckRow[])[0] ?? null,
    });
  }, [locationId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Flag codes with a live (unexpired) acknowledgement — a Set for O(1) lookup.
  const ackedCodes = useMemo(() => new Set((data?.acks ?? []).map((a) => a.code)), [data]);

  // Data for the stacked leads-by-source chart. Three steps:
  // 1) Sum every source across all 12 weeks and keep the top 5; everything
  //    else collapses into an "other" bar so the legend stays readable.
  // 2) Index each gate-passed snapshot's trailing average by its date.
  // 3) For each week, build one Recharts row: {week, source1: n, ..., other,
  //    baseline} — the baseline is the first snapshot value found within that
  //    week's 7 days (snapshots are daily, weeks are Mondays, so we probe
  //    day by day for the nearest match).
  const stackedChart = useMemo(() => {
    if (!data) return { rows: [] as Record<string, unknown>[], sources: [] as string[] };
    const totals: Record<string, number> = {};
    for (const week of data.weekly) {
      for (const [source, count] of Object.entries(week.leads_by_source ?? {})) {
        totals[source] = (totals[source] ?? 0) + count;
      }
    }
    const top = Object.entries(totals).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([s]) => s);
    const baselineByWeek = new Map<string, number | null>();
    for (const row of data.history) {
      if (row.gate_passed && row.leads_trailing_avg !== null) {
        baselineByWeek.set(row.snapshot_date.slice(0, 10), row.leads_trailing_avg);
      }
    }
    const rows = data.weekly.map((week) => {
      const entry: Record<string, unknown> = { week: week.week_start };
      let other = 0;
      for (const [source, count] of Object.entries(week.leads_by_source ?? {})) {
        if (top.includes(source)) entry[source] = count;
        else other += count;
      }
      if (other > 0) entry.other = other;
      // nearest snapshot baseline within the week
      for (let offset = 0; offset < 7; offset += 1) {
        const day = new Date(week.week_start);
        day.setDate(day.getDate() + offset);
        const value = baselineByWeek.get(day.toISOString().slice(0, 10));
        if (value !== undefined) {
          entry.baseline = value;
          break;
        }
      }
      return entry;
    });
    return { rows, sources: [...top, "other"] };
  }, [data]);

  // Delta-vs-peers line chart: gate-passed snapshots only, last 84 (~12wks).
  const deltaChart = useMemo(() => {
    if (!data) return [];
    return data.history.filter((row) => row.gate_passed).slice(-84).map((row) => ({
      date: row.snapshot_date,
      account: row.leads_delta_pct,
      peers: row.peer_median_delta_pct,
    }));
  }, [data]);

  // Histogram bucketing: pick each lead's response time (human-only when the
  // collector could tell humans from automations, otherwise any first touch),
  // drop the nulls, then count how many fall in each bin. The bin test is
  // "at most this bin's max AND above the previous bin's max" — half-open
  // ranges, so every sample lands in exactly one bar.
  const histogram = useMemo(() => {
    if (!data) return [];
    const kindsKnown = data.snapshot?.speed_kind_known ?? false;
    const samples = data.leadEvents
      .map((event) => (kindsKnown ? event.first_human_touch_minutes : event.first_touch_minutes))
      .filter((minutes): minutes is number => minutes !== null);
    return HISTOGRAM_BINS.map((bin, i) => ({
      label: bin.label,
      count: samples.filter(
        (minutes) => minutes <= bin.max && (i === 0 || minutes > HISTOGRAM_BINS[i - 1].max),
      ).length,
    }));
  }, [data]);

  // Early returns for the error and loading states keep the main JSX below
  // free of null checks on `data`.
  if (error) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-6"><EmptyState>{error}</EmptyState></div>
    );
  }
  if (!data) return <div className="p-6"><Skeleton rows={8} /></div>;

  const { sub, snapshot, flags, acks, notes, leadEvents } = data;
  const details = snapshot?.details;
  const quality = snapshot
    ? snapshot.gate_passed ? coverageQuality(snapshot.coverage) : "held"
    : "no data";
  // Honesty in labeling: if automation touches couldn't be separated out, the
  // speed metric says so rather than passing automations off as humans.
  const speedLabel = snapshot?.speed_kind_known ? "Speed to lead (human)" : "Speed to lead (automation incl.)";
  const noData = !snapshot || !snapshot.gate_passed;
  // Split flags by acknowledgement; "Do next" is the top 3 unacked flags,
  // reds before ambers before infos.
  const unacked = flags.filter((f) => !ackedCodes.has(f.code));
  const acked = flags.filter((f) => ackedCodes.has(f.code));
  const doNext = [...unacked].sort((a, b) => {
    const rank = { red: 0, amber: 1, info: 2 } as const;
    return rank[a.severity] - rank[b.severity];
  }).slice(0, 3);
  // Detail tables tied to a firing (unacked) flag start expanded (spec 9.3.7).
  const firingTables = new Set(unacked.map((f) => FLAG_TABLE[f.code]).filter(Boolean));

  // Insert an acknowledgement, then reload so the flag moves to the acked list.
  async function submitAck(code: string, days: 7 | 14 | 30) {
    const email = session?.user?.email;
    if (!email || !locationId) return;
    setAckError(null);
    const err = await acknowledgeFlag({
      locationId, code, ackedBy: email, note: ackNote.trim() || undefined, snoozeDays: days,
    });
    if (err) {
      setAckError(err);
      return;
    }
    setAckFor(null);
    setAckNote("");
    void load();
  }

  // Insert a note (trimmed, capped at 4000 chars) and reload the list.
  async function submitNote() {
    const email = session?.user?.email;
    if (!email || !locationId || !noteBody.trim()) return;
    setNoteError(null);
    const err = await addNote(locationId, email, noteBody.trim().slice(0, 4000));
    if (err) {
      setNoteError(err);
      return;
    }
    setNoteBody("");
    void load();
  }

  // Relative-age label ("today" / "3d ago"); 86400000 = ms in a day.
  function daysAgo(iso: string): string {
    const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
    return days <= 0 ? "today" : `${days}d ago`;
  }

  return (
    <div className="mx-auto max-w-[1200px] px-4 py-4">
      {/* 1. header */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="mb-0.5 text-xxs text-muted">
            <Link to="/" className="underline underline-offset-2">Portfolio</Link> / {sub.slug}
          </div>
          <h1 className="text-lg font-semibold">{sub.name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-2">
            {sub.am_email ? <span>AM: {sub.am_email}</span> : null}
            {sub.vertical ? <span className="rounded bg-grid px-1.5 py-0.5 text-xxs">{sub.vertical}</span> : null}
            {sub.services.map((service) => (
              <span key={service} className="rounded bg-grid px-1.5 py-0.5 text-xxs">{service}</span>
            ))}
            <span className="text-xxs text-muted">
              MRR {sub.mrr !== null ? fmtMoney(sub.mrr) : "not set"} · contract end{" "}
              {sub.contract_end ? fmtDate(sub.contract_end) : "not set"}
            </span>
          </div>
        </div>
        <div className="text-right text-xs">
          <div className="mb-1 flex items-center justify-end gap-2">
            <QualityBadge quality={quality} />
            <span className="text-muted">
              {snapshot ? `snapshot ${fmtDate(snapshot.snapshot_date)}, ${fmtDateTime(snapshot.captured_at)}` : "no snapshot yet"}
            </span>
          </div>
          {details ? (
            <div className="flex justify-end gap-3">
              <ExternalLink href={details.ghl_dashboard_url}>Open in GHL</ExternalLink>
              <ExternalLink href={details.ghl_dashboard_url}>Ads (native dashboard)</ExternalLink>
            </div>
          ) : null}
          {sub.token_status !== "ok" ? (
            <div className="mt-1 text-status-critical">token {sub.token_status} — no fresh data</div>
          ) : null}
        </div>
      </div>

      {noData ? (
        <EmptyState>
          {snapshot
            ? "The latest snapshot was held by the data-quality gate — the numbers below it were not trusted. See Coverage and caveats."
            : "No snapshot collected yet for this account."}
        </EmptyState>
      ) : null}

      {/* 2. Do next */}
      <Section title="Do next">
        {doNext.length === 0 ? (
          <EmptyState>Nothing needs acknowledging — no unacked flags.</EmptyState>
        ) : (
          <div className="grid gap-1.5">
            {doNext.map((flag) => (
              <div key={flag.id} className="rounded border border-grid bg-surface px-3 py-2">
                <div className="flex items-start gap-2">
                  <SeverityChip severity={flag.severity} />
                  <div className="min-w-0 flex-1">
                    <span className="text-xs font-semibold">{flag.code}</span>
                    <p className="text-xs text-ink-2">{flag.action}</p>
                    {flag.deep_link ? (
                      <ExternalLink href={flag.deep_link}>
                        {flag.entity_name ?? flag.entity_type ?? "open in GHL"} ↗
                      </ExternalLink>
                    ) : null}
                  </div>
                  {/* toggles the inline ack form; clicking again closes it */}
                  <button
                    onClick={() => { setAckFor(ackFor === flag.code ? null : flag.code); setAckNote(""); }}
                    className="rounded border border-grid px-2 py-1 text-xxs text-ink-2 hover:bg-plane"
                  >
                    Acknowledge
                  </button>
                </div>
                {/* inline ack form: optional note + pick a snooze length,
                    which submits immediately (no separate save button) */}
                {ackFor === flag.code ? (
                  <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-grid pt-2">
                    <input
                      value={ackNote}
                      onChange={(e) => setAckNote(e.target.value)}
                      placeholder="Note (optional): what did you do?"
                      className="min-w-64 flex-1 rounded border border-grid px-2 py-1 text-xs"
                    />
                    <span className="text-xxs text-muted">snooze:</span>
                    {([7, 14, 30] as const).map((days) => (
                      <button key={days} onClick={() => void submitAck(flag.code, days)}
                              className="rounded border border-grid px-2 py-1 text-xxs hover:bg-plane">
                        {days}d
                      </button>
                    ))}
                    {ackError ? <span className="text-xxs text-status-critical">{ackError}</span> : null}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
        {/* already-acknowledged flags: quiet gray receipts of who/when/note */}
        {acked.length > 0 ? (
          <div className="mt-2 grid gap-1">
            {acked.map((flag) => {
              const ack = acks.find((a) => a.code === flag.code);
              return (
                <div key={flag.id} className="rounded border border-grid/60 bg-plane px-3 py-1.5 text-xxs text-muted">
                  {flag.code} — acked
                  {ack ? ` by ${ack.acked_by.split("@")[0]}, ${daysAgo(ack.acked_at)}${ack.note ? `: ${ack.note}` : ""} (until ${fmtDate(ack.snooze_until)})` : ""}
                </div>
              );
            })}
          </div>
        ) : null}
      </Section>

      {/* 3. Changed this week */}
      <Section title="Changed this week">
        {(snapshot?.flags_new?.length || snapshot?.flags_resolved?.length) ? (
          <div className="flex flex-wrap gap-1.5">
            {(snapshot?.flags_new ?? []).map((code) => (
              <span key={`n-${code}`} className="rounded bg-status-critical/10 px-1.5 py-0.5 text-xxs text-status-critical">
                + {code}
              </span>
            ))}
            {(snapshot?.flags_resolved ?? []).map((code) => (
              <span key={`r-${code}`} className="rounded bg-status-good/10 px-1.5 py-0.5 text-xxs text-status-good-text">
                − {code}
              </span>
            ))}
          </div>
        ) : (
          <EmptyState>No flag changes vs last week.</EmptyState>
        )}
      </Section>

      {/* 4. KPI tiles */}
      {/* Each tile hard-codes its "bad" threshold inline (uncontacted >= 3,
          waiting >= 24h, stale >= $25k, ...) — the tone only colors the value;
          the number itself always shows (color never carries meaning alone). */}
      {snapshot && !noData ? (
        <div className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
          <StatTile
            label="Leads 7d"
            value={fmtNum(snapshot.leads_new_7d)}
            context={
              snapshot.leads_delta_pct !== null
                ? `${fmtPct(snapshot.leads_delta_pct)} vs baseline${
                    snapshot.peer_median_delta_pct !== null ? `, peers ${fmtPct(snapshot.peer_median_delta_pct)}` : ""}`
                : `baseline building (${snapshot.trailing_n ?? 0}/4 weeks)`
            }
            tone={snapshot.leads_delta_pct !== null && snapshot.leads_delta_pct <= -40 ? "bad" : undefined}
          />
          <StatTile label={speedLabel} value={fmtMinutes(snapshot.speed_to_lead_median_min)}
                    context={`p90 ${fmtMinutes(snapshot.speed_to_lead_p90_min)}`} />
          <StatTile label="Uncontacted >24h" value={fmtNum(snapshot.leads_uncontacted_24h)}
                    tone={(snapshot.leads_uncontacted_24h ?? 0) >= 3 ? "bad" : undefined} />
          <StatTile label="Convos waiting" value={fmtNum(snapshot.convos_waiting)}
                    context={(snapshot.convos_waiting ?? 0) > 0 ? `longest ${fmtHours(snapshot.convos_waiting_max_hours)}` : undefined}
                    tone={(snapshot.convos_waiting_max_hours ?? 0) >= 24 ? "bad" : undefined} />
          <StatTile label="Missed calls 7d" value={fmtNum(snapshot.calls_missed_7d)}
                    tone={(snapshot.calls_missed_7d ?? 0) > 0 ? "bad" : undefined} />
          <StatTile label="Open pipeline" value={fmtMoney(snapshot.opps_open_value)}
                    context={`${fmtNum(snapshot.opps_open)} open`} />
          <StatTile label="Stale value (14d+)" value={fmtMoney(snapshot.opps_stale_value)}
                    context={`${fmtNum(snapshot.opps_stale)} deals`}
                    tone={(snapshot.opps_stale_value ?? 0) >= 25000 ? "bad" : undefined} />
          <StatTile label="Deals moved 30d" value={fmtNum(snapshot.opps_moved_30d)}
                    context="created, staged, or closed"
                    tone={snapshot.opps_moved_30d === 0 && (snapshot.opps_open ?? 0) >= 10
                      ? "bad" : undefined} />
          <StatTile label="Past due (SSP)" value={fmtMoney(snapshot.invoices_past_due_amount)}
                    context={`${fmtNum(snapshot.invoices_past_due)} invoices`}
                    tone={(snapshot.invoices_past_due ?? 0) > 0 ? "bad" : undefined} />
          <StatTile label="Days since publish" value={fmtNum(snapshot.days_since_last_publish)}
                    tone={(snapshot.days_since_last_publish ?? 0) >= 14 ? "bad" : undefined} />
          <StatTile label="Client last touch"
                    value={snapshot.client_last_touch_days !== null ? `${snapshot.client_last_touch_days}d` : UNKNOWN}
                    context={snapshot.client_next_appt_at
                      ? `next appt ${fmtDateTime(snapshot.client_next_appt_at)}` : "nothing scheduled"} />
          <StatTile label="No-show 28d"
                    value={snapshot.noshow_rate_28d !== null ? `${snapshot.noshow_rate_28d.toFixed(0)}%` : UNKNOWN}
                    context={`${fmtNum(snapshot.appts_showed_28d)} showed / ${fmtNum(snapshot.appts_noshow_28d)} no-show`}
                    tone={(snapshot.noshow_rate_28d ?? 0) >= 30 ? "bad" : undefined} />
          <StatTile label="Lead → opp 28d"
                    value={snapshot.lead_to_opp_28d_pct !== null ? `${snapshot.lead_to_opp_28d_pct.toFixed(0)}%` : UNKNOWN} />
          <StatTile label="Win rate 90d"
                    value={snapshot.win_rate_90d !== null ? `${snapshot.win_rate_90d.toFixed(0)}%` : UNKNOWN}
                    context={snapshot.median_days_to_close_90d !== null
                      ? `median ${snapshot.median_days_to_close_90d.toFixed(0)}d to close` : undefined} />
        </div>
      ) : null}

      {/* 5. funnel strip */}
      {details ? (
        <Section title="Funnel (28d)">
          <div className="flex flex-wrap items-center gap-2 rounded border border-grid bg-surface px-3 py-2 text-xs">
            {/* label/value pairs joined by arrows; null renders as "n/a" */}
            {[
              ["Form submissions", details.funnel_28d.form_submissions],
              ["New leads", details.funnel_28d.leads],
              ["Opps created", details.funnel_28d.opps_created],
              ["Appts booked", details.funnel_28d.appts_booked],
              ["Won", details.funnel_28d.won],
            ].map(([label, value], index) => (
              <span key={label as string} className="flex items-center gap-2">
                {index > 0 ? <span className="text-muted">→</span> : null}
                <span>
                  <span className="text-xxs uppercase tracking-wide text-muted">{label} </span>
                  <span className="font-semibold tabular">{value === null ? "n/a" : String(value)}</span>
                </span>
              </span>
            ))}
          </div>
        </Section>
      ) : null}

      {/* 6. charts */}
      {stackedChart.rows.length >= 2 ? (
        <div className="mb-6 grid gap-4 lg:grid-cols-3">
          {/* stacked bars: weekly leads split by source, with the trailing
              4-week average overlaid as a dashed reference line */}
          <div className="rounded border border-grid bg-surface p-3 lg:col-span-2">
            <div className="mb-1 text-xs font-semibold">
              Leads by week (12 wk, stacked by source) <span className="font-normal text-muted">— dashed line: trailing 4wk avg</span>
            </div>
            <ResponsiveContainer width="100%" height={200}>
              <ComposedChart data={stackedChart.rows} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="week" tick={{ fontSize: 10, fill: INK2 }} tickFormatter={fmtDate}
                       stroke={GRID} minTickGap={30} />
                <YAxis tick={{ fontSize: 10, fill: INK2 }} stroke={GRID} allowDecimals={false} />
                <Tooltip labelFormatter={(v) => `week of ${fmtDate(String(v))}`}
                         contentStyle={{ fontSize: 11, borderColor: GRID }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {/* one <Bar> per source; the shared stackId piles them up */}
                {stackedChart.sources.map((source, i) => (
                  <Bar key={source} dataKey={source} stackId="leads"
                       fill={SOURCE_COLORS[i % SOURCE_COLORS.length]}
                       stroke="#fcfcfb" strokeWidth={1} maxBarSize={40} />
                ))}
                <Line dataKey="baseline" name="trailing avg" stroke={BASELINE}
                      strokeWidth={1.5} strokeDasharray="4 3" dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          {/* companion line chart: this account's % delta vs its own baseline,
              against the peer median — separates "we dipped" from "the whole
              vertical dipped" */}
          <div className="rounded border border-grid bg-surface p-3">
            <div className="mb-1 flex items-baseline gap-3 text-xs">
              <span className="font-semibold">Delta vs baseline (%)</span>
              <span style={{ color: SERIES }}>— account</span>
              <span className="text-muted">--- peer median</span>
            </div>
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={deltaChart} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: INK2 }} tickFormatter={fmtDate}
                       stroke={GRID} minTickGap={40} />
                <YAxis tick={{ fontSize: 10, fill: INK2 }} stroke={GRID} />
                <ReferenceLine y={0} stroke={BASELINE} />
                <Tooltip labelFormatter={(v) => fmtDate(String(v))}
                         contentStyle={{ fontSize: 11, borderColor: GRID }} />
                <Line dataKey="account" name="account" stroke={SERIES} strokeWidth={2} dot={false} connectNulls />
                <Line dataKey="peers" name="peer median" stroke={BASELINE} strokeWidth={1.5}
                      strokeDasharray="4 3" dot={false} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : null}

      {leadEvents.length > 0 ? (
        <Section title="After-hours arrivals vs response">
          <AfterHoursHeatmap events={leadEvents} timeZone={sub.timezone || "America/Chicago"} />
        </Section>
      ) : null}

      {/* response-time distribution — rendered only when any bar is non-zero */}
      {histogram.some((bin) => bin.count > 0) ? (
        <Section title={`${speedLabel} — distribution, last 30 days`}>
          <div className="rounded border border-grid bg-surface p-3">
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={histogram} margin={{ top: 4, right: 8, bottom: 0, left: -24 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 10, fill: INK2 }} stroke={GRID} />
                <YAxis tick={{ fontSize: 10, fill: INK2 }} stroke={GRID} allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 11, borderColor: GRID }} />
                <Bar dataKey="count" name="leads" fill={SERIES} radius={[3, 3, 0, 0]} maxBarSize={48} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Section>
      ) : null}

      {/* 7. detail tables — collapsed unless a firing flag ties to them */}
      {/* All follow one pattern: Collapsible wrapper (auto-open when its flag
          fires) around a DetailTable whose columns pull from details.ts, with
          deep links back into GHL wherever the data offers one. */}
      {details ? (
        <>
          <Collapsible title={cappedTitle("Uncontacted leads", snapshot?.leads_uncontacted_24h, details.uncontacted_leads.length)}
                       defaultOpen={firingTables.has("uncontacted")}>
            <DetailTable rows={details.uncontacted_leads} empty="No leads sitting uncontacted past 24h."
              columns={[
                { header: "Lead", cell: (r) => <ExternalLink href={r.deep_link}>{r.name}</ExternalLink> },
                { header: "Source", cell: (r) => r.source ?? "—" },
                { header: "Created", cell: (r) => fmtDateTime(r.created_at) },
                { header: "Waiting", cell: (r) => fmtHours(r.hours_since), numeric: true },
              ]} />
          </Collapsible>

          <Collapsible title={cappedTitle("Unassigned leads", snapshot?.leads_unassigned_7d, details.unassigned_leads.length)}
                       defaultOpen={firingTables.has("unassigned")}>
            <DetailTable rows={details.unassigned_leads} empty="Every new lead has an owner."
              columns={[
                { header: "Lead", cell: (r) => <ExternalLink href={r.deep_link}>{r.name}</ExternalLink> },
                { header: "Source", cell: (r) => r.source ?? "—" },
                { header: "Created", cell: (r) => fmtDateTime(r.created_at) },
              ]} />
          </Collapsible>

          {/* missed_calls is optional (Tier 2) — old snapshots lack it, hence
              the ?? [] fallback and the explanatory empty-state wording */}
          <Collapsible title={cappedTitle("Missed calls", snapshot?.calls_missed_7d, details.missed_calls?.length ?? 0)}
                       defaultOpen={(snapshot?.calls_missed_7d ?? 0) > 0}>
            <DetailTable rows={details.missed_calls ?? []}
              empty="No unanswered inbound calls in the window (or this snapshot predates the missed-calls metric)."
              columns={[
                { header: "Contact", cell: (r) => <ExternalLink href={r.deep_link}>{r.contact}</ExternalLink> },
                { header: "When", cell: (r) => fmtDateTime(r.at) },
                { header: "Call status", cell: (r) => r.status },
              ]} />
          </Collapsible>

          <Collapsible title={cappedTitle("Waiting conversations", snapshot?.convos_waiting, details.waiting_convos.length)}
                       defaultOpen={firingTables.has("waiting")}>
            <DetailTable rows={details.waiting_convos} empty="Nothing inbound is waiting 4h+."
              columns={[
                { header: "Contact", cell: (r) => <ExternalLink href={r.deep_link}>{r.contact}</ExternalLink> },
                { header: "Channel", cell: (r) => r.channel ?? "—" },
                { header: "Last inbound", cell: (r) => fmtDateTime(r.last_inbound_at) },
                { header: "Waiting", cell: (r) => fmtHours(r.hours), numeric: true },
              ]} />
          </Collapsible>

          <Collapsible title={cappedTitle("Stale opportunities", snapshot?.opps_stale, details.stale_opps.length)}
                       defaultOpen={firingTables.has("stale")}>
            <DetailTable rows={details.stale_opps} empty="No open deals idle 14 days or more."
              columns={[
                { header: "Opportunity", cell: (r) => <ExternalLink href={r.deep_link}>{r.name}</ExternalLink> },
                { header: "Pipeline / stage", cell: (r) => `${r.pipeline} / ${r.stage}` },
                { header: "Owner", cell: (r) => r.owner },
                { header: "Idle", cell: (r) => (r.days_idle !== null ? `${Math.round(r.days_idle)}d` : UNKNOWN), numeric: true },
                { header: "In stage", cell: (r) => (r.days_in_stage !== null ? `${Math.round(r.days_in_stage)}d` : UNKNOWN), numeric: true },
                { header: "Value", cell: (r) => fmtMoney(r.value), numeric: true },
                { header: "Next step", cell: (r) => r.next_step },
              ]} />
          </Collapsible>

          <Collapsible title={`Missing value (${details.missing_value_opps.length})`} defaultOpen={false}>
            <DetailTable rows={details.missing_value_opps} empty="Every open deal has a dollar value."
              columns={[{ header: "Opportunity", cell: (r) => <ExternalLink href={r.deep_link}>{r.name}</ExternalLink> }]} />
          </Collapsible>

          <Collapsible title={`Past-due invoices — SSP to client (${details.past_due_invoices.length})`}
                       defaultOpen={firingTables.has("past_due")}>
            <DetailTable rows={details.past_due_invoices} empty="Nothing past due."
              columns={[
                { header: "Invoice", cell: (r) => r.number ?? r.invoice_id },
                { header: "Status", cell: (r) => r.status },
                { header: "Due", cell: (r) => fmtDate(r.due_date) },
                { header: "Days over", cell: (r) => r.days_over, numeric: true },
                { header: "Amount", cell: (r) => fmtMoney(r.amount_due), numeric: true },
              ]} />
          </Collapsible>

          <Collapsible title={`Appointments next 7 days (${details.appts_next_7d.length})`}
                       defaultOpen={firingTables.has("appts")}>
            <DetailTable rows={details.appts_next_7d} empty="Nothing on the calendar in the next 7 days."
              columns={[
                { header: "Title", cell: (r) => r.title },
                { header: "When", cell: (r) => fmtDateTime(r.start) },
                { header: "Status", cell: (r) => r.status ?? "—" },
              ]} />
          </Collapsible>

          <Collapsible title={`Recent publishes (${details.recent_publishes.length})`}
                       defaultOpen={firingTables.has("publishes")}>
            <DetailTable rows={details.recent_publishes.slice(0, 15)}
              empty={sub.services.some((s) => s === "content" || s === "social")
                ? "Nothing published in the last 90 days."
                : "Content/social is not in this account's services."}
              columns={[
                { header: "Kind", cell: (r) => r.kind },
                { header: "Title", cell: (r) => r.title ?? "—" },
                { header: "Published", cell: (r) => fmtDateTime(r.published_at) },
              ]} />
          </Collapsible>

          <Collapsible title={`Social accounts (${details.social_accounts.length})`}
                       defaultOpen={firingTables.has("social")}>
            <DetailTable rows={details.social_accounts}
              empty="No social accounts connected (or social is not in services)."
              columns={[
                { header: "Platform", cell: (r) => r.platform ?? "—" },
                { header: "Account", cell: (r) => r.name ?? r.id },
                // glyph + word, not just color, per the accessibility rule
                { header: "Status", cell: (r) => r.expired === null ? UNKNOWN : r.expired
                  ? <span className="text-status-critical">▲ disconnected</span>
                  : <span className="text-status-good-text">✓ connected</span> },
              ]} />
          </Collapsible>

          <Collapsible title="Review proxies" defaultOpen={firingTables.has("reviews")}>
            <div className="grid gap-4 lg:grid-cols-2">
              <DetailTable rows={details.review_asks_stale} empty="No stale review asks."
                columns={[
                  { header: "Contact", cell: (r) => <ExternalLink href={r.deep_link}>{r.name}</ExternalLink> },
                  { header: "Quiet for", cell: (r) => `${r.days_quiet}d`, numeric: true },
                ]} />
              <DetailTable rows={details.review_ask_gap} empty="Every recent win got a review ask."
                columns={[
                  { header: "Won deal (no ask)", cell: (r) => <ExternalLink href={r.deep_link}>{r.name}</ExternalLink> },
                  { header: "Won", cell: (r) => fmtDate(r.won_at) },
                ]} />
            </div>
          </Collapsible>

          <Collapsible title="Lost reasons, last 90 days" defaultOpen={false}>
            {Object.keys(details.lost_reasons_90d).length === 0 ? (
              <EmptyState>No lost deals in the last 90 days.</EmptyState>
            ) : (
              // rows here are [reason, count] tuples, sorted by count desc
              <DetailTable rows={Object.entries(details.lost_reasons_90d).sort((a, b) => b[1] - a[1])} empty=""
                columns={[
                  { header: "Reason", cell: ([reason]) => reason },
                  { header: "Count", cell: ([, count]) => count, numeric: true },
                ]} />
            )}
          </Collapsible>

          <Collapsible title={`Speed to lead — newest contacts (${leadEvents.length})`} defaultOpen={false}>
            <DetailTable rows={leadEvents.slice(0, 25)} empty="No lead events recorded yet."
              columns={[
                { header: "Lead", cell: (r) => r.contact_name ?? r.contact_id },
                { header: "Created", cell: (r) => fmtDateTime(r.created_at) },
                { header: "First touch", cell: (r) => fmtMinutes(r.first_touch_minutes), numeric: true },
                { header: "First human", cell: (r) => fmtMinutes(r.first_human_touch_minutes), numeric: true },
                { header: "Kind", cell: (r) => r.first_outbound_kind ?? UNKNOWN },
              ]} />
          </Collapsible>
        </>
      ) : null}

      {/* 7c. per-form / per-survey health (docs/FORMS-INTEGRATION.md Phase 1):
          every form and survey in the account with its own status. Silent
          entries sort first; auto-opens whenever any entry is silent. */}
      {data.formHealth.length > 0 ? (
        <Collapsible
          title={`Forms & surveys (${data.formHealth.length}, ${
            data.formHealth.filter((f) => f.status === "silent").length} silent)`}
          defaultOpen={data.formHealth.some((f) => f.status === "silent")}
        >
          <div className="rounded border border-grid bg-surface p-3">
            <DetailTable
              rows={[...data.formHealth].sort((a, b) => {
                const order = { silent: 0, unknown: 1, active: 2, new: 3, no_leads: 4 };
                return (order[a.status] ?? 9) - (order[b.status] ?? 9)
                  || a.name.localeCompare(b.name);
              })}
              empty="No forms or surveys found in this account."
              columns={[
                { header: "Name", cell: (f) => f.name },
                { header: "Type", cell: (f) => f.kind },
                {
                  header: "Status",
                  cell: (f) => {
                    // icon + label, never color alone
                    const meta: Record<string, [string, string]> = {
                      active: ["✓ active", "text-status-good-text"],
                      silent: ["⚠ went silent", "text-status-critical"],
                      new: ["+ new", "text-series"],
                      no_leads: ["○ no leads yet", "text-muted"],
                      unknown: ["? unknown", "text-muted"],
                    };
                    const [label, tone] = meta[f.status] ?? [f.status, "text-ink-2"];
                    return <span className={`font-medium ${tone}`}>{label}</span>;
                  },
                },
                { header: "Submissions", cell: (f) => fmtNum(f.submissions_total), numeric: true },
                {
                  header: "Last submission",
                  cell: (f) => (f.last_submission_at ? fmtDateTime(f.last_submission_at) : "—"),
                },
                {
                  header: "Quiet days",
                  cell: (f) => {
                    if (!f.last_submission_at) return "—";
                    const days = Math.floor(
                      (Date.now() - new Date(f.last_submission_at).getTime()) / 86400000);
                    return String(days);
                  },
                  numeric: true,
                },
              ]}
            />
            <p className="mt-1 text-xxs text-muted">
              "Went silent" counts business days only — a form quiet over the
              weekend is not an alarm. Checked nightly by the collector.
            </p>
          </div>
        </Collapsible>
      ) : null}

      {/* 7d. tracking tags (Phase 2): did the client site's pixels actually
          fire when the tag checker loaded it? Hidden until a check exists. */}
      {data.tagCheck ? (
        <Collapsible
          title={`Tracking tags — ${
            data.tagCheck.status === "ok" ? "all firing"
              : data.tagCheck.status === "alert" ? "problem detected" : "check failed"}`}
          defaultOpen={data.tagCheck.status !== "ok"}
        >
          <div className="rounded border border-grid bg-surface p-3">
            <p className="mb-2 text-xxs text-muted">
              Checked {fmtDateTime(data.tagCheck.checked_at)} on{" "}
              <ExternalLink href={data.tagCheck.url}>{data.tagCheck.url}</ExternalLink>
            </p>
            {data.tagCheck.error ? (
              <p className="text-xs text-status-critical">
                The page could not be checked: {data.tagCheck.error}
              </p>
            ) : (
              <DetailTable
                rows={data.tagCheck.results}
                empty="No tag expectations configured for this account."
                columns={[
                  { header: "Tag", cell: (r) => r.type },
                  { header: "Expected ID", cell: (r) => r.id ?? "any" },
                  {
                    header: "Result",
                    cell: (r) =>
                      r.fired === true ? <span className="font-medium text-status-good-text">✓ fired</span>
                        : r.fired === false ? <span className="font-medium text-status-critical">✗ did not fire</span>
                          : <span className="text-muted">? {r.note ?? "unknown"}</span>,
                  },
                ]}
              />
            )}
          </div>
        </Collapsible>
      ) : null}

      {/* 8. coverage: the honesty layer */}
      {/* auto-opens whenever quality isn't "complete", so partial data never
          hides its caveats; the raw JSON dump below the table is for debugging */}
      {snapshot ? (
        <Collapsible title="Coverage and caveats" defaultOpen={quality !== "complete"}>
          <div className="rounded border border-grid bg-surface p-3">
            <DetailTable rows={Object.entries(snapshot.coverage.sources ?? {})} empty="No coverage recorded."
              columns={[
                { header: "Source", cell: ([name]) => name },
                { header: "Status", cell: ([, entry]) => entry.status },
                { header: "Retrieved", cell: ([, entry]) => entry.retrieved, numeric: true },
                { header: "Exhausted", cell: ([, entry]) => (entry.exhausted ? "yes" : "no") },
                { header: "Error", cell: ([, entry]) => entry.error ?? "—" },
                { header: "Note", cell: ([, entry]) => entry.note ?? "—" },
              ]} />
            <pre className="mt-2 max-h-64 overflow-auto rounded bg-plane p-2 text-xxs text-ink-2">
              {JSON.stringify(snapshot.coverage, null, 2)}
            </pre>
          </div>
        </Collapsible>
      ) : null}

      {/* copy-ready weekly client summary (Tier 2): template fill, no LLM */}
      {snapshot && !noData ? (
        <Section
          title="Weekly client summary (copy-ready)"
          right={
            // Clipboard copy via the async navigator.clipboard API; on success
            // the button flips to "Copied ✓" for 2 seconds, then reverts.
            <button
              onClick={() => {
                void navigator.clipboard.writeText(buildClientSummary(sub, snapshot)).then(() => {
                  setCopied(true);
                  setTimeout(() => setCopied(false), 2000);
                });
              }}
              className="rounded border border-grid px-2 py-1 text-xxs text-ink-2 hover:bg-plane"
            >
              {copied ? "Copied ✓" : "Copy"}
            </button>
          }
        >
          <pre className="whitespace-pre-wrap rounded border border-grid bg-surface p-3 font-sans text-xs text-ink-2">
            {buildClientSummary(sub, snapshot)}
          </pre>
          <p className="mt-1 text-xxs text-muted">
            Client-facing wording, filled from this snapshot only — lines with unknown data are omitted.
            Paste into your weekly email and edit freely.
          </p>
        </Section>
      ) : null}

      {/* 9. notes — append-only */}
      <Section title={`Notes (${notes.length})`}>
        <div className="mb-2 flex gap-2">
          <input
            value={noteBody}
            onChange={(e) => setNoteBody(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void submitNote(); }}
            placeholder="Add a note (append-only; no edit, no delete)…"
            maxLength={4000}
            className="flex-1 rounded border border-grid bg-surface px-2 py-1.5 text-xs"
          />
          <button onClick={() => void submitNote()} disabled={!noteBody.trim()}
                  className="rounded bg-ink px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">
            Add note
          </button>
        </div>
        {noteError ? <p className="mb-2 text-xs text-status-critical">{noteError}</p> : null}
        {notes.length === 0 ? (
          <EmptyState>No notes yet.</EmptyState>
        ) : (
          <div className="grid gap-1.5">
            {notes.map((note) => (
              <div key={note.id} className="rounded border border-grid bg-surface px-3 py-2">
                <div className="mb-0.5 text-xxs text-muted">
                  {note.author.split("@")[0]} · {fmtDateTime(note.created_at)}
                </div>
                <p className="whitespace-pre-wrap text-xs text-ink-2">{note.body}</p>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
