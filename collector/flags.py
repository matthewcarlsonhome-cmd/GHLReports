"""Flag catalog from spec section 5. Pure functions: inputs are the metrics
dict, per-account threshold overrides, and the details dict (for entity
references and deep links). Output rows match the `flags` table columns.

Severity weights for the attention sort (red 3, amber 1, info 0) live in the
v_portfolio SQL; this module only assigns severities.
"""

from __future__ import annotations

DEFAULT_THRESHOLDS = {
    "leads_drop_pct": -40.0,          # delta at or below this is a drop
    "peer_held_pct": -20.0,           # peers above this count as "held"
    "dormant_trailing_min": 3.0,      # trailing avg needed for zero-lead flags
    "slow_response_min": 3,           # uncontacted count -> amber
    "slow_response_red": 8,           # uncontacted count -> red
    "no_human_ratio": 0.3,            # no-human-touch / leads -> amber
    "no_human_ratio_red": 0.5,        # -> red
    "no_human_min_leads": 5,
    "convo_wait_red_hours": 24.0,
    "stale_min": 3,
    "stale_frac": 0.3,
    "stale_value_red": 25000.0,
    "no_delivery_days": 14,
    "no_delivery_red_days": 30,
    "past_due_red_days": 30,
    "past_due_red_amount": 2500.0,
    "client_touch_days": 30,
    "client_touch_red_days": 45,
    "review_gap_min": 2,
}


def merged_thresholds(overrides: dict | None) -> dict:
    merged = dict(DEFAULT_THRESHOLDS)
    for key, value in (overrides or {}).items():
        if key in merged and isinstance(value, (int, float)):
            merged[key] = value
    return merged


def _flag(code: str, severity: str, title: str, action: str, detail: str | None = None,
          entity_type: str | None = None, entity_id: str | None = None,
          entity_name: str | None = None, deep_link: str | None = None) -> dict:
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "detail": detail,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_name": entity_name,
        "deep_link": deep_link,
    }


def _fmt_money(value) -> str:
    try:
        return f"${value:,.0f}"
    except (TypeError, ValueError):
        return "$?"


def compute_flags(metrics: dict, thresholds: dict | None, details: dict,
                  services: list[str] | None) -> list[dict]:
    th = merged_thresholds(thresholds)
    flags: list[dict] = []

    leads = metrics.get("leads_new_7d")
    trailing = metrics.get("leads_trailing_avg")
    delta = metrics.get("leads_delta_pct")
    peer = metrics.get("peer_median_delta_pct")
    convos_active = metrics.get("convos_active_7d")
    opps_created = metrics.get("opps_created_7d")

    # INTEGRATION_SUSPECT / LEADS_ZERO
    integration_suspect = (
        leads == 0 and convos_active == 0 and opps_created == 0
        and trailing is not None and trailing >= th["dormant_trailing_min"]
    )
    if integration_suspect:
        flags.append(_flag(
            "INTEGRATION_SUSPECT", "red", "Nothing flowing at all",
            "Nothing flowing at all. Likely a broken form, phone, or webhook, not a quiet week.",
        ))
    elif leads == 0 and trailing is not None and trailing >= th["dormant_trailing_min"]:
        flags.append(_flag(
            "LEADS_ZERO", "red", "Zero leads this week",
            f"Zero leads vs {trailing:.1f} average. Check integration before calling.",
        ))

    # LEADS_DROP / LEADS_DROP_SEASONAL
    if delta is not None and delta <= th["leads_drop_pct"]:
        if peer is not None and peer <= th["peer_held_pct"]:
            flags.append(_flag(
                "LEADS_DROP_SEASONAL", "amber", "Leads down with peers",
                f"Leads down {abs(delta):.0f}%, peers down {abs(peer):.0f}%. "
                "Likely seasonal; mention proactively.",
            ))
        else:
            severity = "red" if peer is not None and peer > th["peer_held_pct"] else "amber"
            flags.append(_flag(
                "LEADS_DROP", severity, "Leads down vs baseline",
                f"Leads down {abs(delta):.0f}% vs baseline while peers held. "
                "Call before they call you.",
                detail=None if peer is not None else "peers or baseline not yet available",
            ))

    # SLOW_RESPONSE
    uncontacted = metrics.get("leads_uncontacted_24h")
    no_human = metrics.get("leads_no_human_touch_7d")
    ratio = None
    if no_human is not None and leads:
        ratio = no_human / leads
    ratio_fires = (
        ratio is not None and ratio >= th["no_human_ratio"] and (leads or 0) >= th["no_human_min_leads"]
    )
    if (uncontacted is not None and uncontacted >= th["slow_response_min"]) or ratio_fires:
        severity = "amber"
        if (ratio is not None and ratio >= th["no_human_ratio_red"]) or \
                (uncontacted is not None and uncontacted >= th["slow_response_red"]):
            severity = "red"
        first = (details.get("uncontacted_leads") or [{}])[0]
        flags.append(_flag(
            "SLOW_RESPONSE", severity, "Leads sitting uncontacted",
            f"{uncontacted or 0} leads uncontacted >24h. Send the response-time report; "
            "it reframes \"bad leads.\"",
            entity_type="contact", entity_id=first.get("contact_id"),
            entity_name=first.get("name"), deep_link=first.get("deep_link"),
        ))

    # CONVOS_WAITING
    waiting = metrics.get("convos_waiting")
    max_hours = metrics.get("convos_waiting_max_hours")
    if waiting:
        severity = "red" if (max_hours or 0) >= th["convo_wait_red_hours"] else "amber"
        oldest = (details.get("waiting_convos") or [{}])[0]
        flags.append(_flag(
            "CONVOS_WAITING", severity, "Inbound conversations waiting",
            f"{waiting} inbound waiting, longest {max_hours:.0f}h. "
            f"Oldest: {oldest.get('contact', '(no name)')}.",
            entity_type="conversation", entity_id=oldest.get("conversation_id"),
            entity_name=oldest.get("contact"), deep_link=oldest.get("deep_link"),
        ))

    # STALE_PIPELINE
    stale = metrics.get("opps_stale")
    stale_value = metrics.get("opps_stale_value") or 0
    opps_open = metrics.get("opps_open") or 0
    if stale is not None:
        count_fires = stale >= max(th["stale_min"], th["stale_frac"] * opps_open)
        value_fires = stale_value >= th["stale_value_red"]
        if (stale and count_fires) or value_fires:
            first = (details.get("stale_opps") or [{}])[0]
            flags.append(_flag(
                "STALE_PIPELINE", "red" if value_fires else "amber", "Stale pipeline",
                f"{stale} deals idle 14d+ worth {_fmt_money(stale_value)}. Re-engage; "
                "Q3 spring-origin inquiries are re-engage, not close-lost.",
                entity_type="opportunity", entity_id=first.get("opp_id"),
                entity_name=first.get("name"), deep_link=first.get("deep_link"),
            ))

    # NO_DELIVERY
    days_since = metrics.get("days_since_last_publish")
    delivery_applies = bool({"content", "social"} & set(services or []))
    if delivery_applies and days_since is not None and days_since >= th["no_delivery_days"]:
        severity = "red" if days_since >= th["no_delivery_red_days"] else "amber"
        flags.append(_flag(
            "NO_DELIVERY", severity, "Nothing published recently",
            f"Nothing published in {days_since} days. Our gap; fix before the client notices.",
        ))

    # PAST_DUE
    past_due = metrics.get("invoices_past_due")
    if past_due:
        amount = metrics.get("invoices_past_due_amount") or 0
        oldest = (details.get("past_due_invoices") or [{}])[0]
        oldest_days = oldest.get("days_over") or 0
        severity = "red" if oldest_days > th["past_due_red_days"] or amount >= th["past_due_red_amount"] else "amber"
        flags.append(_flag(
            "PAST_DUE", severity, "SSP invoices past due",
            f"{_fmt_money(amount)} past due, oldest {oldest_days}d. "
            "Coordinate with billing before the next call.",
            entity_type="invoice", entity_id=oldest.get("invoice_id"),
            entity_name=oldest.get("number"),
        ))

    # NO_CLIENT_TOUCH
    last_touch = metrics.get("client_last_touch_days")
    next_appt = metrics.get("client_next_appt_at")
    if last_touch is not None and last_touch >= th["client_touch_days"] and not next_appt:
        severity = "red" if last_touch >= th["client_touch_red_days"] else "amber"
        flags.append(_flag(
            "NO_CLIENT_TOUCH", severity, "No recent client contact",
            f"No client contact in {last_touch} days, nothing scheduled. Book a check-in.",
        ))

    # REVIEW_ASK_GAP
    gap = metrics.get("review_ask_gap")
    if gap is not None and gap >= th["review_gap_min"]:
        flags.append(_flag(
            "REVIEW_ASK_GAP", "info", "Recent wins missing review asks",
            f"{gap} recent wins never got a review ask.",
        ))

    return flags
