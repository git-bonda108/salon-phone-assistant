"""The tool registry: every tool is defined exactly once, here.

From this registry we derive
  * the HTTP live tools Suisse Line calls (``POST /tools/{name}``),
  * the MCP server our agents and other MCP clients use (``/mcp``),
  * ``suisse-line/tools.json`` that is pasted into the Suisse Line portal
    (``python scripts/export_tools.py``; a test fails if it is out of date).

Schemas are emitted in the JSON-Schema subset Suisse Line accepts. Enums for
services and stylists come from ``data/salon.json``, so a new business only
changes that file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .salon import Salon

Audience = Literal["caller", "owner"]


def _f(description: str, enum: str | None = None, default=None, required: bool = False):
    extra = {"enum_source": enum} if enum else None
    if required:
        return Field(..., description=description, json_schema_extra=extra)
    return Field(default, description=description, json_schema_extra=extra)


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ------------------------------------------------------------------ caller tools
class LookupCallerIn(_In):
    phone: str | None = _f("Caller's phone number from caller ID, e.g. +41791234567. Empty if unknown.")


class CheckAvailabilityIn(_In):
    service: str = _f("Service id.", "services", required=True)
    date: str | None = _f("Requested day as YYYY-MM-DD. Leave empty for 'as soon as possible'.")
    stylist: str | None = _f("Preferred stylist.", "stylists_or_any")
    time_of_day: str | None = _f("Rough preference.", "time_of_day", default="any")
    preferred_time: str | None = _f("Specific wished time as HH:MM, e.g. 14:30. Options closest to it come first.")


class BookAppointmentIn(_In):
    service: str = _f("Service id.", "services", required=True)
    start: str = _f("Start as YYYY-MM-DDTHH:MM, from check_availability.", required=True)
    stylist: str | None = _f("Stylist id from check_availability.", "stylists")
    customer_name: str = _f("First and last name of the customer, spelled as confirmed.", required=True)
    phone: str = _f("Customer's phone number, e.g. +41791234567.", required=True)
    notes: str | None = _f("Optional short note, e.g. 'first colour, patch test needed' or 'very long hair'.", default="")


class FindAppointmentsIn(_In):
    phone: str = _f("Caller's phone number.", required=True)


class CancelAppointmentIn(_In):
    phone: str = _f("Caller's phone number (must match the booking).", required=True)
    appointment_id: str = _f("appointment_id from find_appointments or lookup_caller.", required=True)


class RescheduleAppointmentIn(_In):
    phone: str = _f("Caller's phone number (must match the booking).", required=True)
    appointment_id: str = _f("appointment_id of the appointment to move.", required=True)
    new_start: str = _f("New start as YYYY-MM-DDTHH:MM, from check_availability.", required=True)
    stylist: str | None = _f("Stylist for the new slot; empty keeps the same stylist if possible.", "stylists")


class JoinWaitlistIn(_In):
    phone: str = _f("Caller's phone number.", required=True)
    customer_name: str = _f("First and last name.", required=True)
    service: str = _f("Service id.", "services", required=True)
    date_from: str | None = _f("Earliest acceptable day, YYYY-MM-DD.")
    date_to: str | None = _f("Latest acceptable day, YYYY-MM-DD.")
    stylist: str | None = _f("Only this stylist, or any.", "stylists_or_any")
    time_of_day: str | None = _f("Time preference.", "time_of_day", default="any")


class OfferIn(_In):
    phone: str = _f("Caller's phone number.", required=True)
    offer_id: str = _f("offer_id of the pending offer.", required=True)


# ------------------------------------------------------------------- owner tools
class ListAppointmentsIn(_In):
    date_from: str | None = _f("First day, YYYY-MM-DD. Default: today.")
    date_to: str | None = _f("Last day, YYYY-MM-DD. Default: 7 days after date_from.")
    stylist: str | None = _f("Only this stylist.", "stylists_or_any")


class EmptyIn(_In):
    pass


class StatsIn(_In):
    days: int | None = Field(30, description="Look-back window in days.", ge=1, le=365)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    model: type[BaseModel]
    method: str
    audience: Audience
    read_only: bool
    agent_required: tuple[str, ...] = ()  # required for the voice agent, optional in the backend

    @property
    def path(self) -> str:
        return f"/tools/{self.name}"


REGISTRY: tuple[ToolSpec, ...] = (
    ToolSpec("lookup_caller",
             "Call this FIRST in every call. Returns today's date and time (needed to resolve 'tomorrow', "
             "'next Thursday'), today's opening hours, whether the caller is a returning customer (name, last "
             "service, last stylist), their upcoming appointments and any pending waitlist offer.",
             LookupCallerIn, "lookup_caller", "caller", True),
    ToolSpec("check_availability",
             "Find free appointment times. Returns at most 3 options (start time + stylist). If the requested day "
             "is closed or full it returns the next free options and says whether the waitlist is possible. Never "
             "invent times — only offer what this tool returns.",
             CheckAvailabilityIn, "check_availability", "caller", True),
    ToolSpec("book_appointment",
             "Book an appointment. Only call after the caller explicitly confirmed service, day, time, stylist, "
             "name and phone number. Use start and stylist exactly as returned by check_availability. If the slot "
             "was just taken, returns ok=false with new options.",
             BookAppointmentIn, "book_appointment", "caller", False, agent_required=("stylist",)),
    ToolSpec("find_appointments",
             "List the upcoming appointments booked under a phone number. Use before cancelling or moving an "
             "appointment.",
             FindAppointmentsIn, "find_appointments", "caller", True),
    ToolSpec("cancel_appointment",
             "Cancel one appointment of the caller after they confirmed which one. Returns late_cancellation=true "
             "if less than 24 hours before (mention the cancellation policy politely). The freed slot is "
             "automatically offered to the waitlist.",
             CancelAppointmentIn, "cancel_appointment", "caller", False),
    ToolSpec("reschedule_appointment",
             "Move an existing appointment to a new time the caller chose from check_availability. The old "
             "appointment is only released if the new one could be booked.",
             RescheduleAppointmentIn, "reschedule_appointment", "caller", False),
    ToolSpec("join_waitlist",
             "Put the caller on the waitlist when no offered time fits. As soon as a matching slot frees up "
             "(cancellation), the salon calls them back with an offer.",
             JoinWaitlistIn, "join_waitlist", "caller", False),
    ToolSpec("accept_offer",
             "The caller accepts a pending waitlist offer (from lookup_caller.pending_offer). Books the held slot.",
             OfferIn, "accept_offer", "caller", False),
    ToolSpec("decline_offer",
             "The caller declines a pending waitlist offer. They stay on the waitlist; the slot goes to the next "
             "person.",
             OfferIn, "decline_offer", "caller", False),
    # Owner tools: read-only, for the owner copilot and MCP clients. Never exposed to callers.
    ToolSpec("list_appointments",
             "Owner view: all appointments in a date range (default: the next 7 days), optionally for one stylist.",
             ListAppointmentsIn, "list_appointments", "owner", True),
    ToolSpec("list_waitlist",
             "Owner view: current waitlist entries and open slot offers.",
             EmptyIn, "list_waitlist", "owner", True),
    ToolSpec("salon_stats",
             "Owner view: bookings, cancellations, waitlist offers, recovered slots and recovered revenue in CHF "
             "over the last N days.",
             StatsIn, "salon_stats", "owner", True),
)

BY_NAME: dict[str, ToolSpec] = {t.name: t for t in REGISTRY}
CALLER_TOOLS = tuple(t for t in REGISTRY if t.audience == "caller")
OWNER_TOOLS = tuple(t for t in REGISTRY if t.audience == "owner")


def _enum(source: str, salon: Salon) -> list[str]:
    services = [s.id for s in salon.services]
    stylists = [s.id for s in salon.stylists]
    return {
        "services": services,
        "stylists": stylists,
        "stylists_or_any": ["any", *stylists],
        "time_of_day": ["any", "morning", "afternoon", "evening"],
    }[source]


def parameters_schema(model: type[BaseModel], salon: Salon, also_required: tuple[str, ...] = ()) -> dict:
    """JSON schema in the subset Suisse Line accepts (type/properties/required/enum/description/min/max)."""
    props: dict[str, dict] = {}
    required: list[str] = []
    for name, field in model.model_fields.items():
        ann = field.annotation
        is_int = ann in (int, int | None)
        prop: dict = {"type": "integer" if is_int else "string", "description": field.description or ""}
        extra = field.json_schema_extra or {}
        if isinstance(extra, dict) and extra.get("enum_source"):
            prop["enum"] = _enum(extra["enum_source"], salon)
        for meta in field.metadata:
            if hasattr(meta, "ge"):
                prop["minimum"] = meta.ge
            if hasattr(meta, "le"):
                prop["maximum"] = meta.le
        props[name] = prop
        if field.is_required() or name in also_required:
            required.append(name)
    schema: dict = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def suisse_line_tools(salon: Salon) -> dict:
    """The content of suisse-line/tools.json (caller tools only)."""
    return {
        "_comment": "GENERATED by scripts/export_tools.py from app/tools.py — do not edit by hand. "
                    "Live tools for the Suisse Line agent. Each tool = POST {BASE_URL}{path} with header "
                    "X-Api-Key: {TOOL_API_KEY}. Parameter schemas use only the JSON-Schema subset Suisse Line "
                    "accepts (no format/pattern/oneOf). tests/test_suisse_line_config.py enforces this.",
        "tools": [
            {"name": t.name, "path": t.path, "description": t.description,
             "parameters": parameters_schema(t.model, salon, t.agent_required)}
            for t in CALLER_TOOLS
        ],
    }
