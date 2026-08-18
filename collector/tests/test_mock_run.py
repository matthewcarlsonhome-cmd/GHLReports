"""End-to-end mock run per spec 7.6: the whole collector pipeline against
fixture responses, no network, no database."""

from datetime import date, datetime, timezone

from .. import main as main_mod
from .. import metrics
from .fakes import CLIENT_SUB, PARENT_SUB, FakeStore, make_factory

NOW = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)  # Tue 10:00 CT
ARGV = ["--date", "2026-08-18"]
RUN_DATE = date(2026, 8, 18)


def run_with(store, factory):
    return main_mod.run(ARGV, store=store, client_factory=factory, now_utc=NOW)


def happy_store():
    return FakeStore(
        subs=[PARENT_SUB, CLIENT_SUB],
        trailing={"locA": [10, 8, 9, 13]},
    )


def test_happy_path_metrics_gate_and_flags():
    store = happy_store()
    exit_code = run_with(store, make_factory())
    assert exit_code == 0

    snap = store.snapshots[("locA", "2026-08-18")]
    assert snap["gate_passed"] is True

    # leads: 5 fetched, "Test Person" excluded, 4 in the 7d window
    assert snap["excluded_count"] == 1
    assert snap["leads_new_7d"] == 4
    assert snap["leads_trailing_avg"] == 10.0
    assert snap["trailing_n"] == 4
    assert snap["leads_delta_pct"] == -60.0

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
    stale_rows = snap["details"]["stale_opps"]
    assert stale_rows[0]["opp_id"] == "o1"
    assert stale_rows[0]["owner"] == "Lisa Ames"
    assert stale_rows[0]["idle_source_field"] == "lastActionDate"
    assert snap["details"]["lost_reasons_90d"] == {"price": 1}

    # delivery and relationship
    assert snap["blogs_published_30d"] == 1
    assert snap["social_published_7d"] == 1
    assert snap["days_since_last_publish"] == 2
    assert snap["invoices_past_due"] == 1
    assert snap["invoices_past_due_amount"] == 3000.0
    assert snap["client_last_touch_days"] == 8
    assert snap["client_next_appt_at"] is not None
    assert snap["review_asks_stale"] == 1
    assert snap["review_ask_gap"] == 1

    # flags: exactly these, with these severities
    flag_map = {f["code"]: f["severity"] for f in store.flags[("locA", "2026-08-18")]}
    assert flag_map == {
        "LEADS_DROP": "amber",          # peer unknown (only one client account)
        "CONVOS_WAITING": "red",        # 25h >= 24h
        "STALE_PIPELINE": "red",        # $30k stale >= $25k
        "PAST_DUE": "red",              # $3,000 >= $2,500
    }
    stale_flag = next(f for f in store.flags[("locA", "2026-08-18")]
                      if f["code"] == "STALE_PIPELINE")
    assert stale_flag["entity_id"] == "o1"

    # parent snapshot exists, passes its gate, and raised no flags
    parent_snap = store.snapshots[("locP", "2026-08-18")]
    assert parent_snap["gate_passed"] is True
    assert store.flags[("locP", "2026-08-18")] == []
    assert store.runs[-1]["status"] == "ok"


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
    store = FakeStore(subs=[PARENT_SUB, wrong], trailing={"locA": [10, 8, 9, 13]})
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
