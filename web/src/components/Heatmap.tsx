// Heatmap.tsx — the 7-day × 24-hour lead-activity heatmap on the Account page.
//
// After-hours arrival vs response heatmap (spec Tier 2): pure UI on
// lead_events. 7 days × 24 hours in the account's timezone; a sequential
// single-hue ramp colors either arrival counts or median first response,
// never both at once. Business hours (Mon–Fri 9–17) are outlined so the
// after-hours story reads at a glance.
//
// The bucketing idea: every lead event carries a UTC timestamp. We convert it
// into the *account's* local weekday+hour (a lead at 6pm in the client's town
// is "after hours" no matter where the viewer sits), then increment that cell
// of a 7×24 grid. Cell color = value / max mapped onto the ramp.

import { useMemo, useState } from "react";

import type { LeadEventRow } from "../lib/database.types";

// Validated sequential blue ramp (light steps 100→700)
// "Sequential" = one hue, light→dark, for values that only grow from zero.
// A single hue keeps "darker = more" true everywhere on the grid.
const RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_INDEX: Record<string, number> = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };

type Cell = { arrivals: number; responses: number[] };

// Convert an ISO timestamp to { day, hour } in the given IANA timezone using
// Intl.DateTimeFormat — the browser's built-in timezone database, so no date
// library is needed. formatToParts returns labeled pieces ("Tue", "18") that
// we map back to grid indices. Returns null for anything unparseable.
function localDayHour(iso: string, timeZone: string): { day: number; hour: number } | null {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone, weekday: "short", hour: "numeric", hourCycle: "h23",
    }).formatToParts(new Date(iso));
    const weekday = parts.find((p) => p.type === "weekday")?.value ?? "";
    const hour = Number(parts.find((p) => p.type === "hour")?.value ?? "");
    if (!(weekday in DAY_INDEX) || Number.isNaN(hour)) return null;
    return { day: DAY_INDEX[weekday], hour };
  } catch {
    return null;
  }
}

// Standard median: sort a copy, take the middle element (or the mean of the
// two middles for an even count). Median beats mean here because one 3-day
// response would drag an average way up.
function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// Map value/max (0..1) onto the ramp's discrete buckets. Math.min clamps the
// value === max case, which would otherwise index one past the end.
function rampColor(value: number, max: number): string {
  if (value <= 0 || max <= 0) return "transparent";
  const bucket = Math.min(RAMP.length - 1, Math.floor((value / max) * RAMP.length));
  return RAMP[bucket];
}

// Minutes → compact label for tooltips ("45m" / "2.5h" / "1.2d").
function fmtMinutes(value: number): string {
  if (value < 60) return `${Math.round(value)}m`;
  if (value < 1440) return `${(value / 60).toFixed(1)}h`;
  return `${(value / 1440).toFixed(1)}d`;
}

// Props: `events` = last-30-days lead events; `timeZone` = the account's IANA
// timezone (e.g. "America/Chicago"). One piece of local state: which metric
// currently drives the color (arrival counts vs median response time).
export function AfterHoursHeatmap({ events, timeZone }: {
  events: LeadEventRow[];
  timeZone: string;
}) {
  const [mode, setMode] = useState<"arrivals" | "response">("arrivals");

  // useMemo caches the bucketed grid; it's only rebuilt when the events or
  // timezone actually change, not on every re-render (e.g. a mode toggle).
  const grid = useMemo(() => {
    // 7 rows (days) × 24 columns (hours), each accumulating a count and the
    // raw response-time samples that fell into that slot.
    const cells: Cell[][] = Array.from({ length: 7 }, () =>
      Array.from({ length: 24 }, () => ({ arrivals: 0, responses: [] as number[] })));
    for (const event of events) {
      const slot = localDayHour(event.created_at, timeZone);
      if (!slot) continue;
      const cell = cells[slot.day][slot.hour];
      cell.arrivals += 1;
      // Prefer the human response time; fall back to any first touch
      // (automation included) when no human touch was recorded.
      const response = event.first_human_touch_minutes ?? event.first_touch_minutes;
      if (response !== null) cell.responses.push(response);
    }
    return cells;
  }, [events, timeZone]);

  // Scale anchors for the color ramp (floored at 1 to avoid divide-by-zero).
  const maxArrivals = Math.max(...grid.flat().map((c) => c.arrivals), 1);
  const medians = grid.map((row) => row.map((c) => median(c.responses)));
  const maxMedian = Math.max(...medians.flat().map((m) => m ?? 0), 1);

  const isBusinessHours = (day: number, hour: number) => day < 5 && hour >= 9 && hour < 17;

  return (
    <div className="rounded border border-grid bg-surface p-3">
      <div className="mb-2 flex flex-wrap items-center gap-3 text-xs">
        <span className="font-semibold">Lead arrivals by day and hour ({timeZone})</span>
        {/* mode toggle: one metric colors the grid at a time, never both */}
        <div className="flex rounded border border-grid text-xxs">
          <button onClick={() => setMode("arrivals")}
                  className={`px-2 py-0.5 ${mode === "arrivals" ? "bg-ink text-white" : "text-ink-2"}`}>
            color: arrivals
          </button>
          <button onClick={() => setMode("response")}
                  className={`px-2 py-0.5 ${mode === "response" ? "bg-ink text-white" : "text-ink-2"}`}>
            color: median response
          </button>
        </div>
        {/* in-place legend: says what "darker" means for the current mode */}
        <span className="text-xxs text-muted">
          darker = {mode === "arrivals" ? "more leads" : "slower response"} · outlined block = business hours (Mon–Fri 9–5)
        </span>
      </div>
      <div className="overflow-x-auto">
        {/* the grid is literally an HTML table: one <td> per day-hour cell */}
        <table className="border-separate" style={{ borderSpacing: 2 }}>
          <thead>
            <tr>
              <th />
              {/* label every 3rd hour to keep the header readable */}
              {Array.from({ length: 24 }, (_, hour) => (
                <th key={hour} className="min-w-[18px] text-center text-xxs font-normal text-muted">
                  {hour % 3 === 0 ? hour : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.map((row, day) => (
              <tr key={day}>
                <td className="pr-1 text-right text-xxs text-ink-2">{DAYS[day]}</td>
                {row.map((cell, hour) => {
                  const medianValue = medians[day][hour];
                  // Pick value + scale for whichever metric is coloring cells.
                  const value = mode === "arrivals" ? cell.arrivals : (medianValue ?? 0);
                  const max = mode === "arrivals" ? maxArrivals : maxMedian;
                  // Tooltip always carries both numbers, so color-only cells
                  // still expose exact values on hover (accessibility).
                  const tooltip = cell.arrivals
                    ? `${DAYS[day]} ${hour}:00 — ${cell.arrivals} lead${cell.arrivals === 1 ? "" : "s"}` +
                      (medianValue !== null ? `, median response ${fmtMinutes(medianValue)}` : ", no response recorded")
                    : `${DAYS[day]} ${hour}:00 — no leads`;
                  return (
                    <td key={hour} title={tooltip}
                        className="h-[18px] w-[18px] rounded-sm"
                        style={{
                          backgroundColor: cell.arrivals ? rampColor(value, max) : "#f0efec",
                          outline: isBusinessHours(day, hour) ? "1px solid #898781" : undefined,
                          outlineOffset: -1,
                        }} />
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xxs text-muted">
        Last 30 days of lead events. After-hours arrivals with slow responses are the
        case for extending coverage or automation.
      </p>
    </div>
  );
}
