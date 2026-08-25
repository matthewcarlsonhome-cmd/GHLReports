"""Tests for the Insight-to-Workflow Bridge (collector/automation.py).

The rules worth protecting are the ones that keep this from becoming a
nuisance: only listed codes fire, only newly-appeared ones fire, a first run
is silent, the cap holds, and no PII ever reaches the payload.
"""

from datetime import date

from collector import automation


SUB = {"location_id": "locA", "slug": "softub", "name": "Softub Express",
       "am_email": "mcarlson@smallscreenproducer.com"}
METRICS = {"opps_open": 35, "opps_stale": 35, "opps_moved_30d": 0,
           "bottleneck_stage": "🚧 Construction Process · 🛠 Phase 12 ✋📩", 
           "bottleneck_value_usd": 224000}


def flag(code, severity="amber", **kw):
    base = {"code": code, "severity": severity, "title": code.replace("_", " ").title(),
            "action": "do the thing"}
    base.update(kw)
    return base


# -- which flags are allowed to fire -------------------------------------

def test_only_listed_codes_fire():
    flags = [flag("FORM_WENT_SILENT"), flag("CONVOS_WAITING", "red"),
             flag("UNASSIGNED_LEADS"), flag("PIPELINE_HYGIENE")]
    picked = [f["code"] for f in automation.select_alerts(flags, prev_codes=[])]
    assert picked == ["FORM_WENT_SILENT"]


def test_source_drop_is_red_only():
    amber = automation.select_alerts([flag("SOURCE_DROP", "amber")], prev_codes=[])
    red = automation.select_alerts([flag("SOURCE_DROP", "red")], prev_codes=[])
    assert amber == [] and len(red) == 1


def test_pipeline_codes_are_included():
    flags = [flag("PIPELINE_FROZEN", "red"), flag("STALE_PIPELINE")]
    picked = {f["code"] for f in automation.select_alerts(flags, prev_codes=[])}
    assert picked == {"PIPELINE_FROZEN", "STALE_PIPELINE"}


# -- newly appeared, not merely present ----------------------------------

def test_standing_flag_does_not_refire():
    flags = [flag("FORM_WENT_SILENT")]
    assert automation.select_alerts(flags, prev_codes=["FORM_WENT_SILENT"]) == []


def test_first_run_for_a_location_is_silent():
    # prev_codes None = no prior snapshot at all. Every flag would look new;
    # announcing them all is the storm the rule exists to prevent.
    flags = [flag("FORM_WENT_SILENT"), flag("NO_DELIVERY", "red")]
    assert automation.select_alerts(flags, prev_codes=None) == []


def test_flag_that_cleared_then_returned_fires_again():
    flags = [flag("SOCIAL_DISCONNECTED", "red")]
    assert len(automation.select_alerts(flags, prev_codes=["FORM_WENT_SILENT"])) == 1


# -- payload shape --------------------------------------------------------

def test_payload_is_flat_and_carries_pipeline_context():
    payload = automation.build_payload(
        flag("PIPELINE_FROZEN", "red"), SUB, METRICS, "2026-08-25")
    assert all(isinstance(v, (str, int, float)) for v in payload.values()), \
        "GHL cannot map nested webhook fields"
    assert payload["account_name"] == "Softub Express"
    assert payload["dashboard_url"].endswith("/account/softub")
    assert payload["opps_open"] == 35 and payload["opps_moved_30d"] == 0


def test_emoji_are_stripped_from_stage_names():
    payload = automation.build_payload(flag("PIPELINE_FROZEN"), SUB, METRICS, "2026-08-25")
    assert payload["stage_name"] == "Construction Process · Phase 12"


def test_payload_carries_no_pii():
    payload = automation.build_payload(
        flag("FORM_WENT_SILENT", entity_type="form", entity_name="Hot Tub Brochure"),
        SUB, METRICS, "2026-08-25")
    banned = ("phone", "contact_name", "email_body", "message", "first_name", "last_name")
    assert not any(key in payload for key in banned)
    # am_email is an SSP staff address, deliberately present; nothing else is.
    assert payload["am_email"].endswith("@smallscreenproducer.com")


def test_weekly_payload_needs_a_bottleneck_stage():
    assert automation.weekly_pipeline_payload(SUB, {"opps_open": 5}, "2026-08-25") is None
    payload = automation.weekly_pipeline_payload(SUB, METRICS, "2026-08-25")
    assert payload["flag_code"] == "PIPELINE_WEEKLY"
    assert "$224,000" in payload["action"]
    assert "🚧" not in payload["action"]


# -- mode switch ----------------------------------------------------------

def test_mode_defaults_to_off():
    assert automation.mode_from_env({}) == "off"
    assert automation.mode_from_env({"AUTOMATION_WEBHOOKS": "garbage"}) == "off"
    assert automation.mode_from_env({"AUTOMATION_WEBHOOKS": "ON"}) == "on"
    assert automation.mode_from_env({"AUTOMATION_WEBHOOKS": "dry"}) == "dry"


# -- end to end through send_run_alerts -----------------------------------

class FakeStore:
    def __init__(self, prev): self.prev, self.rows = prev, []
    def read_prev_flag_codes(self, location_id, snapshot_date): return self.prev
    def read_sent_alert_keys(self, snapshot_date): return set()
    def record_automation_send(self, row): self.rows.append(row)


def run(monkeypatch, results, prev, env, posted=None, run_date=date(2026, 8, 25)):
    store = FakeStore(prev)
    sent = posted if posted is not None else []
    monkeypatch.setattr(automation, "post_alert",
                        lambda url, payload, timeout=15.0: (sent.append(payload), (200, None))[1])
    tally = automation.send_run_alerts(store, results, run_date, run_id=7,
                                       log=lambda *a: None, env=env)
    return tally, store, sent


def test_off_sends_nothing(monkeypatch):
    results = {"locA": {"sub": SUB, "metrics": METRICS, "flags": [flag("FORM_WENT_SILENT")]}}
    tally, store, sent = run(monkeypatch, results, [], {"AUTOMATION_WEBHOOKS": "off",
                                                        "AUTOMATION_WEBHOOK_URL": "https://x"})
    assert tally["sent"] == 0 and sent == [] and store.rows == []


def test_missing_url_sends_nothing(monkeypatch):
    results = {"locA": {"sub": SUB, "metrics": METRICS, "flags": [flag("FORM_WENT_SILENT")]}}
    tally, _, sent = run(monkeypatch, results, [], {"AUTOMATION_WEBHOOKS": "on"})
    assert tally["sent"] == 0 and sent == []


def test_dry_run_records_nothing_and_posts_nothing(monkeypatch):
    results = {"locA": {"sub": SUB, "metrics": METRICS, "flags": [flag("FORM_WENT_SILENT")]}}
    tally, store, sent = run(monkeypatch, results, [], {"AUTOMATION_WEBHOOKS": "dry",
                                                        "AUTOMATION_WEBHOOK_URL": "https://x"})
    assert tally["dry"] == 1 and sent == [] and store.rows == []


def test_live_send_writes_an_audit_row(monkeypatch):
    results = {"locA": {"sub": SUB, "metrics": METRICS, "flags": [flag("FORM_WENT_SILENT")]}}
    tally, store, sent = run(monkeypatch, results, [], {"AUTOMATION_WEBHOOKS": "on",
                                                        "AUTOMATION_WEBHOOK_URL": "https://x"})
    assert tally["sent"] == 1 and len(sent) == 1
    assert store.rows[0]["status"] == "sent" and store.rows[0]["mode"] == "live"
    assert store.rows[0]["run_id"] == 7


def test_cap_holds_and_excess_is_recorded(monkeypatch):
    results = {f"loc{i}": {"sub": {**SUB, "location_id": f"loc{i}", "slug": f"s{i}"},
                           "metrics": METRICS, "flags": [flag("FORM_WENT_SILENT")]}
               for i in range(automation.DAILY_SEND_CAP + 4)}
    tally, store, sent = run(monkeypatch, results, [], {"AUTOMATION_WEBHOOKS": "on",
                                                        "AUTOMATION_WEBHOOK_URL": "https://x"})
    assert tally["sent"] == automation.DAILY_SEND_CAP
    assert tally["skipped"] == 4
    assert sum(1 for r in store.rows if r["status"] == "skipped_cap") == 4


def test_weekly_digest_only_on_monday(monkeypatch):
    results = {"locA": {"sub": SUB, "metrics": METRICS, "flags": []}}
    env = {"AUTOMATION_WEBHOOKS": "on", "AUTOMATION_WEBHOOK_URL": "https://x"}
    _, _, tues = run(monkeypatch, results, [], env, run_date=date(2026, 8, 25))
    _, _, mon = run(monkeypatch, results, [], env, run_date=date(2026, 8, 24))
    assert tues == []
    assert [p["flag_code"] for p in mon] == ["PIPELINE_WEEKLY"]


def test_a_broken_store_read_skips_that_location_only(monkeypatch):
    class Boom(FakeStore):
        def read_prev_flag_codes(self, location_id, snapshot_date):
            if location_id == "locA":
                raise RuntimeError("supabase hiccup")
            return []
    store = Boom([])
    sent = []
    monkeypatch.setattr(automation, "post_alert",
                        lambda url, payload, timeout=15.0: (sent.append(payload), (200, None))[1])
    results = {"locA": {"sub": SUB, "metrics": METRICS, "flags": [flag("FORM_WENT_SILENT")]},
               "locB": {"sub": {**SUB, "location_id": "locB", "slug": "other"},
                        "metrics": METRICS, "flags": [flag("NO_DELIVERY", "red")]}}
    tally = automation.send_run_alerts(store, results, date(2026, 8, 25), log=lambda *a: None,
                                       env={"AUTOMATION_WEBHOOKS": "on",
                                            "AUTOMATION_WEBHOOK_URL": "https://x"})
    assert tally["sent"] == 1 and [p["flag_code"] for p in sent] == ["NO_DELIVERY"]


def test_failed_post_is_recorded_not_raised(monkeypatch):
    results = {"locA": {"sub": SUB, "metrics": METRICS, "flags": [flag("FORM_WENT_SILENT")]}}
    store = FakeStore([])
    monkeypatch.setattr(automation, "post_alert",
                        lambda url, payload, timeout=15.0: (None, "ConnectionError: refused"))
    tally = automation.send_run_alerts(store, results, date(2026, 8, 25), log=lambda *a: None,
                                       env={"AUTOMATION_WEBHOOKS": "on",
                                            "AUTOMATION_WEBHOOK_URL": "https://x"})
    assert tally["failed"] == 1 and store.rows[0]["status"] == "failed"


# -- rerun safety ---------------------------------------------------------

class DedupeStore(FakeStore):
    def __init__(self, prev, sent_keys):
        super().__init__(prev)
        self.sent_keys = sent_keys
    def read_sent_alert_keys(self, snapshot_date): return self.sent_keys


def test_rerunning_a_day_does_not_resend(monkeypatch):
    # Same flags, same date, already delivered once — the POST must not repeat.
    store = DedupeStore([], {("locA", "FORM_WENT_SILENT", "Hot Tub Brochure")})
    sent = []
    monkeypatch.setattr(automation, "post_alert",
                        lambda url, payload, timeout=15.0: (sent.append(payload), (200, None))[1])
    results = {"locA": {"sub": SUB, "metrics": METRICS,
                        "flags": [flag("FORM_WENT_SILENT", entity_type="form",
                                       entity_name="Hot Tub Brochure")]}}
    tally = automation.send_run_alerts(store, results, date(2026, 8, 25), log=lambda *a: None,
                                       env={"AUTOMATION_WEBHOOKS": "on",
                                            "AUTOMATION_WEBHOOK_URL": "https://x"})
    assert tally["sent"] == 0 and sent == []


def test_a_different_form_on_the_same_day_still_fires(monkeypatch):
    store = DedupeStore([], {("locA", "FORM_WENT_SILENT", "Hot Tub Brochure")})
    sent = []
    monkeypatch.setattr(automation, "post_alert",
                        lambda url, payload, timeout=15.0: (sent.append(payload), (200, None))[1])
    results = {"locA": {"sub": SUB, "metrics": METRICS,
                        "flags": [flag("FORM_WENT_SILENT", entity_type="form",
                                       entity_name="Service Request")]}}
    tally = automation.send_run_alerts(store, results, date(2026, 8, 25), log=lambda *a: None,
                                       env={"AUTOMATION_WEBHOOKS": "on",
                                            "AUTOMATION_WEBHOOK_URL": "https://x"})
    assert tally["sent"] == 1


def test_unreadable_audit_table_aborts_rather_than_risking_duplicates(monkeypatch):
    class Boom(FakeStore):
        def read_sent_alert_keys(self, snapshot_date): raise RuntimeError("down")
    sent = []
    monkeypatch.setattr(automation, "post_alert",
                        lambda url, payload, timeout=15.0: (sent.append(payload), (200, None))[1])
    results = {"locA": {"sub": SUB, "metrics": METRICS, "flags": [flag("FORM_WENT_SILENT")]}}
    tally = automation.send_run_alerts(Boom([]), results, date(2026, 8, 25), log=lambda *a: None,
                                       env={"AUTOMATION_WEBHOOKS": "on",
                                            "AUTOMATION_WEBHOOK_URL": "https://x"})
    assert tally == {"sent": 0, "dry": 0, "failed": 0, "skipped": 0} and sent == []
