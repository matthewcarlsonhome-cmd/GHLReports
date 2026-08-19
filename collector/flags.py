"""Flag catalog from spec section 5 (v3). Pure functions: inputs are the
metrics dict, per-account threshold overrides, the details dict (for entity
references and deep links), and the subaccount row (services, contract_end).
Output rows match the `flags` table columns.

Acknowledgements do not live here: v_portfolio weighs acked flags at zero via
the flag_acks lateral join, so this module always emits the full truth.

How this fits in:
    The last step of the math layer. metrics.py produces numbers; this file
    compares them to thresholds and emits flag rows the dashboard displays.
    It never fetches or computes — only compares and formats.

Key ideas to understand this file:
  * Severity levels: "red" = act today, "amber" = worth attention, "info" =
    context for the next conversation. Some flags escalate amber -> red as
    the numbers worsen.
  * Threshold overrides: DEFAULT_THRESHOLDS holds the global tuning knobs;
    each account may override individual keys (merged_thresholds), so one
    seasonal client's normal quiet spell doesn't page anyone.
  * None-safety: a missing metric (None) means "unknown", and unknown never
    fires a flag — every check first proves the metric exists.
  * Why acknowledgements aren't here: emitting the full truth every run and
    letting the database down-weight acked flags means a flag can never be
    accidentally lost by being "already seen".
"""

from __future__ import annotations

from datetime import date, timedelta

# Global tuning knobs, grouped by flag family. Per-account overrides replace
# individual values (see merged_thresholds); a few entries are consumed by
# metrics.py rather than here (marked "consumed by metrics").
DEFAULT_THRESHOLDS = {
    # leads
    "lead_drop_pct": -40.0,           # delta at or below this is a drop
    "peer_held_pct": -20.0,           # peers above this count as "held"
    "dormant_trailing_min": 3.0,      # trailing avg needed for zero-lead flags
    # per-source drop
    "source_weekly_avg_min": 3.0,     # trailing weekly avg for a source to matter
    "source_share_pct": 25.0,         # share of trailing volume for a source to matter
    "source_drop_factor": 0.4,        # current <= factor * avg fires
    # lead hygiene
    "unassigned_min": 3,
    "unassigned_share_pct": 30.0,
    "missing_phone_pct": 30.0,
    # forms
    "form_silent_trailing_min": 3.0,
    "form_silent_days": 3,            # business days without a submission before a form is 'silent' (consumed by metrics)
    # response
    "slow_response_min": 3,
    "slow_response_red": 8,
    "no_human_ratio": 0.3,
    "no_human_ratio_red": 0.5,
    "no_human_min_leads": 5,
    "convo_wait_hours": 4.0,          # consumed by metrics (waiting threshold)
    "convo_wait_red_hours": 24.0,
    # pipeline
    "opp_idle_days": 14.0,            # consumed by metrics (stale threshold)
    "opp_stuck_days": 30.0,           # consumed by metrics (stuck threshold)
    "stale_min": 3,
    "stale_frac": 0.3,
    "stale_value_usd": 25000.0,
    "hygiene_stale_min": 50,          # stale count where per-deal follow-up stops being the story
    "hygiene_stale_frac": 0.6,        # ...and the share of open deals that makes it a cleanup problem
    "frozen_min_open": 10,            # open deals needed before "nothing moved" means anything
    "frozen_moved_pct": 5.0,          # under this % of deals moved in 30d -> pipeline is stalling
    "bottleneck_min_usd": 10000.0,    # idle dollars in one stage before the bottleneck is worth naming
    # appointments
    "noshow_rate_pct": 30.0,
    # delivery
    "no_delivery_days": 14,
    "no_delivery_red_days": 30,
    # relationship
    "client_touch_days": 30,
    "client_touch_red_days": 45,
    "renewal_days": 60,
    # reviews
    "review_gap_min": 2,
}


def merged_thresholds(overrides: dict | None) -> dict:
    """Defaults with per-account overrides applied on top.

    Only keys that already exist in DEFAULT_THRESHOLDS and carry numeric
    values are accepted — a typo'd or malformed override is silently ignored
    rather than adding a knob nothing reads.
    """
    merged = dict(DEFAULT_THRESHOLDS)
    for key, value in (overrides or {}).items():
        if key in merged and isinstance(value, (int, float)):
            merged[key] = value
    return merged


def _flag(code: str, severity: str, title: str, action: str, detail: str | None = None,
          entity_type: str | None = None, entity_id: str | None = None,
          entity_name: str | None = None, deep_link: str | None = None) -> dict:
    """Build one flag row (matches the `flags` table columns).

    The entity_* fields optionally point at a concrete example record — the
    oldest waiting conversation, the first unassigned lead — so the dashboard
    can deep-link straight to it.
    """
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
    """Format as whole dollars with thousands separators; "$?" if not a number."""
    try:
        return f"${value:,.0f}"
    except (TypeError, ValueError):
        return "$?"


def compute_flags(metrics: dict, thresholds: dict | None, details: dict,
                  sub: dict, today: date | None = None) -> list[dict]:
    """Evaluate every flag rule against one account's metrics.

    Inputs: the metrics dict, this account's threshold overrides, `details`
    (example records with deep links, keyed by list name), and `sub` (the
    subaccount row: services purchased, contract_end). Returns flag rows in
    catalog order; an empty list means a healthy account.
    """
    th = merged_thresholds(thresholds)
    services = sub.get("services") or []
    flags: list[dict] = []

    # Pull the frequently reused metrics once. Any of these can be None
    # ("unknown"), and None must never fire a flag.
    leads = metrics.get("leads_new_7d")
    trailing = metrics.get("leads_trailing_avg")
    delta = metrics.get("leads_delta_pct")
    peer = metrics.get("peer_median_delta_pct")
    convos_active = metrics.get("convos_active_7d")
    opps_created = metrics.get("opps_created_7d")
    forms_7d = metrics.get("form_submissions_7d")
    forms_avg = metrics.get("form_submissions_trailing_avg")

    # INTEGRATION_SUSPECT / LEADS_ZERO — mutually exclusive by construction.
    # Everything at zero *at once* in an account that normally gets leads
    # smells like a broken integration; zero leads alone (while conversations
    # still flow) is merely a very bad week. Both need a real baseline
    # (dormant_trailing_min) so dormant accounts stay quiet.
    integration_suspect = (
        leads == 0 and convos_active == 0 and opps_created == 0
        and (forms_7d == 0 or forms_7d is None)
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

    # FORM_SILENT — forms went quiet while leads still arrive from elsewhere,
    # isolating the failure to the form/webhook. Suppressed when
    # INTEGRATION_SUSPECT already covers the outage.
    if (forms_7d == 0 and forms_avg is not None
            and forms_avg >= th["form_silent_trailing_min"]
            and (leads or 0) > 0 and not integration_suspect):
        flags.append(_flag(
            "FORM_SILENT", "red", "Forms silent",
            "Forms silent while other channels are alive. Form or webhook is likely broken; "
            "check before the client notices.",
        ))

    # LEADS_DROP / LEADS_DROP_SEASONAL — the peer comparison decides which:
    # peers also down => seasonal (amber, talking point); peers held while
    # this account dropped => account-specific problem (red).
    if delta is not None and delta <= th["lead_drop_pct"]:
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
                detail=None if peer is not None else "peers not yet available",
            ))

    # SOURCE_DROP — per offending source, capped at 3. The total can look
    # fine while one channel (e.g. Google Ads) silently died; only sources
    # that are both sizable (weekly avg) and material (share of volume) can
    # fire. Iterating biggest-source-first makes the cap keep the ones that
    # matter most.
    by_source_now = metrics.get("leads_by_source_7d") or {}
    by_source_trailing = metrics.get("leads_by_source_trailing") or {}
    trailing_total = sum(by_source_trailing.values())
    source_flags = 0
    for source, weekly_avg in sorted(by_source_trailing.items(), key=lambda kv: -kv[1]):
        if source_flags >= 3:
            break
        if weekly_avg < th["source_weekly_avg_min"] or trailing_total <= 0:
            continue
        if weekly_avg / trailing_total * 100.0 < th["source_share_pct"]:
            continue
        # Fires when the source fell to <= 40% (source_drop_factor) of its
        # usual weekly volume; a dead-zero source escalates to red.
        current = by_source_now.get(source, 0)
        if current <= th["source_drop_factor"] * weekly_avg:
            flags.append(_flag(
                "SOURCE_DROP", "red" if current == 0 else "amber", f"Source drop: {source}",
                f"Leads from {source} went from {weekly_avg:.1f}/wk to {current}. "
                "Check that channel specifically (form, ad account, phone routing).",
            ))
            source_flags += 1

    # UNASSIGNED_LEADS — fires on an absolute count OR a share of the week's
    # leads, so both busy accounts (3+ strays) and quiet ones (1 of 3 leads
    # ownerless) get caught.
    unassigned = metrics.get("leads_unassigned_7d")
    if unassigned is not None and leads is not None:
        share = unassigned / leads * 100.0 if leads else 0.0
        if unassigned >= th["unassigned_min"] or (leads and share >= th["unassigned_share_pct"] and unassigned > 0):
            # `or [{}]` gives an empty-dict fallback so .get() calls below
            # are safe when the details list is empty (same trick throughout).
            first = (details.get("unassigned_leads") or [{}])[0]
            flags.append(_flag(
                "UNASSIGNED_LEADS", "amber", "New leads with no owner",
                f"{unassigned} new leads have no owner. Nobody will call them. Fix assignment rules.",
                entity_type="contact", entity_id=first.get("contact_id"),
                entity_name=first.get("name"), deep_link=first.get("deep_link"),
            ))

    # LEADS_UNREACHABLE — too many new leads with no phone number.
    missing_phone = metrics.get("leads_missing_phone_pct_7d")
    if missing_phone is not None and missing_phone >= th["missing_phone_pct"]:
        flags.append(_flag(
            "LEADS_UNREACHABLE", "amber", "New leads missing phone numbers",
            f"{missing_phone:.0f}% of new leads have no phone number. Check form fields.",
        ))

    # SLOW_RESPONSE — two independent triggers: N leads sitting uncontacted
    # for 24h+, or a high share of leads that only ever got automated
    # replies (the ratio needs no_human_min_leads to be meaningful).
    # Either trigger crossing its "red" threshold escalates the severity.
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

    # CONVOS_WAITING — customers who spoke last and are still waiting
    # (weekend-adjusted hours, computed in metrics). Red once the longest
    # wait passes a full day.
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

    # PIPELINE_HYGIENE vs STALE_PIPELINE — same underlying numbers, two very
    # different conversations. When MOST of a big pipeline is idle (hundreds
    # or thousands of deals), "re-engage each one" is useless advice: that is
    # an abandoned-pipeline hygiene problem, and the action is a cleanup
    # session with the client. Only when the stale set is small enough to be
    # a work list does STALE_PIPELINE fire with its per-deal framing. The two
    # are mutually exclusive by construction.
    stale = metrics.get("opps_stale")
    stale_value = metrics.get("opps_stale_value") or 0
    opps_open = metrics.get("opps_open") or 0
    hygiene_fires = (
        stale is not None
        and stale >= th["hygiene_stale_min"]
        and opps_open > 0 and stale >= th["hygiene_stale_frac"] * opps_open
    )
    if hygiene_fires:
        pct = stale / opps_open * 100.0
        flags.append(_flag(
            "PIPELINE_HYGIENE", "amber", "Pipeline needs a cleanup",
            f"{stale} of {opps_open} open deals ({pct:.0f}%) idle 14d+ — too many for "
            "deal-by-deal follow-up. Book a pipeline cleanup with the client: close "
            "out dead deals so the real ones become visible.",
            detail=f"{_fmt_money(stale_value)} nominally at stake, but most of it is likely historical",
        ))
    elif stale is not None:
        count_fires = stale >= max(th["stale_min"], th["stale_frac"] * opps_open)
        value_fires = stale_value >= th["stale_value_usd"]
        if (stale and count_fires) or value_fires:
            first = (details.get("stale_opps") or [{}])[0]
            flags.append(_flag(
                "STALE_PIPELINE", "red" if value_fires else "amber", "Stale pipeline",
                f"{stale} deals idle 14d+ worth {_fmt_money(stale_value)}. Re-engage; "
                "Q3 spring-origin inquiries are re-engage, not close-lost.",
                entity_type="opportunity", entity_id=first.get("opp_id"),
                entity_name=first.get("name"), deep_link=first.get("deep_link"),
            ))

    # PIPELINE_FROZEN — nothing (or almost nothing) has moved in 30 days
    # despite a real book of open deals: no stage changes, no new deals, no
    # closes. Distinct from stale (which is per-deal idleness): this is the
    # whole pipeline not being worked — the strongest "client stopped using
    # the CRM for sales, reach out" signal we can compute.
    moved = metrics.get("opps_moved_30d")
    if (moved is not None and opps_open >= th["frozen_min_open"]):
        moved_pct = moved / opps_open * 100.0
        if moved == 0:
            flags.append(_flag(
                "PIPELINE_FROZEN", "red", "Pipeline frozen",
                f"{opps_open} open deals and not one moved in 30 days (no stage changes, "
                "no new deals, no closes). Call the client — the pipeline isn't being worked.",
            ))
        elif moved_pct < th["frozen_moved_pct"]:
            flags.append(_flag(
                "PIPELINE_FROZEN", "amber", "Pipeline barely moving",
                f"Only {moved} of {opps_open} open deals ({moved_pct:.0f}%) moved in 30 days. "
                "Walk the client through their pipeline on the next call.",
            ))

    # PIPELINE_BOTTLENECK — info only: the single stage holding the most
    # idle dollars, when it's real money. Context for the next client call
    # ("start the pipeline review at Quote"), not an alarm — the stale and
    # hygiene flags above carry any urgency.
    bottleneck_stage = metrics.get("bottleneck_stage")
    bottleneck_value = metrics.get("bottleneck_value_usd")
    if (bottleneck_stage and bottleneck_value is not None
            and bottleneck_value >= th["bottleneck_min_usd"]):
        flags.append(_flag(
            "PIPELINE_BOTTLENECK", "info", "Pipeline bottleneck",
            f"{_fmt_money(bottleneck_value)} sits idle in '{bottleneck_stage}' — "
            "start the pipeline review there.",
        ))

    # HIGH_NOSHOW — appointment no-show rate over 28d (None below 5 outcomes,
    # so it can't fire on tiny samples).
    noshow = metrics.get("noshow_rate_28d")
    if noshow is not None and noshow >= th["noshow_rate_pct"]:
        flags.append(_flag(
            "HIGH_NOSHOW", "amber", "High no-show rate",
            f"{noshow:.0f}% no-show over 28 days. Confirmation and reminder flow needs attention.",
        ))

    # NO_DELIVERY — our own output gap, only for accounts paying for
    # content/social. Ambers at two quiet weeks, reds at a month.
    days_since = metrics.get("days_since_last_publish")
    delivery_applies = bool({"content", "social"} & set(services))
    if delivery_applies and days_since is not None and days_since >= th["no_delivery_days"]:
        severity = "red" if days_since >= th["no_delivery_red_days"] else "amber"
        flags.append(_flag(
            "NO_DELIVERY", severity, "Nothing published recently",
            f"Nothing published in {days_since} days. Our gap; fix before the client notices.",
        ))

    # SOCIAL_DISCONNECTED — always red: a dead connection silently drops
    # every scheduled post until someone reconnects it.
    expired = metrics.get("social_accounts_expired")
    if "social" in services and expired:
        flags.append(_flag(
            "SOCIAL_DISCONNECTED", "red", "Social account disconnected",
            f"{expired} social account(s) disconnected; scheduled posts are silently failing. "
            "Reconnect in Social Planner.",
        ))

    # NO_CLIENT_TOUCH — quiet client relationship AND nothing on the
    # calendar; a booked meeting suppresses the flag entirely.
    last_touch = metrics.get("client_last_touch_days")
    next_appt = metrics.get("client_next_appt_at")
    if last_touch is not None and last_touch >= th["client_touch_days"] and not next_appt:
        severity = "red" if last_touch >= th["client_touch_red_days"] else "amber"
        flags.append(_flag(
            "NO_CLIENT_TOUCH", severity, "No recent client contact",
            f"No client contact in {last_touch} days, nothing scheduled. Book a check-in.",
        ))

    # RENEWAL_SOON — info, amber if the account also has any red (renewal
    # talks while things are visibly broken deserve extra attention, which
    # is why this check runs after every other flag).
    contract_end = sub.get("contract_end")
    if contract_end and today:
        # contract_end may arrive as a date or an ISO string; bad strings
        # simply disable the check.
        try:
            end_date = contract_end if isinstance(contract_end, date) else date.fromisoformat(str(contract_end))
        except ValueError:
            end_date = None
        if end_date and today <= end_date <= today + timedelta(days=int(th["renewal_days"])):
            has_red = any(f["severity"] == "red" for f in flags)
            flags.append(_flag(
                "RENEWAL_SOON", "amber" if has_red else "info", "Renewal approaching",
                f"Renewal in {(end_date - today).days} days. Book the review now.",
            ))

    # REVIEW_ASK_GAP — recent wins whose contact never got the review-request
    # tag. Info only: an opportunity, not a fire.
    gap = metrics.get("review_ask_gap")
    if gap is not None and gap >= th["review_gap_min"]:
        flags.append(_flag(
            "REVIEW_ASK_GAP", "info", "Recent wins missing review asks",
            f"{gap} recent wins never got a review ask.",
        ))

    # FORM_WENT_SILENT / SURVEY_WENT_SILENT — per-form inventory checks
    # (docs/FORMS-INTEGRATION.md Phase 1). Distinct from FORM_SILENT above:
    # that one fires when the account's TOTAL form volume dies; these name
    # the individual forms/surveys that used to produce and stopped, even
    # while the account total still looks fine.
    def _silent_flag(code: str, noun: str, silent_rows: list) -> None:
        names = ", ".join(r.get("name") or "?" for r in silent_rows[:5])
        more = f" (+{len(silent_rows) - 5} more)" if len(silent_rows) > 5 else ""
        flags.append(_flag(
            code, "amber", f"{noun}(s) went silent",
            f"These {noun.lower()}s received submissions before but nothing recently. "
            "Test each one and check the automations behind it.",
            detail=f"{len(silent_rows)} silent: {names}{more}",
        ))

    form_health = (details or {}).get("form_health") or {}
    silent_forms = form_health.get("forms_silent") or []
    if silent_forms:
        _silent_flag("FORM_WENT_SILENT", "Form", silent_forms)
    silent_surveys = form_health.get("surveys_silent") or []
    if silent_surveys:
        _silent_flag("SURVEY_WENT_SILENT", "Survey", silent_surveys)

    # WORKFLOWS_NONE_PUBLISHED — leads are arriving but not a single workflow
    # is published, so nothing is automating follow-up. Ported from the MLH
    # checker's best rule; needs the workflows.readonly scope (None = scope
    # not granted yet = never fires).
    workflows = form_health.get("workflows")
    if (workflows and workflows.get("total", 0) > 0 and workflows.get("published", 0) == 0
            and ((leads or 0) > 0 or (forms_7d or 0) > 0)):
        flags.append(_flag(
            "WORKFLOWS_NONE_PUBLISHED", "amber", "No published workflows",
            "Leads are coming in but no workflow is published — follow-up is all manual. "
            "Publish (or fix) the intake automation.",
            detail=f"{workflows['total']} workflows exist, 0 published",
        ))

    return flags
