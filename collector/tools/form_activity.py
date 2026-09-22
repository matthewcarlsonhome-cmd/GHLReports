"""Every form in every subaccount: is it still used, and where does it live?

    python -m collector.tools.form_activity                     # whole book
    python -m collector.tools.form_activity --location flohr    # one account
    python -m collector.tools.form_activity --crawl             # + check the client sites
    python -m collector.main --form-activity [--form-activity-crawl]   # Render / Actions

Writes three CSVs (default into the current directory) and prints a
per-account summary plus a verdict on GHL's "30-day" history question:

  form-activity.csv   one row per form/survey: channel (website, Google ad,
                      Facebook ad, Facebook lead ad, ...), last real
                      submission, days inactive, 7/30/90/365-day and
                      all-time counts, the page it lives on now,
                      Datadog-candidate recommendation
  form-pages.csv      one row per (form, page URL, source) with hit counts
  form-accounts.csv   one row per subaccount: status counts, 90-day
                      submissions per channel, history reach, form ids seen
                      in submissions but missing from Sites > Forms
                      (usually Facebook lead-ad forms)

How this fits in
----------------
Supersedes tools/form_urls.py (30-day window, URLs only) for the question
"which forms are live, and where". It reads history through
collector/form_history.py, the same code the nightly form_health check
uses, so the report and the dashboard agree. --crawl adds
tools/find_embeds.py: it re-fetches every page a form was submitted from
(is the form still there?) and crawls the account website for embeds of
forms that have no submission history to learn a page from.

Key ideas to understand this file
---------------------------------
* Read-only: GETs against GHL with each account's own PIT; the crawl only
  fetches public web pages. Nothing is written anywhere except the CSVs.
* PII boundary: submissions carry names, emails, phones and answers. Only
  timestamps, page URLs/titles (query strings stripped) and GHL's
  attribution labels (eventData source/medium) are read; the email is
  compared with FORM_CHECK_EMAIL and dropped. The CSVs hold form names,
  ids, URLs, dates and counts only.
* "Real" activity excludes the weekly form-check test submissions
  (docs/FORM-MONITORING.md), so a synthetic test can never make a dead form
  look alive.
* Days inactive are calendar days since the newest real submission. The
  nightly dashboard status uses business days for alerting; the report's
  buckets are for triage: active <=7d, quiet 8-30d, dormant 31-365d,
  stale >365d, never, unknown.
* Exit codes: 0 when every account was read, 1 when any account was
  unreachable (no token / API error).
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from .. import form_history, lead_channels
from ..ghl_client import GHLAuthError, GHLClient, GHLError

LIST_LIMIT = 50                # /forms/ and /surveys/ page size cap
REPORT_PAGE_SIZE = 100         # newest submissions read per form (max GHL allows)
UNLISTED_DAYS = 90             # window scanned for form ids missing from the list
UNLISTED_PAGE_CAP = 20         # 2,000 submissions per account for that scan
VERIFY_PAGE_CAP = 40           # known pages re-fetched per account in --crawl
CRAWL_MAX_PAGES = 60           # site pages scanned per account in --crawl
CRAWL_WORKERS = 6              # accounts crawled in parallel (one site each)
LIVE_DAYS = 90                 # a page seen in a submission this recent counts as live
BUSY_30D = 4                   # >= this many real submissions in 30d = real traffic proves it
HOSTED_BASE = "https://api.leadconnectorhq.com/widget"
WINDOWS = (7, 30, 90, 365)

# Forms that are plumbing rather than lead capture: never Datadog candidates.
UTILITY_RE = re.compile(
    r"unsubscribe|opt[\s-]?in|optin|a2p|template|log\s?in|\btest\b|\bdemo\b|\bsample\b|internal",
    re.IGNORECASE)

FORM_COLUMNS = [
    "account", "am", "slug", "location_id", "kind", "form_id", "form_name",
    "channel", "status", "last_submission", "days_inactive",
    "subs_7d", "subs_30d", "subs_90d", "subs_365d", "subs_all_time",
    "live_page_url", "live_page_last_seen", "other_page_urls", "still_on_page",
    "site_embed_pages", "hosted_form_url", "monitoring", "datadog_candidate",
    "last_form_check", "channel_evidence", "notes",
]
PAGE_COLUMNS = ["account", "slug", "location_id", "kind", "form_id", "form_name",
                "source", "page_url", "page_title", "hits", "last_seen", "still_embedded"]
ACCOUNT_COLUMNS = [
    "account", "am", "slug", "location_id", "forms", "surveys", "active", "quiet",
    "dormant", "stale", "never", "unknown",
    "subs_90d_website", "subs_90d_google_ads", "subs_90d_facebook_ads",
    "subs_90d_facebook_lead_ads", "subs_90d_other",
    "unlisted_form_ids", "unlisted_subs_90d",
    "default_window_total", "all_time_total", "oldest_submission", "history_window",
    "website", "crawl_note", "error",
]


# -- small helpers ----------------------------------------------------------

def _day(ts: str | None) -> date | None:
    try:
        return date.fromisoformat(str(ts)[:10]) if ts else None
    except ValueError:
        return None


def status_for(total: int | None, last_day: date | None, today: date) -> str:
    """Report bucket from calendar days since the newest real submission."""
    if total is None:
        return "unknown"
    if total == 0 or last_day is None:
        return "never" if total == 0 else "unknown"
    days = (today - last_day).days
    if days <= 7:
        return "active"
    if days <= 30:
        return "quiet"
    if days <= 365:
        return "dormant"
    return "stale"


def is_hosted(url: str) -> bool:
    """A GHL-hosted form/survey link rather than a page embedding it."""
    return "/widget/form/" in url or "/widget/survey/" in url


def hosted_url(kind: str, form_id: str) -> str:
    return f"{HOSTED_BASE}/{'survey' if kind == 'survey' else 'form'}/{form_id}"


def list_items(client, path: str, location_id: str, keys: tuple[str, ...]) -> list[dict]:
    """Every form (or survey) in the account via the skip/limit pager."""
    items: list[dict] = []
    skip = 0
    while True:
        data = client.request("GET", path, params={
            "locationId": location_id, "limit": LIST_LIMIT, "skip": skip})
        batch = [b for b in form_history._first_list(data, *keys) if isinstance(b, dict)]
        items.extend(b for b in batch if b.get("id") or b.get("_id"))
        if len(batch) < LIST_LIMIT:
            return items
        skip += LIST_LIMIT


def window_counts(client, path: str, id_param: str, location_id: str, item_id: str,
                  hist: dict, today: date) -> dict[int, int | None]:
    """Real submissions in the last 7/30/90/365 days (form_history.window_counts)."""
    return form_history.window_counts(client, path, id_param, location_id, item_id,
                                      hist, today, WINDOWS)


def aggregate_pages(records: list[dict]) -> dict[str, dict]:
    """{url: {title, hits, last_seen}} over REAL submissions that carried a
    page URL, busiest first when iterated via sorted_pages()."""
    pages: dict[str, dict] = {}
    for rec in records:
        if rec["check"] or not rec["page_url"]:
            continue
        entry = pages.setdefault(rec["page_url"], {"title": "", "hits": 0, "last_seen": ""})
        entry["hits"] += 1
        entry["last_seen"] = max(entry["last_seen"], rec["at"][:10])
        if not entry["title"] and rec["page_title"]:
            entry["title"] = rec["page_title"]
    return pages


def sorted_pages(pages: dict[str, dict]) -> list[tuple[str, dict]]:
    """Most recently used first, then busiest."""
    return sorted(pages.items(), key=lambda kv: (kv[1]["last_seen"], kv[1]["hits"]),
                  reverse=True)


def unlisted_forms(client, location_id: str, listed: set[str], today: date,
                   check_email: str) -> dict[str, dict]:
    """Form ids that received submissions in the last UNLISTED_DAYS but are
    not in Sites > Forms (Facebook lead-ad forms, deleted forms). PII-free:
    formId, time, page URL and GHL's attribution labels only."""
    found: dict[str, dict] = {}
    start = (today - timedelta(days=UNLISTED_DAYS)).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    for page in range(1, UNLISTED_PAGE_CAP + 1):
        data = client.request("GET", "/forms/submissions", params={
            "locationId": location_id, "startAt": start, "endAt": end,
            "limit": REPORT_PAGE_SIZE, "page": page})
        batch = [s for s in form_history._first_list(data, "submissions", "data")
                 if isinstance(s, dict)]
        for sub in batch:
            form_id = str(sub.get("formId") or "")
            if not form_id or form_id in listed:
                continue
            rec = form_history.project(sub, check_email)
            if rec["check"]:
                continue
            event = (sub.get("others") or {}).get("eventData") or {}
            entry = found.setdefault(form_id, {"subs": 0, "last_seen": "", "pages": {},
                                               "labels": defaultdict(int), "records": []})
            entry["subs"] += 1
            if len(entry["records"]) < REPORT_PAGE_SIZE:
                entry["records"].append(rec)
            entry["last_seen"] = max(entry["last_seen"], rec["at"][:10])
            if rec["page_url"]:
                entry["pages"][rec["page_url"]] = entry["pages"].get(rec["page_url"], 0) + 1
            label = " / ".join(str(v) for v in (event.get("source"), event.get("medium")) if v)
            if label:
                entry["labels"][label] += 1
        if len(batch) < REPORT_PAGE_SIZE:
            break
    return found


def history_reach(client, window: form_history.SubmissionWindow,
                  location_id: str) -> dict:
    """The 30-day question, answered per account: how many submissions GHL
    returns with no dates (its default) vs explicit all-time dates, and the
    date of the oldest submission on record."""
    out = {"default_window_total": None, "all_time_total": None, "oldest_submission": None}
    try:
        default = client.request("GET", "/forms/submissions", params={
            "locationId": location_id, "limit": 1, "page": 1})
        out["default_window_total"] = form_history.meta_total(default)
        wide = window.request(client, "/forms/submissions", {
            "locationId": location_id, "limit": 1, "page": 1})
        total = form_history.meta_total(wide)
        out["all_time_total"] = total
        if total:
            last = window.request(client, "/forms/submissions", {
                "locationId": location_id, "limit": 1, "page": total})
            subs = form_history._first_list(last, "submissions", "data")
            if subs and isinstance(subs[0], dict):
                out["oldest_submission"] = str(subs[0].get("createdAt") or "")[:10] or None
    except GHLError:
        pass
    return out


def recommend(kind: str, name: str, live: bool, subs_30d: int | None) -> tuple[str, str]:
    """(monitoring recommendation, datadog_candidate yes/optional/no)."""
    if kind == "unlisted":
        return ("external lead form (not on a web page) - watch lead flow instead", "no")
    if UTILITY_RE.search(name or ""):
        return ("utility/internal form - skip", "no")
    if not live:
        return ("no live page found - confirm it is still in use", "no")
    if subs_30d is not None and subs_30d >= BUSY_30D:
        return ("real traffic proves it weekly - synthetic test optional", "optional")
    return ("weekly synthetic test", "yes")


# -- per-account collection ----------------------------------------------

def collect_account(sub: dict, client, today: date, check_email: str,
                    log=print) -> dict:
    """Read one account. Returns {forms: [...], pages: [...], account: {...},
    unlisted: {...}, error: str|None}; never raises GHLError."""
    location_id = sub["location_id"]
    window = form_history.SubmissionWindow(today)
    result = {"forms": [], "unlisted": {}, "reach": {}, "error": None,
              "window": window}
    try:
        forms = list_items(client, "/forms/", location_id, ("forms", "data", "list"))
    except GHLError as exc:
        result["error"] = str(exc)
        return result
    try:
        surveys = list_items(client, "/surveys/", location_id, ("surveys", "data", "list"))
    except GHLAuthError:
        surveys = []   # surveys.readonly not granted on this token yet
    except GHLError as exc:
        surveys = []
        log(f"  surveys unavailable: {exc}")
    result["reach"] = history_reach(client, window, location_id)

    for kind, items, path, id_param in (
            ("form", forms, "/forms/submissions", "formId"),
            ("survey", surveys, "/surveys/submissions", "surveyId")):
        for item in items:
            item_id = str(item.get("id") or item.get("_id"))
            hist = form_history.fetch_history_safe(
                client, window, path, id_param, location_id, item_id,
                page_size=REPORT_PAGE_SIZE, check_email=check_email)
            counts = window_counts(client, path, id_param, location_id, item_id, hist, today)
            result["forms"].append({
                "kind": kind, "form_id": item_id,
                "name": item.get("name") or f"Unnamed {kind}",
                "hist": hist, "counts": counts,
                "pages": aggregate_pages(hist["records"]),
            })
    listed = {f["form_id"] for f in result["forms"]}
    try:
        result["unlisted"] = unlisted_forms(client, location_id, listed, today, check_email)
    except GHLError as exc:
        log(f"  unlisted-form scan failed: {exc}")
    return result


# -- crawl (optional) ------------------------------------------------------

def crawl_account(sub: dict, result: dict, fetch, max_pages: int, today: date,
                  log=print) -> dict:
    """Re-check known pages and crawl the website. Returns {still: {form_id:
    {url: bool|None}}, site: {form_id: [urls]}, note: str}."""
    from . import find_embeds
    still: dict[str, dict[str, bool | None]] = defaultdict(dict)
    known: dict[str, set[str]] = defaultdict(set)
    for form in result["forms"]:
        for url, entry in form["pages"].items():
            seen = _day(entry["last_seen"])
            if not is_hosted(url) and seen and (today - seen).days <= 365:
                known[url].add(form["form_id"])
    for url in sorted(known, key=lambda u: -len(known[u]))[:VERIFY_PAGE_CAP]:
        body = fetch(url)
        ids = find_embeds.scan_page(body)[0] if body else None
        for form_id in known[url]:
            still[form_id][url] = None if ids is None else (form_id in ids)

    site: dict[str, list[str]] = {}
    note = ""
    website = (sub.get("tag_config") or {}).get("website")
    if website:
        targets = {f["form_id"] for f in result["forms"]}
        found, _native, note = find_embeds.crawl_site(
            find_embeds.site_base(website), targets, fetch,
            max_pages=max_pages, log=lambda *_: None)
        site = {fid: sorted(set(urls)) for fid, urls in found.items()}
    else:
        note = "no website configured"
    return {"still": dict(still), "site": site, "note": note}


# -- rows ------------------------------------------------------------------

def build_rows(sub: dict, result: dict, crawl: dict | None, today: date
               ) -> tuple[list[dict], list[dict], dict]:
    """(form rows, page rows, account row) for one account."""
    base = {"account": sub.get("name") or sub.get("slug") or sub["location_id"],
            "slug": sub.get("slug") or "", "location_id": sub["location_id"]}
    am = sub.get("am_name") or ""
    form_rows: list[dict] = []
    page_rows: list[dict] = []
    tally = defaultdict(int)
    crawl = crawl or {}
    still_map = crawl.get("still") or {}
    site_map = crawl.get("site") or {}
    listed_ids = set()
    website = (sub.get("tag_config") or {}).get("website") or ""
    by_channel: dict[str, int] = defaultdict(int)

    for form in sorted(result["forms"], key=lambda f: (f["kind"], f["name"].lower())):
        hist, counts, pages = form["hist"], form["counts"], form["pages"]
        listed_ids.add(form["form_id"])
        last_day = _day(hist["last_at"])
        status = status_for(hist["total"], last_day, today)
        tally[status] += 1
        ordered = sorted_pages(pages)
        live_urls = [(u, e) for u, e in ordered
                     if _day(e["last_seen"]) and (today - _day(e["last_seen"])).days <= LIVE_DAYS]
        still = still_map.get(form["form_id"], {})
        site_pages = site_map.get(form["form_id"], [])
        live = bool(live_urls or site_pages or any(v for v in still.values()))
        top_url, top = ordered[0] if ordered else ("", {})
        monitoring, candidate = recommend(form["kind"], form["name"], live, counts.get(30))
        channel = lead_channels.classify(form["kind"], form["name"], hist["records"],
                                         website, crawl_pages=site_pages)
        by_channel[_channel_bucket(channel["channel"])] += counts.get(90) or 0
        notes = []
        if hist["window"] and hist["window"] != "all_time":
            notes.append(f"history limited to {hist['window']} window")
        if top_url and is_hosted(top_url):
            notes.append("submitted via the GHL-hosted link, not an embedded page")
        if top_url and still.get(top_url) is False:
            notes.append("form no longer found on its last page")
        form_rows.append({
            **base, "am": am, "kind": form["kind"], "form_id": form["form_id"],
            "form_name": form["name"], "channel": channel["channel"], "status": status,
            "last_submission": last_day.isoformat() if last_day else "",
            "days_inactive": (today - last_day).days if last_day else "",
            "subs_7d": _fmt(counts.get(7)), "subs_30d": _fmt(counts.get(30)),
            "subs_90d": _fmt(counts.get(90)), "subs_365d": _fmt(counts.get(365)),
            "subs_all_time": _fmt(hist["total"]),
            "live_page_url": top_url, "live_page_last_seen": top.get("last_seen", ""),
            "other_page_urls": "; ".join(f"{u} ({e['hits']}, last {e['last_seen']})"
                                         for u, e in ordered[1:6]),
            "still_on_page": _still_label(still.get(top_url)) if top_url and not is_hosted(top_url)
                             else ("" if not crawl else "n/a"),
            "site_embed_pages": "; ".join(site_pages[:5]),
            "hosted_form_url": hosted_url(form["kind"], form["form_id"]),
            "monitoring": monitoring, "datadog_candidate": candidate,
            "last_form_check": (hist["last_check_at"] or "")[:10],
            "channel_evidence": channel["evidence"],
            "notes": "; ".join(notes),
        })
        for url, entry in ordered:
            page_rows.append({**base, "kind": form["kind"], "form_id": form["form_id"],
                              "form_name": form["name"], "source": "submissions",
                              "page_url": url, "page_title": entry["title"],
                              "hits": entry["hits"], "last_seen": entry["last_seen"],
                              "still_embedded": _still_label(still.get(url))})
        for url in site_pages:
            page_rows.append({**base, "kind": form["kind"], "form_id": form["form_id"],
                              "form_name": form["name"], "source": "site crawl",
                              "page_url": url, "page_title": "", "hits": "",
                              "last_seen": "", "still_embedded": "yes"})

    unlisted = result.get("unlisted") or {}
    for form_id, entry in sorted(unlisted.items(), key=lambda kv: -kv[1]["subs"]):
        labels = ", ".join(f"{k} ({v})" for k, v in
                           sorted(entry["labels"].items(), key=lambda kv: -kv[1])[:3])
        top_page = max(entry["pages"].items(), key=lambda kv: kv[1])[0] if entry["pages"] else ""
        last_day = _day(entry["last_seen"])
        monitoring, candidate = recommend("unlisted", "", False, None)
        channel = lead_channels.classify("unlisted", "", entry["records"], website,
                                         labels=dict(entry["labels"]))
        by_channel[_channel_bucket(channel["channel"])] += entry["subs"]
        form_rows.append({
            **base, "am": am, "kind": "unlisted", "form_id": form_id,
            "form_name": "(not in Sites > Forms)", "channel": channel["channel"],
            "status": status_for(entry["subs"], last_day, today),
            "last_submission": entry["last_seen"],
            "days_inactive": (today - last_day).days if last_day else "",
            "subs_7d": "", "subs_30d": "", "subs_90d": entry["subs"], "subs_365d": "",
            "subs_all_time": "", "live_page_url": top_page, "live_page_last_seen": "",
            "other_page_urls": "", "still_on_page": "", "site_embed_pages": "",
            "hosted_form_url": "", "monitoring": monitoring, "datadog_candidate": candidate,
            "last_form_check": "", "channel_evidence": channel["evidence"],
            "notes": f"attribution: {labels}" if labels else "no page or source recorded",
        })

    reach = result.get("reach") or {}
    window = result.get("window")
    account = {
        **base, "am": am,
        "forms": sum(1 for f in result["forms"] if f["kind"] == "form"),
        "surveys": sum(1 for f in result["forms"] if f["kind"] == "survey"),
        **{k: tally.get(k, 0) for k in ("active", "quiet", "dormant", "stale", "never", "unknown")},
        **{f"subs_90d_{k}": by_channel.get(k, 0) for k in CHANNEL_BUCKETS},
        "unlisted_form_ids": len(unlisted),
        "unlisted_subs_90d": sum(e["subs"] for e in unlisted.values()),
        "default_window_total": _fmt(reach.get("default_window_total")),
        "all_time_total": _fmt(reach.get("all_time_total")),
        "oldest_submission": reach.get("oldest_submission") or "",
        "history_window": window.label if window else "",
        "website": (sub.get("tag_config") or {}).get("website") or "",
        "crawl_note": crawl.get("note", "") if crawl else "not crawled",
        "error": result.get("error") or "",
    }
    return form_rows, page_rows, account


CHANNEL_BUCKETS = ("website", "google_ads", "facebook_ads", "facebook_lead_ads", "other")


def _channel_bucket(channel: str) -> str:
    return {lead_channels.WEBSITE_FORM: "website",
            lead_channels.GOOGLE_AD_FORM: "google_ads",
            lead_channels.FACEBOOK_AD_FORM: "facebook_ads",
            lead_channels.FACEBOOK_LEAD_AD: "facebook_lead_ads"}.get(channel, "other")


def _fmt(value) -> str | int:
    return "" if value is None else value


def _still_label(value: bool | None) -> str:
    return {True: "yes", False: "NO", None: ""}.get(value, "") if value is not None else ""


# -- book ------------------------------------------------------------------

def collect_book(store, subs: list[dict], *, crawl: bool = False,
                 crawl_max_pages: int = CRAWL_MAX_PAGES, client_factory=GHLClient,
                 fetch=None, today: date | None = None, log=print
                 ) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Run the report for `subs`. Returns (form rows, page rows, account
    rows, failed account labels). Shared by the CLI below and
    collector/main.py --form-activity (how Render / Actions produce it)."""
    today = today or date.today()
    check_email = form_history.form_check_email()
    results: list[tuple[dict, dict]] = []
    failed: list[str] = []
    for sub in sorted(subs, key=lambda s: (s.get("name") or s.get("slug") or "").lower()):
        label = sub.get("name") or sub.get("slug") or sub["location_id"]
        token = store.get_pit(sub["location_id"])
        if not token:
            log(f"{label}: SKIPPED - no PIT stored")
            failed.append(label)
            continue
        result = collect_account(sub, client_factory(token), today, check_email, log=log)
        if result["error"]:
            log(f"{label}: FAILED - {result['error']}")
            failed.append(label)
        results.append((sub, result))

    crawls: dict[str, dict] = {}
    if crawl:
        if fetch is None:
            from .find_embeds import real_fetch
            fetch = real_fetch
        with ThreadPoolExecutor(max_workers=CRAWL_WORKERS) as pool:
            futures = {sub["location_id"]: pool.submit(crawl_account, sub, result, fetch,
                                                       crawl_max_pages, today, log)
                       for sub, result in results if not result["error"]}
            for location_id, future in futures.items():
                try:
                    crawls[location_id] = future.result()
                except Exception as exc:  # a crawl failure never sinks the report
                    crawls[location_id] = {"still": {}, "site": {}, "note": f"crawl failed: {exc}"}

    form_rows: list[dict] = []
    page_rows: list[dict] = []
    account_rows: list[dict] = []
    for sub, result in results:
        rows, pages, account = build_rows(sub, result, crawls.get(sub["location_id"]), today)
        form_rows.extend(rows)
        page_rows.extend(pages)
        account_rows.append(account)
        if not result["error"]:
            log(f"{account['account']}: {account['forms']} forms, {account['surveys']} surveys - "
                f"{account['active']} active, {account['quiet']} quiet, {account['dormant']} dormant, "
                f"{account['stale']} stale, {account['never']} never; 90d by channel: "
                f"website {account['subs_90d_website']}, Google ads {account['subs_90d_google_ads']}, "
                f"Facebook ads {account['subs_90d_facebook_ads']}, Facebook lead ads "
                f"{account['subs_90d_facebook_lead_ads']}, other {account['subs_90d_other']}; "
                f"{account['unlisted_form_ids']} unlisted form id(s); "
                f"history {account['default_window_total'] or 0} (GHL default) vs "
                f"{account['all_time_total'] or 0} (all-time), oldest "
                f"{account['oldest_submission'] or 'n/a'}")
    log(verdict(account_rows, today))
    return form_rows, page_rows, account_rows, failed


def verdict(account_rows: list[dict], today: date) -> str:
    """One line settling the 30-day question from this run's evidence."""
    oldest = [date.fromisoformat(r["oldest_submission"]) for r in account_rows
              if r.get("oldest_submission")]
    grew = [r for r in account_rows
            if isinstance(r.get("all_time_total"), int) and isinstance(r.get("default_window_total"), int)
            and r["all_time_total"] > r["default_window_total"]]
    limited = [r["account"] for r in account_rows
               if r.get("history_window") not in ("all_time", "", None)]
    if oldest and (today - min(oldest)).days > 31:
        text = (f"30-DAY CHECK: explicit dates returned submissions back to {min(oldest)} "
                f"({(today - min(oldest)).days} days); {len(grew)} account(s) show more "
                "history than GHL's default window. The 30-day limit is the API default, "
                "not a retention cap.")
    elif oldest:
        text = ("30-DAY CHECK: even with explicit dates no submission older than 31 days "
                "came back. GHL may cap history here; treat days_inactive > 30 as '30+'.")
    else:
        text = "30-DAY CHECK: no submissions returned; nothing to conclude."
    if limited:
        text += f" History narrowed by GHL for: {', '.join(limited)}."
    return text


def to_csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def write_outputs(out_dir: str, form_rows, page_rows, account_rows) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for name, rows, columns in (("form-activity.csv", form_rows, FORM_COLUMNS),
                                ("form-pages.csv", page_rows, PAGE_COLUMNS),
                                ("form-accounts.csv", account_rows, ACCOUNT_COLUMNS)):
        path = os.path.join(out_dir, name)
        with open(path, "w", newline="") as fh:
            fh.write(to_csv(rows, columns))
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(prog="form_activity", description=(
        "Per-form activity and page locations across the book (read-only)."))
    parser.add_argument("--location", help="one slug; default: every active account")
    parser.add_argument("--crawl", action="store_true",
                        help="also re-check pages and crawl client websites for embeds")
    parser.add_argument("--max-pages", type=int, default=CRAWL_MAX_PAGES,
                        help=f"site pages per account for --crawl (default {CRAWL_MAX_PAGES})")
    parser.add_argument("--out-dir", default=".", help="where the three CSVs go")
    args = parser.parse_args()

    from ..store import Store
    store = Store()
    subs = store.load_subaccounts()
    if args.location:
        subs = [s for s in subs if s.get("slug") == args.location]
        if not subs:
            print(f"no active subaccount with slug {args.location!r}", file=sys.stderr)
            sys.exit(1)
    form_rows, page_rows, account_rows, failed = collect_book(
        store, subs, crawl=args.crawl, crawl_max_pages=args.max_pages)
    for path in write_outputs(args.out_dir, form_rows, page_rows, account_rows):
        print(f"wrote {path}")
    if failed:
        print(f"{len(failed)} account(s) not checked: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
