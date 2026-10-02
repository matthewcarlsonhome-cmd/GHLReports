"""Pure, owner-safe summaries. No recipient routing, network calls or raw CRM text."""
from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

TOPICS = ("follow_up", "waiting", "speed", "ownership", "lead_flow", "pipeline")
DEFAULT_TOPICS = TOPICS[:-1]
TEMPLATE_VERSION = "owner-weekly-v1"
NUMBERS = ("new_leads", "unassigned", "eligible", "contacted", "completion_pct",
           "uncontacted", "speed_median", "speed_p90", "speed_samples", "waiting",
           "oldest_wait_hours")


def number(value):
    return value if isinstance(value, (float, int)) and not isinstance(value, bool) and isfinite(value) and value >= 0 else None


def safe_metrics(report):
    data = (report or {}).get("data", {})
    raw = data.get("metrics", {})
    result = {k: number(raw.get(k)) for k in NUMBERS}
    cov = data.get("coverage", {})
    for keys, valid in [
        (("new_leads", "unassigned"), cov.get("contacts") is True),
        (("eligible", "contacted", "completion_pct", "uncontacted", "speed_median",
          "speed_p90", "speed_samples"), cov.get("responses") is True),
        (("waiting", "oldest_wait_hours"), cov.get("conversations") is True),
    ]:
        if not valid:
            result.update({k: None for k in keys})
    result["speed_human"] = raw.get("speed_human") if isinstance(raw.get("speed_human"), bool) else None
    return result


def stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def safe_label(value, limit=160):
    # Business labels only. Callers must never pass a customer/entity label.
    return " ".join(str(value or "").split())[:limit]


def report_url(origin, location, report_id, period):
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("Approved HTTPS dashboard origin required")
    return origin.rstrip("/") + "/client/accounts/" + quote(location, safe="") + "?" + urlencode(
        {"view": "week", "period": period, "report": report_id})


def observations(snapshot, flags, run_date):
    """One observation per topic/date. Never copy flag prose, names or entities."""
    coverage = snapshot.get("coverage", {}).get("sources", {})
    def valid(*sources):
        return snapshot.get("gate_passed") is True and all(
            coverage.get(s, {}).get("status") == "complete" and
            coverage.get(s, {}).get("exhausted") is True and
            not coverage.get(s, {}).get("error") and
            not coverage.get(s, {}).get("skipped") for s in sources)
    codes = {f.get("code"): f.get("severity") for f in flags}
    definitions = [
        ("follow_up", "leads_uncontacted_24h", ("contacts", "speed_to_lead"), ("SLOW_RESPONSE",)),
        ("waiting", "convos_waiting", ("conversations",), ("CONVOS_WAITING",)),
        ("ownership", "leads_unassigned_7d", ("contacts",), ("UNASSIGNED_LEADS",)),
        ("lead_flow", "leads_new_7d", ("contacts",), ("LEADS_ZERO", "LEADS_DROP", "SOURCE_DROP")),
        ("pipeline", "opps_moved_30d", ("opportunities",), ("PIPELINE_FROZEN",)),
    ]
    rows = []
    for topic, key, sources, relevant in definitions:
        complete = valid(*sources)
        present = any(c in codes for c in relevant) if topic in ("lead_flow", "pipeline") else (number(snapshot.get(key)) or 0) > 0
        qualifying = any(c in codes for c in relevant)
        rows.append({"topic": topic, "observed_on": str(run_date), "valid": complete,
                     "present": present if complete else False,
                     "qualifying": qualifying if complete else False,
                     "severity": "red" if complete and any(codes.get(c) == "red" for c in relevant) else "amber",
                     "count": number(snapshot.get(key)) if complete else None})
    return rows


def summarize(account, cohort, current, history=(), topics=DEFAULT_TOPICS, previous=None, now=None):
    """Immutable safe document shared by email and dashboard.

    History entries contain only the whitelisted observation contract above.
    Missing source coverage prevents healthy status, never becomes zero.
    """
    topics = tuple(t for t in TOPICS if t in topics)
    if not topics:
        raise ValueError("Choose at least one report topic")
    data = cohort["data"]
    metric, queue = safe_metrics(cohort), safe_metrics(current)
    now = now or stamp(current.get("generated_at"))
    captured = stamp((current.get("data") or {}).get("data_through"))
    stale = not now or not captured or not (-300 <= (now-captured).total_seconds() <= int(account.get("stale_hours", 30))*3600)
    if stale:
        queue = {k: None for k in queue}
    start, end = stamp(data.get("period_start")), stamp(data.get("period_end"))
    if not start or not end or end <= start:
        raise ValueError("No completed reporting days")
    period_start = start.date().isoformat()
    history = [r for r in history if r.get("topic") in topics]
    # Deduplicate by local date: reruns are not multiple confirmation nights.
    indexed = {(str(r.get("observed_on")), r["topic"]): r for r in history}
    today = captured.astimezone(ZoneInfo(data["timezone"])).date() if captured else end.date()
    def confirmed(topic):
        return all(indexed.get(((today-timedelta(days=i)).isoformat(), topic), {}).get("qualifying") is True
                   and indexed.get(((today-timedelta(days=i)).isoformat(), topic), {}).get("valid") is True for i in (0, 1))
    actions = []
    def action(topic, title, detail, step, rank):
        rows = [indexed.get(((today-timedelta(days=i)).isoformat(), topic), {}) for i in (0, 1)]
        conf = confirmed(topic)
        critical = conf and rows[0].get("severity") == "red"
        actions.append({"topic":topic, "title":title, "detail":detail, "next_step":step,
                        "status":"Confirmed issue" if conf else "Follow-up to review",
                        "severity":"red" if critical else "amber",
                        "rank":rank-20 if critical else rank-10 if conf else rank})
    if "follow_up" in topics and (queue["uncontacted"] or 0)>0:
        n=queue["uncontacted"]
        action("follow_up", f"{n:g} recent leads need follow-up",
               "No recorded call, text or email after 24 hours in the current seven-day new-lead window.",
               "Assign these leads and record their next call, text or email in GHL.", 10)
    if "waiting" in topics and (queue["waiting"] or 0)>0:
        oldest=queue["oldest_wait_hours"]
        action("waiting", f"{queue['waiting']:g} conversations need a reply",
               f"Oldest eligible wait: {oldest:g} hours (weekend-adjusted)." if oldest is not None else "Review eligible unanswered conversations.",
               "Answer the oldest messages first; close conversations that no longer need a reply.", 12)
    if "ownership" in topics and (queue["unassigned"] or 0)>0:
        action("ownership", f"{queue['unassigned']:g} leads need an owner",
               "Ownership is observed at the latest collection, not at the historical period end.",
               "Assign a team member and confirm each lead's next step.", 11)
    if "pipeline" in topics and confirmed("pipeline") and not stale:
        action("pipeline", "Review a pipeline with no recorded movement",
               "The established rule detected no qualifying movement for 30 days.",
               "Review active opportunities and update stages and next actions.", 13)
    if "lead_flow" in topics and confirmed("lead_flow") and not stale:
        action("lead_flow", "Review an unusual change in lead flow",
               "The established lead-flow checks found a confirmed change.",
               "Review the lead sources and campaign activity with SSP.", 14)
    actions.sort(key=lambda r:(r["rank"],r["topic"]))
    actions=[{k:v for k,v in r.items() if k!="rank"} for r in actions]
    patterns=[]
    for topic in topics:
        rows=[r for (day,t),r in indexed.items() if t==topic and period_start<=day<end.date().isoformat() and r.get("valid") is True]
        seen=[r for r in rows if r.get("present") is True]
        weeks={datetime.fromisoformat(r["observed_on"]).date().isocalendar()[:2] for r in seen}
        if seen:
            patterns.append({"topic":topic,"observed_checks":len(seen),"valid_checks":len(rows),
                             "weeks":len(weeks),"recurring":len(weeks)>=2})
    required = []
    if "lead_flow" in topics or "ownership" in topics: required += [metric["new_leads"],queue["unassigned"] if "ownership" in topics else 0]
    if "follow_up" in topics: required += [metric["eligible"],metric["uncontacted"],queue["uncontacted"]]
    if "speed" in topics: required += [metric["speed_samples"]]  # no responses => legitimate empty sample
    if "waiting" in topics: required += [queue["waiting"]]
    if "pipeline" in topics:
        required += [0 if indexed.get((today.isoformat(),"pipeline"),{}).get("valid") is True else None]
    limited = stale or any(x is None for x in required)
    comparison=None
    selected_groups={"lead_flow":("new_leads",),"follow_up":("eligible","contacted","completion_pct","uncontacted"),
                     "speed":("speed_median","speed_p90","speed_samples","speed_human"),
                     "waiting":("waiting","oldest_wait_hours"),"ownership":("unassigned",)}
    if previous and not data.get("to_date") and not previous["data"].get("to_date"):
        pstart,pend=stamp(previous["data"]["period_start"]),stamp(previous["data"]["period_end"])
        prior=safe_metrics(previous)["new_leads"]
        if pend==start and previous["data"]["timezone"]==data["timezone"] and metric["new_leads"] is not None and prior is not None:
            comparison={"previous":prior,"change":round(100*(metric["new_leads"]-prior)/prior) if prior else None,
                        "eligible":True}
    if "lead_flow" in topics and data.get("to_date") and data.get("comparison"):
        prior=data["comparison"]
        if prior.get("available") is True and number(prior.get("new_leads")) is not None and metric["new_leads"] is not None:
            n=prior["new_leads"]
            comparison={"previous":n,"change":round(100*(metric["new_leads"]-n)/n) if n else None,
                        "eligible":True,"label":prior["label"]}
    for topic,keys in selected_groups.items():
        if topic not in topics:
            for key in keys: metric[key]=queue[key]=None
    if "lead_flow" not in topics: comparison=None
    return {"schema_version":1,"sample":False,"formula_version":"owner-calendar-v1","template_version":TEMPLATE_VERSION,"account_name":safe_label(account.get("name")),
            "location_id":account["location_id"],"period_kind":cohort["period_kind"],
            "period_start":data["period_start"],"period_end":data["period_end"],"timezone":data["timezone"],
            "to_date":bool(data.get("to_date")),"generated_at":now.isoformat() if now else current["generated_at"],
            "period_generated_at":cohort["generated_at"],"current_as_of":captured.isoformat() if captured else None,
            "topics":list(topics),"state":"limited" if limited else "attention" if actions else "healthy",
            "stale":stale,"metrics":metric,"queue":queue,"comparison":comparison,"actions":actions,"patterns":patterns,
            "data_note":"Some figures are unavailable because source records are incomplete or stale. Missing data does not mean no activity." if limited else
                        "Sources are complete for the selected topics. Speed includes responded leads only; unanswered leads are counted separately."}
