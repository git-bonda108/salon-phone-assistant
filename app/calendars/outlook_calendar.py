"""Microsoft 365 / Outlook calendar backend (Microsoft Graph).

* One calendar per stylist, either in one mailbox ("me" with delegated auth, e.g. an
  outlook.com account) or in each stylist's own Microsoft 365 mailbox (app-only auth
  through the Container App's managed identity: keyless).
* Busy time = every event in ``calendarView`` that is not ``showAs: free``, so staff
  entries (lunch, holidays, walk-ins) block the slot, exactly like the Google adapter.
* Assistant bookings carry phone, service and customer as **single-value extended
  properties** (invisible to users, filterable via Graph), so "cancel my appointment"
  finds the right event without parsing titles.
* All times exchanged in UTC (``Prefer: outlook.timezone="UTC"``) and converted to
  Europe/Zurich at the edge.
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote

import httpx

from .base import Appointment, NewAppointment

GRAPH = "https://graph.microsoft.com/v1.0"
_NS = "{8a6f9a52-3c1e-4b7a-9d0e-5c2b7f1e4a10}"  # our property namespace (GUID)
PROP = {k: f"String {_NS} Name salon{k.capitalize()}" for k in ("phone", "service", "customer", "source")}
_EXPAND = "singleValueExtendedProperties($filter=" + " or ".join(f"id eq '{v}'" for v in PROP.values()) + ")"
_UTC_FMT = "%Y-%m-%dT%H:%M:%S"


class GraphError(RuntimeError):
    pass


class OutlookCalendar:
    def __init__(self, token_provider: Callable[[], str], calendars: dict[str, dict], tz,
                 http: httpx.Client | None = None):
        """``calendars``: stylist id -> {"user": "me" | UPN/object id, "calendar_id": id ("" = default calendar)}."""
        missing = [k for k, v in calendars.items() if not v or "calendar_id" not in v]
        if missing or not calendars:
            raise ValueError(f"No Outlook calendar configured for stylists: {missing or 'all'}")
        self.token = token_provider
        self.calendars = calendars
        self.tz = tz
        self.http = http or httpx.Client(timeout=15)

    # ---------------------------------------------------------------- plumbing
    def _user(self, stylist_id: str) -> str:
        user = self.calendars[stylist_id].get("user", "me")
        return "/me" if user == "me" else f"/users/{quote(user)}"

    def _cal(self, stylist_id: str) -> str:
        cid = self.calendars[stylist_id].get("calendar_id") or ""
        return f"{self._user(stylist_id)}/calendars/{quote(cid, safe='')}" if cid else f"{self._user(stylist_id)}/calendar"

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token()}", "Prefer": 'outlook.timezone="UTC"'}
        full = url if url.startswith("http") else GRAPH + url
        for attempt in range(3):
            r = self.http.request(method, full, headers=headers, **kw)
            if r.status_code in (429, 503) and attempt < 2:  # Graph throttling: honour Retry-After
                _time.sleep(min(float(r.headers.get("Retry-After", "1")), 5))
                continue
            return r
        return r

    def _pages(self, url: str, params: dict | None = None) -> list[dict]:
        items, nxt, first = [], url, True
        while nxt:
            r = self._req("GET", nxt, params=params if first else None)
            first = False
            if r.status_code >= 400:
                raise GraphError(f"Graph {r.status_code}: {r.text[:300]}")  # fail closed
            body = r.json()
            items += body.get("value", [])
            nxt = body.get("@odata.nextLink")
        return items

    def _dt(self, value: dict) -> datetime:
        raw = value["dateTime"].split(".")[0]
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self.tz)

    @staticmethod
    def _utc(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime(_UTC_FMT)

    def _to_appt(self, stylist_id: str, ev: dict) -> Appointment:
        props = {p["id"]: p.get("value", "") for p in ev.get("singleValueExtendedProperties", []) or []}
        get = lambda k: props.get(PROP[k], "")  # noqa: E731
        return Appointment(
            id=f"{stylist_id}:{ev['id']}",
            stylist_id=stylist_id,
            service_id=get("service"),
            start=self._dt(ev["start"]),
            end=self._dt(ev["end"]),
            customer_name=get("customer") or ev.get("subject", ""),
            phone=get("phone"),
            notes=(ev.get("body") or {}).get("content", "") if isinstance(ev.get("body"), dict) else "",
            source=get("source") or "calendar",
        )

    def _view(self, stylist_id: str, start: datetime, end: datetime, expand: bool) -> list[dict]:
        params = {"startDateTime": start.astimezone(timezone.utc).isoformat(),
                  "endDateTime": end.astimezone(timezone.utc).isoformat(),
                  "$select": "id,subject,start,end,showAs,isCancelled,body", "$top": "200"}
        if expand:
            params["$expand"] = _EXPAND
        return self._pages(f"{self._cal(stylist_id)}/calendarView", params)

    # ------------------------------------------------------------ interface
    def busy(self, stylist_id, start, end):
        return [(self._dt(e["start"]), self._dt(e["end"])) for e in self._view(stylist_id, start, end, expand=False)
                if not e.get("isCancelled") and e.get("showAs", "busy") != "free"]

    def create(self, appt: NewAppointment) -> Appointment:
        body = {
            "subject": f"{appt.customer_name} – {appt.service_id}",
            "body": {"contentType": "text",
                     "content": f"Booked by phone assistant. Phone: {appt.phone}" + (f"\nNotes: {appt.notes}" if appt.notes else "")},
            "start": {"dateTime": self._utc(appt.start), "timeZone": "UTC"},
            "end": {"dateTime": self._utc(appt.end), "timeZone": "UTC"},
            "showAs": "busy",
            "isReminderOn": False,
            "singleValueExtendedProperties": [
                {"id": PROP["phone"], "value": appt.phone},
                {"id": PROP["service"], "value": appt.service_id},
                {"id": PROP["customer"], "value": appt.customer_name},
                {"id": PROP["source"], "value": appt.source},
            ],
        }
        r = self._req("POST", f"{self._cal(appt.stylist_id)}/events", json=body)
        if r.status_code >= 400:
            raise GraphError(f"Graph {r.status_code}: {r.text[:300]}")
        ev = r.json()
        ev.setdefault("singleValueExtendedProperties", body["singleValueExtendedProperties"])
        return self._to_appt(appt.stylist_id, ev)

    def _split(self, appointment_id: str) -> tuple[str, str]:
        stylist_id, _, event_id = appointment_id.partition(":")
        return stylist_id, event_id

    def get(self, appointment_id):
        stylist_id, event_id = self._split(appointment_id)
        if stylist_id not in self.calendars or not event_id:
            return None
        r = self._req("GET", f"{self._user(stylist_id)}/events/{quote(event_id, safe='')}", params={"$expand": _EXPAND})
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise GraphError(f"Graph {r.status_code}: {r.text[:300]}")
        ev = r.json()
        return None if ev.get("isCancelled") else self._to_appt(stylist_id, ev)

    def delete(self, appointment_id):
        stylist_id, event_id = self._split(appointment_id)
        r = self._req("DELETE", f"{self._user(stylist_id)}/events/{quote(event_id, safe='')}")
        if r.status_code >= 400 and r.status_code != 404:
            raise GraphError(f"Graph {r.status_code}: {r.text[:300]}")

    def find_by_phone(self, phone, after):
        flt = f"singleValueExtendedProperties/Any(ep: ep/id eq '{PROP['phone']}' and ep/value eq '{phone}')"
        out: list[Appointment] = []
        for stylist_id in self.calendars:
            for ev in self._pages(f"{self._cal(stylist_id)}/events", {"$filter": flt, "$expand": _EXPAND, "$top": "50"}):
                a = self._to_appt(stylist_id, ev)
                if a.end > after and not ev.get("isCancelled"):
                    out.append(a)
        return sorted(out, key=lambda a: a.start)

    def list_range(self, start, end):
        out: list[Appointment] = []
        for stylist_id in self.calendars:
            out += [self._to_appt(stylist_id, e) for e in self._view(stylist_id, start, end, expand=True)
                    if not e.get("isCancelled")]
        return sorted(out, key=lambda a: a.start)


# ----------------------------------------------------------------- auth helpers
GRAPH_SCOPE_APP = "https://graph.microsoft.com/.default"
GRAPH_SCOPES_DELEGATED = ["Calendars.ReadWrite"]


def app_token_provider() -> Callable[[], str]:
    """App-only (Microsoft 365 tenant): managed identity with the Calendars.ReadWrite app role. Keyless."""
    from azure.identity import DefaultAzureCredential

    cred = DefaultAzureCredential()
    return lambda: cred.get_token(GRAPH_SCOPE_APP).token


def delegated_token_provider(client_id: str, cache_source: str, persist: Callable[[str], None] | None = None,
                             authority: str = "https://login.microsoftonline.com/common") -> Callable[[], str]:
    """Delegated (e.g. an outlook.com account): MSAL refresh token from a serialized cache.

    ``cache_source`` is the cache JSON or a path to it (in Azure: a Key Vault secret).
    ``persist`` is called with the new cache when MSAL rotated the refresh token.
    """
    import os

    import msal

    cache = msal.SerializableTokenCache()
    path = cache_source if cache_source and os.path.exists(cache_source) else None
    cache.deserialize(open(path, encoding="utf-8").read() if path else cache_source)
    client = msal.PublicClientApplication(client_id, authority=authority, token_cache=cache)

    def provide() -> str:
        accounts = client.get_accounts()
        if not accounts:
            raise GraphError("No signed-in Outlook account in the token cache: run scripts/connect_outlook.py")
        res = client.acquire_token_silent(GRAPH_SCOPES_DELEGATED, account=accounts[0])
        if not res or "access_token" not in res:
            raise GraphError(f"Outlook token refresh failed: {res and res.get('error_description')}")
        if cache.has_state_changed:
            data = cache.serialize()
            if path:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(data)
            elif persist:
                persist(data)
        return res["access_token"]

    return provide
