"""In-memory doubles for the mock run: FakeClient routes API calls to the JSON
fixtures; FakeStore implements the Store interface without a database."""

from __future__ import annotations

import json
import pathlib

from ..ghl_client import GHLAuthError, GHLError

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def make_routes(empty_locs: set[str] | None = None):
    """Route (method, path, params, body) to fixture data for locA + locP.
    Locations in `empty_locs` return successful empty reads for contacts,
    conversations, and opportunities (the G4 scenario)."""
    empty_locs = empty_locs or set()
    contact_convos = load("contact_conversations.json")
    messages = load("messages.json")

    def route(method: str, path: str, params: dict, body: dict):
        loc = (params.get("locationId") or params.get("location_id")
               or body.get("locationId") or params.get("altId"))
        if path.startswith("/locations/"):
            lid = path.rsplit("/", 1)[1]
            return load(f"location_{lid}.json")
        if path == "/users/":
            return load("users.json") if loc == "locA" else {"users": []}
        if path == "/opportunities/pipelines":
            return load("pipelines.json") if loc == "locA" else {"pipelines": []}
        if path == "/opportunities/search":
            if loc in empty_locs or loc != "locA":
                return {"opportunities": []}
            return load("opportunities.json")
        if path == "/contacts/search":
            filters = body.get("filters") or []
            is_tag = bool(filters) and filters[0].get("field") == "tags"
            if loc in empty_locs:
                return {"contacts": []}
            if is_tag:
                return load("contacts_tagged.json") if loc == "locA" else {"contacts": []}
            if loc == "locA":
                return load("contacts_new.json")
            if loc == "locP":
                return load("parent_contacts_new.json")
            return {"contacts": []}
        if path == "/conversations/search":
            contact_id = params.get("contactId")
            if contact_id is not None:
                if loc == "locP" and contact_id == "clientContact1":
                    return load("parent_conversations_client1.json")
                if loc == "locA":
                    return {"conversations": contact_convos.get(contact_id, [])}
                return {"conversations": []}
            if loc in empty_locs:
                return {"conversations": []}
            return load("conversations.json") if loc == "locA" else load("parent_conversations.json")
        if path.startswith("/conversations/") and path.endswith("/messages"):
            conv_id = path.split("/")[2]
            return messages.get(conv_id, {"messages": {"messages": []}})
        if path == "/calendars/":
            return load("calendars.json") if loc == "locA" else load("parent_calendars.json")
        if path == "/calendars/events":
            return load("events_next7.json") if loc == "locA" else load("parent_events.json")
        if path == "/invoices/":
            return load("parent_invoices.json") if params.get("altId") == "locP" else {"invoices": []}
        if path == "/blogs/site/all":
            return load("blog_sites.json") if loc == "locA" else {"data": []}
        if path == "/blogs/posts/all":
            return load("blog_posts.json") if loc == "locA" else {"blogs": []}
        if path.startswith("/social-media-posting/"):
            return load("social_posts.json") if "/locA/" in path else {"results": {"posts": []}}
        if path.endswith("/tasks"):
            return {"tasks": []}
        raise AssertionError(f"unrouted request in test: {method} {path}")

    return route


class FakeClient:
    def __init__(self, token: str, routes, deny: frozenset = frozenset()):
        self._token = token
        self.routes = routes
        self.deny = deny
        self.requests_made = 0
        self.rate_limited = 0

    def sanitize(self, text: str) -> str:
        return text

    def request(self, method, path, params=None, json_body=None, **_kw):
        self.requests_made += 1
        for prefix in self.deny:
            if path.startswith(prefix):
                raise GHLAuthError(f"{method} {path}: HTTP 403 — denied in test", 403)
        return self.routes(method, path, params or {}, json_body or {})

    def try_request(self, method, path, params=None, json_body=None):
        try:
            return 200, self.request(method, path, params=params, json_body=json_body), None
        except GHLError as exc:
            return exc.status, None, str(exc)


def make_factory(empty_locs: set[str] | None = None,
                 deny_by_loc: dict[str, frozenset] | None = None):
    routes = make_routes(empty_locs=empty_locs)
    deny_by_loc = deny_by_loc or {}

    def factory(token: str) -> FakeClient:
        loc = token.replace("tok-", "")
        return FakeClient(token, routes, deny=deny_by_loc.get(loc, frozenset()))

    return factory


class FakeStore:
    def __init__(self, subs, trailing=None, prev_dead=None, pits=None):
        self.subs = subs
        self.trailing = trailing or {}
        self.prev_dead = prev_dead or {}
        self.pits = pits if pits is not None else {
            s["location_id"]: "tok-" + s["location_id"] for s in subs}
        self.snapshots: dict = {}
        self.flags: dict = {}
        self.lead_events: dict = {}
        self.token_status: dict = {}
        self.runs: list = []

    def start_run(self):
        self.runs.append({"status": "running"})
        return len(self.runs)

    def finish_run(self, run_id, status, ok, held, failed, requests_made, rate_limited, error):
        self.runs[run_id - 1] = {
            "status": status, "ok": ok, "held": held, "failed": failed,
            "requests_made": requests_made, "rate_limited": rate_limited, "error": error}

    def load_subaccounts(self, active=True):
        return [dict(s) for s in self.subs]

    def pit_key_ok(self):
        return True

    def get_pit(self, location_id):
        return self.pits.get(location_id)

    def set_token_status(self, location_id, status, error=None):
        self.token_status[location_id] = (status, error)

    def upsert_snapshot(self, row):
        self.snapshots[(row["location_id"], row["snapshot_date"])] = row

    def upsert_lead_events(self, rows):
        for row in rows:
            self.lead_events[(row["location_id"], row["contact_id"])] = row

    def replace_flags(self, location_id, snapshot_date, flags):
        self.flags[(location_id, snapshot_date)] = flags

    def read_trailing(self, location_id, snapshot_date):
        return list(self.trailing.get(location_id, []))

    def read_prev_dead(self, location_id, snapshot_date):
        return list(self.prev_dead.get(location_id, []))

    def todays_snapshots(self, snapshot_date):
        return [
            {"location_id": row["location_id"], "snapshot_date": key[1],
             "gate_passed": row["gate_passed"], "leads_delta_pct": row.get("leads_delta_pct")}
            for key, row in self.snapshots.items() if key[1] == snapshot_date.isoformat()]

    def update_snapshot_peers(self, location_id, snapshot_date, peer_median_delta_pct, peer_n):
        row = self.snapshots.get((location_id, snapshot_date.isoformat()))
        if row is not None:
            row["peer_median_delta_pct"] = peer_median_delta_pct
            row["peer_n"] = peer_n


PARENT_SUB = {
    "location_id": "locP", "name": "Small Screen Producer", "slug": "ssp",
    "vertical": None, "services": [], "am_email": "matthew@smallscreenproducer.com",
    "timezone": "America/Chicago", "ssp_client_contact_id": None, "is_parent": True,
    "active": True, "thresholds": {}, "token_status": "ok", "token_rotated_at": None,
}

CLIENT_SUB = {
    "location_id": "locA", "name": "Pilot One Pools", "slug": "pilot1",
    "vertical": "pool_builder", "services": ["content", "social", "ads"],
    "am_email": "lisa@smallscreenproducer.com", "timezone": "America/Chicago",
    "ssp_client_contact_id": "clientContact1", "is_parent": False,
    "active": True, "thresholds": {}, "token_status": "ok", "token_rotated_at": None,
}
