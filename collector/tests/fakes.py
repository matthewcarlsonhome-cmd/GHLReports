"""In-memory doubles for the mock run: FakeClient routes API calls to the JSON
fixtures; FakeStore implements the Store interface without a database."""

from __future__ import annotations

import json
import pathlib

from ..ghl_client import GHLAuthError, GHLError

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def make_routes(empty_locs: set[str] | None = None,
                forms_override: dict[str, dict] | None = None):
    """Route (method, path, params, body) to fixture data for locA + locP.
    Locations in `empty_locs` return successful empty reads for contacts,
    conversations, and opportunities (the G4 scenario). `forms_override`
    swaps the forms fixture per location (the FORM_SILENT scenario)."""
    empty_locs = empty_locs or set()
    forms_override = forms_override or {}
    contact_convos = load("contact_conversations.json")
    messages = load("messages.json")
    all_opps = load("opportunities.json")["opportunities"]

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
            status = params.get("status")
            return {"opportunities": [o for o in all_opps
                                      if status in (None, "all") or o.get("status") == status]}
        if path == "/contacts/search":
            filters = body.get("filters") or []
            first_filter = filters[0] if filters else {}
            sort = (body.get("sort") or [{}])[0]
            if not filters and sort.get("direction") == "asc":
                # earliest-contact probe
                if loc == "locA" and loc not in empty_locs:
                    return load("contact_earliest.json")
                if loc == "locP":
                    return load("parent_contacts_new.json")
                return {"contacts": []}
            if loc in empty_locs:
                return {"contacts": []}
            if first_filter.get("field") == "tags":
                return load("contacts_tagged.json") if loc == "locA" else {"contacts": []}
            if loc == "locA":
                return load("contacts_new.json")
            if loc == "locP":
                return load("parent_contacts_new.json")
            return {"contacts": []}
        if path == "/forms/submissions":
            if loc in forms_override:
                return forms_override[loc]
            if loc in empty_locs:
                return {"submissions": []}
            return load("form_submissions.json") if loc == "locA" else {"submissions": []}
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
        if path.endswith("/posts/list") and path.startswith("/social-media-posting/"):
            return load("social_posts.json") if "/locA/" in path else {"results": {"posts": []}}
        if path.endswith("/accounts") and path.startswith("/social-media-posting/"):
            return load("social_accounts.json") if "/locA/" in path else {"results": {"accounts": []}}
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
                 deny_by_loc: dict[str, frozenset] | None = None,
                 forms_override: dict[str, dict] | None = None):
    routes = make_routes(empty_locs=empty_locs, forms_override=forms_override)
    deny_by_loc = deny_by_loc or {}

    def factory(token: str) -> FakeClient:
        loc = token.replace("tok-", "")
        return FakeClient(token, routes, deny=deny_by_loc.get(loc, frozenset()))

    return factory


class FakeStore:
    def __init__(self, subs, prev_dead=None, prior_flags=None, pits=None):
        self.subs = subs
        self.prev_dead = prev_dead or {}
        self.prior_flags = prior_flags or {}   # (location_id, iso_date) -> [codes]
        self.pits = pits if pits is not None else {
            s["location_id"]: "tok-" + s["location_id"] for s in subs}
        self.snapshots: dict = {}
        self.flags: dict = {}
        self.lead_events: dict = {}
        self.lead_history: dict = {}
        self.token_status: dict = {}
        self.subaccount_updates: dict = {}
        self.runs: list = []

    def start_run(self):
        self.runs.append({"status": "running"})
        return len(self.runs)

    def finish_run(self, run_id, status, ok, held, failed, requests_made, rate_limited,
                   error, details=None):
        self.runs[run_id - 1] = {
            "status": status, "ok": ok, "held": held, "failed": failed,
            "requests_made": requests_made, "rate_limited": rate_limited,
            "details": details or {}, "error": error}

    def load_subaccounts(self, active=True):
        return [dict(s) for s in self.subs]

    def pit_key_ok(self):
        return True

    def get_pit(self, location_id):
        return self.pits.get(location_id)

    def pit_updated_at(self, location_id):
        return "2026-08-01T00:00:00+00:00"

    def set_token_status(self, location_id, status, error=None):
        self.token_status[location_id] = (status, error)

    def update_subaccount(self, location_id, fields):
        self.subaccount_updates.setdefault(location_id, {}).update(fields)
        if "token_status" in fields:
            self.token_status[location_id] = (fields["token_status"], fields.get("last_token_error"))

    def upsert_snapshot(self, row):
        self.snapshots[(row["location_id"], row["snapshot_date"])] = row

    def upsert_lead_events(self, rows):
        for row in rows:
            self.lead_events[(row["location_id"], row["contact_id"])] = row

    def upsert_lead_history(self, location_id, rows):
        for row in rows:
            self.lead_history[(location_id, row["week_start"])] = row

    def replace_flags(self, location_id, snapshot_date, flags):
        self.flags[(location_id, snapshot_date)] = flags

    def read_flags(self, location_id, snapshot_date):
        key = (location_id, snapshot_date.isoformat())
        if key in self.prior_flags:
            return list(self.prior_flags[key])
        return [f["code"] for f in self.flags.get(key, [])]

    def read_prev_dead(self, location_id, snapshot_date):
        return list(self.prev_dead.get(location_id, []))

    def update_snapshot_changes(self, location_id, snapshot_date, flags_new, flags_resolved, details):
        row = self.snapshots.get((location_id, snapshot_date.isoformat()))
        if row is not None:
            row["flags_new"] = flags_new
            row["flags_resolved"] = flags_resolved
            row["details"] = details

    def read_portfolio(self, snapshot_date):
        iso = snapshot_date.isoformat()
        snaps = {loc: {"location_id": loc, "gate_passed": row["gate_passed"],
                       "flags_new": row.get("flags_new", []),
                       "flags_resolved": row.get("flags_resolved", [])}
                 for (loc, day), row in self.snapshots.items() if day == iso}
        flags_by_loc = {}
        for (loc, day), flag_list in self.flags.items():
            if day == iso:
                flags_by_loc[loc] = [dict(f) for f in flag_list]
        return {"subs": [dict(s) for s in self.subs], "snapshots_by_loc": snaps,
                "flags_by_loc": flags_by_loc, "acked_by_loc": {}}

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
    "active": True, "thresholds": {}, "mrr": None, "contract_end": None,
    "token_status": "ok", "token_rotated_at": None,
}

CLIENT_SUB = {
    "location_id": "locA", "name": "Pilot One Pools", "slug": "pilot1",
    "vertical": "pool_builder", "services": ["content", "social", "ads"],
    "am_email": "lisa@smallscreenproducer.com", "timezone": "America/Chicago",
    "ssp_client_contact_id": "clientContact1", "is_parent": False,
    "active": True, "thresholds": {}, "mrr": 2500, "contract_end": "2027-01-31",
    "token_status": "ok", "token_rotated_at": None,
}
