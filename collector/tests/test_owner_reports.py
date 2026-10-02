"""Owner summaries, templates, clock rules and delivery isolation use synthetic data."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import pytest
import requests

from collector.owner_reports import summarize, observations, report_url
from collector.owner_report_templates import render
from collector.owner_report_scheduler import schedule, local_instant
from collector.owner_delivery import post, run, synthetic
from zoneinfo import ZoneInfo

NOW=datetime(2026,10,5,13,tzinfo=timezone.utc)
LOC="fixture-a"


def reports():
    metric=dict(new_leads=10,unassigned=0,eligible=8,contacted=8,completion_pct=100,
                uncontacted=0,speed_median=10,speed_p90=25,speed_samples=8,speed_human=True,
                waiting=0,oldest_wait_hours=0)
    period={"location_id":LOC,"period_kind":"week","period_start":"2026-09-28","generated_at":NOW.isoformat(),
            "data":{"period_start":"2026-09-28T00:00:00-05:00","period_end":"2026-10-05T00:00:00-05:00",
                    "timezone":"America/Chicago","data_through":NOW.isoformat(),"to_date":False,
                    "coverage":{"contacts":True,"responses":True,"conversations":False},"metrics":metric}}
    current=deepcopy(period);current["period_kind"]="attention";current["data"]["coverage"]["conversations"]=True
    return period,current


def summary(**kw):
    p,c=reports()
    return summarize({"location_id":LOC,"name":"Example Pool Company"},kw.pop("period",p),kw.pop("current",c),now=NOW,**kw)


def test_healthy_requires_all_selected_sources():
    assert summary()["state"]=="healthy"
    p,c=reports();c["data"]["coverage"]["conversations"]=False
    assert summary(current=c)["state"]=="limited"
    p["data"]["coverage"]["responses"]=False
    assert summary(period=p)["metrics"]["completion_pct"] is None


def test_queue_and_cohort_not_conflated():
    p,c=reports();c["data"]["metrics"]["uncontacted"]=3
    result=summary(period=p,current=c)
    assert result["metrics"]["uncontacted"]==0
    assert result["queue"]["uncontacted"]==3
    assert result["actions"][0]["status"]=="Follow-up to review"


def test_two_distinct_nights_required_and_raw_flag_text_not_exported():
    snapshot={"gate_passed":True,"leads_uncontacted_24h":3,"coverage":{"sources":{
        x:{"status":"complete","exhausted":True} for x in ("contacts","speed_to_lead","conversations","opportunities")}}}
    obs=observations(snapshot,[{"code":"SLOW_RESPONSE","severity":"amber","detail":"PRIVATE_CANARY"}],"2026-10-05")
    assert "PRIVATE_CANARY" not in json.dumps(obs)
    p,c=reports();c["data"]["metrics"]["uncontacted"]=3
    assert summary(current=c,history=obs+obs)["actions"][0]["status"]=="Follow-up to review"
    previous=[dict(r,observed_on="2026-10-04") for r in obs]
    assert summary(current=c,history=obs+previous)["actions"][0]["status"]=="Confirmed issue"


def test_stale_queue_cannot_be_healthy_or_current_action():
    p,c=reports();c["data"]["data_through"]=(NOW-timedelta(days=3)).isoformat()
    result=summary(current=c)
    assert result["stale"] and result["state"]=="limited" and not result["actions"]
    assert result["queue"]["waiting"] is None


def test_no_responses_and_empty_denominator_are_not_zero_speed():
    p,c=reports();p["data"]["metrics"].update(speed_samples=0,speed_median=None,speed_p90=None,eligible=0,contacted=0,completion_pct=None)
    result=summary(period=p)
    assert result["state"]=="healthy" and result["metrics"]["speed_median"] is None


def test_template_escapes_and_uses_snapshot_id():
    result=summary();result["account_name"]='<script>alert("x")</script>'
    out=render({"id":"saved-id","period_start":"2026-09-28","data":result},"https://reports.example.com")
    assert "<script>" not in out["body_html"] and "&lt;script&gt;" in out["body_html"]
    assert "report=saved-id" in out["report_url"] and "%%" not in out["body_html"]
    assert all(isinstance(v,str) for v in out.values())


@pytest.mark.parametrize("origin",["http://reports.example.com","https://x:y@reports.example.com","javascript:alert(1)","https://reports.example.com/?secret=x"])
def test_invalid_report_origin(origin):
    with pytest.raises(ValueError):report_url(origin,LOC,"id","2026-09-28")


@pytest.mark.parametrize("url",["http://localhost/x","https://127.0.0.1/x","https://services.leadconnectorhq.com.evil.invalid/hooks/a/webhook-trigger/b","https://services.leadconnectorhq.com/hooks/a/webhook-trigger/b?x=y"])
def test_destination_rejected_without_network(url):
    def forbidden(*a,**k):raise AssertionError("No network")
    assert post(url,{},forbidden)==("failed",None)


def test_transport_does_not_retry_ambiguous_result():
    calls=[]
    def transport(*args,**kw):
        calls.append(kw);raise requests.Timeout("Sensitive provider detail")
    result=post("https://services.leadconnectorhq.com/hooks/fixture/webhook-trigger/fixture",{},transport)
    assert result==("unknown",None) and len(calls)==1 and calls[0]["allow_redirects"] is False


def test_local_schedule_and_dst():
    result=schedule(NOW,"America/Chicago")
    assert result["period_start"]=="2026-09-28" and result["due_at"]=="2026-10-05T13:00:00+00:00"
    winter=schedule(datetime(2026,11,2,15,tzinfo=timezone.utc),"America/Chicago")
    assert winter["due_at"]=="2026-11-02T14:00:00+00:00"
    from datetime import date,time
    gap=local_instant(date(2026,3,8),time(2,30),ZoneInfo("America/Chicago"))
    assert gap.hour==3 and gap.minute==0
    fold=local_instant(date(2026,11,1),time(1,30),ZoneInfo("America/Chicago"))
    assert fold.fold==0


def test_zero_baseline_has_no_infinite_percentage():
    p,c=reports();previous=deepcopy(p)
    previous["data"].update(period_start="2026-09-21T00:00:00-05:00",period_end=p["data"]["period_start"])
    previous["data"]["metrics"]["new_leads"]=0
    assert summary(previous=previous)["comparison"]=={"previous":0,"change":None,"eligible":True}


def test_off_and_preview_never_send():
    class Store:
        def rpc(self,*a,**k):return None
        def configs(self):return []
        def pending(self):return []
    def forbidden(*a,**k):raise AssertionError("No network")
    for mode in ("off","preview","invalid"):
        assert run(Store(),{"OWNER_REPORTS_MODE":mode},NOW,transport=forbidden)["accepted"]==0


def test_synthetic_test_has_no_real_account_data():
    s=synthetic(LOC,"sample-id",NOW)
    assert s["data"]["account_name"]=="Example Pool Company"
    assert "TEST" in render(s,"https://reports.example.com",True)["subject"]



def test_topic_selection_masks_unselected_metrics_and_queues():
    result=summary(topics=['waiting'])
    assert result['metrics']['new_leads'] is None
    assert result['queue']['unassigned'] is None
    out=render({'id':'saved','period_start':'2026-09-28','data':result},'https://reports.example.com')
    assert 'Not included' in out['body_html']
    assert 'unassigned leads' not in out['current_queue_text']


def test_negative_lead_change_is_not_unavailable_in_email():
    result=summary()
    result['comparison']={'previous':20,'change':-50}
    out=render({'id':'saved','period_start':'2026-09-28','data':result},'https://reports.example.com')
    assert '-50% change' in out['metric_1_detail']


def test_month_to_date_comparison_requires_coverage():
    period,current=reports()
    period['data'].update(to_date=True,comparison={'available':True,'new_leads':20,'label':'Prior month through day 4'})
    result=summary(period=period)
    assert result['comparison']['change']==-50
    period['data']['comparison']['available']=False
    assert summary(period=period)['comparison'] is None


class DispatchStore:
    def __init__(self,mode='test',stale_hours=30):
        from collector.owner_delivery import SSP
        self.calls=[];self.snapshot=synthetic(SSP,'saved-sample',NOW)
        self.job=dict(id='job',mode=mode,location_id=SSP,approved_at=NOW.isoformat(),due_at=NOW.isoformat(),
                      expires_at=(NOW+timedelta(hours=24)).isoformat(),period_start='2026-09-28',
                      period_end='2026-10-05T00:00:00-05:00',timezone='America/Chicago')
        self.config=dict(location_id=SSP,delivery_mode='preview',stale_hours=stale_hours)
    def rpc(self,name,**kw):
        self.calls.append((name,kw))
        if name=='owner_dispatch':return {'url':'fake transport only','workflow':'fixture','recipient_version':1}
    def configs(self):return [self.config]
    def pending(self):return [self.job]
    def save_test_report(self,job,snapshot):self.saved=snapshot;return snapshot
    def latest(self,*a):return self.snapshot
    def hold(self,*a):self.calls.append(('held',a))


def test_test_dispatch_persists_exact_report_before_sending():
    from collector.owner_delivery import SSP
    store=DispatchStore();sent=[]
    def transport(url,payload):sent.append(payload);return 'accepted',200
    run(store,{'OWNER_REPORTS_MODE':'preview'},NOW,'job',transport)
    assert sent[0]['report_id']==store.saved['id']!='job'
    dispatch=next(args for name,args in store.calls if name=='owner_dispatch')
    assert dispatch['p_report']==store.saved['id']
    assert 'report='+store.saved['id'] in dispatch['p_content']['report_url']


def test_unapproved_test_never_dispatches():
    store=DispatchStore()
    run(store,{'OWNER_REPORTS_MODE':'preview'},NOW,transport=lambda *a:pytest.fail('send'))
    assert not any(name=='owner_dispatch' for name,args in store.calls)


def test_dispatch_uses_account_freshness_limit():
    from collector.owner_delivery import SSP
    store=DispatchStore('live',1)
    store.snapshot['data']['current_as_of']=(NOW-timedelta(hours=2)).isoformat()
    run(store,{'OWNER_REPORTS_MODE':'live','OWNER_REPORTS_PILOT_LOCATIONS':SSP},NOW,transport=lambda *a:pytest.fail('send'))
    assert any(name=='held' for name,args in store.calls)
