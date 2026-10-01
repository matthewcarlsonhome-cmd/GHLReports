"""Client-safe aggregates only; no I/O, lead identifiers, names, or email delivery.

Calendar cohorts are calculated from records, never averaged snapshot medians.
The existing 14-day response scan cannot establish older response histories;
those metrics deliberately remain unavailable rather than extrapolated.
"""
from datetime import datetime, time, timedelta, timezone

from . import metrics


def complete(snapshot, *sources):
    if snapshot.get('gate_passed') is not True:
        return False
    for source in sources:
        entry = (snapshot.get('coverage', {}).get('sources', {}).get(source) or {})
        if entry.get('status') != 'complete' or entry.get('exhausted') is not True or entry.get('error') or entry.get('skipped'):
            return False
    return True


def month_start(value, offset=0):
    month = value.year * 12 + value.month - 1 + offset
    return value.replace(year=month // 12, month=month % 12 + 1, day=1)


def build_reports(sub, snapshot, contacts, events, now, history_start, tz):
    """Return immutable report versions, with only whitelisted numeric fields.

    Contacts are already filtered by the established exclusion rules. Periods
    end at midnight (exclusive); today's incomplete day is intentionally absent.
    Response completion is measured at that same cutoff, not at send time.
    """
    cutoff = datetime.combine(now.astimezone(tz).date(), time.min, tzinfo=tz)
    week = cutoff - timedelta(days=cutoff.weekday())
    periods = [('attention', cutoff - timedelta(days=7), cutoff)]
    periods += [('week', week - timedelta(days=7*i), min(week + timedelta(days=7*(1-i)), cutoff)) for i in range(5)]
    periods += [('month', month_start(cutoff, -i), min(month_start(cutoff, 1-i), cutoff)) for i in range(2)]
    rows = []
    for kind, start, end in periods:
        volume_ok = complete(snapshot, 'contacts') and start >= history_start
        cohort = {str(c.get('id')): c for c in contacts if c.get('id') and
                  (created := metrics.parse_ts(c.get('dateAdded'))) is not None and start <= created < end}
        observed = {str(e.get('contact_id')): e for e in events}
        # No response metric is trusted if a source is capped, even if an
        # accidentally matching subset appears to cover the current cohort.
        response_ok = volume_ok and complete(snapshot, 'speed_to_lead') and set(cohort) <= set(observed)
        selected = []
        for cid in cohort:
            if cid not in observed:
                continue
            item = dict(observed[cid])
            for at, duration in [('first_outbound_at','first_touch_minutes'),('first_human_touch_at','first_human_touch_minutes')]:
                stamp = metrics.parse_ts(item.get(at))
                item[at] = stamp if stamp and stamp < end else None
                if item[at] is None:
                    item[duration] = None
            item['created_at'] = metrics.parse_ts(item.get('created_at'))
            selected.append(item)
        speed = metrics.speed_to_lead_metrics(selected, end, start, end - timedelta(microseconds=1))
        aged = [e for e in selected if e['created_at'] and e['created_at'] <= end-timedelta(hours=24)]
        contacted = sum(e.get('first_outbound_at') is not None for e in aged)
        duration_key = 'first_human_touch_minutes' if speed['speed_kind_known'] else 'first_touch_minutes'
        values = {
            'new_leads': len(cohort) if volume_ok else None,
            'unassigned': len(metrics.leads_unassigned(list(cohort.values()))) if volume_ok else None,
            'eligible': len(aged) if response_ok else None,
            'contacted': contacted if response_ok else None,
            'completion_pct': round(100*contacted/len(aged),1) if response_ok and aged else None,
            'uncontacted': speed['leads_uncontacted_24h'] if response_ok else None,
            'speed_median': speed['speed_to_lead_median_min'] if response_ok else None,
            'speed_p90': speed['speed_to_lead_p90_min'] if response_ok else None,
            'speed_samples': sum(e.get(duration_key) is not None for e in selected) if response_ok else None,
            'speed_human': speed['speed_kind_known'] if response_ok else None,
            'waiting': None, 'oldest_wait_hours': None,
        }
        if kind == 'attention':
            # Standing queues retain the established snapshot windows/rules.
            for target, source, sources in [
                ('uncontacted','leads_uncontacted_24h',('contacts','speed_to_lead')),
                ('waiting','convos_waiting',('conversations',)),
                ('oldest_wait_hours','convos_waiting_max_hours',('conversations',)),
                ('speed_median','speed_to_lead_median_min',('contacts','speed_to_lead')),
                ('speed_p90','speed_to_lead_p90_min',('contacts','speed_to_lead')),
                ('speed_human','speed_kind_known',('contacts','speed_to_lead')),
            ]:
                values[target] = snapshot.get(source) if complete(snapshot,*sources) else None
            key = 'first_human_touch_minutes' if values['speed_human'] else 'first_touch_minutes'
            values['speed_samples'] = sum(e.get(key) is not None for e in events
                if (stamp := metrics.parse_ts(e.get('created_at'))) is not None and start <= stamp < end) if response_ok else None
        data = {
            'schema_version': 1, 'formula_version': 'calendar-cohort-v1',
            'timezone': str(tz), 'period_start': start.isoformat(), 'period_end': end.isoformat(),
            'data_through': now.isoformat(), 'cohort_through': end.isoformat(),
            'to_date': kind == 'week' and start == week or kind == 'month' and start == month_start(cutoff),
            'coverage': {'contacts':volume_ok, 'responses':response_ok,
                         'conversations':kind == 'attention' and complete(snapshot,'conversations')},
            'metrics': values,
        }
        rows.append({'location_id':sub['location_id'],'period_kind':kind,'period_start':start.date().isoformat(),
                     'generated_at':now.astimezone(timezone.utc).isoformat(),'data':data})
    return rows
