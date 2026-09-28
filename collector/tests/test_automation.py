"""Tests for the AM notes (collector/automation.py).

The rules worth protecting are the ones that keep an AM from getting the
same email every day, and the ones that keep a note from ever reaching the
wrong person: told once after two nights, quiet while unchanged, worse and
reminders on a schedule, cleared lines that ride along, one note per client
per day, the caps, the recipient checks, and a flat PII-free payload.

Most tests drive evaluate() night by night through the Sim helper, which
commits delivered notes exactly like the live run does.
"""

from datetime import date, timedelta

from .. import automation as A
from .. import main as main_mod
from .fakes import CLIENT_SUB, PARENT_SUB, FakeStore, make_factory

LAUREN = "ldegner@smallscreenproducer.com"
MATTHEW = "mcarlson@smallscreenproducer.com"
D0 = date(2026, 9, 1)                     # a Tuesday

SUB = {"location_id": "locA", "slug": "flohr", "name": "Flohr Pools", "am_email": LAUREN,
       "am_name": "Lauren", "mlh_status": "active", "alert_triggers": ["T2", "T3", "T4"],
       "thresholds": {"slow_response_min": 2}, "alert_tracking_since": "2026-08-01"}
ENV = {"AUTOMATION_WEBHOOKS": "on", "AUTOMATION_WEBHOOK_URL": "https://hooks.example.invalid/x",
       "AM_NOTIFY_ALLOWLIST": f"{LAUREN},{MATTHEW}"}


def day(n, sub=SUB, flags=(), form_rows=(), form_checks=(), gate=True, **metrics):
    base = {"leads_uncontacted_24h": 0, "convos_waiting": 0, "client_users": 5,
            "leads_new_7d": 20, "form_submissions_7d": 5}
    base.update(metrics)
    return A.AccountDay(sub=sub, metrics=base, flags=list(flags), form_rows=list(form_rows),
                        form_checks=list(form_checks), gate_passed=gate,
                        today=D0 + timedelta(days=n))


class Sim:
    """evaluate() night after night with in-memory state, committing every
    note that is ready to go, like the live run does after a good POST."""

    def __init__(self, env=None, acks=None):
        self.settings = A.Settings.from_env(env or ENV)
        self.rows: dict = {}
        self.acks = acks or {}
        self.last_plan = None

    def night(self, *days):
        today = days[0].today
        plan = A.evaluate(list(days), self.rows, self.acks, set(), self.settings, today)
        self.last_plan = plan
        sent = []
        for notice in plan.notices:
            if notice.status:
                continue
            notice.payload = A.compose(notice, self.settings)
            A.commit(self.rows[notice.location_id], notice.items, notice.cleared, today,
                     notice.audience)
            sent.append(notice)
        return sent

    def row(self, code, entity="", loc="locA"):
        return self.rows.get(loc, {}).get((code, entity))


def chips(notice):
    return [item.chip(notice.today) for item in notice.items]


# -- told once, then quiet ------------------------------------------------------

def test_one_night_blip_never_sends():
    sim = Sim()
    assert sim.night(day(0, leads_uncontacted_24h=3)) == []
    assert sim.night(day(1)) == []
    assert sim.row("SLOW_RESPONSE") is None        # forgotten: nobody was told


def test_second_night_sends_new():
    sim = Sim()
    sim.night(day(0, leads_uncontacted_24h=3))
    [note] = sim.night(day(1, leads_uncontacted_24h=3))
    assert chips(note) == ["NEW"]
    assert note.payload["subject"] == "Flohr Pools: 3 leads going cold"


def test_same_problem_is_not_repeated_daily_and_reminders_are_bounded():
    sim = Sim()
    sent_on = []
    for n in range(0, 30):
        for note in sim.night(day(n, leads_uncontacted_24h=3)):
            sent_on.append((n, chips(note)))
    # T2: told on night 1, reminded every 7 days, at most twice, then quiet.
    assert sent_on == [(1, ["NEW"]), (8, ["STILL OPEN (day 9)"]),
                       (15, ["STILL OPEN (day 16)"])]


def test_one_note_per_account_bundles_everything():
    sim = Sim()
    sim.night(day(0, leads_uncontacted_24h=3, convos_waiting=6))
    [note] = sim.night(day(1, leads_uncontacted_24h=3, convos_waiting=6))
    assert len(note.items) == 2
    assert note.payload["item_count"] == "2"
    assert note.payload["subject"].endswith("(+1 more)")


# -- worse ------------------------------------------------------------------------

def test_doubling_counts_as_worse_once():
    sim = Sim()
    sim.night(day(0, leads_uncontacted_24h=3))
    sim.night(day(1, leads_uncontacted_24h=3))
    [note] = sim.night(day(2, leads_uncontacted_24h=6))
    assert chips(note) == ["WORSE"]
    assert "Up from 3 in our last note." in note.payload["body_text"]   # says what changed
    assert sim.night(day(3, leads_uncontacted_24h=6)) == []
    assert sim.row("SLOW_RESPONSE")["reminders"] == 0      # worse is not a reminder


def test_small_growth_is_not_worse():
    sim = Sim()
    sim.night(day(0, leads_uncontacted_24h=2))
    sim.night(day(1, leads_uncontacted_24h=2))
    assert sim.night(day(2, leads_uncontacted_24h=4)) == []   # doubled, but only +2


def test_turning_red_is_worse():
    sim = Sim()
    sim.night(day(0, convos_waiting=6, convos_waiting_max_hours=10))
    sim.night(day(1, convos_waiting=6, convos_waiting_max_hours=10))
    [note] = sim.night(day(2, convos_waiting=6, convos_waiting_max_hours=30))
    assert chips(note) == ["WORSE"] and note.payload["urgency"] == "high"


# -- cleared ------------------------------------------------------------------------

def test_cleared_line_rides_along_and_never_sends_alone():
    sim = Sim()
    sim.night(day(0, leads_uncontacted_24h=3))
    sim.night(day(1, leads_uncontacted_24h=3))
    assert sim.night(day(2)) == []
    assert sim.night(day(3)) == []                  # resolved tonight: no note by itself
    assert sim.row("SLOW_RESPONSE")["status"] == "resolved"
    sim.night(day(4, convos_waiting=6))
    [note] = sim.night(day(5, convos_waiting=6))
    assert [r["code"] for r in note.cleared] == ["SLOW_RESPONSE"]
    assert "Leads going cold: no lead from this week" in note.payload["body_text"]
    assert note.payload["resolved_text"].startswith("Cleared: ")
    [reminder] = sim.night(day(12, convos_waiting=6))   # T3 reminder a week later
    assert chips(reminder)[0].startswith("STILL OPEN")
    assert reminder.cleared == []                   # the cleared line went out once


def test_flapping_back_within_a_week_does_not_resend_new():
    sim = Sim()
    sim.night(day(0, leads_uncontacted_24h=3))
    sim.night(day(1, leads_uncontacted_24h=3))      # NEW
    sim.night(day(2))
    sim.night(day(3))                               # resolved
    assert sim.night(day(5, leads_uncontacted_24h=3)) == []   # back: same episode
    assert sim.row("SLOW_RESPONSE")["status"] == "open"
    [note] = sim.night(day(8, leads_uncontacted_24h=3))       # reminder clock kept going
    assert chips(note)[0].startswith("STILL OPEN")


def test_back_after_more_than_a_week_is_a_new_episode():
    sim = Sim()
    sim.night(day(0, leads_uncontacted_24h=3))
    sim.night(day(1, leads_uncontacted_24h=3))
    sim.night(day(2))
    sim.night(day(3))                               # resolved on night 3
    sim.night(day(12, leads_uncontacted_24h=3))
    [note] = sim.night(day(13, leads_uncontacted_24h=3))
    assert chips(note) == ["NEW"]


def test_a_quiet_form_that_ages_out_closes_without_a_false_cleared_line():
    form = {"kind": "form", "form_id": "f1", "name": "Contact Us", "status": "silent",
            "channel": "Website form", "subs_30d": 12, "last_submission_at": "2026-08-25",
            "page_url": "https://flohr.example/contact"}
    sim = Sim()
    sim.night(day(0, form_rows=[form]))
    [note] = sim.night(day(1, form_rows=[form]))
    assert note.items[0].issue.code == "FORM_WENT_SILENT"
    dormant = dict(form, status="dormant")
    sim.night(day(2, form_rows=[dormant]))
    sim.night(day(3, form_rows=[dormant]))
    row = sim.row("FORM_WENT_SILENT", "f1")
    assert row["status"] == "resolved" and row["cleared_text"] is None


def test_a_form_that_gets_leads_again_is_reported_cleared():
    form = {"kind": "form", "form_id": "f1", "name": "Contact Us", "status": "silent",
            "channel": "Website form", "subs_30d": 12, "last_submission_at": "2026-08-25"}
    sim = Sim()
    sim.night(day(0, form_rows=[form]))
    sim.night(day(1, form_rows=[form]))
    active = dict(form, status="active", last_submission_at="2026-09-03")
    sim.night(day(2, form_rows=[active]))
    sim.night(day(3, form_rows=[active]))
    assert sim.row("FORM_WENT_SILENT", "f1")["cleared_text"] == '"Contact Us" form: sending leads again.'


# -- acknowledge, gate, audience --------------------------------------------------------

def test_acknowledge_silences_new_and_reminders_but_not_worse():
    sim = Sim(acks={"locA": {"SLOW_RESPONSE"}})
    sim.night(day(0, leads_uncontacted_24h=3))
    assert sim.night(day(1, leads_uncontacted_24h=3)) == []   # acked before we told
    sim.acks = {}
    [note] = sim.night(day(2, leads_uncontacted_24h=3))       # snooze over: told once
    assert chips(note) == ["NEW"]
    sim.acks = {"locA": {"SLOW_RESPONSE"}}
    [worse] = sim.night(day(3, leads_uncontacted_24h=8))
    assert chips(worse) == ["WORSE"]


def test_a_held_night_changes_nothing():
    sim = Sim()
    sim.night(day(0, leads_uncontacted_24h=3))
    assert sim.night(day(1, gate=False, leads_uncontacted_24h=3)) == []
    assert sim.row("SLOW_RESPONSE")["seen_runs"] == 1
    sim.night(day(2))                               # held night did not count as absent
    assert sim.row("SLOW_RESPONSE") is None         # pending, absent once: forgotten


def test_first_tracked_night_is_labelled_already_open():
    fresh = dict(SUB, alert_tracking_since=None)
    sim = Sim()
    sim.night(day(0, sub=fresh, convos_waiting=30))
    assert sim.last_plan.newly_tracked == ["locA"]
    [note] = sim.night(day(1, sub=dict(fresh, alert_tracking_since="2026-09-01"),
                           convos_waiting=30))
    assert chips(note) == ["ALREADY OPEN"]
    assert note.first_note and "first Account Health note" in note.payload["body_text"]


def test_a_new_audience_hears_once_so_dry_runs_do_not_use_up_the_real_note():
    dry = Sim(env=dict(ENV, AUTOMATION_WEBHOOKS="dry"))
    dry.night(day(0, convos_waiting=9))
    [rehearsal] = dry.night(day(1, convos_waiting=9))
    assert rehearsal.audience == f"dry:{LAUREN}"
    live = Sim()
    live.rows = dry.rows                            # same state, switched to live
    [real] = live.night(day(2, convos_waiting=9))
    assert chips(real) == ["NEW"] and real.recipient == LAUREN
    assert live.night(day(3, convos_waiting=9)) == []


def test_a_rerun_of_the_same_night_counts_once():
    sim = Sim()
    sim.night(day(0, leads_uncontacted_24h=3))
    sim.night(day(0, leads_uncontacted_24h=3))
    assert sim.row("SLOW_RESPONSE")["seen_runs"] == 1


# -- who gets notes ---------------------------------------------------------------------

def test_only_the_alert_types_an_account_picked():
    sub = dict(SUB, alert_triggers=["T2"])
    sim = Sim()
    sim.night(day(0, sub=sub, convos_waiting=12))
    assert sim.night(day(1, sub=sub, convos_waiting=12)) == []
    assert sim.row("CONVOS_WAITING")["status"] == "open"      # still tracked


def test_no_notes_for_accounts_nobody_works_in():
    for sub, extra in ((dict(SUB, mlh_status="ads_only"), {}), (SUB, {"client_users": 0})):
        sim = Sim()
        sim.night(day(0, sub=sub, leads_uncontacted_24h=3, **extra))
        assert sim.night(day(1, sub=sub, leads_uncontacted_24h=3, **extra)) == []


def test_empty_allowlist_holds_everything():
    sim = Sim(env=dict(ENV, AM_NOTIFY_ALLOWLIST=""))
    sim.night(day(0, leads_uncontacted_24h=3))
    assert sim.night(day(1, leads_uncontacted_24h=3)) == []
    assert [n.status for n in sim.last_plan.notices] == ["held"]


def test_non_staff_addresses_are_refused_twice():
    outsider = "someone@gmail.com"
    # 1) A non-staff address in the allowlist stops the whole run at startup.
    assert A.Settings.from_env(dict(ENV, AM_NOTIFY_ALLOWLIST=outsider)).problems
    # 2) Even if it got past that, a non-staff AM address is never sent to.
    sim = Sim()
    sim.settings = A.Settings("on", "https://x", frozenset({outsider}), "",
                              A.DEFAULT_DASHBOARD_URL, ())
    sub = dict(SUB, am_email=outsider)
    sim.night(day(0, sub=sub, leads_uncontacted_24h=3))
    assert sim.night(day(1, sub=sub, leads_uncontacted_24h=3)) == []
    assert [n.status for n in sim.last_plan.notices] == ["held"]


def test_redirect_goes_to_matthew_labelled_for_lauren():
    sim = Sim(env=dict(ENV, AM_NOTIFY_REDIRECT=MATTHEW))
    sim.night(day(0, leads_uncontacted_24h=3))
    [note] = sim.night(day(1, leads_uncontacted_24h=3))
    payload = note.payload
    assert payload["am_email"] == MATTHEW and payload["route"] == "matthew"
    assert payload["subject"].startswith("[for Lauren] ")
    assert payload["body_text"].startswith("Hi Matthew,")
    assert "would have gone to Lauren" in payload["body_text"]


def test_per_am_cap_sends_the_oldest_first():
    subs = [dict(SUB, location_id=f"loc{i}", slug=f"s{i}", name=f"Client {i}")
            for i in range(A.PER_AM_DAILY_CAP + 2)]
    # Problems pile up while Lauren is not yet allowed: eight start on night
    # 0, the last two a night later.
    sim = Sim(env=dict(ENV, AM_NOTIFY_ALLOWLIST=""))
    sim.night(*[day(0, sub=s, leads_uncontacted_24h=3) for s in subs[:-2]])
    sim.night(*[day(1, sub=s, leads_uncontacted_24h=3) for s in subs])
    sim.night(*[day(2, sub=s, leads_uncontacted_24h=3) for s in subs])
    sim.settings = A.Settings.from_env(ENV)          # Lauren allowed from tonight
    sent = sim.night(*[day(3, sub=s, leads_uncontacted_24h=3) for s in subs])
    deferred = [n.location_id for n in sim.last_plan.notices if n.status == "deferred"]
    assert len(sent) == A.PER_AM_DAILY_CAP
    assert sorted(deferred) == sorted(s["location_id"] for s in subs[-2:])
    tomorrow = sim.night(*[day(4, sub=s, leads_uncontacted_24h=3) for s in subs])
    assert sorted(n.location_id for n in tomorrow) == sorted(deferred)   # rolled over


def test_run_breaker_sends_nothing():
    ams = [f"am{i}@smallscreenproducer.com" for i in range(A.RUN_BREAKER + 1)]
    subs = [dict(SUB, location_id=f"loc{i}", am_email=am) for i, am in enumerate(ams)]
    sim = Sim(env=dict(ENV, AM_NOTIFY_ALLOWLIST=",".join(ams)))
    sim.night(*[day(0, sub=s, leads_uncontacted_24h=3) for s in subs])
    assert sim.night(*[day(1, sub=s, leads_uncontacted_24h=3) for s in subs]) == []
    assert {n.status for n in sim.last_plan.notices} == {"breaker"}


# -- what counts as each alert type ------------------------------------------------------

def test_t2_uses_the_accounts_slow_response_threshold():
    default = dict(SUB, thresholds={})
    assert not A.build_issues(day(0, sub=default, leads_uncontacted_24h=2))
    [issue] = A.build_issues(day(0, leads_uncontacted_24h=2))
    assert issue.code == "SLOW_RESPONSE" and issue.severity == "amber"


def test_t3_needs_five_waiting():
    assert not A.build_issues(day(0, convos_waiting=4, convos_waiting_max_hours=90))
    [issue] = A.build_issues(day(0, convos_waiting=5, convos_waiting_max_hours=90))
    assert issue.severity == "red" and "4 days" in issue.sentence


def test_t4_watches_website_forms_only_and_respects_their_usual_pace():
    base = {"kind": "form", "status": "silent", "channel": "Website form",
            "last_submission_at": "2026-08-25"}                          # 7 days quiet
    busy = dict(base, form_id="busy", name="Contact Us", subs_30d=12)
    ad = dict(busy, form_id="ad", name="Hot Tub - FB Ad", channel="Facebook ad form")
    slow = dict(base, form_id="slow", name="Brochure", subs_30d=2)
    retiring = dict(busy, form_id="old", name="ZZ-RETIRE Contact Us")
    issues = A.build_issues(day(0, form_rows=[busy, ad, slow, retiring]))
    assert [(i.code, i.entity_key) for i in issues] == [("FORM_WENT_SILENT", "busy")]
    assert "about 3 leads a week" in issues[0].sentence and "Aug 25" in issues[0].sentence


def test_t4_includes_all_forms_silent_and_missed_weekly_tests():
    checks = [{"form_id": "f9", "name": "Quote Request", "last_check_at": "2026-08-20",
               "contact_ok": True}]
    issues = A.build_issues(day(0, flags=[{"code": "FORM_SILENT", "severity": "red"}],
                                form_checks=checks, form_submissions_trailing_avg=6))
    assert {(i.code, i.entity_key) for i in issues} == {("FORM_SILENT", ""),
                                                        ("FORM_CHECK_MISSED", "f9")}


def test_source_drop_reads_the_source_from_new_and_old_rows():
    t1 = dict(SUB, alert_triggers=["T1"])
    new = {"code": "SOURCE_DROP", "severity": "red", "entity_type": "source",
           "entity_name": "Google Ads"}
    old = {"code": "SOURCE_DROP", "severity": "red", "title": "Source drop: facebook"}
    issues = A.build_issues(day(0, sub=t1, flags=[new, old]))
    assert sorted(i.entity_key for i in issues) == ["Google Ads", "facebook"]
    amber = dict(new, severity="amber")
    assert not A.build_issues(day(0, sub=t1, flags=[amber]))     # T1 is red only


# -- payload ------------------------------------------------------------------------------

def test_payload_is_flat_strings_with_every_field_the_workflow_maps():
    sim = Sim()
    sim.night(day(0, leads_uncontacted_24h=3))
    [note] = sim.night(day(1, leads_uncontacted_24h=3))
    payload = note.payload
    assert all(isinstance(v, str) for v in payload.values()), "GHL maps flat strings only"
    for key in ("event", "schema_version", "is_test", "send_id", "run_date", "account_name",
                "location_id", "am_email", "am_first_name", "route", "urgency", "item_count",
                "subject", "body_text", "body_html", "item_1", "item_5", "resolved_text",
                "dashboard_url"):
        assert key in payload, key
    assert payload["event"] == "am_account_health" and payload["schema_version"] == "2"
    assert payload["is_test"] == "false" and payload["route"] == "lauren"
    assert payload["dashboard_url"].endswith("/account/locA")
    assert payload["item_1"] == "NEW: 3 leads going cold" and payload["item_2"] == ""
    assert "<html" not in payload["body_html"]      # a fragment the workflow drops in


def test_only_send_test_marks_is_test(monkeypatch):
    posted = []
    monkeypatch.setattr(A, "post_alert", lambda url, payload, timeout=15.0:
                        (posted.append(payload), (200, None))[1])
    assert A.send_test_notice(log=lambda *a: None, env=ENV, today=D0) == 0
    assert posted[0]["is_test"] == "true" and posted[0]["location_id"] == "SAMPLE"
    assert posted[0]["account_name"] == "Sample Pool & Spa"


def test_send_test_needs_the_url():
    assert A.send_test_notice(log=lambda *a: None, env={}, today=D0) == 2


def test_delivery_sample_posts_once_to_matthew_even_with_other_redirect(monkeypatch):
    posted = []
    monkeypatch.setattr(A, "post_alert", lambda url, payload:
                        (posted.append(payload), (200, None))[1])
    env = dict(ENV, AUTOMATION_WEBHOOKS="dry",
               AM_NOTIFY_REDIRECT="ldegner@smallscreenproducer.com")
    assert A.send_test_notice(log=lambda *a: None, env=env, today=D0,
                             deliver_to="mcarlson@smallscreenproducer.com") == 0
    [payload] = posted
    assert payload["is_test"] == "false"
    assert payload["is_delivery_test"] == "true"
    assert payload["route"] == "matthew"
    assert payload["am_email"] == "mcarlson@smallscreenproducer.com"
    assert payload["location_id"] == "SAMPLE"
    assert payload["subject"].startswith("[TEST")


def test_delivery_sample_rejects_any_other_recipient(monkeypatch):
    def unexpected_post(*args):
        raise AssertionError("must not post")
    monkeypatch.setattr(A, "post_alert", unexpected_post)
    assert A.send_test_notice(env=ENV, deliver_to="ldegner@smallscreenproducer.com") == 2


def test_delivery_sample_cli_exits_before_collection(monkeypatch):
    calls = []
    monkeypatch.setattr(A, "send_test_notice", lambda **kw: (calls.append(kw), 0)[1])
    assert main_mod.run(["--send-test-email-to", "mcarlson@smallscreenproducer.com"]) == 0
    assert calls[0]["deliver_to"] == "mcarlson@smallscreenproducer.com"


def test_post_errors_never_echo_the_webhook_url(monkeypatch):
    url = "https://services.example.invalid/hooks/secret-token-123"

    def boom(request, timeout):
        raise OSError(f"cannot reach {url}")
    monkeypatch.setattr(A.urllib.request, "urlopen", boom)
    status, error = A.post_alert(url, {"a": "b"})
    assert status is None and "secret-token-123" not in error


# -- notify_run against a store ---------------------------------------------------------------

def results_for(n, **metrics):
    d = day(n, **metrics)
    return {"locA": {"sub": dict(SUB), "metrics": d.metrics, "flags": [], "form_health": [],
                     "details": {}, "gate_passed": True}}, d.today


def test_off_touches_nothing():
    store = FakeStore(subs=[SUB])
    results, today = results_for(0, leads_uncontacted_24h=3)
    tally = A.notify_run(store, results, today, log=lambda *a: None,
                         env=dict(ENV, AUTOMATION_WEBHOOKS="off"))
    assert sum(tally.values()) == 0 and store.alert_state == {}


def test_dry_run_keeps_state_but_posts_and_audits_nothing(monkeypatch):
    monkeypatch.setattr(A, "post_alert", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    store = FakeStore(subs=[SUB])
    env = dict(ENV, AUTOMATION_WEBHOOKS="dry")
    for n in (0, 1):
        results, today = results_for(n, leads_uncontacted_24h=3)
        tally = A.notify_run(store, results, today, log=lambda *a: None, env=env)
    assert tally["dry"] == 1 and store.automation_sends == []
    row = store.alert_state[("locA", "SLOW_RESPONSE", "")]
    assert row["notified_to"] == f"dry:{LAUREN}"


def test_live_send_posts_audits_and_remembers(monkeypatch):
    posted = []
    monkeypatch.setattr(A, "post_alert", lambda url, payload, timeout=15.0:
                        (posted.append(payload), (200, None))[1])
    store = FakeStore(subs=[SUB])
    for n in (0, 1, 2):
        results, today = results_for(n, leads_uncontacted_24h=3)
        A.notify_run(store, results, today, run_id=7, log=lambda *a: None, env=ENV)
    assert len(posted) == 1                         # night 1 only; night 2 is quiet
    audit = store.automation_sends[0]
    assert (audit["flag_code"], audit["status"], audit["run_id"]) == ("AM_NOTICE", "sent", 7)
    assert audit["item_codes"] == ["SLOW_RESPONSE"]


def test_a_failed_post_is_retried_the_next_night(monkeypatch):
    outcomes = iter([(None, "HTTP 502"), (200, None)])
    monkeypatch.setattr(A, "post_alert", lambda *a, **k: next(outcomes))
    store = FakeStore(subs=[SUB])
    tallies = []
    for n in (0, 1, 2):
        results, today = results_for(n, leads_uncontacted_24h=3)
        tallies.append(A.notify_run(store, results, today, log=lambda *a: None, env=ENV))
    assert tallies[1]["failed"] == 1 and tallies[2]["sent"] == 1


def test_unreadable_state_sends_nothing(monkeypatch):
    class Broken(FakeStore):
        def read_alert_state(self):
            raise RuntimeError("relation alert_state does not exist")
    monkeypatch.setattr(A, "post_alert", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    results, today = results_for(1, leads_uncontacted_24h=3)
    tally = A.notify_run(Broken(subs=[SUB]), results, today, log=lambda *a: None, env=ENV)
    assert sum(tally.values()) == 0


def test_misconfigured_switches_send_nothing():
    for env in (dict(ENV, AUTOMATION_WEBHOOK_URL=""),
                dict(ENV, AM_NOTIFY_REDIRECT="someone@gmail.com")):
        results, today = results_for(1, leads_uncontacted_24h=3)
        tally = A.notify_run(FakeStore(subs=[SUB]), results, today, log=lambda *a: None, env=env)
        assert sum(tally.values()) == 0


def test_whole_pipeline_dry_run_through_main(monkeypatch):
    """main.run() twice (two nights) in dry mode: state is tracked, the
    second night logs a note, nothing is posted."""
    monkeypatch.setattr(A, "post_alert", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setenv("AUTOMATION_WEBHOOKS", "dry")
    monkeypatch.setenv("AM_NOTIFY_ALLOWLIST", "lisa@smallscreenproducer.com")
    client = dict(CLIENT_SUB, alert_triggers=["T2"], thresholds={"slow_response_min": 1})
    store = FakeStore(subs=[PARENT_SUB, client])
    lines = []
    monkeypatch.setattr(main_mod, "log", lambda message: lines.append(message))
    from datetime import datetime, timezone
    now = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)
    for iso in ("2026-08-18", "2026-08-19"):
        main_mod.run(["--date", iso], store=store, client_factory=make_factory(), now_utc=now)
    assert ("locA", "SLOW_RESPONSE", "") in store.alert_state
    assert any(line.startswith("notices [dry]: pilot1") for line in lines)
    assert store.automation_sends == []


# -- preview ------------------------------------------------------------------------------------

class HistoryStore:
    def __init__(self, subs, history):
        self.subs, self.history = subs, history

    def load_subaccounts(self, active=True):
        return [dict(s) for s in self.subs]

    def read_notice_history(self, location_ids, start, end):
        return self.history

    def read_active_acks(self, snapshot_date):
        return {}


def test_preview_replays_history_without_writing(capsys):
    history = {("locA", (D0 + timedelta(days=n)).isoformat()): {
        "metrics": {"leads_uncontacted_24h": 3, "client_users": 4}, "flags": [],
        "form_rows": [], "form_checks": [], "gate_passed": True} for n in range(5)}
    counts = A.preview(HistoryStore([SUB], history), D0 + timedelta(days=4), days=5,
                       log=lambda *a: None, env={})
    assert counts == {LAUREN: 1}
    out = capsys.readouterr().out
    assert "Flohr Pools: 3 leads going cold" in out and "ALREADY OPEN" in out


# -- the Monday digest closes the loop -----------------------------------------------------

def _resolved_row(notified_to, resolved_at=date(2026, 9, 25), told=False):
    return {"location_id": "locA", "code": "SLOW_RESPONSE", "entity_key": "", "status": "resolved",
            "notified_to": notified_to, "resolve_told": told, "resolved_at": resolved_at,
            "cleared_text": "Leads going cold: no lead from this week is waiting over a day "
                            "for a first reply."}


def test_digest_carries_cleared_lines_only_for_what_the_am_was_really_told():
    monday = date(2026, 9, 28)
    subs = [SUB]
    assert A.cleared_for_digest([_resolved_row(LAUREN)], subs, monday)
    for row in (_resolved_row(f"dry:{LAUREN}"),           # a rehearsal
                _resolved_row(MATTHEW),                   # the shadow-week redirect
                _resolved_row(LAUREN, told=True),         # already delivered
                _resolved_row(LAUREN, resolved_at=date(2026, 9, 1))):   # stale
        assert A.cleared_for_digest([row], subs, monday) == {}


def test_digest_section_renders_and_is_marked_delivered_once():
    from .. import digest as digest_mod
    monday = date(2026, 9, 28)
    store = FakeStore(subs=[SUB])
    store.alert_state[("locA", "SLOW_RESPONSE", "")] = _resolved_row(LAUREN)
    cleared = A.cleared_for_digest(store.read_alert_state(), [SUB], monday)
    digests = digest_mod.build_digests(
        [SUB], {"locA": {"gate_passed": True, "client_users": 3}}, {}, {}, monday.isoformat(),
        cleared_by_loc={loc: [r["cleared_text"] for r in rows] for loc, rows in cleared.items()})
    message = digests[LAUREN]
    assert "CLEARED SINCE YOUR LAST ALERT:" in message["text"]
    assert "Flohr Pools: Leads going cold" in message["text"]
    assert "Cleared since your last alert (1)" in message["html"]
    A.mark_cleared_delivered(store, cleared)
    assert A.cleared_for_digest(store.read_alert_state(), [SUB], monday) == {}



def test_a_redirected_digest_does_not_close_out_cleared_lines(monkeypatch):
    # The cleared line waits until Lauren reads her own digest: a copy sent
    # to the rehearsal address told her nothing.
    from .. import main
    monday = date(2026, 9, 28)
    store = FakeStore(subs=[SUB])
    store.alert_state[("locA", "SLOW_RESPONSE", "")] = _resolved_row(LAUREN)
    cleared = A.cleared_for_digest(store.read_alert_state(), [SUB], monday)
    monkeypatch.delenv("DIGEST_ALLOWLIST", raising=False)
    monkeypatch.setenv("DIGEST_REDIRECT", MATTHEW)
    main._close_out_cleared(store, cleared, sent=1, failed=0)
    assert A.cleared_for_digest(store.read_alert_state(), [SUB], monday)
    monkeypatch.delenv("DIGEST_REDIRECT")
    monkeypatch.setenv("DIGEST_ALLOWLIST", LAUREN)
    main._close_out_cleared(store, cleared, sent=1, failed=0)
    assert A.cleared_for_digest(store.read_alert_state(), [SUB], monday) == {}

# -- recipient and webhook hygiene (SECURITY-SPEC SEC-03, SEC-12) ----------------------------

def test_settings_refuse_bad_recipients_and_never_echo_the_url():
    bad = dict(ENV, AM_NOTIFY_ALLOWLIST=f"{LAUREN}, x@evil.example",
               AM_NOTIFY_REDIRECT=f"x@evil.example, {MATTHEW}",
               AUTOMATION_WEBHOOK_URL="http://hooks.example.invalid/secret-123")
    problems = A.Settings.from_env(bad).problems
    assert len(problems) == 3
    assert not any("secret-123" in p for p in problems)


def test_a_smuggled_second_address_is_held():
    sub = dict(SUB, am_email=f"x@evil.example, {LAUREN}")
    sim = Sim(env=dict(ENV, AM_NOTIFY_ALLOWLIST=LAUREN))
    sim.night(day(0, sub=sub, leads_uncontacted_24h=3))
    assert sim.night(day(1, sub=sub, leads_uncontacted_24h=3)) == []
    assert [n.status for n in sim.last_plan.notices] == ["held"]
