"""Pure availability logic (no I/O) — the part that must never be wrong.

A slot is bookable for a stylist when:
  * the salon is open for the full duration of the service,
  * the stylist works that day and offers that service,
  * it does not overlap any busy block (+ cleanup buffer on both sides),
  * it starts at least ``min_lead_minutes`` from now.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .salon import Salon, Service, Stylist

Interval = tuple[datetime, datetime]


@dataclass(frozen=True)
class Slot:
    stylist_id: str
    start: datetime
    end: datetime


def overlaps(start: datetime, end: datetime, busy: list[Interval], buffer: timedelta) -> bool:
    return any(start < b_end + buffer and b_start - buffer < end for b_start, b_end in busy)


def free_slots_for_day(
    salon: Salon,
    service: Service,
    day: date,
    busy_by_stylist: dict[str, list[Interval]],
    now: datetime,
    stylists: list[Stylist] | None = None,
    window: tuple[time, time] = (time(0, 0), time(23, 59)),
) -> list[Slot]:
    tz = salon.tz
    weekday = day.weekday()
    duration = timedelta(minutes=service.duration_minutes)
    buffer = timedelta(minutes=salon.buffer_minutes)
    step = timedelta(minutes=salon.slot_step_minutes)
    earliest = now + timedelta(minutes=salon.min_lead_minutes)
    candidates = stylists if stylists is not None else salon.stylists
    order = {s.id: i for i, s in enumerate(salon.stylists)}

    out: list[Slot] = []
    for stylist in candidates:
        if not stylist.can_do(service) or not stylist.works_on(weekday):
            continue
        busy = busy_by_stylist.get(stylist.id, [])
        for open_t, close_t in salon.hours_on(weekday):
            cursor = datetime.combine(day, open_t, tz)
            close = datetime.combine(day, close_t, tz)
            while cursor + duration <= close:
                end = cursor + duration
                if (
                    cursor >= earliest
                    and window[0] <= cursor.time() <= window[1]
                    and not overlaps(cursor, end, busy, buffer)
                ):
                    out.append(Slot(stylist.id, cursor, end))
                cursor += step
    out.sort(key=lambda s: (s.start, order.get(s.stylist_id, 99)))
    return out


def pick_options(
    slots: list[Slot],
    n: int = 3,
    min_gap: timedelta = timedelta(minutes=60),
    preferred: time | None = None,
) -> list[Slot]:
    """Choose a few well-spread options — a voice agent must not read out 30 slots.

    With a preferred time, options closest to it come first (an exact match
    is always option one).
    """
    unique: dict[datetime, Slot] = {}
    for s in slots:
        unique.setdefault(s.start, s)
    ordered = list(unique.values())
    if preferred is not None:
        def distance(s: Slot) -> float:
            minutes = s.start.hour * 60 + s.start.minute
            return abs(minutes - (preferred.hour * 60 + preferred.minute))
        ordered.sort(key=lambda s: (s.start.date(), distance(s)))
    picked: list[Slot] = []
    for s in ordered:
        if all(abs(s.start - p.start) >= min_gap for p in picked):
            picked.append(s)
        if len(picked) == n:
            break
    if preferred is None:
        picked.sort(key=lambda s: s.start)
    return picked
