"""Web app: owner cockpit and public demo.

Access model
  * ``PUBLIC_DEMO=1`` (the demo deployment, synthetic data only): views are public and
    read-only; the prompt lab and copilot are public but rate-limited; nothing can change
    real data (the prompt lab books into its sandbox).
  * Otherwise every view and agent endpoint needs the admin token.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from . import demo
from .security import check_api_key
from .timeutil import local_iso, parse_date

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent / "web"
ARCHITECTURE_SVG = ROOT / "docs" / "architecture" / "system-architecture.svg"


class RateLimiter:
    """Per-client sliding window plus a global daily cap (protects model spend on the public demo)."""

    def __init__(self, per_client: int = 20, window_s: int = 600, daily_cap: int = 600):
        self.per_client, self.window_s, self.daily_cap = per_client, window_s, daily_cap
        self.hits: dict[str, deque] = defaultdict(deque)
        self.day = time.strftime("%Y-%m-%d")
        self.today = 0

    def allow(self, client: str) -> bool:
        now = time.time()
        if time.strftime("%Y-%m-%d") != self.day:
            self.day, self.today = time.strftime("%Y-%m-%d"), 0
        q = self.hits[client]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.per_client or self.today >= self.daily_cap:
            return False
        q.append(now)
        self.today += 1
        return True


async def demo_startup(svc, team) -> None:
    await asyncio.to_thread(demo.seed, svc)
    for call_id, phone, summary, transcript in demo.TRANSCRIPTS:
        try:
            await team.review_call(call_id, {"event": "call.completed", "callId": call_id, "phoneNumber": phone,
                                             "summary": summary, "transcript": transcript})
        except Exception:  # a failed demo review must not stop the app
            pass


def register_web(app: FastAPI, settings, svc, team, store, cal) -> None:
    limiter = RateLimiter()
    app.state.limiter = limiter

    def is_admin(request: Request) -> bool:
        supplied = (request.headers.get("x-admin-token") or request.query_params.get("token")
                    or (request.headers.get("authorization") or "").removeprefix("Bearer ").strip())
        return check_api_key(settings.admin_token, supplied, None)

    def viewer(request: Request) -> None:
        if not (settings.public_demo or is_admin(request)):
            raise HTTPException(status_code=401, detail="admin token required")

    def costly(request: Request) -> None:
        """Endpoints that call a model: admin, or public demo within the rate limit."""
        if is_admin(request):
            return
        if not settings.public_demo:
            raise HTTPException(status_code=401, detail="admin token required")
        client = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
        if not limiter.allow(client):
            raise HTTPException(status_code=429, detail="Demo limit reached, please try again in a few minutes.")

    # ------------------------------------------------------------------ pages
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/static/{name}", include_in_schema=False)
    def static(name: str):
        allowed = {"app.css": "text/css", "app.js": "application/javascript", "favicon.svg": "image/svg+xml"}
        if name == "architecture.svg":
            return FileResponse(ARCHITECTURE_SVG, media_type="image/svg+xml")
        if name not in allowed:
            raise HTTPException(status_code=404)
        return FileResponse(WEB / name, media_type=allowed[name], headers={"Cache-Control": "no-cache"})

    # ------------------------------------------------------------------- views
    @app.get("/api/overview")
    def overview(request: Request):
        viewer(request)
        now = svc.now()
        stats = svc.salon_stats(30)
        upcoming = cal.list_range(now, now + timedelta(days=7))
        reviews = store.reviews(200)
        events = store.events_since(now - timedelta(days=30))
        return {
            "ok": True, "now": local_iso(now), "salon": {"name": svc.salon.name, "address": svc.salon.address,
                                                         "hours": svc.salon.opening_hours_text()},
            "engines": {"calendar": settings.calendar_backend, "ai": "azure" if team.ai.online else "offline-rules",
                        "agents": "azure-openai" if team.online else "offline",
                        "model": settings.azure_openai_deployment if team.online else None,
                        "region": "Switzerland North"},
            "stats": stats,
            "upcoming_7d": sum(1 for a in upcoming if a.service_id != "block"),
            "waitlist": len([w for w in store.all_waitlist() if w["status"] in ("waiting", "offered")]),
            "calls_reviewed": len(reviews),
            "calls_flagged": sum(1 for r in reviews if r["needs_attention"]),
            "guardrail_blocks": sum(1 for e in events if e["kind"] == "guardrail_blocked"),
            "public_demo": settings.public_demo,
        }

    @app.get("/api/salon")
    def salon_info(request: Request):
        viewer(request)
        s = svc.salon
        return {"name": s.name, "address": s.address, "hours": s.opening_hours_text(),
                "services": [x.public() for x in s.services],
                "stylists": [{"id": x.id, "name": x.name, "role": x.role, "services": sorted(x.services)} for x in s.stylists]}

    @app.get("/api/calendar")
    def calendar(request: Request, date: str | None = None):
        viewer(request)
        day = parse_date(date, svc.now().date())
        if day is None:  # default: today, or the next day the salon is open
            day = svc.now().date()
            for _ in range(7):
                if svc.salon.hours_on(day.weekday()):
                    break
                day += timedelta(days=1)
        tz = svc.salon.tz
        start = datetime.combine(day, datetime.min.time(), tz)
        appts = cal.list_range(start, start + timedelta(days=1))
        holds = [svc._fmt_offer(o) for o in store.pending_offers()
                 if datetime.fromisoformat(o["start"]).astimezone(tz).date() == day]
        return {
            "ok": True, "date": day.isoformat(), "weekday": day.strftime("%A"),
            "hours": [[a.strftime("%H:%M"), b.strftime("%H:%M")] for a, b in svc.salon.hours_on(day.weekday())],
            "stylists": [{"id": s.id, "name": s.name, "working": s.works_on(day.weekday())} for s in svc.salon.stylists],
            "appointments": [{"id": a.id, "stylist": a.stylist_id, "service": a.service_id,
                              "service_name": (svc.salon.find_service(a.service_id).name
                                               if svc.salon.find_service(a.service_id) else "Blocked"),
                              "start": local_iso(a.start), "end": local_iso(a.end),
                              "customer": a.customer_name if a.service_id != "block" else "Blocked",
                              "source": a.source} for a in appts],
            "holds": holds,
        }

    @app.get("/api/waitlist")
    def waitlist(request: Request):
        viewer(request)
        return {"ok": True, "waitlist": store.all_waitlist(), "offers": [
            svc._fmt_offer(o) | {"status": o["status"], "phone": o["phone"]} for o in store.all_offers()]}

    @app.get("/api/activity")
    def activity(request: Request, limit: int = 40):
        viewer(request)
        rows = store.events_since(svc.now() - timedelta(days=30))[-limit:]
        return {"ok": True, "events": [r | {"payload": json.loads(r["payload"])} for r in reversed(rows)]}

    @app.get("/api/reviews")
    def reviews(request: Request, limit: int = 50):
        viewer(request)
        return {"ok": True, "reviews": store.reviews(limit)}

    @app.get("/api/traces")
    def traces(request: Request, limit: int = 30):
        viewer(request)
        return {"ok": True, "traces": store.recent_traces(limit)}

    @app.get("/api/traces/{trace_id}")
    def trace_spans(trace_id: str, request: Request):
        viewer(request)
        return {"ok": True, "trace_id": trace_id, "spans": store.spans(trace_id)}

    # ------------------------------------------------------------------ agents
    async def _message(request: Request) -> dict:
        body = await request.json()
        msg = str(body.get("message", "")).strip()[:1500]
        if not msg:
            raise HTTPException(status_code=400, detail="message required")
        return body | {"message": msg}

    @app.post("/agents/prompt-lab")
    async def prompt_lab(request: Request):
        costly(request)
        body = await _message(request)
        return await team.prompt_lab(str(body.get("session_id") or "default")[:64], body["message"],
                                     str(body.get("phone") or "+41790000000")[:20])

    @app.post("/agents/prompt-lab/reset")
    async def prompt_lab_reset(request: Request):
        costly(request)
        team.reset_sandbox()
        return {"ok": True}

    @app.post("/agents/copilot")
    async def copilot(request: Request):
        costly(request)
        body = await _message(request)
        return await team.copilot(str(body.get("session_id") or "owner")[:64], body["message"])

    # ------------------------------------------------------------- demo control
    @app.post("/api/demo/recovery")
    async def demo_recovery(request: Request):
        costly(request)
        return await asyncio.to_thread(demo.run_recovery_scenario, svc)

    @app.post("/api/demo/reset")
    async def demo_reset(request: Request):
        costly(request)
        if not (settings.demo_seed and hasattr(cal, "reset")):
            raise HTTPException(status_code=400, detail="reset is only available in the demo")
        await asyncio.to_thread(cal.reset)
        await asyncio.to_thread(store.reset_all)
        team.reset_sandbox()
        await demo_startup(svc, team)
        return {"ok": True}
