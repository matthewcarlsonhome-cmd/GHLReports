"""Account manager notices: tell the AM once, then only when something changes.

The collector already knows, every morning, what is wrong at each client
account. This module decides which of those problems an account manager (AM)
should hear about today, writes ONE plain-language note per client account,
and POSTs it to a single GoHighLevel Inbound Webhook in SSP's own GHL account
(the "MC Account Health - Alerts" workflow), which emails it to the AM.
Design and go-live steps: docs/AM-NOTIFY-WORKFLOW-SPEC.md.

How this fits in
----------------
main.py calls notify_run() once per nightly run, after every flag row is
written. Nothing here reads the GHL API, and the only outbound call is
post_alert() to SSP's own workflow. A failure here never fails the run.

    nightly results --> build_issues()   what is wrong at each account today
                    --> observe()        update alert_state: new, still there, gone
                    --> due_items()      what the AM has not heard, or should again
                    --> compose()        one note per account: subject, HTML, text
                    --> caps + recipient checks --> POST (on) or log (dry)
                    --> commit() + save  remember who was told what, and when

AUTOMATION_WEBHOOKS is the kill switch: off (default) does nothing at all,
dry runs everything and logs the notes instead of posting them, on posts.
--notify-preview replays stored history the same way, in memory only.

Key ideas to understand this file
---------------------------------
* Alert types, not flag codes. AMs picked alert types (T2 leads going cold,
  T3 customers left waiting, T4 website capture broken, ...). NOTIFY_RULES
  maps each type onto flag codes and metrics that already exist, with how
  many nights to wait before telling, how often to remind, and how many
  reminders. An account only gets notes for the types listed in its
  subaccounts.alert_triggers (the pilot opt-in).
* Told once. Each issue (account + code + entity, e.g. one form) is a row in
  alert_state. It is announced once, after it shows up two nights running.
  After that the AM hears about it again only if it gets WORSE (turns red,
  or its number doubles), when a spaced reminder is due (a few at most), or
  as a "cleared" line once it goes away. That is what keeps the same email
  from arriving every day.
* One note per client per day, bundling everything for that client. At most
  PER_AM_DAILY_CAP notes per AM per day (the rest roll to tomorrow, oldest
  first); a run that would send more than RUN_BREAKER sends none, because
  that many at once means bad data, not real problems.
* Told whom? alert_state.notified_to records the audience that was told:
  the AM, the shadow-week redirect, or "dry:..." for a rehearsal. A new
  audience hears each open issue once, so a dry run or shadow week never
  uses up the real AM's first note.
* Acknowledge on the dashboard (flag_acks) silences first notes, reminders
  and cleared lines for that code while the snooze lasts. WORSE still sends:
  red is new information.
* Flat payload, no PII. GHL maps {{inboundWebhookRequest.field}} and cannot
  read nested JSON, so every value is a top-level string and the email is
  rendered here (body_html / body_text). Notes carry account names, counts,
  form names, page addresses and lead-source labels; never a lead's name,
  phone, email, message text, or a deal name (deal names are customer
  names). tests/test_pii.py guards this.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from urllib.parse import urlsplit

from . import flags as flags_mod
from . import lead_channels
from .digest import staff_address

# -- constants ----------------------------------------------------------------

EVENT = "am_account_health"          # the workflow's "Valid?" branch checks this
SCHEMA_VERSION = "2"                 # ...and this; bump both sides together
NOTICE_CODE = "AM_NOTICE"            # automation_sends.flag_code for account notes
DEFAULT_DASHBOARD_URL = "https://mlhaccountreports.netlify.app"

PER_AM_DAILY_CAP = 8                 # notes per AM per day; the rest wait a day
RUN_BREAKER = 25                     # more than this in one run = send nothing
FLAP_DAYS = 7                        # back within a week = same episode, no new "NEW"
CLEARED_TTL_DAYS = 14                # a cleared line older than this is dropped
OPEN_SINCE_DAYS = 7                  # first note on an older issue says "OPEN SINCE"
PAYLOAD_ITEMS = 5                    # item_1..item_5 in the payload
T3_MIN_WAITING = 5                   # T3: "five or more" customers waiting

# T4 only watches forms that capture leads from the client's own site.
# Facebook/Google ad forms go quiet whenever an ad is paused; that is an ad
# question, not "website capture broken".
WEBSITE_CHANNELS = frozenset({lead_channels.WEBSITE_FORM, lead_channels.LANDING_PAGE_FORM})
# A quiet form is only news when the silence is long for THAT form: at least
# FORM_QUIET_MIN_DAYS, and at least FORM_QUIET_GAP_FACTOR times its usual gap
# between leads. A form with one lead a month going quiet for a week is not
# broken; one with a lead a day going quiet for four days probably is.
FORM_QUIET_MIN_DAYS = 4
FORM_QUIET_GAP_FACTOR = 3
FORM_QUIET_MIN_LEADS = 3             # need a few leads in 30 days to know the usual gap
# The team retires a form by renaming it "ZZ-RETIRE ..." before deleting it
# two weeks later; a retiring form going quiet is the point, not a problem.
_RETIRING = re.compile(r"^\s*z{2}\W*retire", re.IGNORECASE)

SEVERITY_RANK = MappingProxyType({"info": 0, "amber": 1, "red": 2})


@dataclass(frozen=True)
class Rule:
    """How one flag code becomes AM notes.

    trigger        the alert type an account opts into (subaccounts.alert_triggers)
    min_severity   weaker readings are treated as "not present"
    confirm_runs   nights in a row before the first note (one-night blips never send)
    remind_every   days between reminders while the issue stays open
    max_reminders  after this many reminders the issue goes quiet (dashboard and
                   Monday digest only) unless it gets worse
    counted        the issue carries a number; a doubling of it counts as worse
    """
    trigger: str
    min_severity: str
    confirm_runs: int
    remind_every: int
    max_reminders: int
    counted: bool = False


# Frozen on purpose, like the old DAILY_CODES: widening what can email the
# team should be a reviewed commit, not a setting someone flips at 11pm.
NOTIFY_RULES = MappingProxyType({
    # T1 lead flow stopped
    "INTEGRATION_SUSPECT": Rule("T1", "red", 2, 3, 3),
    "LEADS_ZERO":          Rule("T1", "red", 2, 3, 3),
    "LEADS_DROP":          Rule("T1", "red", 2, 7, 2),
    "SOURCE_DROP":         Rule("T1", "red", 2, 7, 1),
    # T2 leads going cold: N+ leads with no call, text or email for a day
    "SLOW_RESPONSE":       Rule("T2", "amber", 2, 7, 2, counted=True),
    # T3 customers left waiting: 5+ customers waiting for a reply
    "CONVOS_WAITING":      Rule("T3", "amber", 2, 7, 2, counted=True),
    # T4 website capture broken
    "FORM_SILENT":         Rule("T4", "red", 2, 3, 3),
    "FORM_WENT_SILENT":    Rule("T4", "amber", 2, 14, 1),
    "FORM_CHECK_MISSED":   Rule("T4", "amber", 2, 7, 2),
    # T6 social disconnected
    "SOCIAL_DISCONNECTED": Rule("T6", "red", 1, 7, 2),
    # T7 pipeline frozen
    "PIPELINE_FROZEN":     Rule("T7", "red", 2, 14, 1),
})

# AM-facing names, shown on every item. T5 and T8 have no rules yet: nothing
# measures email delivery today, and "deal money parked" is a Monday-digest
# topic until the pipeline standards exist.
ALERT_TYPES = MappingProxyType({
    "T1": "Lead flow stopped",
    "T2": "Leads going cold",
    "T3": "Customers left waiting",
    "T4": "Website capture broken",
    "T5": "Emails not delivering",
    "T6": "Social disconnected",
    "T7": "Pipeline frozen",
    "T8": "Deal money parked",
})

# route = the fixed key the GHL workflow branches on (one branch per AM), and
# the name used in the greeting. Adding an AM = one line here + one branch in
# the workflow; anyone else routes to "other" (the workflow's None branch).
AM_ROUTES = MappingProxyType({
    "ldegner@smallscreenproducer.com": ("lauren", "Lauren"),
    "lhoffman@smallscreenproducer.com": ("lisa", "Lisa"),
    "mcarlson@smallscreenproducer.com": ("matthew", "Matthew"),
})

# Emoji, variation selectors and zero-width joiners. Client form and stage
# names are full of them and they make a subject line unreadable. Mirrors
# stripDecor() in web/src/lib/format.ts.
_DECOR = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️‍←-⇿⬀-⯿]")


# -- small helpers ---------------------------------------------------------------

def strip_decor(value) -> str:
    """Drop emoji/pictographs and collapse whitespace; '' for None."""
    if not value:
        return ""
    return " ".join(_DECOR.sub("", str(value)).split())


def mode_from_env(env=None) -> str:
    """Read AUTOMATION_WEBHOOKS: 'on' | 'dry' | 'off' (default 'off').

    Off is the default on purpose: a fresh deploy that has not been
    configured stays silent rather than guessing.
    """
    raw = ((env if env is not None else os.environ).get("AUTOMATION_WEBHOOKS") or "off")
    raw = raw.strip().lower()
    return raw if raw in ("on", "dry", "off") else "off"


def uses_mlh(sub: dict) -> bool:
    """Notes only go out for accounts whose team works in MLH: the team
    marks the others ads only / not in MLH / canceled (subaccounts.mlh_status,
    docs/ACCOUNT-USAGE.md). The SSP parent always counts."""
    return bool(sub.get("is_parent")) or (sub.get("mlh_status") or "active") == "active"


def _date(value) -> date | None:
    """A date from a date, datetime, or ISO string; None when unparseable."""
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _num(value) -> float | None:
    """A float from a number or numeric string (Postgres numeric arrives as
    either); None when missing or not a number."""
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    number = _num(value)
    return None if number is None else int(round(number))


def _plural(count: int, word: str, plural: str | None = None) -> str:
    return word if count == 1 else (plural or word + "s")


def _fmt_day(value: date) -> str:
    """'Sep 3' — month and day, no year (notes are about recent weeks)."""
    return f"{value:%b} {value.day}"


def _usually(avg, noun: str = "") -> str:
    """' (usually about 6 a week)' or '' when there is no baseline."""
    avg = _num(avg)
    if not avg or avg < 0.5:
        return ""
    return f" (usually about {round(avg)}{' ' + noun if noun else ''} a week)"


def _fmt_wait(hours) -> str:
    """Business-adjusted wait in words: '9 hours', '3 days'."""
    hours = _num(hours) or 0.0
    if hours < 48:
        count = max(1, round(hours))
        return f"{count} {_plural(count, 'hour')}"
    days = round(hours / 24)
    return f"{days} days"


def _page_link(url: str) -> tuple[str, str]:
    """(href, display text) for a client web page, or ('', '') if it is not
    a plain http(s) address. Stored page URLs already have their query
    strings removed (form_history.normalize_url), so no visitor details."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return "", ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return "", ""
    host = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
    path = parts.path.rstrip("/")
    return url.strip(), host + path


# -- what is wrong today: issues ---------------------------------------------------

@dataclass
class Issue:
    """One thing an AM might need to hear about, in AM words.

    code / entity_key   the issue's identity with the account (entity_key is
                        '' for account-level issues, a form id, or a lead
                        source label)
    short               subject-sized, with the number: "4 leads going cold"
    sentence            the finding in one plain sentence
    next_step           what the AM should do about it
    label               the entity's display name (form name, lead source)
    count               the number a doubling is judged on (T2, T3)
    link                the client web page a form lives on, if known
    """
    code: str
    entity_key: str
    severity: str
    short: str
    sentence: str
    next_step: str
    label: str = ""
    count: int | None = None
    link: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.code, self.entity_key)

    @property
    def rule(self) -> Rule:
        return NOTIFY_RULES[self.code]


@dataclass
class AccountDay:
    """One account's inputs for one night, from a live run or stored rows.

    metrics      the snapshot metric columns
    flags        that night's flag rows (only code/severity/entity/title are read)
    form_rows    form_health rows (kind, form_id, name, status, channel,
                 subs_30d, last_submission_at, page_url)
    form_checks  details.form_health.form_checks (weekly synthetic tests)
    """
    sub: dict
    metrics: dict
    flags: list
    form_rows: list
    form_checks: list
    gate_passed: bool
    today: date

    @property
    def location_id(self) -> str:
        return self.sub.get("location_id") or ""

    @classmethod
    def from_result(cls, result: dict, today: date) -> "AccountDay":
        """From main.run()'s in-memory result for one location."""
        details = result.get("details") or {}
        return cls(sub=result.get("sub") or {}, metrics=result.get("metrics") or {},
                   flags=result.get("flags") or [], form_rows=result.get("form_health") or [],
                   form_checks=(details.get("form_health") or {}).get("form_checks") or [],
                   gate_passed=bool(result.get("gate_passed")), today=today)


def _source_of(flag: dict) -> str:
    """The lead source a SOURCE_DROP flag is about. Newer rows carry it as
    the entity; older stored rows only have it in the title."""
    if flag.get("entity_type") == "source" and flag.get("entity_name"):
        return strip_decor(flag["entity_name"])
    title = flag.get("title") or ""
    return strip_decor(title.split(":", 1)[1]) if title.startswith("Source drop:") else ""


def _rate_text(per_30: int) -> str:
    """How often a form usually produces a lead, in words."""
    weekly = round(per_30 * 7 / 30)
    if weekly >= 2:
        return f"about {weekly} leads a week"
    if weekly == 1:
        return "about a lead a week"
    return f"about {per_30} leads a month"


def _quiet_website_form(row: dict, today: date) -> Issue | None:
    """T4: a website form that has gone quiet for longer than its own usual
    gap between leads (see FORM_QUIET_* above). None when it is fine."""
    if row.get("kind") != "form" or row.get("status") != "silent":
        return None
    if row.get("channel") not in WEBSITE_CHANNELS:
        return None
    name = strip_decor(row.get("name"))
    if not name or _RETIRING.match(name):
        return None
    last = _date(row.get("last_submission_at"))
    per_30 = _int(row.get("subs_30d")) or 0
    if last is None or per_30 < FORM_QUIET_MIN_LEADS:
        return None
    quiet = (today - last).days
    # Every lead in the last 30 days arrived before the silence began, so
    # the usual gap is the active part of the window divided by its leads.
    usual_gap = max(30 - quiet, 1) / per_30
    if quiet < max(FORM_QUIET_MIN_DAYS, FORM_QUIET_GAP_FACTOR * usual_gap):
        return None
    return Issue(
        "FORM_WENT_SILENT", str(row.get("form_id") or name), "amber",
        short=f'"{name}" form went quiet',
        sentence=(f'The "{name}" form usually sends {_rate_text(per_30)} and has sent '
                  f"none since {_fmt_day(last)}."),
        next_step=("Open the page it is on and send a test. If the test does not show up "
                   "in the account within a few minutes, the form needs fixing."),
        label=name, link=row.get("page_url") or "")


def build_issues(day: AccountDay) -> list[Issue]:
    """Everything NOTIFY_RULES covers that is wrong at this account today.

    Built for every rule regardless of the account's opt-in: state is kept
    for all collected accounts so an account that opts in later starts with
    real history. Readings below a rule's min_severity count as absent.
    """
    m = day.metrics
    th = flags_mod.merged_thresholds(day.sub.get("thresholds"))
    by_code: dict[str, list[dict]] = {}
    for flag in day.flags:
        by_code.setdefault(flag.get("code"), []).append(flag)
    issues: list[Issue] = []

    # T1 lead flow stopped. INTEGRATION_SUSPECT and LEADS_ZERO never fire
    # together (flags.py makes them mutually exclusive).
    avg = m.get("leads_trailing_avg")
    if by_code.get("INTEGRATION_SUSPECT"):
        issues.append(Issue(
            "INTEGRATION_SUSPECT", "", "red", "Nothing coming in at all",
            f"No new leads, messages or deals came in this week{_usually(avg, 'leads')}.",
            "Something is probably broken. Check their forms, phone line and ads "
            "before you call the client."))
    elif by_code.get("LEADS_ZERO"):
        issues.append(Issue(
            "LEADS_ZERO", "", "red", "No new leads this week",
            f"No new leads came in this week{_usually(avg)}.",
            "Check their forms, phone line and ads, then call the client."))
    for flag in by_code.get("LEADS_DROP", []):
        pct = abs(_int(m.get("leads_delta_pct")) or 0)
        issues.append(Issue(
            "LEADS_DROP", "", flag.get("severity") or "amber", f"Leads down {pct}%",
            f"Leads are down {pct}% from their usual week while similar clients held steady.",
            "Worth a call before they call you: ask what changed on their side "
            "(ads, season, staffing)."))
    for flag in by_code.get("SOURCE_DROP", []):
        source = _source_of(flag)
        if source:
            usual = (m.get("leads_by_source_trailing") or {}).get(source)
            issues.append(Issue(
                "SOURCE_DROP", source, flag.get("severity") or "amber",
                f"Leads from {source} stopped",
                f"No leads came in from {source} this week{_usually(usual)}.",
                "Check that the ad or channel behind it is still running.", label=source))

    # T2 leads going cold. Same threshold as the dashboard's SLOW_RESPONSE
    # (the account's slow_response_min), so the email and the dashboard name
    # the same issue and its Acknowledge button exists. The pilot accounts
    # set it to 2 ("two or more") in migration 0014.
    cold = _int(m.get("leads_uncontacted_24h"))
    if cold is not None and cold >= max(int(th["slow_response_min"]), 1):
        these = "this one has" if cold == 1 else f"these {cold} have"
        issues.append(Issue(
            "SLOW_RESPONSE", "", "red" if cold >= th["slow_response_red"] else "amber",
            f"{cold} {_plural(cold, 'lead')} going cold",
            f"{cold} {_plural(cold, 'lead')} from the past week "
            f"{'has' if cold == 1 else 'have'} had no call, text or email for more than a day.",
            f"Call the client today. Ask who follows up on new leads and whether {these} "
            "been contacted.",
            count=cold))

    # T3 customers left waiting: people who wrote in and got no reply yet
    # (weekend-adjusted, see metrics.waiting_conversations).
    waiting = _int(m.get("convos_waiting"))
    if waiting is not None and waiting >= T3_MIN_WAITING:
        hours = _num(m.get("convos_waiting_max_hours"))
        longest = f" The longest has waited {_fmt_wait(hours)}." if hours else ""
        issues.append(Issue(
            "CONVOS_WAITING", "", "red" if (hours or 0) >= th["convo_wait_red_hours"] else "amber",
            f"{waiting} customers waiting for a reply",
            f"{waiting} people who messaged the client are still waiting for a reply.{longest}",
            "Ask the client to answer them in their Conversations inbox today, and to "
            "close out any that need no reply.",
            count=waiting))

    # T4 website capture broken: every form silent, one website form quiet for
    # longer than usual, or the weekly synthetic test not arriving.
    if by_code.get("FORM_SILENT"):
        issues.append(Issue(
            "FORM_SILENT", "", "red", "Website forms stopped sending leads",
            f"None of their forms sent a lead this week"
            f"{_usually(m.get('form_submissions_trailing_avg'))}, though leads still arrive "
            "from other places.",
            "Check the website's main contact form today: send a test and make sure it "
            "shows up in the account."))
    for row in day.form_rows:
        quiet = _quiet_website_form(row, day.today)
        if quiet:
            issues.append(quiet)
    for check in flags_mod.missed_form_checks(day.form_checks, day.today,
                                              int(th["form_check_stale_days"])):
        name = strip_decor(check.get("name")) or "a form"
        issues.append(Issue(
            "FORM_CHECK_MISSED", str(check.get("form_id") or name), "amber",
            f'Weekly test of "{name}" did not arrive',
            f'Our weekly test of the "{name}" form did not show up in the account.',
            "Submit the form by hand. If that does not arrive either, the form needs "
            "fixing before real leads are lost.",
            label=name))

    # T6 social disconnected.
    if by_code.get("SOCIAL_DISCONNECTED"):
        count = _int(m.get("social_accounts_expired")) or 1
        issues.append(Issue(
            "SOCIAL_DISCONNECTED", "", "red", "Social account disconnected",
            f"{count} social {_plural(count, 'account')} disconnected, so scheduled posts "
            "are not going out.",
            "Reconnect it in Social Planner today."))

    # T7 pipeline frozen (red only: not one deal moved in 30 days).
    for flag in by_code.get("PIPELINE_FROZEN", []):
        open_ct = _int(m.get("opps_open")) or 0
        issues.append(Issue(
            "PIPELINE_FROZEN", "", flag.get("severity") or "amber", "Pipeline frozen",
            f"None of their {open_ct} open deals moved or closed in 30 days.",
            "Book a pipeline review with the client."))

    return [issue for issue in issues if SEVERITY_RANK.get(issue.severity, 0)
            >= SEVERITY_RANK[issue.rule.min_severity]]


def cleared_line(row: dict, day: AccountDay) -> str | None:
    """The "cleared" line for an issue that just went away, or None to close
    it quietly.

    Quiet closes are deliberate: an issue can disappear without anything
    being fixed (a silent form ages into "dormant" after 30 days, a zero-lead
    week turns into a different alert). Telling the AM "cleared" then would
    be false, so only a reading that shows real improvement produces a line.
    """
    code, m = row.get("code"), day.metrics
    label = row.get("entity_label") or "the form"
    th = flags_mod.merged_thresholds(day.sub.get("thresholds"))
    if code == "SLOW_RESPONSE":
        cold = _int(m.get("leads_uncontacted_24h"))
        if cold is None or cold >= th["slow_response_min"]:
            return None
        if cold == 0:
            return "Leads going cold: no lead from this week is waiting over a day for a first reply."
        return f"Leads going cold: down to {cold} {_plural(cold, 'lead')} waiting over a day for a first reply."
    if code == "CONVOS_WAITING":
        waiting = _int(m.get("convos_waiting"))
        if waiting is None or waiting >= T3_MIN_WAITING:
            return None
        if waiting == 0:
            return "Customers left waiting: nobody is waiting for a reply."
        return f"Customers left waiting: down to {waiting} waiting for a reply."
    if code == "FORM_WENT_SILENT":
        now = next((r for r in day.form_rows
                    if str(r.get("form_id")) == row.get("entity_key")), None)
        return f'"{label}" form: sending leads again.' if now and now.get("status") == "active" else None
    if code == "FORM_SILENT":
        return "Website forms: sending leads again." if (_int(m.get("form_submissions_7d")) or 0) > 0 else None
    if code == "FORM_CHECK_MISSED":
        checks = {str(c.get("form_id")) for c in day.form_checks}
        missed = {str(c.get("form_id")) for c in flags_mod.missed_form_checks(
            day.form_checks, day.today, int(th["form_check_stale_days"]))}
        key = row.get("entity_key")
        return f'Weekly test of "{label}": arriving again.' if key in checks and key not in missed else None
    if code in ("INTEGRATION_SUSPECT", "LEADS_ZERO"):
        return "New leads: coming in again." if (_int(m.get("leads_new_7d")) or 0) > 0 else None
    if code == "LEADS_DROP":
        delta = _num(m.get("leads_delta_pct"))
        return ("Leads: back near their usual level."
                if delta is not None and delta > th["lead_drop_pct"] else None)
    if code == "SOURCE_DROP":
        now = (m.get("leads_by_source_7d") or {}).get(row.get("entity_key"), 0)
        return f"Leads from {row.get('entity_key')}: coming in again." if (_num(now) or 0) > 0 else None
    if code == "SOCIAL_DISCONNECTED":
        return "Social accounts: reconnected." if _int(m.get("social_accounts_expired")) == 0 else None
    if code == "PIPELINE_FROZEN":
        return "Pipeline: deals are moving again." if (_int(m.get("opps_moved_30d")) or 0) > 0 else None
    return None


# -- remembering what happened: alert_state ------------------------------------------

def _new_row(location_id: str, issue: Issue, today: date, backlog: bool) -> dict:
    """A fresh alert_state row for an issue seen for the first time (or first
    time since it cleared more than FLAP_DAYS ago)."""
    return {
        "location_id": location_id, "code": issue.code, "entity_key": issue.entity_key,
        "trigger": issue.rule.trigger, "status": "pending", "severity": issue.severity,
        "first_seen": today, "last_seen": today, "seen_runs": 1, "absent_runs": 0,
        "notified_at": None, "last_notified": None, "reminders": 0,
        "notified_severity": None, "notified_to": None, "told_count": None,
        "last_count": issue.count, "entity_label": issue.label or None, "backlog": backlog,
        "resolved_at": None, "resolve_told": False, "cleared_text": None, "summary": None,
        "evaluated_on": today,
    }


def observe(rows: dict, issues: list[Issue], day: AccountDay,
            first_tracking: bool) -> tuple[set, set]:
    """Count one night's sightings into an account's rows (changes `rows` in
    place). Returns (changed keys, deleted keys).

    Present tonight: a new or long-cleared issue starts "pending"; one that
    cleared within FLAP_DAYS reopens quietly (same episode, so no second
    "NEW"); anything else counts one more night. A pending issue becomes
    "open" after its rule's confirm_runs nights in a row.
    Absent tonight: a pending issue is simply forgotten (never confirmed,
    nobody was told); an open one resolves after two absent nights in a row,
    and gets a cleared line if the AM had been told and it really improved.
    """
    today = day.today
    changed: set = set()
    deleted: set = set()
    present = {issue.key for issue in issues}
    for issue in issues:
        row = rows.get(issue.key)
        if row is None or (row["status"] == "resolved"
                           and (row.get("resolved_at") is None
                                or (today - row["resolved_at"]).days > FLAP_DAYS)):
            row = _new_row(day.location_id, issue, today, backlog=first_tracking)
        elif row["status"] == "resolved":
            row.update(status="open", seen_runs=1, absent_runs=0, resolved_at=None,
                       resolve_told=False, cleared_text=None)
        else:
            row["seen_runs"] += 1
            row["absent_runs"] = 0
        row.update(severity=issue.severity, last_seen=today, last_count=issue.count,
                   entity_label=issue.label or row.get("entity_label"),
                   trigger=issue.rule.trigger, evaluated_on=today)
        if row["status"] == "pending" and row["seen_runs"] >= issue.rule.confirm_runs:
            row["status"] = "open"
        rows[issue.key] = row
        changed.add(issue.key)

    for key, row in list(rows.items()):
        if key in present or row["status"] == "resolved":
            continue
        if row["status"] == "pending":
            del rows[key]
            deleted.add(key)
            continue
        row["absent_runs"] += 1
        row["evaluated_on"] = today
        if row["absent_runs"] >= 2:
            row["status"] = "resolved"
            row["resolved_at"] = today
            text = cleared_line(row, day) if row.get("notified_to") else None
            row["cleared_text"] = text
            row["resolve_told"] = text is None     # nothing to deliver = done
        changed.add(key)
    return changed, deleted


# -- what the AM should hear today -----------------------------------------------------

@dataclass
class Item:
    """One line of a note: an issue plus why it is in today's note.

    kind is "first" (this audience has not heard about it), "worse", or
    "reminder". `row` is a copy of the state row taken before today's note
    is recorded, which is what the label ("NEW", "STILL OPEN (day 8)") reads.
    """
    kind: str
    issue: Issue
    row: dict

    def chip(self, today: date) -> str:
        """The label in front of the item. First notes say how old the
        issue is: NEW (this week), OPEN SINCE <date>, or ALREADY OPEN (it was
        open on the first night this account was tracked, so its real start
        is unknown)."""
        row = self.row
        if self.kind == "worse":
            return "WORSE"
        if self.kind == "reminder":
            if row.get("backlog"):
                return "STILL OPEN"
            return f"STILL OPEN (day {(today - row['first_seen']).days + 1})"
        if row.get("backlog"):
            return "ALREADY OPEN"
        if (today - row["first_seen"]).days >= OPEN_SINCE_DAYS:
            return f"OPEN SINCE {_fmt_day(row['first_seen']).upper()}"
        return "NEW"

    def line(self, today: date) -> str:
        """Short one-line form, for item_1..item_5 and the logs."""
        return f"{self.chip(today)}: {self.issue.short}"

    def sentence(self) -> str:
        """The finding, plus what changed since the last note for worse and
        reminder items, so a follow-up never reads as a repeat."""
        told, now = self.row.get("told_count"), self.issue.count
        if self.kind in ("worse", "reminder") and told is not None and now is not None and now != told:
            return f"{self.issue.sentence} {'Up' if now > told else 'Down'} from {told} in our last note."
        return self.issue.sentence


def _is_worse(row: dict, rule: Rule) -> bool:
    """Worse than when the AM was last told: the severity rose (amber to
    red), or a counted number at least doubled (and grew by 3 or more, so
    2 -> 4 leads is not an alarm)."""
    if SEVERITY_RANK.get(row["severity"], 0) > SEVERITY_RANK.get(row.get("notified_severity") or "info", 0):
        return True
    told, now = row.get("told_count"), row.get("last_count")
    return bool(rule.counted and told and now is not None and now >= 2 * told and now >= told + 3)


def _item_order(item: Item):
    """Red first, then worse / first / reminder, then oldest first."""
    kind_rank = {"worse": 0, "first": 1, "reminder": 2}[item.kind]
    return (-SEVERITY_RANK.get(item.issue.severity, 0), kind_rank,
            item.row.get("first_seen") or date.max, item.issue.code, item.issue.entity_key)


def due_items(rows: dict, present: dict, today: date, audience: str,
              enabled: set, snoozed: set) -> list[Item]:
    """Items this audience should get today for one account, best first.

    Only open issues seen tonight, of an alert type the account opted into.
    Snoozed codes (dashboard Acknowledge) skip first notes and reminders but
    not WORSE.
    """
    items = []
    for key, issue in present.items():
        row = rows.get(key)
        rule = NOTIFY_RULES.get(issue.code)
        if row is None or rule is None or row["status"] != "open" or rule.trigger not in enabled:
            continue
        acked = issue.code in snoozed
        if row.get("notified_to") != audience:
            if acked:
                continue
            kind = "first"
        elif _is_worse(row, rule):
            kind = "worse"
        elif (row.get("last_notified") is not None
              and (today - row["last_notified"]).days >= rule.remind_every
              and row["reminders"] < rule.max_reminders):
            if acked:
                continue
            kind = "reminder"
        else:
            continue
        items.append(Item(kind, issue, dict(row)))
    items.sort(key=_item_order)
    return items


def pending_cleared(rows: dict, today: date, audience: str, enabled: set,
                    snoozed: set) -> tuple[list[dict], list[dict]]:
    """(cleared rows that can ride along today, cleared rows to drop).

    A cleared line goes only to the audience that was told about the issue,
    only while fresh (CLEARED_TTL_DAYS), and never for a snoozed code or an
    alert type the account no longer uses. Anything else is dropped (marked
    told) so it cannot surface weeks later.
    """
    ride, drop = [], []
    for row in rows.values():
        if row["status"] != "resolved" or row.get("resolve_told") or not row.get("cleared_text"):
            continue
        rule = NOTIFY_RULES.get(row["code"])
        fresh = row.get("resolved_at") and (today - row["resolved_at"]).days <= CLEARED_TTL_DAYS
        if (rule and rule.trigger in enabled and fresh and row.get("notified_to") == audience
                and row["code"] not in snoozed):
            ride.append(row)
        else:
            drop.append(row)
    ride.sort(key=lambda r: (r.get("resolved_at") or date.min, r["code"]))
    return ride, drop


def commit(rows: dict, items: list[Item], cleared: list[dict], today: date,
           audience: str) -> None:
    """Record that a note went out (or would have, in a dry run): who was
    told, when, at what severity and number. This is what the next nights
    compare against."""
    for item in items:
        row = rows[item.issue.key]
        if item.kind == "first":
            row["notified_at"] = today
            row["reminders"] = 0
        elif item.kind == "reminder":
            row["reminders"] += 1
        row.update(last_notified=today, notified_to=audience,
                   notified_severity=row["severity"], told_count=row.get("last_count"),
                   summary=item.line(today))
    for row in cleared:
        row["resolve_told"] = True


# -- settings: switches and recipients --------------------------------------------------

@dataclass(frozen=True)
class Settings:
    """Everything the run reads from the environment, checked once.

    mode            off / dry / on (AUTOMATION_WEBHOOKS)
    url             AUTOMATION_WEBHOOK_URL: a secret, never logged
    allowlist       AM_NOTIFY_ALLOWLIST: only these AM addresses get notes;
                    empty means nobody (fail closed)
    redirect        AM_NOTIFY_REDIRECT: send every note here instead (the
                    shadow week), labelled with the AM it was meant for
    dashboard_base  DASHBOARD_URL
    problems        reasons nothing may be sent; any entry stops the run
    """
    mode: str
    url: str
    allowlist: frozenset
    redirect: str
    dashboard_base: str
    problems: tuple

    @classmethod
    def from_env(cls, env=None, mode: str | None = None) -> "Settings":
        env = env if env is not None else os.environ
        mode = mode or mode_from_env(env)
        url = (env.get("AUTOMATION_WEBHOOK_URL") or "").strip()
        entries = [a.strip() for a in (env.get("AM_NOTIFY_ALLOWLIST") or "").split(",") if a.strip()]
        allowlist = frozenset(filter(None, (staff_address(a) for a in entries)))
        raw_redirect = (env.get("AM_NOTIFY_REDIRECT") or "").strip()
        redirect = staff_address(raw_redirect) or ""
        problems = []
        if mode == "on" and not url:
            problems.append("AUTOMATION_WEBHOOKS is on but AUTOMATION_WEBHOOK_URL is not set")
        if url:
            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.netloc or any(c.isspace() for c in url):
                # Never echo the value: it is a secret even when malformed.
                problems.append("AUTOMATION_WEBHOOK_URL is not a valid https address")
        if raw_redirect and not redirect:
            problems.append("AM_NOTIFY_REDIRECT is not a single staff address")
        if len(allowlist) != len(entries):
            problems.append(f"AM_NOTIFY_ALLOWLIST has {len(entries) - len(allowlist)} "
                            "entry(ies) that are not a single staff address")
        base = (env.get("DASHBOARD_URL") or DEFAULT_DASHBOARD_URL).strip().rstrip("/")
        return cls(mode, url, allowlist, redirect, base, tuple(problems))

    def audience(self, recipient: str) -> str:
        """What notified_to records: the recipient, marked when rehearsed."""
        return ("dry:" if self.mode == "dry" else "") + recipient


# -- a note for one account -----------------------------------------------------------------

@dataclass
class Notice:
    """One account's note for today, and what happened to it.

    status: "" (to send), sent, failed, dry, preview, held (recipient not
    allowed), deferred (over the per-AM cap; tries again tomorrow), breaker
    (the whole run was too big), already_sent (a note went out earlier today).
    """
    sub: dict
    items: list
    cleared: list
    am_email: str
    recipient: str
    audience: str
    first_note: bool
    today: date
    payload: dict | None = None
    status: str = ""
    error: str = ""

    @property
    def location_id(self) -> str:
        return self.sub.get("location_id") or ""

    @property
    def oldest(self) -> date:
        return min((i.row.get("first_seen") or self.today) for i in self.items)


def _first_name(address: str, sub: dict | None = None) -> str:
    """Greeting name: the AM_ROUTES name, else the first word of am_name."""
    if address in AM_ROUTES:
        return AM_ROUTES[address][1]
    if sub is not None:
        first = re.split(r"[\s/]+", (sub.get("am_name") or "").strip())[0]
        if first:
            return first
    return "there"


def _route(address: str) -> str:
    return AM_ROUTES[address][0] if address in AM_ROUTES else "other"


# Palette and type mirror the Monday digest (digest.py) so the two emails
# read as one product. Email HTML is 2005-vintage on purpose: nested tables,
# inline styles, solid colors, one 600px column (Gmail strips <style>,
# Outlook renders with Word). It is a fragment, not a full document, because
# the GHL workflow drops it into its own email body.
_INK = "#1d2b32"
_BODY_TX = "#3c4a50"
_MUTED = "#5b6a70"
_LINE = "#e3eae9"
_PAPER = "#eef2f1"
_STEP_BG = "#f4f7f6"
_RED = "#c43c3c"
_RED_SOFT = "#fbeaea"
_AMBER = "#a86f0a"
_AMBER_SOFT = "#faf0da"
_GREEN = "#0c7a3c"
_ACCENT = "#2a78d6"
_FONT = "Arial,Helvetica,sans-serif"

FOOTER_TEXT = ("How these notes work: you get at most one note per client a day, and only "
               "when something is new, gets worse, or is due for a reminder. Reminders "
               "stop after a few; anything still open stays on the dashboard and in the "
               "Monday summary. To stop reminders sooner, open the account and press "
               "Acknowledge on the item.")


def _intro(notice: Notice, account: str) -> str:
    if notice.first_note:
        return (f"This is the first Account Health note about {account}. It lists what is "
                "open right now. After this you will only hear when something is new, gets "
                "worse, or is due for a reminder.")
    return f"Here is what needs your attention at {account}:"


def _chip_html(item: Item, today: date) -> str:
    text = html.escape(item.chip(today))
    if item.kind == "worse":
        fg, bg = "#ffffff", _RED
    elif item.issue.severity == "red":
        fg, bg = _RED, _RED_SOFT
    else:
        fg, bg = _AMBER, _AMBER_SOFT
    return (f'<span style="display:inline-block;font-family:{_FONT};font-size:11px;'
            f'font-weight:700;letter-spacing:0.5px;color:{fg};background-color:{bg};'
            f'border-radius:10px;padding:3px 9px;">{text}</span>')


def _item_html(item: Item, today: date) -> str:
    issue = item.issue
    esc = html.escape
    stripe = _RED if issue.severity == "red" else _AMBER
    kind_name = esc(ALERT_TYPES.get(issue.rule.trigger, ""))
    href, shown = _page_link(issue.link)
    page = (f'<div style="font-family:{_FONT};font-size:12px;line-height:18px;color:{_MUTED};'
            f'padding-top:8px;">Page: <a href="{esc(href, quote=True)}" style="color:{_ACCENT};">'
            f'{esc(shown)}</a></div>') if href else ""
    return (
        f'<tr><td style="padding:8px 28px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border:1px solid {_LINE};border-radius:6px;border-collapse:separate;"><tr>'
        f'<td width="4" style="width:4px;background-color:{stripe};border-radius:6px 0 0 6px;"></td>'
        f'<td style="padding:14px 16px;">'
        f'<div>{_chip_html(item, today)}&nbsp;&nbsp;<span style="font-family:{_FONT};'
        f'font-size:12px;font-weight:700;color:{_MUTED};">{kind_name}</span></div>'
        f'<div style="font-family:{_FONT};font-size:16px;line-height:23px;font-weight:700;'
        f'color:{_INK};padding-top:8px;">{esc(item.sentence())}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin-top:10px;"><tr><td style="background-color:{_STEP_BG};border-radius:4px;'
        f'padding:10px 12px;font-family:{_FONT};font-size:14px;line-height:21px;color:{_INK};">'
        f'<b>Next step:</b> {esc(issue.next_step)}</td></tr></table>'
        f'{page}</td></tr></table></td></tr>')


def render_html(notice: Notice, account: str, greeting: str, redirected_from: str,
                dashboard_url: str) -> str:
    """The email body as an email-safe HTML fragment (see the palette note)."""
    esc = html.escape
    today = notice.today
    count = len(notice.items)
    summary = f"{count} {_plural(count, 'thing')} {'needs' if count == 1 else 'need'} your attention"
    shadow = (f'<br><span style="color:{_MUTED};font-size:13px;">This note would have gone to '
              f'{esc(redirected_from)}.</span>') if redirected_from else ""
    parts = [
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background-color:{_PAPER};"><tr><td align="center" style="padding:24px 12px;">',
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;max-width:600px;background-color:#ffffff;border:1px solid {_LINE};'
        f'border-radius:8px;border-collapse:separate;">',
        f'<tr><td style="padding:22px 28px 16px;border-bottom:1px solid {_LINE};">'
        f'<div style="font-family:{_FONT};font-size:11px;letter-spacing:1.5px;'
        f'text-transform:uppercase;font-weight:700;color:{_MUTED};">Account Health</div>'
        f'<div style="font-family:{_FONT};font-size:22px;line-height:28px;font-weight:700;'
        f'color:{_INK};padding-top:6px;">{esc(account)}</div>'
        f'<div style="font-family:{_FONT};font-size:14px;line-height:20px;color:{_BODY_TX};'
        f'padding-top:4px;">{summary}</div></td></tr>',
        f'<tr><td style="padding:18px 28px 8px;font-family:{_FONT};font-size:15px;'
        f'line-height:22px;color:{_INK};">Hi {esc(greeting)},{shadow}<br><br>'
        f'{esc(_intro(notice, account))}</td></tr>',
    ]
    parts += [_item_html(item, today) for item in notice.items]
    if notice.cleared:
        lines = "".join(
            f'<div style="font-family:{_FONT};font-size:14px;line-height:21px;color:{_BODY_TX};'
            f'padding-top:6px;"><span style="color:{_GREEN};font-weight:700;">&#10003;</span>'
            f'&nbsp; {esc(row["cleared_text"])}</div>' for row in notice.cleared)
        parts.append(
            f'<tr><td style="padding:14px 28px 4px;"><div style="font-family:{_FONT};'
            f'font-size:12px;letter-spacing:1.2px;text-transform:uppercase;font-weight:700;'
            f'color:{_GREEN};">Cleared since your last note</div>{lines}</td></tr>')
    parts += [
        f'<tr><td style="padding:20px 28px 6px;"><table role="presentation" cellpadding="0" '
        f'cellspacing="0" border="0"><tr><td style="background-color:{_ACCENT};border-radius:6px;">'
        f'<a href="{esc(dashboard_url, quote=True)}" style="display:inline-block;padding:12px 20px;'
        f'font-family:{_FONT};font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;">'
        f'Open {esc(account)} in Account Health</a></td></tr></table></td></tr>',
        f'<tr><td style="padding:14px 28px 22px;font-family:{_FONT};font-size:12px;'
        f'line-height:18px;color:{_MUTED};">{esc(FOOTER_TEXT)}</td></tr>',
        '</table></td></tr></table>',
    ]
    return "".join(parts)


def render_text(notice: Notice, account: str, greeting: str, redirected_from: str,
                dashboard_url: str) -> str:
    """The same note as plain text (the workflow's fallback if GHL escapes HTML)."""
    today = notice.today
    lines = [f"Hi {greeting},", ""]
    if redirected_from:
        lines += [f"(This note would have gone to {redirected_from}.)", ""]
    lines += [_intro(notice, account), ""]
    for number, item in enumerate(notice.items, 1):
        issue = item.issue
        lines.append(f"{number}. {item.chip(today)} - {ALERT_TYPES.get(issue.rule.trigger, '')}")
        lines.append(f"   {item.sentence()}")
        lines.append(f"   Next step: {issue.next_step}")
        href, shown = _page_link(issue.link)
        if href:
            lines.append(f"   Page: {href}")
        lines.append("")
    if notice.cleared:
        lines.append("Cleared since your last note:")
        lines += [f"- {row['cleared_text']}" for row in notice.cleared]
        lines.append("")
    lines += [f"Open {account} in Account Health: {dashboard_url}", "", FOOTER_TEXT]
    return "\n".join(lines)


def compose(notice: Notice, settings: Settings, is_test: bool = False) -> dict:
    """The flat webhook payload for one note. Every value is a string: GHL's
    If/Else compares strings reliably, and numbers-as-strings avoid type
    surprises in the field mapper."""
    sub = notice.sub
    today = notice.today
    account = strip_decor(sub.get("name")) or sub.get("slug") or notice.location_id
    redirected = bool(settings.redirect) and notice.recipient != notice.am_email
    meant_for = _first_name(notice.am_email, sub) if redirected else ""
    greeting = _first_name(notice.recipient, None if redirected else sub)
    dashboard_url = f"{settings.dashboard_base}/account/{notice.location_id}"
    top = notice.items[0].issue.short
    more = len(notice.items) - 1
    subject = f"{account}: {top}" + (f" (+{more} more)" if more else "")
    if redirected:
        subject = f"[for {meant_for}] {subject}"
    lines = [item.line(today) for item in notice.items]
    payload = {
        "event": EVENT,
        "schema_version": SCHEMA_VERSION,
        "is_test": "true" if is_test else "false",
        "send_id": f"{notice.location_id}-{today.isoformat()}",
        "run_date": today.isoformat(),
        "account_name": account,
        "location_id": notice.location_id,
        "am_email": notice.recipient,
        "am_first_name": greeting,
        "route": _route(notice.recipient),
        "urgency": "high" if any(i.issue.severity == "red" for i in notice.items) else "normal",
        "item_count": str(len(notice.items)),
        "subject": subject,
        "headline": top,
        "body_text": render_text(notice, account, greeting, meant_for, dashboard_url),
        "body_html": render_html(notice, account, greeting, meant_for, dashboard_url),
        "resolved_text": ("Cleared: " + "; ".join(r["cleared_text"] for r in notice.cleared)
                          if notice.cleared else ""),
        "dashboard_url": dashboard_url,
    }
    for number in range(1, PAYLOAD_ITEMS + 1):
        payload[f"item_{number}"] = lines[number - 1] if number <= len(lines) else ""
    return payload


# -- one night, all accounts ---------------------------------------------------------------

@dataclass
class RunPlan:
    """The outcome of evaluate(): updated state plus today's notes."""
    rows: dict                                      # location_id -> {key: row}
    changed: dict = field(default_factory=dict)     # location_id -> keys to save
    deleted: dict = field(default_factory=dict)     # location_id -> keys to delete
    newly_tracked: list = field(default_factory=list)
    notices: list = field(default_factory=list)

    def upserts(self) -> list[dict]:
        return [self.rows[loc][key] for loc, keys in self.changed.items()
                for key in keys if key in self.rows.get(loc, {})]

    def deletes(self) -> list[tuple[str, str, str]]:
        return [(loc, code, entity) for loc, keys in self.deleted.items()
                for code, entity in keys]


def group_rows(rows: list[dict]) -> dict:
    """alert_state rows from the store, grouped by location and keyed by
    (code, entity_key), with date columns parsed."""
    grouped: dict = {}
    for row in rows:
        row = dict(row)
        for column in ("first_seen", "last_seen", "notified_at", "last_notified",
                       "resolved_at", "evaluated_on"):
            row[column] = _date(row.get(column))
        row["entity_key"] = row.get("entity_key") or ""
        grouped.setdefault(row["location_id"], {})[(row["code"], row["entity_key"])] = row
    return grouped


def _enabled(sub: dict) -> set:
    return {str(t).strip().upper() for t in (sub.get("alert_triggers") or []) if str(t).strip()}


def evaluate(days: list[AccountDay], rows_by_loc: dict, snoozed_by_loc: dict,
             sent_today: set, settings: Settings, today: date) -> RunPlan:
    """Advance every account one night and build today's notes (no I/O).

    Shared by the nightly run, the dry run and --notify-preview so all
    three behave identically. Accounts whose data was held by the gate are
    skipped outright: bad data must not open or close issues.
    """
    plan = RunPlan(rows=rows_by_loc)
    for day in days:
        if not day.gate_passed:
            continue
        loc = day.location_id
        rows = rows_by_loc.setdefault(loc, {})
        issues = build_issues(day)
        present = {issue.key: issue for issue in issues}
        # A rerun of the same night must not count the night twice.
        if not any(row.get("evaluated_on") == today for row in rows.values()):
            first_tracking = not day.sub.get("alert_tracking_since") and not rows
            changed, deleted = observe(rows, issues, day, first_tracking)
            plan.changed.setdefault(loc, set()).update(changed)
            plan.deleted.setdefault(loc, set()).update(deleted)
            if first_tracking:
                plan.newly_tracked.append(loc)

        enabled = _enabled(day.sub)
        # No notes for accounts nobody works in MLH (state still updates).
        if not enabled or not uses_mlh(day.sub) or day.metrics.get("client_users") == 0:
            continue
        am_email = (day.sub.get("am_email") or "").strip().lower()
        recipient = settings.redirect or am_email
        audience = settings.audience(recipient)
        snoozed = snoozed_by_loc.get(loc, set())
        items = due_items(rows, present, today, audience, enabled, snoozed)
        ride, drop = pending_cleared(rows, today, audience, enabled, snoozed)
        for row in drop:
            row["resolve_told"] = True
            plan.changed.setdefault(loc, set()).add((row["code"], row["entity_key"]))
        if not items:
            continue                           # a cleared line never sends alone
        plan.notices.append(Notice(
            sub=day.sub, items=items, cleared=ride, am_email=am_email, recipient=recipient,
            audience=audience, today=today,
            first_note=not any(row.get("notified_to") == audience for row in rows.values()),
            status="already_sent" if loc in sent_today else ""))

    _apply_limits(plan.notices, settings)
    return plan


def _apply_limits(notices: list[Notice], settings: Settings) -> None:
    """Recipient checks, the per-AM cap (oldest first) and the run breaker."""
    for notice in notices:
        if notice.status:
            continue
        if (staff_address(notice.am_email) != notice.am_email
                or staff_address(notice.recipient) != notice.recipient
                or notice.am_email not in settings.allowlist):
            notice.status = "held"
    by_am: dict = {}
    for notice in notices:
        if not notice.status:
            by_am.setdefault(notice.am_email, []).append(notice)
    for group in by_am.values():
        group.sort(key=lambda n: (n.oldest, n.location_id))
        for notice in group[PER_AM_DAILY_CAP:]:
            notice.status = "deferred"
    ready = [n for n in notices if not n.status]
    if len(ready) > RUN_BREAKER:
        for notice in ready:
            notice.status = "breaker"


# -- delivery ----------------------------------------------------------------------------

def post_alert(url: str, payload: dict, timeout: float = 15.0) -> tuple[int | None, str | None]:
    """POST one payload. Returns (http_status, error). Retries once on a 5xx
    or network error; a 4xx would fail the same way again.

    Deliberately urllib rather than requests: this is the only outbound call
    in the collector that is not the GHL API, and it has no business sharing
    that client's session, retry ladder, or rate bucket. The URL itself is a
    secret and never appears in a log line or an error message.
    """
    body = json.dumps(payload).encode("utf-8")
    last_error = None
    for attempt in (1, 2):
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, None
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return exc.code, f"HTTP {exc.code}"
            last_error = f"HTTP {exc.code}"
        except Exception as exc:                       # network, DNS, timeout
            # Only the error's type goes on record: free text can quote the
            # URL, and the URL is a secret (logs and automation_sends.error
            # are readable by every staff member). SECURITY-SPEC SEC-12.
            last_error = type(exc).__name__
            if isinstance(exc, urllib.error.URLError) and not isinstance(exc.reason, str):
                last_error += f" ({type(exc.reason).__name__})"
        if attempt == 1:
            continue
    return None, last_error


def _audit(store, run_id, notice: Notice, status: str, http_status=None, error=None, log=print):
    """One automation_sends row per note attempt (live runs only)."""
    try:
        store.record_automation_send({
            "run_id": run_id,
            "location_id": notice.location_id,
            "snapshot_date": notice.today.isoformat(),
            "flag_code": NOTICE_CODE,
            "severity": "red" if any(i.issue.severity == "red" for i in notice.items) else "amber",
            "mode": "live",
            "status": status,
            "http_status": http_status,
            "error": (error or None) and str(error)[:500],
            "payload": notice.payload or {},
            "message_kind": "am_notice",
            "item_codes": [i.issue.code for i in notice.items],
        })
    except Exception as exc:
        log(f"notices: audit row not written ({type(exc).__name__})")


def _deliver(plan: RunPlan, settings: Settings, store, run_id, log) -> dict:
    """Send (or log) every note in the plan and record what happened.
    Returns the tally. Only delivered notes (and dry ones) are committed to
    state; a failed, held or deferred note stays due for tomorrow."""
    tally = {"sent": 0, "dry": 0, "failed": 0, "held": 0, "deferred": 0, "breaker": 0}
    breaker = sum(1 for n in plan.notices if n.status == "breaker")
    if breaker:
        log(f"notices: {breaker} notes in one run is over the {RUN_BREAKER} limit; "
            "none sent (check the data before anything else)")
    for notice in plan.notices:
        label = f"{notice.sub.get('slug') or notice.location_id} -> {notice.recipient or '(no AM)'}"
        if notice.status in ("held", "deferred", "breaker"):
            tally[notice.status] += 1
            why = {"held": "AM not on AM_NOTIFY_ALLOWLIST",
                   "deferred": f"over {PER_AM_DAILY_CAP} notes for this AM today; tries again tomorrow",
                   "breaker": "run breaker"}[notice.status]
            log(f"notices: {label}: not sent ({why})")
            if settings.mode == "on" and notice.status != "held":
                notice.payload = compose(notice, settings)
                _audit(store, run_id, notice, "skipped_cap", error=why, log=log)
            continue
        if notice.status == "already_sent":
            continue
        notice.payload = compose(notice, settings)
        if settings.mode == "dry":
            notice.status = "dry"
            tally["dry"] += 1
            log(f"notices [dry]: {label}: {notice.payload['subject']}")
            for item in notice.items:
                log(f"    {item.line(notice.today)}")
            for row in notice.cleared:
                log(f"    cleared: {row['cleared_text']}")
        else:
            http_status, error = post_alert(settings.url, notice.payload)
            notice.status = "failed" if error else "sent"
            tally[notice.status] += 1
            log(f"notices: {label}: {'FAILED ' + error if error else 'sent'}")
            _audit(store, run_id, notice, notice.status, http_status, error, log)
        if notice.status in ("sent", "dry"):
            rows = plan.rows[notice.location_id]
            commit(rows, notice.items, notice.cleared, notice.today, notice.audience)
            plan.changed.setdefault(notice.location_id, set()).update(
                {i.issue.key for i in notice.items}
                | {(r["code"], r["entity_key"]) for r in notice.cleared})
    return tally


def notify_run(store, results: dict, run_date: date, run_id=None, log=print, env=None) -> dict:
    """The nightly entry point (main.py). Returns the tally.

    Never raises: every failure is logged, because a notification problem
    must not turn a good collection run into a failed one. Reads state
    first and gives up cleanly if it cannot (for example before migration
    0014 is applied), so a half-configured deploy sends nothing.
    """
    settings = Settings.from_env(env)
    empty = {"sent": 0, "dry": 0, "failed": 0, "held": 0, "deferred": 0, "breaker": 0}
    if settings.mode == "off":
        return empty
    if settings.problems:
        for problem in settings.problems:
            log(f"notices: {problem}; nothing sent")
        return empty
    try:
        rows_by_loc = group_rows(store.read_alert_state())
        snoozed = store.read_active_acks(run_date)
        sent_today = store.read_notices_sent(run_date)
    except Exception as exc:
        log(f"notices: could not read alert state ({type(exc).__name__}); nothing sent")
        return empty

    days = [AccountDay.from_result(result, run_date) for result in results.values()]
    plan = evaluate(days, rows_by_loc, snoozed, sent_today, settings, run_date)
    tally = _deliver(plan, settings, store, run_id, log)
    try:
        store.save_alert_state(plan.upserts(), plan.deletes())
        if plan.newly_tracked:
            store.mark_alert_tracking(plan.newly_tracked, run_date)
    except Exception as exc:
        log(f"notices: state not saved ({type(exc).__name__}); tomorrow may repeat today's notes")
    log(f"notices: {tally['sent']} sent, {tally['dry']} dry, {tally['failed']} failed, "
        f"{tally['held']} held, {tally['deferred']} deferred"
        + (f", {tally['breaker']} stopped by the breaker" if tally["breaker"] else ""))
    return tally


# -- the Monday digest closes the loop --------------------------------------------------------

def cleared_for_digest(state_rows: list[dict], subs: list[dict], today: date) -> dict[str, list[dict]]:
    """Cleared issues the Monday digest should report, {location_id: [rows]}.

    A cleared line never sends a note by itself; it rides along with the
    account's next note. When no note comes, the digest carries it instead,
    so an AM who was told about a problem always hears that it went away.
    Only issues told to the AM's own address qualify (not a dry run, not
    the shadow-week redirect), only while fresh (CLEARED_TTL_DAYS).
    """
    am_by_loc = {s.get("location_id"): (s.get("am_email") or "").strip().lower() for s in subs}
    waiting: dict[str, list[dict]] = {}
    for loc, rows in group_rows(state_rows).items():
        for row in rows.values():
            if (row.get("status") == "resolved" and not row.get("resolve_told")
                    and row.get("cleared_text") and row.get("notified_to")
                    and row["notified_to"] == am_by_loc.get(loc)
                    and row.get("resolved_at") is not None
                    and (today - row["resolved_at"]).days <= CLEARED_TTL_DAYS):
                waiting.setdefault(loc, []).append(row)
    return waiting


def mark_cleared_delivered(store, cleared: dict[str, list[dict]]) -> None:
    """Record that the digest carried these cleared lines (never twice)."""
    rows = [dict(row, resolve_told=True) for group in cleared.values() for row in group]
    if rows:
        store.save_alert_state(rows, [])


# -- rehearsals: --send-test and --notify-preview ----------------------------------------------

def sample_notice(today: date) -> Notice:
    """A realistic note for a fake account ("Sample Pool & Spa", SAMPLE):
    what --send-test posts and what the GHL workflow learns its fields from."""
    sub = {"location_id": "SAMPLE", "slug": "sample", "name": "Sample Pool & Spa",
           "am_email": "mcarlson@smallscreenproducer.com", "am_name": "Matthew"}
    waiting = Issue("CONVOS_WAITING", "", "red", "7 customers waiting for a reply",
                    "7 people who messaged the client are still waiting for a reply. "
                    "The longest has waited 3 days.",
                    "Ask the client to answer them in their Conversations inbox today, and to "
                    "close out any that need no reply.", count=7)
    quiet = Issue("FORM_WENT_SILENT", "sample-form", "amber", '"Contact Us" form went quiet',
                  'The "Contact Us" form usually sends about 3 leads a week and has sent none '
                  "since Sep 15.",
                  "Open the page it is on and send a test. If the test does not show up in the "
                  "account within a few minutes, the form needs fixing.",
                  label="Contact Us", link="https://example.com/contact")
    first_seen = date.fromordinal(today.toordinal() - 7)
    items = [Item("first", waiting, {"first_seen": today, "backlog": False}),
             Item("reminder", quiet, {"first_seen": first_seen, "backlog": False, "reminders": 0})]
    cleared = [{"code": "SLOW_RESPONSE", "entity_key": "",
                "cleared_text": "Leads going cold: no lead from this week is waiting over a day "
                                "for a first reply."}]
    return Notice(sub=sub, items=items, cleared=cleared, am_email=sub["am_email"],
                  recipient=sub["am_email"], audience=sub["am_email"], first_note=False,
                  today=today)


def send_test_notice(log=print, env=None, today: date | None = None,
                     deliver_to: str | None = None) -> int:
    """POST one sample note with is_test "true" and return an exit code.

    This is how the GHL workflow gets its first payload: GHL cannot offer the
    {{inboundWebhookRequest.*}} fields until it has received one. The
    workflow's first step drops anything with is_test = true, so this never
    reaches a person. Sends regardless of AUTOMATION_WEBHOOKS (it is the
    rehearsal for building the workflow) and writes no audit row.
    The separate, owner-approved delivery test may pass the guard, but only
    for the fixed synthetic account and Matthew's exact staff address.
    """
    env = env if env is not None else os.environ
    if deliver_to is not None and deliver_to != "mcarlson@smallscreenproducer.com":
        log("send-test: delivery tests are restricted to Matthew; nothing sent")
        return 2
    settings = Settings.from_env(env, mode="on")
    if not settings.url:
        log("send-test: AUTOMATION_WEBHOOK_URL is not set; nothing to send")
        return 2
    payload = compose(sample_notice(today or date.today()), settings,
                      is_test=deliver_to is None)
    if deliver_to is not None:
        payload["subject"] = "[TEST — sample account] " + payload["subject"]
        payload["is_delivery_test"] = "true"
    shown = {k: (v if k not in ("body_html", "body_text") else f"<{len(v)} characters>")
             for k, v in payload.items()}
    log("send-test: posting ONE synthetic delivery test to Matthew" if deliver_to
        else "send-test: posting a sample note (is_test = true):")
    log(json.dumps(shown, indent=2))
    http_status, error = post_alert(settings.url, payload)
    if error:
        log(f"send-test: FAILED ({error})")
        return 2
    if deliver_to:
        log(f"send-test: webhook accepted (HTTP {http_status}); check GHL execution "
            "and Matthew's inbox. This does not confirm email delivery.")
    else:
        log(f"send-test: delivered (HTTP {http_status}). In GHL, fetch the sample request; "
            "every field should now be selectable.")
    return 0


def preview(store, run_date: date, days: int = 14, log=print, env=None) -> dict:
    """--notify-preview: replay the last `days` nights of stored data in
    memory, as if notes had been switched on at the start, and print what
    each AM would have received. Writes nothing and posts nothing.

    Covers the accounts with alert types set (subaccounts.alert_triggers).
    Recipients are the real AMs (or AM_NOTIFY_REDIRECT when set); the
    allowlist is ignored here so the whole picture shows. Returns
    {am_email: number of notes}.
    """
    env = env if env is not None else os.environ
    settings = Settings.from_env(env, mode="on")
    subs = [s for s in store.load_subaccounts(active=True) if _enabled(s)]
    if not subs:
        log("notify-preview: no account has alert types set (subaccounts.alert_triggers)")
        return {}
    settings = Settings(settings.mode, settings.url,
                        frozenset((s.get("am_email") or "").lower() for s in subs),
                        settings.redirect, settings.dashboard_base, ())
    start = date.fromordinal(run_date.toordinal() - days + 1)
    history = store.read_notice_history([s["location_id"] for s in subs], start, run_date)
    snoozed = store.read_active_acks(run_date)
    subs = [dict(s, alert_tracking_since=None) for s in subs]
    rows_by_loc: dict = {}
    counts: dict = {}
    for offset in range(days):
        night = date.fromordinal(start.toordinal() + offset)
        day_inputs = []
        for sub in subs:
            stored = history.get((sub["location_id"], night.isoformat()))
            if stored:
                day_inputs.append(AccountDay(
                    sub=sub, metrics=stored["metrics"], flags=stored["flags"],
                    form_rows=stored["form_rows"], form_checks=stored["form_checks"],
                    gate_passed=stored["gate_passed"], today=night))
        plan = evaluate(day_inputs, rows_by_loc, snoozed, set(), settings, night)
        for loc in plan.newly_tracked:
            for sub in subs:
                if sub["location_id"] == loc:
                    sub["alert_tracking_since"] = night
        for notice in plan.notices:
            if notice.status:
                log(f"{night} {notice.sub.get('slug')}: not sent ({notice.status})")
                continue
            notice.payload = compose(notice, settings)
            notice.status = "preview"
            commit(rows_by_loc[notice.location_id], notice.items, notice.cleared, night,
                   notice.audience)
            counts[notice.am_email] = counts.get(notice.am_email, 0) + 1
            print(f"\n===== {night} · to {notice.recipient} · {notice.payload['subject']} =====")
            print(notice.payload["body_text"])
    log("notify-preview: notes per AM over "
        f"{days} nights: " + (", ".join(f"{am} {n}" for am, n in sorted(counts.items())) or "none"))
    return counts
