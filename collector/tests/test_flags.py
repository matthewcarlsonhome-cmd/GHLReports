from .. import flags as flags_mod

SERVICES = ["content", "social", "ads"]


def base_metrics(**overrides):
    values = {
        "leads_new_7d": 10, "leads_trailing_avg": 10.0, "trailing_n": 4,
        "leads_delta_pct": 0.0, "peer_median_delta_pct": 0.0, "peer_n": 5,
        "convos_active_7d": 5, "opps_created_7d": 2,
        "leads_uncontacted_24h": 0, "leads_no_human_touch_7d": 0,
        "speed_to_lead_median_min": 20.0, "speed_to_lead_p90_min": 60.0,
        "speed_kind_known": True, "excluded_count": 0,
        "convos_waiting": 0, "convos_waiting_max_hours": None,
        "opps_open": 10, "opps_open_value": 50000.0,
        "opps_stale": 0, "opps_stale_value": 0.0, "opps_stuck": 0,
        "opps_missing_value": 0, "opps_no_next_step": 0,
        "opps_won_7d": 1, "opps_lost_7d": 0,
        "blogs_published_30d": 4, "social_published_7d": 3, "days_since_last_publish": 2,
        "invoices_past_due": 0, "invoices_past_due_amount": 0.0,
        "client_last_touch_days": 5, "client_next_appt_at": "2026-08-21T12:00:00Z",
        "review_asks_stale": 0, "review_ask_gap": 0,
    }
    values.update(overrides)
    return values


def codes(flag_list):
    return {f["code"]: f["severity"] for f in flag_list}


def compute(metric_overrides=None, thresholds=None, details=None, services=SERVICES):
    return flags_mod.compute_flags(base_metrics(**(metric_overrides or {})),
                                   thresholds, details or {}, services)


def test_steady_account_has_no_flags():
    assert compute() == []


def test_integration_suspect_suppresses_leads_zero():
    result = codes(compute({"leads_new_7d": 0, "convos_active_7d": 0, "opps_created_7d": 0,
                            "leads_delta_pct": None}))
    assert result.get("INTEGRATION_SUSPECT") == "red"
    assert "LEADS_ZERO" not in result


def test_leads_zero_red_when_other_activity_exists():
    result = codes(compute({"leads_new_7d": 0, "leads_delta_pct": None}))
    assert result.get("LEADS_ZERO") == "red"
    assert "INTEGRATION_SUSPECT" not in result


def test_leads_zero_needs_baseline():
    result = codes(compute({"leads_new_7d": 0, "leads_trailing_avg": None,
                            "leads_delta_pct": None, "trailing_n": 1}))
    assert "LEADS_ZERO" not in result and "INTEGRATION_SUSPECT" not in result


def test_leads_drop_red_when_peers_held():
    result = codes(compute({"leads_delta_pct": -55.0, "peer_median_delta_pct": -5.0}))
    assert result.get("LEADS_DROP") == "red"


def test_leads_drop_amber_when_peer_unknown():
    result = codes(compute({"leads_delta_pct": -55.0, "peer_median_delta_pct": None}))
    assert result.get("LEADS_DROP") == "amber"


def test_leads_drop_seasonal_when_peers_also_down():
    result = codes(compute({"leads_delta_pct": -55.0, "peer_median_delta_pct": -35.0}))
    assert result.get("LEADS_DROP_SEASONAL") == "amber"
    assert "LEADS_DROP" not in result


def test_slow_response_amber_then_red():
    assert codes(compute({"leads_uncontacted_24h": 3})).get("SLOW_RESPONSE") == "amber"
    assert codes(compute({"leads_uncontacted_24h": 8})).get("SLOW_RESPONSE") == "red"
    # ratio path: 3 of 6 leads never got a human touch
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
    # below both bars: 2 stale of 10 open, low value
    assert "STALE_PIPELINE" not in codes(compute({"opps_stale": 2, "opps_stale_value": 1000.0}))


def test_no_delivery_gated_on_services():
    assert codes(compute({"days_since_last_publish": 20})).get("NO_DELIVERY") == "amber"
    assert codes(compute({"days_since_last_publish": 35})).get("NO_DELIVERY") == "red"
    assert "NO_DELIVERY" not in codes(compute({"days_since_last_publish": 35},
                                              services=["ads", "seo"]))


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


def test_review_ask_gap_info():
    assert codes(compute({"review_ask_gap": 2})).get("REVIEW_ASK_GAP") == "info"
    assert "REVIEW_ASK_GAP" not in codes(compute({"review_ask_gap": 1}))


def test_thresholds_override_per_account():
    # raise the uncontacted bar to 10: the default amber case stops firing
    assert "SLOW_RESPONSE" not in codes(compute({"leads_uncontacted_24h": 3},
                                                thresholds={"slow_response_min": 10}))
    # unknown keys and non-numeric values are ignored
    assert codes(compute({"leads_uncontacted_24h": 3},
                         thresholds={"slow_response_min": "high", "bogus": 1})
                 ).get("SLOW_RESPONSE") == "amber"
