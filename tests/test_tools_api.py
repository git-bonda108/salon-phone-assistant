"""End-to-end tests of the live-tool API, written as phone-call scenarios."""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from .conftest import tool

ANNA = "079 123 45 67"
ANNA_E164 = "+41791234567"
BEN = "+41 78 555 66 77"


def test_new_caller_books_womens_cut(client):
    hello = tool(client, "lookup_caller", phone=ANNA)
    assert hello["known"] is False and hello["today"].startswith("Monday 9 November")

    avail = tool(client, "check_availability", service="Damenschnitt", date="2026-11-10", preferred_time="14:00")
    assert avail["requested_time_available"] is True
    first = avail["options"][0]
    assert first["start"] == "2026-11-10T14:00" and first["stylist"] == "lara"

    booked = tool(client, "book_appointment", service="womens_cut", start=first["start"], stylist="lara",
                  customer_name="Anna Meier", phone=ANNA)
    assert booked["ok"] and booked["appointment"]["price_chf"] == 89

    again = tool(client, "lookup_caller", phone="+41791234567")
    assert again["known"] and again["name"] == "Anna Meier"
    assert again["last_stylist"] == "lara" and len(again["upcoming"]) == 1


def test_double_booking_is_prevented_and_alternatives_offered(client):
    tool(client, "book_appointment", service="mens_cut", start="2026-11-10T10:00", stylist="marco",
         customer_name="Ben Keller", phone=BEN)
    clash = tool(client, "book_appointment", service="mens_cut", start="2026-11-10T10:00", stylist="marco",
                 customer_name="Anna Meier", phone=ANNA)
    assert clash["ok"] is False and clash["error"] == "slot_unavailable"
    assert clash["options"] and all(o["start"] != "2026-11-10T10:00" for o in clash["options"])


def test_retry_of_same_booking_is_idempotent(client, svc):
    args = dict(service="mens_cut", start="2026-11-10T10:00", stylist="marco", customer_name="Ben Keller", phone=BEN)
    tool(client, "book_appointment", **args)
    second = tool(client, "book_appointment", **args)
    assert second["ok"] and second["already_booked"]
    assert len(svc.calendar.find_by_phone("+41785556677", svc.now())) == 1


def test_any_stylist_booking_picks_a_free_one(client):
    tool(client, "book_appointment", service="womens_cut", start="2026-11-11T10:00", stylist="lara",
         customer_name="X Y", phone="+41790000001")
    r = tool(client, "book_appointment", service="womens_cut", start="2026-11-11T10:00",
             customer_name="Anna Meier", phone=ANNA)
    assert r["ok"] and r["appointment"]["stylist"] == "sina"


def test_wrong_stylist_unknown_service_and_closed_day(client):
    r = tool(client, "check_availability", service="beard", stylist="lara")
    assert r["ok"] is False and "Marco" in r["message"]
    r = tool(client, "check_availability", service="tattoo")
    assert r["ok"] is False and r["error"] == "unknown_service" and "Balayage" in r["message"]
    r = tool(client, "check_availability", service="mens_cut", date="2026-11-16")  # Monday
    assert r["available_on_requested_date"] is False and "closed on Mondays" in r["message"]
    assert r["options"][0]["start"].startswith("2026-11-17")


def test_relative_dates_and_past_dates(client):
    r = tool(client, "check_availability", service="mens_cut", date="tomorrow")
    assert r["date"] == "2026-11-10"
    r = tool(client, "check_availability", service="mens_cut", date="2026-11-01")
    assert r["ok"] is False and r["error"] == "date_in_past"


def test_asap_without_date(client):
    r = tool(client, "check_availability", service="balayage")
    assert r["options"][0]["start"] == "2026-11-10T09:00"


def test_cancel_only_own_appointment_and_late_flag(client, clock):
    b = tool(client, "book_appointment", service="mens_cut", start="2026-11-10T11:00", stylist="marco",
             customer_name="Ben Keller", phone=BEN)
    appt_id = b["appointment"]["appointment_id"]
    stranger = tool(client, "cancel_appointment", phone=ANNA, appointment_id=appt_id)
    assert stranger["ok"] is False and stranger["error"] == "appointment_not_found"

    clock.now = clock.now + timedelta(hours=20)  # Tue 06:00, 5h before
    r = tool(client, "cancel_appointment", phone="0785556677", appointment_id=appt_id)
    assert r["ok"] and r["late_cancellation"] is True
    assert tool(client, "find_appointments", phone=BEN)["appointments"] == []


def test_reschedule_by_30_minutes_same_stylist(client):
    b = tool(client, "book_appointment", service="womens_cut", start="2026-11-10T14:00", stylist="lara",
             customer_name="Anna Meier", phone=ANNA)
    r = tool(client, "reschedule_appointment", phone=ANNA, appointment_id=b["appointment"]["appointment_id"],
             new_start="2026-11-10T14:30")
    assert r["ok"] and r["appointment"]["start"] == "2026-11-10T14:30" and r["appointment"]["stylist"] == "lara"
    appts = tool(client, "find_appointments", phone=ANNA)["appointments"]
    assert [a["start"] for a in appts] == ["2026-11-10T14:30"]


def test_failed_reschedule_keeps_original(client):
    tool(client, "book_appointment", service="mens_cut", start="2026-11-10T15:00", stylist="marco",
         customer_name="Other", phone="+41790000002")
    b = tool(client, "book_appointment", service="mens_cut", start="2026-11-10T10:00", stylist="marco",
             customer_name="Ben Keller", phone=BEN)
    r = tool(client, "reschedule_appointment", phone=BEN, appointment_id=b["appointment"]["appointment_id"],
             new_start="2026-11-10T15:00", stylist="marco")
    assert r["ok"] is False
    appts = tool(client, "find_appointments", phone=BEN)["appointments"]
    assert [a["start"] for a in appts] == ["2026-11-10T10:00"]


def test_wrapped_arguments_and_invalid_input_are_handled(client):
    r = client.post("/tools/check_availability", json={"arguments": {"service": "mens_cut", "date": "2026-11-10"}})
    assert r.json()["ok"]
    r = client.post("/tools/book_appointment", json={"service": "mens_cut"})
    assert r.status_code == 200 and r.json()["error"] == "invalid_input" and "start" in r.json()["message"]
    r = tool(client, "check_availability", service="mens_cut", date="next blue moon")
    assert r["ok"] is False and r["error"] == "invalid_input"


def test_api_key_required_when_configured(clock):
    app = create_app(Settings(database_path=":memory:", sweep_interval_seconds=0, tool_api_key="s3cret"), clock=clock)
    c = TestClient(app)
    assert c.post("/tools/lookup_caller", json={}).status_code == 401
    assert c.post("/tools/lookup_caller", json={}, headers={"X-Api-Key": "s3cret"}).status_code == 200
    assert c.post("/tools/lookup_caller", json={}, headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.post("/tools/lookup_caller", json={}, headers={"X-Api-Key": "nope"}).status_code == 401


def test_admin_dashboard_renders(client):
    tool(client, "book_appointment", service="mens_cut", start="2026-11-10T10:00", stylist="marco",
         customer_name="Ben <script>", phone=BEN)
    html = client.get("/admin").text
    assert "Upcoming appointments" in html and "Ben &lt;script&gt;" in html
