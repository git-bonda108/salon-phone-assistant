"""One tool registry: Suisse Line config, HTTP and MCP derive from app/tools.py."""

import subprocess
import sys
from pathlib import Path

from app.tools import BY_NAME, CALLER_TOOLS, OWNER_TOOLS

from .conftest import tool

ROOT = Path(__file__).resolve().parent.parent


def test_committed_tools_json_is_generated_from_registry():
    r = subprocess.run([sys.executable, "scripts/export_tools.py", "--check"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_owner_tools_are_not_reachable_with_the_caller_endpoint(client):
    assert {t.name for t in OWNER_TOOLS} == {"list_appointments", "list_waitlist", "salon_stats"}
    for t in OWNER_TOOLS:
        assert client.post(f"/tools/{t.name}", json={}).status_code == 404


def test_every_registry_method_exists(svc):
    for t in BY_NAME.values():
        assert callable(getattr(svc, t.method)), t.name
    assert all(t.audience == "caller" for t in CALLER_TOOLS)


def test_stats_count_bookings_and_recovered_revenue(client, svc):
    from .test_waitlist import fill_marco_tuesday_except
    fill_marco_tuesday_except(svc)
    b = tool(client, "book_appointment", service="mens_cut", start="2026-11-10T14:00", stylist="marco",
             customer_name="Ben Keller", phone="+41785556677")
    tool(client, "join_waitlist", phone="+41791234567", customer_name="Anna Meier", service="mens_cut",
         date_from="2026-11-10", date_to="2026-11-10")
    tool(client, "cancel_appointment", phone="+41785556677", appointment_id=b["appointment"]["appointment_id"])
    offer = svc.store.pending_offer_for_phone("+41791234567")
    tool(client, "accept_offer", phone="+41791234567", offer_id=offer["id"])
    s = svc.salon_stats(30)
    assert (s["bookings"], s["cancellations"], s["offers_sent"], s["slots_recovered"], s["recovered_chf"]) == (2, 1, 1, 1, 49)


def test_reschedule_is_not_counted_as_a_booking(client, svc):
    b = tool(client, "book_appointment", service="womens_cut", start="2026-11-10T14:00", stylist="lara",
             customer_name="Anna Meier", phone="+41791234567")
    tool(client, "reschedule_appointment", phone="+41791234567", appointment_id=b["appointment"]["appointment_id"],
         new_start="2026-11-10T15:00")
    s = svc.salon_stats(30)
    assert s["bookings"] == 1 and s["reschedules"] == 1


def test_owner_list_tools(client, svc):
    tool(client, "book_appointment", service="womens_cut", start="2026-11-10T14:00", stylist="lara",
         customer_name="Anna Meier", phone="+41791234567")
    r = svc.list_appointments("2026-11-10", "2026-11-10")
    assert r["ok"] and r["appointments"][0]["customer_name"] == "Anna Meier"
    assert svc.list_appointments(stylist="marco")["appointments"] == []
    assert svc.list_waitlist()["ok"]
