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
import type {
  AccountNoteRow,
  FlagAckRow,
  FlagRow,
  HistoryRow,
  LeadEventRow,
  LeadHistoryRow,
  SnapshotRow,
  SubaccountRow,
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

const SERIES = "#2a78d6";
const BASELINE = "#898781";
const GRID = "#e1e0d9";
const INK2 = "#52514e";
// Categorical slots for the stacked source chart (validated palette order)
const SOURCE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#c3c2b7"];

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

type Loaded = {
  sub: SubaccountRow;
  snapshot: SnapshotRow | null;
  flags: FlagRow[];
  acks: FlagAckRow[];
  notes: AccountNoteRow[];
  weekly: LeadHistoryRow[];
  history: HistoryRow[];
  leadEvents: LeadEventRow[];
};

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
  const { locationId } = useParams<{ locationId: string }>();
  const { session } = useSession();
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ackFor, setAckFor] = useState<string | null>(null);
  const [ackNote, setAckNote] = useState("");
  const [ackError, setAckError] = useState<string | null>(null);
  const [noteBody, setNoteBody] = useState("");
  const [noteError, setNoteError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!locationId) return;
    const { data: subRows, error: subErr } = await supabase
      .from("subaccounts").select("*").eq("location_id", locationId).limit(1);
    if (subErr || !subRows?.length) {
      setError(subErr?.message ?? "Account not found (or you don't have access).");
      return;
    }
    const { data: snapRows } = await supabase
      .from("snapshots").select("*").eq("location_id", locationId)
      .order("snapshot_date", { ascending: false })
      .order("captured_at", { ascending: false }).limit(1);
    const snapshot = (snapRows?.[0] ?? null) as SnapshotRow | null;

    const twelveWeeksAgo = new Date(Date.now() - 12 * 7 * 86400 * 1000).toISOString().slice(0, 10);
    const [flagsRes, acksRes, notesRes, weeklyRes, historyRes, eventsRes] = await Promise.all([
      snapshot
        ? supabase.from("flags").select("*").eq("location_id", locationId)
            .eq("snapshot_date", snapshot.snapshot_date)
        : Promise.resolve({ data: [] as FlagRow[] }),
      supabase.from("flag_acks").select("*").eq("location_id", locationId)
        .gte("snooze_until", new Date().toISOString().slice(0, 10))
        .order("acked_at", { ascending: false }),
      supabase.from("account_notes").select("*").eq("location_id", locationId)
        .order("created_at", { ascending: false }).limit(100),
      supabase.from("lead_history").select("*").eq("location_id", locationId)
        .gte("week_start", twelveWeeksAgo).order("week_start", { ascending: true }),
      supabase.from("v_history").select("*").eq("location_id", locationId)
        .order("snapshot_date", { ascending: true }),
      supabase.from("lead_events").select("*").eq("location_id", locationId)
        .gte("created_at", new Date(Date.now() - 30 * 86400 * 1000).toISOString())
        .order("created_at", { ascending: false }).limit(500),
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
    });
  }, [locationId]);

  useEffect(() => {
    void load();
  }, [load]);

  const ackedCodes = useMemo(() => new Set((data?.acks ?? []).map((a) => a.code)), [data]);

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

  const deltaChart = useMemo(() => {
    if (!data) return [];
    return data.history.filter((row) => row.gate_passed).slice(-84).map((row) => ({
      date: row.snapshot_date,
      account: row.leads_delta_pct,
      peers: row.peer_median_delta_pct,
    }));
  }, [data]);

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
  const speedLabel = snapshot?.speed_kind_known ? "Speed to lead (human)" : "Speed to lead (automation incl.)";
  const noData = !snapshot || !snapshot.gate_passed;
  const unacked = flags.filter((f) => !ackedCodes.has(f.code));
  const acked = flags.filter((f) => ackedCodes.has(f.code));
  const doNext = [...unacked].sort((a, b) => {
    const rank = { red: 0, amber: 1, info: 2 } as const;
    return rank[a.severity] - rank[b.severity];
  }).slice(0, 3);
  const firingTables = new Set(unacked.map((f) => FLAG_TABLE[f.code]).filter(Boolean));

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
                  <button
                    onClick={() => { setAckFor(ackFor === flag.code ? null : flag.code); setAckNote(""); }}
                    className="rounded border border-grid px-2 py-1 text-xxs text-ink-2 hover:bg-plane"
                  >
                    Acknowledge
                  </button>
                </div>
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
          <StatTile label="Open pipeline" value={fmtMoney(snapshot.opps_open_value)}
                    context={`${fmtNum(snapshot.opps_open)} open`} />
          <StatTile label="Stale value (14d+)" value={fmtMoney(snapshot.opps_stale_value)}
                    context={`${fmtNum(snapshot.opps_stale)} deals`}
                    tone={(snapshot.opps_stale_value ?? 0) >= 25000 ? "bad" : undefined} />
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
      {details ? (
        <>
          <Collapsible title={`Uncontacted leads (${details.uncontacted_leads.length})`}
                       defaultOpen={firingTables.has("uncontacted")}>
            <DetailTable rows={details.uncontacted_leads} empty="No leads sitting uncontacted past 24h."
              columns={[
                { header: "Lead", cell: (r) => <ExternalLink href={r.deep_link}>{r.name}</ExternalLink> },
                { header: "Source", cell: (r) => r.source ?? "—" },
                { header: "Created", cell: (r) => fmtDateTime(r.created_at) },
                { header: "Waiting", cell: (r) => fmtHours(r.hours_since), numeric: true },
              ]} />
          </Collapsible>

          <Collapsible title={`Unassigned leads (${details.unassigned_leads.length})`}
                       defaultOpen={firingTables.has("unassigned")}>
            <DetailTable rows={details.unassigned_leads} empty="Every new lead has an owner."
              columns={[
                { header: "Lead", cell: (r) => <ExternalLink href={r.deep_link}>{r.name}</ExternalLink> },
                { header: "Source", cell: (r) => r.source ?? "—" },
                { header: "Created", cell: (r) => fmtDateTime(r.created_at) },
              ]} />
          </Collapsible>

          <Collapsible title={`Waiting conversations (${details.waiting_convos.length})`}
                       defaultOpen={firingTables.has("waiting")}>
            <DetailTable rows={details.waiting_convos} empty="Nothing inbound is waiting 4h+."
              columns={[
                { header: "Contact", cell: (r) => <ExternalLink href={r.deep_link}>{r.contact}</ExternalLink> },
                { header: "Channel", cell: (r) => r.channel ?? "—" },
                { header: "Last inbound", cell: (r) => fmtDateTime(r.last_inbound_at) },
                { header: "Waiting", cell: (r) => fmtHours(r.hours), numeric: true },
              ]} />
          </Collapsible>

          <Collapsible title={`Stale opportunities (${details.stale_opps.length})`}
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

      {/* 8. coverage: the honesty layer */}
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
