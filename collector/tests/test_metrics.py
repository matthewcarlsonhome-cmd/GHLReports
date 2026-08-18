from datetime import datetime, timezone

from zoneinfo import ZoneInfo

from .. import metrics

CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)  # Tue 10:00 CT


def utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def ct(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=CT)


# -- parse_ts -----------------------------------------------------------------

def test_parse_ts_variants():
    assert metrics.parse_ts("2026-08-16T10:30:00Z") == utc(2026, 8, 16, 10, 30)
    assert metrics.parse_ts("2026-08-16T10:30:00+00:00") == utc(2026, 8, 16, 10, 30)
    assert metrics.parse_ts(1786876200000) == utc(2026, 8, 16, 10, 30)  # epoch ms
    assert metrics.parse_ts(1786876200) == utc(2026, 8, 16, 10, 30)     # epoch s
    assert metrics.parse_ts(None) is None
    assert metrics.parse_ts("not a date") is None


# -- windows ------------------------------------------------------------------

def test_window_7d_is_seven_full_local_days_ending_yesterday():
    start, end = metrics.window_7d(NOW, CT)
    assert start == ct(2026, 8, 11, 0, 0)
    assert end.astimezone(CT).strftime("%Y-%m-%d %H:%M:%S") == "2026-08-17 23:59:59"
    assert metrics.in_window(ct(2026, 8, 11, 0, 0), start, end)
    assert metrics.in_window(ct(2026, 8, 17, 23, 59), start, end)
    assert not metrics.in_window(ct(2026, 8, 18, 0, 0), start, end)
    assert not metrics.in_window(ct(2026, 8, 10, 23, 59), start, end)


# -- weekend wait rule --------------------------------------------------------

def test_weekend_rule_friday_evening():
    # Fri 18:30 CT inbound, observed Mon 12:00 CT -> exactly 3.0h (spec 7.6)
    inbound = ct(2026, 8, 14, 18, 30)
    observed = ct(2026, 8, 17, 12, 0)
    assert metrics.waiting_hours(inbound.astimezone(timezone.utc),
                                 observed.astimezone(timezone.utc), CT) == 3.0


def test_weekend_rule_saturday_and_sunday_start_monday_nine():
    observed = ct(2026, 8, 17, 12, 0)  # Monday noon
    for day in (15, 16):  # Sat, Sun
        inbound = ct(2026, 8, day, 11, 0)
        assert metrics.waiting_hours(inbound.astimezone(timezone.utc),
                                     observed.astimezone(timezone.utc), CT) == 3.0


def test_weekday_inbound_counts_wall_clock():
    inbound = ct(2026, 8, 18, 4, 0)
    observed = ct(2026, 8, 18, 10, 0)
    assert metrics.waiting_hours(inbound.astimezone(timezone.utc),
                                 observed.astimezone(timezone.utc), CT) == 6.0


def test_friday_before_five_pm_is_not_weekendish():
    inbound = ct(2026, 8, 14, 16, 0)
    observed = ct(2026, 8, 14, 22, 0)
    assert metrics.waiting_hours(inbound.astimezone(timezone.utc),
                                 observed.astimezone(timezone.utc), CT) == 6.0


# -- exclusions ---------------------------------------------------------------

def test_whole_word_exclusion():
    assert metrics.is_excluded({"firstName": "Test", "lastName": "Person"})
    assert metrics.is_excluded({"firstName": "Sally", "lastName": "Demo"})
    assert metrics.is_excluded({"firstName": "A", "lastName": "b", "tags": ["Internal-Staff"]})
    assert not metrics.is_excluded({"firstName": "Maria", "lastName": "Testani"})
    assert not metrics.is_excluded({"firstName": "Sam", "lastName": "Ortiz"})


def test_filter_exclusions_counts():
    contacts = [
        {"firstName": "Test", "lastName": "User"},
        {"firstName": "Real", "lastName": "Person"},
    ]
    kept, excluded = metrics.filter_exclusions(contacts)
    assert len(kept) == 1 and excluded == 1


# -- speed to lead classification ---------------------------------------------

def test_classify_outbound():
    assert metrics.classify_outbound({"userId": "u1"}) == "human"
    assert metrics.classify_outbound({"source": "app"}) == "human"
    assert metrics.classify_outbound({"source": "manual"}) == "human"
    assert metrics.classify_outbound({"source": "workflow"}) == "automation"
    assert metrics.classify_outbound({"source": "campaign"}) == "automation"
    assert metrics.classify_outbound({"source": "mystery"}) == "unknown"
    assert metrics.classify_outbound({}) == "unknown"


def test_lead_event_first_touch_minutes():
    contact = {"id": "c1", "firstName": "Jane", "lastName": "Smith",
               "dateAdded": "2026-08-16T10:00:00Z"}
    messages = [
        {"direction": "outbound", "dateAdded": "2026-08-16T11:00:00Z", "source": "workflow"},
        {"direction": "outbound", "dateAdded": "2026-08-16T10:30:00Z", "userId": "u1"},
        {"direction": "inbound", "dateAdded": "2026-08-16T10:05:00Z"},
    ]
    event = metrics.lead_event(contact, messages)
    assert event["first_touch_minutes"] == 30.0
    assert event["first_outbound_kind"] == "human"
    assert event["first_human_touch_minutes"] == 30.0


def test_speed_metrics_unknown_kinds_fall_back_to_any_touch():
    start, end = metrics.window_7d(NOW, CT)
    events = [{
        "created_at": utc(2026, 8, 15, 10),
        "first_outbound_at": utc(2026, 8, 15, 12),
        "first_outbound_kind": "unknown",
        "first_human_touch_at": None,
        "first_touch_minutes": 120.0,
        "first_human_touch_minutes": None,
    }]
    result = metrics.speed_to_lead_metrics(events, NOW, start, end)
    assert result["speed_kind_known"] is False
    assert result["leads_no_human_touch_7d"] is None
    assert result["speed_to_lead_median_min"] == 120.0


# -- stats --------------------------------------------------------------------

def test_median_and_percentile():
    assert metrics.median([]) is None
    assert metrics.median([3.0]) == 3.0
    assert metrics.median([1.0, 3.0]) == 2.0
    assert metrics.percentile([], 90) is None
    assert metrics.percentile([10.0], 90) == 10.0
    assert metrics.percentile(list(map(float, range(1, 11))), 90) == 9.0


def test_baseline_stats_live_from_history():
    win_start, _ = metrics.window_7d(NOW, CT)
    # 2/3/4/5 leads across the four baseline weeks -> avg 3.5
    contacts = []
    for week_index, count in enumerate((2, 3, 4, 5)):
        week_start = win_start - metrics.timedelta(days=7 * (4 - week_index))
        for i in range(count):
            contacts.append({"id": f"x{week_index}-{i}", "source": "web",
                             "dateAdded": (week_start + metrics.timedelta(hours=i)).isoformat()})
    stats = metrics.baseline_stats(contacts, win_start, earliest_added=None)
    assert stats["trailing_n"] == 4
    assert stats["leads_trailing_avg"] == 3.5
    assert stats["leads_by_source_trailing"] == {"web": 3.5}

    # account born mid-baseline: only the weeks it existed count
    born = win_start - metrics.timedelta(days=10)
    stats = metrics.baseline_stats(contacts, win_start, earliest_added=born)
    assert stats["trailing_n"] == 2

    # a single countable week is not enough for a baseline
    born_late = win_start - metrics.timedelta(days=3)
    stats = metrics.baseline_stats(contacts, win_start, earliest_added=born_late)
    assert stats["trailing_n"] == 1
    assert stats["leads_trailing_avg"] is None


def test_delta():
    assert metrics.leads_delta_pct(4, 10.0) == -60.0
    assert metrics.leads_delta_pct(4, None) is None
    assert metrics.leads_delta_pct(4, 2.0) is None  # trailing < 3 -> null


def test_peer_median_requires_four():
    assert metrics.peer_median([-50.0, -30.0, -10.0, 5.0, 20.0]) == (-10.0, 5)
    value, n = metrics.peer_median([-50.0, 10.0])
    assert value is None and n == 2


# -- pipeline -----------------------------------------------------------------

def test_pipeline_idle_source_field_and_stale_stuck():
    start, end = metrics.window_7d(NOW, CT)
    opps = [{
        "id": "o1", "status": "open", "monetaryValue": 30000,
        "updatedAt": "2026-07-10T12:00:00Z",
        "lastActionDate": "2026-07-20T12:00:00Z",
        "lastStatusChangeAt": "2026-07-01T12:00:00Z",
        "lastStageChangeAt": "2026-07-01T12:00:00Z",
        "tasks": [], "calendarEvents": [], "_has_task_keys": True,
    }]
    pipe = metrics.pipeline_metrics(opps, NOW, start, end)
    assert pipe["opps_stale"] == 1
    assert pipe["opps_stuck"] == 1
    item = pipe["per_opp"][0]
    assert item["idle_field"] == "lastActionDate"  # the max of the three
    assert pipe["opps_no_next_step"] == 1


def test_pipeline_next_step_unavailable_when_keys_missing():
    start, end = metrics.window_7d(NOW, CT)
    opps = [{"id": "o1", "status": "open", "monetaryValue": None,
             "updatedAt": "2026-08-17T12:00:00Z", "_has_task_keys": False}]
    pipe = metrics.pipeline_metrics(opps, NOW, start, end)
    assert pipe["next_step_available"] is False
    assert pipe["opps_no_next_step"] is None
    assert pipe["opps_missing_value"] == 1


def test_pipeline_stale_threshold_is_overridable():
    start, end = metrics.window_7d(NOW, CT)
    opps = [{"id": "o1", "status": "open", "monetaryValue": 100,
             "lastActionDate": "2026-08-01T12:00:00Z", "_has_task_keys": True,
             "tasks": [], "calendarEvents": []}]  # ~17 days idle
    default = metrics.pipeline_metrics(opps, NOW, start, end)
    relaxed = metrics.pipeline_metrics(opps, NOW, start, end, stale_days=21)
    assert default["opps_stale"] == 1
    assert relaxed["opps_stale"] == 0


def test_lead_to_opp_cohort():
    cohort_added = (NOW - metrics.timedelta(days=20)).isoformat()
    contacts = [{"id": f"c{i}", "dateAdded": cohort_added} for i in range(6)]
    opps = [{"id": "o1", "status": "open", "createdAt": (NOW - metrics.timedelta(days=10)).isoformat(),
             "contact": {"id": "c0"}, "_has_task_keys": False}]
    assert metrics.lead_to_opp_pct(contacts, opps, NOW) == round(1 / 6 * 100, 1)
    # cohort below 5 -> null
    assert metrics.lead_to_opp_pct(contacts[:3], opps, NOW) is None


def test_appointment_metrics_statuses_and_booked_window():
    start, end = metrics.window_7d(NOW, CT)
    events = [
        {"id": "e1", "startTime": (NOW - metrics.timedelta(days=3)).isoformat(),
         "appointmentStatus": "showed", "dateAdded": "2026-08-15T12:00:00Z"},
        {"id": "e2", "startTime": (NOW - metrics.timedelta(days=5)).isoformat(),
         "appointmentStatus": "noshow", "dateAdded": "2026-08-01T12:00:00Z"},
        {"id": "e3", "startTime": (NOW + metrics.timedelta(days=2)).isoformat(),
         "appointmentStatus": "confirmed", "dateAdded": "2026-08-16T12:00:00Z"},
    ]
    stats = metrics.appointment_metrics(events, NOW, start, end)
    assert stats["appts_booked_7d"] == 2   # e1 + e3 created in the window
    assert stats["appts_showed_28d"] == 1
    assert stats["appts_noshow_28d"] == 1
    assert stats["noshow_rate_28d"] is None  # denominator < 5


def test_flags_changed():
    new, resolved = metrics.flags_changed(["A", "B"], ["B", "C"])
    assert new == ["A"] and resolved == ["C"]


def test_missed_calls_classification_and_window():
    start, end = metrics.window_7d(NOW, CT)
    convo = {"id": "cv1", "contactId": "c1", "contactName": "Call Karl",
             "lastMessageType": "TYPE_CALL"}
    assert metrics.is_call_conversation(convo)
    assert not metrics.is_call_conversation({"lastMessageType": "TYPE_SMS"})
    messages = [
        # missed, in window
        {"type": "TYPE_CALL", "direction": "inbound",
         "dateAdded": "2026-08-16T17:00:00Z", "call_status": "no-answer"},
        # answered call: not missed
        {"type": "TYPE_CALL", "direction": "inbound",
         "dateAdded": "2026-08-16T18:00:00Z", "call_status": "completed"},
        # outbound call: never "missed by us"
        {"type": "TYPE_CALL", "direction": "outbound",
         "dateAdded": "2026-08-16T19:00:00Z", "call_status": "no-answer"},
        # missed but before the window
        {"type": "TYPE_CALL", "direction": "inbound",
         "dateAdded": "2026-07-01T17:00:00Z", "call_status": "missed"},
        # not a call
        {"type": "TYPE_SMS", "direction": "inbound",
         "dateAdded": "2026-08-16T17:30:00Z", "call_status": None},
    ]
    missed = metrics.missed_calls_in_window(convo, messages, start, end)
    assert len(missed) == 1
    assert missed[0]["conversation_id"] == "cv1"
    assert missed[0]["status"] == "no-answer"


# -- invoices -----------------------------------------------------------------

def test_past_due_invoices_status_and_amount_fallback():
    today = NOW.astimezone(CT).date()
    invoices = [
        {"_id": "i1", "status": "sent", "dueDate": "2026-07-30T00:00:00Z", "amountDue": 3000, "total": 3000},
        {"_id": "i2", "status": "paid", "dueDate": "2026-07-01T00:00:00Z", "amountDue": 0, "total": 100},
        {"_id": "i3", "status": "overdue", "dueDate": "2026-08-01T00:00:00Z", "total": 500},
        {"_id": "i4", "status": "sent", "dueDate": "2026-09-01T00:00:00Z", "amountDue": 250},
    ]
    overdue = metrics.past_due_invoices(invoices, today)
    assert {inv["invoice_id"] for inv in overdue} == {"i1", "i3"}
    i3 = next(inv for inv in overdue if inv["invoice_id"] == "i3")
    assert i3["amount_due"] == 500.0  # total fallback when amountDue absent


# -- gate ---------------------------------------------------------------------

class CovStub:
    def __init__(self, unavailable=0, partial=0):
        self._u, self._p = unavailable, partial

    def unavailable_count(self):
        return self._u

    def partial_count(self):
        return self._p


def test_gate_pass_and_each_failure():
    ok, reasons = metrics.gate_check(True, CovStub(), 5, 3, 1, [])
    assert ok and reasons == []

    ok, reasons = metrics.gate_check(False, CovStub(), 5, 3, 1, [])
    assert not ok and any("G1" in r for r in reasons)

    ok, reasons = metrics.gate_check(True, CovStub(unavailable=2), 5, 3, 1, [])
    assert not ok and any("G2" in r for r in reasons)

    ok, reasons = metrics.gate_check(True, CovStub(partial=2), 5, 3, 1, [])
    assert not ok and any("G3" in r for r in reasons)


def test_gate_g4_sudden_zero_vs_dormant():
    ok, reasons = metrics.gate_check(True, CovStub(), 0, 0, 0, [(5, 2, 1), (4, 1, 0), (6, 0, 2)])
    assert not ok and any("G4" in r for r in reasons)

    ok, reasons = metrics.gate_check(True, CovStub(), 0, 0, 0, [(0, 0, 0)] * 3)
    assert ok  # a dormant account is allowed to be dormant

    ok, reasons = metrics.gate_check(True, CovStub(), 0, 0, 0, [(0, 0, 0)] * 2)
    assert not ok  # not enough history to prove dormancy


def test_location_name_matches():
    assert metrics.location_name_matches("Pilot One Pools — GHL", "Pilot One Pools")
    assert metrics.location_name_matches("pilot one pools", "Pilot One Pools")
    assert not metrics.location_name_matches("Different Company", "Pilot One Pools")
    assert not metrics.location_name_matches(None, "Pilot One Pools")
