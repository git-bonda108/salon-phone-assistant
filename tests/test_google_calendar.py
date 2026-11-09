"""Google Calendar adapter against a fake API client (no network, no credentials)."""

from datetime import datetime

from app.calendars.base import NewAppointment
from app.calendars.google_calendar import GoogleCalendar

from .conftest import TZ


class Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeEvents:
    def __init__(self):
        self.calls = []
        self.items = {}

    def insert(self, calendarId, body):
        self.calls.append(("insert", calendarId, body))
        ev = {"id": f"ev{len(self.items) + 1}", **body}
        self.items[ev["id"]] = ev
        return Call(ev)

    def list(self, **kw):
        self.calls.append(("list", kw))
        phone = (kw.get("privateExtendedProperty") or "=").split("=", 1)[1]
        items = [e for e in self.items.values() if not phone or e["extendedProperties"]["private"]["phone"] == phone]
        return Call({"items": items})

    def get(self, calendarId, eventId):
        return Call(self.items[eventId])

    def delete(self, calendarId, eventId):
        self.calls.append(("delete", calendarId, eventId))
        self.items.pop(eventId)
        return Call({})


class FakeFreeBusy:
    def __init__(self, busy):
        self.busy = busy
        self.bodies = []

    def query(self, body):
        self.bodies.append(body)
        cal = body["items"][0]["id"]
        return Call({"calendars": {cal: {"busy": self.busy}}})


class FakeService:
    def __init__(self, busy=()):
        self._events = FakeEvents()
        self._fb = FakeFreeBusy(list(busy))

    def events(self):
        return self._events

    def freebusy(self):
        return self._fb


def test_create_stores_phone_in_private_properties_and_round_trips():
    svc = FakeService()
    cal = GoogleCalendar(svc, {"lara": "lara@group.calendar.google.com"}, TZ)
    appt = cal.create(NewAppointment("lara", "balayage", datetime(2026, 11, 10, 9, tzinfo=TZ),
                                     datetime(2026, 11, 10, 12, tzinfo=TZ), "Anna Meier", "+41791234567"))
    _, cal_id, body = svc._events.calls[0]
    assert cal_id == "lara@group.calendar.google.com"
    assert body["extendedProperties"]["private"] == {
        "phone": "+41791234567", "service": "balayage", "customer": "Anna Meier", "source": "phone-assistant"}
    assert body["start"]["timeZone"] == "Europe/Zurich"
    assert appt.id == "lara:ev1" and appt.start == datetime(2026, 11, 10, 9, tzinfo=TZ)

    found = cal.find_by_phone("+41791234567", datetime(2026, 11, 9, tzinfo=TZ))
    assert [a.id for a in found] == ["lara:ev1"]
    assert svc._events.calls[-1][1]["privateExtendedProperty"] == "phone=+41791234567"

    assert cal.get("lara:ev1").customer_name == "Anna Meier"
    cal.delete("lara:ev1")
    assert ("delete", "lara@group.calendar.google.com", "ev1") in svc._events.calls


def test_busy_comes_from_freebusy_in_local_time():
    svc = FakeService(busy=[{"start": "2026-11-10T11:00:00Z", "end": "2026-11-10T12:00:00Z"}])
    cal = GoogleCalendar(svc, {"lara": "cal-lara"}, TZ)
    busy = cal.busy("lara", datetime(2026, 11, 10, tzinfo=TZ), datetime(2026, 11, 11, tzinfo=TZ))
    assert busy == [(datetime(2026, 11, 10, 12, tzinfo=TZ), datetime(2026, 11, 10, 13, tzinfo=TZ))]


def test_unknown_or_malformed_ids_return_none():
    cal = GoogleCalendar(FakeService(), {"lara": "cal-lara"}, TZ)
    assert cal.get("nobody:ev1") is None
    assert cal.get("garbage") is None


def test_missing_calendar_id_fails_fast():
    import pytest
    with pytest.raises(ValueError):
        GoogleCalendar(FakeService(), {"lara": ""}, TZ)
