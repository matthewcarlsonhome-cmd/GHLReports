"""Is anyone actually working leads in MLH, or is the subaccount running on
ads plus automation alone?

    python -m collector.tools.account_usage                     # whole book
    python -m collector.tools.account_usage --location hamlin   # one account
    python -m collector.main --account-usage                    # Render / Actions

Writes account-usage.csv: one row per subaccount with a usage verdict and
the evidence behind it.

How this fits in
----------------
Leads, deals and replies show up in accounts whose clients never log in:
SSP's standard account setup includes "<Service> - Ads Pipelines" plus
published workflows that take Facebook/Google ad leads, reply instantly
"as" a user, and create deals. A dashboard that counts those as activity
makes an idle client look busy. This report separates what PEOPLE did in
the last 28 days from what automation did, so the team can see which
clients use MLH and gate CRM alerts accordingly.

Key ideas to understand this file
---------------------------------
* Client staff vs SSP staff: GHL users carry roles.type "account" (the
  client's own team) or "agency" (SSP). Only client-staff activity counts
  as the client using MLH; SSP activity is reported separately.
* An outbound message is automated if its source says so (workflow,
  campaign, bulk, api) OR it went out within INSTANT_SECONDS of the lead's
  message or of the conversation starting. The second rule catches
  workflow replies sent under a user's name, which carry a userId and were
  being counted as human replies (the 0-minute "human" first touches seen
  across the book on 2026-09-22).
* message_source_mix reports the raw (source, has-user) pairs of outbound
  messages so the rule above can be checked against live data.
* Deals won or lost, calls placed, and client-staff replies are the human
  evidence. Stage moves are not: workflows move stages too.
* PII boundary: counts, pipeline/workflow names, and the names of the
  client's own users only. No contact names, message text, emails or phones.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from .. import fetchers, metrics
from ..fetchers import Coverage
from ..ghl_client import GHLAuthError, GHLClient, GHLError

WINDOW_DAYS = 28
CONVO_CAP = 60             # recent conversations whose messages are read per account
CONVO_PAGE_CAP = 5         # conversation search pages (100 each)
INSTANT_SECONDS = 60       # outbound this soon after the lead's message = automated reply
WORKING_MESSAGES = 5       # client-staff replies in 28d that count as working in MLH
WORKING_DEALS = 2          # deals won or lost in 28d that count as working in MLH
WORKING_CALLS = 3          # outbound calls in 28d that count as working in MLH
NAMES_CAP = 600            # characters of published workflow names kept per account

ADS_NAME_RE = re.compile(r"\b(fb|facebook|meta|instagram|ig|ga|google|ads?|lead\s?form)\b",
                         re.IGNORECASE)

COLUMNS = [
    "account", "am", "slug", "location_id", "usage", "evidence",
    "client_users", "client_user_names", "ssp_users",
    "leads_28d", "deals_created_28d", "deals_created_in_ads_pipelines_28d",
    "deals_won_28d", "deals_lost_28d", "open_deals", "open_deals_assigned_pct",
    "ads_pipelines", "pipelines", "workflows_published", "workflows_total",
    "ads_workflows_published", "published_workflow_names",
    "outbound_28d", "automated_28d", "instant_replies_28d", "client_staff_messages_28d",
    "ssp_staff_messages_28d", "other_user_messages_28d", "unattributed_28d",
    "client_calls_28d", "appointments_28d", "conversations_sampled", "message_source_mix",
    "error",
]


def split_users(users: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """({client staff id: name}, {SSP staff id: name}); see fetchers.users_by_type."""
    return fetchers.users_by_type(users)


def fetch_workflow_list(client, location_id: str) -> list[dict] | None:
    """[{name, published}] or None when the token lacks workflows.readonly."""
    try:
        data = client.request("GET", "/workflows/", params={"locationId": location_id})
    except GHLError:
        return None
    out = []
    for w in fetchers._first_list(data, "workflows", "list", "data"):
        if not isinstance(w, dict):
            continue
        status = str(w.get("status") or "").lower()
        out.append({"name": str(w.get("name") or "").strip(),
                    "published": status in ("published", "active") or w.get("isActive") is True})
    return out


def classify_messages(convos: list[dict], messages_by_convo: dict[str, list[dict]],
                      client_users: dict, agency_users: dict, since: datetime) -> dict:
    """Tally outbound messages in the window by who (or what) sent them."""
    tally = Counter()
    mix = Counter()
    senders = set()
    created = {c["id"]: metrics.parse_ts(c.get("dateAdded")) for c in convos if c.get("id")}
    for convo_id, messages in messages_by_convo.items():
        ordered = sorted((m for m in messages if metrics.parse_ts(m.get("dateAdded"))),
                         key=lambda m: metrics.parse_ts(m.get("dateAdded")))
        last_inbound = created.get(convo_id)
        for msg in ordered:
            at = metrics.parse_ts(msg.get("dateAdded"))
            direction = str(msg.get("direction") or "").lower()
            if direction == "inbound":
                last_inbound = at
                continue
            if direction != "outbound" or at < since:
                continue
            source = str(msg.get("source") or "").strip().lower()
            uid = msg.get("userId")
            mix[f"{source or 'none'}/{'user' if uid else 'no-user'}"] += 1
            tally["outbound"] += 1
            if ("call" in str(msg.get("type") or "").lower() and uid in client_users
                    and source not in metrics.AUTOMATION_SOURCES):
                tally["calls"] += 1          # a call placed by client staff
            if source in metrics.AUTOMATION_SOURCES:
                tally["automated"] += 1
            elif last_inbound and 0 <= (at - last_inbound).total_seconds() <= INSTANT_SECONDS:
                tally["instant"] += 1
            elif uid and uid in client_users:
                tally["client"] += 1
                senders.add(uid)
            elif uid and uid in agency_users:
                tally["ssp"] += 1
            elif uid:
                tally["other_user"] += 1
            else:
                tally["unattributed"] += 1
    return {"tally": tally, "mix": mix, "senders": senders}


def verdict(client_users: int, leads: int, deals_created: int, won_lost: int,
            client_msgs: int, calls: int, ssp_msgs: int) -> tuple[str, str]:
    """(usage label, plain-language evidence)."""
    evidence = (f"{client_msgs} replies by client staff, {won_lost} deals won/lost, "
                f"{calls} calls by client staff in {WINDOW_DAYS} days; {leads} leads and "
                f"{deals_created} deals arrived")
    if ssp_msgs:
        evidence += f"; SSP staff sent {ssp_msgs} messages"
    if client_users == 0:
        return "No client logins", "no client staff users on the account; " + evidence
    if client_msgs >= WORKING_MESSAGES or won_lost >= WORKING_DEALS or calls >= WORKING_CALLS:
        return "Working in MLH", evidence
    if client_msgs or won_lost or calls:
        return "Light use", evidence
    if leads or deals_created:
        return "Ads + automation only", evidence
    return "Idle", evidence


def collect_account(sub: dict, client, now_utc: datetime, log=print) -> dict:
    """One CSV row for one account. Never raises GHLError."""
    location_id = sub["location_id"]
    row = {"account": sub.get("name") or sub.get("slug") or location_id,
           "am": sub.get("am_name") or "", "slug": sub.get("slug") or "",
           "location_id": location_id, "error": ""}
    cov = Coverage()
    since = now_utc - timedelta(days=WINDOW_DAYS)
    since_ms = int(since.timestamp() * 1000)
    try:
        users = client.request("GET", "/users/", params={"locationId": location_id})
    except GHLError as exc:
        row["error"] = str(exc)
        return row
    client_users, agency_users = split_users(fetchers._first_list(users, "users", "data"))

    pipelines = fetchers.fetch_pipelines(client, cov, location_id)
    ads_pipeline_ids = {str(p.get("id") or p.get("_id")) for p in pipelines
                        if "ads pipeline" in str(p.get("name") or "").lower()}
    opps = fetchers.fetch_opportunities(client, cov, location_id, since_ms)
    contacts = fetchers.fetch_contacts_range(
        client, cov, location_id, since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"))
    workflows = fetch_workflow_list(client, location_id)
    calendars = fetchers.fetch_calendars(client, cov, location_id)
    events = fetchers.fetch_calendar_events(client, cov, location_id, calendars, since_ms,
                                            int(now_utc.timestamp() * 1000))
    convos = fetchers.fetch_recent_conversations(client, cov, location_id, since_ms,
                                                 max_pages=CONVO_PAGE_CAP)
    recent = [c for c in convos if (metrics.parse_ts(c.get("lastMessageDate")) or since) >= since]
    messages_by_convo: dict[str, list[dict]] = {}
    for convo in recent[:CONVO_CAP]:
        try:
            messages_by_convo[convo["id"]] = fetchers.fetch_messages(client, convo["id"], max_pages=1)
        except GHLError:
            continue
    msgs = classify_messages(recent, messages_by_convo, client_users, agency_users, since)
    tally = msgs["tally"]

    def in_window(value) -> bool:
        ts = metrics.parse_ts(value)
        return ts is not None and ts >= since

    created = [o for o in opps if in_window(o.get("createdAt"))]
    won = [o for o in opps if o.get("status") == "won" and in_window(o.get("lastStatusChangeAt"))]
    lost = [o for o in opps if o.get("status") == "lost" and in_window(o.get("lastStatusChangeAt"))]
    open_opps = [o for o in opps if o.get("status") == "open"]
    assigned = sum(1 for o in open_opps if o.get("assignedTo"))
    published = [w for w in (workflows or []) if w["published"]]
    names = "; ".join(sorted(w["name"] for w in published if w["name"]))

    usage, evidence = verdict(len(client_users), len(contacts), len(created),
                              len(won) + len(lost), tally["client"], tally["calls"], tally["ssp"])
    row.update({
        "usage": usage, "evidence": evidence,
        "client_users": len(client_users),
        "client_user_names": "; ".join(sorted(client_users.values())),
        "ssp_users": len(agency_users),
        "leads_28d": len(contacts), "deals_created_28d": len(created),
        "deals_created_in_ads_pipelines_28d": sum(
            1 for o in created if str(o.get("pipelineId")) in ads_pipeline_ids),
        "deals_won_28d": len(won), "deals_lost_28d": len(lost),
        "open_deals": len(open_opps),
        "open_deals_assigned_pct": round(100 * assigned / len(open_opps)) if open_opps else "",
        "ads_pipelines": len(ads_pipeline_ids), "pipelines": len(pipelines),
        "workflows_published": len(published) if workflows is not None else "",
        "workflows_total": len(workflows) if workflows is not None else "",
        "ads_workflows_published": sum(1 for w in published if ADS_NAME_RE.search(w["name"])),
        "published_workflow_names": names[:NAMES_CAP] + ("..." if len(names) > NAMES_CAP else ""),
        "outbound_28d": tally["outbound"], "automated_28d": tally["automated"],
        "instant_replies_28d": tally["instant"],
        "client_staff_messages_28d": tally["client"], "ssp_staff_messages_28d": tally["ssp"],
        "other_user_messages_28d": tally["other_user"], "unattributed_28d": tally["unattributed"],
        "client_calls_28d": tally["calls"], "appointments_28d": len(events),
        "conversations_sampled": len(messages_by_convo),
        "message_source_mix": "; ".join(f"{k}: {v}" for k, v in msgs["mix"].most_common(6)),
    })
    return row


def collect_book(store, subs: list[dict], *, client_factory=GHLClient,
                 now_utc: datetime | None = None, log=print) -> tuple[list[dict], list[str]]:
    """Rows for every account in `subs` (sorted by usage, then name) and the
    labels of accounts that could not be read."""
    now_utc = now_utc or datetime.now(timezone.utc)
    rows: list[dict] = []
    failed: list[str] = []
    for sub in sorted(subs, key=lambda s: (s.get("name") or s.get("slug") or "").lower()):
        label = sub.get("name") or sub.get("slug") or sub["location_id"]
        token = store.get_pit(sub["location_id"])
        if not token:
            log(f"{label}: SKIPPED - no PIT stored")
            failed.append(label)
            continue
        row = collect_account(sub, client_factory(token), now_utc, log=log)
        if row.get("error"):
            log(f"{label}: FAILED - {row['error']}")
            failed.append(label)
        else:
            log(f"{label}: {row['usage']} - {row['evidence']}")
        rows.append(row)
    order = {"Working in MLH": 0, "Light use": 1, "Ads + automation only": 2,
             "No client logins": 3, "Idle": 4}
    rows.sort(key=lambda r: (order.get(r.get("usage"), 9), r["account"].lower()))
    return rows, failed


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(prog="account_usage", description=(
        "Who actually works leads in MLH vs ads + automation only (read-only)."))
    parser.add_argument("--location", help="one slug; default: every active account")
    parser.add_argument("--out", default="account-usage.csv", help="CSV output path")
    args = parser.parse_args()

    from ..store import Store
    store = Store()
    subs = store.load_subaccounts()
    if args.location:
        subs = [s for s in subs if s.get("slug") == args.location]
        if not subs:
            print(f"no active subaccount with slug {args.location!r}", file=sys.stderr)
            sys.exit(1)
    rows, failed = collect_book(store, subs)
    with open(args.out, "w", newline="") as fh:
        fh.write(to_csv(rows))
    print(f"wrote {len(rows)} rows to {args.out}")
    if failed:
        print(f"{len(failed)} account(s) not checked: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
