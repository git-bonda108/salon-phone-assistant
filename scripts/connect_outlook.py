"""Connect an Outlook / Microsoft 365 account (delegated) and prepare the stylist calendars.

Run once, on your own machine or in Azure Cloud Shell:

    pip install msal httpx
    python scripts/connect_outlook.py --client-id <app id from infra/bootstrap.sh> [--create-calendars]

1. Signs you in with a device code (you approve in the browser).
2. Lists your calendars; with --create-calendars, creates "Salon – <Stylist>" calendars for
   every stylist in data/salon.json that does not have one yet.
3. Writes graph-token-cache.json and prints:
   * GRAPH_CALENDARS (stylist -> calendar id) for the app configuration,
   * the az command that stores the token cache in Key Vault (secret "graph-token-cache").

The token cache holds a refresh token: treat the file like a password and delete it after upload.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx
import msal

ROOT = Path(__file__).resolve().parent.parent
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Calendars.ReadWrite"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-id", required=True, help="public client app id (created by infra/bootstrap.sh)")
    ap.add_argument("--authority", default="https://login.microsoftonline.com/common")
    ap.add_argument("--create-calendars", action="store_true")
    ap.add_argument("--vault", default="", help="Key Vault name, to print the upload command")
    ap.add_argument("--out", default="graph-token-cache.json")
    args = ap.parse_args()

    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(args.client_id, authority=args.authority, token_cache=cache)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print("Could not start device flow:", flow)
        return 1
    print(flow["message"], flush=True)
    res = app.acquire_token_by_device_flow(flow)
    if "access_token" not in res:
        print("Sign-in failed:", res.get("error_description"))
        return 1
    headers = {"Authorization": f"Bearer {res['access_token']}"}

    cals = httpx.get(f"{GRAPH}/me/calendars?$select=id,name&$top=100", headers=headers, timeout=20).json()["value"]
    by_name = {c["name"]: c["id"] for c in cals}
    salon = json.loads((ROOT / "data" / "salon.json").read_text(encoding="utf-8"))
    mapping = {}
    for st in salon["stylists"]:
        name = f"Salon – {st['name']}"
        if name not in by_name and args.create_calendars:
            r = httpx.post(f"{GRAPH}/me/calendars", headers=headers, json={"name": name}, timeout=20)
            r.raise_for_status()
            by_name[name] = r.json()["id"]
            print(f"created calendar {name}")
        if name in by_name:
            mapping[st["id"]] = {"user": "me", "calendar_id": by_name[name]}
        else:
            print(f"! no calendar '{name}' (use --create-calendars)")

    Path(args.out).write_text(cache.serialize(), encoding="utf-8")
    print("\nSigned in as:", res.get("id_token_claims", {}).get("preferred_username", "?"))
    print("\nGRAPH_CALENDARS=" + json.dumps(mapping))
    vault = args.vault or "<key-vault-name>"
    print(f"\nStore the token cache in Key Vault, then delete the local file:\n"
          f"  az keyvault secret set --vault-name {vault} --name graph-token-cache --file {args.out}\n"
          f"  rm {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
