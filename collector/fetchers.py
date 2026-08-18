"""One fetch function per endpoint in spec section 3.

Every function records into a Coverage instance and never raises past its
boundary: an auth failure or HTTP error becomes a coverage entry and an
empty result. Parsing is tolerant everywhere — `obj.get("a") or obj.get("b")`,
never `obj["a"]` — because field names are VERIFY until --probe confirms them.
"""

from __future__ import annotations

from .ghl_client import GHLClient, GHLError

PAGE_LIMIT = 100
BLOG_PAGE_LIMIT = 50


class Coverage:
    """Per-source scan bookkeeping: {retrieved, exhausted, error, note}.

    Status per source:
      complete    — scan exhausted, no error
      partial     — something retrieved but scan not exhausted, or error mid-scan
      unavailable — error with nothing retrieved
      skipped     — source intentionally not fetched (not in services, no
                    client contact id); excluded from gate counts
    """

    def __init__(self):
        self.sources: dict[str, dict] = {}

    def record(self, source: str, retrieved: int = 0, exhausted: bool = True,
               error: str | None = None, note: str | None = None, skipped: bool = False) -> None:
        self.sources[source] = {
            "retrieved": retrieved,
            "exhausted": bool(exhausted),
            "error": error,
            "note": note,
            "skipped": bool(skipped),
        }

    def add_note(self, source: str, note: str) -> None:
        entry = self.sources.setdefault(
            source, {"retrieved": 0, "exhausted": True, "error": None, "note": None, "skipped": False}
        )
        entry["note"] = f"{entry['note']}; {note}" if entry.get("note") else note

    def status(self, source: str) -> str:
        entry = self.sources.get(source)
        if entry is None:
            return "missing"
        if entry.get("skipped"):
            return "skipped"
        if entry.get("error") and not entry.get("retrieved"):
            return "unavailable"
        if entry.get("error") or not entry.get("exhausted"):
            return "partial"
        return "complete"

    def counts(self) -> dict[str, int]:
        out = {"complete": 0, "partial": 0, "unavailable": 0, "skipped": 0}
        for source in self.sources:
            out[self.status(source)] += 1
        return out

    def unavailable_count(self) -> int:
        return self.counts()["unavailable"]

    def partial_count(self) -> int:
        return self.counts()["partial"]

    def overall(self) -> str:
        c = self.counts()
        if c["unavailable"] or c["partial"]:
            return "partial"
        return "complete"

    def to_dict(self) -> dict:
        return {
            "sources": {name: {**entry, "status": self.status(name)} for name, entry in self.sources.items()},
            "summary": self.counts(),
        }


def _first_list(data: dict | list | None, *keys: str) -> list:
    """Pull the first list found under any of `keys`, or the object itself if
    it is already a list. Tolerates one level of nesting (e.g. results.posts)."""
    if data is None:
        return []
    if isinstance(data, list):
        return data
    for key in keys:
        val = data.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            for sub in keys:
                inner = val.get(sub)
                if isinstance(inner, list):
                    return inner
    return []


def _get_id(obj: dict) -> str | None:
    val = obj.get("id") or obj.get("_id")
    return str(val) if val is not None else None


# -- location / users / pipelines ---------------------------------------


def fetch_location(client: GHLClient, cov: Coverage, location_id: str) -> dict | None:
    try:
        data = client.request("GET", f"/locations/{location_id}")
    except GHLError as exc:
        cov.record("location", error=str(exc))
        return None
    loc = data.get("location") if isinstance(data.get("location"), dict) else data
    cov.record("location", retrieved=1 if loc else 0,
               error=None if loc else "empty response")
    return loc or None


def fetch_users(client: GHLClient, cov: Coverage, location_id: str) -> list[dict]:
    try:
        data = client.request("GET", "/users/", params={"locationId": location_id})
    except GHLError as exc:
        cov.record("users", error=str(exc))
        return []
    users = _first_list(data, "users", "data")
    cov.record("users", retrieved=len(users))
    return users


def users_map(users: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for u in users:
        uid = _get_id(u)
        if not uid:
            continue
        name = u.get("name") or " ".join(
            p for p in [u.get("firstName"), u.get("lastName")] if p
        ) or u.get("email") or uid
        out[uid] = str(name)
    return out


def fetch_pipelines(client: GHLClient, cov: Coverage, location_id: str) -> list[dict]:
    try:
        data = client.request("GET", "/opportunities/pipelines", params={"locationId": location_id})
    except GHLError as exc:
        cov.record("pipelines", error=str(exc))
        return []
    pipelines = _first_list(data, "pipelines", "data")
    cov.record("pipelines", retrieved=len(pipelines))
    return pipelines


# -- opportunities -------------------------------------------------------


def fetch_opportunities(client: GHLClient, cov: Coverage, location_id: str,
                        max_pages: int | None = None) -> list[dict]:
    out: list[dict] = []
    page = 1
    exhausted = False
    error = None
    note = None
    while True:
        try:
            data = client.request("GET", "/opportunities/search", params={
                "location_id": location_id,
                "limit": PAGE_LIMIT,
                "page": page,
                "status": "all",
                "getTasks": "true",
                "getCalendarEvents": "true",
                "getNotes": "false",
            })
        except GHLError as exc:
            error = str(exc)
            break
        batch = _first_list(data, "opportunities", "data")
        out.extend(batch)
        if len(batch) < PAGE_LIMIT:
            exhausted = True
            break
        if max_pages and page >= max_pages:
            note = f"page cap {max_pages} reached"
            break
        page += 1
    cov.record("opportunities", retrieved=len(out), exhausted=exhausted, error=error, note=note)
    return out


# -- contacts ------------------------------------------------------------


def _contacts_search(client: GHLClient, location_id: str, filters: list[dict],
                     max_pages: int | None, sort_field: str = "dateAdded") -> tuple[list[dict], bool, str | None, str | None]:
    out: list[dict] = []
    page = 1
    exhausted = False
    error = None
    note = None
    while True:
        body = {
            "locationId": location_id,
            "page": page,
            "pageLimit": PAGE_LIMIT,
            "filters": filters,
            "sort": [{"field": sort_field, "direction": "desc"}],
        }
        try:
            data = client.request("POST", "/contacts/search", json_body=body)
        except GHLError as exc:
            error = str(exc)
            break
        batch = _first_list(data, "contacts", "data")
        out.extend(batch)
        if len(batch) < PAGE_LIMIT:
            exhausted = True
            break
        if max_pages and page >= max_pages:
            note = f"page cap {max_pages} reached"
            break
        page += 1
    return out, exhausted, error, note


def fetch_new_contacts(client: GHLClient, cov: Coverage, location_id: str,
                       gte_iso: str, lte_iso: str, max_pages: int | None = None) -> list[dict]:
    filters = [{"field": "dateAdded", "operator": "range", "value": {"gte": gte_iso, "lte": lte_iso}}]
    contacts, exhausted, error, note = _contacts_search(client, location_id, filters, max_pages)
    cov.record("contacts", retrieved=len(contacts), exhausted=exhausted, error=error, note=note)
    return contacts


def fetch_tagged_contacts(client: GHLClient, cov: Coverage, location_id: str,
                          tag: str, max_pages: int | None = None) -> list[dict]:
    filters = [{"field": "tags", "operator": "eq", "value": tag}]
    contacts, exhausted, error, note = _contacts_search(
        client, location_id, filters, max_pages, sort_field="dateUpdated")
    cov.record("tagged_contacts", retrieved=len(contacts), exhausted=exhausted, error=error, note=note)
    return contacts


# -- conversations / messages -------------------------------------------


def fetch_recent_conversations(client: GHLClient, cov: Coverage, location_id: str,
                               since_ms: int, max_pages: int | None = None) -> list[dict]:
    """Newest-first scan, stopping once lastMessageDate falls before since_ms."""
    out: list[dict] = []
    exhausted = False
    error = None
    note = None
    start_after = None
    page = 0
    while True:
        params = {
            "locationId": location_id,
            "limit": PAGE_LIMIT,
            "sortBy": "last_message_date",
            "sort": "desc",
        }
        if start_after is not None:
            params["startAfterDate"] = start_after
        try:
            data = client.request("GET", "/conversations/search", params=params)
        except GHLError as exc:
            error = str(exc)
            break
        batch = _first_list(data, "conversations", "data")
        page += 1
        if not batch:
            exhausted = True
            break
        stop = False
        for convo in batch:
            last = convo.get("lastMessageDate")
            out.append(convo)
            if isinstance(last, (int, float)) and last < since_ms:
                stop = True
        if stop:
            exhausted = True
            break
        if len(batch) < PAGE_LIMIT:
            exhausted = True
            break
        if max_pages and page >= max_pages:
            note = f"page cap {max_pages} reached"
            break
        start_after = batch[-1].get("lastMessageDate")
        if start_after is None:
            note = "pagination cursor missing; stopped early"
            break
    cov.record("conversations", retrieved=len(out), exhausted=exhausted, error=error, note=note)
    return out


def fetch_conversations_for_contact(client: GHLClient, location_id: str, contact_id: str) -> list[dict]:
    """Used inside the speed-to-lead loop; caller aggregates coverage."""
    data = client.request("GET", "/conversations/search",
                          params={"locationId": location_id, "contactId": contact_id, "limit": PAGE_LIMIT})
    return _first_list(data, "conversations", "data")


def fetch_messages(client: GHLClient, conversation_id: str, max_pages: int = 3) -> list[dict]:
    """Handles the documented nested `messages.messages` envelope and flat lists."""
    out: list[dict] = []
    page_token = None
    for _ in range(max_pages):
        params: dict = {"limit": PAGE_LIMIT}
        if page_token:
            params["lastMessageId"] = page_token
        data = client.request("GET", f"/conversations/{conversation_id}/messages", params=params)
        envelope = data.get("messages")
        if isinstance(envelope, dict):
            batch = _first_list(envelope, "messages", "data")
            next_page = envelope.get("nextPage")
            page_token = envelope.get("lastMessageId")
        else:
            batch = _first_list(data, "messages", "data")
            next_page = data.get("nextPage")
            page_token = data.get("lastMessageId")
        out.extend(batch)
        if not next_page or not page_token or len(batch) == 0:
            break
    return out


# -- calendars / events --------------------------------------------------


def fetch_calendars(client: GHLClient, cov: Coverage, location_id: str) -> list[dict]:
    try:
        data = client.request("GET", "/calendars/", params={"locationId": location_id})
    except GHLError as exc:
        cov.record("calendars", error=str(exc))
        return []
    calendars = _first_list(data, "calendars", "data")
    cov.record("calendars", retrieved=len(calendars))
    return calendars


def fetch_calendar_events(client: GHLClient, cov: Coverage, location_id: str,
                          calendars: list[dict], start_ms: int, end_ms: int,
                          source: str = "calendar_events") -> list[dict]:
    out: list[dict] = []
    errors: list[str] = []
    for cal in calendars:
        cal_id = _get_id(cal)
        if not cal_id:
            continue
        try:
            data = client.request("GET", "/calendars/events", params={
                "locationId": location_id,
                "calendarId": cal_id,
                "startTime": start_ms,
                "endTime": end_ms,
            })
        except GHLError as exc:
            errors.append(str(exc))
            continue
        out.extend(_first_list(data, "events", "data"))
    error = "; ".join(errors[:3]) if errors else None
    cov.record(source, retrieved=len(out), exhausted=not errors, error=error,
               note=None if not errors else f"{len(errors)}/{len(calendars)} calendars failed")
    return out


# -- invoices ------------------------------------------------------------


def fetch_invoices(client: GHLClient, cov: Coverage, location_id: str,
                   max_pages: int | None = None) -> list[dict]:
    out: list[dict] = []
    offset = 0
    exhausted = False
    error = None
    note = None
    page = 0
    while True:
        try:
            data = client.request("GET", "/invoices/", params={
                "altId": location_id,
                "altType": "location",
                "limit": PAGE_LIMIT,
                "offset": offset,
            })
        except GHLError as exc:
            error = str(exc)
            break
        batch = _first_list(data, "invoices", "data")
        out.extend(batch)
        page += 1
        if len(batch) < PAGE_LIMIT:
            exhausted = True
            break
        if max_pages and page >= max_pages:
            note = f"page cap {max_pages} reached"
            break
        offset += PAGE_LIMIT
    cov.record("invoices", retrieved=len(out), exhausted=exhausted, error=error, note=note)
    return out


# -- blogs / social ------------------------------------------------------


def fetch_blog_sites(client: GHLClient, cov: Coverage, location_id: str) -> list[dict]:
    try:
        data = client.request("GET", "/blogs/site/all",
                              params={"locationId": location_id, "limit": BLOG_PAGE_LIMIT, "skip": 0})
    except GHLError as exc:
        cov.record("blogs", error=str(exc))
        return []
    return _first_list(data, "data", "sites", "blogs")


def fetch_blog_posts(client: GHLClient, cov: Coverage, location_id: str,
                     sites: list[dict], max_pages: int | None = None) -> list[dict]:
    out: list[dict] = []
    errors: list[str] = []
    for site in sites:
        blog_id = _get_id(site)
        if not blog_id:
            continue
        offset = 0
        page = 0
        while True:
            try:
                data = client.request("GET", "/blogs/posts/all", params={
                    "locationId": location_id,
                    "blogId": blog_id,
                    "limit": BLOG_PAGE_LIMIT,
                    "offset": offset,
                    "status": "PUBLISHED",
                })
            except GHLError as exc:
                errors.append(str(exc))
                break
            batch = _first_list(data, "blogs", "posts", "data")
            out.extend(batch)
            page += 1
            if len(batch) < BLOG_PAGE_LIMIT or (max_pages and page >= max_pages):
                break
            offset += BLOG_PAGE_LIMIT
    error = "; ".join(errors[:3]) if errors else None
    cov.record("blogs", retrieved=len(out), exhausted=not errors, error=error,
               note=None if sites else "no blog sites found")
    return out


def fetch_social_posts(client: GHLClient, cov: Coverage, location_id: str,
                       from_iso: str, to_iso: str, max_pages: int | None = None) -> list[dict]:
    out: list[dict] = []
    skip = 0
    exhausted = False
    error = None
    note = None
    page = 0
    while True:
        body = {
            "type": "all",
            "skip": skip,
            "limit": BLOG_PAGE_LIMIT,
            "fromDate": from_iso,
            "toDate": to_iso,
            "includeUsers": False,
        }
        try:
            data = client.request("POST", f"/social-media-posting/{location_id}/posts/list", json_body=body)
        except GHLError as exc:
            error = str(exc)
            break
        batch = _first_list(data, "results", "posts", "data")
        out.extend(batch)
        page += 1
        if len(batch) < BLOG_PAGE_LIMIT:
            exhausted = True
            break
        if max_pages and page >= max_pages:
            note = f"page cap {max_pages} reached"
            break
        skip += BLOG_PAGE_LIMIT
    cov.record("social", retrieved=len(out), exhausted=exhausted, error=error, note=note)
    return out


# -- contact tasks fallback ---------------------------------------------


def fetch_contact_tasks(client: GHLClient, contact_id: str) -> list[dict]:
    data = client.request("GET", f"/contacts/{contact_id}/tasks")
    return _first_list(data, "tasks", "data")
