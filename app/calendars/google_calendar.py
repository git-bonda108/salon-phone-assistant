"""Google Calendar backend.

Setup (see docs/operations/google-calendar-setup.md):
  * one Google calendar per stylist (the owner keeps using it normally),
  * a Google Cloud service account, each stylist calendar shared with it
    ("Make changes to events"),
  * GOOGLE_SERVICE_ACCOUNT_JSON = the key file content or a path to it.

Busy time comes from the FreeBusy API, so *anything* in the calendar blocks
the slot — lunch breaks, holidays, walk-ins entered by staff.
Assistant-made bookings carry the caller's phone number in private extended
properties so we can find them again ("I want to cancel my appointment").
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from .base import Appointment, NewAppointment

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def build_google_service(service_account_json: str):
    """Create a Calendar API client from a key file path or the JSON itself."""
    from google.oauth2 import service_account  # imported lazily: optional dependency
    from googleapiclient.discovery import build

    if os.path.exists(service_account_json):
        with open(service_account_json, encoding="utf-8") as fh:
            info = json.load(fh)
    else:
        info = json.loads(service_account_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


class GoogleCalendar:
    def __init__(self, service, calendar_ids: dict[str, str], tz):
        """``calendar_ids`` maps stylist id -> Google calendar id."""
        missing = [k for k, v in calendar_ids.items() if not v]
        if missing:
            raise ValueError(f"No Google calendar id configured for stylists: {missing}")
        self.svc = service
        self.calendar_ids = calendar_ids
        self.tz = tz

    # appointment id = "<stylist>:<google event id>" so we know which calendar
    @staticmethod
    def _split(appointment_id: str) -> tuple[str, str]:
        stylist_id, _, event_id = appointment_id.partition(":")
        return stylist_id, event_id

    def _parse_dt(self, value: dict) -> datetime:
        raw = value.get("dateTime") or value.get("date")
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return dt.astimezone(self.tz)

    def _to_appt(self, stylist_id: str, ev: dict) -> Appointment:
        props = ev.get("extendedProperties", {}).get("private", {})
        return Appointment(
            id=f"{stylist_id}:{ev['id']}",
            stylist_id=stylist_id,
            service_id=props.get("service", ""),
            start=self._parse_dt(ev["start"]),
            end=self._parse_dt(ev["end"]),
            customer_name=props.get("customer", ev.get("summary", "")),
            phone=props.get("phone", ""),
            notes=ev.get("description", ""),
            source=props.get("source", "calendar"),
        )

    def busy(self, stylist_id, start, end):
        cal = self.calendar_ids[stylist_id]
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "timeZone": str(self.tz),
            "items": [{"id": cal}],
        }
        res = self.svc.freebusy().query(body=body).execute()
        cal_res = res.get("calendars", {}).get(cal, {})
        if cal_res.get("errors"):
            # Fail closed: if we cannot read the calendar, nothing is free.
            raise RuntimeError(f"FreeBusy error for {stylist_id}: {cal_res['errors']}")
        return [(self._parse_dt({"dateTime": b["start"]}), self._parse_dt({"dateTime": b["end"]}))
                for b in cal_res.get("busy", [])]

    def create(self, appt: NewAppointment) -> Appointment:
        cal = self.calendar_ids[appt.stylist_id]
        body = {
            "summary": f"{appt.customer_name} – {appt.service_id}",
            "description": (
                f"Booked by phone assistant\nPhone: {appt.phone}\n"
                + (f"Notes: {appt.notes}" if appt.notes else "")
            ),
            "start": {"dateTime": appt.start.isoformat(), "timeZone": str(self.tz)},
            "end": {"dateTime": appt.end.isoformat(), "timeZone": str(self.tz)},
            "extendedProperties": {"private": {
                "phone": appt.phone,
                "service": appt.service_id,
                "customer": appt.customer_name,
                "source": appt.source,
            }},
        }
        ev = self.svc.events().insert(calendarId=cal, body=body).execute()
        return self._to_appt(appt.stylist_id, ev)

    def get(self, appointment_id):
        stylist_id, event_id = self._split(appointment_id)
        if stylist_id not in self.calendar_ids or not event_id:
            return None
        try:
            ev = self.svc.events().get(calendarId=self.calendar_ids[stylist_id], eventId=event_id).execute()
        except Exception:  # googleapiclient HttpError 404/410
            return None
        if ev.get("status") == "cancelled":
            return None
        return self._to_appt(stylist_id, ev)

    def delete(self, appointment_id):
        stylist_id, event_id = self._split(appointment_id)
        self.svc.events().delete(calendarId=self.calendar_ids[stylist_id], eventId=event_id).execute()

    def _list(self, stylist_id: str, **kwargs) -> list[Appointment]:
        out: list[Appointment] = []
        page_token = None
        while True:
            res = self.svc.events().list(
                calendarId=self.calendar_ids[stylist_id], singleEvents=True, orderBy="startTime",
                pageToken=page_token, **kwargs,
            ).execute()
            out += [self._to_appt(stylist_id, ev) for ev in res.get("items", []) if ev.get("status") != "cancelled"]
            page_token = res.get("nextPageToken")
            if not page_token:
                return out

    def find_by_phone(self, phone, after):
        found: list[Appointment] = []
        for stylist_id in self.calendar_ids:
            found += self._list(stylist_id, timeMin=after.isoformat(), privateExtendedProperty=f"phone={phone}")
        return sorted(found, key=lambda a: a.start)

    def list_range(self, start, end):
        found: list[Appointment] = []
        for stylist_id in self.calendar_ids:
            found += self._list(stylist_id, timeMin=start.isoformat(), timeMax=end.isoformat())
        return sorted(found, key=lambda a: a.start)
