// forms.ts: shared labels for form_health rows (Account page Forms card and
// the Forms tab). The collector decides type and status; these only turn
// them into plain words. See docs/FORM-MONITORING.md.
import type { FormHealthRow } from "./database.types";

// Icon + words, never color alone. Order is also the default sort order:
// forms that need a look come first.
export const FORM_STATUS: Record<string, { label: string; tone: string; order: number }> = {
  silent: { label: "⚠ went quiet", tone: "text-status-critical", order: 0 },
  unknown: { label: "? unknown", tone: "text-muted", order: 1 },
  active: { label: "✓ working", tone: "text-status-good-text", order: 2 },
  new: { label: "+ new, no leads yet", tone: "text-series", order: 3 },
  dormant: { label: "◌ quiet 30+ days", tone: "text-muted", order: 4 },
  no_leads: { label: "○ never used", tone: "text-muted", order: 5 },
};

export function formTypeLabel(f: Pick<FormHealthRow, "channel" | "kind">): string {
  const base = f.channel ?? (f.kind === "unlisted" ? "Not in Sites > Forms" : "Not classified yet");
  return f.kind === "survey" ? `${base} (survey)` : base;
}

// host/path without the scheme or www, trimmed for a table cell.
export function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    const path = u.pathname === "/" ? "" : u.pathname;
    return `${u.host.replace(/^www\./, "")}${path}`.slice(0, 48);
  } catch {
    return url.slice(0, 48);
  }
}

export function daysSince(iso: string | null): number | null {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}

// Row order for form tables: forms that have a last submission first,
// ordered by days quiet (most recently active on top, or longest quiet on
// top with longestFirst); forms never submitted to sink to the bottom by
// name. Compares the timestamps themselves, so same-day forms keep their
// real order.
export function byDaysQuiet(longestFirst = false) {
  return (a: Pick<FormHealthRow, "last_submission_at" | "name">,
          b: Pick<FormHealthRow, "last_submission_at" | "name">): number => {
    const ta = a.last_submission_at ? new Date(a.last_submission_at).getTime() : null;
    const tb = b.last_submission_at ? new Date(b.last_submission_at).getTime() : null;
    if (ta === null && tb === null) return a.name.localeCompare(b.name);
    if (ta === null) return 1;
    if (tb === null) return -1;
    return (longestFirst ? ta - tb : tb - ta) || a.name.localeCompare(b.name);
  };
}

// The weekly synthetic form check (docs/FORM-MONITORING.md §4): did the
// latest one land, and as a real CRM contact?
export function weeklyTestLabel(f: Pick<FormHealthRow, "last_check_at" | "check_contact_ok">): string {
  const days = daysSince(f.last_check_at);
  if (days === null) return "—";
  if (f.check_contact_ok === false) return `✗ ${days}d ago, no contact`;
  return days > 8 ? `⚠ last ${days}d ago` : `✓ ${days}d ago`;
}
