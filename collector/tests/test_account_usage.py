"""Tests for tools/account_usage.py: telling client staff working leads in
MLH apart from ads + automation running on their own."""

from datetime import datetime, timedelta, timezone

from .. import main as main_mod
from ..tools import account_usage as au
from .fakes import FakeStore

NOW = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


USERS = [{"id": "uClient", "name": "Taylor Hamlin", "email": "t@hamlin.example",
          "roles": {"type": "account", "role": "admin"}},
         {"id": "uSSP", "name": "Lisa Hoffman", "roles": {"type": "agency", "role": "user"}}]


def convo(cid, start):
    return {"id": cid, "contactId": "c-" + cid, "dateAdded": int(start.timestamp() * 1000),
            "lastMessageDate": int((start + timedelta(hours=2)).timestamp() * 1000)}


def msg(direction, at, source=None, user=None, mtype="TYPE_SMS"):
    return {"id": f"{direction}-{at.timestamp()}", "direction": direction, "dateAdded": iso(at),
            "source": source, "userId": user, "type": mtype, "body": "Hi Jane, call me at 555"}


def test_split_users_by_role_type():
    client, agency = au.split_users(USERS)
    assert client == {"uClient": "Taylor Hamlin"} and agency == {"uSSP": "Lisa Hoffman"}


def test_instant_replies_under_a_users_name_are_not_human():
    start = NOW - timedelta(days=3)
    convos = [convo("k1", start)]
    messages = {"k1": [
        # workflow auto-reply sent "as" the client user 5 seconds after the lead
        msg("outbound", start + timedelta(seconds=5), source="app", user="uClient"),
        msg("inbound", start + timedelta(minutes=30)),
        # a real reply by client staff 40 minutes after the lead wrote back
        msg("outbound", start + timedelta(minutes=70), source="app", user="uClient"),
        msg("outbound", start + timedelta(minutes=71), source="workflow", user="uClient"),
        msg("outbound", start + timedelta(minutes=90), user="uSSP"),
        msg("outbound", start + timedelta(minutes=95), user="uClient", mtype="TYPE_CALL"),
    ]}
    client, agency = au.split_users(USERS)
    out = au.classify_messages(convos, messages, client, agency, NOW - timedelta(days=28))
    t = out["tally"]
    assert t["instant"] == 1 and t["automated"] == 1
    assert t["client"] == 2 and t["ssp"] == 1 and t["calls"] == 1
    assert t["outbound"] == 5
    assert out["mix"]["app/user"] == 2
    assert "Jane" not in repr(out)                       # bodies never read


def test_verdict_tiers():
    assert au.verdict(0, 10, 5, 0, 0, 0, 0)[0] == "No client logins"
    assert au.verdict(1, 10, 5, 0, 0, 0, 3)[0] == "Ads + automation only"
    assert au.verdict(1, 0, 0, 0, 0, 0, 0)[0] == "Idle"
    assert au.verdict(1, 10, 5, 1, 0, 0, 0)[0] == "Light use"
    assert au.verdict(2, 10, 5, 0, au.WORKING_MESSAGES, 0, 0)[0] == "Working in MLH"
    assert au.verdict(2, 10, 5, au.WORKING_DEALS, 0, 0, 0)[0] == "Working in MLH"
    label, evidence = au.verdict(1, 35, 9, 0, 0, 0, 4)
    assert "SSP staff sent 4" in evidence and "35 leads" in evidence


def _routes(method, path, params, body):
    start = NOW - timedelta(days=2)
    if path == "/users/":
        return {"users": USERS}
    if path == "/opportunities/pipelines":
        return {"pipelines": [{"id": "pAds", "name": "Inground Pool - Ads Pipelines 📢"},
                              {"id": "pSales", "name": "📊 Sales Pipeline"}]}
    if path == "/opportunities/search":
        if params.get("status") == "open":
            return {"opportunities": [
                {"id": "o1", "status": "open", "pipelineId": "pAds", "createdAt": iso(start)},
                {"id": "o2", "status": "open", "pipelineId": "pAds", "createdAt": iso(start),
                 "assignedTo": "uClient"}]}
        return {"opportunities": []}
    if path == "/contacts/search":
        return {"contacts": [{"id": "c1", "dateAdded": iso(start)}], "total": 1}
    if path == "/workflows/":
        return {"workflows": [{"name": "FB Lead Ads - Inground → Ads Pipeline", "status": "published"},
                              {"name": "Review Request", "status": "draft"}]}
    if path == "/calendars/":
        return {"calendars": []}
    if path == "/conversations/search":
        return {"conversations": [convo("k1", start)]}
    if path == "/conversations/k1/messages":
        return {"messages": {"messages": [msg("outbound", start + timedelta(seconds=3),
                                              source="app", user="uClient")],
                             "nextPage": False}}
    raise AssertionError(f"unrouted {method} {path}")


class Client:
    def __init__(self, token):
        self.requests_made = 0

    def request(self, method, path, params=None, json_body=None, **_kw):
        self.requests_made += 1
        return _routes(method, path, params or {}, json_body or {})


SUB = {"location_id": "locH", "name": "Hamlin Pools", "slug": "hamlin", "am_name": "Lisa"}


def test_hamlin_shape_reads_as_ads_plus_automation():
    row = au.collect_account(SUB, Client("tok"), NOW, log=lambda *_: None)
    assert row["usage"] == "Ads + automation only"
    assert row["client_users"] == 1 and row["client_user_names"] == "Taylor Hamlin"
    assert row["ssp_users"] == 1
    assert row["deals_created_in_ads_pipelines_28d"] == 2 and row["ads_pipelines"] == 1
    assert row["instant_replies_28d"] == 1 and row["client_staff_messages_28d"] == 0
    assert row["ads_workflows_published"] == 1
    assert row["published_workflow_names"] == "FB Lead Ads - Inground → Ads Pipeline"
    assert row["open_deals_assigned_pct"] == 50
    assert "t@hamlin.example" not in repr(row)


def test_main_account_usage_mode(tmp_path, capsys):
    store = FakeStore(subs=[SUB])
    code = main_mod.run(["--account-usage", "--report-dir", str(tmp_path)], store=store,
                        client_factory=Client, now_utc=NOW)
    assert code == 0
    assert "===== account-usage.csv BEGIN" in capsys.readouterr().out
    text = (tmp_path / "account-usage.csv").read_text()
    assert text.splitlines()[0].startswith("account,am,slug,location_id,usage,evidence")
    assert "Ads + automation only" in text


def test_users_by_type_defaults_unknown_roles_to_client():
    from ..fetchers import users_by_type
    client, agency = users_by_type([{"id": "a", "name": "No Role"},
                                    {"id": "b", "name": "SSP", "roles": {"type": "agency"}}])
    assert client == {"a": "No Role"} and agency == {"b": "SSP"}
