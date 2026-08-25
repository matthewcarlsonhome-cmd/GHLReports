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


def test_automation_payload_carries_no_contact_pii():
    """The webhook bridge is the only path that sends data OUT of the app.

    Whatever the flag layer produced, the payload that leaves the building
    must never carry a lead's name, phone number, email, or message text.
    """
    from collector import automation

    payload = automation.build_payload(
        {"code": "SLOW_RESPONSE", "severity": "amber", "title": "Leads sitting uncontacted",
         "action": "3 leads uncontacted for 2+ days.",
         "entity_type": "contact", "entity_id": "cnt_123", "entity_name": "Maria Testani",
         "deep_link": "https://crm.example.com/contacts/cnt_123"},
        {"location_id": "locA", "slug": "acme", "name": "Acme Pools",
         "am_email": "mcarlson@smallscreenproducer.com"},
        {"opps_open": 4}, "2026-08-25")

    for banned in ("entity_id", "deep_link", "phone", "contact_phone", "contact_email",
                   "message", "body", "email"):
        assert banned not in payload, f"{banned} must never leave the app"

    serialized = str(payload)
    assert "Maria Testani" not in serialized, "a contact entity name must be dropped"
    assert "cnt_123" not in serialized
    assert "crm.example.com" not in serialized


def test_opportunity_names_are_dropped_because_they_are_usually_people():
    """Live data check: GHL opportunity names are routinely the customer's
    name, and STALE_PIPELINE attaches one. It must not reach the payload."""
    from collector import automation

    payload = automation.build_payload(
        {"code": "STALE_PIPELINE", "severity": "amber", "title": "Stale pipeline",
         "action": "4 deals idle 14d+. Re-engage.",
         "entity_type": "opportunity", "entity_name": "Eric Dybala"},
        {"location_id": "locA", "slug": "acme", "name": "Acme Pools"},
        {"opps_open": 6, "opps_stale": 4}, "2026-08-25")
    assert payload["entity_name"] == ""
    assert "Dybala" not in str(payload)


def test_form_names_still_come_through():
    """The suppression must not gut the alerts that need a name to be useful."""
    from collector import automation

    payload = automation.build_payload(
        {"code": "FORM_WENT_SILENT", "severity": "amber", "title": "Form went silent",
         "action": "quiet 4 business days", "entity_type": "form",
         "entity_name": "Hot Tub Brochure"},
        {"location_id": "locA", "slug": "acme", "name": "Acme Pools"},
        {}, "2026-08-25")
    assert payload["entity_name"] == "Hot Tub Brochure"
