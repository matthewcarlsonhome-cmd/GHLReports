"""End-to-end mock run per spec 7.6 (v3): the whole collector pipeline against
fixture responses, no network, no database."""

from datetime import date, datetime, timezone

from .. import main as main_mod
from .fakes import CLIENT_SUB, PARENT_SUB, FakeStore, make_factory

NOW = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)  # Tue 10:00 CT
ARGV = ["--date", "2026-08-18"]
RUN_DATE = date(2026, 8, 18)


def run_with(store, factory, argv=ARGV):
    return main_mod.run(argv, store=store, client_factory=factory, now_utc=NOW)


def happy_store(**kwargs):
    return FakeStore(subs=[PARENT_SUB, CLIENT_SUB], **kwargs)


def test_happy_path_metrics_gate_and_flags():
    store = happy_store()
    exit_code = run_with(store, make_factory())
    assert exit_code == 0

    snap = store.snapshots[("locA", "2026-08-18")]
    assert snap["gate_passed"] is True

    # leads: 5 window contacts fetched, "Test Person" excluded, 4 in the 7d window
    assert snap["excluded_count"] == 1
    assert snap["leads_new_7d"] == 4

    # baseline computed live from CRM history: weeks 10/8/9/13 -> 10.0
    assert snap["trailing_n"] == 4
    assert snap["leads_trailing_avg"] == 10.0
    assert snap["leads_delta_pct"] == -60.0
    assert snap["leads_by_source_7d"] == {"facebook": 1, "web": 3}
    assert snap["leads_by_source_trailing"] == {"facebook": 4.0, "web": 6.0}
    assert snap["leads_unassigned_7d"] == 0
    assert snap["leads_missing_phone_pct_7d"] is None  # n < 5

    # forms: one submission per week; Aug 13 lands in the window
    assert snap["form_submissions_7d"] == 1
    assert snap["form_submissions_trailing_avg"] == 1.0

    # speed to lead: human vs automation classification
    assert snap["speed_kind_known"] is True
    assert snap["leads_uncontacted_24h"] == 1        # Jane Smith, no outbound
    assert snap["leads_no_human_touch_7d"] == 1      # Maria Testani, workflow only
    assert snap["speed_to_lead_median_min"] == 30.0
    human_event = store.lead_events[("locA", "c2")]
    assert human_event["first_outbound_kind"] == "human"
    assert human_event["first_human_touch_minutes"] == 30.0
    auto_event = store.lead_events[("locA", "c4")]
    assert auto_event["first_outbound_kind"] == "automation"
    assert auto_event["first_human_touch_at"] is None

    # conversations: weekend rule makes Friday-evening inbound 25h, Tuesday one 10h
    assert snap["convos_waiting"] == 2
    assert snap["convos_waiting_max_hours"] == 25.0
    assert snap["convos_active_7d"] == 2

    # pipeline: the stale/stuck flags land on the right opp
    assert snap["opps_open"] == 2
    assert snap["opps_stale"] == 1
    assert snap["opps_stale_value"] == 30000.0
    assert snap["opps_stuck"] == 1
    assert snap["opps_no_next_step"] == 1
    assert snap["opps_won_7d"] == 1
    assert snap["opps_lost_7d"] == 1
    assert snap["opps_created_7d"] == 1
    assert snap["win_rate_90d"] is None              # won+lost = 2 < 5
    assert snap["median_days_to_close_90d"] == 14.0  # o3: Aug 1 -> Aug 15
    assert snap["lead_to_opp_28d_pct"] == 0.0        # 27-contact cohort, no matching opps
    stale_rows = snap["details"]["stale_opps"]
    assert stale_rows[0]["opp_id"] == "o1"
    assert stale_rows[0]["owner"] == "Lisa Ames"
    assert stale_rows[0]["idle_source_field"] == "lastActionDate"
    assert snap["details"]["lost_reasons_90d"] == {"price": 1}

    # appointments
    assert snap["appts_booked_7d"] == 1              # ev1 created Aug 15
    assert snap["appts_showed_28d"] == 1
    assert snap["appts_noshow_28d"] == 1
    assert snap["noshow_rate_28d"] is None           # denominator 2 < 5

    # delivery and social accounts
    assert snap["blogs_published_30d"] == 1
    assert snap["social_published_7d"] == 1
    assert snap["days_since_last_publish"] == 2
    assert snap["social_accounts_total"] == 2
    assert snap["social_accounts_expired"] == 0

    # relationship
    assert snap["invoices_past_due"] == 1
    assert snap["invoices_past_due_amount"] == 3000.0
    assert snap["client_last_touch_days"] == 8
    assert snap["client_next_appt_at"] is not None
    assert snap["review_asks_stale"] == 1
    assert snap["review_ask_gap"] == 1

    # 28d funnel
    funnel = snap["details"]["funnel_28d"]
    assert funnel["leads"] == 34            # weeks 8+9+13 plus the 4 window leads
    assert funnel["form_submissions"] == 4
    assert funnel["opps_created"] == 3
    assert funnel["appts_booked"] == 3      # ev1, ev2, ev3 all created inside 28d
    assert funnel["won"] == 1

    # lead_history: current ISO week (Aug 17) and previous (Aug 10)
    assert store.lead_history[("locA", "2026-08-17")]["leads"] == 1     # Al Recent
    assert store.lead_history[("locA", "2026-08-10")]["leads"] == 3     # Jane, Bob, Maria

    # flags: exactly these, with these severities
    flag_map = {f["code"]: f["severity"] for f in store.flags[("locA", "2026-08-18")]}
    assert flag_map == {
        "LEADS_DROP": "amber",          # peer unknown (only one client account)
        "SOURCE_DROP": "amber",         # facebook 4/wk -> 1
        "CONVOS_WAITING": "red",        # 25h >= 24h
        "STALE_PIPELINE": "red",        # $30k stale >= $25k
        "PAST_DUE": "red",              # $3,000 >= $2,500
    }
    source_flag = next(f for f in store.flags[("locA", "2026-08-18")] if f["code"] == "SOURCE_DROP")
    assert "facebook" in source_flag["action"]
    stale_flag = next(f for f in store.flags[("locA", "2026-08-18")] if f["code"] == "STALE_PIPELINE")
    assert stale_flag["entity_id"] == "o1"

    # change tracking with no prior week: everything is new
    assert sorted(snap["flags_new"]) == sorted(flag_map.keys())
    assert snap["flags_resolved"] == []
    assert snap["details"]["changed"]["new"] == snap["flags_new"]

    # parent snapshot exists, passes its gate, and raised no flags
    parent_snap = store.snapshots[("locP", "2026-08-18")]
    assert parent_snap["gate_passed"] is True
    assert store.flags[("locP", "2026-08-18")] == []

    run = store.runs[-1]
    assert run["status"] == "ok"
    assert run["details"]["locA"]["status"] == "ok"
    assert run["details"]["locA"]["requests"] > 0


def test_flags_new_and_resolved_against_seeded_prior_week():
    store = happy_store(prior_flags={
        ("locA", "2026-08-11"): ["CONVOS_WAITING", "NO_DELIVERY"],
    })
    assert run_with(store, make_factory()) == 0
    snap = store.snapshots[("locA", "2026-08-18")]
    assert snap["flags_new"] == ["LEADS_DROP", "PAST_DUE", "SOURCE_DROP", "STALE_PIPELINE"]
    assert snap["flags_resolved"] == ["NO_DELIVERY"]


def test_form_silent_fires_when_forms_die_but_leads_flow():
    # forms had a real weekly average, now zero, while leads still arrive
    silent_forms = {"locA": {"submissions": [
        {"id": f"f{i}", "formId": "form1", "contactId": None,
         "createdAt": stamp}
        for i, stamp in enumerate([
            "2026-07-15T15:00:00Z", "2026-07-16T15:00:00Z", "2026-07-17T15:00:00Z",
            "2026-07-22T15:00:00Z", "2026-07-23T15:00:00Z", "2026-07-24T15:00:00Z",
            "2026-07-29T15:00:00Z", "2026-07-30T15:00:00Z", "2026-07-31T15:00:00Z",
            "2026-08-05T15:00:00Z", "2026-08-06T15:00:00Z", "2026-08-07T15:00:00Z",
        ], start=1)]}}
    store = happy_store()
    assert run_with(store, make_factory(forms_override=silent_forms)) == 0
    snap = store.snapshots[("locA", "2026-08-18")]
    assert snap["form_submissions_7d"] == 0
    assert snap["form_submissions_trailing_avg"] == 3.0
    flag_map = {f["code"]: f["severity"] for f in store.flags[("locA", "2026-08-18")]}
    assert flag_map.get("FORM_SILENT") == "red"
    assert "INTEGRATION_SUSPECT" not in flag_map   # leads are flowing


def test_gate_fails_when_two_sources_403():
    store = happy_store()
    factory = make_factory(deny_by_loc={
        "locA": frozenset({"/contacts/search", "/opportunities/search"})})
    exit_code = run_with(store, factory)
    assert exit_code == 2

    snap = store.snapshots[("locA", "2026-08-18")]
    assert snap["gate_passed"] is False
    statuses = {name: entry["status"]
                for name, entry in snap["coverage"]["sources"].items()}
    unavailable = [name for name, status in statuses.items() if status == "unavailable"]
    assert len(unavailable) >= 2
    assert store.runs[-1]["held"] == 1


def test_gate_fails_on_wrong_location_name():
    wrong = {**CLIENT_SUB, "name": "Wrong Name Pools"}
    store = FakeStore(subs=[PARENT_SUB, wrong])
    exit_code = run_with(store, make_factory())
    assert exit_code == 2
    snap = store.snapshots[("locA", "2026-08-18")]
    assert snap["gate_passed"] is False


def test_g4_dead_account_exemption():
    # sudden all-zero after healthy history -> held
    store = FakeStore(subs=[PARENT_SUB, CLIENT_SUB],
                      prev_dead={"locA": [(5, 2, 1), (4, 1, 0), (6, 0, 2)]})
    exit_code = run_with(store, make_factory(empty_locs={"locA"}))
    assert exit_code == 2
    assert store.snapshots[("locA", "2026-08-18")]["gate_passed"] is False

    # three gate-passed all-zero snapshots -> dormancy proven, gate passes
    store = FakeStore(subs=[PARENT_SUB, CLIENT_SUB],
                      prev_dead={"locA": [(0, 0, 0)] * 3})
    exit_code = run_with(store, make_factory(empty_locs={"locA"}))
    assert exit_code == 0
    assert store.snapshots[("locA", "2026-08-18")]["gate_passed"] is True


def test_missing_token_marks_location_failed():
    store = FakeStore(subs=[PARENT_SUB, CLIENT_SUB],
                      pits={"locP": "tok-locP"})  # no PIT for locA
    exit_code = run_with(store, make_factory())
    assert exit_code == 2
    assert store.token_status["locA"] == ("none", None)
    assert ("locA", "2026-08-18") not in store.snapshots
    assert store.runs[-1]["failed"] == 1


def test_backfill_writes_lead_history_weeks():
    store = happy_store()
    exit_code = run_with(store, make_factory(), argv=["--backfill", "6", "--date", "2026-08-18"])
    assert exit_code == 0
    weeks = sorted(week for loc, week in store.lead_history if loc == "locA")
    assert weeks == ["2026-07-13", "2026-07-20", "2026-07-27",
                     "2026-08-03", "2026-08-10", "2026-08-17"]
    assert store.lead_history[("locA", "2026-07-13")]["leads"] == 10
    assert store.lead_history[("locA", "2026-07-20")]["leads"] == 8
    assert store.lead_history[("locA", "2026-07-27")]["leads"] == 9
    assert store.lead_history[("locA", "2026-08-03")]["leads"] == 13
    assert store.lead_history[("locA", "2026-08-10")]["leads"] == 3
    assert store.lead_history[("locA", "2026-08-17")]["leads"] == 1
    by_source = store.lead_history[("locA", "2026-07-13")]["leads_by_source"]
    assert by_source == {"facebook": 4, "web": 6}


def make_peer_book(deltas_by_loc: dict[str, tuple[str, float | None]]):
    """Build a FakeStore pre-loaded with today's snapshots and matching subs."""
    store = FakeStore(subs=[])
    subs_by_id = {}
    for loc, (vertical, delta) in deltas_by_loc.items():
        store.snapshots[(loc, RUN_DATE.isoformat())] = {
            "location_id": loc, "snapshot_date": RUN_DATE.isoformat(),
            "gate_passed": True, "leads_delta_pct": delta,
        }
        subs_by_id[loc] = {"location_id": loc, "vertical": vertical, "is_parent": False}
    return store, subs_by_id


def test_peer_median_with_enough_peers():
    store, subs = make_peer_book({
        "l1": ("pool_builder", -50.0), "l2": ("pool_builder", -30.0),
        "l3": ("pool_builder", -10.0), "l4": ("pool_builder", 5.0),
        "l5": ("pool_builder", 20.0),
    })
    peers = main_mod.peer_pass(store, subs, RUN_DATE)
    assert peers["l1"] == (-10.0, 5)
    assert store.snapshots[("l3", RUN_DATE.isoformat())]["peer_median_delta_pct"] == -10.0


def test_peer_median_small_vertical_falls_back_to_book():
    store, subs = make_peer_book({
        "l1": ("pool_builder", -50.0), "l2": ("pool_builder", -30.0),
        "l3": ("pool_builder", -10.0), "l4": ("pool_builder", 5.0),
        "l5": ("pool_builder", 20.0),
        "h1": ("hot_tub", 40.0), "h2": ("hot_tub", -80.0),
    })
    peers = main_mod.peer_pass(store, subs, RUN_DATE)
    # whole-book median across all seven deltas
    assert peers["h1"] == (-10.0, 7)


def test_peer_median_null_when_book_too_small():
    store, subs = make_peer_book({"l1": ("pool_builder", -50.0),
                                  "l2": ("hot_tub", 10.0)})
    peers = main_mod.peer_pass(store, subs, RUN_DATE)
    value, _n = peers["l1"]
    assert value is None
