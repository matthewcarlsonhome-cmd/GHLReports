// Runs.tsx — the operational health page: a log of the last 30 collector runs
// (the nightly job that pulls data out of GoHighLevel) plus a token-health
// report. This page answers "did last night's data collection actually work,
// and which API tokens need attention?" — it's for the person running the
// system, not for account managers.
import { Fragment, useEffect, useState } from "react";

import { DetailTable, EmptyState, Section, Skeleton } from "../components/ui";
import type { CollectorRunRow, SubaccountRow } from "../lib/database.types";
import { fmtDateTime, fmtNum } from "../lib/format";
import { supabase } from "../lib/supabase";

// GHL Private Integration Tokens should be rotated ~quarterly; warn well
// before the 90-day mark so rotation happens on schedule, not in a panic.
const ROTATION_WARN_DAYS = 80;

// Lists every account whose API token needs action: status not "ok" (missing
// or invalid — no data can be collected) or older than the rotation warning.
// Healthy accounts are filtered out entirely, so an empty table is good news.
function TokenHealth({ subs }: { subs: SubaccountRow[] }) {
  const today = Date.now();
  const unhealthy = subs.filter((sub) => {
    if (!sub.active) return false;
    if (sub.token_status !== "ok") return true;
    if (!sub.token_rotated_at) return false;
    // Token age in days: epoch milliseconds (ms since 1970-01-01 UTC, what
    // Date.now()/getTime() return) divided by 86,400,000 ms per day.
    const age = (today - new Date(sub.token_rotated_at).getTime()) / 86400000;
    return age > ROTATION_WARN_DAYS;
  });
  return (
    <Section title="Token health">
      {unhealthy.length === 0 ? (
        <EmptyState>All tokens ok and younger than 80 days.</EmptyState>
      ) : (
        <DetailTable
          rows={unhealthy}
          empty=""
          columns={[
            { header: "Account", cell: (r) => r.name },
            {
              header: "Status",
              cell: (r) =>
                r.token_status !== "ok" ? (
                  <span className="text-status-critical">▲ {r.token_status}</span>
                ) : (
                  <span className="text-ink-2">ok</span>
                ),
            },
            { header: "Rotated", cell: (r) => r.token_rotated_at ?? "never" },
            {
              // The fix is literal instructions, keyed to the exact Vault
              // secret name for this account.
              header: "Action",
              cell: (r) =>
                r.token_status !== "ok"
                  ? `Add/replace Vault secret ghl_pit_${r.location_id}`
                  : "Rotate: new PIT in GHL, then replace the Vault secret value",
            },
          ]}
        />
      )}
    </Section>
  );
}

export default function Runs() {
  // runs === null means "still loading" (distinct from "loaded, empty" = []).
  const [runs, setRuns] = useState<CollectorRunRow[] | null>(null);
  const [subs, setSubs] = useState<SubaccountRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  // ID of the run whose per-location breakdown row is open (one at a time).
  const [expanded, setExpanded] = useState<number | null>(null);

  // One-time load on mount. Promise.all runs both queries concurrently; the
  // IIFE wrapper exists because useEffect's callback itself can't be async.
  useEffect(() => {
    (async () => {
      const [runsRes, subsRes] = await Promise.all([
        supabase.from("collector_runs").select("*")
          .order("started_at", { ascending: false }).limit(30),
        supabase.from("subaccounts").select("*"),
      ]);
      if (runsRes.error) setError(runsRes.error.message);
      else setRuns((runsRes.data ?? []) as CollectorRunRow[]);
      setSubs((subsRes.data ?? []) as SubaccountRow[]);
    })();
  }, []);

  // Run details key locations by raw ID; map to the human slug when we can.
  const nameFor = (locationId: string) =>
    subs.find((s) => s.location_id === locationId)?.slug ?? locationId;

  return (
    <div className="mx-auto max-w-5xl px-4 py-4">
      <Section title="Collector runs (last 30)">
        {error ? <EmptyState>Could not load runs: {error}</EmptyState> : null}
        {!runs && !error ? <Skeleton rows={5} /> : null}
        {runs ? (
          runs.length === 0 ? (
            <EmptyState>No runs recorded yet — trigger the Render cron (or run the collector locally).</EmptyState>
          ) : (
            <div className="overflow-x-auto rounded border border-grid bg-surface">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-grid text-left text-xxs uppercase tracking-wide text-muted">
                    <th className="px-2 py-1.5 font-medium">Started</th>
                    <th className="px-2 py-1.5 font-medium">Finished</th>
                    <th className="px-2 py-1.5 font-medium">Status</th>
                    <th className="px-2 py-1.5 text-right font-medium">OK</th>
                    <th className="px-2 py-1.5 text-right font-medium">Held</th>
                    <th className="px-2 py-1.5 text-right font-medium">Failed</th>
                    <th className="px-2 py-1.5 text-right font-medium">Requests</th>
                    {/* 429 = HTTP "Too Many Requests" (rate limited by GHL) */}
                    <th className="px-2 py-1.5 text-right font-medium">429s</th>
                    <th className="px-2 py-1.5 font-medium">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {/* Each run renders 1–2 <tr>s (summary + optional detail),
                      so <Fragment key> groups them without extra DOM. */}
                  {runs.map((run) => (
                    <Fragment key={run.id}>
                      {/* clicking a row toggles its per-location breakdown */}
                      <tr
                        className="cursor-pointer border-b border-grid/60 hover:bg-plane"
                        onClick={() => setExpanded(expanded === run.id ? null : run.id)}
                      >
                        <td className="px-2 py-1.5">{fmtDateTime(run.started_at)}</td>
                        <td className="px-2 py-1.5">{fmtDateTime(run.finished_at)}</td>
                        <td className="px-2 py-1.5">
                          <span className={
                            run.status === "ok" ? "text-status-good-text"
                              : run.status === "failed" ? "text-status-critical" : "text-ink-2"}>
                            {run.status ?? "—"}
                          </span>
                        </td>
                        <td className="px-2 py-1.5 text-right tabular">{run.locations_ok}</td>
                        <td className="px-2 py-1.5 text-right tabular">{run.locations_held}</td>
                        <td className="px-2 py-1.5 text-right tabular">{run.locations_failed}</td>
                        <td className="px-2 py-1.5 text-right tabular">{fmtNum(run.requests_made)}</td>
                        <td className="px-2 py-1.5 text-right tabular">{fmtNum(run.rate_limited)}</td>
                        <td className="max-w-64 truncate px-2 py-1.5 text-xxs text-status-critical"
                            title={run.error ?? undefined}>
                          {run.error ?? "—"}
                        </td>
                      </tr>
                      {/* expanded detail: a nested table spanning all columns,
                          one row per collected location */}
                      {expanded === run.id && Object.keys(run.details ?? {}).length > 0 ? (
                        <tr className="border-b border-grid/60 bg-plane">
                          <td colSpan={9} className="px-3 py-2">
                            <table className="w-full text-xxs">
                              <thead>
                                <tr className="text-left uppercase tracking-wide text-muted">
                                  <th className="py-0.5 pr-3 font-medium">Location</th>
                                  <th className="py-0.5 pr-3 font-medium">Status</th>
                                  <th className="py-0.5 pr-3 text-right font-medium">Requests</th>
                                  <th className="py-0.5 pr-3 text-right font-medium">429s</th>
                                  <th className="py-0.5 pr-3 text-right font-medium">Seconds</th>
                                  <th className="py-0.5 font-medium">Gate / error</th>
                                </tr>
                              </thead>
                              <tbody>
                                {Object.entries(run.details).map(([locationId, detail]) => (
                                  <tr key={locationId} className="border-t border-grid/40">
                                    <td className="py-1 pr-3">{nameFor(locationId)}</td>
                                    <td className="py-1 pr-3">{detail.status ?? "—"}</td>
                                    <td className="py-1 pr-3 text-right tabular">{detail.requests ?? "—"}</td>
                                    <td className="py-1 pr-3 text-right tabular">{detail.rate_limited ?? "—"}</td>
                                    <td className="py-1 pr-3 text-right tabular">{detail.seconds ?? "—"}</td>
                                    <td className="py-1 text-ink-2">
                                      {detail.error ?? (detail.gate?.length ? detail.gate.join("; ") : "—")}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </Section>

      <TokenHealth subs={subs} />
    </div>
  );
}
