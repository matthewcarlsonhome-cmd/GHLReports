// ui.tsx — the shared building-block components (badges, chips, tiles, tables)
// used by every page. Each one is a small "presentational" component: it takes
// props in, returns JSX out, and holds no state and does no data fetching.
import type { ReactNode } from "react";

import type { PortfolioState } from "../lib/database.types";
import type { DataQuality } from "../lib/format";

// Color never carries state alone: every badge and chip pairs its color with
// an icon or a text label (spec v3 9.2 — "icon plus color, never color alone").
// That's an accessibility rule — colorblind users and grayscale printouts must
// still be able to tell "attention" from "steady". Hence the glyph/label
// tables below alongside every color class.

// Compact state marker for table rows: a glyph (●/✓/–) colored by state.
// With withLabel=false the text label is still rendered for screen readers via
// the "sr-only" class (visually hidden, read aloud by assistive tech).
export function StateIcon({ state, withLabel = false }: { state: PortfolioState; withLabel?: boolean }) {
  const spec: Record<PortfolioState, { glyph: string; className: string; label: string }> = {
    attention: { glyph: "●", className: "text-status-critical", label: "Needs attention" },
    steady: { glyph: "✓", className: "text-status-good-text", label: "Steady" },
    no_data: { glyph: "–", className: "text-muted", label: "No data" },
  };
  const { glyph, className, label } = spec[state];
  return (
    <span className={`inline-flex items-center gap-1 text-xxs font-medium ${className}`} title={label}>
      <span aria-hidden>{glyph}</span>
      {withLabel ? label : <span className="sr-only">{label}</span>}
    </span>
  );
}

// Full pill version of the state marker — glyph + label + tinted background.
export function StateBadge({ state }: { state: PortfolioState }) {
  const styles: Record<PortfolioState, string> = {
    attention: "bg-status-critical/10 text-status-critical border-status-critical/30",
    steady: "bg-status-good/10 text-status-good-text border-status-good/30",
    no_data: "bg-grid text-ink-2 border-hairline/50",
  };
  const labels: Record<PortfolioState, string> = {
    attention: "● Needs attention",
    steady: "✓ Steady",
    no_data: "– No data",
  };
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-xxs font-medium ${styles[state]}`}>
      {labels[state]}
    </span>
  );
}

// Loading placeholder: N pulsing gray bars of decreasing width, shown while a
// page's first query is still in flight.
export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="animate-pulse rounded border border-grid bg-surface p-3">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="mb-2 h-3 rounded bg-grid last:mb-0" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

// Flag-severity pill ("● 2 red", "▲ 1 amber"). Different glyph per severity
// (dot/triangle/i) so severities remain distinguishable without color.
// Renders nothing when count is exactly 0; count === undefined means "show the
// label without a number" (used on individual flags rather than tallies).
export function SeverityChip({ severity, count }: { severity: "red" | "amber" | "info"; count?: number }) {
  if (count === 0) return null;
  const styles = {
    red: "bg-status-critical text-white",
    amber: "bg-status-warning text-ink",
    info: "bg-grid text-ink-2",
  } as const;
  const glyphs = { red: "●", amber: "▲", info: "i" } as const;
  const labels = { red: "red", amber: "amber", info: "info" } as const;
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xxs font-semibold ${styles[severity]}`}>
      <span aria-hidden>{glyphs[severity]} </span>
      {count !== undefined ? `${count} ${labels[severity]}` : labels[severity]}
    </span>
  );
}

// Data-trust badge (complete / partial / held / no data). The quality word
// itself is the badge text, so color is reinforcement rather than the message.
export function QualityBadge({ quality }: { quality: DataQuality }) {
  const styles: Record<DataQuality, string> = {
    complete: "text-status-good-text border-status-good/40",
    partial: "text-ink-2 border-status-warning/60",
    held: "text-status-critical border-status-critical/40",
    "no data": "text-muted border-hairline/50",
  };
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-xxs ${styles[quality]}`}>
      {quality}
    </span>
  );
}

// Titled page section. `right` is an optional slot for a control rendered on
// the same line as the heading (e.g. the Copy button on the client summary).
export function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="mb-6">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

// Dashed-border box for "there is nothing here" messages — deliberately calm,
// since an empty list is usually good news on this dashboard.
export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded border border-dashed border-grid bg-surface px-3 py-4 text-xs text-muted">
      {children}
    </div>
  );
}

// KPI tile: small label, big value, optional context line underneath.
// `tone` colors only the value ("bad" red / "good" green); the threshold logic
// deciding the tone lives at the call site, not here.
export function StatTile({
  label,
  value,
  context,
  tone,
}: {
  label: string;
  value: string;
  context?: string;
  tone?: "bad" | "good";
}) {
  const valueColor =
    tone === "bad" ? "text-status-critical" : tone === "good" ? "text-status-good-text" : "text-ink";
  return (
    <div className="rounded border border-grid bg-surface px-3 py-2">
      <div className="text-xxs uppercase tracking-wide text-muted">{label}</div>
      <div className={`text-lg font-semibold ${valueColor}`}>{value}</div>
      {context ? <div className="text-xxs text-ink-2">{context}</div> : null}
    </div>
  );
}

// Generic data table used by every drill-down list. It's a *generic* component
// (<T>): callers pass rows of any type plus column definitions whose `cell`
// function renders one cell from one row. `numeric` right-aligns a column and
// switches on tabular (equal-width) digits so numbers line up vertically.
// An empty rows array renders the `empty` message instead of a bare table.
export function DetailTable<T>({
  columns,
  rows,
  empty,
}: {
  columns: { header: string; cell: (row: T) => ReactNode; numeric?: boolean }[];
  rows: T[];
  empty: string;
}) {
  if (!rows.length) return <EmptyState>{empty}</EmptyState>;
  return (
    <div className="overflow-x-auto rounded border border-grid bg-surface">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-grid text-left text-xxs uppercase tracking-wide text-muted">
            {columns.map((col) => (
              <th key={col.header} className={`px-2 py-1.5 font-medium ${col.numeric ? "text-right" : ""}`}>
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-grid/60 last:border-0 hover:bg-plane">
              {columns.map((col) => (
                <td key={col.header} className={`px-2 py-1.5 ${col.numeric ? "text-right tabular" : ""}`}>
                  {col.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Out-of-app link (deep links into GHL). target="_blank" opens a new tab;
// rel="noreferrer" stops the target page from reading window.opener or the
// referrer — standard hygiene for links to another origin.
export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-series underline decoration-series/40 underline-offset-2 hover:decoration-series"
    >
      {children}
    </a>
  );
}
