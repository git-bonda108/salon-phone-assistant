"""Local calendar backend (SQLite). Used for tests, demos and as a fallback
when no Google credentials are configured."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from .base import Appointment, NewAppointment

SCHEMA = """
CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    stylist_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_appt_stylist ON appointments(stylist_id, start);
CREATE INDEX IF NOT EXISTS idx_appt_phone ON appointments(phone);
"""


def _u(dt: datetime) -> str:
    """Store UTC so string comparison in SQL is correct across DST changes."""
    return dt.astimezone(timezone.utc).isoformat()


class SqliteCalendar:
    def __init__(self, conn: sqlite3.Connection, tz):
        self.conn = conn
        self.tz = tz
        self._lock = threading.Lock()
        with self._lock:
            self.conn.executescript(SCHEMA)

    def _row(self, r) -> Appointment:
        return Appointment(
            id=r["id"],
            stylist_id=r["stylist_id"],
            service_id=r["service_id"],
            start=datetime.fromisoformat(r["start"]).astimezone(self.tz),
            end=datetime.fromisoformat(r["end"]).astimezone(self.tz),
            customer_name=r["customer_name"],
            phone=r["phone"],
            notes=r["notes"],
            source=r["source"],
        )

    def busy(self, stylist_id, start, end):
        with self._lock:
            rows = self.conn.execute(
                "SELECT start, end FROM appointments WHERE stylist_id=? AND start < ? AND end > ?",
                (stylist_id, _u(end), _u(start)),
            ).fetchall()
        return [(datetime.fromisoformat(a).astimezone(self.tz), datetime.fromisoformat(b).astimezone(self.tz)) for a, b in rows]

    def create(self, appt: NewAppointment) -> Appointment:
        new_id = uuid.uuid4().hex[:10]
        with self._lock:
            self.conn.execute(
                "INSERT INTO appointments VALUES (?,?,?,?,?,?,?,?,?)",
                (new_id, appt.stylist_id, appt.service_id, _u(appt.start), _u(appt.end),
                 appt.customer_name, appt.phone, appt.notes, appt.source),
            )
            self.conn.commit()
        return Appointment(id=new_id, **appt.__dict__)

    def get(self, appointment_id):
        with self._lock:
            r = self.conn.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        return self._row(r) if r else None

    def delete(self, appointment_id):
        with self._lock:
            self.conn.execute("DELETE FROM appointments WHERE id=?", (appointment_id,))
            self.conn.commit()

    def find_by_phone(self, phone, after):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM appointments WHERE phone=? AND end > ? ORDER BY start",
                (phone, _u(after)),
            ).fetchall()
        return [self._row(r) for r in rows]

    def list_range(self, start, end):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM appointments WHERE start < ? AND end > ? ORDER BY start",
                (_u(end), _u(start)),
            ).fetchall()
        return [self._row(r) for r in rows]
