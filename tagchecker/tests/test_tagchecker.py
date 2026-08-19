"""Unit tests for the tag checker's pure decision logic. No browser, no
network — check_site's Playwright plumbing is exercised only in production;
everything that can be wrong in a subtle way (pattern matching, ID
verification, status collapse) is testable right here."""

from tagchecker.main import evaluate_expectations, status_of

GTM_URL = "https://www.googletagmanager.com/gtm.js?id=GTM-TTM56TD9&l=dataLayer"
GA4_URL = "https://region1.google-analytics.com/g/collect?v=2&tid=G-ABC123&cid=555"
META_URL = "https://www.facebook.com/tr/?id=1234567890&ev=PageView"


def test_fires_on_matching_url_and_id():
    results = evaluate_expectations(
        [{"type": "gtm_container", "id": "GTM-TTM56TD9"}], [GTM_URL])
    assert results == [{"type": "gtm_container", "id": "GTM-TTM56TD9", "fired": True}]


def test_wrong_id_does_not_count():
    # A DIFFERENT GTM container firing must not satisfy this client's check —
    # that is the whole point of ID verification.
    results = evaluate_expectations(
        [{"type": "gtm_container", "id": "GTM-OTHER"}], [GTM_URL])
    assert results[0]["fired"] is False


def test_no_id_means_presence_is_enough():
    results = evaluate_expectations([{"type": "ga4"}], [GA4_URL])
    assert results[0]["fired"] is True


def test_ga4_with_measurement_id():
    assert evaluate_expectations([{"type": "ga4", "id": "G-ABC123"}], [GA4_URL])[0]["fired"] is True
    assert evaluate_expectations([{"type": "ga4", "id": "G-NOPE"}], [GA4_URL])[0]["fired"] is False


def test_meta_pixel_and_unrelated_traffic():
    urls = ["https://example.com/style.css", META_URL, "https://cdn.example.com/app.js"]
    results = evaluate_expectations(
        [{"type": "meta_pixel", "id": "1234567890"}, {"type": "tiktok_pixel"}], urls)
    assert results[0]["fired"] is True
    assert results[1]["fired"] is False


def test_unknown_tag_type_is_surfaced_not_dropped():
    results = evaluate_expectations([{"type": "hotjar"}], [GTM_URL])
    assert results[0]["fired"] is None
    assert results[0]["note"] == "unknown tag type"


def test_status_collapse():
    assert status_of([], "page timed out") == "error"
    assert status_of([{"fired": True}, {"fired": False}], None) == "alert"
    assert status_of([{"fired": True}, {"fired": True}], None) == "ok"
    assert status_of([{"fired": None}], None) == "ok"  # config typo alone isn't an alert
    assert status_of([], None) == "ok"
