"""Optional PIT management CLI (spec 8.4: the Supabase Vault UI is the
primary path for loading tokens; this wraps the same RPCs for anyone who
prefers a terminal). The token is prompted with getpass — never passed as a
command-line argument, never echoed, never logged.

    python -m collector.tools.pit set    --location <id|slug>
    python -m collector.tools.pit rotate --location <id|slug>
    python -m collector.tools.pit delete --location <id|slug>
    python -m collector.tools.pit status
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import date

from ..store import Store

ROTATION_WARN_DAYS = 80


def resolve(store: Store, key: str) -> dict:
    for sub in store.load_subaccounts(active=False):
        if sub.get("location_id") == key or sub.get("slug") == key:
            return sub
    print(f"unknown location {key!r} — is it in the subaccounts table?", file=sys.stderr)
    sys.exit(1)


def cmd_set(store: Store, key: str, rotate: bool) -> int:
    sub = resolve(store, key)
    token = getpass.getpass(f"Paste the PIT for {sub.get('name')} ({sub['location_id']}): ").strip()
    if len(token) < 20:
        print("token looks wrong (too short); nothing stored", file=sys.stderr)
        return 1
    if not store.set_pit(sub["location_id"], token):
        print("unauthorized (COLLECTOR_KEY rejected)", file=sys.stderr)
        return 1
    print(f"{'rotated' if rotate else 'stored'} PIT for {sub.get('slug') or sub['location_id']}")
    return 0


def cmd_delete(store: Store, key: str) -> int:
    sub = resolve(store, key)
    if not store.delete_pit(sub["location_id"]):
        print("unauthorized (COLLECTOR_KEY rejected)", file=sys.stderr)
        return 1
    print(f"deleted PIT for {sub.get('slug') or sub['location_id']}")
    return 0


def cmd_status(store: Store) -> int:
    subs = store.load_subaccounts(active=False)
    today = date.today()
    print(f"{'slug':<14} {'location_id':<24} {'status':<8} {'rotated':<12} note")
    for sub in sorted(subs, key=lambda s: (not s.get("is_parent"), s.get("slug") or "")):
        rotated = sub.get("token_rotated_at") or ""
        note = ""
        if rotated:
            try:
                age = (today - date.fromisoformat(str(rotated))).days
                if age > ROTATION_WARN_DAYS:
                    note = f"ROTATE — {age} days old"
            except ValueError:
                pass
        if not sub.get("active"):
            note = (note + " " if note else "") + "(inactive)"
        print(f"{(sub.get('slug') or ''):<14} {sub['location_id']:<24} "
              f"{sub.get('token_status', '?'):<8} {str(rotated):<12} {note}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="pit", description="Manage GHL Private Integration Tokens in Supabase Vault")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("set", "rotate", "delete"):
        p = sub.add_parser(name)
        p.add_argument("--location", required=True, help="location_id or slug")
    sub.add_parser("status")
    args = parser.parse_args()

    store = Store()
    if args.command == "status":
        sys.exit(cmd_status(store))
    if args.command in ("set", "rotate"):
        sys.exit(cmd_set(store, args.location, rotate=args.command == "rotate"))
    if args.command == "delete":
        sys.exit(cmd_delete(store, args.location))


if __name__ == "__main__":
    main()
