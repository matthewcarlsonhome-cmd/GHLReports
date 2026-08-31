"""Tests for tools/form_urls.py — the form→page-URL extraction CLI.

Covers URL normalization, the submissions aggregation (pagination, no-URL
submissions, the safety cap), row building (placed / no_url_data / silent),
the PII boundary (submitted answer fields living next to eventData in
`others` must never surface in the aggregate or the CSV rows), and the
--form-urls mode of collector/main.py that prints the CSV into the run log.
"""

from datetime import datetime, timezone

from .. import main as main_mod
from ..tools.form_urls import (MAX_SUBMISSION_PAGES, PAGE_LIMIT, account_rows,
                               fetch_submission_pages, normalize_url,
                               rows_to_csv_text)
from .fakes import CLIENT_SUB, PARENT_SUB, FakeClient, FakeStore, make_factory


def _submission(form_id, created, url=None, title=None, others_extra=None):
    event = {"domain": "example.com", "source": "Direct traffic", "medium": "form"}
    if url:
        event["page"] = {"url": url, "title": title or ""}
    others = {"eventData": event}
    others.update(others_extra or {})
    return {"id": "s-" + created, "formId": form_id,
            "createdAt": created + "T15:00:00.000Z", "others": others}


def _client(pages):
    """FakeClient whose /forms/submissions returns pages[n-1] for page=n."""
    def routes(method, path, params, body):
        assert (method, path) == ("GET", "/forms/submissions")
        page = params.get("page", 1)
        batch = pages[page - 1] if page <= len(pages) else []
        return {"submissions": batch, "meta": {"total": sum(len(p) for p in pages)}}
    return FakeClient("tok-test", routes)


# -- normalize_url ---------------------------------------------------------

def test_normalize_url_strips_query_and_fragment_and_lowercases_host():
    url = "https://AAApools.COM/Contact-Us/?utm_source=google&gclid=x#form"
    assert normalize_url(url) == "https://aaapools.com/Contact-Us/"


def test_normalize_url_keep_query_keeps_query_but_not_fragment():
    url = "https://a.com/p?step=2#x"
    assert normalize_url(url, keep_query=True) == "https://a.com/p?step=2"


def test_normalize_url_bare_domain_gets_root_path():
    assert normalize_url("https://a.com") == "https://a.com/"


# -- fetch_submission_pages ------------------------------------------------

def test_aggregates_hits_and_last_seen_per_form_and_url():
    client = _client([[
        _submission("f1", "2026-08-10", "https://a.com/contact?utm=x", "Contact"),
        _submission("f1", "2026-08-20", "https://a.com/contact", "Contact"),
        _submission("f1", "2026-08-15", "https://a.com/quote", "Quote"),
        _submission("f2", "2026-08-01"),  # no page URL captured
    ]])
    per_form, complete = fetch_submission_pages(
        client, "loc1", "2026-08-01", "2026-08-31", keep_query=False)
    assert complete
    contact = per_form["f1"]["https://a.com/contact"]
    assert contact == {"title": "Contact", "hits": 2, "last_seen": "2026-08-20"}
    assert per_form["f1"]["https://a.com/quote"]["hits"] == 1
    assert per_form["f2"][""]["hits"] == 1


def test_paginates_until_short_page():
    page1 = [_submission("f1", "2026-08-10", "https://a.com/x")] * PAGE_LIMIT
    page2 = [_submission("f1", "2026-08-11", "https://a.com/x")] * 3
    client = _client([page1, page2])
    per_form, complete = fetch_submission_pages(
        client, "loc1", "2026-08-01", "2026-08-31", keep_query=False)
    assert complete
    assert per_form["f1"]["https://a.com/x"]["hits"] == PAGE_LIMIT + 3
    assert client.requests_made == 2


def test_page_cap_marks_window_truncated():
    full = [_submission("f1", "2026-08-10", "https://a.com/x")] * PAGE_LIMIT
    client = _client([full] * (MAX_SUBMISSION_PAGES + 2))
    _, complete = fetch_submission_pages(
        client, "loc1", "2026-08-01", "2026-08-31", keep_query=False)
    assert not complete
    assert client.requests_made == MAX_SUBMISSION_PAGES


def test_submitted_answers_never_reach_the_aggregate():
    # `others` carries the submitted answers next to eventData; only
    # eventData-derived strings may appear anywhere in the output.
    client = _client([[
        _submission("f1", "2026-08-10", "https://a.com/contact", "Contact",
                    others_extra={"email": "jane@customer.com",
                                  "full_name": "Jane Customer",
                                  "phone": "+15551234567"}),
    ]])
    per_form, _ = fetch_submission_pages(
        client, "loc1", "2026-08-01", "2026-08-31", keep_query=False)
    dumped = repr(per_form)
    for leak in ("jane@customer.com", "Jane Customer", "5551234567"):
        assert leak not in dumped


# -- account_rows ----------------------------------------------------------

SUB = {"name": "AAA Pools", "slug": "aaapools", "location_id": "loc1"}
FORMS = [{"form_id": "f1", "name": "Contact Us"},
         {"form_id": "f2", "name": "API-only intake"},
         {"form_id": "f3", "name": "Dormant form"}]


def _rows():
    per_form = {
        "f1": {"https://a.com/contact": {"title": "Contact", "hits": 5,
                                         "last_seen": "2026-08-20"},
               "https://a.com/quote": {"title": "Quote", "hits": 9,
                                       "last_seen": "2026-08-22"},
               "": {"title": "", "hits": 1, "last_seen": "2026-08-02"}},
        "f2": {"": {"title": "", "hits": 4, "last_seen": "2026-08-19"}},
    }
    return account_rows(SUB, FORMS, per_form, keep_query=False)


def test_placed_rows_sorted_by_hits_with_no_url_row_after():
    f1 = [r for r in _rows() if r["form_id"] == "f1"]
    assert [r["status"] for r in f1] == ["placed", "placed", "no_url_data"]
    assert [r["page_url"] for r in f1[:2]] == [
        "https://a.com/quote", "https://a.com/contact"]
    assert f1[0]["hits"] == 9 and f1[0]["last_seen"] == "2026-08-22"


def test_submissions_without_urls_report_no_url_data_not_silent():
    f2 = [r for r in _rows() if r["form_id"] == "f2"]
    assert [r["status"] for r in f2] == ["no_url_data"]
    assert f2[0]["hits"] == 4


def test_form_with_no_submissions_gets_silent_stub_row():
    f3 = [r for r in _rows() if r["form_id"] == "f3"]
    assert [r["status"] for r in f3] == ["silent"]
    assert f3[0]["page_url"] == "" and f3[0]["hits"] == 0


def test_rows_carry_account_identity_for_the_csv():
    for row in _rows():
        assert row["account"] == "AAA Pools"
        assert row["slug"] == "aaapools"
        assert row["location_id"] == "loc1"


# -- main.py --form-urls mode ----------------------------------------------

def test_main_form_urls_mode_prints_csv_between_markers(capsys):
    submissions = {"submissions": [
        _submission("formA1", "2026-08-15", "https://client.com/pool-quote",
                    "Get a Quote"),
        _submission("formA1", "2026-08-17", "https://client.com/pool-quote",
                    "Get a Quote"),
    ]}
    store = FakeStore(subs=[PARENT_SUB, CLIENT_SUB])
    code = main_mod.run(["--form-urls"], store=store,
                        client_factory=make_factory(
                            forms_override={"locA": submissions}),
                        now_utc=datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc))
    assert code == 0
    out = capsys.readouterr().out
    assert "===== form-urls.csv BEGIN" in out and "form-urls.csv END" in out
    csv_part = out.split("BEGIN (copy the lines between the markers) =====")[1]
    csv_part = csv_part.split("=====")[0]
    lines = [l for l in csv_part.strip().splitlines() if l]
    assert lines[0].startswith("account,slug,location_id,form_id,form_name")
    placed = [l for l in lines if ",placed," in l]
    assert len(placed) == 1
    assert "formA1" in placed[0] and "https://client.com/pool-quote" in placed[0]
    assert placed[0].rstrip().endswith("2026-08-17")  # hits row keeps last_seen
    # The four other locA fixture forms had no submissions in the window.
    assert sum(1 for l in lines if ",silent," in l) == 4
    # No snapshot was written: the mode exits before the collection pipeline.
    assert store.snapshots == {}


def test_rows_to_csv_text_roundtrips_columns():
    text = rows_to_csv_text(_rows())
    lines = text.strip().splitlines()
    assert lines[0] == "account,slug,location_id,form_id,form_name,status,page_url,page_title,hits,last_seen"
    assert len(lines) == 1 + len(_rows())
