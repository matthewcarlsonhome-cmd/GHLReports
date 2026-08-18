"""Monday digest email per AM (spec Tier 2), sent by the collector via Resend.

Pure template fill — no LLM anywhere near the numbers. Digests go only to
@smallscreenproducer.com addresses, and contain only what the dashboard
already shows: account names, counts, amounts, and flag actions.

Requires RESEND_API_KEY and DIGEST_FROM; without them the digest is skipped
with a log line, never an error.
"""

from __future__ import annotations

import html
import os

import requests

RESEND_URL = "https://api.resend.com/emails"
STAFF_DOMAIN = "@smallscreenproducer.com"
DASHBOARD_URL = "https://health.smallscreenproducer.com"


def _fmt_money(value) -> str:
    try:
        return f"${value:,.0f}"
    except (TypeError, ValueError):
        return "$?"


def _account_lines(sub: dict, snapshot: dict | None, flags: list[dict],
                   acked_codes: set[str]) -> tuple[str, list[str]]:
    """(state, detail lines) for one account in an AM's digest."""
    name = sub.get("name") or sub.get("slug") or sub.get("location_id")
    if sub.get("token_status") != "ok" or not snapshot or not snapshot.get("gate_passed"):
        return "no_data", [f"- {name}: no data (token or gate) — check /runs"]

    unacked = [f for f in flags if f.get("code") not in acked_codes and f.get("severity") in ("red", "amber")]
    reds = [f for f in unacked if f.get("severity") == "red"]
    if not unacked:
        return "steady", []

    lines = [f"- {name}" + (f" (MRR {_fmt_money(sub['mrr'])})" if sub.get("mrr") is not None else "")]
    for flag in sorted(unacked, key=lambda f: 0 if f.get("severity") == "red" else 1)[:3]:
        marker = "RED" if flag.get("severity") == "red" else "amber"
        lines.append(f"    [{marker}] {flag.get('action') or flag.get('code')}")
    new_codes = snapshot.get("flags_new") or []
    resolved_codes = snapshot.get("flags_resolved") or []
    if new_codes or resolved_codes:
        changed = []
        if new_codes:
            changed.append("new: " + ", ".join(new_codes))
        if resolved_codes:
            changed.append("resolved: " + ", ".join(resolved_codes))
        lines.append("    changed this week — " + "; ".join(changed))
    return ("attention" if reds or len(unacked) >= 2 else "steady_flagged"), lines


def build_digests(subs: list[dict], snapshots_by_loc: dict[str, dict],
                  flags_by_loc: dict[str, list[dict]],
                  acked_by_loc: dict[str, set[str]],
                  run_date: str) -> dict[str, dict]:
    """{am_email: {subject, text, html}} for every AM with client accounts.
    Non-staff addresses are dropped outright."""
    by_am: dict[str, list[dict]] = {}
    for sub in subs:
        if sub.get("is_parent") or not sub.get("active", True):
            continue
        am = (sub.get("am_email") or "").strip().lower()
        if not am.endswith(STAFF_DOMAIN):
            continue
        by_am.setdefault(am, []).append(sub)

    digests: dict[str, dict] = {}
    for am, accounts in sorted(by_am.items()):
        attention_blocks: list[str] = []
        steady_names: list[str] = []
        no_data_lines: list[str] = []
        mrr_at_risk = 0.0

        for sub in sorted(accounts, key=lambda s: s.get("name") or ""):
            loc = sub["location_id"]
            state, lines = _account_lines(
                sub, snapshots_by_loc.get(loc), flags_by_loc.get(loc, []),
                acked_by_loc.get(loc, set()))
            if state == "no_data":
                no_data_lines.extend(lines)
            elif state == "steady":
                steady_names.append(sub.get("name") or loc)
            else:
                attention_blocks.extend(lines)
                if state == "attention" and sub.get("mrr") is not None:
                    mrr_at_risk += float(sub["mrr"])

        attention_count = sum(1 for line in attention_blocks if not line.startswith("    "))
        subject = (f"Account health — {attention_count} need attention"
                   if attention_count else "Account health — all steady")

        parts = [
            f"Week of {run_date}. Your book: {len(accounts)} accounts.",
            "",
        ]
        if attention_blocks:
            parts.append(f"NEEDS ATTENTION ({attention_count}"
                         + (f", {_fmt_money(mrr_at_risk)} MRR" if mrr_at_risk else "") + "):")
            parts.extend(attention_blocks)
            parts.append("")
        if steady_names:
            parts.append(f"Steady ({len(steady_names)}): " + ", ".join(steady_names))
            parts.append("")
        if no_data_lines:
            parts.append("NO DATA:")
            parts.extend(no_data_lines)
            parts.append("")
        parts.append(f"Full picture: {DASHBOARD_URL}")
        text = "\n".join(parts)

        html_body = "<pre style=\"font-family: ui-monospace, monospace; font-size: 13px;\">" \
            + html.escape(text) + "</pre>"
        digests[am] = {"subject": subject, "text": text, "html": html_body}
    return digests


def send_digests(digests: dict[str, dict], log=print) -> tuple[int, int]:
    """POST each digest to Resend. Returns (sent, failed)."""
    api_key = os.environ.get("RESEND_API_KEY")
    from_addr = os.environ.get("DIGEST_FROM")
    if not api_key or not from_addr:
        log("digest: RESEND_API_KEY / DIGEST_FROM not set — skipping send")
        return 0, 0
    sent = failed = 0
    for to, message in digests.items():
        try:
            resp = requests.post(RESEND_URL, timeout=30, headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }, json={
                "from": from_addr,
                "to": [to],
                "subject": message["subject"],
                "text": message["text"],
                "html": message["html"],
            })
            if resp.status_code < 300:
                sent += 1
                log(f"digest: sent to {to}")
            else:
                failed += 1
                log(f"digest: {to} failed (HTTP {resp.status_code})")
        except requests.RequestException as exc:
            failed += 1
            log(f"digest: {to} failed ({type(exc).__name__})")
    return sent, failed
