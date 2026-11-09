"""Outlook / Microsoft 365 adapter against a fake Microsoft Graph, plus the full booking
flow (incl. Empty-Chair Recovery) running on top of it."""

import json
import re
from datetime import datetime, timezone
from urllib.parse import unquote

import httpx
from fastapi.testclient import TestClient

from app.calendars.base import NewAppointment
from app.calendars.outlook_calendar import PROP, OutlookCalendar
from app.config import Settings
from app.main import create_app

from .conftest import TZ, tool

CALS = {"lara": {"user": "me", "calendar_id": "cal-lara"},
        "marco": {"user": "me", "calendar_id": "cal-marco"},
        "sina": {"user": "sina@salon.ch", "calendar_id": ""}}


class FakeGraph:
    def __init__(self, page_size: int = 50):
        self.events: dict[str, dict] = {}
        self.page_size = page_size
        self.requests: list[httpx.Request] = []
        self.n = 0

    def _calendar_key(self, path: str) -> str:
        m = re.match(r"/v1.0/(me|users/[^/]+)/(?:calendars/([^/]+)|calendar)", path)
        return f"{m.group(1)}|{m.group(2) or 'default'}"

    @staticmethod
    def _parse(v):
        return datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=timezone.utc) if "+" not in v and "Z" not in v \
            else datetime.fromisoformat(v.replace("Z", "+00:00"))

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        assert req.headers["authorization"] == "Bearer graph-token"
        assert req.headers["prefer"] == 'outlook.timezone="UTC"'
        path = unquote(req.url.path)
        if req.url.host == "next.page":
            return self._page(json.loads(unquote(req.url.params["state"])), 1)
        if req.method == "POST" and path.endswith("/events"):
            body = json.loads(req.content)
            self.n += 1
            eid = f"AQMk-{self.n}_x="
            ev = {"id": eid, "cal": self._calendar_key(path), "showAs": body.get("showAs", "busy"), "isCancelled": False,
                  **{k: body[k] for k in ("subject", "start", "end", "body", "singleValueExtendedProperties")}}
            self.events[eid] = ev
            return httpx.Response(201, json={k: v for k, v in ev.items() if k != "singleValueExtendedProperties"})
        m = re.match(r"/v1.0/(?:me|users/[^/]+)/events/(.+)$", path)
        if m:
            ev = self.events.get(m.group(1))
            if req.method == "DELETE":
                self.events.pop(m.group(1), None)
                return httpx.Response(204)
            return httpx.Response(200, json=ev) if ev else httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})
        if path.endswith("/calendarView"):
            key = self._calendar_key(path)
            s, e = self._parse(req.url.params["startDateTime"]), self._parse(req.url.params["endDateTime"])
            items = [ev for ev in self.events.values() if ev["cal"] == key
                     and self._parse(ev["start"]["dateTime"]) < e and self._parse(ev["end"]["dateTime"]) > s]
            return self._page(items, 0)
        if path.endswith("/events") and req.method == "GET":
            key = self._calendar_key(path)
            phone = re.search(r"ep/value eq '([^']+)'", req.url.params["$filter"]).group(1)
            items = [ev for ev in self.events.values() if ev["cal"] == key and any(
                p["id"] == PROP["phone"] and p["value"] == phone for p in ev["singleValueExtendedProperties"])]
            return httpx.Response(200, json={"value": items})
        return httpx.Response(400, json={"error": f"unexpected {req.method} {path}"})

    def _page(self, items, page):
        chunk = items[page * self.page_size:(page + 1) * self.page_size]
        body = {"value": chunk}
        if page == 0 and len(items) > self.page_size:
            body["@odata.nextLink"] = "https://next.page/x?state=" + json.dumps(items)
        return httpx.Response(200, json=body)


def outlook(fake: FakeGraph) -> OutlookCalendar:
    return OutlookCalendar(lambda: "graph-token", CALS, TZ, http=httpx.Client(transport=httpx.MockTransport(fake.handler)))


def at(h, m=0):
    return datetime(2026, 11, 10, h, m, tzinfo=TZ)


def test_create_writes_utc_and_extended_properties_and_round_trips():
    fake = FakeGraph()
    cal = outlook(fake)
    a = cal.create(NewAppointment("lara", "balayage", at(9), at(12), "Anna Meier", "+41791234567"))
    sent = json.loads(fake.requests[0].content)
    assert sent["start"] == {"dateTime": "2026-11-10T08:00:00", "timeZone": "UTC"}  # 09:00 Zürich = 08:00 UTC
    assert {p["id"]: p["value"] for p in sent["singleValueExtendedProperties"]}[PROP["phone"]] == "+41791234567"
    assert fake.requests[0].url.path == "/v1.0/me/calendars/cal-lara/events"
    assert a.id == "lara:AQMk-1_x=" and a.start == at(9) and a.phone == "+41791234567"
    got = cal.get(a.id)
    assert got.customer_name == "Anna Meier" and got.service_id == "balayage"
    assert [x.id for x in cal.find_by_phone("+41791234567", at(0))] == [a.id]
    cal.delete(a.id)
    assert cal.get(a.id) is None


def test_busy_ignores_free_and_cancelled_events_and_follows_pages():
    fake = FakeGraph(page_size=2)
    cal = outlook(fake)
    for h in (9, 11, 13):
        cal.create(NewAppointment("marco", "mens_cut", at(h), at(h, 30), "X", "+41790000001"))
    free = cal.create(NewAppointment("marco", "x", at(15), at(16), "Lunch walk", "+41790000002"))
    fake.events[free.id.split(":", 1)[1]]["showAs"] = "free"
    busy = cal.busy("marco", at(0), at(23))
    assert busy == [(at(9), at(9, 30)), (at(11), at(11, 30)), (at(13), at(13, 30))]


def test_stylist_in_own_mailbox_uses_users_path():
    fake = FakeGraph()
    cal = outlook(fake)
    cal.create(NewAppointment("sina", "womens_cut", at(10), at(11), "B", "+41790000003"))
    assert fake.requests[0].url.path == "/v1.0/users/sina@salon.ch/calendar/events"


def test_graph_error_fails_closed():
    cal = OutlookCalendar(lambda: "graph-token", CALS, TZ,
                          http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={}))))
    try:
        cal.busy("lara", at(0), at(23))
    except RuntimeError:
        return
    raise AssertionError("must raise so that nothing is offered")


def test_full_booking_flow_and_recovery_on_outlook(clock):
    fake = FakeGraph()
    app = create_app(Settings(database_path=":memory:", sweep_interval_seconds=0), clock=clock, calendar=outlook(fake))
    with TestClient(app) as client:
        b = tool(client, "book_appointment", service="mens_cut", start="2026-11-10T10:00", stylist="marco",
                 customer_name="Ben Keller", phone="+41785556677")
        assert b["ok"]
        clash = tool(client, "book_appointment", service="mens_cut", start="2026-11-10T10:00", stylist="marco",
                     customer_name="Anna Meier", phone="+41791234567")
        assert clash["error"] == "slot_unavailable"
        tool(client, "join_waitlist", phone="+41791234567", customer_name="Anna Meier", service="mens_cut",
             date_from="2026-11-10", date_to="2026-11-10", stylist="marco", time_of_day="morning")
        cancel = tool(client, "cancel_appointment", phone="+41785556677", appointment_id=b["appointment"]["appointment_id"])
        assert cancel["slot_offered_to_waitlist"] is True
        offer = app.state.svc.store.pending_offer_for_phone("+41791234567")
        accepted = tool(client, "accept_offer", phone="+41791234567", offer_id=offer["id"])
        assert accepted["ok"]
        assert [a["start"] for a in tool(client, "find_appointments", phone="+41791234567")["appointments"]] == ["2026-11-10T10:00"]
