"""MCP servers generated from the registry: in-process and over streamable HTTP."""

import asyncio
import json

from fastapi.testclient import TestClient
from mcp import Client

from app.config import Settings
from app.main import create_app
from app.tools import CALLER_TOOLS


def run(coro):
    return asyncio.run(coro)


def test_caller_server_lists_exactly_the_caller_tools(app):
    async def go():
        async with Client(app.state.mcp["caller"]) as c:
            return (await c.list_tools()).tools
    tools = run(go())
    assert [t.name for t in tools] == [t.name for t in CALLER_TOOLS]
    book = next(t for t in tools if t.name == "book_appointment")
    assert book.annotations.destructive_hint is True
    assert "balayage" in book.input_schema["properties"]["service"]["enum"]


def test_owner_server_has_owner_and_ai_tools_but_no_write_tools(app):
    async def go():
        async with Client(app.state.mcp["owner"]) as c:
            return (await c.list_tools()).tools
    names = {t.name for t in run(go())}
    assert {"list_appointments", "list_waitlist", "salon_stats", "check_availability",
            "redact_pii", "prompt_shield", "moderate_text"} <= names
    assert not names & {"book_appointment", "cancel_appointment", "reschedule_appointment"}


def test_booking_over_mcp_matches_http(app):
    async def go():
        async with Client(app.state.mcp["caller"]) as c:
            r = await c.call_tool("book_appointment", {"service": "mens_cut", "start": "2026-11-10T10:00",
                                                      "stylist": "marco", "customer_name": "Ben Keller",
                                                      "phone": "+41785556677"})
            again = await c.call_tool("book_appointment", {"service": "mens_cut", "start": "2026-11-10T10:00",
                                                          "stylist": "marco", "customer_name": "Ben Keller",
                                                          "phone": "+41785556677"})
            return json.loads(r.content[0].text), json.loads(again.content[0].text)
    first, second = run(go())
    assert first["ok"] and second["already_booked"]


def test_streamable_http_requires_the_right_key(clock):
    app = create_app(Settings(database_path=":memory:", sweep_interval_seconds=0, tool_api_key="caller-key",
                              admin_token="owner-key"), clock=clock)
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(app) as c:
        assert c.post("/mcp/caller/", json=init, headers=hdr).status_code == 401
        assert c.post("/mcp/caller/", json=init, headers={**hdr, "X-Api-Key": "owner-key"}).status_code == 401
        ok = c.post("/mcp/caller/", json=init, headers={**hdr, "X-Api-Key": "caller-key"})
        assert ok.status_code == 200 and "serverInfo" in ok.text
        assert c.post("/mcp/owner/", json=init, headers={**hdr, "Authorization": "Bearer owner-key"}).status_code == 200
