"""Separate owner dispatcher. Off by default; no GHL collection or AM side effects."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from uuid import uuid4
import hashlib
import json
import os
import re
import secrets

import requests

from .owner_report_scheduler import schedule
from .owner_report_templates import render
from .owner_reports import stamp
from .owner_store import OwnerStore

DESTINATION = re.compile(r"https://services\.leadconnectorhq\.com/hooks/[A-Za-z0-9_-]+/webhook-trigger/[A-Za-z0-9_-]+")
SSP = "ZnckuEDPIcWu8fn72ppi"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def post(url, payload, transport=requests.post):
    """No redirect or automatic retry: ambiguous results need reconciliation."""
    if not DESTINATION.fullmatch(url):
        return "failed", None
    encoded=json.dumps(payload).encode()
    if len(encoded)>96000: return "failed", None
    try:
        response=transport(url,json=payload,timeout=(5,20),allow_redirects=False)
        return ("accepted" if 200<=response.status_code<300 else "unknown"),response.status_code
    except requests.RequestException:
        return "unknown",None


def synthetic(location, report_id, now):
    """Only sample business data. Never substitute a real report for a test."""
    start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    from datetime import timedelta
    metric={"new_leads":40,"unassigned":2,"eligible":38,"contacted":34,"completion_pct":89.5,
            "uncontacted":4,"speed_median":12,"speed_p90":58,"speed_samples":35,"speed_human":True,
            "waiting":7,"oldest_wait_hours":18}
    data={"schema_version":1,"account_name":"Example Pool Company","location_id":location,
          "period_kind":"week","period_start":(start-timedelta(days=7)).isoformat(),"period_end":start.isoformat(),
          "timezone":"America/Chicago","state":"attention","current_as_of":now.isoformat(),"metrics":metric,"queue":metric,
          "topics":["follow_up","waiting","speed","ownership","lead_flow"],"to_date":False,"stale":False,"patterns":[],"sample":True,
          "comparison":None,"template_version":"owner-weekly-v1","actions":[{"topic":"follow_up","status":"Sample priority","severity":"amber","title":"4 recent leads need follow-up",
          "detail":"Fictional example for template verification.","next_step":"Assign a team member and record the next follow-up."}],
          "data_note":"TEST — FICTIONAL DATA. No real customer information is included."}
    return {"id":report_id,"period_start":data["period_start"][:10],"data":data}


def run(store, env, now, approved_test=None, transport=post):
    mode=env.get("OWNER_REPORTS_MODE","off")
    if mode not in ("off","preview","live"): mode="off"
    pilots=[x.strip() for x in env.get("OWNER_REPORTS_PILOT_LOCATIONS","").split(",") if x.strip()]
    cap=int(env.get("OWNER_REPORTS_DAILY_CAP","1"))
    if not 1<=cap<=100: raise ValueError("Invalid daily cap")
    store.rpc("owner_heartbeat",p_mode=mode,p_pilots=pilots,p_cap=cap)
    if mode=="off": return {"mode":mode,"accepted":0,"held":0}
    configs={c["location_id"]:c for c in store.configs()}
    for loc,c in configs.items():
        if c["delivery_mode"]!="live" or mode!="live" or loc not in pilots or not c.get("recipient") or not c.get("destination"): continue
        times=schedule(now,c["account"]["timezone"],c["weekday"],c["local_time"])
        if now<stamp(times["due_at"]): continue
        store.enqueue(dict(times,location_id=loc,recipient_version=c["recipient"]["version"],
                           destination_version=c["destination"]["version"],settings_revision=c["revision"]))
    accepted=held=0
    for job in store.pending():
        if job["mode"]=="test" and job["id"]!=approved_test: continue
        if job["mode"]=="live" and (mode!="live" or job["location_id"] not in pilots): continue
        if stamp(job["expires_at"])<=now:
            store.hold(job["id"],"Delivery window missed; review before any resend",True); continue
        if stamp(job["due_at"])>now: continue
        config=configs.get(job["location_id"])
        if not config: continue
        if job["mode"]=="test":
            if job["location_id"]!=SSP or not job.get("approved_at"): continue
            snapshot=store.save_test_report(job, synthetic(SSP,str(uuid4()),now))
        else:
            snapshot=store.latest(job["location_id"],job["period_start"])
            captured=stamp((snapshot or {}).get("data",{}).get("current_as_of"))
            useful=snapshot and any(isinstance(x,(float,int)) for x in snapshot["data"]["metrics"].values() if not isinstance(x,bool))
            if not snapshot or not captured or not(-300<=(now-captured).total_seconds()<=config.get("stale_hours",30)*3600) or not useful or snapshot["data"].get("to_date"):
                store.hold(job["id"],"Current report data is unavailable or stale"); held+=1; continue
            # The database binds this version atomically when claiming dispatch.
        content=render(snapshot,env.get("DASHBOARD_URL","https://mlhaccountreports.netlify.app"),job["mode"]=="test")
        claim,completion=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        connection=store.rpc("owner_dispatch",p_id=job["id"],p_claim_hash=digest(claim),
            p_completion_hash=digest(completion),p_content=content,
            p_hash=digest(json.dumps(content,sort_keys=True)),p_report=snapshot["id"])
        if not connection: continue
        payload={"event":"owner_followup_weekly","schema_version":"1","mode":job["mode"],
                 "delivery_id":job["id"],"location_id":job["location_id"],
                 "workflow_binding":connection["workflow"],"recipient_binding_version":str(connection["recipient_version"]),
                 "period_start":job["period_start"],"period_end":job["period_end"],"timezone":job["timezone"],
                 "report_id":snapshot["id"],"template_version":"owner-weekly-v1",
                 "claim_expires_at":job["expires_at"],"claim_credential":claim,
                 "completion_credential":completion}
        # A flat JSON string lets GHL map one input without guessed merge paths.
        payload["envelope"]=json.dumps(payload,separators=(",",":"))
        status,http=transport(connection["url"],payload)
        store.rpc("owner_dispatch_result",p_id=job["id"],p_status=status,p_http=http)
        accepted+=status=="accepted"
    return {"mode":mode,"accepted":accepted,"held":held}


def main():
    parser=argparse.ArgumentParser(description="Independent weekly owner dispatcher; defaults off.")
    parser.add_argument("--approved-test",help="Explicitly dispatch one already approved SSP test ID")
    args=parser.parse_args()
    from .store import Store
    try:
        result=run(OwnerStore(Store().client),os.environ,datetime.now(timezone.utc),args.approved_test)
        print(f"Owner reports: mode={result['mode']}, accepted={result['accepted']}, held={result['held']}. Acceptance is not inbox delivery.")
    except Exception:
        # Provider errors may embed credentials/request data. Never echo them.
        print("Owner report dispatcher unavailable; inspect protected configuration and database status.")
        raise SystemExit(1)


if __name__=="__main__": main()
