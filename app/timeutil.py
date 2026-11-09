"""Parsing and formatting helpers tuned for voice agents.

LLM voice agents send dates and times in slightly different shapes; these
helpers accept the common variants and produce labels that sound natural
when read aloud ("Thursday 13 November at 14:00").
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TIME_OF_DAY = {
    "any": (time(0, 0), time(23, 59)),
    "morning": (time(0, 0), time(11, 59)),
    "afternoon": (time(12, 0), time(16, 59)),
    "evening": (time(17, 0), time(23, 59)),
}


class InputError(ValueError):
    """Raised for caller/agent input we cannot interpret. Message is speakable."""


def parse_date(value: str | None, today: date) -> date | None:
    if value is None or not str(value).strip():
        return None
    v = str(value).strip().lower()
    if v in ("today", "heute", "hüt", "aujourd'hui", "oggi"):
        return today
    if v in ("tomorrow", "morgen", "morn", "demain", "domani"):
        return today + timedelta(days=1)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", v)  # Swiss format 13.11.2026
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    raise InputError(f"I could not understand the date '{value}'. Please use YYYY-MM-DD.")


def parse_time(value: str | None) -> time | None:
    if value is None or not str(value).strip():
        return None
    m = re.match(r"^(\d{1,2})[:.h](\d{2})", str(value).strip())
    if not m:
        m2 = re.match(r"^(\d{1,2})$", str(value).strip())
        if m2:
            return time(int(m2[1]), 0)
        raise InputError(f"I could not understand the time '{value}'. Please use HH:MM.")
    return time(int(m[1]), int(m[2]))


def parse_local_datetime(value: str, tz: ZoneInfo) -> datetime:
    """Accept '2026-11-13T14:00', '2026-11-13 14:00', with or without offset."""
    v = str(value).strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError(
            f"I could not understand the start time '{value}'. Use YYYY-MM-DDTHH:MM."
        ) from exc
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def speak_datetime(dt: datetime) -> str:
    return f"{dt:%A} {dt.day} {dt:%B} at {dt:%H:%M}"


def speak_date(d: date) -> str:
    return f"{d:%A} {d.day} {d:%B}"


def local_iso(dt: datetime) -> str:
    """ISO string without seconds/offset — what we ask the agent to send back."""
    return dt.strftime("%Y-%m-%dT%H:%M")


def normalize_phone(raw: str | None) -> str:
    """Normalise Swiss and international numbers to E.164 (+41791234567)."""
    if not raw:
        return ""
    digits = re.sub(r"[^\d+]", "", str(raw))
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    elif digits.startswith("0") and len(digits) == 10:
        digits = "+41" + digits[1:]
    elif not digits.startswith("+") and digits.startswith("41") and len(digits) == 11:
        digits = "+" + digits
    return digits
