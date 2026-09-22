// Forms.tsx: the Forms tab. Every form in every client account that uses
// MyLeadHub, grouped by client: what kind of form it is (website, Google ad,
// Facebook ad, Facebook lead ad ...), whether it's working, where it lives,
// and its form id.
//
// Which accounts: usesMlh() (lib/mlh.ts). Accounts the team marked ads only,
// not in MLH or canceled, and accounts with no client users, are left out.
// Data: the latest form_health rows per account (written nightly by the
// collector; see docs/FORM-MONITORING.md). Type, page and 30-day counts are
// filled from the first nightly run with the 2026-09-23 collector.
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { DetailTable, EmptyState, ExternalLink, Section, Skeleton } from "../components/ui";
import type { FormHealthRow, PortfolioRow } from "../lib/database.types";
import { fmtDateTime, fmtNum } from "../lib/format";
import { daysSince, FORM_STATUS, formTypeLabel, shortUrl, weeklyTestLabel } from "../lib/forms";
import { usesMlh } from "../lib/mlh";
import { supabase } from "../lib/supabase";
import { useSession } from "../lib/useSession";

type Account = Pick<PortfolioRow, "location_id" | "name" | "am_email" | "am_name" | "is_parent"
  | "mlh_status" | "mlh_note" | "client_users" | "snapshot_date">;

// Status filter choices. Default is every form, however long it has been
// quiet: all-time history is the point (GHL's API defaults to the last
// month; the collector asks for all of it).
const STATUS_FILTERS: { value: string; label: string; statuses: string[] }[] = [
  { value: "", label: "All statuses", statuses: [] },
  { value: "working", label: "Working", statuses: ["active"] },
  { value: "quiet", label: "Went quiet (under 30 days)", statuses: ["silent"] },
  { value: "old", label: "Quiet 30+ days", statuses: ["dormant"] },
  { value: "never", label: "Never used", statuses: ["no_leads", "new"] },
];
const PAGE = 1000;   // PostgREST returns at most 1,000 rows per request

async function loadFormRows(ids: string[], dates: string[]): Promise<FormHealthRow[]> {
  const out: FormHealthRow[] = [];
  for (let from = 0; ; from += PAGE) {
    const { data, error } = await supabase.from("form_health").select("*")
      .in("location_id", ids).in("snapshot_date", dates)
      .order("id").range(from, from + PAGE - 1);
    if (error) throw error;
    out.push(...((data ?? []) as FormHealthRow[]));
    if (!data || data.length < PAGE) return out;
  }
}

export default function Forms() {
  const { session } = useSession();
  const [params, setParams] = useSearchParams();
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [hiddenCount, setHiddenCount] = useState(0);
  const [forms, setForms] = useState<FormHealthRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Everyone lands on the whole book; "My accounts" narrows to your own
  // (?view=mine). Old ?view=all links keep working.
  const view = params.get("view") === "mine" ? "mine" : "all";
  const includeSsp = params.get("ssp") === "1";
  const statusFilter = STATUS_FILTERS.find((s) => s.value === (params.get("status") ?? "")) ?? STATUS_FILTERS[0];
  const type = params.get("type") ?? "";
  const search = params.get("q") ?? "";

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null || value === "") next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  }

  useEffect(() => {
    (async () => {
      const { data, error: err } = await supabase.from("v_portfolio")
        .select("location_id,name,am_email,am_name,is_parent,mlh_status,mlh_note,client_users,snapshot_date");
      if (err) { setError(err.message); return; }
      const all = (data ?? []) as Account[];
      const kept = all.filter((a) => usesMlh(a) && a.snapshot_date);
      setHiddenCount(all.filter((a) => !usesMlh(a)).length);
      setAccounts(kept);
      if (!kept.length) return;
      try {
        const dates = [...new Set(kept.map((a) => a.snapshot_date as string))];
        const rows = await loadFormRows(kept.map((a) => a.location_id), dates);
        // Keep each account's rows from its own latest snapshot only.
        const latest = new Map(kept.map((a) => [a.location_id, a.snapshot_date]));
        setForms(rows.filter((r) => latest.get(r.location_id) === r.snapshot_date));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  // Which accounts show, after the My/All and SSP switches.
  const visibleAccounts = useMemo(() => {
    const email = session?.user?.email?.toLowerCase();
    return (accounts ?? []).filter((a) => {
      if (a.is_parent && !includeSsp) return false;
      if (view === "mine" && email && !a.is_parent && (a.am_email ?? "").toLowerCase() !== email) return false;
      return true;
    });
  }, [accounts, includeSsp, view, session]);

  // Rows per account after the type / status / search filters.
  const byAccount = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const map = new Map<string, FormHealthRow[]>();
    for (const f of forms) {
      if (statusFilter.statuses.length && !statusFilter.statuses.includes(f.status)) continue;
      if (type && formTypeLabel({ ...f, kind: f.kind === "survey" ? "form" : f.kind }) !== type) continue;
      if (needle && !f.name.toLowerCase().includes(needle) && !f.form_id.toLowerCase().includes(needle)) continue;
      const list = map.get(f.location_id) ?? [];
      list.push(f);
      map.set(f.location_id, list);
    }
    for (const list of map.values()) {
      list.sort((a, b) => (FORM_STATUS[a.status]?.order ?? 9) - (FORM_STATUS[b.status]?.order ?? 9)
        || a.name.localeCompare(b.name));
    }
    return map;
  }, [forms, statusFilter, type, search]);

  const visibleIds = new Set(visibleAccounts.map((a) => a.location_id));
  const visibleForms = forms.filter((f) => visibleIds.has(f.location_id));
  const typeOptions = useMemo(() => [...new Set(forms.map((f) =>
    formTypeLabel({ ...f, kind: f.kind === "survey" ? "form" : f.kind })))].sort(), [forms]);
  const counts = (status: string) => visibleForms.filter((f) => f.status === status).length;
  const unclassified = forms.length > 0 && forms.every((f) => f.channel === null);

  // Accounts with a form that went quiet first, then alphabetical.
  const ordered = [...visibleAccounts].sort((a, b) => {
    const quiet = (id: string) => (byAccount.get(id) ?? []).some((f) => f.status === "silent") ? 0 : 1;
    return quiet(a.location_id) - quiet(b.location_id) || a.name.localeCompare(b.name);
  });

  return (
    <main className="mx-auto max-w-6xl px-4 py-4">
      <Section title="Forms">
        <p className="mb-3 text-xs text-ink-2">
          Every form in the {visibleAccounts.length} client account{visibleAccounts.length === 1 ? "" : "s"} that
          use MyLeadHub: what kind of form it is, whether it's working, and where it lives.
          {hiddenCount ? ` ${hiddenCount} account${hiddenCount === 1 ? "" : "s"} not using MLH (ads only, not in MLH, canceled, or no client users) are left out.` : ""}
        </p>

        <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
          <div className="flex overflow-hidden rounded border border-grid">
            <button onClick={() => setParam("view", null)}
                    className={`px-2.5 py-1 ${view === "all" ? "bg-ink font-medium text-white" : "text-ink-2"}`}>
              All
            </button>
            <button onClick={() => setParam("view", "mine")}
                    className={`px-2.5 py-1 ${view === "mine" ? "bg-ink font-medium text-white" : "text-ink-2"}`}>
              My accounts
            </button>
          </div>
          <select value={type} onChange={(e) => setParam("type", e.target.value || null)}
                  className="rounded border border-grid bg-surface px-2 py-1">
            <option value="">All form types</option>
            {typeOptions.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <select value={statusFilter.value} onChange={(e) => setParam("status", e.target.value || null)}
                  className="rounded border border-grid bg-surface px-2 py-1">
            {STATUS_FILTERS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <label className="flex items-center gap-1.5 text-ink-2">
            <input type="checkbox" checked={includeSsp} onChange={(e) => setParam("ssp", e.target.checked ? "1" : null)} />
            Include SSP
          </label>
          <input value={search} onChange={(e) => setParam("q", e.target.value)}
                 placeholder="Search form name or id"
                 className="rounded border border-grid bg-surface px-2 py-1" />
        </div>

        {accounts !== null && forms.length ? (
          <div className="mb-3 flex flex-wrap gap-3 text-xs">
            {(["active", "silent", "dormant", "no_leads"] as const).map((s) => (
              <span key={s} className={`rounded border border-grid bg-surface px-2 py-1 ${FORM_STATUS[s].tone}`}>
                {FORM_STATUS[s].label}: <span className="font-semibold">{fmtNum(counts(s))}</span>
              </span>
            ))}
          </div>
        ) : null}

        {unclassified ? (
          <p className="mb-3 rounded border border-dashed border-grid bg-surface px-3 py-2 text-xxs text-muted">
            Until the next nightly run with the updated collector, this data still comes from GHL's
            30-day default: forms quiet longer than that show as "never used" with no date. That run
            fills in every form's real last submission and days quiet (no limit), plus type, page and
            30-day counts.
          </p>
        ) : null}

        {error ? <EmptyState>Could not load forms: {error}</EmptyState> : null}
        {accounts === null && !error ? <Skeleton rows={6} /> : null}
        {accounts !== null && !ordered.length ? (
          <EmptyState>No accounts to show. Switch to "All" to see every account.</EmptyState>
        ) : null}

        {ordered.map((account) => {
          const rows = byAccount.get(account.location_id) ?? [];
          const all = forms.filter((f) => f.location_id === account.location_id);
          const working = all.filter((f) => f.status === "active").length;
          const quiet = all.filter((f) => f.status === "silent").length;
          return (
            <details key={account.location_id} open={quiet > 0 || ordered.length <= 3}
                     className="mb-2 rounded border border-grid bg-surface">
              <summary className="cursor-pointer select-none px-3 py-2 text-sm">
                <span className="font-semibold text-ink">{account.name}</span>
                <span className="ml-2 text-xxs text-muted">
                  {account.am_name ?? account.am_email?.split("@")[0] ?? ""} · {all.length} forms ·{" "}
                  <span className="text-status-good-text">{working} working</span>
                  {quiet ? <> · <span className="text-status-critical">{quiet} went quiet</span></> : null}
                </span>
                <Link to={`/account/${account.location_id}`}
                      className="ml-2 text-xxs text-series underline underline-offset-2">account page</Link>
              </summary>
              <div className="px-3 pb-3">
                <DetailTable
                  rows={rows}
                  empty="No forms match the filters in this account."
                  columns={[
                    { header: "Form", cell: (f) => f.name },
                    { header: "Form ID", cell: (f) => <span className="font-mono text-xxs text-ink-2">{f.form_id}</span> },
                    { header: "Type", cell: (f) => formTypeLabel(f) },
                    {
                      header: "Status",
                      cell: (f) => {
                        const meta = FORM_STATUS[f.status];
                        return <span className={`font-medium ${meta?.tone ?? "text-ink-2"}`}>{meta?.label ?? f.status}</span>;
                      },
                    },
                    { header: "30d", cell: (f) => fmtNum(f.subs_30d), numeric: true },
                    { header: "All time", cell: (f) => fmtNum(f.submissions_total), numeric: true },
                    { header: "Last submission", cell: (f) => (f.last_submission_at ? fmtDateTime(f.last_submission_at) : "—") },
                    {
                      header: "Days quiet",
                      cell: (f) => { const d = daysSince(f.last_submission_at); return d === null ? "—" : String(d); },
                      numeric: true,
                    },
                    {
                      header: "Lives on",
                      cell: (f) => (f.page_url ? <ExternalLink href={f.page_url}>{shortUrl(f.page_url)}</ExternalLink> : "—"),
                    },
                    { header: "Weekly test", cell: (f) => weeklyTestLabel(f) },
                  ]}
                />
              </div>
            </details>
          );
        })}

        <p className="mt-3 text-xxs text-muted">
          "Working" = a real submission within the last 3 business days. "Went quiet" = had submissions,
          none recently (checked nightly). Weekly test submissions never count as real activity.
          Facebook lead ads live inside Facebook, so they have no page.
        </p>
      </Section>
    </main>
  );
}
