"""Tag/pixel firing checker — verifies each client's tracking tags actually fire.

What this does
--------------
Marketing tags (Google Analytics 4, Google Tag Manager, the Meta/Facebook
pixel, Google Ads conversion, TikTok) only prove they work by making network
requests when a real page loads. This service loads each configured client
website in a headless Chromium browser, watches every outbound network
request the page makes, and checks that each EXPECTED tag fired — optionally
verifying the exact measurement/container ID, so a competitor's stray GA tag
on a shared template can't masquerade as the client's.

Configuration lives in the database (subaccounts.tag_config):
    { "website": "https://…",             -- required to check at all
      "consent_click": "css selector",    -- cookie-banner accept, or null
      "expect": [ {"type": "gtm_container", "id": "GTM-XXXX"},
                  {"type": "ga4"} ] }     -- id optional per entry
Accounts with no "expect" list are skipped — storing just the website is
free and ready for later.

Results land in the tag_checks table (one row per account per run):
status 'ok' (all expected tags fired), 'alert' (at least one didn't), or
'error' (the page itself couldn't be checked). The dashboard's account page
shows the latest row.

Honest limitations (inherited from the reviewed MLH checker, kept on purpose):
  * Consent banners: if a site blocks tags until consent and consent_click
    is missing or stale, every tag falsely reports not-fired.
  * Delayed triggers: one page load + a fixed wait. A tag that fires on
    scroll depth or a long timer will read as not-fired.
  * Bot detection: some vendors no-op in headless browsers. A tag that
    systematically "fails" on every site is the check being blocked, not
    thirty broken pixels — spot-check in a real browser before panicking.

Running it
----------
  python -m tagchecker.main             # check every configured account
  python -m tagchecker.main --dry-run   # print results, write nothing
Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY. No GHL tokens, no COLLECTOR_KEY.
Deployed as a scheduled GitHub Actions workflow (.github/workflows/tagchecker.yml).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from urllib.parse import parse_qs, urlparse

# Which network requests identify which tag type. url_match finds the vendor
# endpoint; id_param names the query-string key carrying the measurement/
# pixel/container ID (None = this type can't be ID-verified, presence is all).
TAG_PATTERNS: dict[str, dict] = {
    "ga4":           {"url_match": r"google-analytics\.com/g/collect|analytics\.google\.com/g/collect",
                      "id_param": "tid"},
    "gtm_container": {"url_match": r"googletagmanager\.com/gtm\.js", "id_param": "id"},
    "meta_pixel":    {"url_match": r"facebook\.com/tr/?\?", "id_param": "id"},
    "google_ads":    {"url_match": r"googleadservices\.com/pagead/conversion|google\.com/pagead/conversion",
                      "id_param": None},
    "tiktok_pixel":  {"url_match": r"analytics\.tiktok\.com/api/v2/pixel", "id_param": "sdkid"},
}

# How long to keep listening for tag requests after the page loads. Blunt but
# simple; tags that fire later than this read as not-fired (see limitations).
TAG_WAIT_MS = 8000
PAGE_LOAD_TIMEOUT_MS = 20000
CONSENT_CLICK_TIMEOUT_MS = 4000


def _query_param(url: str, key: str) -> str | None:
    """The first value of ?key=... in a URL, or None. Never raises."""
    try:
        values = parse_qs(urlparse(url).query).get(key)
        return values[0] if values else None
    except Exception:
        return None


def evaluate_expectations(expect: list[dict], captured_urls: list[str]) -> list[dict]:
    """Pure decision logic: which expected tags appear in the captured
    request URLs? Separated from the browser so it's testable without one.

    Returns one result per expectation: {type, id, fired} where fired is
    True/False, or None for an unknown tag type (config typo — surfaced,
    not silently dropped).
    """
    results: list[dict] = []
    for tag in expect or []:
        tag_type = tag.get("type")
        wanted_id = tag.get("id")
        pattern = TAG_PATTERNS.get(tag_type or "")
        if pattern is None:
            results.append({"type": tag_type, "id": wanted_id, "fired": None,
                            "note": "unknown tag type"})
            continue
        url_re = re.compile(pattern["url_match"])
        fired = False
        for url in captured_urls:
            if not url_re.search(url):
                continue
            if not wanted_id or not pattern["id_param"]:
                fired = True    # no specific ID to verify; the endpoint firing is enough
                break
            if _query_param(url, pattern["id_param"]) == wanted_id:
                fired = True
                break
        results.append({"type": tag_type, "id": wanted_id, "fired": fired})
    return results


def status_of(results: list[dict], error: str | None) -> str:
    """Collapse per-tag results to the row status: error > alert > ok."""
    if error:
        return "error"
    if any(r.get("fired") is False for r in results):
        return "alert"
    return "ok"


def check_site(browser, website: str, consent_click: str | None,
               expect: list[dict]) -> dict:
    """Load one site in a fresh browser context and evaluate its tags.

    A fresh context per site = clean cookies/storage, so one client's consent
    state can't leak into the next client's check.
    """
    captured: list[str] = []
    context = page = None
    error = None
    try:
        context = browser.new_context()
        page = context.new_page()
        page.on("request", lambda req: captured.append(req.url))
        page.goto(website, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
        if consent_click:
            try:
                page.click(consent_click, timeout=CONSENT_CLICK_TIMEOUT_MS)
            except Exception:
                pass  # banner absent / already accepted / selector stale — proceed
        page.wait_for_timeout(TAG_WAIT_MS)
    except Exception as exc:  # page never loaded, DNS failure, timeout, ...
        error = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        for closer in (page, context):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
    results = evaluate_expectations(expect, captured) if not error else []
    return {"status": status_of(results, error), "results": results, "error": error}


def load_accounts(client) -> list[dict]:
    """Active accounts whose tag_config has both a website and expectations."""
    rows = (client.table("subaccounts")
            .select("location_id,name,tag_config")
            .eq("active", True).execute().data) or []
    out = []
    for row in rows:
        cfg = row.get("tag_config") or {}
        if cfg.get("website") and cfg.get("expect"):
            out.append(row)
    return out


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that client tracking tags fire.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print results instead of writing tag_checks rows")
    args = parser.parse_args(argv)

    from supabase import create_client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("tagchecker: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set", file=sys.stderr)
        return 1
    client = create_client(url, key)

    accounts = load_accounts(client)
    if not accounts:
        print("tagchecker: no accounts have tag expectations configured — nothing to do")
        return 0

    # One shared browser for the whole run (contexts are cheap; browsers are
    # not), launched only after we know there is work.
    from playwright.sync_api import sync_playwright
    alerts = errors = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for account in accounts:
                cfg = account["tag_config"]
                outcome = check_site(browser, cfg["website"], cfg.get("consent_click"),
                                     cfg.get("expect") or [])
                line = (f"{account['name']}: {outcome['status']}"
                        + (f" — {outcome['error']}" if outcome["error"] else ""))
                for r in outcome["results"]:
                    line += f"\n    {r['type']}{'(' + r['id'] + ')' if r.get('id') else ''}: " \
                            + ("FIRED" if r["fired"] else "NOT FIRED" if r["fired"] is False else "unknown type")
                print(line)
                alerts += outcome["status"] == "alert"
                errors += outcome["status"] == "error"
                if not args.dry_run:
                    client.table("tag_checks").insert({
                        "location_id": account["location_id"],
                        "url": cfg["website"],
                        "status": outcome["status"],
                        "results": outcome["results"],
                        "error": outcome["error"],
                    }).execute()
        finally:
            browser.close()

    print(f"tagchecker done: {len(accounts)} checked, {alerts} alert(s), {errors} error(s)")
    # Alerts are data, not infrastructure failures — always exit 0 unless the
    # checker itself couldn't run (import/env problems return 1 above).
    return 0


if __name__ == "__main__":
    sys.exit(run())
