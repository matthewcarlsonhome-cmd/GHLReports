// Safe client report contract. No internal snapshot or customer detail fields.
export interface ClientAccount { location_id: string; name: string; timezone: string; stale_hours: number; enabled_views?: string[] }
export interface ClientReport {
  previous_complete?: ClientReport | null;
  period_kind: 'attention' | 'week' | 'month'; period_start: string; generated_at: string;
  data: {
    schema_version: number; timezone: string; period_start: string; period_end: string;
    data_through: string; cohort_through: string; to_date: boolean;
    coverage: { contacts: boolean; responses: boolean; conversations: boolean };
    metrics: { new_leads: number | null; unassigned: number | null; eligible: number | null;
      contacted: number | null; completion_pct: number | null; uncontacted: number | null;
      speed_median: number | null; speed_p90: number | null; speed_samples: number | null;
      speed_human: boolean | null; waiting: number | null; oldest_wait_hours: number | null };
  };
}
export function staleReport(report: ClientReport, hours: number, now = Date.now()) {
  const captured = Date.parse(report.data.data_through);
  return !Number.isFinite(captured) || captured > now + 300000 || now - captured > hours * 3600000;
}
export function displayNumber(value: number | null | undefined, suffix = '') {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toLocaleString()}${suffix}` : 'Unavailable';
}
export function displayDuration(value: number | null) {
  if (value === null || !Number.isFinite(value)) return 'Unavailable';
  if (value < 60) return `${Math.round(value)} min`;
  if (value < 1440) return `${(value / 60).toFixed(1)} hr`;
  return `${(value / 1440).toFixed(1)} days`;
}
export function periodLabel(report: ClientReport) {
  const start=new Date(report.data.period_start), end=new Date(Date.parse(report.data.period_end)-1);
  if(end < start) return 'No completed days in this period yet';
  const format=new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric',year:'numeric',timeZone:report.data.timezone});
  return `${format.format(start)} – ${format.format(end)}`;
}
export function safeDestination(path: string) {
  return path.startsWith('/') && !path.startsWith('//') && !path.includes('\\') ? path : '/client';
}
export function leadTrend(current: ClientReport, previous?: ClientReport) {
  if (!previous || current.data.to_date || !current.data.coverage.contacts || !previous.data.coverage.contacts) return null;
  const a = current.data.metrics.new_leads, b = previous.data.metrics.new_leads;
  return a !== null && b !== null && b >= 3 ? Math.round(100 * (a-b)/b) : null;
}
