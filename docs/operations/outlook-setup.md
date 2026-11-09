# Connect an Outlook / Microsoft 365 calendar

The adapter ([`app/calendars/outlook_calendar.py`](../../app/calendars/outlook_calendar.py)) uses Microsoft Graph and behaves exactly like the Google adapter: one calendar per stylist, any non-free event blocks time, and assistant bookings carry the caller's phone in hidden extended properties. The same booking and recovery flow runs on top of it in [`tests/test_outlook_calendar.py`](../../tests/test_outlook_calendar.py).

## Option A: Outlook.com or one Microsoft 365 mailbox (delegated)

Good for a small salon where one account holds all stylist calendars.

1. Run [`infra/bootstrap.sh`](../../infra/bootstrap.sh) once; it creates the *Outlook calendar connector* app and prints `GRAPH_CLIENT_ID`.
2. On your computer or in Cloud Shell:
   ```bash
   pip install msal httpx
   python scripts/connect_outlook.py --client-id <GRAPH_CLIENT_ID> --create-calendars --vault <key-vault-name>
   ```
   Approve the device-code sign-in in the browser. The script creates *Salon – Lara / Marco / Sina* calendars if missing and prints `GRAPH_CALENDARS`.
3. Store the token cache in Key Vault (command printed by the script), then delete the local file.
4. Set repository variables `CALENDAR_BACKEND=outlook` and `GRAPH_CALENDARS=<printed JSON>`, then redeploy.

The app refreshes the token itself and writes rotated refresh tokens back to Key Vault.

## Option B: Microsoft 365 tenant, one mailbox per stylist (app-only, keyless)

For salons on Microsoft 365 Business. No user token at all: the Container App's managed identity calls Graph.

1. Grant the managed identity the Graph application permission `Calendars.ReadWrite` (an Entra admin does this once, e.g. with `New-MgServicePrincipalAppRoleAssignment`).
2. **Limit it to the stylists' mailboxes** with Exchange Online *RBAC for Applications* (or an application access policy), so the app cannot read other mailboxes.
3. Set `GRAPH_AUTH=app` and `GRAPH_CALENDARS={"lara": {"user": "lara@salon.ch", "calendar_id": ""}, …}` (empty `calendar_id` = the mailbox's default calendar).

## Smoke test

Add a "Lunch" event 12:00–13:00 tomorrow in Lara's calendar, then ask the prompt lab or call `check_availability` for 12:00. It must not be offered. Book a slot and check that the event appears in the right calendar.
