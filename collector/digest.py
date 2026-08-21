"""Monday digest email per AM (spec Tier 2), sent through SSP's own SMTP.

Pure template fill — no LLM anywhere near the numbers. Digests go only to
@smallscreenproducer.com addresses, and contain only what the dashboard
already shows: account names, counts, amounts, and flag actions.

Requires SMTP_USER and SMTP_PASS (the same Google Workspace account + app
password the Supabase auth mailer uses); without them the digest is skipped
with a log line, never an error. Optional: SMTP_HOST (default smtp.gmail.com),
SMTP_PORT (default 465), DIGEST_FROM (default SMTP_USER), DIGEST_CC (comma
list CC'd on every digest — e.g. a manager who wants the whole picture), and
DASHBOARD_URL for the link in the footer.

How this fits in
----------------
main.py calls into this module in two places: automatically at the end of the
Monday collection run, and on demand via ``--digest`` (where ``--dry-run``
prints the emails instead of sending them). Building and sending are split on
purpose: build_digests is a pure function (data in, email dicts out — easy to
test with fakes), while send_digests is the only part that talks to the
network — one SMTP connection, one message per AM.

Key ideas to understand this file
---------------------------------
* One email per AM (account manager), covering only THEIR accounts. The
  grouping key is the subaccount's am_email column.
* Accounts are bucketed into three states: "needs attention" (unacked red
  flags, or several flags), "steady" (no unacked flags worth showing), and
  "no data" (bad token or gate-held snapshot — deliberately loud, since
  silent data gaps are how problems hide).
* Acked flags: an AM can snooze a flag in the dashboard; snoozed codes are
  filtered out here so the digest only nags about NEW information.
* Safety rails: recipients (To and CC alike) must be on the staff domain (a
  typo'd am_email cannot leak client data outside the company), and the body
  is plain numbers/names the dashboard already shows — no LLM-generated prose.
"""

from __future__ import annotations

import contextlib
import html
import os
import smtplib
import ssl
from email.message import EmailMessage

STAFF_DOMAIN = "@smallscreenproducer.com"
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://mlhaccountreports.netlify.app")
SMTP_HOST_DEFAULT = "smtp.gmail.com"
SMTP_PORT_DEFAULT = 465


def _fmt_money(value) -> str:
    """Format a number as $1,234; bad/missing input becomes "$?" not a crash."""
    try:
        return f"${value:,.0f}"
    except (TypeError, ValueError):
        return "$?"


def _account_lines(sub: dict, snapshot: dict | None, flags: list[dict],
                   acked_codes: set[str]) -> tuple[str, list[str]]:
    """(state, detail lines) for one account in an AM's digest.

    State is one of "no_data", "steady", "steady_flagged", or "attention";
    build_digests uses it to pick a section. Lines are pre-indented text
    ready to drop into the plain-text email body.
    """
    name = sub.get("name") or sub.get("slug") or sub.get("location_id")
    # No trustworthy snapshot today (broken token, or the gate held it) —
    # surface that fact rather than showing stale or empty numbers.
    if sub.get("token_status") != "ok" or not snapshot or not snapshot.get("gate_passed"):
        return "no_data", [f"- {name}: no data (token or gate) — check /runs"]

    # Only unacked red/amber flags count; snoozed codes were already seen.
    unacked = [f for f in flags if f.get("code") not in acked_codes and f.get("severity") in ("red", "amber")]
    reds = [f for f in unacked if f.get("severity") == "red"]
    if not unacked:
        return "steady", []

    # Header line (with monthly recurring revenue when known), then at most 3
    # flag actions, reds first, then a "changed this week" line if flags moved.
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
    Non-staff addresses are dropped outright.

    Pure function — no network, no database. The inputs are exactly what
    store.read_portfolio returns for one date (subs plus per-location
    snapshots, flags, and acked codes), so main.py can pipe one into the
    other; tests build the inputs by hand.
    """
    # Group client accounts (never the parent) under each AM's email.
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
        # Sort this AM's accounts into the three buckets, tallying the MRR of
        # accounts that need attention for the subject/summary line.
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

        # Unindented lines are account headers; indented ones are flag detail,
        # so counting headers gives the number of accounts needing attention.
        attention_count = sum(1 for line in attention_blocks if not line.startswith("    "))
        subject = (f"Account health — {attention_count} need attention"
                   if attention_count else "Account health — all steady")

        # Assemble the plain-text body: attention first, then steady one-liner,
        # then the no-data section, then a link to the full dashboard.
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

        # The HTML variant is just the text in a <pre> block (escaped so
        # account names can't inject markup) — same content, monospace look.
        html_body = "<pre style=\"font-family: ui-monospace, monospace; font-size: 13px;\">" \
            + html.escape(text) + "</pre>"
        digests[am] = {"subject": subject, "text": text, "html": html_body}
    return digests


def _staff_addresses(raw: str) -> list[str]:
    """Comma-separated addresses filtered to the staff domain, trimmed."""
    return [a.strip() for a in raw.split(",")
            if a.strip() and a.strip().lower().endswith(STAFF_DOMAIN)]


def _connect(host: str, port: int, user: str, password: str) -> smtplib.SMTP:
    """Open and authenticate one SMTP connection. Port 465 is implicit TLS
    (what Gmail expects); anything else is treated as STARTTLS."""
    context = ssl.create_default_context()
    if port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, context=context, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.starttls(context=context)
    server.login(user, password)
    return server


def send_digests(digests: dict[str, dict], log=print) -> tuple[int, int]:
    """Send each digest over SMTP. Returns (sent, failed).

    The only networked function in the module — called by main.py on Mondays
    (or --digest). Missing SMTP config is a logged no-op, not an error, so
    environments without email (local dev, staging) collect normally. One
    connection is reused across messages (with a single reconnect attempt if
    the server drops it mid-batch); one AM's failed email never blocks the
    others. Non-staff recipients are refused here as well as in
    build_digests — belt and braces around client data.
    """
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    if not user or not password:
        log("digest: SMTP_USER / SMTP_PASS not set — skipping send")
        return 0, 0
    host = os.environ.get("SMTP_HOST", SMTP_HOST_DEFAULT)
    try:
        port = int(os.environ.get("SMTP_PORT", SMTP_PORT_DEFAULT))
    except ValueError:
        port = SMTP_PORT_DEFAULT
    from_addr = os.environ.get("DIGEST_FROM") or user
    cc = _staff_addresses(os.environ.get("DIGEST_CC", ""))

    sent = failed = 0
    server: smtplib.SMTP | None = None
    for to, message in digests.items():
        if not to.strip().lower().endswith(STAFF_DOMAIN):
            log(f"digest: {to} skipped (not a staff address)")
            continue
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = to
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = message["subject"]
        msg.set_content(message["text"])
        msg.add_alternative(message["html"], subtype="html")
        try:
            if server is None:
                server = _connect(host, port, user, password)
            server.send_message(msg)
            sent += 1
            log(f"digest: sent to {to}")
        except (smtplib.SMTPException, OSError):
            # The server may have dropped an idle connection — reconnect once
            # for this message; a second failure counts as failed and the next
            # message starts fresh.
            with contextlib.suppress(smtplib.SMTPException, OSError):
                if server is not None:
                    server.quit()
            server = None
            try:
                server = _connect(host, port, user, password)
                server.send_message(msg)
                sent += 1
                log(f"digest: sent to {to} (after reconnect)")
            except (smtplib.SMTPException, OSError) as exc:
                failed += 1
                log(f"digest: {to} failed ({type(exc).__name__})")
                server = None
    if server is not None:
        with contextlib.suppress(smtplib.SMTPException, OSError):
            server.quit()
    return sent, failed
