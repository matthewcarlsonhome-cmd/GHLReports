"""Form and survey submission history: which date window we ask GHL for,
and which submissions count as real customer activity.

How this fits in
----------------
Both the nightly form inventory (fetchers.fetch_form_inventory /
fetch_surveys) and the on-demand report (tools/form_activity.py) read a
form's history through fetch_history() here, so "last submission" and
"days inactive" mean the same thing everywhere.

Key ideas to understand this file
---------------------------------
* The 30-day ceiling is a DEFAULT, not a retention limit. GHL's published
  API spec (github.com/GoHighLevel/highlevel-api-docs, apps/forms.json and
  apps/surveys.json) says of GET /forms/submissions: startAt "By default it
  will be same date of last month", endAt "By default it will be current
  date". A call that omits both sees roughly the last month only, and
  meta.total counts only that month. That is why earlier snapshots showed
  every last-submission date stopping at ~30 days and totals shrinking as
  old submissions aged out. SubmissionWindow always sends explicit dates.
* Defensive window ladder: GHL documents no maximum range, but if it ever
  rejects a wide one (HTTP 400/422) SubmissionWindow steps down to 365
  days, then to the API default, once per location, and reports which
  window was used so a narrowed result is visible, never silent.
* Newest first: GHL returns submissions newest-first (verified against the
  live form_health data: the busiest forms' newest dates were 0-2 days old,
  which an oldest-first order would make impossible). We still sort each
  page by createdAt so nothing here depends on that ordering.
* Form checks (synthetic tests): submissions made by the weekly monitoring
  test carry one reserved email address (FORM_CHECK_EMAIL). They are kept
  apart from real activity: a weekly test must never make a dead form look
  alive. docs/FORM-MONITORING.md explains the whole loop.
* PII boundary: a submission carries the person's name, email, phone and
  answers. project() keeps only the timestamp, the page the form was on,
  and whether it was a form check (plus that check's contact id, which is
  our own test contact). The email is read only to compare it with
  FORM_CHECK_EMAIL and is never returned.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .ghl_client import GHLAuthError, GHLError

# Explicit start of "all time". GHL launched in 2018, so no location can
# hold older submissions.
ALL_TIME_START = "2018-01-01"
DEFAULT_FORM_CHECK_EMAIL = "formcheck@smallscreenproducer.com"
DEEP_PAGE_CAP = 5          # extra pages read when the newest page is all form checks


def form_check_email() -> str:
    """The reserved address the weekly form-check tests submit with
    (FORM_CHECK_EMAIL env var; blank disables form-check detection)."""
    return os.environ.get("FORM_CHECK_EMAIL", DEFAULT_FORM_CHECK_EMAIL).strip().lower()


def normalize_url(url: str, keep_query: bool = False) -> str:
    """scheme://host/path, lowercased host, no fragment; query optional.
    Query strings are dropped by default: they carry utm/gclid noise and,
    on prefilled links, sometimes a visitor's own details."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path or "/", parts.query if keep_query else "", ""))


def _first_list(data, *keys) -> list:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in keys:
        val = data.get(key)
        if isinstance(val, list):
            return val
    return []


def meta_total(data) -> int | None:
    """meta.total (the filtered count GHL reports), or None when absent."""
    if not isinstance(data, dict):
        return None
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    total = meta.get("total", data.get("total", data.get("count")))
    try:
        return int(total) if total is not None else None
    except (TypeError, ValueError):
        return None


class SubmissionWindow:
    """Which date range GHL accepts for one location, remembered per run.

    Rungs, widest first: all_time (ALL_TIME_START..tomorrow), 365d, then
    default (no dates: GHL's ~30-day default). A 400/422 on a wider rung
    steps down only if the narrower rung then SUCCEEDS; if it fails the
    same way the problem was not the window, so the rung is restored and
    the error raised. endAt is tomorrow so today's submissions are included
    whatever timezone GHL applies to the date.
    """

    RUNGS = ("all_time", "365d", "default")

    def __init__(self, today: date):
        self.today = today
        self.step = 0

    @property
    def label(self) -> str:
        return self.RUNGS[self.step]

    @property
    def limited(self) -> bool:
        """True when history is narrower than all-time for this location."""
        return self.step > 0

    def params(self, step: int | None = None) -> dict:
        rung = self.RUNGS[self.step if step is None else step]
        end = (self.today + timedelta(days=1)).isoformat()
        if rung == "all_time":
            return {"startAt": ALL_TIME_START, "endAt": end}
        if rung == "365d":
            return {"startAt": (self.today - timedelta(days=365)).isoformat(), "endAt": end}
        return {}

    def request(self, client, path: str, params: dict) -> dict:
        try:
            return client.request("GET", path, params={**params, **self.params()})
        except GHLAuthError:
            raise
        except GHLError as exc:
            if exc.status not in (400, 422) or self.step >= len(self.RUNGS) - 1:
                raise
            first_error = exc
        for step in range(self.step + 1, len(self.RUNGS)):
            try:
                data = client.request("GET", path, params={**params, **self.params(step)})
            except GHLAuthError:
                raise
            except GHLError as exc:
                if exc.status in (400, 422):
                    continue
                raise
            self.step = step
            return data
        raise first_error


GOOGLE_CLICK_KEYS = ("gclid", "gbraid", "wbraid", "gad_source")
FACEBOOK_UTM = ("facebook", "fb", "ig", "instagram", "meta")
PAID_MEDIUM = ("cpc", "ppc", "paid", "paidsearch", "paid_search", "paid-social", "paid_social",
               "paidsocial", "ads", "ad")


def ad_click(raw_url: str, event: dict) -> str:
    """'google' / 'facebook' when the submission carries an ad-click marker
    (click ids or paid utm tags on the page URL, or Facebook's fbc click
    cookie), else ''. Returns a label only; the ids themselves are never
    kept."""
    try:
        query = {k.lower(): [v.lower() for v in vals]
                 for k, vals in parse_qs(urlsplit(raw_url).query).items()}
    except ValueError:
        query = {}
    source = (query.get("utm_source") or [""])[0]
    medium = (query.get("utm_medium") or [""])[0]
    if any(k in query for k in GOOGLE_CLICK_KEYS) or (source == "google" and medium in PAID_MEDIUM):
        return "google"
    if "fbclid" in query or source in FACEBOOK_UTM or event.get("fbc"):
        return "facebook"
    return ""


def project(sub: dict, check_email: str) -> dict:
    """PII-free view of one submission: when, which page, how the visitor
    arrived, form check or not. The contact id is kept only for form checks
    (our own test contact)."""
    email = str(sub.get("email") or "").strip().lower()
    is_check = bool(check_email) and email == check_email
    event = (sub.get("others") or {}).get("eventData") or {}
    if not isinstance(event, dict):
        event = {}
    page = event.get("page") if isinstance(event.get("page"), dict) else {}
    raw_url = str(page.get("url") or "")
    return {
        "at": str(sub.get("createdAt") or ""),
        "page_url": normalize_url(raw_url) if raw_url.startswith("http") else "",
        "page_title": str(page.get("title") or ""),
        "ad": ad_click(raw_url, event),
        # GHL's own attribution label ("Paid Search", "Direct traffic", ...)
        "source": str(event.get("source") or "").strip(),
        "check": is_check,
        "contact_id": sub.get("contactId") if is_check else None,
    }


def fetch_history(client, window: SubmissionWindow, path: str, id_param: str,
                  location_id: str, item_id: str, *, page_size: int = 20,
                  check_email: str | None = None) -> dict:
    """History of one form/survey from its newest `page_size` submissions.

    Returns a PII-free summary:
      total        real submissions in the window (form checks excluded),
                   None if it could not be determined
      raw_total    GHL's meta.total, form checks included
      last_at      newest REAL submission (ISO string) or None
      last_page_url  page the newest real submission came from ("" if none)
      check_total  form-check submissions in the window (a lower bound
                   when GHL's q filter cannot confirm the full count)
      last_check_at / last_check_contact_id  newest form check, if any
      records      projected submissions read (newest first) - page URLs
                   for the report; never names, emails, phones or answers
      covers_all   True when `records` holds the entire window
      window       which SubmissionWindow rung produced this

    Raises GHLError like client.request; fetch_history_safe() wraps it.
    """
    check_email = form_check_email() if check_email is None else check_email
    base = {"locationId": location_id, id_param: item_id, "limit": page_size}
    data = window.request(client, path, {**base, "page": 1})
    raw_total = meta_total(data)
    records = [project(s, check_email)
               for s in _first_list(data, "submissions", "data") if isinstance(s, dict)]
    covers_all = raw_total is not None and len(records) >= raw_total
    if raw_total is None:
        # No meta: a short page is the whole window, a full one is unknown.
        covers_all = len(records) < page_size
        raw_total = len(records) if covers_all else None

    # A page made entirely of form checks hides the newest real submission
    # deeper down; read on (bounded) until one appears.
    page = 1
    while (records and all(r["check"] for r in records) and not covers_all
           and page <= DEEP_PAGE_CAP):
        page += 1
        more = window.request(client, path, {**base, "page": page})
        batch = [project(s, check_email)
                 for s in _first_list(more, "submissions", "data") if isinstance(s, dict)]
        if not batch:
            covers_all = True
            break
        records.extend(batch)
        if raw_total is not None and len(records) >= raw_total:
            covers_all = True
        if len(batch) < page_size:
            covers_all = True

    records.sort(key=lambda r: r["at"], reverse=True)
    checks = [r for r in records if r["check"]]
    real = [r for r in records if not r["check"]]

    check_total = len(checks)
    if checks and not covers_all:
        # Form checks older than the page would otherwise count as real.
        # Count them with GHL's search filter (q matches contact id, name,
        # email or phone), but only trust the answer when it is consistent:
        # the first hit must itself be a form check, and the count must
        # leave room for the real submissions already seen. If q were ever
        # ignored, meta.total would equal raw_total and fail that test; the
        # in-page count then stands as a lower bound.
        check_data = window.request(client, path, {**base, "limit": 1, "page": 1,
                                                   "q": check_email})
        q_total = meta_total(check_data)
        first = [project(s, check_email)
                 for s in _first_list(check_data, "submissions", "data") if isinstance(s, dict)]
        if (q_total is not None and raw_total is not None and real and first
                and first[0]["check"] and len(checks) <= q_total <= raw_total - len(real)):
            check_total = q_total

    total = None if raw_total is None else max(raw_total - check_total, 0)
    return {
        "total": total,
        "raw_total": raw_total,
        "last_at": real[0]["at"] if real else None,
        "last_page_url": real[0]["page_url"] if real else "",
        "check_total": check_total,
        "last_check_at": checks[0]["at"] if checks else None,
        "last_check_contact_id": checks[0]["contact_id"] if checks else None,
        "records": records,
        "covers_all": covers_all,
        "window": window.label,
    }


UNKNOWN_HISTORY = {
    "total": None, "raw_total": None, "last_at": None, "last_page_url": "",
    "check_total": None, "last_check_at": None, "last_check_contact_id": None,
    "records": [], "covers_all": False, "window": None,
}


def fetch_history_safe(client, window: SubmissionWindow, path: str, id_param: str,
                       location_id: str, item_id: str, **kwargs) -> dict:
    """fetch_history, but any API failure becomes the 'unknown' summary
    (total None), which classifies as 'unknown', never as a fake zero."""
    try:
        return fetch_history(client, window, path, id_param, location_id, item_id, **kwargs)
    except GHLError:
        return dict(UNKNOWN_HISTORY, window=window.label)
