"""Tests for tools/find_embeds.py — the client-site embed crawler.

All network goes through the injectable fetch, faked here as a dict of
url -> body. Covers sitemap discovery (robots.txt, index recursion), the
BFS fallback, candidate-first ordering, widget scanning across embed hosts,
and the CSV row statuses (found / not_found / unlisted / native_form_page /
no_website).
"""

from ..tools.find_embeds import (build_rows, candidate_rank, crawl_site,
                                 discover_sitemap_urls, run_crawl, scan_page,
                                 site_base, same_host)

BASE = "https://client.com"


def fake_fetch(pages: dict):
    calls = []

    def fetch(url):
        calls.append(url)
        return pages.get(url)

    fetch.calls = calls
    return fetch


# -- url helpers -----------------------------------------------------------

def test_site_base_normalizes_stored_websites():
    assert site_base("https://learn.aaapools.com/hot-tub-ga") == "https://learn.aaapools.com"
    assert site_base("client.com/") == "https://client.com"
    assert site_base("https://WWW.Client.com/") == "https://www.client.com"


def test_same_host_treats_www_as_equal():
    assert same_host("https://www.client.com/x", BASE)
    assert same_host("https://client.com/x", "https://www.client.com")
    assert not same_host("https://other.com/x", BASE)


def test_candidate_pages_rank_before_deep_blog_posts():
    urls = [BASE + "/category/news/2024/pool-opening-tips-archive",
            BASE + "/contact-us", BASE + "/"]
    ranked = sorted(urls, key=candidate_rank)
    assert ranked[0] == BASE + "/"
    assert ranked[1] == BASE + "/contact-us"


# -- discovery -------------------------------------------------------------

def test_sitemap_from_robots_with_index_recursion():
    fetch = fake_fetch({
        BASE + "/robots.txt": "User-agent: *\nSitemap: https://client.com/sm_index.xml\n",
        BASE + "/sm_index.xml": ("<sitemapindex><sitemap><loc>https://client.com/sm_pages.xml"
                                 "</loc></sitemap></sitemapindex>"),
        BASE + "/sm_pages.xml": ("<urlset><url><loc>https://client.com/contact</loc></url>"
                                 "<url><loc>https://client.com/logo.png</loc></url>"
                                 "<url><loc>https://other.com/spam</loc></url>"
                                 "<url><loc>https://client.com/contact</loc></url></urlset>"),
    })
    urls = discover_sitemap_urls(BASE, fetch)
    # deduped, same-host only, assets dropped
    assert urls == [BASE + "/contact"]


def test_bfs_fallback_when_no_sitemap():
    fetch = fake_fetch({
        BASE + "/": '<a href="/quote">q</a> <a href="https://other.com/x">n</a>',
        BASE + "/quote": '<a href="/quote/thanks">t</a>',
        BASE + "/quote/thanks": "done",
    })
    found, native, note = crawl_site(BASE, set(), fetch, log=lambda *_: None)
    assert "no sitemap" in note
    assert BASE + "/quote" in fetch.calls  # followed the same-host link
    assert all("other.com" not in u for u in fetch.calls)


# -- scanning --------------------------------------------------------------

def test_scan_page_finds_widget_ids_on_any_embed_host():
    body = """
      <iframe src="https://api.leadconnectorhq.com/widget/form/AwmP7GFejTrOUu6WE3aF"></iframe>
      <iframe src='https://link.msgsndr.com/widget/survey/Q7rXp2LmNv8sYwZt5AbC'></iframe>
      <iframe src="https://crm.smallscreenproducer.com/widget/form/TFdzuMUWmp1hz1Q4bL1g?x=1">
    """
    ids, native = scan_page(body)
    assert ids == {"AwmP7GFejTrOUu6WE3aF", "Q7rXp2LmNv8sYwZt5AbC",
                   "TFdzuMUWmp1hz1Q4bL1g"}
    assert not native


def test_scan_page_finds_inline_funnel_forms():
    # GHL funnel pages render forms inline: el_<formId>_<field> element ids
    # and "formId" keys in packed JSON — no widget iframe at all.
    body = """
      <input id="el_5ynEIFJZNDcZiTDgl0sg_first_name">
      <script>{"formId":"SxDhR1BuOShS7xDLTvVr","x":1}</script>
      <div id="el_short_x">generic builder id, too short to be a GHL key</div>
    """
    ids, _ = scan_page(body)
    assert ids == {"5ynEIFJZNDcZiTDgl0sg", "SxDhR1BuOShS7xDLTvVr"}


def test_scan_page_finds_amp_serialized_embeds():
    # AMP builds keep the id in inline-<id> element ids / data-form-id attrs
    # even when the iframe src is mangled (seen live on an AMP client site).
    body = """
      <amp-iframe id="inline-PADwizizkUAVaJQuUIf5" src="mhtml:...">
      <div data-form-id="CtA7ZeTB1C7I5uiiIuQf" data-height="600">
    """
    ids, _ = scan_page(body)
    assert ids == {"PADwizizkUAVaJQuUIf5", "CtA7ZeTB1C7I5uiiIuQf"}


def test_scan_page_flags_native_post_forms_but_not_get_search():
    assert scan_page('<form method="post" action="/thank-you">')[1] is True
    assert scan_page('<form method="get" id="searchform">')[1] is False


# -- end to end ------------------------------------------------------------

TARGETS = [{"account": "AAA Pools", "slug": "aaapools", "status": "active",
            "kind": "form", "form_name": "Website | Contact Us",
            "form_id": "AwmP7GFejTrOUu6WE3aF"},
           {"account": "AAA Pools", "slug": "aaapools", "status": "silent",
            "kind": "form", "form_name": "Old Contest",
            "form_id": "OldContest111111111"}]


def _site_pages():
    return {
        BASE + "/robots.txt": "Sitemap: https://client.com/sitemap.xml",
        BASE + "/sitemap.xml": ("<urlset><url><loc>https://client.com/contact</loc></url>"
                                "<url><loc>https://client.com/quote</loc></url>"
                                "<url><loc>https://client.com/about</loc></url></urlset>"),
        BASE + "/contact": ('<iframe src="https://api.leadconnectorhq.com/'
                            'widget/form/AwmP7GFejTrOUu6WE3aF"></iframe>'),
        BASE + "/quote": '<form method="post" action="/thanks">native</form>',
        BASE + "/about": ('<iframe src="https://api.leadconnectorhq.com/'
                          'widget/form/UnlistedFormId12345"></iframe>'),
    }


def test_run_crawl_full_statuses():
    rows = run_crawl({"aaapools": TARGETS},
                     {"aaapools": "https://client.com/"},
                     fetch=fake_fetch(_site_pages()), log=lambda *_: None)
    by_result = {}
    for r in rows:
        by_result.setdefault(r["result"], []).append(r)
    assert [r["page_url"] for r in by_result["found"]] == [BASE + "/contact"]
    assert by_result["found"][0]["form_id"] == "AwmP7GFejTrOUu6WE3aF"
    assert by_result["not_found"][0]["form_id"] == "OldContest111111111"
    assert by_result["unlisted"][0]["form_id"] == "UnlistedFormId12345"
    assert by_result["unlisted"][0]["page_url"] == BASE + "/about"
    # /quote is a candidate page with only a native form
    assert by_result["native_form_page"][0]["page_url"] == BASE + "/quote"


def test_run_crawl_without_website_marks_rows_no_website():
    rows = run_crawl({"aaapools": TARGETS}, {}, fetch=fake_fetch({}),
                     log=lambda *_: None)
    assert {r["result"] for r in rows} == {"no_website"}
    assert len(rows) == 2


def test_build_rows_one_row_per_found_page():
    found = {"AwmP7GFejTrOUu6WE3aF": [BASE + "/contact", BASE + "/pool-quote"]}
    rows = build_rows(TARGETS, found, [], "sitemap: 3 pages")
    found_rows = [r for r in rows if r["result"] == "found"]
    assert len(found_rows) == 2
    assert {r["page_url"] for r in found_rows} == {BASE + "/contact",
                                                   BASE + "/pool-quote"}
