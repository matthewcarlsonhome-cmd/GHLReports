"""In-memory doubles for the mock run: FakeClient routes API calls to the JSON
fixtures; FakeStore implements the Store interface without a database.

How this fits in
----------------
The tests run the REAL pipeline — main.run() with all the genuine fetchers,
metrics, gate, flags, and digest code — but with the two edges of the system
swapped out: the GHL API becomes FakeClient and the Supabase database becomes
FakeStore. main.run() was built for exactly this swap: its ``store`` and
``client_factory`` parameters exist so tests can inject these fakes (a
dependency-injection style alternative to pytest's monkeypatch, which would
instead patch the real names in place).

Key ideas to understand this file
---------------------------------
* Test double — any stand-in object used in place of a real dependency.
  These are "fakes": doubles with real working behavior (FakeStore genuinely
  stores and returns data, in dicts instead of Postgres tables), as opposed
  to stubs that return canned values or mocks that just record calls.
* Fixture — a checked-in JSON file under tests/fixtures/ holding a captured,
  realistic API response (contacts_new.json, opportunities.json, ...).
  make_routes() maps each (method, path, params) an HTTP call would use to
  the right fixture, so the fetchers exercise their real parsing logic
  against realistic payload shapes — without network, tokens, or flakiness.
* The fixture world has exactly two locations: "locA", a client account with
  rich data, and "locP", the SSP parent account (invoices, client contact
  conversations). Any other location id gets empty-but-valid responses.
* Scenario knobs: ``empty_locs`` makes a location return successful empty
  reads (testing the G4 dead-source gate), ``forms_override`` swaps the
  forms fixture (the FORM_SILENT flag), and ``deny_by_loc`` makes specific
  path prefixes raise 403s (testing coverage/degradation handling).
* An unrouted request raises AssertionError on purpose: if collector code
  starts calling a new endpoint, a test fails loudly instead of silently
  returning nothing.
"""

from __future__ import annotations

import json
import pathlib

from ..ghl_client import GHLAuthError, GHLError

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    """Read one JSON fixture file from tests/fixtures/ into a dict."""
    return json.loads((FIXTURES / name).read_text())


def make_routes(empty_locs: set[str] | None = None,
                forms_override: dict[str, dict] | None = None):
    """Route (method, path, params, body) to fixture data for locA + locP.
    Locations in `empty_locs` return successful empty reads for contacts,
    conversations, and opportunities (the G4 scenario). `forms_override`
    swaps the forms fixture per location (the FORM_SILENT scenario).

    The returned ``route`` function is the whole fake API: it plays the role
    the HTTP layer plays in production, returning the parsed-JSON body the
    real GHLClient.request would return for the same call.
    """
    empty_locs = empty_locs or set()
    forms_override = forms_override or {}
    contact_convos = load("contact_conversations.json")
    messages = load("messages.json")
    all_opps = load("opportunities.json")["opportunities"]

    def route(method: str, path: str, params: dict, body: dict):
        """Dispatch one fake API call to fixture data (see make_routes)."""
        # The location id can arrive several ways depending on the endpoint:
        # camelCase or snake_case query param, request body, or "altId".
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
        # Contacts search is the trickiest route: the real endpoint is one
        # POST used three different ways, told apart by its body — (a) no
        # filters + ascending sort = the earliest-contact probe, (b) a tags
        # filter = the review-tag fetch, (c) a dateAdded range = the main
        # 42-day contacts fetch.
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
        # Conversations search doubles as both the recent-activity scan (no
        # contactId) and the per-contact lookup used by speed-to-lead and the
        # parent relationship metrics (contactId present).
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
            # messages.json is keyed by conversation id; the odd nested
            # {"messages": {"messages": []}} default mirrors the real API's
            # envelope shape.
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
        # Fail fast on any endpoint the fixtures don't model (see docstring).
        raise AssertionError(f"unrouted request in test: {method} {path}")

    return route


class FakeClient:
    """Stand-in for GHLClient with the same surface the pipeline touches.

    request() dispatches to the routes function instead of doing HTTP, while
    still counting requests_made (so per-location accounting is testable) and
    honoring a `deny` set of path prefixes that raise 403 GHLAuthError — the
    way tests simulate a token missing a permission scope.
    """

    def __init__(self, token: str, routes, deny: frozenset = frozenset()):
        self._token = token
        self.routes = routes
        self.deny = deny
        self.requests_made = 0
        self.rate_limited = 0

    def sanitize(self, text: str) -> str:
        """No-op counterpart of the real client's token-scrubbing helper."""
        return text

    def request(self, method, path, params=None, json_body=None, **_kw):
        """Fake GHLClient.request: count, check deny-list, route to fixtures."""
        self.requests_made += 1
        for prefix in self.deny:
            if path.startswith(prefix):
                raise GHLAuthError(f"{method} {path}: HTTP 403 — denied in test", 403)
        return self.routes(method, path, params or {}, json_body or {})

    def try_request(self, method, path, params=None, json_body=None):
        """Non-raising variant used by probe mode: (status, data, error)."""
        try:
            return 200, self.request(method, path, params=params, json_body=json_body), None
        except GHLError as exc:
            return exc.status, None, str(exc)


def make_factory(empty_locs: set[str] | None = None,
                 deny_by_loc: dict[str, frozenset] | None = None,
                 forms_override: dict[str, dict] | None = None):
    """Build a client factory to pass as main.run(client_factory=...).

    Mirrors how run() constructs one GHLClient per token. FakeStore stores
    the token "tok-<location_id>" for each location, so the factory can peel
    the prefix off to know which location a client is for and attach that
    location's deny-list.
    """
    routes = make_routes(empty_locs=empty_locs, forms_override=forms_override)
    deny_by_loc = deny_by_loc or {}

    def factory(token: str) -> FakeClient:
        loc = token.replace("tok-", "")
        return FakeClient(token, routes, deny=deny_by_loc.get(loc, frozenset()))

    return factory


class FakeStore:
    """Dict-backed implementation of the Store interface (see store.Store).

    Each Postgres table becomes a plain dict keyed the way the real table's
    upsert key works (e.g. snapshots by (location_id, date)), which makes the
    real code's idempotent-upsert behavior hold here too. Constructor args
    seed pre-existing state: subaccount config rows, prior gate-passed
    activity (prev_dead), last week's flag codes (prior_flags), and stored
    tokens (pits, defaulting to "tok-<location_id>" for every sub). After a
    run, tests assert directly on the .snapshots / .flags / .runs dicts.
    """

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
        # Copies, so a test's seed data can't be mutated by the code under test.
        return [dict(s) for s in self.subs]

    def pit_key_ok(self):
        # The fake always accepts the collector key.
        return True

    def get_pit(self, location_id):
        return self.pits.get(location_id)

    def pit_updated_at(self, location_id):
        # Fixed rotation stamp so token-bookkeeping paths are deterministic.
        return "2026-08-01T00:00:00+00:00"

    def set_token_status(self, location_id, status, error=None):
        self.token_status[location_id] = (status, error)

    def update_subaccount(self, location_id, fields):
        self.subaccount_updates.setdefault(location_id, {}).update(fields)
        # Mirror token_status writes so tests can assert on either dict.
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
        # Seeded prior_flags (for "last week") win over flags written this run.
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
        # Same bundle shape as Store.read_portfolio, assembled from the dicts;
        # acks aren't modeled here, so acked_by_loc is always empty.
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


# Canonical subaccount config rows for tests: the SSP parent ("locP") and one
# client account ("locA") — the same two locations the fixture routes model.
# Tests copy and tweak these instead of building rows from scratch.
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
