"""Map each form to the client-site page URL(s) it is embedded on.

    python -m collector.tools.form_urls                      # whole book, 30 days
    python -m collector.tools.form_urls --location aaapools  # one account
    python -m collector.tools.form_urls --days 60 --out /tmp/form-urls.csv

Writes a CSV (default form-urls.csv in the current directory) with one row per
(form, page URL) pair, plus a row for every form that had no submissions in
the window, and prints a per-account summary.

How this fits in
----------------
GHL never stores where a form got embedded — the iframe snippet lives only in
the client's page HTML. But every submission records the page it came from:
GET /forms/submissions returns others.eventData.page.url (+ title, domain,
referrer, source). This tool aggregates those per form so the Datadog browser
tests know which live page to open per form. It can only see forms that HAD
submissions in the window; a silent form's placement exists nowhere in GHL
(fall back to the client team's page list, or crawling the site for
/widget/form/<id> iframes).

Key ideas to understand this file
---------------------------------
* Read-only: form list + submissions GETs with the collector's existing
  scopes. Nothing is written to any GHL account.
* PII boundary: a submission's `others` dict ALSO carries the submitted
  answers (names, emails, phones). Only others["eventData"] is ever read
  here; answer fields never reach the CSV or stdout.
* URLs are normalized to scheme://host/path — query strings (utm tags etc.)
  and fragments are stripped so the same page dedupes to one row. Pass
  --keep-query to disable when a page varies by query parameter.
* Exit codes: 0 when every requested account was checked (even if some forms
  are silent), 1 when an account was unreachable (no token / API error) so a
  cron wrapper can tell "ran clean" from "partial".
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from urllib.parse import urlsplit, urlunsplit

from ..ghl_client import GHLClient, GHLError
from ..store import Store

PAGE_LIMIT = 100           # /forms/submissions page size
FORM_LIST_LIMIT = 50       # /forms/ list endpoint caps at 50/page
MAX_SUBMISSION_PAGES = 50  # safety cap: 5,000 submissions per account/window

CSV_COLUMNS = ["account", "slug", "location_id", "form_id", "form_name",
               "status", "page_url", "page_title", "hits", "last_seen"]


def normalize_url(url: str, keep_query: bool = False) -> str:
    """scheme://host/path, lowercased host, no fragment; query optional."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path or "/", parts.query if keep_query else "", ""))


def fetch_forms(client: GHLClient, location_id: str) -> list[dict]:
    """[{form_id, name}] for every form in the account (skip/limit pager)."""
    forms: list[dict] = []
    skip = 0
    while True:
        data = client.request("GET", "/forms/", params={
            "locationId": location_id, "limit": FORM_LIST_LIMIT, "skip": skip})
        batch = data.get("forms") or data.get("data") or data.get("list") or []
        batch = [b for b in batch if isinstance(b, dict)]
        for form in batch:
            form_id = form.get("id") or form.get("_id")
            if form_id:
                forms.append({"form_id": form_id,
                              "name": form.get("name") or "Unnamed form"})
        if len(batch) < FORM_LIST_LIMIT:
            return forms
        skip += FORM_LIST_LIMIT


def fetch_submission_pages(client: GHLClient, location_id: str,
                           start: str, end: str,
                           keep_query: bool) -> tuple[dict, bool]:
    """Aggregate the window's submissions into
    {form_id: {url: {"title", "hits", "last_seen"}}}. The "" url key collects
    submissions whose eventData carried no page URL (API posts, some mobile
    widgets). Second value is False when the page cap truncated the window."""
    per_form: dict[str, dict[str, dict]] = {}
    page = 1
    complete = True
    while True:
        data = client.request("GET", "/forms/submissions", params={
            "locationId": location_id, "startAt": start, "endAt": end,
            "limit": PAGE_LIMIT, "page": page})
        batch = data.get("submissions") or data.get("data") or []
        batch = [b for b in batch if isinstance(b, dict)]
        for sub in batch:
            form_id = sub.get("formId")
            if not form_id:
                continue
            # PII boundary: eventData only — never the sibling answer fields.
            event = (sub.get("others") or {}).get("eventData") or {}
            page_info = event.get("page") or {}
            raw_url = page_info.get("url") or ""
            url = normalize_url(raw_url, keep_query) if raw_url else ""
            seen = (sub.get("createdAt") or "")[:10]
            entry = per_form.setdefault(form_id, {}).setdefault(url, {
                "title": page_info.get("title") or "", "hits": 0, "last_seen": ""})
            entry["hits"] += 1
            entry["last_seen"] = max(entry["last_seen"], seen)
            if not entry["title"] and page_info.get("title"):
                entry["title"] = page_info["title"]
        if len(batch) < PAGE_LIMIT:
            break
        page += 1
        if page > MAX_SUBMISSION_PAGES:
            complete = False
            break
    return per_form, complete


def account_rows(sub: dict, forms: list[dict], per_form: dict,
                 keep_query: bool) -> list[dict]:
    """One CSV row per (form, url); silent/no-url forms get a stub row."""
    base = {"account": sub.get("name") or sub.get("slug") or "?",
            "slug": sub.get("slug") or "", "location_id": sub["location_id"]}
    rows: list[dict] = []
    for form in sorted(forms, key=lambda f: f["name"].lower()):
        urls = per_form.get(form["form_id"], {})
        placed = {u: e for u, e in urls.items() if u}
        no_url = urls.get("")
        if placed:
            for url, entry in sorted(placed.items(),
                                     key=lambda kv: -kv[1]["hits"]):
                rows.append({**base, "form_id": form["form_id"],
                             "form_name": form["name"], "status": "placed",
                             "page_url": url, "page_title": entry["title"],
                             "hits": entry["hits"],
                             "last_seen": entry["last_seen"]})
            if no_url:
                rows.append({**base, "form_id": form["form_id"],
                             "form_name": form["name"], "status": "no_url_data",
                             "page_url": "", "page_title": "",
                             "hits": no_url["hits"],
                             "last_seen": no_url["last_seen"]})
        elif no_url:
            # Submissions happened but none carried a page URL.
            rows.append({**base, "form_id": form["form_id"],
                         "form_name": form["name"], "status": "no_url_data",
                         "page_url": "", "page_title": "",
                         "hits": no_url["hits"], "last_seen": no_url["last_seen"]})
        else:
            rows.append({**base, "form_id": form["form_id"],
                         "form_name": form["name"], "status": "silent",
                         "page_url": "", "page_title": "", "hits": 0,
                         "last_seen": ""})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(prog="form_urls", description=(
        "Map forms to the page URLs they were submitted from (read-only)."))
    parser.add_argument("--location", help="one slug; default: every active account")
    parser.add_argument("--days", type=int, default=30,
                        help="submission window in days (default 30)")
    parser.add_argument("--out", default="form-urls.csv", help="CSV output path")
    parser.add_argument("--keep-query", action="store_true",
                        help="keep URL query strings instead of stripping them")
    args = parser.parse_args()

    store = Store()
    subs = store.load_subaccounts()
    if args.location:
        subs = [s for s in subs if s.get("slug") == args.location]
        if not subs:
            print(f"no active subaccount with slug {args.location!r}", file=sys.stderr)
            sys.exit(1)
    subs.sort(key=lambda s: (s.get("name") or s.get("slug") or "").lower())

    start = (date.today() - timedelta(days=args.days)).isoformat()
    end = date.today().isoformat()
    rows: list[dict] = []
    failed: list[str] = []
    for sub in subs:
        label = sub.get("name") or sub.get("slug") or sub["location_id"]
        token = store.get_pit(sub["location_id"])
        if not token:
            print(f"{label}: SKIPPED — no PIT stored", file=sys.stderr)
            failed.append(label)
            continue
        client = GHLClient(token)
        try:
            forms = fetch_forms(client, sub["location_id"])
            per_form, complete = fetch_submission_pages(
                client, sub["location_id"], start, end, args.keep_query)
        except GHLError as exc:
            print(f"{label}: FAILED — {exc}", file=sys.stderr)
            failed.append(label)
            continue
        acct = account_rows(sub, forms, per_form, args.keep_query)
        rows.extend(acct)
        placed = len({r["form_id"] for r in acct if r["status"] == "placed"})
        silent = sum(1 for r in acct if r["status"] == "silent")
        suffix = "" if complete else f" (window truncated at {MAX_SUBMISSION_PAGES * PAGE_LIMIT} submissions)"
        print(f"{label}: {len(forms)} forms — {placed} placed, {silent} silent{suffix}")

    with open(args.out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    placed_total = len({(r["location_id"], r["form_id"])
                        for r in rows if r["status"] == "placed"})
    print(f"\nwrote {len(rows)} rows to {args.out} "
          f"({placed_total} forms with a known page URL)")
    if failed:
        print(f"{len(failed)} account(s) not checked: {', '.join(failed)}",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
