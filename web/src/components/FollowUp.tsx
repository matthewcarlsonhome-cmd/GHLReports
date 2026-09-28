// A small default workspace; all of the existing reporting remains one click away.
import { useSearchParams, Link } from "react-router-dom";
import type { PortfolioRow, SnapshotRow } from "../lib/database.types";
import { followUpSummary, reportingMetrics, sourceComplete } from "../lib/followUp";
import { fmtDate, fmtHours, fmtMinutes, fmtNum, fmtPct } from "../lib/format";
import { usesMlh } from "../lib/mlh";
import { EmptyState, Skeleton } from "./ui";

export function ReportViews({ detailed }: { detailed: boolean }) {
  const [params, setParams] = useSearchParams();
  return <nav aria-label="Account health views" className="mb-6 flex w-fit max-w-full gap-1 rounded-xl border border-grid bg-surface p-1 text-sm">
    {[false, true].map((report) => <button key={String(report)} aria-pressed={detailed === report}
      onClick={() => { const next = new URLSearchParams(params); next.set("mode", report ? "reports" : "followup"); setParams(next); }}
      className={`rounded-lg px-4 py-2 font-medium transition-colors ${detailed === report ? "bg-ink text-white" : "text-ink-2 hover:bg-plane"}`}>
      {report ? "Detailed reports" : "Follow-up"}
    </button>)}
  </nav>;
}

export function FollowUpCounts({ row }: { row: SnapshotRow | null }) {
  const summary = followUpSummary(row);
  const metrics = row ? reportingMetrics(row) : null;
  const speedAvailable = sourceComplete(row, "contacts", "speed_to_lead");
  const volumeKnown = metrics?.leads_new_7d !== null && metrics?.leads_new_7d !== undefined;
  const baseline = metrics?.leads_trailing_avg;
  return <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
    <div className="rounded-xl border border-grid bg-surface p-5">
      <p className="text-sm font-medium text-ink-2">New leads · last 7 days</p>
      <p className="my-2 text-3xl font-semibold tabular">{fmtNum(metrics?.leads_new_7d)}</p>
      <p className="text-xs leading-relaxed text-ink-2">{!volumeKnown
        ? "Data incomplete or unavailable. Review coverage in Detailed reports."
        : baseline === null || baseline === undefined ? `Building a baseline (${row?.trailing_n ?? 0}/4 weeks).`
        : `Typical week: ${fmtNum(baseline)} leads · previous ${row?.trailing_n ?? 4} weeks.`}</p>
      {volumeKnown && baseline !== null && baseline !== undefined ? <p className="mt-1 text-xs leading-relaxed text-ink-2">
        {metrics?.leads_delta_pct !== null && metrics?.leads_delta_pct !== undefined
          ? `${fmtPct(metrics.leads_delta_pct)} compared with a typical week.`
          : "Too few leads for a reliable percentage comparison."}
      </p> : null}
    </div>
    {[
      { title: "Leads needing a first response", count: summary.leads, detail: "New leads from the past 7 days, uncontacted for over 24 hours.", oldest: null },
      { title: "Customers waiting for a reply", count: summary.replies, detail: "Inbound conversations waiting 4+ hours, adjusted for weekends.", oldest: (summary.replies ?? 0) > 0 ? `Oldest waiting reply: ${fmtHours(metrics?.convos_waiting_max_hours)}` : null },
    ].map((item) => <div key={item.title} className="rounded-xl border border-grid bg-surface p-5">
      <p className="text-sm font-medium text-ink-2">{item.title}</p>
      <p className={`my-2 text-3xl font-semibold tabular ${(item.count ?? 0) > 0 ? "text-status-critical" : "text-ink"}`}>{item.count ?? "Unknown"}</p>
      <p className="text-xs leading-relaxed text-ink-2">{item.count === null ? "Data incomplete or unavailable. Review coverage in Detailed reports." : item.detail}</p>
      {item.oldest ? <p className="mt-1 text-xs font-medium text-ink-2">{item.oldest}</p> : null}
    </div>)}
    <div className="rounded-xl border border-grid bg-surface p-5">
      <p className="text-sm font-medium text-ink-2">{row?.speed_kind_known ? "Speed to lead (human)" : "Speed to lead (automation incl.)"}</p>
      <p className="my-2 text-3xl font-semibold tabular">{fmtMinutes(metrics?.speed_to_lead_median_min)}</p>
      <p className="text-xs leading-relaxed text-ink-2">Median first response · 90th percentile: {fmtMinutes(metrics?.speed_to_lead_p90_min)}</p>
      <p className="mt-1 text-xs leading-relaxed text-ink-2">{!speedAvailable || metrics?.speed_to_lead_median_min === null
        ? "Data incomplete or unavailable. Review coverage in Detailed reports."
        : row?.speed_kind_known ? "Human replies only." : "Includes automated replies."}</p>
    </div>
  </div>;
}

export function FollowUpPortfolio({ rows, error, email }: { rows: PortfolioRow[] | null; error: string | null; email?: string }) {
  const [params, setParams] = useSearchParams();
  const search = params.get("fq") ?? "";
  const owner = params.get("owner") ?? "";
  const status = params.get("followup") ?? "";
  function filter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
  }
  const accounts = (rows ?? []).filter((row) => !row.is_parent && usesMlh(row));
  const managers = [...new Map(accounts.filter((r) => r.am_email).map((r) => [r.am_email!, r.am_name ?? r.am_email!])).entries()].sort((a, b) => a[1].localeCompare(b[1]));
  const matching = accounts.filter((row) => (!owner || (row.am_email ?? "").toLowerCase() === (owner === "mine" ? email ?? "" : owner).toLowerCase())
    && `${row.name} ${row.slug}`.toLowerCase().includes(search.trim().toLowerCase()));
  const counts = { "follow-up": 0, "check-data": 0, clear: 0 };
  for (const row of matching) counts[followUpSummary(row).status]++;
  const visible = matching.filter((row) => !status || followUpSummary(row).status === status)
    .sort((a, b) => followUpSummary(a).rank - followUpSummary(b).rank || a.name.localeCompare(b.name));
  return <div className="mx-auto max-w-[1200px] px-4 py-6 sm:px-6">
    <div className="mb-5"><p className="mb-1 text-xs font-medium uppercase tracking-widest text-ink-2">Account health</p>
      <h1 className="text-2xl font-semibold tracking-tight">Know who needs a follow-up.</h1>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-ink-2">Start with missed leads and waiting replies. Open an account for the next step, supporting details, and a ready-to-copy report.</p>
    </div>
    <ReportViews detailed={false} />
    <div className="mb-4 grid grid-cols-3 gap-2 sm:gap-3">
      {([ ["follow-up", "Need follow-up"], ["clear", "No follow-up outstanding"], ["check-data", "Need data review"] ] as const).map(([key, label]) =>
        <button key={key} aria-pressed={status === key} onClick={() => filter("followup", status === key ? "" : key)}
          className={`rounded-xl border p-3 text-left sm:p-4 transition-colors ${status === key ? "border-ink bg-ink text-white" : "border-grid bg-surface hover:border-hairline"}`}>
          <span className="block text-xs sm:text-sm">{label}</span><span className="mt-1 block text-3xl font-semibold tabular">{rows && !error ? counts[key] : "—"}</span>
          <span className="text-xs opacity-70">{counts[key] === 1 ? "account" : "accounts"}</span>
        </button>)}
    </div>
    <div className="mb-4 flex flex-wrap gap-3">
      <input aria-label="Search accounts" type="search" value={search} onChange={(e) => filter("fq", e.target.value)} placeholder="Search accounts…"
        className="min-w-0 basis-full sm:basis-0 flex-1 rounded-lg border border-grid bg-surface px-3 py-2.5 text-sm" />
      <select aria-label="Account manager" value={owner} onChange={(e) => filter("owner", e.target.value)} className="max-w-full rounded-lg border border-grid bg-surface px-3 py-2.5 text-sm">
        <option value="">All account managers</option>{email ? <option value="mine">My accounts</option> : null}
        {managers.map(([address, name]) => <option key={address} value={address}>{name}</option>)}
      </select>
      {status || search || owner ? <button className="text-sm underline underline-offset-4" onClick={() => { const next = new URLSearchParams(params); ["fq", "owner", "followup"].forEach((key) => next.delete(key)); setParams(next, { replace: true }); }}>Clear filters</button> : null}
    </div>
    <p className="mb-3 text-xs leading-relaxed text-ink-2">Daily snapshot · Counts show work to review, not whether an email was sent. Full account coverage is available in Detailed reports.</p>
    {error ? <EmptyState>Could not load accounts: {error}</EmptyState> : !rows ? <Skeleton rows={6} /> : <>
      <p className="mb-3 text-sm text-ink-2" role="status">{visible.length} account{visible.length === 1 ? "" : "s"}{status ? " · filtered" : " · follow-up first"}</p>
      <div className="grid gap-3">
        {visible.map((row) => { const summary = followUpSummary(row); return <Link key={row.location_id} to={`/account/${row.location_id}`} className="group rounded-xl border border-grid bg-surface p-4 transition-colors hover:border-hairline hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-series sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold group-hover:underline">{row.name}</h2>
            <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${summary.status === "follow-up" ? "bg-status-critical/10 text-status-critical" : summary.status === "clear" ? "bg-status-good/10 text-status-good-text" : "bg-grid text-ink-2"}`}>{summary.label}</span>
          </div>
          <p className="mt-1 text-xs text-ink-2">{row.am_name ?? row.am_email ?? "No account manager"} · {row.snapshot_date ? `Updated ${fmtDate(row.snapshot_date)}` : "No snapshot yet"}</p>
          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-[1fr_1fr_2fr]">
            <div><span className="text-xl font-semibold tabular">{summary.leads ?? "—"}</span><p className="text-xs text-ink-2">leads uncontacted over 24h</p></div>
            <div><span className="text-xl font-semibold tabular">{summary.replies ?? "—"}</span><p className="text-xs text-ink-2">customers waiting for replies</p></div>
            <p className="col-span-2 text-sm leading-relaxed text-ink-2 sm:col-span-1">{summary.action}{summary.incomplete && summary.status === "follow-up" ? " Some data is unavailable." : ""}</p>
          </div>
          <span className="mt-3 inline-block text-xs font-medium text-series">Review account →</span>
        </Link>; })}
      </div>
      {!visible.length ? <EmptyState>No accounts match. Clear the filters to see more accounts.</EmptyState> : null}
    </>}
  </div>;
}
