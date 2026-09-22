"""Tests for form_history.py: the explicit history window (GHL defaults to
the last month when startAt/endAt are omitted), the defensive window
ladder, form-check (synthetic test) separation, and the PII boundary."""

from datetime import date

import pytest

from .. import form_history as fh
from ..ghl_client import GHLAuthError, GHLHttpError

TODAY = date(2026, 9, 22)
CHECK = "formcheck@smallscreenproducer.com"


class RecordingClient:
    """Fake client: answers via `handler(params)` and records every call."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    def request(self, method, path, params=None, json_body=None, **_kw):
        params = dict(params or {})
        self.calls.append({"path": path, **params})
        return self.handler(params)


def sub(day, email="lead@customer.com", url="https://client.com/contact", **event):
    return {"id": f"s-{day}-{email}", "formId": "f1", "contactId": f"c-{email}",
            "createdAt": f"{day}T15:00:00.000Z", "email": email, "name": "Jane Customer",
            "others": {"eventData": {"page": {"url": url, "title": "Contact"}, **event},
                       "full_name": "Jane Customer", "phone": "+15551234567"}}


def page_handler(subs, q_total=None, ignore_q=False):
    """Serve `subs` newest-first in pages; q= filters to form checks."""
    def handler(params):
        rows = sorted(subs, key=lambda s: s["createdAt"], reverse=True)
        if params.get("q") and not ignore_q:
            rows = [s for s in rows if s["email"] == params["q"]]
            total = len(rows) if q_total is None else q_total
        else:
            total = len(rows)
        limit, page = params.get("limit", 20), params.get("page", 1)
        return {"submissions": rows[(page - 1) * limit: page * limit], "meta": {"total": total}}
    return handler


# -- the window ----------------------------------------------------------------

def test_every_history_call_sends_explicit_all_time_dates():
    client = RecordingClient(page_handler([sub("2025-04-02")]))
    window = fh.SubmissionWindow(TODAY)
    hist = fh.fetch_history(client, window, "/forms/submissions", "formId", "loc1", "f1",
                            check_email=CHECK)
    assert client.calls[0]["startAt"] == fh.ALL_TIME_START
    assert client.calls[0]["endAt"] == "2026-09-23"      # tomorrow: today always included
    # a submission 173 days old is visible - the old 30-day default hid it
    assert hist["last_at"].startswith("2025-04-02") and hist["total"] == 1
    assert hist["window"] == "all_time"


def test_window_steps_down_once_when_ghl_rejects_wide_ranges():
    def handler(params):
        if params.get("startAt") == fh.ALL_TIME_START:
            raise GHLHttpError("GET /forms/submissions: HTTP 422: range too large", 422)
        return {"submissions": [], "meta": {"total": 0}}
    client = RecordingClient(handler)
    window = fh.SubmissionWindow(TODAY)
    window.request(client, "/forms/submissions", {"locationId": "loc1"})
    assert window.label == "365d" and window.limited
    window.request(client, "/forms/submissions", {"locationId": "loc1"})
    # the second call goes straight to the accepted rung: no wasted retry
    assert [c.get("startAt") for c in client.calls] == [
        fh.ALL_TIME_START, "2025-09-22", "2025-09-22"]


def test_non_window_400_restores_the_rung_and_raises():
    def handler(params):
        raise GHLHttpError("GET /forms/submissions: HTTP 400: bad formId", 400)
    client = RecordingClient(handler)
    window = fh.SubmissionWindow(TODAY)
    with pytest.raises(GHLHttpError):
        window.request(client, "/forms/submissions", {"locationId": "loc1"})
    assert window.label == "all_time"                      # not narrowed for nothing
    assert len(client.calls) == 3                           # tried every rung once


def test_auth_errors_propagate_without_touching_the_window():
    def handler(params):
        raise GHLAuthError("GET /forms/submissions: HTTP 403", 403)
    window = fh.SubmissionWindow(TODAY)
    with pytest.raises(GHLAuthError):
        window.request(RecordingClient(handler), "/forms/submissions", {})
    assert window.label == "all_time"


# -- form checks (synthetic tests) are not real activity ----------------------

def test_form_checks_never_count_as_real_activity():
    subs = [sub("2026-09-21", email=CHECK), sub("2026-09-14", email=CHECK),
            sub("2026-06-01"), sub("2026-05-20")]
    hist = fh.fetch_history(RecordingClient(page_handler(subs)), fh.SubmissionWindow(TODAY),
                            "/forms/submissions", "formId", "loc1", "f1", check_email=CHECK)
    assert hist["last_at"].startswith("2026-06-01")          # not last night's test
    assert hist["total"] == 2 and hist["raw_total"] == 4 and hist["check_total"] == 2
    assert hist["last_check_at"].startswith("2026-09-21")
    assert hist["last_check_contact_id"] == f"c-{CHECK}"


def test_page_of_only_form_checks_reads_deeper_for_the_real_submission():
    checks = [sub(f"2026-09-{d:02d}", email=CHECK) for d in range(1, 21)]  # 20 weekly-ish tests
    subs = checks + [sub("2025-11-03")]
    client = RecordingClient(page_handler(subs))
    hist = fh.fetch_history(client, fh.SubmissionWindow(TODAY), "/forms/submissions",
                            "formId", "loc1", "f1", page_size=20, check_email=CHECK)
    assert hist["last_at"].startswith("2025-11-03")
    assert hist["total"] == 1
    assert [c.get("page") for c in client.calls[:2]] == [1, 2]


def test_q_count_is_trusted_only_when_consistent():
    # 25 real + 5 checks; page of 20 holds 3 checks + 17 real, so the rest
    # of the checks sit beyond the page and need the q count.
    real = [sub(f"2026-08-{d:02d}") for d in range(1, 26)]
    checks = [sub(f"2026-09-{d:02d}", email=CHECK) for d in (1, 8, 15)] + \
             [sub("2026-07-01", email=CHECK), sub("2026-07-08", email=CHECK)]
    exact = fh.fetch_history(RecordingClient(page_handler(real + checks)),
                             fh.SubmissionWindow(TODAY), "/forms/submissions", "formId",
                             "loc1", "f1", page_size=20, check_email=CHECK)
    assert exact["check_total"] == 5 and exact["total"] == 25
    # If GHL ignored q, its total would equal everything: rejected, and the
    # in-page count stands as a lower bound instead of zeroing real totals.
    ignored = fh.fetch_history(RecordingClient(page_handler(real + checks, ignore_q=True)),
                               fh.SubmissionWindow(TODAY), "/forms/submissions", "formId",
                               "loc1", "f1", page_size=20, check_email=CHECK)
    assert ignored["check_total"] == 3 and ignored["total"] == 27


def test_form_check_detection_can_be_disabled():
    subs = [sub("2026-09-21", email=CHECK)]
    hist = fh.fetch_history(RecordingClient(page_handler(subs)), fh.SubmissionWindow(TODAY),
                            "/forms/submissions", "formId", "loc1", "f1", check_email="")
    assert hist["total"] == 1 and hist["last_check_at"] is None


def test_safe_variant_reports_unknown_instead_of_zero():
    def handler(params):
        raise GHLHttpError("GET /forms/submissions: HTTP 500", 500)
    hist = fh.fetch_history_safe(RecordingClient(handler), fh.SubmissionWindow(TODAY),
                                 "/forms/submissions", "formId", "loc1", "f1")
    assert hist["total"] is None and hist["last_at"] is None


# -- projection: PII boundary and ad-click signals -----------------------------

def test_projection_keeps_no_personal_data():
    rec = fh.project(sub("2026-09-01", url="https://client.com/contact?email=jane@customer.com"),
                     CHECK)
    dumped = repr(rec)
    for leak in ("jane@customer.com", "Jane Customer", "5551234567", "lead@customer.com"):
        assert leak not in dumped
    assert rec["page_url"] == "https://client.com/contact"
    assert rec["contact_id"] is None      # only form checks keep their (test) contact id


@pytest.mark.parametrize("url,event,expected", [
    ("https://c.com/p?gclid=abc", {}, "google"),
    ("https://c.com/p?gbraid=x", {}, "google"),
    ("https://c.com/p?utm_source=google&utm_medium=cpc", {}, "google"),
    ("https://c.com/p?utm_source=google&utm_medium=organic", {}, ""),
    ("https://c.com/p?fbclid=abc", {}, "facebook"),
    ("https://c.com/p?utm_source=ig", {}, "facebook"),
    ("https://c.com/p", {"fbc": "fb.1.123.abc"}, "facebook"),
    ("https://c.com/p", {"fbp": "fb.1.123.456"}, ""),       # pixel id alone is not an ad click
    ("https://c.com/p", {}, ""),
])
def test_ad_click_labels(url, event, expected):
    assert fh.ad_click(url, event) == expected


def test_projection_never_keeps_click_ids():
    rec = fh.project(sub("2026-09-01", url="https://c.com/p?gclid=SECRET123", fbc="fb.1.SECRET"),
                     CHECK)
    assert "SECRET" not in repr(rec) and rec["ad"] == "google"
