"""Account-local weekly scheduling; collection and email delivery remain separate."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def local_instant(day, clock, zone):
    # Earliest fold on an ambiguous time; move forward through a nonexistent time.
    naive=datetime.combine(day,clock)
    for minutes in range(181):
        candidate=(naive+timedelta(minutes=minutes)).replace(tzinfo=zone,fold=0)
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)==candidate.replace(tzinfo=None):
            return candidate
    raise ValueError("Local send time unavailable")


def schedule(now, zone_name, weekday=0, clock="08:00:00"):
    zone=ZoneInfo(zone_name); local=now.astimezone(zone)
    monday=local.date()-timedelta(days=local.weekday())
    due=local_instant(monday+timedelta(days=weekday),time.fromisoformat(clock),zone)
    period_start=monday-timedelta(days=7)
    return {"period_start":period_start.isoformat(),
            "period_end":datetime.combine(monday,time.min,zone).isoformat(),
            "timezone":zone_name,"due_at":due.astimezone(timezone.utc).isoformat(),
            "expires_at":(due.astimezone(timezone.utc)+timedelta(hours=24)).isoformat()}


def next_due(now, zone_name, weekday=0, clock="08:00:00"):
    current=schedule(now,zone_name,weekday,clock)
    due=datetime.fromisoformat(current["due_at"])
    return due if due>now else datetime.fromisoformat(schedule(now+timedelta(days=7),zone_name,weekday,clock)["due_at"])
