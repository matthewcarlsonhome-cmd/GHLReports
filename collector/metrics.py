"""Pure metric functions for spec section 4 (v3). No I/O, no network, no clock
reads: "now" is always passed in so runs and tests are reproducible.

All timestamps are parsed to timezone-aware UTC datetimes; window math happens
in the location's local timezone. Missing data yields None (rendered "Unknown"
upstream), never zero.

How this fits in:
    fetchers.py hands this module clean, PII-safe dicts; everything here is
    plain arithmetic on those dicts, and flags.py turns the resulting
    metrics into alerts.

Key ideas to understand this file:
  * Injected clock: no function ever calls "what time is it now?" itself —
    `now_utc` is always a parameter. That makes every computation
    reproducible: tests (and re-runs) pass a fixed timestamp and get the
    exact same answer every time.
  * Timezone-aware datetimes: a Python datetime can be "naive" (no
    timezone) or "aware" (carries one). Naive/aware values cannot be
    compared, so parse_ts() makes everything aware (UTC by default), and
    business-day logic converts to the location's local zone.
  * Timestamp formats: inputs arrive as ISO 8601 strings
    ("2026-08-18T14:00:00Z"), epoch seconds, or epoch milliseconds
    (seconds/milliseconds since 1970-01-01 UTC); parse_ts accepts all three.
  * The 7-full-local-day window: "this week" means the 7 complete local
    calendar days ending yesterday at 23:59:59. Today is excluded because
    it is still in progress — including a partial day would make every
    metric look like a drop.
  * Baseline: the 28 days *before* that window, split into four weekly
    buckets and averaged. "Is this week down?" is always asked relative to
    the account's own recent history, not an absolute number.
  * Cohort: a group selected by when they were created (e.g. contacts added
    14-42 days ago), then followed to see what happened to them.
"""

from __future__ import annotations

import math
from datetime import datetime, date, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Chicago"

# Contacts whose name contains one of these whole words are test/demo data,
# excluded from every metric (see _has_excluded_word for the whole-word rule).
EXCLUDE_WORDS = {"test", "testing", "demo", "sample"}
EXCLUDE_TAG = "internal-staff"
REVIEW_TAG = "review-request"

# Message `source` values that tell a human touch apart from an automated one.
HUMAN_SOURCES = {"app", "manual", "user"}
AUTOMATION_SOURCES = {"workflow", "campaign", "bulk_actions", "api"}

SHOWED_STATUSES = {"showed"}
NOSHOW_STATUSES = {"noshow", "no_show", "no-show"}

# VERIFY with --probe: the meta.call.status vocabulary for calls nobody answered
MISSED_CALL_STATUSES = {"no-answer", "noanswer", "no_answer", "missed", "busy", "failed", "voicemail"}


# -- time parsing and windows -------------------------------------------


def parse_ts(value) -> datetime | None:
    """Tolerant timestamp parse: ISO 8601 (with/without Z), epoch ms, epoch s.

    Always returns an *aware* UTC datetime or None — never raises, never
    returns naive. The `> 1e11` test distinguishes epoch milliseconds from
    epoch seconds (1e11 seconds is the year 5138, so any number that large
    must be milliseconds).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            seconds = float(value) / 1000.0 if float(value) > 1e11 else float(value)
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return parse_ts(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def get_tz(tz_name: str | None) -> ZoneInfo:
    """Resolve an IANA timezone name (e.g. "America/Denver"), falling back to
    the default on None, typos, or unknown names — never raises."""
    try:
        return ZoneInfo(tz_name or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def window_7d(now_utc: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """7 full local days ending yesterday 23:59:59, as aware datetimes.

    "Local" is the account's timezone: convert now to local, find local
    midnight, then step back one second for the window end and seven days
    for the start. Today is deliberately excluded (see module docstring).
    """
    local_now = now_utc.astimezone(tz)
    today_start = datetime.combine(local_now.date(), dtime.min, tzinfo=tz)
    end = today_start - timedelta(seconds=1)
    start = today_start - timedelta(days=7)
    return start, end


def baseline_weeks(win_start: datetime) -> list[tuple[datetime, datetime]]:
    """The four 7-day buckets of the baseline window [win_start-28d, win_start),
    oldest first.

    Each tuple is (start, end) where end is exclusive; the [::-1] flips the
    newest-first construction into oldest-first order.
    """
    return [
        (win_start - timedelta(days=7 * (i + 1)), win_start - timedelta(days=7 * i))
        for i in range(4)
    ][::-1]


def rolling_window(now_utc: datetime, tz: ZoneInfo, days: int) -> tuple[datetime, datetime]:
    """Start of the local day `days` ago through now (used for fetch ranges)."""
    local_now = now_utc.astimezone(tz)
    today_start = datetime.combine(local_now.date(), dtime.min, tzinfo=tz)
    return today_start - timedelta(days=days), local_now


def in_window(ts: datetime | None, start: datetime, end: datetime) -> bool:
    """True if ts exists and lies in [start, end] (both bounds inclusive)."""
    return ts is not None and start <= ts <= end


def iso_week_start(day: date) -> date:
    """The Monday of the ISO week containing `day` (weekday() is Mon=0)."""
    return day - timedelta(days=day.weekday())


def business_days_between(start: date, end: date) -> int:
    """Weekdays (Mon-Fri) in the range (start, end] — i.e. how many business
    days have ELAPSED since `start` as of `end`. Saturday and Sunday never
    count, so a form whose last submission was Friday reads 1 elapsed day on
    Monday, not 3 — the weekend-false-alarm fix over a naive day count."""
    if end <= start:
        return 0
    days = 0
    day = start
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days += 1
    return days


FORM_NEW_DAYS = 30  # a form this young with zero submissions is 'new', not 'no_leads'


def classify_form(total: int | None, last_at, created_at, today: date,
                  silent_days: int = 3) -> str:
    """Per-form/per-survey health status (docs/FORMS-INTEGRATION.md Phase 1).

    Port of the reviewed MLH checker's getStatus() with two fixes: silence is
    measured in BUSINESS days (weekend gaps don't count), and the threshold
    comes from the account's thresholds ("form_silent_days", default 3).

      unknown  — submission count could not be determined (never guesses)
      active   — has submissions, newest within the silence threshold
      silent   — had submissions before, nothing within the threshold
      new      — zero submissions but created in the last FORM_NEW_DAYS
      no_leads — zero submissions, older than FORM_NEW_DAYS
    """
    if total is None:
        return "unknown"
    last = parse_ts(last_at)
    if total > 0:
        if last is None:
            return "unknown"  # count says active-ish but no timestamp to judge by
        quiet = business_days_between(last.date(), today)
        return "active" if quiet < silent_days else "silent"
    created = parse_ts(created_at)
    if created is not None and (today - created.date()).days <= FORM_NEW_DAYS:
        return "new"
    return "no_leads"


# -- exclusions ----------------------------------------------------------


def _has_excluded_word(name: str | None) -> bool:
    """True if the name contains an EXCLUDE_WORDS entry as a *whole word*.

    Whole-word matching is the point: we split on non-alphanumeric characters
    and compare complete tokens, so "Test" and "demo-account" are excluded
    but a real customer named "Testani" survives (a plain substring check
    would wrongly drop them).
    """
    if not name:
        return False
    words = "".join(c if c.isalnum() else " " for c in name.lower()).split()
    return any(w in EXCLUDE_WORDS for w in words)


def is_excluded(contact: dict) -> bool:
    """Should this contact be dropped from all metrics? True for test-word
    names and for anyone tagged as internal staff."""
    if _has_excluded_word(contact.get("firstName")) or _has_excluded_word(contact.get("lastName")):
        return True
    tags = contact.get("tags") or []
    return any(str(t).strip().lower() == EXCLUDE_TAG for t in tags)


def filter_exclusions(contacts: list[dict]) -> tuple[list[dict], int]:
    """Split contacts into (kept, excluded_count); the count is reported so
    an unusually high exclusion rate is visible."""
    kept = [c for c in contacts if not is_excluded(c)]
    return kept, len(contacts) - len(kept)


# -- small stats ---------------------------------------------------------


def median(values: list[float]) -> float | None:
    """Middle value when sorted (mean of the two middles for even counts);
    None for an empty list. Preferred over the mean because one extreme
    outlier can't drag it."""
    vals = sorted(values)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return (vals[mid - 1] + vals[mid]) / 2.0


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile: ceil(p/100 * n).

    "p90 = 12 min" means 90% of values are at or below 12 minutes. The
    nearest-rank method always returns an actual observed value (no
    interpolation): sort, then take the value at rank ceil(p% of n),
    clamped to a valid index.
    """
    vals = sorted(values)
    if not vals:
        return None
    rank = max(1, min(len(vals), math.ceil(pct / 100.0 * len(vals))))
    return float(vals[rank - 1])


# -- leads, sources, and the live baseline --------------------------------


def contact_name(contact: dict) -> str:
    """Best display name: first+last, else company, else a placeholder."""
    name = " ".join(p for p in [contact.get("firstName"), contact.get("lastName")] if p)
    return name or contact.get("companyName") or contact.get("name") or "(no name)"


def contact_source(contact: dict) -> str:
    """Where the lead came from: the explicit source field first, then the
    marketing attribution (session source / UTM), else "unknown"."""
    source = contact.get("source")
    if source:
        return str(source)
    attribution = contact.get("attributionSource") or {}
    if isinstance(attribution, dict):
        alt = attribution.get("sessionSource") or attribution.get("utmSource")
        if alt:
            return str(alt)
    return "unknown"


def leads_in_window(contacts: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Contacts created (dateAdded) inside the window."""
    return [c for c in contacts if in_window(parse_ts(c.get("dateAdded")), start, end)]


def leads_by_source(contacts: list[dict]) -> dict[str, int]:
    """Count of leads per source, e.g. {"google": 5, "facebook": 2}."""
    out: dict[str, int] = {}
    for contact in contacts:
        source = contact_source(contact)
        out[source] = out.get(source, 0) + 1
    return out


def baseline_stats(contacts: list[dict], win_start: datetime,
                   earliest_added: datetime | None) -> dict:
    """Live baseline from CRM history (spec 4.1): the 28 days before the 7d
    window, bucketed into four weeks. A week counts toward trailing_n only if
    the account existed during it (earliest contact before the week's end);
    with no earliest date the account is assumed older than the baseline.

    Returns the trailing weekly average, how many weeks it rests on
    (trailing_n), and a per-source weekly average. Averages need at least 2
    countable weeks; below that they are None/{} — too little history to
    call anything a "drop".
    """
    weeks = baseline_weeks(win_start)
    weekly_counts: list[int] = []
    weekly_sources: list[dict[str, int]] = []
    trailing_n = 0
    for week_start, week_end in weeks:
        # A week only counts if the account existed during it — otherwise a
        # brand-new account would average in phantom zero-weeks.
        countable = earliest_added is None or earliest_added < week_end
        # week_end is exclusive; subtracting one second makes it work with
        # the inclusive in_window() check.
        bucket = [c for c in contacts
                  if in_window(parse_ts(c.get("dateAdded")), week_start, week_end - timedelta(seconds=1))]
        if countable:
            trailing_n += 1
            weekly_counts.append(len(bucket))
            weekly_sources.append(leads_by_source(bucket))
    trailing_avg = (sum(weekly_counts) / len(weekly_counts)) if trailing_n >= 2 else None

    # Same idea per source: total each source across the countable weeks,
    # then divide by trailing_n for a weekly average.
    source_totals: dict[str, float] = {}
    for bucket_sources in weekly_sources:
        for source, count in bucket_sources.items():
            source_totals[source] = source_totals.get(source, 0) + count
    source_weekly_avg = (
        {source: round(total / trailing_n, 2) for source, total in source_totals.items()}
        if trailing_n >= 2 else {}
    )
    return {
        "leads_trailing_avg": round(trailing_avg, 2) if trailing_avg is not None else None,
        "trailing_n": trailing_n,
        "leads_by_source_trailing": source_weekly_avg,
    }


def leads_delta_pct(leads_new_7d: int, trailing_avg: float | None) -> float | None:
    """Percent change of this week vs the baseline average.

    None when the baseline is missing or under 3 leads/week — with numbers
    that small, "2 leads instead of 1" would read as +100%, pure noise.
    """
    if trailing_avg is None or trailing_avg < 3:
        return None
    return round((leads_new_7d - trailing_avg) / trailing_avg * 100.0, 1)


def leads_unassigned(contacts_7d: list[dict]) -> list[dict]:
    """New leads that no team member owns — nobody is responsible for calling."""
    return [c for c in contacts_7d if not c.get("assignedTo")]


def missing_phone_pct(contacts_7d: list[dict]) -> float | None:
    """Percent of new leads with no phone number (via the has_phone boolean).
    None below 5 leads — a percentage of 2 people is meaningless."""
    if len(contacts_7d) < 5:
        return None
    missing = sum(1 for c in contacts_7d if not c.get("has_phone"))
    return round(missing / len(contacts_7d) * 100.0, 1)


def form_submission_stats(submissions: list[dict] | None, win_start: datetime,
                          win_end: datetime, trailing_n: int) -> dict:
    """7d count and baseline weekly average from a single fetch covering both
    windows. None submissions = source unavailable = both metrics null.

    `trailing_n` (from baseline_stats) says how many baseline weeks the
    account actually existed for; we only average over those.
    """
    if submissions is None:
        return {"form_submissions_7d": None, "form_submissions_trailing_avg": None}
    current = sum(1 for s in submissions
                  if in_window(parse_ts(s.get("createdAt")), win_start, win_end))
    weeks = baseline_weeks(win_start)
    weekly = [
        sum(1 for s in submissions
            if in_window(parse_ts(s.get("createdAt")), week_start, week_end - timedelta(seconds=1)))
        for week_start, week_end in weeks
    ]
    usable = weekly[-trailing_n:] if trailing_n else []
    avg = round(sum(usable) / len(usable), 2) if len(usable) >= 2 else None
    return {"form_submissions_7d": current, "form_submissions_trailing_avg": avg}


def peer_median(deltas: list[float]) -> tuple[float | None, int]:
    """Median lead-delta across peer accounts, used to tell "everyone is down"
    (seasonal) from "just this account is down". None under 4 peers."""
    vals = [d for d in deltas if d is not None]
    if len(vals) < 4:
        return None, len(vals)
    return round(median(vals), 1), len(vals)


# -- speed to lead -------------------------------------------------------


def classify_outbound(message: dict) -> str:
    """Was this outbound message sent by a human or an automation?

    A userId means a person clicked send. Otherwise the message `source`
    decides; anything unrecognized is "unknown", which downstream treats as
    "we can't tell humans from bots for this account".
    """
    if message.get("userId"):
        return "human"
    source = str(message.get("source") or "").strip().lower()
    if source in HUMAN_SOURCES:
        return "human"
    if source in AUTOMATION_SOURCES:
        return "automation"
    return "unknown"


def _is_outbound(message: dict) -> bool:
    """True if the message went from the business to the lead."""
    return str(message.get("direction") or "").strip().lower() == "outbound"


def lead_event(contact: dict, messages: list[dict]) -> dict:
    """First-touch record for one contact across all its conversations' messages.
    Names, IDs, and timestamps only — never phone, email, or message text.

    "First touch" = the earliest outbound message of any kind; "first human
    touch" = the earliest one a person sent. Both are also expressed as
    minutes since the contact was created (the speed-to-lead numbers).
    """
    created = parse_ts(contact.get("dateAdded"))
    # Timestamped outbound messages, oldest first.
    outbound = [(parse_ts(m.get("dateAdded")), m) for m in messages if _is_outbound(m)]
    outbound = [(ts, m) for ts, m in outbound if ts is not None]
    outbound.sort(key=lambda pair: pair[0])

    first_outbound_at = outbound[0][0] if outbound else None
    first_outbound_kind = classify_outbound(outbound[0][1]) if outbound else None
    human = [(ts, m) for ts, m in outbound if classify_outbound(m) == "human"]
    first_human_at = human[0][0] if human else None

    def minutes_since(ts):
        """Minutes from contact creation to ts; None if either side is missing."""
        if ts is None or created is None:
            return None
        return round((ts - created).total_seconds() / 60.0, 1)

    return {
        "contact_id": contact.get("id"),
        "contact_name": contact_name(contact),
        "source": contact_source(contact),
        "created_at": created,
        "first_outbound_at": first_outbound_at,
        "first_outbound_kind": first_outbound_kind,
        "first_human_touch_at": first_human_at,
        "first_touch_minutes": minutes_since(first_outbound_at),
        "first_human_touch_minutes": minutes_since(first_human_at),
    }


def speed_to_lead_metrics(events: list[dict], now_utc: datetime,
                          win_start: datetime, win_end: datetime) -> dict:
    """Uncontacted / no-human-touch counts and median/p90 minutes over the 7d window.

    `kinds_known` guards the human-vs-automation split: if every classified
    first touch came back "unknown", this account's data can't support the
    distinction, so human-only metrics report None instead of a bogus zero.
    """
    kinds_seen = {e.get("first_outbound_kind") for e in events if e.get("first_outbound_at")}
    kinds_known = bool(kinds_seen) and kinds_seen != {"unknown"}

    # Only leads at least 24h old can be called "uncontacted for 24h" —
    # a lead created an hour ago hasn't had a fair chance yet.
    cutoff_24h = now_utc - timedelta(hours=24)
    in_win = [e for e in events if in_window(e.get("created_at"), win_start, win_end)]
    aged = [e for e in in_win if e.get("created_at") and e["created_at"] <= cutoff_24h]

    uncontacted = [e for e in aged if e.get("first_outbound_at") is None]
    no_human = None
    if kinds_known:
        no_human = [
            e for e in aged
            if e.get("first_outbound_at") is not None and e.get("first_human_touch_at") is None
        ]

    # Speed samples: prefer time-to-human when we can identify humans,
    # otherwise fall back to time-to-any-response.
    if kinds_known:
        samples = [e["first_human_touch_minutes"] for e in in_win
                   if e.get("first_human_touch_minutes") is not None]
    else:
        samples = [e["first_touch_minutes"] for e in in_win
                   if e.get("first_touch_minutes") is not None]

    return {
        "leads_uncontacted_24h": len(uncontacted),
        "leads_no_human_touch_7d": len(no_human) if no_human is not None else None,
        "speed_to_lead_median_min": median(samples),
        "speed_to_lead_p90_min": percentile(samples, 90),
        "speed_kind_known": kinds_known,
        "uncontacted_events": uncontacted,
    }


# -- conversations waiting (weekend rule) --------------------------------


def wait_clock_start(inbound_utc: datetime, tz: ZoneInfo) -> datetime:
    """Inbound landing Friday >=17:00 local, or Sat/Sun, starts the clock the
    following Monday 09:00 local; otherwise the clock starts at arrival.

    The weekend rule keeps Monday reports fair: without it, every message
    that arrived Saturday would show 40+ "waiting hours" that nobody was
    expected to be working.
    """
    local = inbound_utc.astimezone(tz)
    wd = local.weekday()  # Mon=0 .. Sun=6
    weekendish = (wd == 4 and local.hour >= 17) or wd in (5, 6)
    if not weekendish:
        return inbound_utc
    # Days until Monday: Friday evening -> +3, Saturday -> +2, Sunday -> +1.
    days_to_monday = {4: 3, 5: 2, 6: 1}[wd]
    monday = local.date() + timedelta(days=days_to_monday)
    start_local = datetime.combine(monday, dtime(hour=9), tzinfo=tz)
    return start_local.astimezone(timezone.utc)


def waiting_hours(last_inbound_utc: datetime, now_utc: datetime, tz: ZoneInfo) -> float:
    """Hours a customer has been waiting for a reply, weekend-adjusted.
    Clamped at 0: a Saturday message hasn't "waited" yet on Sunday, since
    its clock only starts Monday 09:00."""
    start = wait_clock_start(last_inbound_utc, tz)
    return max(0.0, round((now_utc - start).total_seconds() / 3600.0, 1))


def waiting_conversations(conversations: list[dict], now_utc: datetime, tz: ZoneInfo,
                          since_utc: datetime, min_hours: float = 4.0) -> list[dict]:
    """Conversations whose last message is inbound and has waited >= min_hours.

    "Last message is inbound" means the customer spoke last and is still
    waiting on us. Result is sorted longest-waiting first so the worst case
    leads the report.
    """
    out = []
    for convo in conversations:
        direction = str(convo.get("lastMessageDirection") or "").strip().lower()
        if direction != "inbound":
            continue
        last = parse_ts(convo.get("lastMessageDate"))
        if last is None or last < since_utc:
            continue
        hours = waiting_hours(last, now_utc, tz)
        if hours < min_hours:
            continue
        out.append({
            "conversation_id": convo.get("id"),
            "contact_id": convo.get("contactId"),
            "contact": convo.get("contactName") or "(no name)",
            "channel": convo.get("lastMessageType") or convo.get("type"),
            "hours": hours,
            "last_inbound_at": last,
        })
    out.sort(key=lambda w: w["hours"], reverse=True)
    return out


def convos_active_7d(conversations: list[dict], start: datetime, end: datetime) -> int:
    """How many conversations had any message during the window."""
    return sum(1 for c in conversations if in_window(parse_ts(c.get("lastMessageDate")), start, end))


# -- pipeline ------------------------------------------------------------

# Candidate "last activity" timestamps on an opportunity, best-first. Not
# every account populates every field, so idle time uses the newest one found.
IDLE_FIELDS = ("lastActionDate", "lastStatusChangeAt", "updatedAt")


def opp_idle(opp: dict, now_utc: datetime) -> tuple[float | None, str | None]:
    """(days since the deal was last touched, which field said so).

    Takes the *most recent* of the IDLE_FIELDS timestamps — using the most
    generous evidence of activity avoids calling a deal stale unfairly.
    (None, None) if no field parses.
    """
    best_ts, best_field = None, None
    for field in IDLE_FIELDS:
        ts = parse_ts(opp.get(field))
        if ts is not None and (best_ts is None or ts > best_ts):
            best_ts, best_field = ts, field
    if best_ts is None:
        return None, None
    return max(0.0, (now_utc - best_ts).total_seconds() / 86400.0), best_field


def opp_days_in_stage(opp: dict, now_utc: datetime) -> float | None:
    """Days since the deal last moved to a different pipeline stage."""
    ts = parse_ts(opp.get("lastStageChangeAt"))
    if ts is None:
        return None
    return max(0.0, (now_utc - ts).total_seconds() / 86400.0)


def opp_next_step(opp: dict, now_utc: datetime, feature_available: bool) -> str:
    """Does this deal have a planned next step?

    "task" = an open task exists, "event" = a future appointment exists,
    "none" = neither (the bad case), "unknown" = this account's API never
    returns task data so we cannot judge (feature_available is False).
    """
    if not feature_available:
        return "unknown"
    tasks = opp.get("tasks") or []
    if any(not t.get("completed") for t in tasks):
        return "task"
    events = opp.get("calendarEvents") or []
    for event in events:
        start = parse_ts(event.get("startTime"))
        if start is not None and start > now_utc:
            return "event"
    return "none"


def pipeline_metrics(opps: list[dict], now_utc: datetime,
                     win_start: datetime, win_end: datetime,
                     stale_days: float = 14.0, stuck_days: float = 30.0) -> dict:
    """All pipeline health numbers in one pass over the opportunities.

    Terms: "stale" = open deal untouched for stale_days+; "stuck" = open deal
    sitting in the same stage for stuck_days+; win rate and days-to-close use
    the deals closed in the last 90 days.
    """
    open_opps = [o for o in opps if str(o.get("status") or "").lower() == "open"]
    # True if any API response included task/event keys (see _clean_opportunity).
    feature_available = any(o.get("_has_task_keys") for o in opps)

    stale, stale_value = [], 0.0
    stuck = 0
    missing_value = 0
    no_next_step = 0
    open_value = 0.0
    per_opp: list[dict] = []

    for opp in open_opps:
        # A missing or zero dollar value both count as "missing" — a $0 deal
        # can't be prioritized either.
        value = opp.get("monetaryValue")
        numeric_value = float(value) if isinstance(value, (int, float)) else None
        if numeric_value:
            open_value += numeric_value
        if not numeric_value:
            missing_value += 1

        idle_days, idle_field = opp_idle(opp, now_utc)
        stage_days = opp_days_in_stage(opp, now_utc)
        next_step = opp_next_step(opp, now_utc, feature_available)

        is_stale = idle_days is not None and idle_days >= stale_days
        if is_stale:
            stale.append(opp)
            if numeric_value:
                stale_value += numeric_value
        if stage_days is not None and stage_days >= stuck_days:
            stuck += 1
        if feature_available and next_step == "none":
            no_next_step += 1

        per_opp.append({
            "opp": opp,
            "idle_days": idle_days,
            "idle_field": idle_field,
            "stage_days": stage_days,
            "next_step": next_step,
            "value": numeric_value,
            "is_stale": is_stale,
        })

    def closed_in(status: str, start: datetime, end: datetime) -> list[dict]:
        """Deals with this closed status whose status change fell in the range."""
        return [o for o in opps
                if str(o.get("status") or "").lower() == status
                and in_window(parse_ts(o.get("lastStatusChangeAt")), start, end)]

    won_7d = len(closed_in("won", win_start, win_end))
    lost_7d = len(closed_in("lost", win_start, win_end))

    # Win rate needs at least 5 closed deals; below that a single deal would
    # swing the percentage by 20+ points.
    cutoff_90 = now_utc - timedelta(days=90)
    won_90 = closed_in("won", cutoff_90, now_utc)
    lost_90 = closed_in("lost", cutoff_90, now_utc)
    win_rate = (round(len(won_90) / (len(won_90) + len(lost_90)) * 100.0, 1)
                if len(won_90) + len(lost_90) >= 5 else None)

    close_days = []
    for opp in won_90:
        created = parse_ts(opp.get("createdAt"))
        closed = parse_ts(opp.get("lastStatusChangeAt"))
        if created is not None and closed is not None and closed >= created:
            close_days.append((closed - created).total_seconds() / 86400.0)
    median_close = round(median(close_days), 1) if close_days else None

    lost_reasons: dict[str, int] = {}
    for opp in lost_90:
        reason = str(opp.get("lostReasonId") or "unspecified")
        lost_reasons[reason] = lost_reasons.get(reason, 0) + 1

    created_7d = sum(1 for o in opps if in_window(parse_ts(o.get("createdAt")), win_start, win_end))

    # Pipeline movement, last 30 days: is the client actually WORKING the
    # pipeline? A deal counts as moved if in the last 30 days it was created,
    # changed stage (open deals), or closed (won/lost). Zero movement with a
    # pipeline full of open deals is the "client stopped using GHL for
    # sales" smell — worth a call regardless of the stale count.
    cutoff_30 = now_utc - timedelta(days=30)
    moved_30d = 0
    for opp in opps:
        status = str(opp.get("status") or "").lower()
        stamps = [parse_ts(opp.get("createdAt"))]
        if status == "open":
            stamps.append(parse_ts(opp.get("lastStageChangeAt")))
        else:
            stamps.append(parse_ts(opp.get("lastStatusChangeAt")))
        if any(ts is not None and ts >= cutoff_30 for ts in stamps):
            moved_30d += 1

    return {
        "opps_open": len(open_opps),
        "opps_open_value": round(open_value, 2),
        "opps_stale": len(stale),
        "opps_stale_value": round(stale_value, 2),
        "opps_stuck": stuck,
        "opps_moved_30d": moved_30d,
        "opps_missing_value": missing_value,
        "opps_no_next_step": no_next_step if feature_available else None,
        "opps_won_7d": won_7d,
        "opps_lost_7d": lost_7d,
        "opps_created_7d": created_7d,
        "win_rate_90d": win_rate,
        "median_days_to_close_90d": median_close,
        "lost_reasons_90d": lost_reasons,
        "next_step_available": feature_available,
        "per_opp": per_opp,
    }


def lead_to_opp_pct(contacts: list[dict], opps: list[dict], now_utc: datetime) -> float | None:
    """Of contacts created 14 to 42 days ago, the percent with any opportunity
    whose contact id matches and createdAt >= the contact's dateAdded.

    The cohort (see module docstring) deliberately excludes the last 14 days:
    those leads haven't had time to convert yet, and counting them would
    understate the conversion rate. None under 5 cohort members.
    """
    start = now_utc - timedelta(days=42)
    end = now_utc - timedelta(days=14)
    # contact id -> when they were added (needed for the createdAt >= check).
    cohort = {}
    for contact in contacts:
        added = parse_ts(contact.get("dateAdded"))
        if added is not None and start <= added <= end and contact.get("id"):
            cohort[str(contact["id"])] = added
    if len(cohort) < 5:
        return None
    converted = set()
    for opp in opps:
        contact_id = str((opp.get("contact") or {}).get("id") or "")
        if contact_id not in cohort:
            continue
        created = parse_ts(opp.get("createdAt"))
        if created is not None and created >= cohort[contact_id]:
            converted.add(contact_id)
    return round(len(converted) / len(cohort) * 100.0, 1)


# -- appointments ----------------------------------------------------------


def appointment_metrics(events: list[dict], now_utc: datetime,
                        win_start: datetime, win_end: datetime) -> dict:
    """From client-calendar events fetched [-28d, +7d]. Booked = created field
    in the 7d window (VERIFY dateAdded vs createdAt); showed/no-show by
    appointmentStatus on events that started in the last 28 days.

    Two different windows on purpose: bookings are judged on the 7d report
    window, but show/no-show needs the longer 28d span to accumulate enough
    finished appointments. The rate needs 5+ outcomes, else None.
    """
    booked_7d = sum(1 for e in events
                    if in_window(parse_ts(e.get("dateAdded")), win_start, win_end))
    cutoff_28 = now_utc - timedelta(days=28)
    showed = noshow = 0
    for event in events:
        start = parse_ts(event.get("startTime"))
        if start is None or not (cutoff_28 <= start <= now_utc):
            continue
        status = str(event.get("appointmentStatus") or "").strip().lower()
        if status in SHOWED_STATUSES:
            showed += 1
        elif status in NOSHOW_STATUSES:
            noshow += 1
    rate = round(noshow / (showed + noshow) * 100.0, 1) if showed + noshow >= 5 else None
    return {
        "appts_booked_7d": booked_7d,
        "appts_showed_28d": showed,
        "appts_noshow_28d": noshow,
        "noshow_rate_28d": rate,
    }


# -- delivery ------------------------------------------------------------


def _publish_ts(post: dict) -> datetime | None:
    """Best-available publish time for a post, trying three field names."""
    return parse_ts(post.get("publishedAt") or post.get("createdAt") or post.get("updatedAt"))


def delivery_metrics(blog_posts: list[dict], social_posts: list[dict],
                     now_utc: datetime, services: list[str]) -> dict:
    """Are we (the agency) actually publishing content for this client?

    Only applies to accounts paying for content/social services; for others
    every value is None so no false "nothing published" alarm can fire.
    """
    applicable = bool({"content", "social"} & set(services or []))
    if not applicable:
        return {
            "delivery_applicable": False,
            "blogs_published_30d": None,
            "social_published_7d": None,
            "days_since_last_publish": None,
            "recent_publishes": [],
        }

    cutoff_30 = now_utc - timedelta(days=30)
    cutoff_7 = now_utc - timedelta(days=7)

    # Pair each post with its parsed publish timestamp, dropping unparseable ones.
    blog_stamps = [(ts, p) for p in blog_posts if (ts := _publish_ts(p)) is not None]
    social_stamps = [(ts, p) for p in social_posts if (ts := _publish_ts(p)) is not None]

    blogs_30 = sum(1 for ts, _ in blog_stamps if ts >= cutoff_30)
    social_7 = sum(1 for ts, _ in social_stamps if ts >= cutoff_7)

    all_stamps = (
        [(ts, "blog", p) for ts, p in blog_stamps]
        + [(ts, "social", p) for ts, p in social_stamps]
    )
    all_stamps.sort(key=lambda item: item[0], reverse=True)

    if all_stamps:
        days_since = max(0, int((now_utc - all_stamps[0][0]).total_seconds() // 86400))
    else:
        days_since = 90  # "no publish found in 90d" — caller adds the coverage note

    recent = [
        {"kind": kind, "title": p.get("title"), "published_at": ts.isoformat()}
        for ts, kind, p in all_stamps[:50]
    ]
    return {
        "delivery_applicable": True,
        "blogs_published_30d": blogs_30,
        "social_published_7d": social_7,
        "days_since_last_publish": days_since,
        "recent_publishes": recent,
        "no_publish_found": not all_stamps,
    }


def social_account_stats(accounts: list[dict] | None) -> dict:
    """Connected/expired social account counts; None input (fetch failed)
    yields None metrics, not zeros."""
    if accounts is None:
        return {"social_accounts_total": None, "social_accounts_expired": None}
    expired = sum(1 for a in accounts if a.get("expired"))
    return {"social_accounts_total": len(accounts), "social_accounts_expired": expired}


# -- relationship (from the SSP parent account) --------------------------

# Invoice statuses that can never be "past due" — already paid, cancelled,
# or never actually issued.
PAST_DUE_EXCLUDED_STATUSES = {"paid", "void", "draft", "deleted"}


def past_due_invoices(invoices: list[dict], today: date) -> list[dict]:
    """Unpaid invoices whose due date has passed, most-overdue first.
    Compares calendar dates, not times: an invoice due today is not yet late."""
    out = []
    for inv in invoices:
        status = str(inv.get("status") or "").strip().lower()
        if status in PAST_DUE_EXCLUDED_STATUSES:
            continue
        due = parse_ts(inv.get("dueDate"))
        if due is None or due.date() >= today:
            continue
        amount = inv.get("amountDue")
        if not isinstance(amount, (int, float)):
            amount = inv.get("total")
        out.append({
            "invoice_id": inv.get("_id") or inv.get("id"),
            "number": inv.get("invoiceNumber"),
            "amount_due": float(amount) if isinstance(amount, (int, float)) else None,
            "due_date": due.date().isoformat(),
            "days_over": (today - due.date()).days,
            "status": status,
        })
    out.sort(key=lambda inv: inv["days_over"], reverse=True)
    return out


def relationship_metrics(client_invoices: list[dict], client_conversations: list[dict],
                         client_events: list[dict], now_utc: datetime, today: date) -> dict:
    """Health of the agency-client relationship itself (billing + contact).

    Inputs come from the agency's own parent account, where the client is a
    contact. A "touch" is any conversation activity or a past meeting; a
    future meeting counts as the next scheduled appointment instead.
    """
    overdue = past_due_invoices(client_invoices, today)
    amount = sum(inv["amount_due"] for inv in overdue if inv["amount_due"] is not None)

    touches: list[datetime] = []
    for convo in client_conversations:
        ts = parse_ts(convo.get("lastMessageDate"))
        if ts is not None:
            touches.append(ts)
    # Split calendar events on "now": past ones are touches, future ones are
    # upcoming appointments.
    future_events: list[datetime] = []
    for event in client_events:
        ts = parse_ts(event.get("startTime"))
        if ts is None:
            continue
        if ts <= now_utc:
            touches.append(ts)
        else:
            future_events.append(ts)

    last_touch = max(touches) if touches else None
    next_appt = min(future_events) if future_events else None
    last_touch_days = int((now_utc - last_touch).total_seconds() // 86400) if last_touch else None

    return {
        "invoices_past_due": len(overdue),
        "invoices_past_due_amount": round(amount, 2) if overdue else 0.0,
        "client_last_touch_days": last_touch_days,
        "client_next_appt_at": next_appt,
        "client_last_touch_at": last_touch,
        "past_due_detail": overdue,
    }


# -- review proxies --------------------------------------------------------


def review_proxies(tagged_contacts: list[dict], opps: list[dict], now_utc: datetime) -> dict:
    """Indirect review-process signals (we can't see actual reviews via API).

    Two proxies: contacts tagged for a review ask that haven't been touched
    in 7+ days (the ask stalled), and deals won in the last 30 days whose
    contact never got the review tag at all (the ask never happened).
    """
    cutoff_7 = now_utc - timedelta(days=7)
    cutoff_30 = now_utc - timedelta(days=30)

    stale = []
    for contact in tagged_contacts:
        updated = parse_ts(contact.get("dateUpdated"))
        if updated is not None and updated <= cutoff_7:
            stale.append({
                "contact_id": contact.get("id"),
                "name": contact_name(contact),
                "days_quiet": int((now_utc - updated).total_seconds() // 86400),
            })

    gap = []
    for opp in opps:
        if str(opp.get("status") or "").lower() != "won":
            continue
        won_at = parse_ts(opp.get("lastStatusChangeAt"))
        if won_at is None or won_at < cutoff_30:
            continue
        contact = opp.get("contact") or {}
        tags = [str(t).strip().lower() for t in (contact.get("tags") or [])]
        if REVIEW_TAG not in tags:
            gap.append({
                "opp_id": opp.get("id"),
                "name": opp.get("name") or "(no name)",
                "won_at": won_at.isoformat(),
                "contact_id": contact.get("id"),
            })

    return {
        "review_asks_stale": len(stale),
        "review_ask_gap": len(gap),
        "stale_detail": stale,
        "gap_detail": gap,
    }


# -- missed calls (Tier 2) --------------------------------------------------


def is_call_conversation(convo: dict) -> bool:
    """Cheap pre-filter: does this conversation look phone-related? Checks
    both type fields since either may carry e.g. "TYPE_CALL"."""
    kind = f"{convo.get('lastMessageType') or ''} {convo.get('type') or ''}".upper()
    return "CALL" in kind


def missed_calls_in_window(convo: dict, messages: list[dict],
                           start: datetime, end: datetime) -> list[dict]:
    """Inbound call messages nobody answered, within the window. One row per
    missed call: conversation/contact identifiers and a timestamp only.

    A "missed call" = call-type message + inbound + a call_status in the
    MISSED_CALL_STATUSES vocabulary ("no-answer", "voicemail", ...).
    """
    out = []
    for message in messages:
        if "CALL" not in str(message.get("type") or "").upper():
            continue
        if str(message.get("direction") or "").strip().lower() != "inbound":
            continue
        status = str(message.get("call_status") or "").strip().lower()
        if status not in MISSED_CALL_STATUSES:
            continue
        at = parse_ts(message.get("dateAdded"))
        if not in_window(at, start, end):
            continue
        out.append({
            "conversation_id": convo.get("id"),
            "contact_id": convo.get("contactId"),
            "contact": convo.get("contactName") or "(no name)",
            "at": at,
            "status": status,
        })
    return out


# -- change tracking -------------------------------------------------------


def flags_changed(today_codes: list[str], prior_codes: list[str]) -> tuple[list[str], list[str]]:
    """Set-diff of flag codes vs the previous run: (newly raised, resolved)."""
    today_set, prior_set = set(today_codes), set(prior_codes)
    return sorted(today_set - prior_set), sorted(prior_set - today_set)


# -- gate ----------------------------------------------------------------


def _normalize_name(name: str) -> str:
    """Lowercase, treat '&' and the word 'and' as the same, collapse runs of
    whitespace — so cosmetic spelling differences don't fail the identity
    gate ("AAA Pools & Spas" vs a configured "AAA Pools and Spas")."""
    lowered = name.strip().lower().replace("&", " and ")
    return " ".join(lowered.split())


def location_name_matches(api_name: str | None, configured_name: str) -> bool:
    """G1 identity check: is our configured name a case-insensitive substring
    of the API's location name? Guards against a token that quietly points at
    the wrong account. Both sides are normalized first ('&' == 'and',
    whitespace collapsed) so punctuation variants still match."""
    if not api_name:
        return False
    return _normalize_name(configured_name) in _normalize_name(api_name)


def gate_check(g1_ok: bool, coverage, leads_new_7d: int | None,
               convos_active: int | None, opps_created: int | None,
               prev_dead: list[tuple]) -> tuple[bool, list[str]]:
    """Should today's numbers be published at all? Returns (ok, reasons).

    Four gates, all must pass — G1 identity, G2 <2 unavailable, G3 <2
    partial, G4 sudden all-zero:
      G1: the token points at the location we think it does.
      G2: fewer than 2 data sources completely unavailable.
      G3: fewer than 2 data sources only partially scanned.
      G4: an all-zero read (no leads, conversations, or opportunities) is
          only believable if the last 3 runs were also all-zero; a *sudden*
          all-zero usually means the fetch broke, not the business.
    """
    reasons: list[str] = []
    if not g1_ok:
        reasons.append("G1: location identity check failed")
    if coverage.unavailable_count() >= 2:
        reasons.append(f"G2: {coverage.unavailable_count()} sources unavailable")
    if coverage.partial_count() >= 2:
        reasons.append(f"G3: {coverage.partial_count()} partial scans")

    # G4: prev_dead rows are (leads, convos, opps) tuples from prior runs,
    # newest first; `x or 0` treats None (unknown) the same as zero.
    all_zero = (leads_new_7d or 0) == 0 and (convos_active or 0) == 0 and (opps_created or 0) == 0
    if all_zero:
        prev_all_zero = (
            len(prev_dead) >= 3
            and all((row[0] or 0) == 0 and (row[1] or 0) == 0 and (row[2] or 0) == 0
                    for row in prev_dead[:3])
        )
        if not prev_all_zero:
            reasons.append("G4: sudden all-zero read (leads, conversations, opportunities)")

    return (not reasons), reasons


# -- lead history bucketing -------------------------------------------------


def weekly_lead_history(contacts: list[dict], submissions: list[dict] | None,
                        tz: ZoneInfo, weeks: list[date]) -> list[dict]:
    """Bucket kept contacts (and form submissions when available) into ISO
    weeks; `weeks` is a list of Monday dates to emit rows for.

    Each timestamp is converted to the location's local date before picking
    its Monday, so a Sunday-11pm lead lands in the local week, not UTC's.
    form_submissions stays None when the source was unavailable.
    """
    wanted = set(weeks)
    lead_buckets: dict[date, list[dict]] = {w: [] for w in wanted}
    for contact in contacts:
        added = parse_ts(contact.get("dateAdded"))
        if added is None:
            continue
        week = iso_week_start(added.astimezone(tz).date())
        if week in wanted:
            lead_buckets[week].append(contact)
    form_counts: dict[date, int] | None = None
    if submissions is not None:
        form_counts = {w: 0 for w in wanted}
        for submission in submissions:
            created = parse_ts(submission.get("createdAt"))
            if created is None:
                continue
            week = iso_week_start(created.astimezone(tz).date())
            if week in wanted:
                form_counts[week] += 1
    return [{
        "week_start": week.isoformat(),
        "leads": len(lead_buckets[week]),
        "leads_by_source": leads_by_source(lead_buckets[week]),
        "form_submissions": form_counts[week] if form_counts is not None else None,
    } for week in sorted(wanted)]


# -- deep links ----------------------------------------------------------
# URL builders for the GHL web app, so a flag can jump straight to the
# record it's about. Each falls back to a broader page when ids are missing.


def link_contact(base: str, location_id: str, contact_id: str | None) -> str:
    """Contact detail page, or the dashboard if we have no contact id."""
    if not contact_id:
        return link_dashboard(base, location_id)
    return f"{base}/v2/location/{location_id}/contacts/detail/{contact_id}"


def link_conversation(base: str, location_id: str, conversation_id: str | None,
                      contact_id: str | None = None) -> str:
    """Conversation view, falling back to the contact, then the dashboard."""
    if conversation_id:
        return f"{base}/v2/location/{location_id}/conversations/conversations/{conversation_id}"
    return link_contact(base, location_id, contact_id)


def link_opportunity(base: str, location_id: str, opp_id: str | None,
                     contact_id: str | None = None) -> str:
    """Opportunity in the pipeline list, falling back like link_conversation."""
    if opp_id:
        return f"{base}/v2/location/{location_id}/opportunities/list?opportunityId={opp_id}"
    return link_contact(base, location_id, contact_id)


def link_dashboard(base: str, location_id: str) -> str:
    """The location's main dashboard — the last-resort link target."""
    return f"{base}/v2/location/{location_id}/dashboard"
