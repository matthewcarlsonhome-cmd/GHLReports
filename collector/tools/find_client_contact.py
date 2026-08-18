"""Find a client's contact ID inside the SSP parent account (spec section 6).

    python -m collector.tools.find_client_contact --q "<company name>"

Searches the parent subaccount's contacts by free-text query and prints
candidates so the ID can be copied into subaccounts.ssp_client_contact_id.
"""

from __future__ import annotations

import argparse
import sys

from ..ghl_client import GHLClient, GHLError
from ..store import Store


def main() -> None:
    parser = argparse.ArgumentParser(prog="find_client_contact")
    parser.add_argument("--q", required=True, help="company or contact name to search")
    args = parser.parse_args()

    store = Store()
    parent = next((s for s in store.load_subaccounts(active=False) if s.get("is_parent")), None)
    if not parent:
        print("no is_parent subaccount configured", file=sys.stderr)
        sys.exit(1)
    token = store.get_pit(parent["location_id"])
    if not token:
        print("no PIT stored for the parent — run pit set --location ssp first", file=sys.stderr)
        sys.exit(1)

    client = GHLClient(token)
    try:
        data = client.request("POST", "/contacts/search", json_body={
            "locationId": parent["location_id"],
            "page": 1,
            "pageLimit": 20,
            "query": args.q,
        })
    except GHLError as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        sys.exit(1)

    contacts = data.get("contacts") or data.get("data") or []
    if not contacts:
        print("no candidates found")
        return
    print(f"{'contact_id':<28} name / company")
    for contact in contacts:
        cid = contact.get("id") or contact.get("_id") or "?"
        name = " ".join(p for p in [contact.get("firstName"), contact.get("lastName")] if p)
        company = contact.get("companyName") or ""
        label = " — ".join(p for p in [name, company] if p) or "(no name)"
        print(f"{str(cid):<28} {label}")


if __name__ == "__main__":
    main()
