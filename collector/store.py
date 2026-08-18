"""Supabase persistence (supabase-py v2, service role).

The service role bypasses RLS, but PIT retrieval still goes through the
get_pit RPC which requires COLLECTOR_KEY (spec section 8). Tokens are never
stored on this object beyond returning them to the caller.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines, # comments); never overrides
    variables already set in the environment."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class StoreConfigError(RuntimeError):
    pass


class Store:
    def __init__(self, url: str | None = None, service_key: str | None = None,
                 collector_key: str | None = None):
        load_dotenv()
        self.url = url or os.environ.get("SUPABASE_URL")
        self.service_key = service_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        self.collector_key = collector_key or os.environ.get("COLLECTOR_KEY")
        missing = [name for name, val in [
            ("SUPABASE_URL", self.url),
            ("SUPABASE_SERVICE_ROLE_KEY", self.service_key),
            ("COLLECTOR_KEY", self.collector_key),
        ] if not val]
        if missing:
            raise StoreConfigError(f"missing environment variables: {', '.join(missing)}")
        from supabase import create_client  # imported lazily so tests can fake the Store
        self.client = create_client(self.url, self.service_key)

    # -- runs -------------------------------------------------------------

    def start_run(self) -> int:
        result = self.client.table("collector_runs").insert({"status": "running"}).execute()
        return result.data[0]["id"]

    def finish_run(self, run_id: int, status: str, ok: int, held: int, failed: int,
                   requests_made: int, rate_limited: int, error: str | None,
                   details: dict | None = None) -> None:
        self.client.table("collector_runs").update({
            "finished_at": "now()",
            "status": status,
            "locations_ok": ok,
            "locations_held": held,
            "locations_failed": failed,
            "requests_made": requests_made,
            "rate_limited": rate_limited,
            "details": details or {},
            "error": error,
        }).eq("id", run_id).execute()

    # -- subaccounts / tokens ----------------------------------------------

    def load_subaccounts(self, active: bool = True) -> list[dict]:
        query = self.client.table("subaccounts").select("*")
        if active:
            query = query.eq("active", True)
        return query.execute().data or []

    def pit_key_ok(self) -> bool:
        result = self.client.rpc("pit_key_ok", {"p_key": self.collector_key}).execute()
        return bool(result.data)

    def get_pit(self, location_id: str) -> str | None:
        result = self.client.rpc("get_pit", {
            "p_location_id": location_id,
            "p_collector_key": self.collector_key,
        }).execute()
        return result.data or None

    def set_pit(self, location_id: str, token: str) -> bool:
        result = self.client.rpc("set_pit", {
            "p_location_id": location_id,
            "p_token": token,
            "p_collector_key": self.collector_key,
        }).execute()
        return bool(result.data)

    def delete_pit(self, location_id: str) -> bool:
        result = self.client.rpc("delete_pit", {
            "p_location_id": location_id,
            "p_collector_key": self.collector_key,
        }).execute()
        return bool(result.data)

    def pit_updated_at(self, location_id: str) -> str | None:
        result = self.client.rpc("pit_updated_at", {
            "p_location_id": location_id,
            "p_collector_key": self.collector_key,
        }).execute()
        return result.data or None

    def set_token_status(self, location_id: str, status: str, error: str | None = None) -> None:
        self.update_subaccount(location_id, {"token_status": status, "last_token_error": error})

    def update_subaccount(self, location_id: str, fields: dict) -> None:
        self.client.table("subaccounts").update(fields).eq("location_id", location_id).execute()

    # -- snapshots / flags / lead events ------------------------------------

    def upsert_snapshot(self, row: dict) -> None:
        self.client.table("snapshots").upsert(
            row, on_conflict="location_id,snapshot_date").execute()

    def upsert_lead_events(self, rows: list[dict]) -> None:
        if rows:
            self.client.table("lead_events").upsert(
                rows, on_conflict="location_id,contact_id").execute()

    def replace_flags(self, location_id: str, snapshot_date: str, flags: list[dict]) -> None:
        self.client.table("flags").delete().eq("location_id", location_id).eq(
            "snapshot_date", snapshot_date).execute()
        if flags:
            rows = [{**flag, "location_id": location_id, "snapshot_date": snapshot_date}
                    for flag in flags]
            self.client.table("flags").insert(rows).execute()

    def upsert_lead_history(self, location_id: str, rows: list[dict]) -> None:
        if rows:
            payload = [{**row, "location_id": location_id} for row in rows]
            self.client.table("lead_history").upsert(
                payload, on_conflict="location_id,week_start").execute()

    # -- reads for gate / change tracking / peer pass --------------------------

    def read_flags(self, location_id: str, snapshot_date: date) -> list[str]:
        result = self.client.table("flags").select("code").eq(
            "location_id", location_id).eq(
            "snapshot_date", snapshot_date.isoformat()).execute()
        return [row["code"] for row in (result.data or [])]

    def update_snapshot_changes(self, location_id: str, snapshot_date: date,
                                flags_new: list[str], flags_resolved: list[str],
                                details: dict) -> None:
        self.client.table("snapshots").update({
            "flags_new": flags_new,
            "flags_resolved": flags_resolved,
            "details": details,
        }).eq("location_id", location_id).eq(
            "snapshot_date", snapshot_date.isoformat()).execute()

    def read_prev_dead(self, location_id: str, snapshot_date: date) -> list[tuple]:
        result = self.client.table("snapshots").select(
            "leads_new_7d,convos_active_7d,opps_created_7d,snapshot_date"
        ).eq("location_id", location_id).eq("gate_passed", True).lt(
            "snapshot_date", snapshot_date.isoformat()
        ).order("snapshot_date", desc=True).limit(3).execute()
        return [(row.get("leads_new_7d"), row.get("convos_active_7d"), row.get("opps_created_7d"))
                for row in (result.data or [])]

    def read_portfolio(self, snapshot_date: date) -> dict:
        """Everything the Monday digest needs for one date."""
        subs = self.load_subaccounts(active=True)
        snaps = self.client.table("snapshots").select(
            "location_id,gate_passed,flags_new,flags_resolved"
        ).eq("snapshot_date", snapshot_date.isoformat()).execute().data or []
        flags = self.client.table("flags").select(
            "location_id,code,severity,action"
        ).eq("snapshot_date", snapshot_date.isoformat()).execute().data or []
        acks = self.client.table("flag_acks").select(
            "location_id,code"
        ).gte("snooze_until", snapshot_date.isoformat()).execute().data or []

        flags_by_loc: dict[str, list[dict]] = {}
        for flag in flags:
            flags_by_loc.setdefault(flag["location_id"], []).append(flag)
        acked_by_loc: dict[str, set] = {}
        for ack in acks:
            acked_by_loc.setdefault(ack["location_id"], set()).add(ack["code"])
        return {
            "subs": subs,
            "snapshots_by_loc": {s["location_id"]: s for s in snaps},
            "flags_by_loc": flags_by_loc,
            "acked_by_loc": acked_by_loc,
        }

    def todays_snapshots(self, snapshot_date: date) -> list[dict]:
        result = self.client.table("snapshots").select(
            "location_id,snapshot_date,gate_passed,leads_delta_pct"
        ).eq("snapshot_date", snapshot_date.isoformat()).execute()
        return result.data or []

    def update_snapshot_peers(self, location_id: str, snapshot_date: date,
                              peer_median_delta_pct: float | None, peer_n: int | None) -> None:
        self.client.table("snapshots").update({
            "peer_median_delta_pct": peer_median_delta_pct,
            "peer_n": peer_n,
        }).eq("location_id", location_id).eq(
            "snapshot_date", snapshot_date.isoformat()).execute()
