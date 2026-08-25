"""Insight-to-Workflow Bridge (Phase 1): push new breakage alerts to GHL.

The collector already knows, every morning, which accounts broke overnight.
This module is the one-way door that tells somebody: it POSTs a flat JSON
payload to a single GoHighLevel Inbound Webhook living in SSP's own
subaccount, where one published workflow turns it into a task and an email.

How this fits in
----------------
main.py calls send_run_alerts() once, after flags are computed and written
and before the run row is closed. Nothing above this module knows the
webhook exists; nothing in this module reads the GHL API. A failure here can
never fail the collection run — the alerts are a courtesy on top of data
that is already safely in Supabase.

Key ideas to understand this file
---------------------------------
* Newly appeared, not merely present. An account with a form that went
  quiet three weeks ago must not be re-announced every morning. The trigger
  compares today's flag codes against the codes from that location's
  PREVIOUS RUN — not against last week, which is what the dashboard's
  flags_new uses. A code present yesterday and today is old news.
* First run is silent. If a location has no prior snapshot at all, every
  flag it has would look new, so the whole location is skipped for this
  run. That is what stops a first deploy from firing 100 alerts.
* Two cadences. Daily codes fire on appearance. The pipeline picture
  (PIPELINE_WEEKLY) is a standing condition that barely changes, so it goes
  out once a week on Mondays as one send per account with a bottleneck —
  the workflow branches on flag_code to email it rather than open a task.
* Flat payload. GHL maps webhook fields as
  {{inboundWebhookRequest.field_name}} and cannot navigate nested JSON, so
  every value is a string or a number at the top level.
* No PII, ever. Payloads carry account names, flag text, entity names and
  counts. Never a contact name, phone number, email address, or message
  body. test_pii.py asserts this alongside the rest of the app.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

# Codes that fire the moment they newly appear. Deliberately a frozen set in
# code rather than a config table: widening what can page the team should be
# a reviewed commit, not a checkbox someone flips at 11pm.
DAILY_CODES = frozenset({
    "FORM_WENT_SILENT",
    "SURVEY_WENT_SILENT",
    "SOCIAL_DISCONNECTED",
    "SOURCE_DROP",          # red severity only — see _is_alertable
    "NO_DELIVERY",
    "PIPELINE_FROZEN",
    "STALE_PIPELINE",
    "INTEGRATION_SUSPECT",
    "LEADS_ZERO",
})

# SOURCE_DROP fires on ~3 accounts as amber at any time and is noisy there;
# only a total collapse (red) is worth interrupting someone for.
RED_ONLY_CODES = frozenset({"SOURCE_DROP"})

WEEKLY_CODE = "PIPELINE_WEEKLY"

# Entity types whose `entity_name` is safe to put in a payload. Everything
# else is dropped, name and all.
#
# This is not paranoia: STALE_PIPELINE attaches the first stale deal, and GHL
# opportunity names are routinely just the customer's name ("Eric Dybala").
# The alert does not need it — the count and the dashboard link are what an
# AM acts on — so the name never leaves the app. A new code that attaches a
# person-shaped entity is opted OUT by default rather than in.
SAFE_ENTITY_TYPES = frozenset({"form", "survey", "pipeline", "social", "source"})

# A flag storm (a bad token, a GHL outage) must not become a task storm.
# The weekly digest is exempt: its volume is bounded by the roster.
DAILY_SEND_CAP = 10

DEFAULT_DASHBOARD_URL = "https://mlhaccountreports.netlify.app"

# Emoji, variation selectors and zero-width joiners, stripped from anything
# that lands in a GHL task title. Client pipeline stages are full of them
# ("🚧 Construction Process · 🛠 Phase 12 ✋📩4*8") and they make an alert
# unreadable. Mirrors stripDecor() in web/src/lib/format.ts.
_DECOR = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️‍←-⇿⬀-⯿]")


def strip_decor(value) -> str:
    """Drop emoji/pictographs and collapse whitespace; '' for None."""
    if not value:
        return ""
    return " ".join(_DECOR.sub("", str(value)).split())


def mode_from_env(env=None) -> str:
    """Read AUTOMATION_WEBHOOKS: 'on' | 'dry' | 'off' (default 'off').

    Off is the default on purpose — a fresh deploy that has not been
    configured stays silent rather than guessing.
    """
    raw = ((env or os.environ).get("AUTOMATION_WEBHOOKS") or "off").strip().lower()
    return raw if raw in ("on", "dry", "off") else "off"


def _is_alertable(flag: dict) -> bool:
    """True when this flag is one the daily trigger is allowed to send."""
    code = flag.get("code")
    if code not in DAILY_CODES:
        return False
    if code in RED_ONLY_CODES and flag.get("severity") != "red":
        return False
    return True


def build_payload(flag: dict, sub: dict, metrics: dict, run_date: str,
                  dashboard_base: str = DEFAULT_DASHBOARD_URL) -> dict:
    """One flat alert dict, ready to POST.

    Pipeline fields are always present (empty/zero for non-pipeline codes) so
    a single set of GHL field mappings covers every alert — a workflow built
    against a form alert keeps working when a pipeline alert arrives.
    """
    slug = sub.get("slug") or sub.get("location_id") or ""
    return {
        "event": "account_health_alert",
        "flag_code": flag.get("code") or "",
        "severity": flag.get("severity") or "",
        "flag_title": strip_decor(flag.get("title")),
        "action": strip_decor(flag.get("action")),
        "account_name": sub.get("name") or "",
        "account_slug": slug,
        "am_email": sub.get("am_email") or "",
        "entity_type": flag.get("entity_type") or "",
        # Name only for entity types that cannot be a person — see
        # SAFE_ENTITY_TYPES. entity_id and deep_link are never included at all.
        "entity_name": (strip_decor(flag.get("entity_name"))
                        if flag.get("entity_type") in SAFE_ENTITY_TYPES else ""),
        "detected_on": run_date,
        "dashboard_url": f"{dashboard_base.rstrip('/')}/account/{slug}",
        # Pipeline context — see the module docstring's "flat payload" note.
        "stage_name": strip_decor(metrics.get("bottleneck_stage")),
        "opps_open": metrics.get("opps_open") or 0,
        "opps_stale": metrics.get("opps_stale") or 0,
        "opps_moved_30d": metrics.get("opps_moved_30d") or 0,
    }


def weekly_pipeline_payload(sub: dict, metrics: dict, run_date: str,
                            dashboard_base: str = DEFAULT_DASHBOARD_URL) -> dict | None:
    """The Monday pipeline read for one account, or None if there is nothing
    to say (no bottleneck stage means no dollars are visibly parked)."""
    stage = strip_decor(metrics.get("bottleneck_stage"))
    if not stage:
        return None
    value = metrics.get("bottleneck_value_usd") or 0
    stale = metrics.get("opps_stale") or 0
    open_ct = metrics.get("opps_open") or 0
    moved = metrics.get("opps_moved_30d") or 0
    action = (f"${value:,.0f} sits idle in '{stage}'. "
              f"{stale} of {open_ct} open deals are idle 14d+; {moved} moved in the last 30 days. "
              "Start the pipeline review at that stage.")
    payload = build_payload(
        {"code": WEEKLY_CODE, "severity": "info", "title": "Weekly pipeline read",
         "action": action, "entity_type": "pipeline", "entity_name": stage},
        sub, metrics, run_date, dashboard_base)
    return payload


def select_alerts(location_flags: list[dict], prev_codes: list[str] | None) -> list[dict]:
    """Flags to announce for one location: alertable AND not present last run.

    prev_codes is None when the location has no prior run to compare against
    (a brand-new account, or the first run after a deploy). Returning [] there
    is the "first run is silent" rule from the module docstring.
    """
    if prev_codes is None:
        return []
    seen = set(prev_codes)
    return [f for f in location_flags if _is_alertable(f) and f.get("code") not in seen]


def post_alert(url: str, payload: dict, timeout: float = 15.0) -> tuple[int | None, str | None]:
    """POST one payload. Returns (http_status, error). Retries once.

    Deliberately urllib rather than requests: this is the only outbound call
    in the collector that is not the GHL API, and it has no business sharing
    that client's session, retry ladder, or rate bucket.
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
            # A 4xx will fail identically on retry; a 5xx might not.
            if exc.code < 500:
                return exc.code, f"HTTP {exc.code}"
            last_error = f"HTTP {exc.code}"
        except Exception as exc:                       # network, DNS, timeout
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt == 1:
            continue
    return None, last_error


def send_test_alert(kind: str = "daily", log=print, env=None) -> int:
    """POST one sample alert and return an exit code. Used by --send-test.

    This is how the GHL workflow gets its first payload: GHL cannot show the
    {{inboundWebhookRequest.*}} field pickers until it has received one, so
    somebody has to fire a sample before any action can be mapped. Running it
    from the collector rather than a terminal keeps operations browser-only
    and keeps the webhook URL in the environment where it belongs.

    Sends regardless of AUTOMATION_WEBHOOKS so the workflow can be built and
    tested before the nightly bridge is switched on. Writes no audit row --
    this is a rehearsal, not an alert about a real account.
    """
    env = env or os.environ
    url = (env.get("AUTOMATION_WEBHOOK_URL") or "").strip()
    if not url:
        log("send-test: AUTOMATION_WEBHOOK_URL is not set — nothing to send")
        return 2
    sample_sub = {"location_id": "SAMPLE", "slug": "sample-account",
                  "name": "Sample Pool & Spa", "am_email": "mcarlson@smallscreenproducer.com"}
    sample_metrics = {"opps_open": 42, "opps_stale": 11, "opps_moved_30d": 3,
                      "bottleneck_stage": "Inground Pool Sales · Hold for Decision",
                      "bottleneck_value_usd": 203480}
    sample_date = env.get("SAMPLE_DATE") or "2026-08-25"
    base = (env.get("DASHBOARD_URL") or DEFAULT_DASHBOARD_URL).strip()

    if kind == "weekly":
        # The Monday shape, so the workflow's If/Else branch can be tested
        # without waiting for a Monday.
        payload = weekly_pipeline_payload(sample_sub, sample_metrics, sample_date, base)
    else:
        payload = build_payload(
        {"code": "FORM_WENT_SILENT", "severity": "amber", "title": "Form went silent",
         "action": "SAMPLE ALERT — Hot Tub Brochure quiet 4 business days; was ~6/wk. "
                   "Check the page.",
         "entity_type": "form", "entity_name": "Hot Tub Brochure"},
        sample_sub, sample_metrics, sample_date, base)

    log(f"send-test: posting sample {kind} payload:")
    log(json.dumps(payload, indent=2))
    http_status, error = post_alert(url, payload)
    if error:
        log(f"send-test: FAILED — {error}")
        return 2
    log(f"send-test: delivered (HTTP {http_status}). "
        "Reopen the trigger in GHL — every field should now be selectable.")
    return 0


def send_run_alerts(store, results: dict, run_date, run_id=None, log=print, env=None) -> dict:
    """Push this run's new alerts. Returns {sent, dry, failed, skipped}.

    Never raises: every failure is logged and recorded, because a webhook
    problem must not turn a good collection run into a failed one.
    """
    env = env or os.environ
    mode = mode_from_env(env)
    if mode == "off":
        return {"sent": 0, "dry": 0, "failed": 0, "skipped": 0}

    url = (env.get("AUTOMATION_WEBHOOK_URL") or "").strip()
    if not url:
        log("automation: AUTOMATION_WEBHOOKS is on but AUTOMATION_WEBHOOK_URL is unset — nothing sent")
        return {"sent": 0, "dry": 0, "failed": 0, "skipped": 0}

    dashboard_base = (env.get("DASHBOARD_URL") or DEFAULT_DASHBOARD_URL).strip()
    date_str = run_date.isoformat()
    tally = {"sent": 0, "dry": 0, "failed": 0, "skipped": 0}

    # Anything already delivered for this date. A same-day rerun recomputes
    # identical flags and would otherwise read as newly appeared all over
    # again; this is what makes reruns safe.
    try:
        already_sent = store.read_sent_alert_keys(run_date)
    except Exception as exc:
        log(f"automation: could not read prior sends ({exc}); skipping this run to avoid duplicates")
        return tally

    # -- daily: anything that newly appeared since this location's last run --
    queued: list[tuple[dict, dict]] = []
    for location_id, result in results.items():
        sub = result.get("sub") or {}
        try:
            prev_codes = store.read_prev_flag_codes(location_id, run_date)
        except Exception as exc:                        # a read failure is not fatal
            log(f"automation: {sub.get('slug') or location_id}: prior-flag read failed ({exc}); skipping")
            continue
        for flag in select_alerts(result.get("flags") or [], prev_codes):
            payload = build_payload(flag, sub, result.get("metrics") or {},
                                    date_str, dashboard_base)
            if _key(sub, payload) in already_sent:
                continue
            queued.append((sub, payload))

    # -- weekly: Monday only, one per account that has a bottleneck stage --
    weekly: list[tuple[dict, dict]] = []
    if run_date.weekday() == 0:
        for location_id, result in results.items():
            sub = result.get("sub") or {}
            payload = weekly_pipeline_payload(sub, result.get("metrics") or {},
                                              date_str, dashboard_base)
            if payload and _key(sub, payload) not in already_sent:
                weekly.append((sub, payload))

    over_cap = queued[DAILY_SEND_CAP:]
    queued = queued[:DAILY_SEND_CAP]
    if over_cap:
        log(f"automation: {len(over_cap)} alerts over the {DAILY_SEND_CAP}/run cap — logged, not sent")

    for sub, payload in over_cap:
        tally["skipped"] += 1
        _record(store, run_id, sub, date_str, payload, mode, "skipped_cap", None,
                f"over the {DAILY_SEND_CAP}/run cap", log)

    _deliver(queued + weekly, url, mode, store, run_id, date_str, tally, log)

    if mode == "dry":
        log(f"automation: dry run — {tally['dry']} alerts would have been sent")
    else:
        log(f"automation: {tally['sent']} sent, {tally['failed']} failed, {tally['skipped']} over cap")
    return tally


def _deliver(items, url, mode, store, run_id, date_str, tally, log) -> None:
    """POST each (sub, payload), tallying and auditing. Shared by both paths."""
    for sub, payload in items:
        label = f"{payload['flag_code']} · {sub.get('slug') or ''}"
        if mode == "dry":
            tally["dry"] += 1
            log(f"automation [dry]: would send {label} — {payload['action'][:90]}")
            continue
        http_status, error = post_alert(url, payload)
        if error is None:
            tally["sent"] += 1
            log(f"automation: sent {label}")
            _record(store, run_id, sub, date_str, payload, mode, "sent", http_status, None, log)
        else:
            tally["failed"] += 1
            log(f"automation: FAILED {label} — {error}")
            _record(store, run_id, sub, date_str, payload, mode, "failed", http_status, error, log)


def send_weekly_alerts(store, run_date, log=print, env=None) -> dict:
    """Fire the weekly pipeline digest on demand, from already-stored data.

    The nightly run does this automatically on Mondays from the run it just
    performed. This is the same send driven from the snapshots table instead,
    so the digest can be demonstrated — or re-sent after a failure — on any
    day without re-collecting anything.

    Honors the same kill switch and the same already-sent dedupe as the
    nightly path, so running it twice cannot double-send.
    """
    env = env or os.environ
    mode = mode_from_env(env)
    tally = {"sent": 0, "dry": 0, "failed": 0, "skipped": 0}
    if mode == "off":
        log("weekly: AUTOMATION_WEBHOOKS is off — nothing sent")
        return tally
    url = (env.get("AUTOMATION_WEBHOOK_URL") or "").strip()
    if not url:
        log("weekly: AUTOMATION_WEBHOOK_URL is not set — nothing sent")
        return tally

    dashboard_base = (env.get("DASHBOARD_URL") or DEFAULT_DASHBOARD_URL).strip()
    date_str = run_date.isoformat()
    subs_by_id = {s["location_id"]: s for s in store.load_subaccounts(active=True)}
    already_sent = store.read_sent_alert_keys(run_date)

    items = []
    for row in store.read_pipeline_snapshots(run_date):
        sub = subs_by_id.get(row.get("location_id"))
        if not sub:
            continue
        payload = weekly_pipeline_payload(sub, row, date_str, dashboard_base)
        if payload and _key(sub, payload) not in already_sent:
            items.append((sub, payload))

    if not items:
        log(f"weekly: nothing to send for {date_str} "
            "(no account has a bottleneck stage, or all were sent already)")
        return tally
    log(f"weekly: {len(items)} account(s) with a pipeline read for {date_str}")
    _deliver(items, url, mode, store, None, date_str, tally, log)
    return tally


def _key(sub: dict, payload: dict) -> tuple[str, str, str]:
    """Identity of one alert, matching store.read_sent_alert_keys().

    Keyed on location_id rather than the payload's slug because that is what
    the audit row stores; the payload deliberately carries only the slug.
    """
    return (sub.get("location_id") or "",
            payload.get("flag_code") or "", payload.get("entity_name") or "")


def _record(store, run_id, sub, date_str, payload, mode, status, http_status, error, log) -> None:
    """Write one audit row, swallowing (but logging) any write failure."""
    if mode == "dry":
        return
    try:
        store.record_automation_send({
            "run_id": run_id,
            "location_id": sub.get("location_id"),
            "snapshot_date": date_str,
            "flag_code": payload.get("flag_code"),
            "severity": payload.get("severity") or None,
            "entity_type": payload.get("entity_type") or None,
            "entity_name": payload.get("entity_name") or None,
            "mode": mode if mode in ("live", "dry") else "live",
            "status": status,
            "http_status": http_status,
            "error": (error or None) and str(error)[:500],
            "payload": payload,
        })
    except Exception as exc:
        log(f"automation: audit row not written ({type(exc).__name__}: {exc})")
