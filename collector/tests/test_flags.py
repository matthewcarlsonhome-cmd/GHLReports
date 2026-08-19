from datetime import date

from .. import flags as flags_mod

TODAY = date(2026, 8, 18)
SUB = {"services": ["content", "social", "ads"], "contract_end": None, "mrr": 2500}


def base_metrics(**overrides):
    values = {
        "leads_new_7d": 10, "leads_trailing_avg": 10.0, "trailing_n": 4,
        "leads_delta_pct": 0.0, "peer_median_delta_pct": 0.0, "peer_n": 5,
        "leads_by_source_7d": {"web": 10}, "leads_by_source_trailing": {"web": 10.0},
        "leads_unassigned_7d": 0, "leads_missing_phone_pct_7d": 0.0,
        "form_submissions_7d": 4, "form_submissions_trailing_avg": 4.0,
        "convos_active_7d": 5, "opps_created_7d": 2,
        "leads_uncontacted_24h": 0, "leads_no_human_touch_7d": 0,
        "speed_to_lead_median_min": 20.0, "speed_to_lead_p90_min": 60.0,
        "speed_kind_known": True, "excluded_count": 0,
        "convos_waiting": 0, "convos_waiting_max_hours": None,
        "opps_open": 10, "opps_open_value": 50000.0,
        "opps_stale": 0, "opps_stale_value": 0.0, "opps_stuck": 0, "opps_moved_30d": 5,
        "opps_missing_value": 0, "opps_no_next_step": 0,
        "opps_won_7d": 1, "opps_lost_7d": 0,
        "lead_to_opp_28d_pct": 20.0, "win_rate_90d": 50.0, "median_days_to_close_90d": 12.0,
        "appts_booked_7d": 3, "appts_showed_28d": 8, "appts_noshow_28d": 1, "noshow_rate_28d": 11.1,
        "blogs_published_30d": 4, "social_published_7d": 3, "days_since_last_publish": 2,
        "social_accounts_total": 2, "social_accounts_expired": 0,
        "invoices_past_due": 0, "invoices_past_due_amount": 0.0,
        "client_last_touch_days": 5, "client_next_appt_at": "2026-08-21T12:00:00Z",
        "review_asks_stale": 0, "review_ask_gap": 0,
    }
    values.update(overrides)
    return values


def codes(flag_list):
    return {f["code"]: f["severity"] for f in flag_list}


def compute(metric_overrides=None, thresholds=None, details=None, sub=None):
    return flags_mod.compute_flags(base_metrics(**(metric_overrides or {})),
                                   thresholds, details or {}, sub or SUB, today=TODAY)


def test_steady_account_has_no_flags():
    assert compute() == []


def test_pipeline_bottleneck_info_flag():
    # Real money parked in one stage -> info flag naming the stage
    result = codes(compute({"bottleneck_stage": "Quote", "bottleneck_value_usd": 30000.0}))
    assert result.get("PIPELINE_BOTTLENECK") == "info"
    # Small change stays quiet; unknown never fires
    assert "PIPELINE_BOTTLENECK" not in codes(compute({"bottleneck_stage": "Quote",
                                                       "bottleneck_value_usd": 500.0}))
    assert "PIPELINE_BOTTLENECK" not in codes(compute({"bottleneck_stage": None,
                                                       "bottleneck_value_usd": None}))


def test_pipeline_frozen():
    # 12 open deals, nothing moved in 30 days -> red, call the client
    result = codes(compute({"opps_open": 12, "opps_moved_30d": 0}))
    assert result.get("PIPELINE_FROZEN") == "red"
    # 1 of 40 moved (2.5% < 5%) -> amber, barely moving
    result = codes(compute({"opps_open": 40, "opps_moved_30d": 1}))
    assert result.get("PIPELINE_FROZEN") == "amber"
    # healthy movement -> quiet
    assert "PIPELINE_FROZEN" not in codes(compute({"opps_open": 40, "opps_moved_30d": 10}))
    # too few open deals to judge -> quiet
    assert "PIPELINE_FROZEN" not in codes(compute({"opps_open": 5, "opps_moved_30d": 0}))
    # movement unknown (older snapshot) -> quiet, unknown never fires
    assert "PIPELINE_FROZEN" not in codes(compute({"opps_open": 40, "opps_moved_30d": None}))


def test_pipeline_hygiene_replaces_stale_for_abandoned_pipelines():
    # 800 of 1000 open deals idle: a cleanup conversation, not a follow-up
    # list — PIPELINE_HYGIENE fires and suppresses STALE_PIPELINE entirely.
    result = codes(compute({"opps_open": 1000, "opps_stale": 800,
                            "opps_stale_value": 900000.0, "opps_moved_30d": 100}))
    assert result.get("PIPELINE_HYGIENE") == "amber"
    assert "STALE_PIPELINE" not in result
    # a small stale set keeps the classic per-deal STALE_PIPELINE framing
    result = codes(compute({"opps_open": 10, "opps_stale": 4, "opps_stale_value": 30000.0}))
    assert result.get("STALE_PIPELINE") == "red"
    assert "PIPELINE_HYGIENE" not in result


def test_integration_suspect_suppresses_leads_zero():
    result = codes(compute({"leads_new_7d": 0, "convos_active_7d": 0, "opps_created_7d": 0,
                            "form_submissions_7d": 0, "leads_delta_pct": None}))
    assert result.get("INTEGRATION_SUSPECT") == "red"
    assert "LEADS_ZERO" not in result


def test_integration_suspect_counts_forms_unavailable_as_silent():
    result = codes(compute({"leads_new_7d": 0, "convos_active_7d": 0, "opps_created_7d": 0,
                            "form_submissions_7d": None, "form_submissions_trailing_avg": None,
                            "leads_delta_pct": None}))
    assert result.get("INTEGRATION_SUSPECT") == "red"


def test_integration_suspect_blocked_by_live_forms():
    # forms still arriving -> not a total outage -> LEADS_ZERO instead
    result = codes(compute({"leads_new_7d": 0, "convos_active_7d": 0, "opps_created_7d": 0,
                            "form_submissions_7d": 2, "leads_delta_pct": None}))
    assert "INTEGRATION_SUSPECT" not in result
    assert result.get("LEADS_ZERO") == "red"


def test_form_silent_requires_history_and_live_leads():
    result = codes(compute({"form_submissions_7d": 0, "form_submissions_trailing_avg": 5.0}))
    assert result.get("FORM_SILENT") == "red"
    # trailing avg too small
    assert "FORM_SILENT" not in codes(compute({"form_submissions_7d": 0,
                                               "form_submissions_trailing_avg": 1.0}))
    # no leads at all -> the integration story, not the form story
    result = codes(compute({"form_submissions_7d": 0, "form_submissions_trailing_avg": 5.0,
                            "leads_new_7d": 0, "convos_active_7d": 0, "opps_created_7d": 0,
                            "leads_delta_pct": None}))
    assert "FORM_SILENT" not in result


def test_leads_drop_variants():
    assert codes(compute({"leads_delta_pct": -55.0, "peer_median_delta_pct": -5.0})
                 ).get("LEADS_DROP") == "red"
    assert codes(compute({"leads_delta_pct": -55.0, "peer_median_delta_pct": None})
                 ).get("LEADS_DROP") == "amber"
    result = codes(compute({"leads_delta_pct": -55.0, "peer_median_delta_pct": -35.0}))
    assert result.get("LEADS_DROP_SEASONAL") == "amber"
    assert "LEADS_DROP" not in result


def test_source_drop_major_source_only():
    # facebook is 40% of trailing volume at 4/wk and fell to zero -> red
    result = compute({
        "leads_by_source_7d": {"web": 6},
        "leads_by_source_trailing": {"facebook": 4.0, "web": 6.0},
    })
    by_code = codes(result)
    assert by_code.get("SOURCE_DROP") == "red"
    assert "facebook" in next(f for f in result if f["code"] == "SOURCE_DROP")["action"]

    # a minor source (low share, low avg) never fires
    assert "SOURCE_DROP" not in codes(compute({
        "leads_by_source_7d": {"web": 9},
        "leads_by_source_trailing": {"gmb": 1.0, "web": 9.0},
    }))

    # partial drop -> amber
    assert codes(compute({
        "leads_by_source_7d": {"facebook": 1, "web": 6},
        "leads_by_source_trailing": {"facebook": 4.0, "web": 6.0},
    })).get("SOURCE_DROP") == "amber"


def test_unassigned_and_unreachable():
    assert codes(compute({"leads_unassigned_7d": 3})).get("UNASSIGNED_LEADS") == "amber"
    assert codes(compute({"leads_unassigned_7d": 2, "leads_new_7d": 5})
                 ).get("UNASSIGNED_LEADS") == "amber"  # 40% share
    assert "UNASSIGNED_LEADS" not in codes(compute({"leads_unassigned_7d": 1}))
    assert codes(compute({"leads_missing_phone_pct_7d": 35.0})
                 ).get("LEADS_UNREACHABLE") == "amber"
    assert "LEADS_UNREACHABLE" not in codes(compute({"leads_missing_phone_pct_7d": None}))


def test_slow_response_amber_then_red():
    assert codes(compute({"leads_uncontacted_24h": 3})).get("SLOW_RESPONSE") == "amber"
    assert codes(compute({"leads_uncontacted_24h": 8})).get("SLOW_RESPONSE") == "red"
    assert codes(compute({"leads_new_7d": 6, "leads_no_human_touch_7d": 3})
                 ).get("SLOW_RESPONSE") == "red"
    assert "SLOW_RESPONSE" not in codes(compute({"leads_uncontacted_24h": 2}))


def test_convos_waiting_amber_then_red():
    details = {"waiting_convos": [{"conversation_id": "w1", "contact": "Friday Fred",
                                   "hours": 25.0, "deep_link": "x"}]}
    amber = compute({"convos_waiting": 2, "convos_waiting_max_hours": 6.0}, details=details)
    assert codes(amber).get("CONVOS_WAITING") == "amber"
    red = compute({"convos_waiting": 2, "convos_waiting_max_hours": 25.0}, details=details)
    assert codes(red).get("CONVOS_WAITING") == "red"
    flag = next(f for f in red if f["code"] == "CONVOS_WAITING")
    assert "Friday Fred" in flag["action"]


def test_stale_pipeline_count_vs_value_severity():
    assert codes(compute({"opps_stale": 4, "opps_stale_value": 8000.0})
                 ).get("STALE_PIPELINE") == "amber"
    assert codes(compute({"opps_stale": 1, "opps_stale_value": 30000.0})
                 ).get("STALE_PIPELINE") == "red"
    assert "STALE_PIPELINE" not in codes(compute({"opps_stale": 2, "opps_stale_value": 1000.0}))


def test_high_noshow():
    assert codes(compute({"noshow_rate_28d": 35.0})).get("HIGH_NOSHOW") == "amber"
    assert "HIGH_NOSHOW" not in codes(compute({"noshow_rate_28d": None}))


def test_no_delivery_gated_on_services():
    assert codes(compute({"days_since_last_publish": 20})).get("NO_DELIVERY") == "amber"
    assert codes(compute({"days_since_last_publish": 35})).get("NO_DELIVERY") == "red"
    ads_only = {**SUB, "services": ["ads", "seo"]}
    assert "NO_DELIVERY" not in codes(compute({"days_since_last_publish": 35}, sub=ads_only))


def test_social_disconnected():
    assert codes(compute({"social_accounts_expired": 1})).get("SOCIAL_DISCONNECTED") == "red"
    ads_only = {**SUB, "services": ["ads"]}
    assert "SOCIAL_DISCONNECTED" not in codes(compute({"social_accounts_expired": 1}, sub=ads_only))


def test_past_due_severity_by_age_and_amount():
    details_old = {"past_due_invoices": [{"invoice_id": "i1", "number": "42",
                                          "days_over": 45, "amount_due": 100.0}]}
    details_new = {"past_due_invoices": [{"invoice_id": "i2", "number": "43",
                                          "days_over": 5, "amount_due": 100.0}]}
    assert codes(compute({"invoices_past_due": 1, "invoices_past_due_amount": 100.0},
                         details=details_old)).get("PAST_DUE") == "red"
    assert codes(compute({"invoices_past_due": 1, "invoices_past_due_amount": 3000.0},
                         details=details_new)).get("PAST_DUE") == "red"
    assert codes(compute({"invoices_past_due": 1, "invoices_past_due_amount": 100.0},
                         details=details_new)).get("PAST_DUE") == "amber"


def test_no_client_touch_suppressed_by_next_appt():
    assert "NO_CLIENT_TOUCH" not in codes(compute({"client_last_touch_days": 40}))
    assert codes(compute({"client_last_touch_days": 40, "client_next_appt_at": None})
                 ).get("NO_CLIENT_TOUCH") == "amber"
    assert codes(compute({"client_last_touch_days": 50, "client_next_appt_at": None})
                 ).get("NO_CLIENT_TOUCH") == "red"


def test_renewal_soon_info_unless_red_present():
    renewing = {**SUB, "contract_end": "2026-09-30"}   # 43 days out
    assert codes(compute(sub=renewing)).get("RENEWAL_SOON") == "info"
    # with a red flag on the account it escalates to amber
    result = codes(compute({"opps_stale": 1, "opps_stale_value": 30000.0}, sub=renewing))
    assert result.get("RENEWAL_SOON") == "amber"
    far_out = {**SUB, "contract_end": "2027-06-30"}
    assert "RENEWAL_SOON" not in codes(compute(sub=far_out))


def test_review_ask_gap_info():
    assert codes(compute({"review_ask_gap": 2})).get("REVIEW_ASK_GAP") == "info"
    assert "REVIEW_ASK_GAP" not in codes(compute({"review_ask_gap": 1}))


def test_thresholds_override_per_account():
    assert "SLOW_RESPONSE" not in codes(compute({"leads_uncontacted_24h": 3},
                                                thresholds={"slow_response_min": 10}))
    # unknown keys and non-numeric values are ignored
    assert codes(compute({"leads_uncontacted_24h": 3},
                         thresholds={"slow_response_min": "high", "bogus": 1})
                 ).get("SLOW_RESPONSE") == "amber"
    # the v3 example keys work: raise the stale-value bar
    assert "STALE_PIPELINE" not in codes(compute(
        {"opps_stale": 1, "opps_stale_value": 30000.0},
        thresholds={"stale_value_usd": 50000}))
