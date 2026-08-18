"""Monday digest builder (Tier 2): pure template fill, staff recipients only."""

from ..digest import build_digests

SUBS = [
    {"location_id": "locP", "name": "SSP", "slug": "ssp", "is_parent": True,
     "am_email": "matthew@smallscreenproducer.com", "active": True, "token_status": "ok"},
    {"location_id": "l1", "name": "Pilot One Pools", "slug": "p1", "is_parent": False,
     "am_email": "lisa@smallscreenproducer.com", "active": True, "token_status": "ok",
     "mrr": 2500},
    {"location_id": "l2", "name": "Quiet Spas", "slug": "p2", "is_parent": False,
     "am_email": "lisa@smallscreenproducer.com", "active": True, "token_status": "ok",
     "mrr": None},
    {"location_id": "l3", "name": "Dead Token Pools", "slug": "p3", "is_parent": False,
     "am_email": "lauren@smallscreenproducer.com", "active": True, "token_status": "invalid"},
    {"location_id": "l4", "name": "Outsider", "slug": "p4", "is_parent": False,
     "am_email": "someone@gmail.com", "active": True, "token_status": "ok"},
]

SNAPSHOTS = {
    "l1": {"gate_passed": True, "flags_new": ["LEADS_DROP"], "flags_resolved": ["NO_DELIVERY"]},
    "l2": {"gate_passed": True, "flags_new": [], "flags_resolved": []},
    "l4": {"gate_passed": True, "flags_new": [], "flags_resolved": []},
}

FLAGS = {
    "l1": [
        {"code": "LEADS_DROP", "severity": "red", "action": "Leads down 60% vs baseline. Call."},
        {"code": "PAST_DUE", "severity": "amber", "action": "$3,000 past due."},
        {"code": "REVIEW_ASK_GAP", "severity": "info", "action": "2 wins missing review asks."},
    ],
}


def build(acked=None):
    return build_digests(SUBS, SNAPSHOTS, FLAGS, acked or {}, "2026-08-17")


def test_grouping_and_recipient_filtering():
    digests = build()
    assert set(digests) == {"lisa@smallscreenproducer.com", "lauren@smallscreenproducer.com"}
    # the parent and the non-staff AM never receive anything


def test_attention_content_and_subject():
    lisa = build()["lisa@smallscreenproducer.com"]
    assert lisa["subject"] == "Account health — 1 need attention"
    assert "Pilot One Pools" in lisa["text"]
    assert "$2,500" in lisa["text"]                       # MRR at risk
    assert "[RED] Leads down 60%" in lisa["text"]
    assert "new: LEADS_DROP" in lisa["text"]
    assert "resolved: NO_DELIVERY" in lisa["text"]
    assert "Steady (1): Quiet Spas" in lisa["text"]
    assert "REVIEW_ASK_GAP" not in lisa["text"]           # info flags stay out of the digest


def test_no_data_accounts_are_called_out():
    lauren = build()["lauren@smallscreenproducer.com"]
    assert lauren["subject"] == "Account health — all steady"
    assert "Dead Token Pools: no data" in lauren["text"]


def test_acked_flags_drop_out():
    digests = build(acked={"l1": {"LEADS_DROP", "PAST_DUE"}})
    lisa = digests["lisa@smallscreenproducer.com"]
    assert lisa["subject"] == "Account health — all steady"
    assert "Steady (2)" in lisa["text"]
