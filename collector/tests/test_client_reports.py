"""Calendar reports must have honest denominators, coverage, and safe fields."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from collector.client_reports import build_reports

TZ=ZoneInfo('America/Chicago')
NOW=datetime(2026,10,1,10,30,tzinfo=timezone.utc)
def fixture():
    complete={'status':'complete','exhausted':True}
    snapshot={'gate_passed':True,'coverage':{'sources':{s:dict(complete) for s in ('contacts','speed_to_lead','conversations')}},'leads_uncontacted_24h':2,'convos_waiting':3,'convos_waiting_max_hours':8}
    contacts=[{'id':str(i),'dateAdded':f'2026-09-{day:02d}T12:00:00Z'} for i,day in enumerate([28,29,30])]
    events=[{'contact_id':str(i),'created_at':datetime.fromisoformat(c['dateAdded'].replace('Z','+00:00')),'first_outbound_at':datetime.fromisoformat(c['dateAdded'].replace('Z','+00:00'))+timedelta(minutes=mins),'first_outbound_kind':'human','first_human_touch_at':datetime.fromisoformat(c['dateAdded'].replace('Z','+00:00'))+timedelta(minutes=mins),'first_touch_minutes':mins,'first_human_touch_minutes':mins} for i,(c,mins) in enumerate(zip(contacts,[5,20,200]))]
    return snapshot,contacts,events
def build(snapshot=None,contacts=None,events=None,now=NOW):
    a,b,c=fixture()
    return build_reports({'location_id':'synthetic'},snapshot if snapshot is not None else a,contacts if contacts is not None else b,events if events is not None else c,now,now-timedelta(days=42),TZ)
def week(rows):return next(r['data'] for r in rows if r['period_kind']=='week')
def test_individual_median_and_aged_denominator():
    data=week(build());m=data['metrics']
    assert m['speed_median']==20 and m['speed_samples']==3
    assert m['eligible']==2 and m['contacted']==2 and m['completion_pct']==100
    assert data['period_start'].startswith('2026-09-28')
    assert data['period_end'].startswith('2026-10-01')

def test_attention_preserves_existing_snapshot_speed():
    a,b,c=fixture();a.update(speed_to_lead_median_min=17,speed_to_lead_p90_min=90,speed_kind_known=True)
    m=build(a,b,c)[0]['data']['metrics']
    assert m['speed_median']==17 and m['speed_p90']==90 and m['speed_human'] is True
    assert m['waiting']==3 and m['uncontacted']==2
@pytest.mark.parametrize('change',[{'status':'partial'},{'exhausted':False},{'error':'secret detail'},{'skipped':True}])
def test_partial_response_never_looks_zero(change):
    a,b,c=fixture();a['coverage']['sources']['speed_to_lead'].update(change)
    m=week(build(a,b,c))['metrics'];assert m['new_leads']==3 and m['speed_median'] is None and m['uncontacted'] is None
def test_missing_old_lead_response_masks_month_not_week():
    a,b,c=fixture();b.append({'id':'old','dateAdded':'2026-09-02T12:00:00Z'})
    rows=build(a,b,c);month=next(r['data'] for r in rows if r['period_kind']=='month' and r['period_start']=='2026-09-01')
    assert month['metrics']['new_leads']==4 and month['metrics']['speed_median'] is None
    assert week(rows)['metrics']['speed_median']==20
def test_future_response_excluded_from_completed_period():
    a,b,c=fixture();c[0]['first_human_touch_at']=NOW;c[0]['first_outbound_at']=NOW
    m=week(build(a,b,c))['metrics'];assert m['contacted']==1 and m['uncontacted']==1 and m['speed_samples']==2
def test_empty_held_and_incomplete_history():
    assert week(build(contacts=[],events=[]))['metrics']['new_leads']==0
    assert week(build(contacts=[],events=[]))['metrics']['completion_pct'] is None
    a,b,c=fixture();a['gate_passed']=False
    assert all(v is None for k,v in week(build(a,b,c))['metrics'].items())
    rows=build(now=datetime(2026,10,31,10,tzinfo=timezone.utc))
    old=next(r for r in rows if r['period_kind']=='month' and r['period_start']=='2026-09-01')
    assert old['data']['metrics']['new_leads'] is None
def test_calendar_dst_and_half_open_boundaries():
    rows=build(contacts=[],events=[],now=datetime(2026,11,3,10,tzinfo=timezone.utc))
    prior=next(r['data'] for r in rows if r['period_kind']=='week' and r['period_start']=='2026-10-26')
    start=datetime.fromisoformat(prior['period_start']);end=datetime.fromisoformat(prior['period_end'])
    assert (end-start).total_seconds()==169*3600
    a,b,c=fixture();b.append({'id':'boundary','dateAdded':'2026-10-01T05:00:00Z'})
    assert week(build(a,b,c))['metrics']['new_leads']==3
