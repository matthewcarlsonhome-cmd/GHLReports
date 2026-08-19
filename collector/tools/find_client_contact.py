"""Find a client's contact ID inside the SSP parent account (spec section 6).

    python -m collector.tools.find_client_contact --q "<company name>"

Searches the parent subaccount's contacts by free-text query and prints
candidates so the ID can be copied into subaccounts.ssp_client_contact_id.

How this fits in
----------------
Relationship metrics (last touch, next appointment with a client) come from
SSP's own CRM — the "parent" account — where each client
company exists as a contact. The collector links a client location to that
contact through the subaccounts.ssp_client_contact_id column, and this
one-shot admin CLI is how a human finds the right id to put there during
onboarding. Without it set, collect_location records the relationship source
as skipped.

Key ideas to understand this file
---------------------------------
* Uses the same building blocks as the collector: Store to load config and
  fetch the parent's PIT from Vault, then GHLClient for one authenticated
  POST /contacts/search against the parent location.
* Prints up to 20 candidates (id + name/company); the human picks the right
  one and updates the subaccounts row by hand. Nothing is written here.
* Exit codes: 0 on success (even with zero candidates), 1 when the parent
  is unconfigured, has no token, or the search request fails.
"""

from __future__ import annotations

import argparse
import sys

from ..ghl_client import GHLClient, GHLError
from ..store import Store


def main() -> None:
    """Search the parent account's contacts and print candidate ids."""
    parser = argparse.ArgumentParser(prog="find_client_contact")
    parser.add_argument("--q", required=True, help="company or contact name to search")
    args = parser.parse_args()

    # Locate the parent subaccount and its stored token — both must exist.
    store = Store()
    parent = next((s for s in store.load_subaccounts(active=False) if s.get("is_parent")), None)
    if not parent:
        print("no is_parent subaccount configured", file=sys.stderr)
        sys.exit(1)
    token = store.get_pit(parent["location_id"])
    if not token:
        print("no PIT stored for the parent — run pit set --location ssp first", file=sys.stderr)
        sys.exit(1)

    # One free-text contact search against the parent location (max 20 hits).
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

    # Print a small table; the API's envelope key varies, hence the fallbacks.
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
