// Portfolio.tsx — the home page: every account in one big filterable, sortable
// table, grouped into "Needs attention" / "Steady" / "No data" sections.
//
// Key ideas needed to read this file:
// - Filter state lives in the URL query string (?view=all&q=pool...), read and
//   written via react-router's useSearchParams. That makes every filter combo
//   a shareable/bookmarkable link, and Back/Forward work over filter changes.
// - useMemo caches derived data ("memoization"): the filtered+sorted row list
//   is recomputed only when one of its listed dependencies changes, not on
//   every render.
// - A global window "keydown" listener implements keyboard shortcuts:
//   j/k move the selection, Enter opens the account, a acknowledges the top
//   flag, / focuses search.
// - Column choices persist in localStorage; CSV export builds the file
//   in-browser from the currently visible rows.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { Sparkline } from "../components/Sparkline";
import { EmptyState, QualityBadge, Section, SeverityChip, Skeleton, StateIcon } from "../components/ui";
import type { FlagRow, LeadHistoryRow, PortfolioRow, PortfolioState } from "../lib/database.types";
import { dataQuality, fmtDate, fmtHours, fmtMinutes, fmtMoney, fmtNum, fmtPct, UNKNOWN } from "../lib/format";
import { type Band, bandOf, type Grade, gradeAccount, humanizeCode } from "../lib/grade";
import { supabase } from "../lib/supabase";
import { useSession } from "../lib/useSession";
import { acknowledgeFlag } from "../lib/writes";

const REFRESH_MS = 15 * 60 * 1000; // auto-reload the data every 15 minutes
const SPARK_WEEKS = 8; // weeks of history behind each sparkline
const COLUMNS_KEY = "portfolio.columns"; // localStorage key for chosen columns

// Expanded columns behind the column chooser (spec v3 9.3), persisted locally.
// The always-on columns (account, state, leads...) are hard-coded in the table;
// these are the optional extras each user toggles for themselves.
const EXPANDED_COLUMNS = [
  { key: "am", label: "AM" },
  { key: "new_flags", label: "New flags" },
  { key: "uncontacted", label: "Uncont. >24h" },
  { key: "waiting", label: "Waiting" },
  { key: "stale", label: "Stale opps" },
  { key: "forms_silent", label: "Silent forms" },
  { key: "past_due", label: "Past due" },
  { key: "publish", label: "Since publish" },
  { key: "client_touch", label: "Client touch" },
  { key: "unassigned", label: "Unassigned" },
  { key: "noshow", label: "No-show %" },
  { key: "lead_opp", label: "Lead→opp %" },
  { key: "win_rate", label: "Win rate" },
  { key: "social", label: "Social disc." },
  { key: "contract", label: "Contract end" },
  { key: "quality", label: "Quality" },
  { key: "snapshot", label: "Snapshot" },
] as const;
// "typeof ...[number]['key']" derives the union of the key strings above, so
// adding a column to the array automatically extends this type.
type ExpandedKey = (typeof EXPANDED_COLUMNS)[number]["key"];

// Read the saved column set from localStorage. try/catch guards against
// corrupt JSON or storage being blocked; either way we fall back to defaults.
function loadColumns(): Set<ExpandedKey> {
  try {
    const raw = localStorage.getItem(COLUMNS_KEY);
    if (raw) return new Set(JSON.parse(raw) as ExpandedKey[]);
  } catch {
    /* fall through to default */
  }
  return new Set<ExpandedKey>(["stale", "quality"]);
}

// Sort comparator #1 (the default): grade worst-first. Lower score = worse
// account = higher on the page; no-data rows (score -1) sink to the bottom
// so real problems outrank missing tokens. Ties break on attention_score,
// then biggest lead drop. [...rows] copies before sorting because Array.sort
// mutates and React state must not be mutated in place.
function sortByGrade(rows: PortfolioRow[], grades: Record<string, Grade>): PortfolioRow[] {
  return [...rows].sort((a, b) => {
    const ga = grades[a.location_id]?.score ?? 100;
    const gb = grades[b.location_id]?.score ?? 100;
    // no-data (-1) is "unknown", not "worst" — park it after F-grades
    const sa = ga < 0 ? 101 : ga;
    const sb = gb < 0 ? 101 : gb;
    if (sa !== sb) return sa - sb;
    if (b.attention_score !== a.attention_score) return b.attention_score - a.attention_score;
    const da = a.leads_delta_pct ?? Number.POSITIVE_INFINITY;
    const db = b.leads_delta_pct ?? Number.POSITIVE_INFINITY;
    return da - db;
  });
}

// Grade letter styling: color reinforces the letter, never replaces it.
function gradeClasses(letter: Grade["letter"]): string {
  switch (letter) {
    case "A": case "B": return "bg-status-good/10 text-status-good-text";
    case "C": return "bg-status-warning/20 text-ink";
    case "D": case "F": return "bg-status-critical/10 text-status-critical";
    default: return "bg-plane text-muted";
  }
}

const BAND_META: { band: Band; icon: string; label: string }[] = [
  { band: "attention", icon: "●", label: "Needs attention" },
  { band: "watch", icon: "◐", label: "Watch" },
  { band: "healthy", icon: "✓", label: "Healthy" },
  { band: "no_data", icon: "○", label: "No data" },
];

// Sort comparator #2: "MRR at risk" — accounts needing attention first (that's
// the aRisk/bRisk 0-vs-1 trick), then by revenue descending, so the most
// valuable troubled accounts top the list. Unknown MRR (-1) sorts below $0.
function sortByMrrAtRisk(rows: PortfolioRow[]): PortfolioRow[] {
  return [...rows].sort((a, b) => {
    const aRisk = a.attention_score > 0 ? 0 : 1;
    const bRisk = b.attention_score > 0 ? 0 : 1;
    if (aRisk !== bRisk) return aRisk - bRisk;
    return (b.mrr ?? -1) - (a.mrr ?? -1);
  });
}

// CSV escaping per RFC 4180: a field containing a quote, comma, or newline is
// wrapped in double quotes, with embedded quotes doubled ("" inside).
// Everything else passes through untouched; null/undefined become empty cells.
function csvEscape(value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export default function Portfolio() {
  const { session } = useSession();
  const navigate = useNavigate();
  // params is the live URL query string; setParams rewrites it (see setParam).
  const [params, setParams] = useSearchParams();
  // Server data: null = still loading (vs [] = loaded but empty).
  const [rows, setRows] = useState<PortfolioRow[] | null>(null);
  const [sparks, setSparks] = useState<Record<string, (number | null)[]>>({});
  const [flagsByLoc, setFlagsByLoc] = useState<Record<string, FlagRow[]>>({});
  // Latest note date per account, for the "days since last note" nudge.
  const [lastNoteByLoc, setLastNoteByLoc] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  // UI-only state: keyboard-selected row index, chosen columns, open menus.
  const [selected, setSelected] = useState(0);
  const [columns, setColumns] = useState<Set<ExpandedKey>>(loadColumns);
  const [chooserOpen, setChooserOpen] = useState(false);
  const [ackNotice, setAckNotice] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  // Decode each filter from the query string, with a safe default when the
  // param is absent or has an unexpected value.
  const view = params.get("view") === "all" ? "all" : "mine";
  const includeSsp = params.get("ssp") === "1";
  const search = params.get("q") ?? "";
  const vertical = params.get("vertical") ?? "";
  const stateFilter = params.get("state") ?? "";
  const flagFilter = params.get("flag") ?? "";
  const sortMode = params.get("sort") === "mrr" ? "mrr" : "grade";
  const groupByAm = params.get("group") === "am";
  const band = (params.get("band") ?? "") as Band | "";
  const layout = params.get("layout") === "wall" ? "wall" : "table";

  // Load everything the page needs, in three queries:
  // 1) the v_portfolio view (one row per account, pre-joined server-side),
  // 2) weekly lead history for the sparklines,
  // 3) current flags, for the flag filter and the "a" acknowledge shortcut.
  // useCallback keeps the function identity stable so the effect below doesn't
  // re-subscribe its interval on every render.
  const load = useCallback(async () => {
    const { data, error: err } = await supabase.from("v_portfolio").select("*");
    if (err) {
      setError(err.message);
      return;
    }
    const portfolio = (data ?? []) as PortfolioRow[];
    setRows(portfolio);
    setError(null);

    // Sparkline data: last 8 weeks of weekly lead counts, grouped by account.
    // 86400 = seconds per day; the slice(0, 10) keeps just "YYYY-MM-DD".
    const since = new Date(Date.now() - SPARK_WEEKS * 7 * 86400 * 1000)
      .toISOString().slice(0, 10);
    const { data: history } = await supabase
      .from("lead_history")
      .select("location_id,week_start,leads")
      .gte("week_start", since)
      .order("week_start", { ascending: true });
    const grouped: Record<string, (number | null)[]> = {};
    for (const entry of (history ?? []) as Pick<LeadHistoryRow, "location_id" | "week_start" | "leads">[]) {
      // "??=" assigns the array only when the key is still missing.
      (grouped[entry.location_id] ??= []).push(entry.leads);
    }
    setSparks(grouped);

    // Newest note per account (rows arrive newest-first, so the first one
    // seen per location wins) — powers the "days since last note" column.
    const { data: noteRows } = await supabase
      .from("account_notes")
      .select("location_id,created_at")
      .order("created_at", { ascending: false })
      .limit(500);
    const newest: Record<string, string> = {};
    for (const note of (noteRows ?? []) as { location_id: string; created_at: string }[]) {
      newest[note.location_id] ??= note.created_at;
    }
    setLastNoteByLoc(newest);

    // Flags for each account's *latest* snapshot date (dates differ between
    // accounts, so collect the distinct set and query them all at once).
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

  // Initial load, plus a 15-minute refresh interval (cleared on unmount).
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  // One grade per account, memoized off the raw rows.
  const grades = useMemo(() => {
    const out: Record<string, Grade> = {};
    for (const row of rows ?? []) out[row.location_id] = gradeAccount(row);
    return out;
  }, [rows]);

  // The heart of the page, in two stages: baseVisible applies every filter
  // EXCEPT the triage band (so the band chips can show counts for the whole
  // current view), then `visible` applies the band chip on top and sorts.
  const baseVisible = useMemo(() => {
    if (!rows) return [];
    const email = session?.user?.email?.toLowerCase();
    const needle = search.trim().toLowerCase();
    return rows.filter((row) => {
      // Each check eliminates; a row must survive all of them to show.
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
  }, [rows, view, includeSsp, search, vertical, stateFilter, flagFilter, session, flagsByLoc]);

  const visible = useMemo(() => {
    const banded = band ? baseVisible.filter((row) => bandOf(row) === band) : baseVisible;
    return sortMode === "mrr" ? sortByMrrAtRisk(banded) : sortByGrade(banded, grades);
  }, [baseVisible, band, sortMode, grades]);

  // Chip counts + the "Data as of" stamp for the triage header.
  const bandCounts = useMemo(() => {
    const counts: Record<Band, number> = { attention: 0, watch: 0, healthy: 0, no_data: 0 };
    for (const row of baseVisible) counts[bandOf(row)] += 1;
    return counts;
  }, [baseVisible]);
  const dataAsOf = useMemo(() => {
    const dates = baseVisible.map((r) => r.snapshot_date).filter(Boolean) as string[];
    return dates.length ? dates.sort()[dates.length - 1] : null;
  }, [baseVisible]);

  // "Since yesterday" strip: accounts whose latest snapshot raised new flags.
  // (flags_new compares against one week prior, so the honest label is
  // "new this week".) Sorted worst-grade-first, capped for readability.
  const overnight = useMemo(() =>
    sortByGrade(baseVisible.filter((r) => (r.flags_new?.length ?? 0) > 0), grades)
      .map((r) => ({
        row: r,
        codes: (r.flags_new ?? []).map(humanizeCode).join(", "),
      })),
  [baseVisible, grades]);

  // Dropdown option lists derived from the data itself (deduped via Set).
  const verticals = useMemo(
    () => [...new Set((rows ?? []).map((r) => r.vertical).filter(Boolean))].sort() as string[],
    [rows]);
  const flagCodes = useMemo(
    () => [...new Set(Object.values(flagsByLoc).flat().map((f) => f.code))].sort(),
    [flagsByLoc]);

  // Header numbers: total monthly revenue currently sitting in accounts
  // that need attention (excluding SSP's own parent account).
  const attentionRows = baseVisible.filter((r) => r.state === "attention" && !r.is_parent);
  const mrrInAttention = attentionRows.reduce((sum, r) => sum + (r.mrr ?? 0), 0);
  const attentionNoMrr = attentionRows.filter((r) => r.mrr === null).length;

  // Keep the keyboard selection inside bounds when filtering shrinks the list.
  useEffect(() => {
    setSelected((s) => Math.min(s, Math.max(0, visible.length - 1)));
  }, [visible.length]);

  // The "a" shortcut: acknowledge the most severe (red before amber, info
  // excluded) flag on the selected row, with the default 7-day snooze.
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

  // Global keyboard handler. Listening on window means it works wherever
  // focus is — except inside form fields, where typing "j" must stay typing,
  // so those bail out early (Escape blurs the field instead).
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
        // preventDefault stops Firefox's quick-find from stealing "/".
        event.preventDefault();
        searchRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, selected, navigate, ackSelected]);

  // Write one filter into the URL. Empty/null deletes the param so default
  // states produce clean URLs; replace:true avoids flooding browser history
  // with an entry per keystroke in the search box.
  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null || value === "") next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  }

  // Toggle a column on/off and persist the choice. A *new* Set is built first
  // because React only notices state changes when the object identity changes.
  function toggleColumn(key: ExpandedKey) {
    const next = new Set(columns);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setColumns(next);
    localStorage.setItem(COLUMNS_KEY, JSON.stringify([...next]));
  }

  // CSV export, fully in the browser: build the text, wrap it in a Blob,
  // point a temporary object URL at it, and "click" an invisible <a download>
  // to trigger the save dialog. Exports exactly what's visible — current
  // filters, current sort, currently chosen expanded columns.
  function exportCsv() {
    const headers = ["account", "am", "grade", "state", "red", "amber", "acked", "new_flags",
      "leads_7d", "delta_pct", "peer_delta_pct", "speed_median_min", "missed_calls",
      "mrr", "forms_silent", "top_action", ...[...columns]];
    const lines = [headers.join(",")];
    for (const row of visible) {
      // Raw values per expanded column (unformatted, spreadsheet-friendly).
      const extended: Record<ExpandedKey, unknown> = {
        am: row.am_email, new_flags: (row.flags_new ?? []).join("; "),
        uncontacted: row.leads_uncontacted_24h, waiting: row.convos_waiting,
        stale: row.opps_stale, forms_silent: row.forms_silent_ct,
        past_due: row.invoices_past_due_amount,
        publish: row.days_since_last_publish, client_touch: row.client_last_touch_days,
        unassigned: row.leads_unassigned_7d, noshow: row.noshow_rate_28d,
        lead_opp: row.lead_to_opp_28d_pct, win_rate: row.win_rate_90d,
        social: row.social_accounts_expired, contract: row.contract_end,
        quality: dataQuality(row), snapshot: row.snapshot_date,
      };
      lines.push([
        row.name, row.am_email, grades[row.location_id]?.letter ?? "",
        row.state, row.red, row.amber, row.acked,
        (row.flags_new ?? []).join("; "),
        row.leads_new_7d, row.leads_delta_pct, row.peer_median_delta_pct,
        row.speed_to_lead_median_min, row.calls_missed_7d,
        row.mrr, row.forms_silent_ct, row.top_action,
        ...[...columns].map((key) => extended[key]),
      ].map(csvEscape).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `account-health-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url); // free the blob's memory once the click is done
  }

  // Days since an ISO timestamp, or null. Used by the "Last note" nudge.
  function daysAgo(iso: string | undefined): number | null {
    if (!iso) return null;
    return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  }

  // Render one expanded-column cell. Shared convention: "—" for no-data rows,
  // "not set" for missing manual config (contract) — never a fake 0.
  function cell(row: PortfolioRow, key: ExpandedKey) {
    const noData = row.state === "no_data";
    switch (key) {
      case "am": return row.am_email?.split("@")[0] ?? "—";
      case "new_flags": return (row.flags_new?.length ?? 0) > 0 ? `+${row.flags_new!.length}` : "—";
      case "uncontacted": return noData ? "—" : fmtNum(row.leads_uncontacted_24h);
      case "waiting":
        return noData || row.convos_waiting === null ? "—"
          : row.convos_waiting > 0
            ? `${row.convos_waiting} (max ${fmtHours(row.convos_waiting_max_hours)})` : "0";
      case "stale":
        return noData || row.opps_stale === null ? "—"
          : row.opps_stale > 0
            ? `${row.opps_stale} (${fmtMoney(row.opps_stale_value)})` : "0";
      case "forms_silent": return noData || row.forms_silent_ct === null ? "—" : fmtNum(row.forms_silent_ct);
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
      case "contract": return row.contract_end ? fmtDate(row.contract_end) : "not set";
      case "quality": return <QualityBadge quality={dataQuality(row)} />;
      case "snapshot": return fmtDate(row.snapshot_date);
    }
  }

  // The grade badge, with its full deduction list on hover — the grade must
  // never be a number nobody can explain.
  function gradeBadge(row: PortfolioRow) {
    const grade = grades[row.location_id];
    if (!grade) return null;
    return (
      <span title={`Score ${grade.score < 0 ? "—" : grade.score}/100\n${grade.reasons.join("\n")}`}
            className={`inline-block min-w-[1.4rem] rounded px-1.5 py-0.5 text-center text-xs font-semibold ${gradeClasses(grade.letter)}`}>
        {grade.letter}
      </span>
    );
  }

  // Desktop layout: the full table ("hidden ... md:block" = only at >=768px;
  // renderCards below is its mobile twin). Plain function, not a component —
  // it can read all the surrounding state directly.
  function renderTable(sectionRows: PortfolioRow[]) {
    return (
      <div className="hidden overflow-x-auto rounded border border-grid bg-surface md:block">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-grid text-left text-xxs uppercase tracking-wide text-muted">
              <th className="px-2 py-1.5 font-medium">Account</th>
              <th className="px-2 py-1.5 font-medium">Grade</th>
              <th className="px-2 py-1.5 font-medium">State / flags</th>
              <th className="px-2 py-1.5 font-medium">Leads 7d</th>
              <th className="px-2 py-1.5 font-medium">8 wk</th>
              <th className="px-2 py-1.5 text-right font-medium">Speed</th>
              <th className="px-2 py-1.5 text-right font-medium">Missed</th>
              <th className="px-2 py-1.5 text-right font-medium">MRR</th>
              {EXPANDED_COLUMNS.filter((c) => columns.has(c.key)).map((c) => (
                <th key={c.key} className="px-2 py-1.5 text-right font-medium">{c.label}</th>
              ))}
              <th className="px-2 py-1.5 font-medium">Top action</th>
              <th className="px-2 py-1.5 text-right font-medium">Last note</th>
            </tr>
          </thead>
          <tbody>
            {sectionRows.map((row) => {
              // Selection is tracked as an index into the *whole* visible
              // list, so j/k walk across section boundaries seamlessly.
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
                    {/* real <Link> inside the clickable row so middle-click /
                        cmd-click open-in-new-tab still works; stopPropagation
                        keeps the row's own onClick from double-navigating */}
                    <Link
                      to={`/account/${row.location_id}`}
                      className="font-medium text-series underline decoration-series/30 underline-offset-2"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {row.name}
                    </Link>
                    {row.is_parent ? <span className="ml-1 text-xxs text-muted">(SSP)</span> : null}
                  </td>
                  <td className="px-2 py-1.5">{gradeBadge(row)}</td>
                  <td className="px-2 py-1.5">
                    <div className="flex items-center gap-1">
                      <StateIcon state={row.state} />
                      <SeverityChip severity="red" count={row.red} />
                      <SeverityChip severity="amber" count={row.amber} />
                      {row.acked > 0 ? <span className="text-xxs text-muted">acked {row.acked}</span> : null}
                      {(row.flags_new?.length ?? 0) > 0 ? (
                        <span className="rounded bg-series/10 px-1 py-0.5 text-xxs text-series"
                              title={(row.flags_new ?? []).join(", ")}>
                          +{row.flags_new!.length} new
                        </span>
                      ) : null}
                    </div>
                  </td>
                  <td className="px-2 py-1.5">
                    {/* leads cell: count + delta vs baseline, or the reason
                        no number exists (no data / unknown / still building
                        the 4-week baseline) */}
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
                            {/* arrow + signed % vs the account's own baseline */}
                            {row.leads_delta_pct > 0 ? "▲" : row.leads_delta_pct < 0 ? "▼" : "→"} {fmtPct(row.leads_delta_pct)}
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
                  <td className="px-2 py-1.5 text-right tabular"
                      title="Median minutes to first response for new leads (7d)">
                    {noData ? "—" : fmtMinutes(row.speed_to_lead_median_min)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular" title="Missed inbound calls (7d)">
                    {noData ? "—" : fmtNum(row.calls_missed_7d)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular text-xs">
                    {row.mrr === null ? <span className="text-muted">not set</span> : fmtMoney(row.mrr)}
                  </td>
                  {EXPANDED_COLUMNS.filter((c) => columns.has(c.key)).map((c) => (
                    <td key={c.key} className="px-2 py-1.5 text-right tabular text-xs">{cell(row, c.key)}</td>
                  ))}
                  <td className="max-w-64 truncate px-2 py-1.5 text-xxs text-ink-2" title={row.top_action ?? undefined}>
                    {row.top_action ?? "—"}
                  </td>
                  <td className="px-2 py-1.5 text-right text-xxs text-muted"
                      title="Days since the last account note — the quiet accountability nudge">
                    {(() => {
                      const days = daysAgo(lastNoteByLoc[row.location_id]);
                      return days === null ? "never" : days === 0 ? "today" : `${days}d`;
                    })()}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  // Mobile layout: the same rows as tappable cards ("md:hidden" — shown only
  // below the md breakpoint, where the wide table can't fit).
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

  // The tile wall: one compact tile per account, all 31 on one screen —
  // the big-monitor / office-TV view. Color follows the grade (and the
  // letter itself is always shown, so color is never the only signal);
  // the badge names the worst unacknowledged problem in plain words.
  function renderWall(sectionRows: PortfolioRow[]) {
    return (
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
        {sectionRows.map((row) => {
          const grade = grades[row.location_id];
          const flags = (flagsByLoc[row.location_id] ?? [])
            .filter((f) => f.severity !== "info")
            .sort((a, b) => (a.severity === "red" ? 0 : 1) - (b.severity === "red" ? 0 : 1));
          const worst = flags[0];
          const index = visible.indexOf(row);
          return (
            <Link key={row.location_id} to={`/account/${row.location_id}`}
                  className={`block rounded border p-2 ${
                    index === selected ? "border-series outline outline-1 outline-series/40" : "border-grid"
                  } bg-surface hover:bg-plane`}>
              <div className="mb-1 flex items-start justify-between gap-1">
                <span className="truncate text-xs font-medium" title={row.name}>{row.name}</span>
                {gradeBadge(row)}
              </div>
              <div className="flex items-center gap-1 text-xxs text-ink-2">
                <StateIcon state={row.state} />
                {row.state === "no_data" ? (
                  <span className="text-muted">{row.token_status !== "ok" ? "awaiting token" : "no data"}</span>
                ) : (
                  <>
                    <span className="tabular">{fmtNum(row.leads_new_7d)} leads</span>
                    {row.leads_delta_pct !== null ? (
                      <span className={row.leads_delta_pct <= -40 ? "text-status-critical" : ""}>
                        {row.leads_delta_pct > 0 ? "▲" : row.leads_delta_pct < 0 ? "▼" : "→"}
                        {Math.abs(row.leads_delta_pct).toFixed(0)}%
                      </span>
                    ) : null}
                  </>
                )}
              </div>
              {worst ? (
                <div className={`mt-1 truncate rounded px-1 py-0.5 text-xxs ${
                  worst.severity === "red" ? "bg-status-critical/10 text-status-critical" : "bg-status-warning/20 text-ink"
                }`} title={worst.title}>
                  {humanizeCode(worst.code)}
                </div>
              ) : grade?.letter === "A" ? (
                <div className="mt-1 text-xxs text-status-good-text">all clear</div>
              ) : null}
            </Link>
          );
        })}
      </div>
    );
  }

  // The three page sections, in display order (worst first).
  const sections: { state: PortfolioState; title: string; empty: string }[] = [
    { state: "attention", title: "Needs attention", empty: "No accounts need attention." },
    { state: "steady", title: "Steady", empty: "No steady accounts in this view." },
    { state: "no_data", title: "No data", empty: "Every account in this view has fresh data." },
  ];

  // Alternate grouping: one section per account manager instead of per state.
  // Map preserves the order rows already have; entries are then sorted by AM.
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
      {/* every control below reads from and writes to the URL via setParam */}
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
          <option value="grade">Sort: worst grade first</option>
          <option value="mrr">Sort: MRR at risk</option>
        </select>
        {/* table vs tile-wall layout toggle (the wall is the big-monitor view) */}
        <div className="flex rounded border border-grid bg-surface text-xs">
          <button onClick={() => setParam("layout", null)}
                  className={`px-2.5 py-1 ${layout === "table" ? "bg-ink font-medium text-white" : "text-ink-2"}`}>
            Table
          </button>
          <button onClick={() => setParam("layout", "wall")}
                  className={`px-2.5 py-1 ${layout === "wall" ? "bg-ink font-medium text-white" : "text-ink-2"}`}>
            Wall
          </button>
        </div>
        <label className="flex items-center gap-1.5 text-xs text-ink-2">
          <input type="checkbox" checked={groupByAm}
                 onChange={(e) => setParam("group", e.target.checked ? "am" : null)} />
          Group by AM
        </label>
        {/* column chooser dropdown (state is local, persisted to localStorage) */}
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

      {/* Triage header: the whole book in one strip. Each chip is a click-
          filter (click again to clear); counts reflect the current view's
          other filters, so "My accounts" chips show MY book's numbers. */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {BAND_META.map(({ band: b, icon, label }) => {
          const active = band === b;
          const tone = b === "attention" ? "text-status-critical"
            : b === "watch" ? "text-status-warning"
            : b === "healthy" ? "text-status-good-text" : "text-muted";
          return (
            <button key={b} onClick={() => setParam("band", active ? null : b)}
                    className={`rounded border px-2.5 py-1 text-xs ${
                      active ? "border-ink bg-ink text-white" : "border-grid bg-surface text-ink-2 hover:bg-plane"}`}>
              <span className={active ? "" : tone} aria-hidden>{icon}</span>{" "}
              {label} <span className="font-semibold tabular">{bandCounts[b]}</span>
            </button>
          );
        })}
        <div className="rounded border border-grid bg-surface px-2.5 py-1 text-xs">
          <span className="text-xxs uppercase tracking-wide text-muted">MRR at risk </span>
          <span className="font-semibold">{fmtMoney(mrrInAttention)}</span>
          {attentionNoMrr > 0 ? (
            <span className="ml-1 text-xxs text-muted">({attentionNoMrr} without MRR set)</span>
          ) : null}
        </div>
        <span className="ml-auto text-xxs text-muted">
          {dataAsOf ? `Data as of ${fmtDate(dataAsOf)}` : "No data yet"}
        </span>
      </div>

      {/* "New this week" strip — the morning briefing. flags_new compares to
          one week prior (spec), so the honest wording is per-week, not
          overnight. */}
      {overnight.length > 0 ? (
        <div className="mb-3 rounded border border-grid bg-surface px-3 py-1.5 text-xs text-ink-2">
          <span className="font-medium text-ink">
            {overnight.reduce((n, o) => n + (o.row.flags_new?.length ?? 0), 0)} new issue(s) this week:
          </span>{" "}
          {overnight.slice(0, 6).map((o, i) => (
            <span key={o.row.location_id}>
              {i > 0 ? " · " : ""}
              <Link to={`/account/${o.row.location_id}`} className="text-series underline decoration-series/30 underline-offset-2">
                {o.row.name}
              </Link>{" "}
              +{o.codes}
            </span>
          ))}
          {overnight.length > 6 ? <span className="text-muted"> · +{overnight.length - 6} more accounts</span> : null}
        </div>
      ) : null}
      {ackNotice ? <div className="mb-2 text-xs text-ink-2">{ackNotice}</div> : null}

      {error ? <EmptyState>Could not load the portfolio: {error}</EmptyState> : null}
      {!rows && !error ? <Skeleton rows={6} /> : null}

      {/* body: wall layout is one flat grade-sorted grid; otherwise either
          the group-by-AM layout or the three state sections */}
      {rows && layout === "wall"
        ? (visible.length === 0
            ? <EmptyState>No accounts match the current filters.</EmptyState>
            : renderWall(visible))
        : null}

      {rows && layout !== "wall" && amGroups
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

      {rows && layout !== "wall" && !amGroups
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
