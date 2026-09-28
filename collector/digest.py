"""Monday digest email per AM (spec Tier 2), sent through SSP's own SMTP.

Pure template fill — no LLM anywhere near the numbers. Digests go only to
@smallscreenproducer.com addresses, and contain only what the dashboard
already shows: account names, counts, amounts, and flag actions.

Requires SMTP_USER and SMTP_PASS (the same Google Workspace account + app
password the Supabase auth mailer uses); without them the digest is skipped
with a log line, never an error. SMTP_USER is the sender, not a recipient.
Optional: SMTP_HOST (default smtp.gmail.com), SMTP_PORT (default 465),
DIGEST_FROM (default SMTP_USER), DIGEST_CC (comma list CC'd on every digest,
e.g. a manager who wants the whole picture), and DASHBOARD_URL for the link
in the footer.

Who receives it (Recipients below) is opt-in, and nobody is the default:
DIGEST_REDIRECT sends every AM's digest to one address, labelled with the AM
it was meant for (the rehearsal setting); otherwise only AMs listed in
DIGEST_ALLOWLIST get theirs. Added 2026-09-28 after a manual Monday run
mailed the AMs before the pilot had started.

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
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

STAFF_DOMAIN = "@smallscreenproducer.com"
# Exactly one bare address at exactly our domain. A suffix check on the raw
# string is not enough: "x@evil.example, y@smallscreenproducer.com" ends with
# the domain, and the mail library would send to both (SECURITY-SPEC SEC-03).
_STAFF_ADDRESS = re.compile(r"[a-z0-9._%+-]+@smallscreenproducer\.com")


def staff_address(raw) -> str | None:
    """The staff address in `raw`, lowercased, or None if `raw` is anything
    other than one plain @smallscreenproducer.com address (no second
    address, no separators, no display name). Every outbound recipient in
    the collector goes through this: digest To and CC, and the AM notes."""
    if not isinstance(raw, str):
        return None
    address = raw.strip().lower()
    return address if _STAFF_ADDRESS.fullmatch(address) else None


@dataclass(frozen=True)
class Recipients:
    """Who may receive a digest, read from the environment at send time.

    Nobody, unless someone chose otherwise. The team decides when AMs start
    getting mail, not the calendar: before this gate, any run on a Monday
    mailed every AM (2026-09-28, a manual run did exactly that).

    redirect   DIGEST_REDIRECT: one staff address that receives every
               digest instead of its AM, subject labelled "[for Lauren]",
               no CC. The rehearsal setting. When set, the allowlist is not
               consulted: only this one address gets mail.
    allowlist  DIGEST_ALLOWLIST: comma list of staff addresses. With no
               redirect, an AM gets their digest only when listed, and a
               DIGEST_CC address is kept only when listed. Empty = nobody.
    problems   malformed entries. Any problem stops every send (fail closed).
    """
    redirect: str
    allowlist: frozenset
    problems: tuple

    @classmethod
    def from_env(cls, env=None) -> "Recipients":
        env = env if env is not None else os.environ
        raw_redirect = (env.get("DIGEST_REDIRECT") or "").strip()
        redirect = staff_address(raw_redirect) or ""
        entries = [a for a in (env.get("DIGEST_ALLOWLIST") or "").split(",") if a.strip()]
        bad = sum(1 for a in entries if staff_address(a) is None)
        problems = []
        if raw_redirect and not redirect:
            problems.append("DIGEST_REDIRECT is not a single staff address")
        if bad:
            problems.append(f"DIGEST_ALLOWLIST has {bad} entry(ies) that are not a single "
                            "staff address")
        allowlist = frozenset(filter(None, (staff_address(a) for a in entries)))
        return cls(redirect, allowlist, tuple(problems))

    def destination(self, am: str) -> str | None:
        """Where the digest for AM address `am` goes, or None (held)."""
        if self.problems:
            return None
        if self.redirect:
            return self.redirect
        return am if am in self.allowlist else None

    def reaches(self, am) -> bool:
        """True when AM `am` reads their own digest (not a rehearsal copy).
        main.py uses it so cleared lines count as told only to that AM."""
        am = staff_address(am)
        return am is not None and self.destination(am) == am
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://mlhaccountreports.netlify.app")
SMTP_HOST_DEFAULT = "smtp.gmail.com"
SMTP_PORT_DEFAULT = 465


# Plain-English names for flag codes, used wherever a week-over-week diff
# would otherwise print CONVOS_WAITING at an account manager. An unknown code
# degrades to lower-cased words rather than shouting an identifier.
_CODE_LABELS = {
    "INTEGRATION_SUSPECT": "nothing flowing at all",
    "LEADS_ZERO": "zero leads this week",
    "FORM_SILENT": "forms silent",
    "LEADS_DROP": "leads down vs baseline",
    "LEADS_DROP_SEASONAL": "seasonal lead dip",
    "SOURCE_DROP": "a lead source dropped",
    "UNASSIGNED_LEADS": "new leads with no owner",
    "LEADS_UNREACHABLE": "leads missing phone numbers",
    "SLOW_RESPONSE": "leads sitting uncontacted",
    "CONVOS_WAITING": "inbound conversations waiting",
    "PIPELINE_HYGIENE": "pipeline needs a cleanup",
    "STALE_PIPELINE": "stale deals piling up",
    "PIPELINE_FROZEN": "pipeline not moving",
    "PIPELINE_BOTTLENECK": "money parked in one stage",
    "HIGH_NOSHOW": "high appointment no-shows",
    "NO_DELIVERY": "nothing published recently",
    "SOCIAL_DISCONNECTED": "social account disconnected",
    "NO_CLIENT_TOUCH": "no recent client contact",
    "RENEWAL_SOON": "renewal approaching",
    "REVIEW_ASK_GAP": "wins missing review asks",
    "WORKFLOWS_NONE_PUBLISHED": "no published workflows",
    "FORM_WENT_SILENT": "a form went silent",
    "SURVEY_WENT_SILENT": "a survey went silent",
    "FORM_CHECK_MISSED": "weekly form test didn't arrive",
    "NOT_WORKING_IN_MLH": "leads not worked in MLH",
}


def _label(code: str) -> str:
    """Human words for a flag code; unknown codes degrade gracefully."""
    return _CODE_LABELS.get(code, code.replace("_", " ").lower())


def _fmt_money(value) -> str:
    """Format a number as $1,234; bad/missing input becomes "$?" not a crash."""
    try:
        return f"${value:,.0f}"
    except (TypeError, ValueError):
        return "$?"


def _account_summary(sub: dict, snapshot: dict | None, flags: list[dict],
                     acked_codes: set[str]) -> tuple[str, dict]:
    """(state, info) for one account in an AM's digest.

    State is one of "no_data", "steady", "steady_flagged", or "attention";
    build_digests uses it to pick a section. info is the structured record
    both renderers consume — text and HTML are projections of the same data,
    which is what keeps them from drifting apart. Every red/amber flag is
    included, reds first: the digest is the complete picture, not a teaser
    for the dashboard.
    """
    name = sub.get("name") or sub.get("slug") or sub.get("location_id")
    info = {"name": name, "loc": sub.get("location_id") or "", "mrr": sub.get("mrr"),
            "flags": [], "new": [], "resolved": []}
    # No trustworthy snapshot today (broken token, or the gate held it) —
    # surface that fact rather than showing stale or empty numbers.
    if sub.get("token_status") != "ok" or not snapshot or not snapshot.get("gate_passed"):
        return "no_data", info

    # Only unacked red/amber flags count; snoozed codes were already seen.
    unacked = [f for f in flags if f.get("code") not in acked_codes
               and f.get("severity") in ("red", "amber")]
    reds = [f for f in unacked if f.get("severity") == "red"]
    if not unacked:
        return "steady", info

    ordered = sorted(unacked, key=lambda f: 0 if f.get("severity") == "red" else 1)
    info["flags"] = [(f.get("severity"), f.get("action") or f.get("code") or "")
                     for f in ordered]
    info["new"] = snapshot.get("flags_new") or []
    info["resolved"] = snapshot.get("flags_resolved") or []
    # Chart inputs (may be None on older snapshots — renderers skip then).
    info["opps_open"] = snapshot.get("opps_open")
    info["opps_stale"] = snapshot.get("opps_stale")
    info["opps_moved_30d"] = snapshot.get("opps_moved_30d")
    info["speed_median_min"] = snapshot.get("speed_to_lead_median_min")
    info["uncontacted_24h"] = snapshot.get("leads_uncontacted_24h")
    return ("attention" if reds or len(unacked) >= 2 else "steady_flagged"), info


def _text_block(info: dict) -> list[str]:
    """One account's plain-text lines (the format the first digests used)."""
    lines = [f"- {info['name']}"
             + (f" (MRR {_fmt_money(info['mrr'])})" if info["mrr"] is not None else "")]
    for severity, action in info["flags"]:
        marker = "RED" if severity == "red" else "amber"
        lines.append(f"    [{marker}] {action}")
    if info.get("opps_open"):
        lines.append(f"    pipeline: {info['opps_stale'] or 0} of {info['opps_open']} "
                     f"open deals idle 14d+; {info['opps_moved_30d'] or 0} moved in 30d")
    if info.get("speed_median_min") is not None or info.get("uncontacted_24h"):
        lines.append(f"    response: first touch {_fmt_minutes(info.get('speed_median_min'))}; "
                     f"{info.get('uncontacted_24h') or 0} leads without follow-up in 24h+")
    if info["new"] or info["resolved"]:
        changed = []
        if info["new"]:
            changed.append("new: " + ", ".join(_label(c) for c in info["new"]))
        if info["resolved"]:
            changed.append("resolved: " + ", ".join(_label(c) for c in info["resolved"]))
        lines.append("    changed this week — " + "; ".join(changed))
    return lines


# -- HTML rendering ---------------------------------------------------------
#
# Email clients are a hostile rendering target: Gmail strips <style> blocks,
# Outlook renders with Word's engine (no flexbox, no grid, patchy CSS), and
# dark-mode clients recolor at will. So the HTML below is 2005-vintage on
# purpose: nested tables, every style inline, solid hex colors, system fonts,
# one 600px column. Palette mirrors the dashboard.

_INK = "#1d2b32"; _BODY_TX = "#3c4a50"; _MUTED = "#5b6a70"; _FAINT = "#8a979c"
_LINE = "#e3eae9"; _PAPER = "#eef2f1"
_RED = "#c43c3c"; _RED_SOFT = "#fbeaea"
_AMBER = "#a86f0a"; _AMBER_SOFT = "#faf0da"
_GREEN = "#0c7a3c"; _GREEN_SOFT = "#e2f3e8"
_ACCENT = "#2a78d6"
# Deliberately terse: email clients ignore inheritance so this repeats on
# ~300 elements, and Gmail CLIPS any message over ~102KB — which silently
# amputates the bottom of the digest. Byte budget beats Segoe UI.
_FONT = "Arial,sans-serif"


def _pill(severity: str) -> str:
    """Severity badge that survives every client: solid bg, white text."""
    color = _RED if severity == "red" else _AMBER
    label = "RED" if severity == "red" else "AMBER"
    return (f'<span style="display:inline-block;font-family:{_FONT};font-size:10px;'
            f'font-weight:700;letter-spacing:1px;color:#fff;'
            f'background-color:{color};border-radius:9px;padding:2px 8px;">{label}</span>')


def _stat_tile(count: int, label: str, color: str, soft: str) -> str:
    return (f'<td width="32%" align="center" valign="top" '
            f'style="background-color:{soft};border-radius:8px;padding:14px 6px 12px;">'
            f'<div style="font-family:{_FONT};font-size:26px;font-weight:700;'
            f'color:{color};line-height:1;">{count}</div>'
            f'<div style="font-family:{_FONT};font-size:10px;letter-spacing:1.2px;'
            f'text-transform:uppercase;color:{_MUTED};padding-top:6px;">{label}</div></td>')


def _section_heading(text: str, color: str, rule: str) -> str:
    return (f'<tr><td style="padding:22px 32px 2px;">'
            f'<div style="font-family:{_FONT};font-size:12px;letter-spacing:1.5px;'
            f'text-transform:uppercase;color:{color};font-weight:700;'
            f'border-bottom:2px solid {rule};padding-bottom:6px;">{text}</div></td></tr>')


def _fmt_minutes(minutes) -> str:
    """Humanize a minutes value: <1 min / 12 min / 3.4h / 2.1d; '?' if unknown."""
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return "?"
    if m < 1:
        return "&lt;1 min" if False else "<1 min"
    if m < 90:
        return f"{m:.0f} min"
    if m < 2880:
        return f"{m / 60:.1f}h"
    return f"{m / 1440:.1f}d"


def _pipeline_bar(info: dict) -> str:
    """Idle-share bar: what fraction of open deals sat untouched 14d+.

    A stacked email-safe bar (two colored table cells) beats any number here:
    Flohr at 99% idle and Olympic at 36% idle look instantly different. Fill
    goes red when nothing at all moved in 30 days on a real book — the frozen
    case — else amber. Skipped when the account has no open deals.
    """
    open_ct = info.get("opps_open") or 0
    if not open_ct:
        return ""
    stale = min(info.get("opps_stale") or 0, open_ct)
    moved = info.get("opps_moved_30d") or 0
    pct = stale / open_ct * 100.0
    # Keep a sliver of each side visible so 1% and 99% still read as mixed.
    fill = max(2, min(98, round(pct))) if 0 < pct < 100 else round(pct)
    color = _RED if (moved == 0 and open_ct >= 10) else "#d99a2b"
    bar = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
           'style="margin-top:8px;"><tr>')
    if fill:
        bar += f'<td width="{fill}%" height="8" style="background-color:{color};"></td>'
    if fill < 100:
        bar += f'<td width="{100 - fill}%" height="8" style="background-color:{_GREEN_SOFT};"></td>'
    bar += "</tr></table>"
    caption = (f'<div style="font-family:{_FONT};font-size:12px;color:{_MUTED};'
               f'padding-top:4px;">{stale:,} of {open_ct:,} open deals idle 14d+ '
               f'&middot; {moved:,} moved in 30d</div>')
    return bar + caption


def _dot(color: str) -> str:
    return (f'<span style="display:inline-block;width:8px;height:8px;'
            f'border-radius:4px;background-color:{color};"></span>&nbsp;')


def _response_line(info: dict) -> str:
    """First-touch speed and the human follow-up gap, as one dotted line.

    Median first touch is nearly always automation answering in seconds —
    green and reassuring but not the story. The load-bearing number is how
    many leads have had NO follow-up in 24h+; its dot goes amber then red.
    """
    sp = info.get("speed_median_min")
    waiting = info.get("uncontacted_24h")
    if sp is None and not waiting:
        return ""
    parts = []
    if sp is not None:
        sp_color = _GREEN if float(sp) < 5 else (_AMBER if float(sp) < 60 else _RED)
        parts.append(f'{_dot(sp_color)}First touch {_fmt_minutes(sp)}')
    w = waiting or 0
    w_color = _GREEN if w == 0 else (_AMBER if w < 10 else _RED)
    w_text = "no leads waiting 24h+" if w == 0 else f"{w} lead{'s' if w != 1 else ''} without follow-up 24h+"
    parts.append(f"{_dot(w_color)}{w_text}")
    return (f'<div style="font-family:{_FONT};font-size:12px;color:{_BODY_TX};'
            f'padding-top:7px;">' + " &nbsp; ".join(parts) + "</div>")


def _account_card(info: dict) -> str:
    """One needs-attention account: linked name, MRR, pills, changed line."""
    esc = html.escape
    # The SPA routes accounts by location_id (/account/HBhf…), not by slug.
    url = f"{DASHBOARD_URL}/account/{info['loc']}" if info["loc"] else DASHBOARD_URL
    mrr = (f'<td align="right" valign="top" style="font-family:{_FONT};font-size:12px;'
           f'color:{_MUTED};white-space:nowrap;padding-left:10px;">'
           f'MRR {_fmt_money(info["mrr"])}</td>') if info["mrr"] is not None else ""
    rows = [(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="font-family:{_FONT};font-size:15px;font-weight:600;">'
        f'<a href="{url}" style="color:{_ACCENT};text-decoration:none;">'
        f'{esc(info["name"])} &rarr;</a></td>{mrr}</tr></table>')]
    rows.append(_pipeline_bar(info))
    rows.append(_response_line(info))
    for severity, action in info["flags"]:
        rows.append(
            f'<div style="margin-top:7px;font-family:{_FONT};font-size:13px;'
            f'line-height:1.5;color:{_BODY_TX};">{_pill(severity)} {esc(action)}</div>')
    if info["new"] or info["resolved"]:
        parts = []
        if info["new"]:
            parts.append("new: " + ", ".join(html.escape(_label(c)) for c in info["new"]))
        if info["resolved"]:
            parts.append("resolved: " + ", ".join(html.escape(_label(c)) for c in info["resolved"]))
        rows.append(f'<div style="font-family:{_FONT};font-size:12px;color:{_FAINT};'
                    f'padding-top:7px;">This week — {" · ".join(parts)}</div>')
    return ('<tr><td style="padding:14px 32px 14px;border-bottom:1px solid '
            f'{_LINE};">' + "".join(rows) + "</td></tr>")


def _render_html(run_date: str, total: int, attention: list[dict],
                 steady_names: list[str], no_data: list[dict],
                 mrr_at_risk: float, am_name: str = "",
                 cleared: list[tuple[str, str]] | None = None) -> str:
    """The full email body. One 600px card on a soft ground.

    `cleared` is (account name, line) for issues an AM note told this AM
    about that have since cleared (automation.cleared_for_digest)."""
    esc = html.escape
    att_ct, steady_ct, nd_ct = len(attention), len(steady_names), len(no_data)
    preview = f"{att_ct} need attention · {steady_ct} steady · {nd_ct} no data"

    out = [
        f'<div style="margin:0;padding:0;background-color:{_PAPER};">',
        # Hidden preheader: what inboxes show as the preview snippet.
        f'<div style="display:none;max-height:0;overflow:hidden;">{preview}</div>',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background-color:{_PAPER};"><tr><td align="center" style="padding:26px 12px;">',
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="width:600px;max-width:100%;background-color:#fff;'
        f'border:1px solid {_LINE};border-radius:10px;">',
        # Header
        '<tr><td style="padding:26px 32px 0;">'
        f'<div style="font-family:{_FONT};font-size:11px;letter-spacing:2px;'
        f'text-transform:uppercase;color:{_ACCENT};font-weight:600;">SSP Account Health'
        + (f' &middot; {esc(am_name)}' if am_name else '') + '</div>'
        f'<div style="font-family:{_FONT};font-size:22px;font-weight:700;color:{_INK};'
        f'padding-top:6px;">Monday digest &mdash; week of {esc(run_date)}</div>'
        f'<div style="font-family:{_FONT};font-size:13px;color:{_MUTED};padding-top:4px;">'
        f'{total} accounts in your book'
        + (f' &middot; {_fmt_money(mrr_at_risk)} MRR needs attention' if mrr_at_risk else "")
        + "</div></td></tr>",
        # Summary strip
        '<tr><td style="padding:18px 32px 6px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        + _stat_tile(att_ct, "Need attention", _RED if att_ct else _MUTED, _RED_SOFT)
        + '<td width="2%"></td>'
        + _stat_tile(steady_ct, "Steady", _GREEN, _GREEN_SOFT)
        + '<td width="2%"></td>'
        + _stat_tile(nd_ct, "No data", _AMBER if nd_ct else _MUTED, _AMBER_SOFT)
        + "</tr></table></td></tr>",
    ]

    if attention:
        out.append(_section_heading(f"Needs attention ({att_ct})", _RED, "#f3d4d4"))
        out.extend(_account_card(info) for info in attention)
    if steady_names:
        out.append(_section_heading(f"Steady ({steady_ct})", _GREEN, "#cfe9da"))
        out.append('<tr><td style="padding:12px 32px 4px;">'
                   f'<div style="font-family:{_FONT};font-size:13px;line-height:1.8;'
                   f'color:{_MUTED};">'
                   + " &nbsp;&middot;&nbsp; ".join(esc(n) for n in steady_names)
                   + "</div></td></tr>")
    if no_data:
        out.append(_section_heading(f"No data ({nd_ct})", _AMBER, "#ecdcb6"))
        rows = "".join(
            f'<div style="font-family:{_FONT};font-size:13px;color:{_INK};'
            f'background-color:{_AMBER_SOFT};border-radius:6px;padding:9px 12px;'
            f'margin-top:6px;">{esc(info["name"])} '
            f'<span style="color:#8a6a1f;">&mdash; no data (token or gate); '
            f'check the Runs page</span></div>'
            for info in no_data)
        out.append(f'<tr><td style="padding:10px 32px 4px;">{rows}</td></tr>')
    if cleared:
        out.append(_section_heading(f"Cleared since your last alert ({len(cleared)})",
                                    _GREEN, "#cfe9da"))
        rows = "".join(
            f'<div style="font-family:{_FONT};font-size:13px;line-height:1.5;color:{_BODY_TX};'
            f'padding-top:6px;"><span style="color:{_GREEN};font-weight:700;">&#10003;</span>'
            f'&nbsp; <b>{esc(name)}</b>: {esc(line)}</div>' for name, line in cleared)
        out.append(f'<tr><td style="padding:8px 32px 4px;">{rows}</td></tr>')

    out.append(
        '<tr><td align="center" style="padding:26px 32px 8px;">'
        f'<a href="{DASHBOARD_URL}" style="display:inline-block;font-family:{_FONT};'
        f'background-color:{_ACCENT};color:#fff;text-decoration:none;font-weight:600;'
        f'font-size:14px;padding:11px 24px;border-radius:8px;">Open the dashboard</a></td></tr>')
    out.append(
        '<tr><td align="center" style="padding:4px 32px 24px;">'
        f'<div style="font-family:{_FONT};font-size:11px;color:{_FAINT};">'
        'Sent by the Account Health collector &middot; data as of last night&rsquo;s run '
        '&middot; snoozed flags are not shown</div></td></tr>')
    out.append("</table></td></tr></table></div>")
    return "".join(out)


def build_digests(subs: list[dict], snapshots_by_loc: dict[str, dict],
                  flags_by_loc: dict[str, list[dict]],
                  acked_by_loc: dict[str, set[str]],
                  run_date: str,
                  cleared_by_loc: dict[str, list[str]] | None = None) -> dict[str, dict]:
    """{am_email: {subject, text, html}} for every AM with client accounts.
    Non-staff addresses are dropped outright.

    cleared_by_loc: optional {location_id: [cleared lines]} from the AM
    notes (automation.cleared_for_digest). The weekday notes never send a
    "cleared" line on its own, so the digest is where a fix the AM was told
    about gets closed out.

    Pure function — no network, no database. The inputs are exactly what
    store.read_portfolio returns for one date (subs plus per-location
    snapshots, flags, and acked codes), so main.py can pipe one into the
    other; tests build the inputs by hand.
    """
    # Group accounts under each AM's email. The parent (SSP itself) is
    # included on purpose — its own pipeline and forms deserve the same
    # Monday scrutiny as any client's.
    by_am: dict[str, list[dict]] = {}
    for sub in subs:
        if not sub.get("active", True):
            continue
        # Accounts not using MLH stay out of AM mail: the team marked them
        # ads only / not in MLH / canceled (subaccounts.mlh_status), or they
        # have no client staff users, so nobody at the client can act on a
        # CRM alert. Marked accounts are no longer collected at all (see
        # main.run); zero-user ones are, so they come back once someone can
        # log in. Both stay visible behind the portfolio toggle.
        snap = snapshots_by_loc.get(sub.get("location_id")) or {}
        if not sub.get("is_parent") and (
                (sub.get("mlh_status") or "active") != "active" or snap.get("client_users") == 0):
            continue
        am = staff_address(sub.get("am_email"))
        if am is None:
            continue
        by_am.setdefault(am, []).append(sub)

    # Gmail clips messages over ~102KB, silently amputating the bottom of
    # the digest (accounts late in the alphabet just vanish). Budget leaves
    # headroom for quoted-printable encoding growth in transit.
    BYTE_BUDGET = 88_000

    def compose(chunk: list[dict], am_name: str = "") -> dict:
        """Render one email for a subset of an AM's accounts."""
        attention: list[dict] = []
        steady_names: list[str] = []
        no_data: list[dict] = []
        cleared: list[tuple[str, str]] = []
        mrr_at_risk = 0.0
        for sub in chunk:
            loc = sub["location_id"]
            cleared += [(sub.get("name") or sub.get("slug") or loc, line)
                        for line in (cleared_by_loc or {}).get(loc, [])]
            state, info = _account_summary(
                sub, snapshots_by_loc.get(loc), flags_by_loc.get(loc, []),
                acked_by_loc.get(loc, set()))
            if state == "no_data":
                no_data.append(info)
            elif state == "steady":
                steady_names.append(info["name"])
            else:
                attention.append(info)
                if state == "attention" and info["mrr"] is not None:
                    mrr_at_risk += float(info["mrr"])

        # One strictly alphabetical list — Matthew's call: scanning for a
        # specific client beats severity grouping. (Reds still sort first
        # WITHIN each account's card.)
        attention.sort(key=lambda i: (i["name"] or "").lower())

        attention_count = len(attention)
        subject = (f"Account health — {attention_count} need attention"
                   if attention_count else "Account health — all steady")

        # Plain-text body: same shape the first digests used — it is what
        # text-only clients and previews fall back to.
        parts = [
            (f"{am_name} — week of {run_date}. Your book: {len(chunk)} accounts."
             if am_name else
             f"Week of {run_date}. Your book: {len(chunk)} accounts."),
            "",
        ]
        if attention:
            parts.append(f"NEEDS ATTENTION ({attention_count}"
                         + (f", {_fmt_money(mrr_at_risk)} MRR" if mrr_at_risk else "") + "):")
            for info in attention:
                parts.extend(_text_block(info))
            parts.append("")
        if steady_names:
            parts.append(f"Steady ({len(steady_names)}): " + ", ".join(steady_names))
            parts.append("")
        if no_data:
            parts.append("NO DATA:")
            parts.extend(f"- {info['name']}: no data (token or gate) — check /runs"
                         for info in no_data)
            parts.append("")
        if cleared:
            parts.append("CLEARED SINCE YOUR LAST ALERT:")
            parts.extend(f"- {name}: {line}" for name, line in cleared)
            parts.append("")
        parts.append(f"Full picture: {DASHBOARD_URL}")
        text = "\n".join(parts)
        html_body = _render_html(run_date, len(chunk), attention,
                                 steady_names, no_data, mrr_at_risk, am_name, cleared)
        return {"subject": subject, "text": text, "html": html_body}

    digests: dict[str, dict] = {}
    for am, accounts in sorted(by_am.items()):
        ordered = sorted(accounts, key=lambda s: (s.get("name") or "").lower())
        # One mailbox can cover accounts labelled slightly differently
        # ("Michael" and "Michael / Dada (FB)"); the shortest label is the
        # person, which is what belongs in a greeting.
        names = {(s.get("am_name") or "").strip() for s in ordered}
        names.discard("")
        am_name = min(names, key=len) if names else ""
        message = compose(ordered, am_name)
        if len(message["html"].encode()) > BYTE_BUDGET and len(ordered) > 1:
            # Too big for one email: split into alphabetical parts, each under
            # budget, so no account is ever silently clipped away. Greedy
            # packing — grow a chunk until adding one more account overflows.
            chunks: list[list[dict]] = []
            current: list[dict] = []
            for sub in ordered:
                current.append(sub)
                if (len(current) > 1
                        and len(compose(current, am_name)["html"].encode()) > BYTE_BUDGET):
                    current.pop()
                    chunks.append(current)
                    current = [sub]
            if current:
                chunks.append(current)
            messages = [compose(c, am_name) for c in chunks]
            total = len(messages)
            for i, msg in enumerate(messages, 1):
                msg["subject"] = msg["subject"].replace(
                    "Account health", f"Account health ({i}/{total})", 1)
            message = {**messages[0], "extra_parts": messages[1:]}
        # Kept beside the email so a redirected copy can say whose it is.
        message["am_name"] = am_name
        digests[am] = message
    return digests


def _staff_addresses(raw: str) -> list[str]:
    """Comma-separated list (DIGEST_CC) reduced to valid staff addresses."""
    return [addr for addr in (staff_address(part) for part in (raw or "").split(","))
            if addr]


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


def send_digests(digests: dict[str, dict], log=print, env=None) -> tuple[int, int]:
    """Send each digest over SMTP. Returns (sent, failed).

    The only networked function in the module — called by main.py on Mondays
    (or --digest). Nothing leaves unless Recipients allows it: with neither
    DIGEST_REDIRECT nor DIGEST_ALLOWLIST set, every digest is held and the
    log says so. Missing SMTP config is a logged no-op too, not an error, so
    environments without email (local dev, staging) collect normally. One
    connection is reused across messages (with a single reconnect attempt if
    the server drops it mid-batch); one AM's failed email never blocks the
    others. Non-staff recipients are refused here as well as in
    build_digests — belt and braces around client data.
    """
    env = env if env is not None else os.environ
    rules = Recipients.from_env(env)
    if rules.problems:
        for problem in rules.problems:
            log(f"digest: {problem}; nothing sent")
        return 0, 0
    if not rules.redirect and not rules.allowlist:
        log(f"digest: {len(digests)} built, none sent (set DIGEST_REDIRECT or "
            "DIGEST_ALLOWLIST to choose who receives it)")
        return 0, 0
    user = env.get("SMTP_USER")
    password = env.get("SMTP_PASS")
    if not user or not password:
        log("digest: SMTP_USER / SMTP_PASS not set — skipping send")
        return 0, 0
    host = env.get("SMTP_HOST", SMTP_HOST_DEFAULT)
    try:
        port = int(env.get("SMTP_PORT", SMTP_PORT_DEFAULT))
    except ValueError:
        port = SMTP_PORT_DEFAULT
    from_addr = env.get("DIGEST_FROM") or user
    cc_listed = _staff_addresses(env.get("DIGEST_CC", ""))
    # A redirect is a rehearsal: one reader, no copies. Otherwise a CC
    # address needs to be on the allowlist like everyone else.
    cc = [] if rules.redirect else [a for a in cc_listed if a in rules.allowlist]
    if len(cc) < len(cc_listed):
        log(f"digest: {len(cc_listed) - len(cc)} DIGEST_CC address(es) left off "
            + ("(redirected)" if rules.redirect else "(not on DIGEST_ALLOWLIST)"))

    sent = failed = 0
    server: smtplib.SMTP | None = None
    for key, first in digests.items():
        am = staff_address(key)
        if am is None:
            log(f"digest: {key} skipped (not a staff address)")
            continue
        to = rules.destination(am)
        if to is None:
            log(f"digest: {am} held (not on DIGEST_ALLOWLIST)")
            continue
        # A redirected copy names the AM it was written for.
        label = "" if to == am else f"[for {first.get('am_name') or am}] "
        copies = [a for a in cc if a != to]
        # An oversized book arrives as numbered parts (see build_digests's
        # BYTE_BUDGET) — each part is its own email so Gmail never clips.
        every_part = [first] + list(first.get("extra_parts") or [])
        if len(every_part) > 1:
            log(f"digest: {am} split into {len(every_part)} parts (size)")
        for message in every_part:
            msg = EmailMessage()
            msg["From"] = from_addr
            msg["To"] = to
            if copies:
                msg["Cc"] = ", ".join(copies)
            msg["Subject"] = label + message["subject"]
            msg.set_content(message["text"])
            msg.add_alternative(message["html"], subtype="html")
            try:
                if server is None:
                    server = _connect(host, port, user, password)
                server.send_message(msg)
                sent += 1
                log(f"digest: sent to {to}" + (f" (for {am})" if label else ""))
            except (smtplib.SMTPException, OSError):
                # The server may have dropped an idle connection — reconnect
                # once for this message; a second failure counts as failed and
                # the next message starts fresh.
                with contextlib.suppress(smtplib.SMTPException, OSError):
                    if server is not None:
                        server.quit()
                server = None
                try:
                    server = _connect(host, port, user, password)
                    server.send_message(msg)
                    sent += 1
                    log(f"digest: sent to {to} (after reconnect)"
                        + (f" (for {am})" if label else ""))
                except (smtplib.SMTPException, OSError) as exc:
                    failed += 1
                    log(f"digest: {to} failed ({type(exc).__name__})")
                    server = None
    if server is not None:
        with contextlib.suppress(smtplib.SMTPException, OSError):
            server.quit()
    return sent, failed
