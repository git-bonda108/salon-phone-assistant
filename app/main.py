"""HTTP layer: Suisse Line live-tool endpoints, call webhook, admin dashboard.

Design rules for voice-agent tools:
  * Every /tools/* call answers HTTP 200 with ``{"ok": ..., "message": ...}``.
    A voice agent handles "ok: false + a sentence to say" far better than a
    4xx/5xx it can only report as "something went wrong".
  * Arguments may arrive flat or wrapped in ``arguments``/``parameters``.
  * Answers must be fast: Suisse Line expects a tool result within 30 s.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .booking import BookingService, ToolError
from .calendars.sqlite_calendar import SqliteCalendar
from .config import Settings
from .notifier import Notifier
from .salon import load_salon
from .security import check_api_key, verify_signature
from .store import Store
from .timeutil import local_iso, normalize_phone

log = logging.getLogger("app")


# ---------------------------------------------------------------- tool inputs
class _In(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class LookupCallerIn(_In):
    phone: str | None = None


class CheckAvailabilityIn(_In):
    service: str
    date: str | None = None
    stylist: str | None = None
    time_of_day: str | None = "any"
    preferred_time: str | None = None


class BookIn(_In):
    service: str
    start: str
    customer_name: str
    phone: str
    stylist: str | None = None
    notes: str | None = ""


class PhoneIn(_In):
    phone: str


class AppointmentIn(_In):
    phone: str
    appointment_id: str


class RescheduleIn(AppointmentIn):
    new_start: str
    stylist: str | None = None


class WaitlistIn(_In):
    phone: str
    customer_name: str
    service: str
    date_from: str | None = None
    date_to: str | None = None
    stylist: str | None = None
    time_of_day: str | None = "any"


class OfferIn(_In):
    phone: str
    offer_id: str


TOOLS: dict[str, tuple[type[BaseModel], str]] = {
    "lookup_caller": (LookupCallerIn, "lookup_caller"),
    "check_availability": (CheckAvailabilityIn, "check_availability"),
    "book_appointment": (BookIn, "book_appointment"),
    "find_appointments": (PhoneIn, "find_appointments"),
    "cancel_appointment": (AppointmentIn, "cancel_appointment"),
    "reschedule_appointment": (RescheduleIn, "reschedule_appointment"),
    "join_waitlist": (WaitlistIn, "join_waitlist"),
    "accept_offer": (OfferIn, "accept_offer"),
    "decline_offer": (OfferIn, "decline_offer"),
}


def _unwrap(body: object) -> dict:
    if not isinstance(body, dict):
        return {}
    for key in ("arguments", "parameters", "args", "input"):
        inner = body.get(key)
        if isinstance(inner, dict):
            return inner
        if isinstance(inner, str):
            try:
                parsed = json.loads(inner)
                if isinstance(parsed, dict):
                    return parsed
            except ValueError:
                pass
    return body


def build_calendar(settings: Settings, conn: sqlite3.Connection, salon):
    if settings.calendar_backend == "google":
        from .calendars.google_calendar import GoogleCalendar, build_google_service

        ids = {s.id: settings.google_calendar_ids.get(s.id) or s.google_calendar_id for s in salon.stylists}
        return GoogleCalendar(build_google_service(settings.google_service_account_json), ids, salon.tz)
    return SqliteCalendar(conn, salon.tz)


def create_app(settings: Settings | None = None, clock: Callable[[], datetime] | None = None,
               calendar=None) -> FastAPI:
    settings = settings or Settings.from_env()
    salon = load_salon(settings.salon_config)
    if settings.database_path != ":memory:":
        Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    store = Store(conn)
    cal = calendar or build_calendar(settings, conn, salon)
    notifier = Notifier(store, settings.notify_webhook_url, settings.notify_signing_secret)
    svc = BookingService(salon, cal, store, notifier, clock=clock, offer_ttl_minutes=settings.offer_ttl_minutes)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if settings.sweep_interval_seconds > 0:
            async def loop():
                while True:
                    await asyncio.sleep(settings.sweep_interval_seconds)
                    try:
                        await asyncio.to_thread(svc.sweep)
                    except Exception:
                        log.exception("sweep failed")
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title=f"{salon.name} – phone assistant backend", version="1.0.0", lifespan=lifespan)
    app.state.svc = svc
    app.state.settings = settings

    @app.get("/health")
    def health():
        return {"ok": True, "salon": salon.name, "calendar": settings.calendar_backend}

    @app.post("/tools/{tool}")
    async def run_tool(tool: str, request: Request):
        if not check_api_key(settings.tool_api_key, request.headers.get("x-api-key"),
                             request.headers.get("authorization")):
            raise HTTPException(status_code=401, detail="invalid api key")
        if tool not in TOOLS:
            raise HTTPException(status_code=404, detail="unknown tool")
        model, method = TOOLS[tool]
        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            args = model.model_validate(_unwrap(body))
        except ValidationError as exc:
            missing = ", ".join(str(e["loc"][-1]) for e in exc.errors())
            return {"ok": False, "error": "invalid_input",
                    "message": f"Missing or invalid fields: {missing}. Ask the caller and try again."}
        try:
            await asyncio.to_thread(svc.sweep)
            result = await asyncio.to_thread(getattr(svc, method), **args.model_dump())
        except ToolError as exc:
            result = exc.to_dict()
        except Exception:
            log.exception("tool %s failed", tool)
            result = {"ok": False, "error": "internal",
                      "message": "Technical problem with the booking system. Take a message with name, "
                                 "phone number and wish, and promise a call-back."}
        log.info("tool %s %s -> ok=%s", tool, args.model_dump(), result.get("ok"))
        return JSONResponse(result)

    @app.post("/webhooks/suisse-line")
    async def suisse_line_webhook(request: Request):
        raw = await request.body()
        if settings.webhook_secret:
            if not verify_signature(settings.webhook_secret, raw,
                                    request.headers.get(settings.webhook_signature_header)):
                raise HTTPException(status_code=401, detail="invalid signature")
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid json")
        event = payload.get("event", "unknown")
        call_id = str(payload.get("callId") or payload.get("call_id") or "")
        if not call_id:
            raise HTTPException(status_code=400, detail="missing callId")
        is_new = store.record_call(
            f"{call_id}:{event}", normalize_phone(payload.get("phoneNumber")), event,
            payload.get("summary", ""), payload.get("duration"), payload,
        )
        return {"ok": True, "duplicate": not is_new}

    @app.get("/admin", response_class=HTMLResponse)
    def admin(token: str = ""):
        if settings.admin_token and token != settings.admin_token:
            raise HTTPException(status_code=401, detail="admin token required (?token=...)")
        now = svc.now()
        appts = cal.list_range(now - timedelta(hours=2), now + timedelta(days=14))
        return render_dashboard(salon, svc, appts, store, now)

    return app


def _table(title: str, headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c if c is not None else ''))}</td>" for c in r) + "</tr>"
                   for r in rows) or f"<tr><td colspan='{len(headers)}' class='empty'>nothing yet</td></tr>"
    return f"<section><h2>{html.escape(title)}</h2><div class='scroll'><table><tr>{head}</tr>{body}</table></div></section>"


def render_dashboard(salon, svc: BookingService, appts, store: Store, now: datetime) -> str:
    tz = salon.tz
    def loc(v: str) -> str:
        try:
            return local_iso(datetime.fromisoformat(v).astimezone(tz)).replace("T", " ")
        except (TypeError, ValueError):
            return v
    sections = [
        _table("Upcoming appointments (14 days)", ["When", "Stylist", "Service", "Customer", "Phone", "Source"],
               [[local_iso(a.start).replace("T", " "), a.stylist_id, a.service_id, a.customer_name, a.phone, a.source]
                for a in appts]),
        _table("Waitlist", ["Since", "Name", "Phone", "Service", "Stylist", "From", "To", "Time", "Status"],
               [[loc(w["created_at"]), w["name"], w["phone"], w["service_id"], w["stylist_id"] or "any",
                 w["date_from"], w["date_to"], w["time_of_day"], w["status"]] for w in store.all_waitlist()]),
        _table("Slot offers (Empty-Chair Recovery)", ["Created", "Phone", "Service", "Stylist", "Slot", "Expires", "Status"],
               [[loc(o["created_at"]), o["phone"], o["service_id"], o["stylist_id"], loc(o["start"]),
                 loc(o["expires_at"]), o["status"]] for o in store.all_offers()]),
        _table("Notifications outbox", ["Time", "Type", "Delivered", "Payload"],
               [[loc(n["created_at"]), n["kind"], "yes" if n["delivered"] else f"no ({n['error']})", n["payload"][:160]]
                for n in store.outbox()]),
        _table("Calls (Suisse Line webhooks)", ["Received", "Event", "Phone", "Duration s", "Summary"],
               [[loc(c["received_at"]), c["event"], c["phone"], c["duration"], c["summary"]] for c in store.recent_calls()]),
    ]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(salon.name)} – assistant</title>
<style>
:root{{--bg:#faf8f5;--fg:#1d1b19;--muted:#6b645d;--line:#e6e0d8;--accent:#b43b7b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151312;--fg:#eee9e3;--muted:#a39b92;--line:#2c2825}}}}
body{{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:28px 0 8px;color:var(--accent)}}
p{{color:var(--muted);margin:0}} .scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--muted);font-weight:600}} td.empty{{color:var(--muted);font-style:italic}}
</style></head><body><main style="max-width:1100px;margin:auto">
<h1>{html.escape(salon.name)} · phone assistant</h1>
<p>{html.escape(salon.address)} · now {local_iso(now).replace("T", " ")} · {html.escape(salon.opening_hours_text())}</p>
{''.join(sections)}
</main></body></html>"""

