import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { Sparkline } from "../components/Sparkline";
import { EmptyState, QualityBadge, Section, SeverityChip, Skeleton, StateIcon } from "../components/ui";
import type { FlagRow, LeadHistoryRow, PortfolioRow, PortfolioState } from "../lib/database.types";
import { dataQuality, fmtDate, fmtHours, fmtMoney, fmtNum, fmtPct, UNKNOWN } from "../lib/format";
import { supabase } from "../lib/supabase";
import { useSession } from "../lib/useSession";
import { acknowledgeFlag } from "../lib/writes";

const REFRESH_MS = 15 * 60 * 1000;
const SPARK_WEEKS = 8;
const COLUMNS_KEY = "portfolio.columns";

// Expanded columns behind the column chooser (spec v3 9.3), persisted locally.
const EXPANDED_COLUMNS = [
  { key: "stale", label: "Stale opps" },
  { key: "past_due", label: "Past due" },
  { key: "publish", label: "Since publish" },
  { key: "client_touch", label: "Client touch" },
  { key: "unassigned", label: "Unassigned" },
  { key: "noshow", label: "No-show %" },
  { key: "lead_opp", label: "Lead→opp %" },
  { key: "win_rate", label: "Win rate" },
  { key: "social", label: "Social disc." },
  { key: "mrr", label: "MRR" },
  { key: "contract", label: "Contract end" },
  { key: "quality", label: "Quality" },
  { key: "snapshot", label: "Snapshot" },
] as const;
type ExpandedKey = (typeof EXPANDED_COLUMNS)[number]["key"];

function loadColumns(): Set<ExpandedKey> {
  try {
    const raw = localStorage.getItem(COLUMNS_KEY);
    if (raw) return new Set(JSON.parse(raw) as ExpandedKey[]);
  } catch {
    /* fall through to default */
  }
  return new Set<ExpandedKey>(["stale", "past_due", "quality", "snapshot"]);
}

function sortByAttention(rows: PortfolioRow[]): PortfolioRow[] {
  return [...rows].sort((a, b) => {
    if (b.attention_score !== a.attention_score) return b.attention_score - a.attention_score;
    const da = a.leads_delta_pct ?? Number.POSITIVE_INFINITY;
    const db = b.leads_delta_pct ?? Number.POSITIVE_INFINITY;
    return da - db;
  });
}

function sortByMrrAtRisk(rows: PortfolioRow[]): PortfolioRow[] {
  return [...rows].sort((a, b) => {
    const aRisk = a.attention_score > 0 ? 0 : 1;
    const bRisk = b.attention_score > 0 ? 0 : 1;
    if (aRisk !== bRisk) return aRisk - bRisk;
    return (b.mrr ?? -1) - (a.mrr ?? -1);
  });
}

function csvEscape(value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export default function Portfolio() {
  const { session } = useSession();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<PortfolioRow[] | null>(null);
  const [sparks, setSparks] = useState<Record<string, (number | null)[]>>({});
  const [flagsByLoc, setFlagsByLoc] = useState<Record<string, FlagRow[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [columns, setColumns] = useState<Set<ExpandedKey>>(loadColumns);
  const [chooserOpen, setChooserOpen] = useState(false);
  const [ackNotice, setAckNotice] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const view = params.get("view") === "all" ? "all" : "mine";
  const includeSsp = params.get("ssp") === "1";
  const search = params.get("q") ?? "";
  const vertical = params.get("vertical") ?? "";
  const stateFilter = params.get("state") ?? "";
  const flagFilter = params.get("flag") ?? "";
  const sortMode = params.get("sort") === "mrr" ? "mrr" : "attention";
  const groupByAm = params.get("group") === "am";

  const load = useCallback(async () => {
    const { data, error: err } = await supabase.from("v_portfolio").select("*");
    if (err) {
      setError(err.message);
      return;
    }
    const portfolio = (data ?? []) as PortfolioRow[];
    setRows(portfolio);
    setError(null);

    const since = new Date(Date.now() - SPARK_WEEKS * 7 * 86400 * 1000)
      .toISOString().slice(0, 10);
    const { data: history } = await supabase
      .from("lead_history")
      .select("location_id,week_start,leads")
      .gte("week_start", since)
      .order("week_start", { ascending: true });
    const grouped: Record<string, (number | null)[]> = {};
    for (const entry of (history ?? []) as Pick<LeadHistoryRow, "location_id" | "week_start" | "leads">[]) {
      (grouped[entry.location_id] ??= []).push(entry.leads);
    }
    setSparks(grouped);

    const dates = [...new Set(portfolio.map((r) => r.snapshot_date).filter(Boolean))] as string[];
    if (dates.length) {
      const { data: flagRows } = await supabase
        .from("flags")
        .select("*")
        .in("snapshot_date", dates);
      const byLoc: Record<string, FlagRow[]> = {};
      for (const flag of (flagRows ?? []) as FlagRow[]) {
        (byLoc[flag.location_id] ??= []).push(flag);
      }
      setFlagsByLoc(byLoc);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const visible = useMemo(() => {
    if (!rows) return [];
    const email = session?.user?.email?.toLowerCase();
    const needle = search.trim().toLowerCase();
    const filtered = rows.filter((row) => {
      if (row.is_parent && !includeSsp) return false;
      if (view === "mine" && email && !row.is_parent
          && (row.am_email ?? "").toLowerCase() !== email) return false;
      if (needle && !row.name.toLowerCase().includes(needle)
          && !row.slug.toLowerCase().includes(needle)) return false;
      if (vertical && (row.vertical ?? "") !== vertical) return false;
      if (stateFilter && row.state !== stateFilter) return false;
      if (flagFilter && !(flagsByLoc[row.location_id] ?? []).some((f) => f.code === flagFilter)) return false;
      return true;
    });
    return sortMode === "mrr" ? sortByMrrAtRisk(filtered) : sortByAttention(filtered);
  }, [rows, view, includeSsp, search, vertical, stateFilter, flagFilter, sortMode, session, flagsByLoc]);

  const verticals = useMemo(
    () => [...new Set((rows ?? []).map((r) => r.vertical).filter(Boolean))].sort() as string[],
    [rows]);
  const flagCodes = useMemo(
    () => [...new Set(Object.values(flagsByLoc).flat().map((f) => f.code))].sort(),
    [flagsByLoc]);

  const attentionRows = visible.filter((r) => r.state === "attention" && !r.is_parent);
  const mrrInAttention = attentionRows.reduce((sum, r) => sum + (r.mrr ?? 0), 0);
  const attentionNoMrr = attentionRows.filter((r) => r.mrr === null).length;

  useEffect(() => {
    setSelected((s) => Math.min(s, Math.max(0, visible.length - 1)));
  }, [visible.length]);

  const ackSelected = useCallback(async () => {
    const row = visible[selected];
    const email = session?.user?.email;
    if (!row || !email) return;
    const flags = (flagsByLoc[row.location_id] ?? [])
      .filter((f) => f.severity !== "info")
      .sort((a, b) => (a.severity === "red" ? 0 : 1) - (b.severity === "red" ? 0 : 1));
    const top = flags[0];
    if (!top) {
      setAckNotice(`${row.name}: nothing to acknowledge`);
      return;
    }
    const err = await acknowledgeFlag({
      locationId: row.location_id, code: top.code, ackedBy: email, snoozeDays: 7,
    });
    setAckNotice(err ? `Ack failed: ${err}` : `Acknowledged ${top.code} on ${row.name} (7d)`);
    if (!err) void load();
  }, [visible, selected, session, flagsByLoc, load]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT") {
        if (event.key === "Escape") target.blur();
        return;
      }
      if (event.key === "j") setSelected((s) => Math.min(s + 1, visible.length - 1));
      else if (event.key === "k") setSelected((s) => Math.max(s - 1, 0));
      else if (event.key === "Enter" && visible[selected]) {
        navigate(`/account/${visible[selected].location_id}`);
      } else if (event.key === "a") void ackSelected();
      else if (event.key === "/") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, selected, navigate, ackSelected]);

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null || value === "") next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  }

  function toggleColumn(key: ExpandedKey) {
    const next = new Set(columns);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setColumns(next);
    localStorage.setItem(COLUMNS_KEY, JSON.stringify([...next]));
  }

  function exportCsv() {
    const headers = ["account", "am", "state", "red", "amber", "acked", "new_flags",
      "leads_7d", "delta_pct", "peer_delta_pct", "uncontacted_24h", "convos_waiting",
      "top_action", ...[...columns]];
    const lines = [headers.join(",")];
    for (const row of visible) {
      const extended: Record<ExpandedKey, unknown> = {
        stale: row.opps_stale, past_due: row.invoices_past_due_amount,
        publish: row.days_since_last_publish, client_touch: row.client_last_touch_days,
        unassigned: row.leads_unassigned_7d, noshow: row.noshow_rate_28d,
        lead_opp: row.lead_to_opp_28d_pct, win_rate: row.win_rate_90d,
        social: row.social_accounts_expired, mrr: row.mrr, contract: row.contract_end,
        quality: dataQuality(row), snapshot: row.snapshot_date,
      };
      lines.push([
        row.name, row.am_email, row.state, row.red, row.amber, row.acked,
        (row.flags_new ?? []).join("; "),
        row.leads_new_7d, row.leads_delta_pct, row.peer_median_delta_pct,
        row.leads_uncontacted_24h, row.convos_waiting, row.top_action,
        ...[...columns].map((key) => extended[key]),
      ].map(csvEscape).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `account-health-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function cell(row: PortfolioRow, key: ExpandedKey) {
    const noData = row.state === "no_data";
    switch (key) {
      case "stale":
        return noData || row.opps_stale === null ? "—"
          : row.opps_stale > 0
            ? `${row.opps_stale} (${fmtMoney(row.opps_stale_value)})` : "0";
      case "past_due":
        return noData || row.invoices_past_due === null ? "—"
          : row.invoices_past_due > 0 ? fmtMoney(row.invoices_past_due_amount) : "$0";
      case "publish": return noData ? "—" : fmtNum(row.days_since_last_publish);
      case "client_touch": return noData ? "—" : fmtNum(row.client_last_touch_days);
      case "unassigned": return noData ? "—" : fmtNum(row.leads_unassigned_7d);
      case "noshow": return noData || row.noshow_rate_28d === null ? "—" : `${row.noshow_rate_28d.toFixed(0)}%`;
      case "lead_opp": return noData || row.lead_to_opp_28d_pct === null ? "—" : `${row.lead_to_opp_28d_pct.toFixed(0)}%`;
      case "win_rate": return noData || row.win_rate_90d === null ? "—" : `${row.win_rate_90d.toFixed(0)}%`;
      case "social": return noData || row.social_accounts_expired === null ? "—" : fmtNum(row.social_accounts_expired);
      case "mrr": return row.mrr === null ? "not set" : fmtMoney(row.mrr);
      case "contract": return row.contract_end ? fmtDate(row.contract_end) : "not set";
      case "quality": return <QualityBadge quality={dataQuality(row)} />;
      case "snapshot": return fmtDate(row.snapshot_date);
    }
  }

  function renderTable(sectionRows: PortfolioRow[]) {
    return (
      <div className="hidden overflow-x-auto rounded border border-grid bg-surface md:block">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-grid text-left text-xxs uppercase tracking-wide text-muted">
              <th className="px-2 py-1.5 font-medium">Account</th>
              <th className="px-2 py-1.5 font-medium">AM</th>
              <th className="px-2 py-1.5 font-medium">State / flags</th>
              <th className="px-2 py-1.5 font-medium">New</th>
              <th className="px-2 py-1.5 font-medium">Leads 7d</th>
              <th className="px-2 py-1.5 font-medium">8 wk</th>
              <th className="px-2 py-1.5 text-right font-medium">Uncont. &gt;24h</th>
              <th className="px-2 py-1.5 text-right font-medium">Waiting</th>
              {EXPANDED_COLUMNS.filter((c) => columns.has(c.key)).map((c) => (
                <th key={c.key} className="px-2 py-1.5 text-right font-medium">{c.label}</th>
              ))}
              <th className="px-2 py-1.5 font-medium">Top action</th>
            </tr>
          </thead>
          <tbody>
            {sectionRows.map((row) => {
              const index = visible.indexOf(row);
              const isSelected = index === selected;
              const noData = row.state === "no_data";
              return (
                <tr
                  key={row.location_id}
                  className={`cursor-pointer border-b border-grid/60 last:border-0 ${
                    isSelected ? "bg-series/5 outline outline-1 outline-series/40" : "hover:bg-plane"}`}
                  onClick={() => navigate(`/account/${row.location_id}`)}
                >
                  <td className="px-2 py-1.5">
                    <Link
                      to={`/account/${row.location_id}`}
                      className="font-medium text-series underline decoration-series/30 underline-offset-2"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {row.name}
                    </Link>
                    {row.is_parent ? <span className="ml-1 text-xxs text-muted">(SSP)</span> : null}
                  </td>
                  <td className="px-2 py-1.5 text-xs text-ink-2">{row.am_email?.split("@")[0] ?? "—"}</td>
                  <td className="px-2 py-1.5">
                    <div className="flex items-center gap-1">
                      <StateIcon state={row.state} />
                      <SeverityChip severity="red" count={row.red} />
                      <SeverityChip severity="amber" count={row.amber} />
                      {row.acked > 0 ? <span className="text-xxs text-muted">acked {row.acked}</span> : null}
                    </div>
                  </td>
                  <td className="px-2 py-1.5">
                    {(row.flags_new?.length ?? 0) > 0 ? (
                      <span className="rounded bg-series/10 px-1 py-0.5 text-xxs text-series"
                            title={(row.flags_new ?? []).join(", ")}>
                        +{row.flags_new!.length}
                      </span>
                    ) : (
                      <span className="text-xxs text-muted">—</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5">
                    {noData ? (
                      <span className="text-muted">
                        no data{row.token_status !== "ok" ? " (token)" : ""}
                      </span>
                    ) : row.leads_new_7d === null ? (
                      <span className="text-muted">{UNKNOWN}</span>
                    ) : (
                      <div>
                        <span className="font-medium tabular">{fmtNum(row.leads_new_7d)}</span>{" "}
                        {row.leads_delta_pct !== null ? (
                          <span className={`text-xxs ${row.leads_delta_pct <= -40 ? "text-status-critical" : "text-ink-2"}`}>
                            {fmtPct(row.leads_delta_pct)}
                          </span>
                        ) : row.leads_trailing_avg === null ? (
                          <span className="text-xxs text-muted">baseline building ({row.trailing_n ?? 0}/4)</span>
                        ) : null}
                        {row.peer_median_delta_pct !== null ? (
                          <div className="text-xxs text-muted">peers {fmtPct(row.peer_median_delta_pct)}</div>
                        ) : null}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-1.5"><Sparkline values={sparks[row.location_id] ?? []} /></td>
                  <td className="px-2 py-1.5 text-right tabular">{noData ? "—" : fmtNum(row.leads_uncontacted_24h)}</td>
                  <td className="px-2 py-1.5 text-right tabular">
                    {noData || row.convos_waiting === null ? "—"
                      : row.convos_waiting > 0
                        ? <>{row.convos_waiting}<span className="text-xxs text-muted"> (max {fmtHours(row.convos_waiting_max_hours)})</span></>
                        : "0"}
                  </td>
                  {EXPANDED_COLUMNS.filter((c) => columns.has(c.key)).map((c) => (
                    <td key={c.key} className="px-2 py-1.5 text-right tabular text-xs">{cell(row, c.key)}</td>
                  ))}
                  <td className="max-w-64 truncate px-2 py-1.5 text-xxs text-ink-2" title={row.top_action ?? undefined}>
                    {row.top_action ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  function renderCards(sectionRows: PortfolioRow[]) {
    return (
      <div className="grid gap-2 md:hidden">
        {sectionRows.map((row) => (
          <Link key={row.location_id} to={`/account/${row.location_id}`}
                className="block rounded border border-grid bg-surface p-3">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-sm font-medium">{row.name}</span>
              <StateIcon state={row.state} withLabel />
            </div>
            <div className="mb-1 flex gap-1">
              <SeverityChip severity="red" count={row.red} />
              <SeverityChip severity="amber" count={row.amber} />
            </div>
            {row.state === "no_data" ? (
              <div className="text-xs text-muted">
                no data{row.token_status !== "ok" ? " — token invalid or missing" : ""}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs text-ink-2">
                <span>Leads 7d: <span className="tabular text-ink">{fmtNum(row.leads_new_7d)}</span>{" "}
                  {row.leads_delta_pct !== null ? fmtPct(row.leads_delta_pct) : ""}</span>
                <span>Waiting: <span className="tabular text-ink">{fmtNum(row.convos_waiting)}</span></span>
                <span>Uncontacted: <span className="tabular text-ink">{fmtNum(row.leads_uncontacted_24h)}</span></span>
                <span>Stale: <span className="tabular text-ink">{fmtNum(row.opps_stale)}</span></span>
              </div>
            )}
            {row.top_action ? <div className="mt-1 text-xxs text-ink-2">{row.top_action}</div> : null}
          </Link>
        ))}
      </div>
    );
  }

  const sections: { state: PortfolioState; title: string; empty: string }[] = [
    { state: "attention", title: "Needs attention", empty: "No accounts need attention." },
    { state: "steady", title: "Steady", empty: "No steady accounts in this view." },
    { state: "no_data", title: "No data", empty: "Every account in this view has fresh data." },
  ];

  const amGroups = useMemo(() => {
    if (!groupByAm) return null;
    const groups = new Map<string, PortfolioRow[]>();
    for (const row of visible) {
      const am = row.am_email ?? "(no AM)";
      if (!groups.has(am)) groups.set(am, []);
      groups.get(am)!.push(row);
    }
    return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [visible, groupByAm]);

  return (
    <div className="mx-auto max-w-[1500px] px-4 py-4">
      {/* filters */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex rounded border border-grid bg-surface text-xs">
          <button onClick={() => setParam("view", null)}
                  className={`px-2.5 py-1 ${view === "mine" ? "bg-ink font-medium text-white" : "text-ink-2"}`}>
            My accounts
          </button>
          <button onClick={() => setParam("view", "all")}
                  className={`px-2.5 py-1 ${view === "all" ? "bg-ink font-medium text-white" : "text-ink-2"}`}>
            All
          </button>
        </div>
        <label className="flex items-center gap-1.5 text-xs text-ink-2">
          <input type="checkbox" checked={includeSsp}
                 onChange={(e) => setParam("ssp", e.target.checked ? "1" : null)} />
          Include SSP
        </label>
        <input
          ref={searchRef}
          value={search}
          onChange={(e) => setParam("q", e.target.value || null)}
          placeholder="Search accounts ( / )"
          className="w-44 rounded border border-grid bg-surface px-2 py-1 text-xs"
        />
        <select value={vertical} onChange={(e) => setParam("vertical", e.target.value || null)}
                className="rounded border border-grid bg-surface px-1.5 py-1 text-xs text-ink-2">
          <option value="">All verticals</option>
          {verticals.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
        <select value={stateFilter} onChange={(e) => setParam("state", e.target.value || null)}
                className="rounded border border-grid bg-surface px-1.5 py-1 text-xs text-ink-2">
          <option value="">All states</option>
          <option value="attention">Needs attention</option>
          <option value="steady">Steady</option>
          <option value="no_data">No data</option>
        </select>
        <select value={flagFilter} onChange={(e) => setParam("flag", e.target.value || null)}
                className="rounded border border-grid bg-surface px-1.5 py-1 text-xs text-ink-2">
          <option value="">All flags</option>
          {flagCodes.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={sortMode} onChange={(e) => setParam("sort", e.target.value === "mrr" ? "mrr" : null)}
                className="rounded border border-grid bg-surface px-1.5 py-1 text-xs text-ink-2">
          <option value="attention">Sort: attention</option>
          <option value="mrr">Sort: MRR at risk</option>
        </select>
        <label className="flex items-center gap-1.5 text-xs text-ink-2">
          <input type="checkbox" checked={groupByAm}
                 onChange={(e) => setParam("group", e.target.checked ? "am" : null)} />
          Group by AM
        </label>
        <div className="relative">
          <button onClick={() => setChooserOpen((v) => !v)}
                  className="rounded border border-grid bg-surface px-2 py-1 text-xs text-ink-2">
            Columns ▾
          </button>
          {chooserOpen ? (
            <div className="absolute z-10 mt-1 w-44 rounded border border-grid bg-surface p-2 shadow-sm">
              {EXPANDED_COLUMNS.map((c) => (
                <label key={c.key} className="flex items-center gap-1.5 py-0.5 text-xs text-ink-2">
                  <input type="checkbox" checked={columns.has(c.key)} onChange={() => toggleColumn(c.key)} />
                  {c.label}
                </label>
              ))}
            </div>
          ) : null}
        </div>
        <button onClick={exportCsv} className="rounded border border-grid bg-surface px-2 py-1 text-xs text-ink-2">
          Export CSV
        </button>
        <span className="ml-auto text-xxs text-muted">j/k select · Enter open · a acknowledge · / search</span>
      </div>

      {/* MRR-in-attention header tile */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="rounded border border-grid bg-surface px-3 py-1.5">
          <span className="text-xxs uppercase tracking-wide text-muted">MRR in attention </span>
          <span className="text-sm font-semibold">{fmtMoney(mrrInAttention)}</span>
          {attentionNoMrr > 0 ? (
            <span className="ml-1 text-xxs text-muted">({attentionNoMrr} accounts without MRR set)</span>
          ) : null}
        </div>
        {ackNotice ? <span className="text-xs text-ink-2">{ackNotice}</span> : null}
      </div>

      {error ? <EmptyState>Could not load the portfolio: {error}</EmptyState> : null}
      {!rows && !error ? <Skeleton rows={6} /> : null}

      {rows && amGroups
        ? amGroups.map(([am, groupRows]) => {
            const attention = groupRows.filter((r) => r.state === "attention");
            const attentionMrr = attention.reduce((sum, r) => sum + (r.mrr ?? 0), 0);
            return (
              <Section key={am}
                       title={`${am.split("@")[0]}: ${groupRows.length} accounts, ${attention.length} attention, ${fmtMoney(attentionMrr)} MRR in attention`}>
                {renderTable(groupRows)}
                {renderCards(groupRows)}
              </Section>
            );
          })
        : null}

      {rows && !amGroups
        ? sections.map(({ state, title, empty }) => {
            const sectionRows = visible.filter((row) => row.state === state);
            return (
              <Section key={state} title={`${title} (${sectionRows.length})`}>
                {sectionRows.length === 0 ? <EmptyState>{empty}</EmptyState> : (
                  <>
                    {renderTable(sectionRows)}
                    {renderCards(sectionRows)}
                  </>
                )}
              </Section>
            );
          })
        : null}
    </div>
  );
}
