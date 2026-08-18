"""Collector entry point. Run sequence per spec section 7.2 (v3):

1. start run + pit_key_ok   2. load subaccounts   3. parent first (invoices,
events, users)   4. each client subaccount: fetch -> metrics -> snapshot +
lead_events + lead_history   5. peer pass: vertical medians, then flags and
flags_new/flags_resolved per location   6. finish run with per-location
details. Exit 0 all ok, 2 any held or failed, 1 crash.

CLI: --probe | --dry-run | --location <id|slug> | --max-pages N
     | --date YYYY-MM-DD | --backfill N
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

from . import fetchers, flags as flags_mod, metrics
from .fetchers import Coverage
from .ghl_client import GHLClient, GHLAuthError, GHLError

SPEED_CAP = 100
DETAILS_CAP = 50
TOKEN_ROTATION_WARN_DAYS = 80
HISTORY_DAYS = 42       # one contacts fetch covers 7d window, 28d baseline, 14-42d cohort, 28d funnel
CLOSED_OPP_DAYS = 90


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def app_base() -> str:
    return os.environ.get("GHL_APP_BASE", "https://crm.smallscreenproducer.com").rstrip("/")


def run_timezone():
    return metrics.get_tz(os.environ.get("TZ") or metrics.DEFAULT_TZ)


# -- parent context -------------------------------------------------------


class ParentContext:
    def __init__(self):
        self.available = False
        self.error: str | None = None
        self.location_id: str | None = None
        self.client: GHLClient | None = None
        self.invoices_by_contact: dict[str, list[dict]] = {}
        self.events_by_contact: dict[str, list[dict]] = {}


def build_parent_context(parent_sub: dict, client: GHLClient, now_utc: datetime,
                         max_pages: int | None) -> ParentContext:
    ctx = ParentContext()
    ctx.location_id = parent_sub["location_id"]
    ctx.client = client
    cov = Coverage()

    invoices = fetchers.fetch_invoices(client, cov, ctx.location_id, max_pages=max_pages)
    for inv in invoices:
        contact = (inv.get("contactDetails") or {}).get("id")
        if contact:
            ctx.invoices_by_contact.setdefault(str(contact), []).append(inv)

    calendars = fetchers.fetch_calendars(client, cov, ctx.location_id)
    start_ms = int((now_utc - timedelta(days=60)).timestamp() * 1000)
    end_ms = int((now_utc + timedelta(days=60)).timestamp() * 1000)
    events = fetchers.fetch_calendar_events(client, cov, ctx.location_id, calendars,
                                            start_ms, end_ms, source="parent_events")
    for event in events:
        contact = event.get("contactId")
        if contact:
            ctx.events_by_contact.setdefault(str(contact), []).append(event)

    ctx.available = cov.status("invoices") != "unavailable"
    if not ctx.available:
        ctx.error = (cov.sources.get("invoices") or {}).get("error")
    return ctx


# -- details builder (contract in spec 7.5) --------------------------------


def build_details(*, location_id: str, base: str, umap: dict, pipeline_map: dict,
                  by_source_7d: dict, funnel_28d: dict, speed: dict,
                  lead_events: list[dict], unassigned: list[dict], waiting: list[dict],
                  pipe: dict, rel: dict | None, reviews: dict, events_next7: list[dict],
                  delivery: dict, social_accounts: list[dict] | None,
                  client_contact_id: str | None) -> dict:
    def contact_link(contact_id):
        return metrics.link_contact(base, location_id, contact_id)

    uncontacted = [{
        "contact_id": e.get("contact_id"),
        "name": e.get("contact_name"),
        "source": e.get("source"),
        "created_at": e["created_at"].isoformat() if e.get("created_at") else None,
        "hours_since": round((speed["now_utc"] - e["created_at"]).total_seconds() / 3600.0, 1)
        if e.get("created_at") else None,
        "deep_link": contact_link(e.get("contact_id")),
    } for e in speed.get("uncontacted_events", [])[:DETAILS_CAP]]

    unassigned_rows = [{
        "contact_id": c.get("id"),
        "name": metrics.contact_name(c),
        "source": metrics.contact_source(c),
        "created_at": (metrics.parse_ts(c.get("dateAdded")) or speed["now_utc"]).isoformat(),
        "deep_link": contact_link(c.get("id")),
    } for c in unassigned[:DETAILS_CAP]]

    waiting_rows = [{
        "conversation_id": w.get("conversation_id"),
        "contact_id": w.get("contact_id"),
        "contact": w.get("contact"),
        "channel": w.get("channel"),
        "hours": w.get("hours"),
        "last_inbound_at": w["last_inbound_at"].isoformat() if w.get("last_inbound_at") else None,
        "deep_link": metrics.link_conversation(base, location_id, w.get("conversation_id"),
                                               w.get("contact_id")),
    } for w in waiting[:DETAILS_CAP]]

    def pipeline_name(opp):
        entry = pipeline_map.get(str(opp.get("pipelineId")))
        return entry["name"] if entry else str(opp.get("pipelineId") or "?")

    def stage_name(opp):
        entry = pipeline_map.get(str(opp.get("pipelineId"))) or {}
        return (entry.get("stages") or {}).get(str(opp.get("pipelineStageId")),
                                               str(opp.get("pipelineStageId") or "?"))

    stale_rows = []
    for item in pipe.get("per_opp", []):
        if not item["is_stale"]:
            continue
        opp = item["opp"]
        opp_id = opp.get("id")
        owner_id = opp.get("assignedTo")
        stale_rows.append({
            "opp_id": opp_id,
            "name": opp.get("name") or "(no name)",
            "pipeline": pipeline_name(opp),
            "stage": stage_name(opp),
            "days_idle": round(item["idle_days"], 1) if item["idle_days"] is not None else None,
            "idle_source_field": item["idle_field"],
            "days_in_stage": round(item["stage_days"], 1) if item["stage_days"] is not None else None,
            "value": item["value"],
            "owner_id": owner_id,
            "owner": umap.get(str(owner_id), str(owner_id) if owner_id else "unassigned"),
            "next_step": item["next_step"],
            "deep_link": metrics.link_opportunity(base, location_id, opp_id,
                                                  (opp.get("contact") or {}).get("id")),
        })
    stale_rows.sort(key=lambda r: (r["days_idle"] or 0), reverse=True)

    missing_value_rows = [{
        "opp_id": item["opp"].get("id"),
        "name": item["opp"].get("name") or "(no name)",
        "deep_link": metrics.link_opportunity(base, location_id, item["opp"].get("id"),
                                              (item["opp"].get("contact") or {}).get("id")),
    } for item in pipe.get("per_opp", []) if not item["value"]][:DETAILS_CAP]

    appts = [{
        "id": ev.get("id"),
        "title": ev.get("title") or "(no title)",
        "start": (metrics.parse_ts(ev.get("startTime")) or speed["now_utc"]).isoformat(),
        "contact_id": ev.get("contactId"),
        "status": ev.get("appointmentStatus"),
    } for ev in events_next7[:DETAILS_CAP]]

    review_stale_rows = [{**row, "deep_link": contact_link(row.get("contact_id"))}
                         for row in reviews.get("stale_detail", [])[:DETAILS_CAP]]
    review_gap_rows = [{**row, "deep_link": metrics.link_opportunity(
        base, location_id, row.get("opp_id"), row.get("contact_id"))}
        for row in reviews.get("gap_detail", [])[:DETAILS_CAP]]

    client_block = None
    if client_contact_id and rel is not None:
        client_block = {
            "contact_id": client_contact_id,
            "last_touch_at": rel["client_last_touch_at"].isoformat() if rel.get("client_last_touch_at") else None,
            "next_appt_at": rel["client_next_appt_at"].isoformat() if rel.get("client_next_appt_at") else None,
            "deep_link": contact_link(client_contact_id),
        }

    speed_rows = [{
        "contact_id": e.get("contact_id"),
        "name": e.get("contact_name"),
        "created_at": e["created_at"].isoformat() if e.get("created_at") else None,
        "first_touch_minutes": e.get("first_touch_minutes"),
        "first_human_touch_minutes": e.get("first_human_touch_minutes"),
        "kind": e.get("first_outbound_kind"),
    } for e in lead_events[:DETAILS_CAP]]

    return {
        "users": umap,
        "pipelines": pipeline_map,
        "leads_by_source": by_source_7d,
        "funnel_28d": funnel_28d,
        "uncontacted_leads": uncontacted,
        "unassigned_leads": unassigned_rows,
        "waiting_convos": waiting_rows,
        "stale_opps": stale_rows[:DETAILS_CAP],
        "missing_value_opps": missing_value_rows,
        "past_due_invoices": (rel or {}).get("past_due_detail", [])[:DETAILS_CAP],
        "appts_next_7d": appts,
        "recent_publishes": delivery.get("recent_publishes", [])[:DETAILS_CAP],
        "social_accounts": (social_accounts or [])[:DETAILS_CAP],
        "review_asks_stale": review_stale_rows,
        "review_ask_gap": review_gap_rows,
        "client": client_block,
        "lost_reasons_90d": pipe.get("lost_reasons_90d", {}),
        "speed_to_lead": speed_rows,
        "changed": {"new": [], "resolved": []},   # filled after the peer pass
        "ghl_dashboard_url": metrics.link_dashboard(base, location_id),
    }


# -- per-location collection ------------------------------------------------


def collect_location(sub: dict, client: GHLClient, store, parent_ctx: ParentContext,
                     now_utc: datetime, run_date: date, max_pages: int | None) -> dict:
    location_id = sub["location_id"]
    base = app_base()
    cov = Coverage()
    thresholds = flags_mod.merged_thresholds(sub.get("thresholds"))

    # G1: identity — a 401/403 here means the token itself is bad.
    try:
        loc_data = client.request("GET", f"/locations/{location_id}")
    except GHLAuthError as exc:
        return {"token_invalid": True, "error": str(exc)}
    except GHLError as exc:
        cov.record("location", error=str(exc))
        loc = None
    else:
        loc = loc_data.get("location") if isinstance(loc_data.get("location"), dict) else loc_data
        cov.record("location", retrieved=1 if loc else 0, error=None if loc else "empty response")

    g1_ok = bool(
        loc
        and str(loc.get("id") or loc.get("_id") or "") == str(location_id)
        and metrics.location_name_matches(loc.get("name"), sub.get("name") or "")
    )
    tz = metrics.get_tz((loc or {}).get("timezone") or sub.get("timezone"))

    win_start, win_end = metrics.window_7d(now_utc, tz)
    history_start, history_end = metrics.rolling_window(now_utc, tz, HISTORY_DAYS)

    users = fetchers.fetch_users(client, cov, location_id)
    umap = fetchers.users_map(users)

    pipelines = fetchers.fetch_pipelines(client, cov, location_id)
    pipeline_map = {}
    for p in pipelines:
        pid = p.get("id") or p.get("_id")
        if pid:
            pipeline_map[str(pid)] = {
                "name": p.get("name") or str(pid),
                "stages": {str(s.get("id") or s.get("_id")): s.get("name") or "?"
                           for s in (p.get("stages") or []) if s.get("id") or s.get("_id")},
            }

    closed_cutoff_ms = int((now_utc - timedelta(days=CLOSED_OPP_DAYS)).timestamp() * 1000)
    opps = fetchers.fetch_opportunities(client, cov, location_id, closed_cutoff_ms,
                                        max_pages=max_pages)

    since_14d = metrics.rolling_window(now_utc, tz, 14)[0]
    convos = fetchers.fetch_recent_conversations(client, cov, location_id,
                                                 int(since_14d.timestamp() * 1000),
                                                 max_pages=max_pages)

    # One contacts fetch covers every window (7d, baseline, cohort, funnel, speed)
    contacts_all = fetchers.fetch_contacts_range(
        client, cov, location_id,
        gte_iso=history_start.astimezone(timezone.utc).isoformat(),
        lte_iso=history_end.astimezone(timezone.utc).isoformat(),
        max_pages=max_pages)
    earliest_added = metrics.parse_ts(fetchers.fetch_earliest_contact_added(client, location_id))

    tagged = fetchers.fetch_tagged_contacts(client, cov, location_id, metrics.REVIEW_TAG,
                                            max_pages=max_pages)

    submissions = fetchers.fetch_form_submissions(
        client, cov, location_id,
        start_date=history_start.astimezone(tz).date().isoformat(),
        end_date=history_end.astimezone(tz).date().isoformat(),
        max_pages=max_pages)

    calendars = fetchers.fetch_calendars(client, cov, location_id)
    events = fetchers.fetch_calendar_events(
        client, cov, location_id, calendars,
        int((now_utc - timedelta(days=28)).timestamp() * 1000),
        int((now_utc + timedelta(days=7)).timestamp() * 1000))
    events_next7 = [e for e in events
                    if (ts := metrics.parse_ts(e.get("startTime"))) is not None and ts > now_utc]

    services = sub.get("services") or []
    delivery_applies = bool({"content", "social"} & set(services))
    social_accounts = None
    if delivery_applies:
        sites = fetchers.fetch_blog_sites(client, cov, location_id)
        blog_posts = fetchers.fetch_blog_posts(client, cov, location_id, sites, max_pages=max_pages)
        social_posts = fetchers.fetch_social_posts(
            client, cov, location_id,
            from_iso=(now_utc - timedelta(days=90)).isoformat(),
            to_iso=now_utc.isoformat(), max_pages=max_pages)
        if "social" in services:
            social_accounts = fetchers.fetch_social_accounts(client, cov, location_id)
        else:
            cov.record("social_accounts", skipped=True, note="social not in services; skipped")
    else:
        blog_posts, social_posts = [], []
        cov.record("blogs", skipped=True, note="content/social not in services; skipped")
        cov.record("social", skipped=True, note="content/social not in services; skipped")
        cov.record("social_accounts", skipped=True, note="content/social not in services; skipped")

    # Exclusions, windows, and the live baseline
    kept_all, _ = metrics.filter_exclusions(contacts_all)
    leads_7d_raw = metrics.leads_in_window(contacts_all, win_start, win_end)
    leads_7d = metrics.leads_in_window(kept_all, win_start, win_end)
    excluded_count = len(leads_7d_raw) - len(leads_7d)

    baseline = metrics.baseline_stats(kept_all, win_start, earliest_added)
    delta = metrics.leads_delta_pct(len(leads_7d), baseline["leads_trailing_avg"])
    by_source_7d = metrics.leads_by_source(leads_7d)
    unassigned = metrics.leads_unassigned(leads_7d)
    missing_phone = metrics.missing_phone_pct(leads_7d)
    form_stats = metrics.form_submission_stats(submissions, win_start, win_end,
                                               baseline["trailing_n"])

    # Speed to lead: newest first, capped
    def created_key(c):
        return metrics.parse_ts(c.get("dateAdded")) or datetime.min.replace(tzinfo=timezone.utc)

    recent_14d = [c for c in kept_all
                  if (ts := metrics.parse_ts(c.get("dateAdded"))) is not None and ts >= since_14d]
    speed_targets = sorted(recent_14d, key=created_key, reverse=True)[:SPEED_CAP]
    lead_events: list[dict] = []
    speed_errors = 0
    for contact in speed_targets:
        contact_id = contact.get("id")
        if not contact_id:
            continue
        try:
            convs = fetchers.fetch_conversations_for_contact(client, location_id, str(contact_id))
            messages: list[dict] = []
            for conv in convs:
                conv_id = conv.get("id")
                if conv_id:
                    messages.extend(fetchers.fetch_messages(client, str(conv_id)))
        except GHLError:
            speed_errors += 1
            continue
        lead_events.append(metrics.lead_event(contact, messages))
    capped = len(recent_14d) > SPEED_CAP
    cov.record("speed_to_lead", retrieved=len(lead_events),
               exhausted=not capped and speed_errors == 0,
               error=f"{speed_errors} contacts failed" if speed_errors else None,
               note=f"capped at {SPEED_CAP} newest contacts" if capped else None)

    speed = metrics.speed_to_lead_metrics(lead_events, now_utc, win_start, win_end)
    speed["now_utc"] = now_utc

    waiting = metrics.waiting_conversations(convos, now_utc, tz, since_utc=since_14d,
                                            min_hours=thresholds["convo_wait_hours"])
    active_7d = metrics.convos_active_7d(convos, win_start, win_end)

    pipe = metrics.pipeline_metrics(opps, now_utc, win_start, win_end,
                                    stale_days=thresholds["opp_idle_days"],
                                    stuck_days=thresholds["opp_stuck_days"])
    if not pipe["next_step_available"]:
        cov.add_note("opportunities", "next-step check unavailable")
    lead_to_opp = metrics.lead_to_opp_pct(kept_all, opps, now_utc)

    appt_stats = metrics.appointment_metrics(events, now_utc, win_start, win_end)

    delivery = metrics.delivery_metrics(blog_posts, social_posts, now_utc, services)
    if delivery.get("no_publish_found"):
        cov.add_note("blogs", "no publish found in 90d")
    social_stats = metrics.social_account_stats(social_accounts)

    # Relationship metrics come from the parent account
    rel = None
    client_contact_id = sub.get("ssp_client_contact_id")
    if sub.get("is_parent"):
        cov.record("relationship", skipped=True, note="parent account")
    elif not client_contact_id:
        cov.record("relationship", skipped=True, note="no ssp_client_contact_id configured")
    elif not parent_ctx.available:
        cov.record("relationship", error=f"parent account unavailable: {parent_ctx.error}")
    else:
        client_invoices = parent_ctx.invoices_by_contact.get(str(client_contact_id), [])
        client_events = parent_ctx.events_by_contact.get(str(client_contact_id), [])
        try:
            client_convos = fetchers.fetch_conversations_for_contact(
                parent_ctx.client, parent_ctx.location_id, str(client_contact_id))
            rel_error = None
        except GHLError as exc:
            client_convos = []
            rel_error = str(exc)
        rel = metrics.relationship_metrics(
            client_invoices, client_convos, client_events, now_utc,
            now_utc.astimezone(tz).date())
        cov.record("relationship",
                   retrieved=len(client_invoices) + len(client_convos) + len(client_events),
                   exhausted=rel_error is None, error=rel_error)

    reviews = metrics.review_proxies(tagged, opps, now_utc)

    prev_dead = store.read_prev_dead(location_id, run_date)
    gate_passed, gate_reasons = metrics.gate_check(
        g1_ok, cov, len(leads_7d), active_7d, pipe["opps_created_7d"], prev_dead)
    if gate_reasons:
        cov.add_note("location" if not g1_ok else "opportunities", "; ".join(gate_reasons))

    # 28-day funnel for the drilldown strip
    cutoff_28 = now_utc - timedelta(days=28)
    funnel_28d = {
        "form_submissions": None if submissions is None else sum(
            1 for s in submissions
            if (ts := metrics.parse_ts(s.get("createdAt"))) is not None and ts >= cutoff_28),
        "leads": sum(1 for c in kept_all
                     if (ts := metrics.parse_ts(c.get("dateAdded"))) is not None and ts >= cutoff_28),
        "opps_created": sum(1 for o in opps
                            if (ts := metrics.parse_ts(o.get("createdAt"))) is not None and ts >= cutoff_28),
        "appts_booked": sum(1 for e in events
                            if (ts := metrics.parse_ts(e.get("dateAdded"))) is not None and ts >= cutoff_28),
        "won": sum(1 for o in opps
                   if str(o.get("status") or "").lower() == "won"
                   and (ts := metrics.parse_ts(o.get("lastStatusChangeAt"))) is not None and ts >= cutoff_28),
    }

    metric_values = {
        "leads_new_7d": len(leads_7d),
        "leads_trailing_avg": baseline["leads_trailing_avg"],
        "trailing_n": baseline["trailing_n"],
        "leads_delta_pct": delta,
        "peer_median_delta_pct": None,   # peer pass fills this in
        "peer_n": None,
        "leads_by_source_7d": by_source_7d,
        "leads_by_source_trailing": baseline["leads_by_source_trailing"],
        "leads_unassigned_7d": len(unassigned),
        "leads_missing_phone_pct_7d": missing_phone,
        "form_submissions_7d": form_stats["form_submissions_7d"],
        "form_submissions_trailing_avg": form_stats["form_submissions_trailing_avg"],
        "convos_active_7d": active_7d,
        "opps_created_7d": pipe["opps_created_7d"],
        "leads_uncontacted_24h": speed["leads_uncontacted_24h"],
        "leads_no_human_touch_7d": speed["leads_no_human_touch_7d"],
        "speed_to_lead_median_min": speed["speed_to_lead_median_min"],
        "speed_to_lead_p90_min": speed["speed_to_lead_p90_min"],
        "speed_kind_known": speed["speed_kind_known"],
        "excluded_count": excluded_count,
        "convos_waiting": len(waiting),
        "convos_waiting_max_hours": waiting[0]["hours"] if waiting else None,
        "opps_open": pipe["opps_open"],
        "opps_open_value": pipe["opps_open_value"],
        "opps_stale": pipe["opps_stale"],
        "opps_stale_value": pipe["opps_stale_value"],
        "opps_stuck": pipe["opps_stuck"],
        "opps_missing_value": pipe["opps_missing_value"],
        "opps_no_next_step": pipe["opps_no_next_step"],
        "opps_won_7d": pipe["opps_won_7d"],
        "opps_lost_7d": pipe["opps_lost_7d"],
        "lead_to_opp_28d_pct": lead_to_opp,
        "win_rate_90d": pipe["win_rate_90d"],
        "median_days_to_close_90d": pipe["median_days_to_close_90d"],
        "appts_booked_7d": appt_stats["appts_booked_7d"],
        "appts_showed_28d": appt_stats["appts_showed_28d"],
        "appts_noshow_28d": appt_stats["appts_noshow_28d"],
        "noshow_rate_28d": appt_stats["noshow_rate_28d"],
        "blogs_published_30d": delivery["blogs_published_30d"],
        "social_published_7d": delivery["social_published_7d"],
        "days_since_last_publish": delivery["days_since_last_publish"],
        "social_accounts_total": social_stats["social_accounts_total"],
        "social_accounts_expired": social_stats["social_accounts_expired"],
        "invoices_past_due": rel["invoices_past_due"] if rel else None,
        "invoices_past_due_amount": rel["invoices_past_due_amount"] if rel else None,
        "client_last_touch_days": rel["client_last_touch_days"] if rel else None,
        "client_next_appt_at": rel["client_next_appt_at"].isoformat()
        if rel and rel["client_next_appt_at"] else None,
        "review_asks_stale": reviews["review_asks_stale"],
        "review_ask_gap": reviews["review_ask_gap"],
    }

    details = build_details(
        location_id=location_id, base=base, umap=umap, pipeline_map=pipeline_map,
        by_source_7d=by_source_7d, funnel_28d=funnel_28d, speed=speed,
        lead_events=lead_events, unassigned=unassigned, waiting=waiting, pipe=pipe,
        rel=rel, reviews=reviews, events_next7=events_next7, delivery=delivery,
        social_accounts=social_accounts, client_contact_id=client_contact_id)
    if parent_ctx.location_id and details.get("client"):
        details["client"]["deep_link"] = metrics.link_contact(
            base, parent_ctx.location_id, client_contact_id)

    snapshot = {
        "location_id": location_id,
        "snapshot_date": run_date.isoformat(),
        "gate_passed": gate_passed,
        "coverage": cov.to_dict(),
        **metric_values,
        "details": details,
    }

    lead_event_rows = [{
        "location_id": location_id,
        "contact_id": str(e["contact_id"]),
        "contact_name": e.get("contact_name"),
        "source": e.get("source"),
        "created_at": e["created_at"].isoformat() if e.get("created_at") else None,
        "first_outbound_at": e["first_outbound_at"].isoformat() if e.get("first_outbound_at") else None,
        "first_outbound_kind": e.get("first_outbound_kind"),
        "first_human_touch_at": e["first_human_touch_at"].isoformat() if e.get("first_human_touch_at") else None,
        "first_touch_minutes": e.get("first_touch_minutes"),
        "first_human_touch_minutes": e.get("first_human_touch_minutes"),
    } for e in lead_events if e.get("contact_id") and e.get("created_at")]

    # lead_history rows for the current and previous ISO week (spec 4.1)
    today_local = now_utc.astimezone(tz).date()
    this_week = metrics.iso_week_start(today_local)
    history_rows = metrics.weekly_lead_history(
        kept_all, submissions, tz, [this_week, this_week - timedelta(days=7)])

    return {
        "token_invalid": False,
        "snapshot": snapshot,
        "lead_events": lead_event_rows,
        "lead_history": history_rows,
        "metrics": metric_values,
        "details": details,
        "gate_passed": gate_passed,
        "gate_reasons": gate_reasons,
    }


# -- peer pass ---------------------------------------------------------------


def peer_pass(store, subs_by_id: dict, run_date: date) -> dict[str, tuple[float | None, int | None]]:
    """Vertical medians over today's gate-passed client snapshots; fall back to
    the whole active book when a vertical has < 4 non-null deltas."""
    snaps = store.todays_snapshots(run_date)
    client_snaps = []
    for snap in snaps:
        sub = subs_by_id.get(snap["location_id"])
        if not sub or sub.get("is_parent"):
            continue
        client_snaps.append((snap, sub.get("vertical") or "other"))

    def deltas(rows):
        return [s["leads_delta_pct"] for s, _ in rows
                if s.get("gate_passed") and s.get("leads_delta_pct") is not None]

    book_median, book_n = metrics.peer_median(deltas(client_snaps))

    results: dict[str, tuple[float | None, int | None]] = {}
    by_vertical: dict[str, list] = {}
    for row in client_snaps:
        by_vertical.setdefault(row[1], []).append(row)

    for vertical, rows in by_vertical.items():
        vert_median, vert_n = metrics.peer_median(deltas(rows))
        if vert_median is not None:
            value = (vert_median, vert_n)
        elif book_median is not None:
            value = (book_median, book_n)
        else:
            value = (None, vert_n)
        for snap, _ in rows:
            results[snap["location_id"]] = value

    for location_id, (peer_value, peer_n) in results.items():
        store.update_snapshot_peers(location_id, run_date, peer_value, peer_n)
    return results


# -- probe -------------------------------------------------------------------

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
BODY_KEYS = ("body", "lastmessagebody", "snippet", "message")


def _redact(obj):
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            lower = key.lower()
            if any(word in lower for word in ("email", "phone", "address")) or lower in BODY_KEYS:
                out[key] = "«redacted»" if value else value
            else:
                out[key] = _redact(value)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj[:2]]
    if isinstance(obj, str):
        return PHONE_RE.sub("«redacted»", EMAIL_RE.sub("«redacted»", obj))
    return obj


def probe_location(client: GHLClient, sub: dict, now_utc: datetime) -> str:
    location_id = sub["location_id"]
    lines = [f"\n## Probe: {sub.get('name')} ({location_id}) — {now_utc.isoformat(timespec='seconds')}\n"]
    month_ago = (now_utc - timedelta(days=30)).isoformat()
    ninety_ago = (now_utc - timedelta(days=90)).isoformat()

    first_convo_id: str | None = None
    first_contact_id: str | None = None
    first_calendar_id: str | None = None
    first_blog_id: str | None = None

    def show(label: str, method: str, path: str, params=None, body=None, pick=None):
        status, data, error = client.try_request(method, path, params=params, json_body=body)
        lines.append(f"### {label}\n`{method} {path}` → **HTTP {status}**\n")
        if error:
            lines.append(f"- error: {error}\n")
            print(f"  {label}: HTTP {status} — {error}")
            return
        record = None
        if pick and data is not None:
            record = pick(data)
        sample = _redact(record if record is not None else data)
        text = json.dumps(sample, indent=2, default=str)[:2000]
        lines.append(f"```json\n{text}\n```\n")
        print(f"  {label}: HTTP {status}")

    def first_of(*keys):
        def picker(data):
            items = fetchers._first_list(data, *keys)
            return items[0] if items else data
        return picker

    print(f"Probing {sub.get('name')} ({location_id})")
    show("Location", "GET", f"/locations/{location_id}")
    show("Users", "GET", "/users/", params={"locationId": location_id}, pick=first_of("users", "data"))
    show("Pipelines", "GET", "/opportunities/pipelines",
         params={"locationId": location_id}, pick=first_of("pipelines", "data"))
    show("Opportunities (open)", "GET", "/opportunities/search",
         params={"location_id": location_id, "limit": 2, "status": "open",
                 "getTasks": "true", "getCalendarEvents": "true"},
         pick=first_of("opportunities", "data"))
    show("Opportunities (won, date param test)", "GET", "/opportunities/search",
         params={"location_id": location_id, "limit": 2, "status": "won",
                 "date": (now_utc - timedelta(days=90)).date().isoformat(),
                 "endDate": now_utc.date().isoformat()},
         pick=first_of("opportunities", "data"))

    status, data, error = client.try_request("POST", "/contacts/search", json_body={
        "locationId": location_id, "page": 1, "pageLimit": 2,
        "filters": [{"field": "dateAdded", "operator": "range",
                     "value": {"gte": month_ago, "lte": now_utc.isoformat()}}],
        "sort": [{"field": "dateAdded", "direction": "desc"}],
    })
    lines.append(f"### Contacts search (dateAdded range; check attributionSource)\n`POST /contacts/search` → **HTTP {status}**\n")
    if error:
        lines.append(f"- error: {error}\n")
        print(f"  Contacts search: HTTP {status} — {error}")
    else:
        contacts = fetchers._first_list(data, "contacts", "data")
        if contacts:
            first_contact_id = contacts[0].get("id") or contacts[0].get("_id")
        lines.append(f"```json\n{json.dumps(_redact(contacts[0] if contacts else data), indent=2, default=str)[:2000]}\n```\n")
        print(f"  Contacts search: HTTP {status}")

    show("Contacts by tag", "POST", "/contacts/search", body={
        "locationId": location_id, "page": 1, "pageLimit": 2,
        "filters": [{"field": "tags", "operator": "eq", "value": metrics.REVIEW_TAG}],
    }, pick=first_of("contacts", "data"))

    show("Form submissions", "GET", "/forms/submissions", params={
        "locationId": location_id,
        "startAt": (now_utc - timedelta(days=30)).date().isoformat(),
        "endAt": now_utc.date().isoformat(),
        "limit": 2, "page": 1,
    }, pick=first_of("submissions", "data"))

    status, data, error = client.try_request("GET", "/conversations/search", params={
        "locationId": location_id, "limit": 2, "sortBy": "last_message_date", "sort": "desc"})
    lines.append(f"### Conversations\n`GET /conversations/search` → **HTTP {status}**\n")
    if error:
        lines.append(f"- error: {error}\n")
        print(f"  Conversations: HTTP {status} — {error}")
    else:
        convos = fetchers._first_list(data, "conversations", "data")
        if convos:
            first_convo_id = convos[0].get("id") or convos[0].get("_id")
        lines.append(f"```json\n{json.dumps(_redact(convos[0] if convos else data), indent=2, default=str)[:2000]}\n```\n")
        print(f"  Conversations: HTTP {status}")

    if first_convo_id:
        show("Messages (note nested messages.messages; source/userId vocabulary)", "GET",
             f"/conversations/{first_convo_id}/messages", params={"limit": 2})
    else:
        lines.append("### Messages\n- skipped: no conversation found\n")

    status, data, error = client.try_request("GET", "/calendars/", params={"locationId": location_id})
    lines.append(f"### Calendars\n`GET /calendars/` → **HTTP {status}**\n")
    if error:
        lines.append(f"- error: {error}\n")
        print(f"  Calendars: HTTP {status} — {error}")
    else:
        cals = fetchers._first_list(data, "calendars", "data")
        if cals:
            first_calendar_id = cals[0].get("id") or cals[0].get("_id")
        lines.append(f"```json\n{json.dumps(_redact(cals[0] if cals else data), indent=2, default=str)[:1500]}\n```\n")
        print(f"  Calendars: HTTP {status}")

    if first_calendar_id:
        show("Calendar events (check appointmentStatus + created field)", "GET", "/calendars/events", params={
            "locationId": location_id, "calendarId": first_calendar_id,
            "startTime": int((now_utc - timedelta(days=28)).timestamp() * 1000),
            "endTime": int((now_utc + timedelta(days=7)).timestamp() * 1000),
        }, pick=first_of("events", "data"))
    else:
        lines.append("### Calendar events\n- skipped: no calendar found\n")

    show("Invoices", "GET", "/invoices/",
         params={"altId": location_id, "altType": "location", "limit": 2, "offset": 0},
         pick=first_of("invoices", "data"))

    status, data, error = client.try_request("GET", "/blogs/site/all",
                                             params={"locationId": location_id, "limit": 2, "skip": 0})
    lines.append(f"### Blog sites\n`GET /blogs/site/all` → **HTTP {status}**\n")
    if error:
        lines.append(f"- error: {error}\n")
        print(f"  Blog sites: HTTP {status} — {error}")
    else:
        sites = fetchers._first_list(data, "data", "sites", "blogs")
        if sites:
            first_blog_id = sites[0].get("id") or sites[0].get("_id")
        lines.append(f"```json\n{json.dumps(_redact(sites[0] if sites else data), indent=2, default=str)[:1500]}\n```\n")
        print(f"  Blog sites: HTTP {status}")

    if first_blog_id:
        show("Blog posts", "GET", "/blogs/posts/all", params={
            "locationId": location_id, "blogId": first_blog_id,
            "limit": 2, "offset": 0, "status": "PUBLISHED",
        }, pick=first_of("blogs", "posts", "data"))
    else:
        lines.append("### Blog posts\n- skipped: no blog site found\n")

    show("Social posts", "POST", f"/social-media-posting/{location_id}/posts/list", body={
        "type": "all", "skip": 0, "limit": 2,
        "fromDate": ninety_ago, "toDate": now_utc.isoformat(), "includeUsers": False,
    }, pick=first_of("results", "posts", "data"))

    show("Social accounts (check expired/disconnected field)", "GET",
         f"/social-media-posting/{location_id}/accounts",
         pick=first_of("results", "accounts", "data"))

    if first_contact_id:
        show("Contact tasks (fallback)", "GET", f"/contacts/{first_contact_id}/tasks",
             pick=first_of("tasks", "data"))
    else:
        lines.append("### Contact tasks\n- skipped: no contact found\n")

    lines.append(
        "\n### Checklist (fix fetchers.py from the output above, rerun --probe)\n"
        "- [ ] contacts/search range filter accepted (HTTP 200, filtered results)?\n"
        "- [ ] `attributionSource` present on contacts?\n"
        "- [ ] opportunity timestamp fields present (`lastStatusChangeAt`, `lastStageChangeAt`, `lastActionDate`)?\n"
        "- [ ] `tasks` / `calendarEvents` populated on opportunities/search?\n"
        "- [ ] do `date`/`endDate` filter opportunities (compare open vs won calls)?\n"
        "- [ ] message `source` / `userId` values seen (update HUMAN/AUTOMATION sets in metrics.py)?\n"
        "- [ ] invoice `status` values seen?\n"
        "- [ ] forms/submissions envelope as parsed?\n"
        "- [ ] calendar event created field (`dateAdded` vs `createdAt`) and `appointmentStatus` values?\n"
        "- [ ] blog / social envelope keys as parsed?\n"
        "- [ ] social accounts expired/disconnected field name?\n"
    )
    return "".join(f"{line}\n" if not line.endswith("\n") else line for line in lines)


# -- backfill ------------------------------------------------------------------


def backfill_location(sub: dict, client: GHLClient, store, now_utc: datetime,
                      weeks_back: int, max_pages: int | None, dry_run: bool) -> int:
    location_id = sub["location_id"]
    tz = metrics.get_tz(sub.get("timezone"))
    cov = Coverage()
    today_local = now_utc.astimezone(tz).date()
    this_week = metrics.iso_week_start(today_local)
    weeks = [this_week - timedelta(days=7 * i) for i in range(weeks_back)]
    range_start = datetime.combine(min(weeks), datetime.min.time(), tzinfo=tz)

    contacts = fetchers.fetch_contacts_range(
        client, cov, location_id,
        gte_iso=range_start.astimezone(timezone.utc).isoformat(),
        lte_iso=now_utc.isoformat(),
        max_pages=max_pages)
    kept, _ = metrics.filter_exclusions(contacts)
    submissions = fetchers.fetch_form_submissions(
        client, cov, location_id,
        start_date=min(weeks).isoformat(),
        end_date=today_local.isoformat(),
        max_pages=max_pages)
    rows = metrics.weekly_lead_history(kept, submissions, tz, weeks)
    if not dry_run:
        store.upsert_lead_history(location_id, rows)
    return len(rows)


# -- run ---------------------------------------------------------------------


def resolve_location(subs: list[dict], key: str) -> dict | None:
    for sub in subs:
        if sub.get("location_id") == key or sub.get("slug") == key:
            return sub
    return None


def run(argv: list[str] | None = None, store=None, client_factory=None,
        now_utc: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collector", description="GHL account health collector")
    parser.add_argument("--probe", action="store_true", help="verify endpoint shapes for one location")
    parser.add_argument("--dry-run", action="store_true", help="fetch and compute, write nothing")
    parser.add_argument("--location", help="location_id or slug; parent is always included")
    parser.add_argument("--max-pages", type=int, default=None, help="page cap per paged source")
    parser.add_argument("--date", help="snapshot date YYYY-MM-DD (default: today local)")
    parser.add_argument("--backfill", type=int, metavar="N",
                        help="write N ISO weeks of lead_history from CRM history, then exit")
    args = parser.parse_args(argv)

    if store is None:
        from .store import Store
        store = Store()
    if client_factory is None:
        client_factory = GHLClient
    now_utc = now_utc or datetime.now(timezone.utc)
    run_date = date.fromisoformat(args.date) if args.date else now_utc.astimezone(run_timezone()).date()

    if not store.pit_key_ok():
        log("COLLECTOR_KEY rejected by pit_key_ok — aborting (check Vault bootstrap and env)")
        return 1

    subs = store.load_subaccounts(active=True)
    if not subs:
        log("no active subaccounts configured")
        return 1
    parent_sub = next((s for s in subs if s.get("is_parent")), None)

    targets = subs
    if args.location:
        chosen = resolve_location(subs, args.location)
        if not chosen:
            log(f"unknown location {args.location!r}")
            return 1
        targets = [chosen]

    # -- probe mode -----------------------------------------------------------
    if args.probe:
        sub = targets[0] if args.location else (parent_sub or subs[0])
        token = store.get_pit(sub["location_id"])
        if not token:
            log(f"no PIT stored for {sub.get('slug') or sub['location_id']} — add "
                f"ghl_pit_{sub['location_id']} in the Supabase Vault UI")
            return 1
        client = client_factory(token)
        report = probe_location(client, sub, now_utc)
        with open("VERIFICATION.md", "a") as fh:
            fh.write(report)
        log("probe appended to VERIFICATION.md")
        return 0

    # -- backfill mode ----------------------------------------------------------
    if args.backfill:
        failed = 0
        for sub in targets:
            slug = sub.get("slug") or sub["location_id"]
            token = store.get_pit(sub["location_id"])
            if not token:
                log(f"{slug}: no PIT stored — skipping backfill")
                failed += 1
                continue
            client = client_factory(token)
            rows = backfill_location(sub, client, store, now_utc, args.backfill,
                                     args.max_pages, args.dry_run)
            log(f"{slug}: backfilled {rows} lead_history weeks")
        return 0 if failed == 0 else 2

    # -- collection -----------------------------------------------------------
    run_id = None if args.dry_run else store.start_run()
    clients: list[GHLClient] = []
    results: dict[str, dict] = {}
    run_details: dict[str, dict] = {}
    ok = held = failed = 0
    errors: list[str] = []

    parent_ctx = ParentContext()
    if parent_sub:
        token = store.get_pit(parent_sub["location_id"])
        if token:
            parent_client = client_factory(token)
            clients.append(parent_client)
            parent_ctx = build_parent_context(parent_sub, parent_client, now_utc, args.max_pages)
            if not parent_ctx.available:
                log(f"parent context unavailable: {parent_ctx.error}")
        else:
            parent_ctx.error = "no PIT stored for parent"
            log("parent PIT missing; relationship metrics will be unavailable")
    else:
        parent_ctx.error = "no parent subaccount configured"
        log("no is_parent subaccount configured; relationship metrics unavailable")

    ordered = ([parent_sub] if parent_sub and (not args.location or parent_sub in targets) else [])
    ordered += [s for s in targets if not s.get("is_parent")]

    for sub in ordered:
        slug = sub.get("slug") or sub["location_id"]
        location_id = sub["location_id"]
        rotated = sub.get("token_rotated_at")
        if rotated:
            try:
                age = (run_date - date.fromisoformat(str(rotated))).days
                if age > TOKEN_ROTATION_WARN_DAYS:
                    log(f"{slug}: PIT is {age} days old — rotate soon (90-day policy)")
            except ValueError:
                pass

        if sub.get("is_parent") and parent_ctx.client is not None:
            client = parent_ctx.client
        else:
            token = store.get_pit(location_id)
            if not token:
                log(f"{slug}: no PIT stored — marking token_status=none")
                if not args.dry_run:
                    store.set_token_status(location_id, "none")
                failed += 1
                errors.append(f"{slug}: token missing")
                run_details[location_id] = {"status": "failed", "error": "token missing"}
                continue
            client = client_factory(token)
            clients.append(client)

        log(f"{slug}: collecting")
        started = time.monotonic()
        requests_before = client.requests_made
        rate_limited_before = client.rate_limited
        try:
            result = collect_location(sub, client, store, parent_ctx, now_utc, run_date, args.max_pages)
        except Exception as exc:  # noqa: BLE001 — one location must not kill the run
            failed += 1
            message = client.sanitize(f"{type(exc).__name__}: {exc}") if hasattr(client, "sanitize") else str(exc)
            errors.append(f"{slug}: {message}")
            run_details[location_id] = {"status": "failed", "error": message,
                                        "seconds": round(time.monotonic() - started, 1)}
            log(f"{slug}: FAILED — {message}")
            continue

        loc_detail = {
            "requests": client.requests_made - requests_before,
            "rate_limited": client.rate_limited - rate_limited_before,
            "seconds": round(time.monotonic() - started, 1),
        }

        if result.get("token_invalid"):
            failed += 1
            errors.append(f"{slug}: token invalid ({result.get('error')})")
            log(f"{slug}: token invalid — marking token_status=invalid")
            if not args.dry_run:
                store.set_token_status(location_id, "invalid", result.get("error"))
            run_details[location_id] = {**loc_detail, "status": "failed", "error": result.get("error")}
            continue

        if not args.dry_run:
            rotated_at = None
            try:
                stamp = store.pit_updated_at(location_id)
                if stamp:
                    rotated_at = str(stamp)[:10]
            except Exception:  # noqa: BLE001 — rotation bookkeeping must not fail the run
                rotated_at = None
            fields = {"token_status": "ok", "last_token_error": None}
            if rotated_at:
                fields["token_rotated_at"] = rotated_at
            store.update_subaccount(location_id, fields)
            store.upsert_snapshot(result["snapshot"])
            store.upsert_lead_events(result["lead_events"])
            store.upsert_lead_history(location_id, result["lead_history"])
        results[location_id] = {**result, "sub": sub}

        if result["gate_passed"]:
            ok += 1
            log(f"{slug}: snapshot written, gate passed")
        else:
            held += 1
            log(f"{slug}: snapshot HELD — {'; '.join(result['gate_reasons'])}")
        run_details[location_id] = {
            **loc_detail,
            "status": "ok" if result["gate_passed"] else "held",
            "gate": result["gate_reasons"],
            "error": None,
        }

    # -- peer pass, flags, and change tracking -----------------------------------
    subs_by_id = {s["location_id"]: s for s in subs}
    if args.dry_run:
        for location_id, result in results.items():
            sub = result["sub"]
            location_flags = flags_mod.compute_flags(
                result["metrics"], sub.get("thresholds"), result["details"], sub, today=run_date)
            printable = {
                "location": sub.get("slug") or location_id,
                "gate_passed": result["gate_passed"],
                "gate_reasons": result["gate_reasons"],
                "metrics": result["metrics"],
                "flags": location_flags,
            }
            print(json.dumps(printable, indent=2, default=str))
        log("dry run complete; nothing written")
        return 0

    peers = peer_pass(store, subs_by_id, run_date)
    for location_id, result in results.items():
        peer_value, peer_n = peers.get(location_id, (None, None))
        result["metrics"]["peer_median_delta_pct"] = peer_value
        result["metrics"]["peer_n"] = peer_n
        sub = result["sub"]
        location_flags = flags_mod.compute_flags(
            result["metrics"], sub.get("thresholds"), result["details"], sub, today=run_date)
        prior_codes = store.read_flags(location_id, run_date - timedelta(days=7))
        flags_new, flags_resolved = metrics.flags_changed(
            [f["code"] for f in location_flags], prior_codes)
        store.replace_flags(location_id, run_date.isoformat(), location_flags)
        result["details"]["changed"] = {"new": flags_new, "resolved": flags_resolved}
        store.update_snapshot_changes(location_id, run_date, flags_new, flags_resolved,
                                      result["details"])
        log(f"{sub.get('slug') or location_id}: {len(location_flags)} flags "
            f"(+{len(flags_new)} new, -{len(flags_resolved)} resolved)")

    requests_made = sum(c.requests_made for c in clients)
    rate_limited = sum(c.rate_limited for c in clients)
    status = "ok" if failed == 0 and held == 0 else ("failed" if ok == 0 and held == 0 else "partial")
    error_summary = "; ".join(errors)[:2000] or None
    if rate_limited:
        error_summary = f"{error_summary + '; ' if error_summary else ''}429s: {rate_limited}"
    if run_id is not None:
        store.finish_run(run_id, status, ok, held, failed, requests_made, rate_limited,
                         error_summary, details=run_details)
    log(f"done: {ok} ok, {held} held, {failed} failed, "
        f"{requests_made} requests ({rate_limited} rate limited)")
    return 0 if failed == 0 and held == 0 else 2


def main() -> None:
    try:
        sys.exit(run())
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 — crash path, exit 1 per spec
        log(f"CRASH: {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
