"""Calendar abstraction. The salon's calendar is the single source of truth
for bookings: staff keep using Google Calendar as usual (lunch breaks,
holidays, walk-ins) and the assistant simply respects whatever is in it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class NewAppointment:
    stylist_id: str
    service_id: str
    start: datetime
    end: datetime
    customer_name: str
    phone: str
    notes: str = ""
    source: str = "phone-assistant"


@dataclass(frozen=True)
class Appointment(NewAppointment):
    id: str = ""


class CalendarBackend(Protocol):
    def busy(self, stylist_id: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]: ...

    def create(self, appt: NewAppointment) -> Appointment: ...

    def get(self, appointment_id: str) -> Appointment | None: ...

    def delete(self, appointment_id: str) -> None: ...

    def find_by_phone(self, phone: str, after: datetime) -> list[Appointment]: ...

    def list_range(self, start: datetime, end: datetime) -> list[Appointment]: ...
