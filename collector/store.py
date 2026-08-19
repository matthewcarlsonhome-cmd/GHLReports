"""Supabase persistence (supabase-py v2, service role).

The service role bypasses RLS, but PIT retrieval still goes through the
get_pit RPC which requires COLLECTOR_KEY (spec section 8). Tokens are never
stored on this object beyond returning them to the caller.

How this fits in
----------------
This is the collector's only touchpoint with the database. Supabase is a
hosted Postgres with an auto-generated HTTP API on top; the ``supabase-py``
client library turns method chains like ``.table(...).upsert(...)`` into
those HTTP calls, so nothing here speaks raw SQL. main.py constructs one
Store per run and calls it for reads (subaccounts, prior snapshots/flags),
writes (snapshots, lead events, flags, run bookkeeping), and secrets
(fetching each location's PIT just before collecting it).

Key ideas to understand this file
---------------------------------
* Service role — Supabase issues several API keys; the "service role" key is
  the trusted backend key that bypasses RLS (Row Level Security, Postgres's
  per-row permission rules that constrain browser/dashboard users). The
  collector is a backend batch job, so it uses this key and must never ship
  to a client.
* RPC — "remote procedure call": ``client.rpc("name", args)`` invokes a SQL
  function defined in the database. All PIT operations are RPCs on purpose:
  the secret tokens live in Supabase Vault (encrypted storage inside
  Postgres), and only those Vault-backed functions can decrypt them. Even the
  service role cannot read the tokens table directly; it must present
  COLLECTOR_KEY, a second shared secret checked inside each function. Tokens
  are therefore fetched fresh per run and never persisted app-side.
* Upsert — "update or insert" in one statement, keyed by the ``on_conflict``
  columns (e.g. location_id + snapshot_date). Rerunning the collector for a
  date overwrites that date's rows instead of duplicating them, which is
  what makes reruns idempotent.
* Environment variables — SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, and
  COLLECTOR_KEY configure the Store; a tiny .env loader below fills them in
  for local development.
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
    """Raised when required Supabase configuration is missing at startup."""
    pass


class Store:
    """All database reads/writes for the collector, behind one small API.

    main.py depends only on the method names here, which is what lets tests
    swap in FakeStore (see tests/fakes.py) without a database. Construction
    reads config from arguments or environment variables and fails loudly
    (StoreConfigError) if anything is missing — before any run bookkeeping.
    """

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
    # Bookkeeping rows in collector_runs: one per collection run, opened at
    # the start and closed with totals at the end. A row stuck in "running"
    # is the tell-tale of a crashed run.

    def start_run(self) -> int:
        """Insert a 'running' collector_runs row; returns its id."""
        result = self.client.table("collector_runs").insert({"status": "running"}).execute()
        return result.data[0]["id"]

    def finish_run(self, run_id: int, status: str, ok: int, held: int, failed: int,
                   requests_made: int, rate_limited: int, error: str | None,
                   details: dict | None = None) -> None:
        """Close the run row with totals, per-location details, and errors.

        Called once at the very end of a (non-dry) run. ``details`` is a
        JSONB dict of {location_id: {status, requests, seconds, ...}}.
        """
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
    # The subaccounts table is the collector's configuration: one row per GHL
    # location with slug, vertical, services, AM email, thresholds, and token
    # health columns. The pit_* methods below are all Vault RPCs — SQL
    # functions that check COLLECTOR_KEY before touching the encrypted
    # tokens (see the module docstring).

    def load_subaccounts(self, active: bool = True) -> list[dict]:
        """All configured locations; active=True filters to the live book."""
        query = self.client.table("subaccounts").select("*")
        if active:
            query = query.eq("active", True)
        return query.execute().data or []

    def pit_key_ok(self) -> bool:
        """True if our COLLECTOR_KEY matches Vault's. Run() checks this first."""
        result = self.client.rpc("pit_key_ok", {"p_key": self.collector_key}).execute()
        return bool(result.data)

    def get_pit(self, location_id: str) -> str | None:
        """Decrypt and return one location's API token, or None if not stored.

        Called once per location per run, just before collection starts; the
        token goes straight into a GHLClient and is never written anywhere.
        """
        result = self.client.rpc("get_pit", {
            "p_location_id": location_id,
            "p_collector_key": self.collector_key,
        }).execute()
        return result.data or None

    def set_pit(self, location_id: str, token: str) -> bool:
        """Store/rotate a token in Vault (used by tools/pit.py, not the run)."""
        result = self.client.rpc("set_pit", {
            "p_location_id": location_id,
            "p_token": token,
            "p_collector_key": self.collector_key,
        }).execute()
        return bool(result.data)

    def delete_pit(self, location_id: str) -> bool:
        """Remove a stored token (tools/pit.py). False = key rejected."""
        result = self.client.rpc("delete_pit", {
            "p_location_id": location_id,
            "p_collector_key": self.collector_key,
        }).execute()
        return bool(result.data)

    def pit_updated_at(self, location_id: str) -> str | None:
        """When the token was last set — feeds the rotation-age warnings."""
        result = self.client.rpc("pit_updated_at", {
            "p_location_id": location_id,
            "p_collector_key": self.collector_key,
        }).execute()
        return result.data or None

    def set_token_status(self, location_id: str, status: str, error: str | None = None) -> None:
        """Record token health ('ok' / 'invalid' / 'none') on the subaccount."""
        self.update_subaccount(location_id, {"token_status": status, "last_token_error": error})

    def update_subaccount(self, location_id: str, fields: dict) -> None:
        """Patch arbitrary columns on one subaccount row."""
        self.client.table("subaccounts").update(fields).eq("location_id", location_id).execute()

    # -- snapshots / flags / lead events ------------------------------------
    # The collector's three output tables. Every write is an upsert (or a
    # delete-then-insert for flags), keyed so that rerunning a date replaces
    # that date's data instead of appending duplicates.

    def upsert_snapshot(self, row: dict) -> None:
        """Write the daily snapshot; key = (location, date), so reruns overwrite."""
        self.client.table("snapshots").upsert(
            row, on_conflict="location_id,snapshot_date").execute()

    def upsert_lead_events(self, rows: list[dict]) -> None:
        """Write per-lead speed-to-lead rows; key = (location, contact)."""
        if rows:
            self.client.table("lead_events").upsert(
                rows, on_conflict="location_id,contact_id").execute()

    def replace_flags(self, location_id: str, snapshot_date: str, flags: list[dict]) -> None:
        """Swap out the full flag set for one location+date.

        Delete-then-insert rather than upsert because the NUMBER of flags can
        shrink between runs — a stale leftover flag must not survive a rerun.
        """
        self.client.table("flags").delete().eq("location_id", location_id).eq(
            "snapshot_date", snapshot_date).execute()
        if flags:
            rows = [{**flag, "location_id": location_id, "snapshot_date": snapshot_date}
                    for flag in flags]
            self.client.table("flags").insert(rows).execute()

    def upsert_form_health(self, location_id: str, snapshot_date: str, rows: list[dict]) -> None:
        """Replace the day's per-form/per-survey health rows for one location
        (same delete-then-insert idiom as flags, same rerun-safe key)."""
        self.client.table("form_health").delete().eq("location_id", location_id).eq(
            "snapshot_date", snapshot_date).execute()
        if rows:
            self.client.table("form_health").insert(rows).execute()

    def upsert_lead_history(self, location_id: str, rows: list[dict]) -> None:
        """Write weekly lead totals; key = (location, ISO week start)."""
        if rows:
            payload = [{**row, "location_id": location_id} for row in rows]
            self.client.table("lead_history").upsert(
                payload, on_conflict="location_id,week_start").execute()

    # -- reads for gate / change tracking / peer pass --------------------------

    def read_flags(self, location_id: str, snapshot_date: date) -> list[str]:
        """Flag codes for one location+date; change tracking reads last week's."""
        result = self.client.table("flags").select("code").eq(
            "location_id", location_id).eq(
            "snapshot_date", snapshot_date.isoformat()).execute()
        return [row["code"] for row in (result.data or [])]

    def update_snapshot_changes(self, location_id: str, snapshot_date: date,
                                flags_new: list[str], flags_resolved: list[str],
                                details: dict) -> None:
        """Stamp flags_new/flags_resolved (and refreshed details) onto today's
        snapshot — the last write of the flags/change-tracking sequence."""
        self.client.table("snapshots").update({
            "flags_new": flags_new,
            "flags_resolved": flags_resolved,
            "details": details,
        }).eq("location_id", location_id).eq(
            "snapshot_date", snapshot_date.isoformat()).execute()

    def read_prev_dead(self, location_id: str, snapshot_date: date) -> list[tuple]:
        """Activity counts from the last 3 gate-passed snapshots before today.

        Input for the gate's dead-source check: if these show activity but
        today reads all zeros, today's fetch probably broke.
        """
        result = self.client.table("snapshots").select(
            "leads_new_7d,convos_active_7d,opps_created_7d,snapshot_date"
        ).eq("location_id", location_id).eq("gate_passed", True).lt(
            "snapshot_date", snapshot_date.isoformat()
        ).order("snapshot_date", desc=True).limit(3).execute()
        return [(row.get("leads_new_7d"), row.get("convos_active_7d"), row.get("opps_created_7d"))
                for row in (result.data or [])]

    def read_portfolio(self, snapshot_date: date) -> dict:
        """Everything the Monday digest needs for one date, in one bundle:
        active subaccounts, that date's snapshots and flags (grouped by
        location), and which flag codes AMs have snoozed (flag_acks rows
        whose snooze window still covers the date)."""
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
        """Slim view of every location's snapshot for one date (peer pass input)."""
        result = self.client.table("snapshots").select(
            "location_id,snapshot_date,gate_passed,leads_delta_pct"
        ).eq("snapshot_date", snapshot_date.isoformat()).execute()
        return result.data or []

    def update_snapshot_peers(self, location_id: str, snapshot_date: date,
                              peer_median_delta_pct: float | None, peer_n: int | None) -> None:
        """Write one location's peer median + peer count (peer pass output)."""
        self.client.table("snapshots").update({
            "peer_median_delta_pct": peer_median_delta_pct,
            "peer_n": peer_n,
        }).eq("location_id", location_id).eq(
            "snapshot_date", snapshot_date.isoformat()).execute()
