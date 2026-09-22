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

// The weekly synthetic form check (docs/FORM-MONITORING.md §4): did the
// latest one land, and as a real CRM contact?
export function weeklyTestLabel(f: Pick<FormHealthRow, "last_check_at" | "check_contact_ok">): string {
  const days = daysSince(f.last_check_at);
  if (days === null) return "—";
  if (f.check_contact_ok === false) return `✗ ${days}d ago, no contact`;
  return days > 8 ? `⚠ last ${days}d ago` : `✓ ${days}d ago`;
}
