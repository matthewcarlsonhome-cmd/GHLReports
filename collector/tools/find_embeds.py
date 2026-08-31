"""Crawl client websites to find where GHL forms are actually embedded.

    python -m collector.tools.find_embeds --targets targets.csv
    python -m collector.tools.find_embeds --targets targets.csv --sites sites.csv
    python -m collector.tools.find_embeds --targets targets.csv --location pettis

Reads a target list of form/survey IDs (CSV with at least slug,form_id —
e.g. exported from the form-inventory workbook's Active/Silent sheets),
crawls each account's public website, and writes embeds.csv mapping each
form to the page URL(s) whose HTML embeds it.

How this fits in
----------------
GHL never records where a form got embedded, and a silent form has no
submissions to infer a page from (tools/form_urls.py covers forms that DO
submit). But every GHL embed is an iframe whose src contains
/widget/form/<id> (or /widget/survey/<id>), so the client's own site is the
source of truth: read the sitemap, fetch the pages, scan the HTML. Websites
come from subaccounts.tag_config->website (the tag checker's config) unless
a --sites CSV overrides them. Public pages only; nothing touches GHL.

Key ideas to understand this file
---------------------------------
* Injectable fetch: every HTTP GET goes through one `fetch(url)` function so
  tests swap in a dict-backed fake. The real one is stdlib urllib with an
  honest User-Agent and a per-request delay — this is someone else's server.
* Page discovery: robots.txt Sitemap lines, then common sitemap paths
  (recursing into sitemap indexes), else a same-host BFS from the homepage.
  Pages are scanned candidates-first (contact/quote/schedule/... URLs) so
  the cap spends its budget where forms live.
* A page can also contain a NATIVE (non-GHL) form; embeds.csv notes pages
  where a <form> exists but no target GHL widget does — the usual reason a
  "silent" form is silent is that the site's form simply isn't the GHL one.
* Output statuses per target form: found (one row per page), not_found.
  Extra rows: unlisted (a GHL widget id on the site that isn't in the
  target list), native_form_page (candidate page with only a non-GHL form).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from urllib.parse import urljoin, urlsplit

USER_AGENT = "SSP-FormAudit/1.0 (+https://www.smallscreenproducer.com)"
TIMEOUT = 15
MAX_PAGES_DEFAULT = 150
BFS_DEPTH = 2

# GHL widget embeds, any host (api.leadconnectorhq.com, link.msgsndr.com,
# whitelabel domains). The id charset is GHL's usual base62-ish key.
WIDGET_RE = re.compile(r"/widget/(form|survey)/([A-Za-z0-9_-]{8,40})")
# Forms on GHL-hosted funnel pages render INLINE, not as widget iframes:
# the form id shows up in element ids (el_<formId>_<field>) and in the
# page's packed JSON ("formId":"<id>"). Both patterns require the id to be
# 15+ chars so generic el_* element ids don't false-positive.
INLINE_EL_RE = re.compile(r"\bel_([A-Za-z0-9]{15,40})_")
FORMID_JSON_RE = re.compile(r"""formId["']?\s*[:=]\s*["']([A-Za-z0-9_-]{15,40})""")
# AMP builds serialize the iframe differently: the id survives as an
# inline-<id> element id or a data-form-id attribute (seen live on
# texaspoolsandpatios.com). One caveat this scanner can't fix: builders
# that inject the embed purely client-side (seen on a Wix site) leave no
# trace in raw HTML — those need a rendered-browser check.
INLINE_ID_RE = re.compile(r"""\binline-([A-Za-z0-9_-]{15,40})""")
DATA_FORM_ID_RE = re.compile(r"""data-form-id=["']([A-Za-z0-9_-]{15,40})["']""")
# A native HTML form on the page (crude on purpose; search boxes are
# filtered out by requiring a method=post or several input fields nearby).
NATIVE_FORM_RE = re.compile(r"<form\b[^>]*method=[\"']?post", re.IGNORECASE)
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
HREF_RE = re.compile(r"""href=["']([^"'#]+)["']""", re.IGNORECASE)
SITEMAP_LINE_RE = re.compile(r"^sitemap:\s*(\S+)", re.IGNORECASE | re.MULTILINE)

# URL substrings that mark pages likely to carry a form — scanned first.
CANDIDATE_WORDS = (
    "contact", "quote", "request", "schedule", "appointment", "consult",
    "estimate", "financ", "brochure", "coupon", "special", "free", "book",
    "career", "employ", "newsletter", "warranty", "water-test", "opening",
    "closing", "design", "build", "renovat", "service", "repair", "form",
    "get-started", "thank", "landing", "offer", "signup", "sign-up",
)

SKIP_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf",
                   ".zip", ".mp4", ".mp3", ".css", ".js", ".ico", ".xml")

CSV_COLUMNS = ["account", "slug", "status", "kind", "form_name", "form_id",
               "result", "page_url", "note"]


def real_fetch(url: str, delay: float = 0.4) -> str | None:
    """GET one URL, returning body text or None. Sleeps `delay` first —
    the crawler is a guest on client infrastructure."""
    time.sleep(delay)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read(2_000_000)  # 2MB cap per page
        return body.decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def site_base(website: str) -> str:
    """https://host from whatever is stored (path, trailing slash, etc.)."""
    parts = urlsplit(website if "//" in website else "https://" + website)
    return f"https://{parts.netloc.lower()}"


def same_host(url: str, base: str) -> bool:
    host = urlsplit(url).netloc.lower()
    want = urlsplit(base).netloc.lower()
    return host == want or host == "www." + want or "www." + host == want


def is_page_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return not path.endswith(SKIP_EXTENSIONS)


def discover_sitemap_urls(base: str, fetch) -> list[str]:
    """Page URLs from robots.txt sitemaps or common sitemap paths, recursing
    one level into sitemap indexes. Empty list = no usable sitemap."""
    sitemaps: list[str] = []
    robots = fetch(base + "/robots.txt")
    if robots:
        sitemaps += SITEMAP_LINE_RE.findall(robots)
    if not sitemaps:
        sitemaps = [base + p for p in
                    ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")]
    pages: list[str] = []
    seen_maps: set[str] = set()
    queue = list(dict.fromkeys(sitemaps))
    while queue:
        sm_url = queue.pop(0)
        if sm_url in seen_maps or len(seen_maps) > 30:
            continue
        seen_maps.add(sm_url)
        body = fetch(sm_url)
        if not body:
            continue
        locs = LOC_RE.findall(body)
        if "<sitemapindex" in body:
            queue.extend(locs)
        else:
            pages.extend(locs)
    # Dedupe, same host only, drop assets.
    out: list[str] = []
    seen: set[str] = set()
    for url in pages:
        url = url.strip()
        if url not in seen and same_host(url, base) and is_page_url(url):
            seen.add(url)
            out.append(url)
    return out


def bfs_urls(base: str, fetch, cap: int) -> list[str]:
    """Fallback discovery: follow same-host links from the homepage,
    BFS_DEPTH levels deep, up to `cap` pages fetched."""
    seen: set[str] = {base + "/"}
    order: list[str] = [base + "/"]
    frontier = [base + "/"]
    fetched = 0
    for _ in range(BFS_DEPTH):
        nxt: list[str] = []
        for url in frontier:
            if fetched >= cap:
                return order
            body = fetch(url)
            fetched += 1
            if not body:
                continue
            for href in HREF_RE.findall(body):
                link = urljoin(url, href).split("#")[0]
                if (link not in seen and same_host(link, base)
                        and is_page_url(link) and link.startswith("http")):
                    seen.add(link)
                    order.append(link)
                    nxt.append(link)
        frontier = nxt
    return order


def candidate_rank(url: str) -> tuple[int, int]:
    """Sort key: candidate pages first (0), then everything else; short
    paths (closer to the site root) before deep ones."""
    path = urlsplit(url).path.lower()
    is_candidate = any(w in path for w in CANDIDATE_WORDS) or path in ("", "/")
    trimmed = path.strip("/")
    depth = 0 if not trimmed else trimmed.count("/") + 1
    return (0 if is_candidate else 1, depth)


def scan_page(body: str) -> tuple[set[str], bool]:
    """(GHL form/survey ids in the HTML — widget iframes plus inline funnel
    renders — , page has a native POST form)."""
    ids = {m.group(2) for m in WIDGET_RE.finditer(body)}
    ids |= set(INLINE_EL_RE.findall(body))
    ids |= set(FORMID_JSON_RE.findall(body))
    ids |= set(INLINE_ID_RE.findall(body))
    ids |= set(DATA_FORM_ID_RE.findall(body))
    return ids, bool(NATIVE_FORM_RE.search(body))


def crawl_site(base: str, target_ids: set[str], fetch,
               max_pages: int = MAX_PAGES_DEFAULT,
               log=print) -> tuple[dict[str, list[str]], list[str], str]:
    """Crawl one site. Returns (found: form_id -> [page urls],
    native_form_pages: candidate pages with a POST form but no GHL widget,
    note: how discovery went)."""
    urls = discover_sitemap_urls(base, fetch)
    note = f"sitemap: {len(urls)} pages"
    if not urls:
        urls = bfs_urls(base, fetch, cap=max_pages)
        note = f"no sitemap; crawled {len(urls)} pages from homepage"
    urls = sorted(urls, key=candidate_rank)[:max_pages]
    if len(urls) == max_pages:
        note += f" (scanned first {max_pages})"

    found: dict[str, list[str]] = defaultdict(list)
    native_pages: list[str] = []
    for url in urls:
        body = fetch(url)
        if not body:
            continue
        ids, has_native = scan_page(body)
        for fid in ids:
            found[fid].append(url)
        if has_native and not ids and candidate_rank(url)[0] == 0:
            native_pages.append(url)
    log(f"  {note}; {sum(len(v) for v in found.values())} embed hits, "
        f"{len(native_pages)} native-form pages")
    return dict(found), native_pages, note


def build_rows(targets: list[dict], found: dict[str, list[str]],
               native_pages: list[str], note: str) -> list[dict]:
    """CSV rows for one account's crawl (see module docstring statuses)."""
    rows: list[dict] = []
    listed_ids = set()
    for t in targets:
        listed_ids.add(t["form_id"])
        base_row = {k: t.get(k, "") for k in
                    ("account", "slug", "status", "kind", "form_name", "form_id")}
        pages = found.get(t["form_id"], [])
        if pages:
            for page in pages:
                rows.append({**base_row, "result": "found",
                             "page_url": page, "note": ""})
        else:
            rows.append({**base_row, "result": "not_found",
                         "page_url": "", "note": note})
    context = targets[0] if targets else {}
    for fid, pages in sorted(found.items()):
        if fid in listed_ids:
            continue
        for page in pages:
            rows.append({"account": context.get("account", ""),
                         "slug": context.get("slug", ""), "status": "",
                         "kind": "", "form_name": "(not in target list)",
                         "form_id": fid, "result": "unlisted",
                         "page_url": page, "note": ""})
    for page in native_pages:
        rows.append({"account": context.get("account", ""),
                     "slug": context.get("slug", ""), "status": "", "kind": "",
                     "form_name": "", "form_id": "",
                     "result": "native_form_page", "page_url": page,
                     "note": "candidate page with a non-GHL form"})
    return rows


def load_targets(path: str) -> dict[str, list[dict]]:
    """targets.csv grouped by slug. Needs slug and form_id columns."""
    by_slug: dict[str, list[dict]] = defaultdict(list)
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("slug") and row.get("form_id"):
                by_slug[row["slug"].strip()].append(
                    {k: (v or "").strip() for k, v in row.items()})
    return dict(by_slug)


def load_sites(path: str) -> dict[str, str]:
    """sites.csv → {slug: website}. Needs slug and website columns."""
    with open(path, newline="") as fh:
        return {r["slug"].strip(): r["website"].strip()
                for r in csv.DictReader(fh) if r.get("slug") and r.get("website")}


def sites_from_store() -> dict[str, str]:
    """{slug: website} from subaccounts.tag_config (the tag checker config)."""
    from ..store import Store
    return {s["slug"]: (s.get("tag_config") or {}).get("website")
            for s in Store().load_subaccounts()
            if (s.get("tag_config") or {}).get("website")}


def run_crawl(targets_by_slug: dict[str, list[dict]], sites: dict[str, str],
              fetch=real_fetch, max_pages: int = MAX_PAGES_DEFAULT,
              log=print) -> list[dict]:
    """Crawl every targeted account that has a known website."""
    rows: list[dict] = []
    for slug in sorted(targets_by_slug):
        targets = targets_by_slug[slug]
        website = sites.get(slug)
        label = targets[0].get("account") or slug
        if not website:
            log(f"{label}: no website configured — skipped")
            for t in targets:
                rows.append({**{k: t.get(k, "") for k in
                                ("account", "slug", "status", "kind",
                                 "form_name", "form_id")},
                             "result": "no_website", "page_url": "",
                             "note": "no website in tag_config or --sites"})
            continue
        base = site_base(website)
        log(f"{label}: crawling {base} for {len(targets)} form(s)")
        found, native_pages, note = crawl_site(
            base, {t["form_id"] for t in targets}, fetch,
            max_pages=max_pages, log=log)
        rows.extend(build_rows(targets, found, native_pages, note))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(prog="find_embeds", description=(
        "Crawl client sites for GHL form/survey embeds (public pages only)."))
    parser.add_argument("--targets", required=True,
                        help="CSV of forms to locate (slug,form_id,+optional cols)")
    parser.add_argument("--sites", help="CSV slug,website override; default: "
                                        "subaccounts.tag_config->website")
    parser.add_argument("--location", help="only this slug")
    parser.add_argument("--out", default="embeds.csv", help="output CSV path")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES_DEFAULT,
                        help=f"page cap per site (default {MAX_PAGES_DEFAULT})")
    args = parser.parse_args()

    targets_by_slug = load_targets(args.targets)
    if args.location:
        targets_by_slug = {k: v for k, v in targets_by_slug.items()
                           if k == args.location}
        if not targets_by_slug:
            print(f"no targets for slug {args.location!r}", file=sys.stderr)
            sys.exit(1)
    sites = load_sites(args.sites) if args.sites else sites_from_store()

    rows = run_crawl(targets_by_slug, sites, max_pages=args.max_pages)
    with open(args.out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    found = len({(r["slug"], r["form_id"]) for r in rows if r["result"] == "found"})
    total = sum(len(v) for v in targets_by_slug.values())
    print(f"\nwrote {len(rows)} rows to {args.out} — "
          f"{found} of {total} target forms located")


if __name__ == "__main__":
    main()
