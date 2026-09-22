"""Tests for tools/form_activity.py (per-form activity + page locations) and
lead_channels.py (which channel each form belongs to)."""

from datetime import date, datetime, timezone

from .. import form_history, lead_channels
from .. import main as main_mod
from ..ghl_client import GHLAuthError
from ..tools import form_activity as fa
from .fakes import FakeStore

TODAY = date(2026, 9, 22)
LOC = "locF"
F1, F2, F3, F4, F5, FB = "FormOneAAAAAAAAAAAAA", "FormTwoAAAAAAAAAAAAA", "FormThreeAAAAAAAAAAA", "FormFourAAAAAAAAAAAA", "FormFiveAAAAAAAAAAAA", "FbLeadFormAAAAAAAAAA"
SUB = {"location_id": LOC, "name": "Flohr Pools", "slug": "flohr", "am_name": "Lauren",
       "tag_config": {"website": "https://flohrpools.com/"}}

FORMS = [{"id": F1, "name": "Website / Contact Us"},
         {"id": F2, "name": "Hot Tub - GA Ad"},
         {"id": F3, "name": "Pool Covers - FB Ad"},
         {"id": F4, "name": "Unsubscribe"},
         {"id": F5, "name": "Spring Promo 2025"}]


def _sub(form_id, day, url=None, email="lead@customer.com", **event):
    others = {"eventData": {**event}, "full_name": "Jane Customer", "phone": "+15551234567"}
    if url:
        others["eventData"]["page"] = {"url": url, "title": "Page"}
    return {"id": f"{form_id}-{day}-{email}", "formId": form_id, "createdAt": f"{day}T15:00:00.000Z",
            "email": email, "contactId": "c1", "others": others}


SUBMISSIONS = (
    [_sub(F1, f"2026-09-{d:02d}", "https://flohrpools.com/contact?utm_source=x") for d in (2, 9, 16, 20)]
    + [_sub(F1, "2026-09-21", "https://flohrpools.com/contact?gclid=CLICKID1")]
    + [_sub(F1, "2026-09-22", email="formcheck@smallscreenproducer.com",
            url="https://api.leadconnectorhq.com/widget/form/" + F1)]
    + [_sub(F2, "2026-09-10", "https://promo.flohrpools.com/hot-tub-ga?gclid=CLICKID2"),
       _sub(F2, "2026-08-01", "https://promo.flohrpools.com/hot-tub-ga?gclid=CLICKID3")]
    + [_sub(F4, "2026-07-15", "https://link.flohrpools.com/widget/form/" + F4)]
    + [_sub(F5, "2025-04-02", "https://flohrpools.com/spring")]
    # Facebook lead-ad submissions: a form id GHL's form list doesn't have
    + [_sub(FB, f"2026-09-{d:02d}", source="facebook", medium="facebook lead form")
       for d in (3, 12, 19)]
)


def routes(method, path, params, body):
    if path == "/forms/":
        return {"forms": FORMS if params.get("skip", 0) == 0 else []}
    if path == "/surveys/":
        raise GHLAuthError("GET /surveys/: HTTP 403", 403)
    if path == "/forms/submissions":
        rows = sorted(SUBMISSIONS, key=lambda s: s["createdAt"], reverse=True)
        if params.get("formId"):
            rows = [s for s in rows if s["formId"] == params["formId"]]
        start, end = params.get("startAt"), params.get("endAt")
        if start:
            rows = [s for s in rows if start <= s["createdAt"][:10] < end]
        else:  # GHL's default window: same date last month
            rows = [s for s in rows if s["createdAt"][:10] >= "2026-08-22"]
        if params.get("q"):
            rows = [s for s in rows if s["email"] == params["q"]]
        limit, page = params.get("limit", 20), params.get("page", 1)
        return {"submissions": rows[(page - 1) * limit: page * limit], "meta": {"total": len(rows)}}
    raise AssertionError(f"unrouted {method} {path}")


class Client:
    def __init__(self, token):
        self.requests_made = 0

    def request(self, method, path, params=None, json_body=None, **_kw):
        self.requests_made += 1
        return routes(method, path, params or {}, json_body or {})


def run_book(crawl=False, fetch=None):
    store = FakeStore(subs=[SUB])
    return fa.collect_book(store, [SUB], crawl=crawl, client_factory=Client, fetch=fetch,
                           today=TODAY, log=lambda *_: None)


def rows_by_id(form_rows):
    return {r["form_id"]: r for r in form_rows}


# -- per-form rows ---------------------------------------------------------------

def test_form_rows_carry_real_history_status_and_counts():
    form_rows, _pages, _accounts, failed = run_book()
    assert failed == []
    rows = rows_by_id(form_rows)
    f1 = rows[F1]
    assert f1["last_submission"] == "2026-09-21"            # last night's form check ignored
    assert f1["days_inactive"] == 1 and f1["status"] == "active"
    assert (f1["subs_7d"], f1["subs_30d"], f1["subs_all_time"]) == (3, 5, 5)
    assert f1["live_page_url"] == "https://flohrpools.com/contact"   # query stripped
    assert f1["last_form_check"] == "2026-09-22"
    f5 = rows[F5]
    assert (f5["status"], f5["last_submission"], f5["days_inactive"]) == ("stale", "2025-04-02", 538)
    assert rows[F3]["status"] == "never" and rows[F3]["subs_all_time"] == 0


def test_channels_distinguish_website_google_facebook_and_lead_ads():
    rows = rows_by_id(run_book()[0])
    assert rows[F1]["channel"] == lead_channels.WEBSITE_FORM      # GA traffic, but a site form
    assert "1 of 5 recent submissions from Google ad clicks" in rows[F1]["channel_evidence"]
    assert rows[F2]["channel"] == lead_channels.GOOGLE_AD_FORM
    assert rows[F3]["channel"] == lead_channels.FACEBOOK_AD_FORM  # by name; never submitted
    lead_ad = rows[FB]
    assert lead_ad["kind"] == "unlisted" and lead_ad["channel"] == lead_channels.FACEBOOK_LEAD_AD
    assert lead_ad["subs_90d"] == 3 and lead_ad["datadog_candidate"] == "no"


def test_datadog_candidates_and_utility_forms():
    rows = rows_by_id(run_book()[0])
    assert rows[F1]["datadog_candidate"] == "optional"   # 5 real in 30d: traffic proves it
    assert rows[F2]["datadog_candidate"] == "yes"        # live GA page, low volume
    assert rows[F3]["datadog_candidate"] == "no"         # no page ever recorded
    assert rows[F4]["datadog_candidate"] == "no"         # Unsubscribe = utility
    assert rows[F4]["channel"] == lead_channels.STANDALONE_LINK


def test_account_row_answers_the_30_day_question():
    form_rows, _pages, accounts, _failed = run_book()
    acct = accounts[0]
    assert acct["default_window_total"] < acct["all_time_total"]
    assert acct["oldest_submission"] == "2025-04-02" and acct["history_window"] == "all_time"
    assert acct["subs_90d_facebook_lead_ads"] == 3 and acct["subs_90d_google_ads"] == 2
    assert acct["unlisted_form_ids"] == 1
    line = fa.verdict(accounts, TODAY)
    assert "not a retention cap" in line and "2025-04-02" in line


def test_verdict_flags_a_real_cap_if_history_never_goes_past_31_days():
    accounts = [{"account": "A", "oldest_submission": "2026-09-01", "all_time_total": 4,
                 "default_window_total": 4, "history_window": "all_time"}]
    assert "may cap history" in fa.verdict(accounts, TODAY)


def test_csv_holds_no_personal_data():
    form_rows, page_rows, accounts, _ = run_book()
    text = fa.to_csv(form_rows, fa.FORM_COLUMNS) + fa.to_csv(page_rows, fa.PAGE_COLUMNS)
    for leak in ("Jane Customer", "lead@customer.com", "5551234567", "gclid", "CLICKID"):
        assert leak not in text


# -- crawl -----------------------------------------------------------------------

def test_crawl_confirms_pages_and_finds_embeds_without_submissions():
    pages = {
        "https://flohrpools.com/contact": f'<iframe src="https://link.flohrpools.com/widget/form/{F1}">',
        "https://promo.flohrpools.com/hot-tub-ga": "<p>form removed</p>",
        "https://flohrpools.com/robots.txt": "",
        "https://flohrpools.com/sitemap.xml": "<urlset><loc>https://flohrpools.com/covers</loc></urlset>",
        "https://flohrpools.com/covers": f'<iframe src="https://link.flohrpools.com/widget/form/{F3}">',
    }
    rows = rows_by_id(run_book(crawl=True, fetch=lambda url: pages.get(url))[0])
    assert rows[F1]["still_on_page"] == "yes"
    assert rows[F2]["still_on_page"] == "NO"
    assert "no longer found" in rows[F2]["notes"]
    assert rows[F3]["site_embed_pages"] == "https://flohrpools.com/covers"
    assert rows[F3]["datadog_candidate"] == "yes"        # now known to be live


# -- lead_channels unit rules -------------------------------------------------------

def _rec(url, ad=""):
    return {"page_url": url, "ad": ad, "check": False, "source": ""}


def test_channel_rules_placement_and_traffic():
    site = "https://www.client.com"
    assert lead_channels.classify("form", "Quote", [_rec("https://client.com/quote")], site)[
        "channel"] == lead_channels.WEBSITE_FORM
    assert lead_channels.classify("form", "Quote", [_rec("https://promo.client.com/x", "facebook")],
                                  site)["channel"] == lead_channels.FACEBOOK_AD_FORM
    assert lead_channels.classify("form", "Quote", [_rec("https://promo.client.com/x")], site)[
        "channel"] == lead_channels.LANDING_PAGE_FORM
    assert lead_channels.classify("form", "Quote", [], site)["channel"] == lead_channels.UNKNOWN
    unlisted = lead_channels.classify("unlisted", "", [_rec("https://other.com/x")], site,
                                      labels={"Direct traffic": 2})
    assert unlisted["channel"] == lead_channels.UNLISTED_FORM


# -- main.py --form-activity ------------------------------------------------------

def test_main_form_activity_mode_writes_and_prints_reports(tmp_path, capsys):
    store = FakeStore(subs=[SUB])
    code = main_mod.run(["--form-activity", "--report-dir", str(tmp_path)], store=store,
                        client_factory=Client,
                        now_utc=datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc))
    assert code == 0
    out = capsys.readouterr().out
    for name in ("form-activity.csv", "form-pages.csv", "form-accounts.csv"):
        assert f"===== {name} BEGIN" in out and (tmp_path / name).exists()
    assert "30-DAY CHECK" in out
    assert store.snapshots == {}                          # a report, not a collection run


def test_history_module_is_the_single_source_of_truth():
    # The report and the nightly share one projection and one window.
    assert fa.form_history is form_history


def test_reports_leave_out_accounts_not_using_mlh(tmp_path, capsys):
    ads_only = dict(SUB, location_id="locX", name="GA Only Pools", slug="gaonly",
                    mlh_status="ads_only")
    store = FakeStore(subs=[SUB, ads_only])
    code = main_mod.run(["--form-activity", "--report-dir", str(tmp_path)], store=store,
                        client_factory=Client,
                        now_utc=datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc))
    assert code == 0
    accounts = (tmp_path / "form-accounts.csv").read_text()
    assert "Flohr Pools" in accounts and "GA Only Pools" not in accounts
    assert "not using MLH left out" in capsys.readouterr().out
