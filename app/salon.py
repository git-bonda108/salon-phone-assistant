"""Business configuration of the salon: services, stylists, opening hours.

Everything the phone assistant needs to know about *this* business lives in
``data/salon.json`` so that a new customer (another salon, a barber, a nail
studio) is onboarded by editing one file, not code.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _norm(text: str) -> str:
    """Lower-case, strip accents and punctuation noise for fuzzy matching."""
    text = unicodedata.normalize("NFKD", text.strip().lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.replace("-", " ").replace("_", " ").replace("'", "").split())


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    name_de: str
    duration_minutes: int
    price_chf: int
    aliases: tuple[str, ...] = ()

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "name_de": self.name_de,
            "duration_minutes": self.duration_minutes,
            "price_chf": self.price_chf,
        }


@dataclass(frozen=True)
class Stylist:
    id: str
    name: str
    role: str
    services: frozenset[str]
    working_days: frozenset[int]  # 0 = Monday
    google_calendar_id: str = ""

    def can_do(self, service: Service) -> bool:
        return service.id in self.services

    def works_on(self, weekday: int) -> bool:
        return weekday in self.working_days


@dataclass
class Salon:
    name: str
    address: str
    phone: str
    timezone: str
    opening_hours: dict[int, list[tuple[time, time]]]
    services: list[Service]
    stylists: list[Stylist]
    slot_step_minutes: int = 15
    buffer_minutes: int = 10
    min_lead_minutes: int = 60
    max_days_ahead: int = 60
    late_cancellation_hours: int = 24
    _service_index: dict[str, Service] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for s in self.services:
            for key in (s.id, s.name, s.name_de, *s.aliases):
                self._service_index.setdefault(_norm(key), s)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    # ---- lookups -------------------------------------------------------
    def find_service(self, text: str | None) -> Service | None:
        if not text:
            return None
        key = _norm(text)
        if key in self._service_index:
            return self._service_index[key]
        # Loose match: the caller's words contain an alias or vice versa.
        for alias, service in self._service_index.items():
            if len(alias) >= 4 and (alias in key or key in alias):
                return service
        return None

    def service(self, service_id: str) -> Service:
        found = self.find_service(service_id)
        if found is None:
            raise KeyError(service_id)
        return found

    def find_stylist(self, text: str | None) -> Stylist | None:
        if not text:
            return None
        key = _norm(text)
        for s in self.stylists:
            if key in (_norm(s.id), _norm(s.name)):
                return s
        return None

    def stylist(self, stylist_id: str) -> Stylist:
        found = self.find_stylist(stylist_id)
        if found is None:
            raise KeyError(stylist_id)
        return found

    def stylists_for(self, service: Service) -> list[Stylist]:
        return [s for s in self.stylists if s.can_do(service)]

    def hours_on(self, weekday: int) -> list[tuple[time, time]]:
        return self.opening_hours.get(weekday, [])

    def opening_hours_text(self) -> str:
        parts = []
        for i, key in enumerate(DAY_KEYS):
            spans = self.hours_on(i)
            label = key.capitalize()
            if spans:
                parts.append(f"{label} " + ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in spans))
            else:
                parts.append(f"{label} closed")
        return "; ".join(parts)


def _t(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def load_salon(path: str | Path) -> Salon:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    hours: dict[int, list[tuple[time, time]]] = {}
    for key, spans in raw.get("opening_hours", {}).items():
        hours[DAY_KEYS.index(key)] = [(_t(a), _t(b)) for a, b in spans]
    services = [
        Service(
            id=s["id"],
            name=s["name"],
            name_de=s.get("name_de", s["name"]),
            duration_minutes=int(s["duration_minutes"]),
            price_chf=int(s["price_chf"]),
            aliases=tuple(s.get("aliases", [])),
        )
        for s in raw["services"]
    ]
    service_ids = {s.id for s in services}
    stylists = []
    for s in raw["stylists"]:
        unknown = set(s["services"]) - service_ids
        if unknown:
            raise ValueError(f"stylist {s['id']} has unknown services {unknown}")
        stylists.append(
            Stylist(
                id=s["id"],
                name=s["name"],
                role=s.get("role", ""),
                services=frozenset(s["services"]),
                working_days=frozenset(DAY_KEYS.index(d) for d in s["working_days"]),
                google_calendar_id=s.get("google_calendar_id", ""),
            )
        )
    return Salon(
        name=raw["name"],
        address=raw.get("address", ""),
        phone=raw.get("phone", ""),
        timezone=raw.get("timezone", "Europe/Zurich"),
        opening_hours=hours,
        services=services,
        stylists=stylists,
        slot_step_minutes=int(raw.get("slot_step_minutes", 15)),
        buffer_minutes=int(raw.get("buffer_minutes", 10)),
        min_lead_minutes=int(raw.get("min_lead_minutes", 60)),
        max_days_ahead=int(raw.get("max_days_ahead", 60)),
        late_cancellation_hours=int(raw.get("late_cancellation_hours", 24)),
    )
