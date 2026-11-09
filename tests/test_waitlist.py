"""Own feature: Empty-Chair Recovery (waitlist + automatic slot offers)."""

import json
from datetime import datetime, timedelta

from app.calendars.base import NewAppointment

from .conftest import TZ, tool

TUE = "2026-11-10"
ANNA, BEN, CARLA = "+41791234567", "+41785556677", "+41764443322"


def fill_marco_tuesday_except(svc, hh=14, mm=0):
    """Marco fully booked on Tuesday, except one 30-min customer appointment we can cancel."""
    def block(h1, m1, h2, m2):
        svc.calendar.create(NewAppointment("marco", "block", datetime(2026, 11, 10, h1, m1, tzinfo=TZ),
                                           datetime(2026, 11, 10, h2, m2, tzinfo=TZ), "Staff block", "+41000000000"))
    block(9, 0, 13, 50)
    block(14, 40, 18, 30)


def test_full_day_waitlist_cancel_offer_accept(client, svc):
    fill_marco_tuesday_except(svc)
    booked = tool(client, "book_appointment", service="mens_cut", start=f"{TUE}T14:00", stylist="marco",
                  customer_name="Ben Keller", phone=BEN)
    assert booked["ok"]

    full = tool(client, "check_availability", service="mens_cut", date=TUE)
    assert full["available_on_requested_date"] is False and full["waitlist_possible"]

    w = tool(client, "join_waitlist", phone=ANNA, customer_name="Anna Meier", service="mens_cut",
             date_from=TUE, date_to=TUE)
    assert w["ok"] and w["position"] == 1

    cancel = tool(client, "cancel_appointment", phone=BEN, appointment_id=booked["appointment"]["appointment_id"])
    assert cancel["slot_offered_to_waitlist"] is True

    # The notification went out (outbox) with what an outbound agent needs.
    note = [json.loads(o["payload"]) for o in svc.store.outbox() if o["kind"] == "waitlist.offer"][0]
    assert note["phone"] == ANNA and note["start"] == f"{TUE}T14:00" and note["offer_id"]

    # Slot is held: another caller does not see it.
    other = tool(client, "check_availability", service="mens_cut", date=TUE)
    assert all(o["start"] != f"{TUE}T14:00" for o in other["options"])

    # Anna calls back (or the outbound agent reaches her) — lookup shows the offer.
    hello = tool(client, "lookup_caller", phone=ANNA)
    assert hello["pending_offer"]["offer_id"] == note["offer_id"]
    assert "PENDING WAITLIST OFFER" in hello["message"]

    accepted = tool(client, "accept_offer", phone=ANNA, offer_id=note["offer_id"])
    assert accepted["ok"] and accepted["appointment"]["start"] == f"{TUE}T14:00"
    assert svc.store.get_waitlist(w["waitlist_id"])["status"] == "booked"


def test_decline_cascades_to_next_person(client, svc):
    fill_marco_tuesday_except(svc)
    b = tool(client, "book_appointment", service="mens_cut", start=f"{TUE}T14:00", stylist="marco",
             customer_name="Ben Keller", phone=BEN)
    tool(client, "join_waitlist", phone=ANNA, customer_name="Anna Meier", service="mens_cut", date_from=TUE, date_to=TUE)
    tool(client, "join_waitlist", phone=CARLA, customer_name="Carla Rossi", service="mens_cut", date_from=TUE, date_to=TUE)
    tool(client, "cancel_appointment", phone=BEN, appointment_id=b["appointment"]["appointment_id"])

    first = svc.store.pending_offer_for_phone(ANNA)
    assert first and svc.store.pending_offer_for_phone(CARLA) is None
    r = tool(client, "decline_offer", phone=ANNA, offer_id=first["id"])
    assert r["ok"]
    assert svc.store.pending_offer_for_phone(CARLA) is not None
    assert svc.store.pending_offer_for_phone(ANNA) is None  # never offered the same slot twice


def test_expired_offer_cascades_on_sweep(client, svc, clock):
    fill_marco_tuesday_except(svc)
    b = tool(client, "book_appointment", service="mens_cut", start=f"{TUE}T14:00", stylist="marco",
             customer_name="Ben Keller", phone=BEN)
    tool(client, "join_waitlist", phone=ANNA, customer_name="Anna Meier", service="mens_cut", date_from=TUE, date_to=TUE)
    tool(client, "join_waitlist", phone=CARLA, customer_name="Carla Rossi", service="mens_cut", date_from=TUE, date_to=TUE)
    tool(client, "cancel_appointment", phone=BEN, appointment_id=b["appointment"]["appointment_id"])
    offer = svc.store.pending_offer_for_phone(ANNA)

    clock.now = clock.now + timedelta(minutes=21)
    assert svc.sweep()["expired_offers"] == 1
    assert svc.store.get_offer(offer["id"])["status"] == "expired"
    assert svc.store.pending_offer_for_phone(CARLA) is not None
    late = tool(client, "accept_offer", phone=ANNA, offer_id=offer["id"])
    assert late["ok"] is False


def test_waitlist_respects_stylist_and_time_of_day(client, svc):
    b = tool(client, "book_appointment", service="womens_cut", start="2026-11-11T10:00", stylist="sina",
             customer_name="Ben Keller", phone=BEN)
    tool(client, "join_waitlist", phone=ANNA, customer_name="Anna Meier", service="womens_cut",
         date_from="2026-11-11", date_to="2026-11-11", stylist="lara")
    tool(client, "join_waitlist", phone=CARLA, customer_name="Carla Rossi", service="womens_cut",
         date_from="2026-11-11", date_to="2026-11-11", time_of_day="afternoon")
    cancel = tool(client, "cancel_appointment", phone=BEN, appointment_id=b["appointment"]["appointment_id"])
    # Sina's morning slot fits neither Anna (wants Lara) nor Carla (afternoon only).
    assert cancel["slot_offered_to_waitlist"] is False


def test_long_service_not_offered_into_short_gap(client, svc):
    def block(h1, m1, h2, m2):
        svc.calendar.create(NewAppointment("lara", "block", datetime(2026, 11, 10, h1, m1, tzinfo=TZ),
                                           datetime(2026, 11, 10, h2, m2, tzinfo=TZ), "Staff block", "+41000000000"))
    block(9, 0, 13, 50)
    block(15, 10, 18, 30)
    b = tool(client, "book_appointment", service="womens_cut", start=f"{TUE}T14:00", stylist="lara",
             customer_name="Ben Keller", phone=BEN)
    tool(client, "join_waitlist", phone=ANNA, customer_name="Anna Meier", service="balayage", date_from=TUE, date_to=TUE)
    tool(client, "join_waitlist", phone=CARLA, customer_name="Carla Rossi", service="blow_dry", date_from=TUE, date_to=TUE)
    tool(client, "cancel_appointment", phone=BEN, appointment_id=b["appointment"]["appointment_id"])
    # 3h balayage does not fit the freed hour -> skipped although Anna is first; Carla's 30-min blow-dry fits.
    assert svc.store.pending_offer_for_phone(ANNA) is None
    offer = svc.store.pending_offer_for_phone(CARLA)
    assert offer is not None


def test_rejoining_waitlist_updates_instead_of_duplicating(client, svc):
    tool(client, "join_waitlist", phone=ANNA, customer_name="Anna Meier", service="balayage")
    r = tool(client, "join_waitlist", phone=ANNA, customer_name="Anna Meier", service="balayage", time_of_day="morning")
    assert r["position"] == 1
    assert len([w for w in svc.store.all_waitlist() if w["phone"] == ANNA]) == 1
