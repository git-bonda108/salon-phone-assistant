"""Booking service: everything the phone assistant can do, independent of HTTP.

Each public method returns a JSON-able dict with ``ok`` and a short English
``message`` the voice agent can paraphrase in the caller's language. Expected
problems raise ``ToolError`` with a speakable message (never a stack trace).

Own feature — *Empty-Chair Recovery*:
  When the salon is full, the assistant puts the caller on a waitlist
  (service, stylist, date range, time of day). The moment any appointment is
  cancelled or moved, the freed time is matched against the waitlist and the
  first fitting person gets an offer (outbound call / SMS via webhook). The
  slot is held for them for a short time; on decline or timeout it cascades
  to the next person. A last-minute cancellation becomes a booking instead
  of an empty chair.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Callable

from .calendars.base import Appointment, CalendarBackend, NewAppointment
from .notifier import Notifier
from .salon import Salon, Service, Stylist
from .slots import Slot, free_slots_for_day, overlaps, pick_options
from .store import Store
from .timeutil import (
    TIME_OF_DAY,
    InputError,
    local_iso,
    normalize_phone,
    parse_date,
    parse_local_datetime,
    parse_time,
    speak_date,
    speak_datetime,
)

log = logging.getLogger("booking")


class ToolError(Exception):
    def __init__(self, message: str, code: str = "error", **extra):
        super().__init__(message)
        self.message = message
        self.code = code
        self.extra = extra

    def to_dict(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message, **self.extra}


class BookingService:
    def __init__(
        self,
        salon: Salon,
        calendar: CalendarBackend,
        store: Store,
        notifier: Notifier,
        clock: Callable[[], datetime] | None = None,
        offer_ttl_minutes: int = 20,
        search_days: int = 14,
    ):
        self.salon = salon
        self.calendar = calendar
        self.store = store
        self.notifier = notifier
        self._clock = clock
        self.offer_ttl = timedelta(minutes=offer_ttl_minutes)
        self.search_days = search_days
        self.lock = threading.RLock()  # serialises check-and-book (no double bookings)

    # ------------------------------------------------------------------ utils
    def now(self) -> datetime:
        if self._clock:
            return self._clock().astimezone(self.salon.tz)
        return datetime.now(self.salon.tz)

    def _service(self, text: str | None) -> Service:
        svc = self.salon.find_service(text)
        if svc is None:
            names = ", ".join(f"{s.name} ({s.id})" for s in self.salon.services)
            raise ToolError(f"We don't offer '{text}'. Our services are: {names}.", "unknown_service")
        return svc

    def _stylist(self, text: str | None, svc: Service | None = None) -> Stylist | None:
        if not text or str(text).strip().lower() in ("any", "egal", "anyone", "no preference"):
            return None
        st = self.salon.find_stylist(text)
        if st is None:
            names = ", ".join(s.name for s in self.salon.stylists)
            raise ToolError(f"There is no stylist called '{text}'. Our team: {names}.", "unknown_stylist")
        if svc and not st.can_do(svc):
            who = ", ".join(s.name for s in self.salon.stylists_for(svc))
            raise ToolError(f"{st.name} does not do {svc.name}. It is done by: {who}.", "stylist_cannot_do_service")
        return st

    def _phone(self, raw: str | None) -> str:
        phone = normalize_phone(raw)
        if len(phone) < 8:
            raise ToolError("I need the caller's phone number to continue.", "missing_phone")
        return phone

    def _parse(self, fn, *args):
        try:
            return fn(*args)
        except InputError as exc:
            raise ToolError(str(exc), "invalid_input") from exc

    def _holds(self, stylist_id: str, exclude_offer_id: str | None) -> list[tuple[datetime, datetime]]:
        """Pending waitlist offers temporarily hold their slot."""
        now = self.now()
        out = []
        for o in self.store.pending_offers():
            if o["stylist_id"] != stylist_id or o["id"] == exclude_offer_id:
                continue
            if datetime.fromisoformat(o["expires_at"]) <= now:
                continue
            out.append((datetime.fromisoformat(o["start"]).astimezone(self.salon.tz),
                        datetime.fromisoformat(o["end"]).astimezone(self.salon.tz)))
        return out

    def _slots_for_day(self, svc: Service, day: date, stylists: list[Stylist],
                       window=TIME_OF_DAY["any"], exclude_offer_id: str | None = None) -> list[Slot]:
        tz = self.salon.tz
        start = datetime.combine(day, datetime.min.time(), tz)
        end = start + timedelta(days=1)
        busy = {}
        for st in stylists:
            if st.can_do(svc) and st.works_on(day.weekday()) and self.salon.hours_on(day.weekday()):
                busy[st.id] = self.calendar.busy(st.id, start, end) + self._holds(st.id, exclude_offer_id)
        return free_slots_for_day(self.salon, svc, day, busy, self.now(), stylists, window)

    def _search(self, svc: Service, first_day: date, stylists: list[Stylist], window, preferred,
                days: int, n: int = 3) -> list[Slot]:
        found: list[Slot] = []
        last_day = min(first_day + timedelta(days=days), self.now().date() + timedelta(days=self.salon.max_days_ahead))
        day = first_day
        while day <= last_day and len(found) < n:
            slots = self._slots_for_day(svc, day, stylists, window)
            if slots:
                found += pick_options(slots, n=n - len(found), preferred=preferred)
            day += timedelta(days=1)
        return found

    def _fmt_slot(self, s: Slot, svc: Service) -> dict:
        st = self.salon.stylist(s.stylist_id)
        return {
            "start": local_iso(s.start),
            "stylist": st.id,
            "stylist_name": st.name,
            "label": f"{speak_datetime(s.start)} with {st.name}",
            "duration_minutes": svc.duration_minutes,
        }

    def _fmt_appt(self, a: Appointment) -> dict:
        svc = self.salon.find_service(a.service_id)
        st = self.salon.find_stylist(a.stylist_id)
        return {
            "appointment_id": a.id,
            "service": svc.id if svc else a.service_id,
            "service_name": svc.name if svc else a.service_id,
            "stylist": a.stylist_id,
            "stylist_name": st.name if st else a.stylist_id,
            "start": local_iso(a.start),
            "label": f"{svc.name if svc else 'Appointment'} on {speak_datetime(a.start)}"
                     + (f" with {st.name}" if st else ""),
            "price_chf": svc.price_chf if svc else None,
        }

    def _fmt_offer(self, o: dict) -> dict:
        start = datetime.fromisoformat(o["start"]).astimezone(self.salon.tz)
        svc = self.salon.find_service(o["service_id"])
        st = self.salon.find_stylist(o["stylist_id"])
        expires = datetime.fromisoformat(o["expires_at"]).astimezone(self.salon.tz)
        return {
            "offer_id": o["id"],
            "service": o["service_id"],
            "service_name": svc.name if svc else o["service_id"],
            "stylist": o["stylist_id"],
            "stylist_name": st.name if st else o["stylist_id"],
            "start": local_iso(start),
            "label": f"{svc.name if svc else ''} on {speak_datetime(start)} with {st.name if st else ''}",
            "expires_at": local_iso(expires),
        }

    # ------------------------------------------------------------ tools
    def lookup_caller(self, phone: str | None) -> dict:
        now = self.now()
        hours = self.salon.hours_on(now.weekday())
        base = {
            "ok": True,
            "now": local_iso(now),
            "today": speak_date(now.date()),
            "open_today": ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in hours) or "closed",
            "opening_hours": self.salon.opening_hours_text(),
        }
        p = normalize_phone(phone)
        if len(p) < 8:
            return {**base, "known": False, "message": f"Today is {base['today']}. Caller number unknown."}
        cust = self.store.get_customer(p)
        appts = self.calendar.find_by_phone(p, now)
        upcoming = [self._fmt_appt(a) for a in appts]
        offer = self.store.pending_offer_for_phone(p)
        if offer and datetime.fromisoformat(offer["expires_at"]) <= now:
            offer = None
        name = cust["name"] if cust else (appts[0].customer_name if appts else None)
        msg = [f"Today is {base['today']}, {now:%H:%M}."]
        if cust:
            last_svc = self.salon.find_service(cust["last_service"] or "")
            last_st = self.salon.find_stylist(cust["last_stylist"] or "")
            msg.append(f"Returning customer: {cust['name']}"
                       + (f", last time {last_svc.name} with {last_st.name}" if last_svc and last_st else "") + ".")
        else:
            msg.append("New caller.")
        if upcoming:
            msg.append("Upcoming: " + "; ".join(u["label"] for u in upcoming) + ".")
        if offer:
            msg.append(f"PENDING WAITLIST OFFER for this caller: {self._fmt_offer(offer)['label']} — offer it first.")
        return {
            **base,
            "known": bool(cust),
            "phone": p,
            "name": name,
            "last_service": cust["last_service"] if cust else None,
            "last_stylist": cust["last_stylist"] if cust else None,
            "upcoming": upcoming,
            "pending_offer": self._fmt_offer(offer) if offer else None,
            "message": " ".join(msg),
        }

    def check_availability(self, service: str, date: str | None = None, stylist: str | None = None,
                           time_of_day: str | None = "any", preferred_time: str | None = None) -> dict:
        svc = self._service(service)
        st = self._stylist(stylist, svc)
        now = self.now()
        today = now.date()
        day = self._parse(parse_date, date, today)
        pref = self._parse(parse_time, preferred_time)
        tod = (time_of_day or "any").lower()
        if tod not in TIME_OF_DAY:
            raise ToolError("time_of_day must be one of: any, morning, afternoon, evening.", "invalid_input")
        window = TIME_OF_DAY[tod]
        stylists = [st] if st else self.salon.stylists_for(svc)
        service_info = svc.public()

        if day is not None:
            if day < today:
                raise ToolError(f"{speak_date(day)} is in the past. Today is {speak_date(today)}.", "date_in_past")
            if day > today + timedelta(days=self.salon.max_days_ahead):
                raise ToolError(f"We only take bookings up to {self.salon.max_days_ahead} days ahead.", "too_far_ahead")
            slots = self._slots_for_day(svc, day, stylists, window)
            options = pick_options(slots, preferred=pref)
            if options:
                exact = pref is not None and options[0].start.time() == pref
                lead = (f"Yes, {pref:%H:%M} is free. " if exact
                        else (f"{pref:%H:%M} is not free. " if pref else ""))
                return {
                    "ok": True, "service": service_info, "date": day.isoformat(),
                    "available_on_requested_date": True, "requested_time_available": exact if pref else None,
                    "options": [self._fmt_slot(s, svc) for s in options],
                    "message": lead + f"Options for {svc.name} on {speak_date(day)}: "
                               + "; ".join(f"{s.start:%H:%M} with {self.salon.stylist(s.stylist_id).name}" for s in options) + ".",
                }
            if not self.salon.hours_on(day.weekday()):
                reason = f"The salon is closed on {day:%A}s."
            elif st and not st.works_on(day.weekday()):
                reason = f"{st.name} does not work on {day:%A}s."
            else:
                reason = f"{speak_date(day)} is fully booked for {svc.name}" + (f" with {st.name}" if st else "") + "."
            alternatives = self._search(svc, day + timedelta(days=1), stylists, window, pref, self.search_days)
            return {
                "ok": True, "service": service_info, "date": day.isoformat(),
                "available_on_requested_date": False, "reason": reason,
                "options": [self._fmt_slot(s, svc) for s in alternatives],
                "waitlist_possible": True,
                "message": reason + (" Next free: " + "; ".join(self._fmt_slot(s, svc)["label"] for s in alternatives) + "."
                                     if alternatives else " Nothing free in the next two weeks.")
                           + " If none fits, offer the waitlist for the preferred date.",
            }

        options = self._search(svc, today, stylists, window, pref, self.search_days)
        return {
            "ok": True, "service": service_info, "date": None,
            "options": [self._fmt_slot(s, svc) for s in options],
            "waitlist_possible": True,
            "message": (f"Next free for {svc.name}: " + "; ".join(self._fmt_slot(s, svc)["label"] for s in options) + "."
                        if options else f"No free slot for {svc.name} in the next two weeks. Offer the waitlist."),
        }

    def book_appointment(self, service: str, start: str, customer_name: str, phone: str,
                         stylist: str | None = None, notes: str = "", _offer_id: str | None = None,
                         _log_event: bool = True) -> dict:
        svc = self._service(service)
        st = self._stylist(stylist, svc)
        phone = self._phone(phone)
        name = (customer_name or "").strip()
        if not name:
            raise ToolError("I need the customer's name for the booking.", "missing_name")
        start_dt = self._parse(parse_local_datetime, start, self.salon.tz)
        end_dt = start_dt + timedelta(minutes=svc.duration_minutes)
        candidates = [st] if st else self.salon.stylists_for(svc)

        with self.lock:
            for a in self.calendar.find_by_phone(phone, start_dt - timedelta(minutes=1)):
                if a.start == start_dt and a.service_id == svc.id:  # agent retried the same booking
                    return {"ok": True, "already_booked": True, "appointment": self._fmt_appt(a),
                            "message": f"This booking already exists: {self._fmt_appt(a)['label']}."}
            chosen = None
            for c in candidates:
                free = self._slots_for_day(svc, start_dt.date(), [c], exclude_offer_id=_offer_id)
                if any(s.start == start_dt for s in free):
                    chosen = c
                    break
            if chosen is None:
                alts = pick_options(self._slots_for_day(svc, start_dt.date(), candidates), preferred=start_dt.time())
                raise ToolError(
                    f"Sorry, {speak_datetime(start_dt)} is not available for {svc.name}."
                    + (" Alternatives: " + "; ".join(self._fmt_slot(s, svc)["label"] for s in alts) + "." if alts else ""),
                    "slot_unavailable", options=[self._fmt_slot(s, svc) for s in alts],
                )
            appt = self.calendar.create(NewAppointment(
                stylist_id=chosen.id, service_id=svc.id, start=start_dt, end=end_dt,
                customer_name=name, phone=phone, notes=notes or "",
            ))
        self.store.upsert_customer(phone, name, svc.id, chosen.id, booked=True)
        info = self._fmt_appt(appt)
        if _log_event:
            self.store.add_event("booked", phone, svc.price_chf, {"appointment": info, "via_offer": bool(_offer_id)})
        self.notifier.send("booking.confirmed", {"phone": phone, "name": name, **info})
        return {
            "ok": True, "appointment": info,
            "message": f"Booked {info['label']} for {name}. Price CHF {svc.price_chf}. "
                       f"Please arrive 5 minutes early.",
        }

    def find_appointments(self, phone: str) -> dict:
        phone = self._phone(phone)
        appts = [self._fmt_appt(a) for a in self.calendar.find_by_phone(phone, self.now())]
        return {
            "ok": True, "appointments": appts,
            "message": ("Upcoming appointments: " + "; ".join(a["label"] for a in appts) + ".")
                       if appts else "No upcoming appointments found for this phone number.",
        }

    def _owned_appointment(self, phone: str, appointment_id: str) -> Appointment:
        appt = self.calendar.get(appointment_id or "")
        if appt is None or normalize_phone(appt.phone) != phone:
            raise ToolError("I couldn't find that appointment for this phone number. Use find_appointments first.",
                            "appointment_not_found")
        if appt.start <= self.now():
            raise ToolError("That appointment is already in the past.", "appointment_in_past")
        return appt

    def cancel_appointment(self, phone: str, appointment_id: str) -> dict:
        phone = self._phone(phone)
        with self.lock:
            appt = self._owned_appointment(phone, appointment_id)
            late = appt.start - self.now() < timedelta(hours=self.salon.late_cancellation_hours)
            self.calendar.delete(appt.id)
        svc_c = self.salon.find_service(appt.service_id)
        self.store.add_event("cancelled", phone, svc_c.price_chf if svc_c else None,
                             {"appointment": self._fmt_appt(appt), "late": late})
        offer = self.on_slot_freed(appt.stylist_id, appt.start, appt.end)
        msg = f"Cancelled {self._fmt_appt(appt)['label']}."
        if late:
            msg += (f" Note: this is less than {self.salon.late_cancellation_hours} hours before the appointment"
                    " (see cancellation policy).")
        return {"ok": True, "cancelled": self._fmt_appt(appt), "late_cancellation": late,
                "slot_offered_to_waitlist": offer is not None, "message": msg}

    def reschedule_appointment(self, phone: str, appointment_id: str, new_start: str,
                               stylist: str | None = None) -> dict:
        phone = self._phone(phone)
        with self.lock:
            old = self._owned_appointment(phone, appointment_id)
            # Free the old slot first (so moving by 30 minutes works), restore it if the new one fails.
            self.calendar.delete(old.id)
            try:
                try:
                    result = self.book_appointment(old.service_id, new_start, old.customer_name, phone,
                                                   stylist or old.stylist_id, old.notes, _log_event=False)
                except ToolError as exc:
                    if stylist or exc.code != "slot_unavailable":
                        raise
                    result = self.book_appointment(old.service_id, new_start, old.customer_name, phone, None, old.notes,
                                                   _log_event=False)
            except ToolError:
                self.calendar.create(NewAppointment(**{k: getattr(old, k) for k in NewAppointment.__dataclass_fields__}))
                raise
        self.store.add_event("rescheduled", phone, None, {"from": self._fmt_appt(old), "to": result["appointment"]})
        new_start_dt = parse_local_datetime(result["appointment"]["start"], self.salon.tz)
        offer = None
        if new_start_dt != old.start or result["appointment"]["stylist"] != old.stylist_id:
            offer = self.on_slot_freed(old.stylist_id, old.start, old.end)
        return {**result, "previous": self._fmt_appt(old), "slot_offered_to_waitlist": offer is not None,
                "message": f"Moved from {speak_datetime(old.start)} to {result['appointment']['label']}."}

    def join_waitlist(self, phone: str, customer_name: str, service: str, date_from: str | None = None,
                      date_to: str | None = None, stylist: str | None = None, time_of_day: str | None = "any") -> dict:
        svc = self._service(service)
        st = self._stylist(stylist, svc)
        phone = self._phone(phone)
        name = (customer_name or "").strip()
        if not name:
            raise ToolError("I need the customer's name for the waitlist.", "missing_name")
        today = self.now().date()
        d_from = self._parse(parse_date, date_from, today) or today
        d_to = self._parse(parse_date, date_to, today) or d_from + timedelta(days=self.search_days)
        if d_to < d_from or d_to < today:
            raise ToolError("The waitlist date range is invalid.", "invalid_input")
        tod = (time_of_day or "any").lower()
        if tod not in TIME_OF_DAY:
            tod = "any"
        entry = self.store.add_waitlist(phone, name, svc.id, st.id if st else None, max(d_from, today), d_to, tod)
        self.store.upsert_customer(phone, name)
        pos = self.store.waitlist_position(entry["id"])
        self.store.add_event("waitlist_joined", phone, None, {"service": svc.id, "waitlist_id": entry["id"]})
        return {
            "ok": True, "waitlist_id": entry["id"], "position": pos,
            "message": f"{name} is on the waitlist (position {pos}) for {svc.name}"
                       + (f" with {st.name}" if st else "")
                       + (f" on {speak_date(d_from)}" if d_from == d_to
                          else f" between {speak_date(d_from)} and {speak_date(d_to)}")
                       + ("" if tod == "any" else f", {tod}s")
                       + ". We call them as soon as a matching slot frees up.",
        }

    def accept_offer(self, phone: str, offer_id: str) -> dict:
        phone = self._phone(phone)
        with self.lock:
            offer = self.store.get_offer(offer_id or "")
            if not offer or offer["phone"] != phone or offer["status"] != "pending":
                raise ToolError("I can't find an open offer for this phone number.", "offer_not_found")
            if datetime.fromisoformat(offer["expires_at"]) <= self.now():
                self._close_offer(offer, "expired")
                raise ToolError("Sorry, that offer has just expired. You are still on the waitlist.", "offer_expired")
            entry = self.store.get_waitlist(offer["waitlist_id"])
            start = datetime.fromisoformat(offer["start"]).astimezone(self.salon.tz)
            try:
                result = self.book_appointment(offer["service_id"], local_iso(start), entry["name"], phone,
                                               offer["stylist_id"], "booked via waitlist", _offer_id=offer["id"])
            except ToolError:
                self.store.set_offer_status(offer["id"], "failed")
                self.store.set_waitlist_status(entry["id"], "waiting")
                raise ToolError("Sorry, that slot is no longer available. You stay on the waitlist.", "offer_failed")
            self.store.set_offer_status(offer["id"], "accepted")
            self.store.set_waitlist_status(entry["id"], "booked")
            svc_a = self.salon.find_service(offer["service_id"])
            self.store.add_event("offer_accepted", phone, svc_a.price_chf if svc_a else None, {"offer_id": offer["id"]})
        return {**result, "message": "Offer accepted. " + result["message"]}

    def decline_offer(self, phone: str, offer_id: str) -> dict:
        phone = self._phone(phone)
        offer = self.store.get_offer(offer_id or "")
        if not offer or offer["phone"] != phone or offer["status"] != "pending":
            raise ToolError("I can't find an open offer for this phone number.", "offer_not_found")
        self._close_offer(offer, "declined")
        return {"ok": True, "message": "No problem — the offer is declined and the caller stays on the waitlist."}

    # ------------------------------------------------------------ owner tools
    def list_appointments(self, date_from: str | None = None, date_to: str | None = None,
                          stylist: str | None = None) -> dict:
        today = self.now().date()
        d_from = self._parse(parse_date, date_from, today) or today
        d_to = self._parse(parse_date, date_to, today) or d_from + timedelta(days=7)
        st = self._stylist(stylist)
        tz = self.salon.tz
        appts = self.calendar.list_range(datetime.combine(d_from, datetime.min.time(), tz),
                                         datetime.combine(d_to + timedelta(days=1), datetime.min.time(), tz))
        rows = [{**self._fmt_appt(a), "customer_name": a.customer_name, "phone": a.phone, "source": a.source}
                for a in appts if not st or a.stylist_id == st.id]
        return {"ok": True, "appointments": rows,
                "message": f"{len(rows)} appointments between {speak_date(d_from)} and {speak_date(d_to)}."}

    def list_waitlist(self) -> dict:
        waiting = [w for w in self.store.all_waitlist() if w["status"] in ("waiting", "offered")]
        offers = [self._fmt_offer(o) | {"phone": o["phone"]} for o in self.store.pending_offers()]
        return {"ok": True, "waitlist": waiting, "open_offers": offers,
                "message": f"{len(waiting)} people waiting, {len(offers)} open offers."}

    def salon_stats(self, days: int | None = 30) -> dict:
        days = max(1, min(int(days or 30), 365))
        events = self.store.events_since(self.now() - timedelta(days=days))
        count = lambda k: sum(1 for e in events if e["kind"] == k)  # noqa: E731
        recovered = [e for e in events if e["kind"] == "offer_accepted"]
        stats = {
            "days": days,
            "bookings": count("booked"),
            "cancellations": count("cancelled"),
            "reschedules": count("rescheduled"),
            "waitlist_joined": count("waitlist_joined"),
            "offers_sent": count("offer_sent"),
            "slots_recovered": len(recovered),
            "recovered_chf": round(sum(e["amount_chf"] or 0 for e in recovered), 2),
        }
        return {"ok": True, **stats,
                "message": f"Last {days} days: {stats['bookings']} bookings, {stats['cancellations']} cancellations, "
                           f"{stats['slots_recovered']} slots recovered (CHF {stats['recovered_chf']:.0f})."}

    # ------------------------------------------------- Empty-Chair Recovery
    def on_slot_freed(self, stylist_id: str, start: datetime, end: datetime) -> dict | None:
        """Offer freed time to the first matching waitlist entry. Returns the offer or None."""
        st = self.salon.find_stylist(stylist_id)
        if st is None:
            return None
        day = start.date()
        with self.lock:
            for entry in self.store.waiting_entries():
                if entry["stylist_id"] and entry["stylist_id"] != stylist_id:
                    continue
                svc = self.salon.find_service(entry["service_id"])
                if svc is None or not st.can_do(svc):
                    continue
                if not (date.fromisoformat(entry["date_from"]) <= day <= date.fromisoformat(entry["date_to"])):
                    continue
                slots = self._slots_for_day(svc, day, [st], TIME_OF_DAY.get(entry["time_of_day"], TIME_OF_DAY["any"]))
                fitting = [s for s in slots if overlaps(s.start, s.end, [(start, end)], timedelta(0))]
                previously = {o["waitlist_id"] for s in fitting for o in self.store.offers_for_slot(stylist_id, s.start)}
                fitting = [s for s in fitting if entry["id"] not in previously]
                if not fitting:
                    continue
                slot = min(fitting, key=lambda s: (s.start != start, s.start))
                expires = self.now() + self.offer_ttl
                offer = self.store.add_offer(entry["id"], entry["phone"], svc.id, st.id, slot.start, slot.end, expires)
                self.store.set_waitlist_status(entry["id"], "offered")
                payload = {"phone": entry["phone"], "name": entry["name"], **self._fmt_offer(offer)}
                self.store.add_event("offer_sent", entry["phone"], svc.price_chf, {"offer_id": offer["id"]})
                self.notifier.send("waitlist.offer", payload)
                log.info("waitlist offer %s -> %s", offer["id"], entry["phone"])
                return offer
        return None

    def _close_offer(self, offer: dict, status: str) -> None:
        self.store.set_offer_status(offer["id"], status)
        self.store.add_event(f"offer_{status}", offer["phone"], None, {"offer_id": offer["id"]})
        self.store.set_waitlist_status(offer["waitlist_id"], "waiting")
        start = datetime.fromisoformat(offer["start"]).astimezone(self.salon.tz)
        end = datetime.fromisoformat(offer["end"]).astimezone(self.salon.tz)
        self.on_slot_freed(offer["stylist_id"], start, end)  # cascade to the next person

    def sweep(self) -> dict:
        """Expire timed-out offers (cascading them) and stale waitlist entries."""
        now = self.now()
        expired = 0
        for o in self.store.pending_offers():
            if datetime.fromisoformat(o["expires_at"]) <= now:
                self._close_offer(o, "expired")
                expired += 1
        stale = self.store.expire_old_waitlist(now.date())
        return {"expired_offers": expired, "expired_waitlist": stale}
