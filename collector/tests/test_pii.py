"""PII boundary test (spec 7.2/7.6): no phone number, email address, or
message body from any fixture record may appear in the produced snapshot,
flags, lead_events, or run details. The fixtures carry distinctive canary
values so a leak is unambiguous."""

import json

from .test_mock_run import run_with
from .fakes import CLIENT_SUB, PARENT_SUB, FakeStore, make_factory

PII_CANARIES = [
    "+1608555",                     # every fixture phone number shares this prefix
    "@example-client.com",          # every fixture contact email
    "PII_BODY_DO_NOT_STORE",        # every fixture message body
    "lisa@x.com",                   # user emails from the users fixture
]


def test_no_pii_reaches_any_stored_row():
    store = FakeStore(subs=[PARENT_SUB, CLIENT_SUB])
    assert run_with(store, make_factory()) == 0

    everything = json.dumps({
        "snapshots": {"|".join(k): v for k, v in store.snapshots.items()},
        "flags": {"|".join(k): v for k, v in store.flags.items()},
        "lead_events": {"|".join(k): v for k, v in store.lead_events.items()},
        "lead_history": {"|".join(k): v for k, v in store.lead_history.items()},
        "runs": store.runs,
    }, default=str)

    for canary in PII_CANARIES:
        assert canary not in everything, f"PII canary {canary!r} leaked into stored rows"

    # presence checks survive as booleans, not values
    snap = store.snapshots[("locA", "2026-08-18")]
    assert "leads_missing_phone_pct_7d" in snap


def _two_night_note(day_kwargs):
    """Run the AM-note logic for two nights on the same inputs and return the
    payload of the note it produces on the second night."""
    from datetime import date
    from collector import automation

    settings = automation.Settings.from_env({
        "AUTOMATION_WEBHOOKS": "on", "AUTOMATION_WEBHOOK_URL": "https://x.invalid/h",
        "AM_NOTIFY_ALLOWLIST": "mcarlson@smallscreenproducer.com"})
    sub = {"location_id": "locA", "slug": "acme", "name": "Acme Pools",
           "am_email": "mcarlson@smallscreenproducer.com", "mlh_status": "active",
           "alert_triggers": ["T1", "T2", "T3", "T4", "T6", "T7"],
           "thresholds": {"slow_response_min": 2}, "alert_tracking_since": "2026-08-01"}
    rows: dict = {}
    notes = []
    for night in (date(2026, 8, 25), date(2026, 8, 26)):
        day = automation.AccountDay(sub=sub, today=night, gate_passed=True, **day_kwargs)
        plan = automation.evaluate([day], rows, {}, set(), settings, night)
        notes = [n for n in plan.notices if not n.status]
        for note in notes:
            note.payload = automation.compose(note, settings)
            automation.commit(rows[note.location_id], note.items, note.cleared, night,
                              note.audience)
    assert len(notes) == 1
    return notes[0].payload


def test_am_note_carries_no_contact_pii():
    """The GHL webhook is the only path that sends data OUT of the app.

    Whatever the flag layer produced (contact names in actions and entities,
    deal names that are customer names), the note that leaves the building
    must carry counts and account-level facts only.
    """
    payload = _two_night_note({
        "metrics": {"leads_uncontacted_24h": 3, "convos_waiting": 6,
                    "convos_waiting_max_hours": 30, "client_users": 4},
        "flags": [
            {"code": "CONVOS_WAITING", "severity": "red", "title": "Inbound conversations waiting",
             "action": "6 inbound waiting, longest 30h. Oldest: Maria Testani.",
             "entity_type": "conversation", "entity_id": "cnv_9", "entity_name": "Maria Testani",
             "deep_link": "https://crm.example.com/conversations/cnv_9"},
            {"code": "SLOW_RESPONSE", "severity": "amber", "title": "Leads sitting uncontacted",
             "action": "3 leads uncontacted >24h.", "entity_type": "contact",
             "entity_id": "cnt_123", "entity_name": "Jane Smith +16085551234",
             "deep_link": "https://crm.example.com/contacts/cnt_123"},
            {"code": "STALE_PIPELINE", "severity": "red", "title": "Stale pipeline",
             "action": "4 deals idle 14d+.", "entity_type": "opportunity",
             "entity_name": "Eric Dybala"},
        ],
        "form_rows": [], "form_checks": []})
    serialized = str(payload)
    for banned in ("Maria Testani", "Jane Smith", "Eric Dybala", "cnt_123", "cnv_9",
                   "crm.example.com", *PII_CANARIES):
        assert banned not in serialized, f"{banned!r} must never leave the app"
    assert payload["am_email"].endswith("@smallscreenproducer.com")   # staff only
    assert "customers waiting" in payload["subject"] or "going cold" in payload["subject"]


def test_form_names_and_pages_still_come_through():
    """The PII rules must not gut the notes that need a name to be useful."""
    payload = _two_night_note({
        "metrics": {"client_users": 4},
        "flags": [],
        "form_rows": [{"kind": "form", "form_id": "f1", "name": "Hot Tub Brochure",
                       "status": "silent", "channel": "Website form", "subs_30d": 12,
                       "last_submission_at": "2026-08-16",
                       "page_url": "https://acme.example/hot-tubs"}],
        "form_checks": []})
    assert '"Hot Tub Brochure" form went quiet' in payload["subject"]
    assert "acme.example/hot-tubs" in payload["body_text"]
