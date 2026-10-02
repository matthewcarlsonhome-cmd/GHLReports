"""Owner-report persistence. Service-only methods never return routes to browsers."""
from datetime import timedelta
from .owner_reports import DEFAULT_TOPICS, observations, stamp, summarize


class OwnerStore:
    def __init__(self, client):
        self.client=client

    def rpc(self, name, **args):
        return self.client.rpc(name,args).execute().data

    def rows(self, table, location=None):
        query=self.client.table(table).select("*")
        if location is not None: query=query.eq("location_id",location)
        return query.execute().data or []

    def publish(self, account, result, run_date):
        location=account["location_id"]
        settings=self.rows("client_report_settings",location)
        if not settings or not settings[0]["publication_enabled"]: return
        reports=result.get("client_reports",[])
        if not reports: return
        owner_settings=self.rows("owner_report_settings",location)
        topics=owner_settings[0]["topics"] if owner_settings else DEFAULT_TOPICS
        obs=observations(result["snapshot"],result.get("flags",[]),run_date)
        self.client.table("owner_issue_observations").upsert(
            [dict(r,location_id=location) for r in obs],on_conflict="location_id,observed_on,topic").execute()
        now=stamp(reports[0]["generated_at"])
        history=self.client.table("owner_issue_observations").select("*").eq("location_id",location).gte(
            "observed_on",(now-timedelta(days=100)).date().isoformat()).execute().data or []
        # Prospective metadata retention only. Never infer unobserved responses.
        complete=result["snapshot"].get("coverage",{}).get("sources",{}).get("speed_to_lead",{})
        safe=[]
        for event in result.get("lead_events",[]):
            safe.append({k:event.get(k) for k in ("location_id","contact_id","created_at","first_outbound_at",
                "first_human_touch_at","first_outbound_kind","first_touch_minutes","first_human_touch_minutes")})
            safe[-1].update(observed_through=now.isoformat(),source_complete=complete.get("status")=="complete" and complete.get("exhausted") is True and not complete.get("error") and not complete.get("skipped"))
        if safe: self.client.table("owner_response_history").upsert(safe,on_conflict="location_id,contact_id").execute()
        current=next(r for r in reports if r["period_kind"]=="attention")
        rows=[]
        for report in reports:
            if stamp(report["data"]["period_end"])<=stamp(report["data"]["period_start"]): continue
            previous=next((p for p in reports if p["period_kind"]==report["period_kind"] and
                           stamp(p["data"]["period_end"])==stamp(report["data"]["period_start"]) and not p["data"]["to_date"]),None)
            data=summarize(dict(account,stale_hours=settings[0]["stale_hours"]),report,current,history,topics,previous,now)
            rows.append({"location_id":location,"period_kind":report["period_kind"],
                         "period_start":report["period_start"],"period_end":data["period_end"],
                         "generated_at":now.isoformat(),"data":data})
        self.client.table("owner_report_snapshots").upsert(rows,on_conflict="location_id,period_kind,period_start,generated_at",ignore_duplicates=True).execute()

    def configs(self):
        settings=self.rows("owner_report_settings")
        accounts={r["location_id"]:r for r in self.rows("subaccounts")}
        recipients={r["location_id"]:r for r in self.rows("owner_report_recipients")}
        destinations={r["location_id"]:r for r in self.rows("owner_report_destinations")}
        publication={r["location_id"]:r for r in self.rows("client_report_settings")}
        return [dict(s,account=accounts[s["location_id"]],stale_hours=publication.get(s["location_id"],{}).get("stale_hours",30),recipient=recipients.get(s["location_id"]),
                     destination=destinations.get(s["location_id"])) for s in settings if s["location_id"] in accounts]

    def latest(self, location, period):
        rows=self.client.table("owner_report_snapshots").select("*").eq("location_id",location).eq(
            "period_kind","week").eq("period_start",period).not_.eq("data->>sample","true").order("generated_at",desc=True).limit(1).execute().data or []
        return rows[0] if rows else None

    def enqueue(self, row):
        # ON CONFLICT partial-index inference is not supported by every PostgREST
        # version; a database function owns the race-safe insertion.
        return self.rpc("owner_enqueue",p_row=row)

    def pending(self):
        return self.client.table("owner_report_outbox").select("*").in_("status",["queued","held"]).execute().data or []

    def hold(self, delivery_id, reason, expired=False):
        self.client.table("owner_report_outbox").update(
            {"status":"skipped" if expired else "held","reason":reason}).eq("id",delivery_id).in_("status",["queued","held"]).execute()

    def save_test_report(self, job, snapshot):
        row={"id":snapshot["id"],"location_id":job["location_id"],"period_kind":"week",
             "period_start":snapshot["period_start"],"period_end":snapshot["data"]["period_end"],
             "generated_at":snapshot["data"]["current_as_of"],"data":snapshot["data"]}
        self.client.table("owner_report_snapshots").insert(row).execute()
        return row
