"""Which channel a form belongs to: Facebook lead ad, Facebook or Google ad
form, website form, or standalone link, and the evidence behind the label.

How this fits in
----------------
SSP's Facebook/Instagram and Google campaigns deliver leads into the same
subaccount as the client's website, so "the account has leads" says nothing
about which of those is working. tools/form_activity.py labels every form
with classify() so an AM can see, per account, how much comes from ads and
how much from the client's own site.

Key ideas to understand this file
---------------------------------
* Three kinds of evidence, strongest last in the rules below:
  - the form's NAME (SSP convention: "... - FB Ad", "... - GA Ad",
    "Website / Contact Us"),
  - WHERE it lives (the page URLs its submissions came from: the client's
    main site, a campaign subdomain like promo.<client>.com, or GHL's own
    hosted link),
  - HOW visitors arrived (ad-click markers on each submission: Google
    gclid/gbraid/wbraid or paid utm tags, Facebook fbclid/fbc or facebook
    utm tags; form_history.ad_click extracts the label, never the ids).
* A website form that happens to get Google ad traffic stays a "Website
  form"; the traffic share is reported next to it. Placement answers "what
  is this form", traffic answers "what feeds it".
* Facebook lead ads (instant forms) never touch a web page: GHL records
  their submissions under form ids that are not in Sites > Forms. The
  report finds those ids; here they become "Facebook lead ad" when the
  attribution points at Facebook or nothing points anywhere.
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlsplit

FACEBOOK_LEAD_AD = "Facebook lead ad"
FACEBOOK_AD_FORM = "Facebook ad form"
GOOGLE_AD_FORM = "Google ad form"
WEBSITE_FORM = "Website form"
STANDALONE_LINK = "Standalone form link"
LANDING_PAGE_FORM = "Landing page form"
UNLISTED_FORM = "Unlisted form"
UNKNOWN = "Unknown"

CHANNELS = (FACEBOOK_LEAD_AD, FACEBOOK_AD_FORM, GOOGLE_AD_FORM, WEBSITE_FORM,
            STANDALONE_LINK, LANDING_PAGE_FORM, UNLISTED_FORM, UNKNOWN)

FB_NAME_RE = re.compile(r"\b(fb|facebook|instagram|ig|meta)\b", re.IGNORECASE)
GOOGLE_NAME_RE = re.compile(r"\b(ga|google|adwords|ppc|sem)\b", re.IGNORECASE)
WEBSITE_NAME_RE = re.compile(r"\bwebsite\b", re.IGNORECASE)
FACEBOOK_LABEL_RE = re.compile(r"facebook|instagram|\bfb\b|\bmeta\b", re.IGNORECASE)


def bare_host(url_or_host: str) -> str:
    """Lowercased host without a leading www."""
    text = url_or_host or ""
    host = urlsplit(text if "//" in text else "https://" + text).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_hosted(url: str) -> bool:
    """A GHL-hosted form/survey link rather than a page embedding it."""
    return "/widget/form/" in url or "/widget/survey/" in url


def name_hint(name: str) -> str:
    """'facebook' / 'google' / 'website' from SSP's naming convention."""
    if FB_NAME_RE.search(name or ""):
        return "facebook"
    if GOOGLE_NAME_RE.search(name or ""):
        return "google"
    if WEBSITE_NAME_RE.search(name or ""):
        return "website"
    return ""


def placement(urls: list[str], client_host: str) -> tuple[str, str]:
    """(placement key, host) from the page URLs a form was used on, most
    frequent host winning: hosted / website / subdomain / other / ''."""
    if not urls:
        return "", ""
    hosted = sum(1 for u in urls if is_hosted(u))
    if hosted * 2 >= len(urls):
        return "hosted", bare_host(next(u for u in urls if is_hosted(u)))
    host = Counter(bare_host(u) for u in urls if not is_hosted(u)).most_common(1)[0][0]
    if client_host and host == client_host:
        return "website", host
    if client_host and host.endswith("." + client_host):
        return "subdomain", host
    return "other", host


def traffic(records: list[dict]) -> tuple[str, str]:
    """('google' | 'facebook' | 'mixed' | '', human summary) from the ad-click
    markers on real submissions."""
    real = [r for r in records if not r.get("check")]
    if not real:
        return "", ""
    ads = Counter(r.get("ad") for r in real if r.get("ad"))
    n = len(real)
    google, facebook = ads.get("google", 0), ads.get("facebook", 0)
    summary = f"{google} of {n} recent submissions from Google ad clicks, {facebook} from Facebook"
    if google * 2 >= n and google:
        return "google", summary
    if facebook * 2 >= n and facebook:
        return "facebook", summary
    return "mixed", summary


def classify(kind: str, name: str, records: list[dict], client_website: str = "",
             crawl_pages: list[str] | None = None, labels: dict | None = None
             ) -> dict:
    """{channel, placement, traffic, evidence} for one form.

    `records` are form_history.project() rows (newest submissions); for
    kind == 'unlisted', `labels` is {GHL attribution label: count}.
    `crawl_pages` are pages the site crawl found the form on, used when
    submissions recorded no page."""
    client_host = bare_host(client_website) if client_website else ""
    urls = [r["page_url"] for r in records if not r.get("check") and r.get("page_url")]
    place, host = placement(urls or list(crawl_pages or []), client_host)
    flow, flow_summary = traffic(records)
    hint = name_hint(name)
    evidence: list[str] = []

    if kind == "unlisted":
        label_text = " ".join(labels or {})
        if not urls or FACEBOOK_LABEL_RE.search(label_text) or flow == "facebook":
            evidence.append("not in Sites > Forms and "
                            + ("attributed to Facebook" if FACEBOOK_LABEL_RE.search(label_text)
                               else "no web page recorded"))
            return {"channel": FACEBOOK_LEAD_AD, "placement": "inside Facebook/Instagram",
                    "traffic": flow, "evidence": "; ".join(evidence)}
        evidence.append(f"not in Sites > Forms; used on {host}")
        return {"channel": UNLISTED_FORM, "placement": place, "traffic": flow,
                "evidence": "; ".join(evidence)}

    if hint:
        evidence.append(f"name says {hint}")
    if host:
        where = {"website": "the client's main site", "subdomain": "a campaign subdomain",
                 "hosted": "GHL's hosted link", "other": "another site"}.get(place, "")
        evidence.append(f"used on {host}" + (f" ({where})" if where else ""))
    if flow_summary:
        evidence.append(flow_summary)

    if hint == "google":
        channel = GOOGLE_AD_FORM
    elif hint == "facebook":
        channel = FACEBOOK_AD_FORM
    elif hint == "website" or place == "website":
        channel = WEBSITE_FORM
    elif place == "hosted":
        channel = STANDALONE_LINK
    elif place in ("subdomain", "other"):
        channel = {"google": GOOGLE_AD_FORM, "facebook": FACEBOOK_AD_FORM}.get(flow, LANDING_PAGE_FORM)
    else:
        channel = UNKNOWN
        evidence.append("no submissions or page to judge by")
    return {"channel": channel, "placement": place, "traffic": flow,
            "evidence": "; ".join(evidence)}
