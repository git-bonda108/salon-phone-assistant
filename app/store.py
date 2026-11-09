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
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                       -- booked | cancelled | rescheduled | waitlist_joined | offer_sent | offer_accepted | offer_declined | offer_expired
    phone TEXT,
    amount_chf REAL,
    payload TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);
CREATE TABLE IF NOT EXISTS agent_spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_id TEXT,
    kind TEXT NOT NULL,
    name TEXT,
    started_at TEXT,
    ended_at TEXT,
    error TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON agent_spans(trace_id);
CREATE TABLE IF NOT EXISTS agent_traces (
    trace_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS call_reviews (
    call_id TEXT PRIMARY KEY,
    phone TEXT,
    created_at TEXT NOT NULL,
    engine TEXT NOT NULL,
    needs_attention INTEGER NOT NULL DEFAULT 0,
    flags TEXT NOT NULL,
    review TEXT NOT NULL,
    redacted_transcript TEXT NOT NULL,
    trace_id TEXT
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

    # ---- audit events ---------------------------------------------------
    def add_event(self, kind: str, phone: str | None = None, amount_chf: float | None = None,
                  payload: dict | None = None) -> None:
        self._x("INSERT INTO events(kind, phone, amount_chf, payload, at) VALUES (?,?,?,?,?)",
                (kind, phone, amount_chf, json.dumps(payload or {}, ensure_ascii=False), _now()))

    def events_since(self, since: datetime) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM events WHERE at >= ? ORDER BY id", (_utc(since),))]

    # ---- agent traces (kept locally, never exported) ----------------------
    def add_trace(self, trace_id: str, name: str) -> None:
        self._x("INSERT OR IGNORE INTO agent_traces VALUES (?,?,?)", (trace_id, name, _now()))

    def add_span(self, row: dict) -> None:
        self._x("INSERT OR REPLACE INTO agent_spans VALUES (?,?,?,?,?,?,?,?,?)",
                (row["span_id"], row["trace_id"], row.get("parent_id"), row["kind"], row.get("name"),
                 row.get("started_at"), row.get("ended_at"), row.get("error"),
                 json.dumps(row.get("data", {}), ensure_ascii=False, default=str)[:20000]))

    def spans(self, trace_id: str) -> list[dict]:
        rows = self._q("SELECT * FROM agent_spans WHERE trace_id=? ORDER BY started_at", (trace_id,))
        return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    def recent_traces(self, limit: int = 30) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM agent_traces ORDER BY started_at DESC LIMIT ?", (limit,))]

    # ---- call reviews ------------------------------------------------------
    def save_review(self, call_id: str, phone: str, engine: str, needs_attention: bool, flags: list[str],
                    review: dict, redacted_transcript: str, trace_id: str | None) -> None:
        self._x("INSERT OR REPLACE INTO call_reviews VALUES (?,?,?,?,?,?,?,?,?)",
                (call_id, phone, _now(), engine, 1 if needs_attention else 0, json.dumps(flags),
                 json.dumps(review, ensure_ascii=False), redacted_transcript, trace_id))

    def reviews(self, limit: int = 50) -> list[dict]:
        rows = self._q("SELECT * FROM call_reviews ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) | {"flags": json.loads(r["flags"]), "review": json.loads(r["review"])} for r in rows]

    def add_outbox(self, kind: str, payload: dict, delivered: bool, error: str | None = None) -> None:
        self._x(
            "INSERT INTO outbox(kind, payload, delivered, error, created_at) VALUES (?,?,?,?,?)",
            (kind, json.dumps(payload, ensure_ascii=False), 1 if delivered else 0, error, _now()),
        )

    def outbox(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))]
