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
