"""Small SQLite store for what the calendar does not hold: customers,
waitlist entries, slot offers, call logs and an outbox of notifications."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import date, datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    phone TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    last_service TEXT,
    last_stylist TEXT,
    bookings INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS waitlist (
    id TEXT PRIMARY KEY,
    phone TEXT NOT NULL,
    name TEXT NOT NULL,
    service_id TEXT NOT NULL,
    stylist_id TEXT,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    time_of_day TEXT NOT NULL DEFAULT 'any',
    status TEXT NOT NULL DEFAULT 'waiting',   -- waiting | offered | booked | cancelled | expired
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS offers (
    id TEXT PRIMARY KEY,
    waitlist_id TEXT NOT NULL,
    phone TEXT NOT NULL,
    service_id TEXT NOT NULL,
    stylist_id TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | accepted | declined | expired | failed
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    phone TEXT,
    event TEXT,
    summary TEXT,
    duration INTEGER,
    received_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL
);
"""


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.lock = threading.RLock()
        with self.lock:
            conn.executescript(SCHEMA)

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> None:
        with self.lock:
            self.conn.execute(sql, args)
            self.conn.commit()

    # ---- customers ----------------------------------------------------
    def get_customer(self, phone: str) -> dict | None:
        rows = self._q("SELECT * FROM customers WHERE phone=?", (phone,))
        return dict(rows[0]) if rows else None

    def upsert_customer(self, phone: str, name: str, service_id: str | None = None,
                        stylist_id: str | None = None, booked: bool = False) -> None:
        self._x(
            """INSERT INTO customers(phone, name, last_service, last_stylist, bookings, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(phone) DO UPDATE SET
                 name=excluded.name,
                 last_service=COALESCE(excluded.last_service, customers.last_service),
                 last_stylist=COALESCE(excluded.last_stylist, customers.last_stylist),
                 bookings=customers.bookings + excluded.bookings,
                 updated_at=excluded.updated_at""",
            (phone, name, service_id, stylist_id, 1 if booked else 0, _now()),
        )

    # ---- waitlist -----------------------------------------------------
    def add_waitlist(self, phone: str, name: str, service_id: str, stylist_id: str | None,
                     date_from: date, date_to: date, time_of_day: str) -> dict:
        existing = self._q(
            "SELECT * FROM waitlist WHERE phone=? AND service_id=? AND status IN ('waiting','offered')",
            (phone, service_id),
        )
        if existing:  # one active entry per caller+service: update it instead of duplicating
            wid = existing[0]["id"]
            self._x(
                "UPDATE waitlist SET name=?, stylist_id=?, date_from=?, date_to=?, time_of_day=? WHERE id=?",
                (name, stylist_id, date_from.isoformat(), date_to.isoformat(), time_of_day, wid),
            )
        else:
            wid = uuid.uuid4().hex[:8]
            self._x(
                "INSERT INTO waitlist VALUES (?,?,?,?,?,?,?,?, 'waiting', ?)",
                (wid, phone, name, service_id, stylist_id, date_from.isoformat(), date_to.isoformat(),
                 time_of_day, _now()),
            )
        return self.get_waitlist(wid)

    def get_waitlist(self, wid: str) -> dict | None:
        rows = self._q("SELECT * FROM waitlist WHERE id=?", (wid,))
        return dict(rows[0]) if rows else None

    def waitlist_position(self, wid: str) -> int:
        entry = self.get_waitlist(wid)
        rows = self._q(
            "SELECT COUNT(*) FROM waitlist WHERE status IN ('waiting','offered') AND service_id=? AND created_at <= ?",
            (entry["service_id"], entry["created_at"]),
        )
        return rows[0][0]

    def waiting_entries(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM waitlist WHERE status='waiting' ORDER BY created_at")]

    def all_waitlist(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM waitlist ORDER BY created_at DESC LIMIT 100")]

    def set_waitlist_status(self, wid: str, status: str) -> None:
        self._x("UPDATE waitlist SET status=? WHERE id=?", (status, wid))

    def expire_old_waitlist(self, today: date) -> int:
        with self.lock:
            cur = self.conn.execute(
                "UPDATE waitlist SET status='expired' WHERE status='waiting' AND date_to < ?", (today.isoformat(),)
            )
            self.conn.commit()
            return cur.rowcount

    # ---- offers -------------------------------------------------------
    def add_offer(self, waitlist_id: str, phone: str, service_id: str, stylist_id: str,
                  start: datetime, end: datetime, expires_at: datetime) -> dict:
        oid = uuid.uuid4().hex[:8]
        self._x(
            "INSERT INTO offers VALUES (?,?,?,?,?,?,?, 'pending', ?, ?)",
            (oid, waitlist_id, phone, service_id, stylist_id, _utc(start), _utc(end), _utc(expires_at), _now()),
        )
        return self.get_offer(oid)

    def get_offer(self, oid: str) -> dict | None:
        rows = self._q("SELECT * FROM offers WHERE id=?", (oid,))
        return dict(rows[0]) if rows else None

    def pending_offers(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM offers WHERE status='pending' ORDER BY created_at")]

    def pending_offer_for_phone(self, phone: str) -> dict | None:
        rows = self._q("SELECT * FROM offers WHERE status='pending' AND phone=? ORDER BY created_at", (phone,))
        return dict(rows[0]) if rows else None

    def offers_for_slot(self, stylist_id: str, start: datetime) -> list[dict]:
        return [dict(r) for r in self._q(
            "SELECT * FROM offers WHERE stylist_id=? AND start=?", (stylist_id, _utc(start)))]

    def all_offers(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM offers ORDER BY created_at DESC LIMIT 100")]

    def set_offer_status(self, oid: str, status: str) -> None:
        self._x("UPDATE offers SET status=? WHERE id=?", (status, oid))

    # ---- calls & outbox ---------------------------------------------------
    def record_call(self, call_id: str, phone: str, event: str, summary: str, duration: int | None,
                    payload: dict) -> bool:
        """Returns False if this call/event was already recorded (webhook retries)."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO calls VALUES (?,?,?,?,?,?,?)",
                (call_id, phone, event, summary, duration, _now(), json.dumps(payload, ensure_ascii=False)),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def recent_calls(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM calls ORDER BY received_at DESC LIMIT ?", (limit,))]

    def last_call_for_phone(self, phone: str) -> dict | None:
        rows = self._q("SELECT * FROM calls WHERE phone=? ORDER BY received_at DESC LIMIT 1", (phone,))
        return dict(rows[0]) if rows else None

    def add_outbox(self, kind: str, payload: dict, delivered: bool, error: str | None = None) -> None:
        self._x(
            "INSERT INTO outbox(kind, payload, delivered, error, created_at) VALUES (?,?,?,?,?)",
            (kind, json.dumps(payload, ensure_ascii=False), 1 if delivered else 0, error, _now()),
        )

    def outbox(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))]
