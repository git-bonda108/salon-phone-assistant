"""MCP servers generated from the tool registry (app/tools.py).

Two scopes, two servers, two credentials:

  * ``/mcp/caller``: the tools a caller-facing agent may use (same as Suisse Line's
    live tools). Authenticated with ``TOOL_API_KEY``.
  * ``/mcp/owner``: owner tools (appointments, waitlist, stats), read-only caller tools,
    and the Azure AI tools (PII redaction, sentiment, Prompt Shields, moderation).
    Authenticated with ``ADMIN_TOKEN``. The owner copilot and the call reviewer use it.

Our own agents talk MCP to these servers **in-process** (``InProcessMCP``): the same
protocol and the same tool list as external MCP clients, without a network hop.
"""

from __future__ import annotations

import hmac
import inspect
import json
from typing import Annotated, Any, Callable

from pydantic import Field

from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .booking import BookingService, ToolError
from .tools import CALLER_TOOLS, OWNER_TOOLS, ToolSpec, _enum

READ_ONLY_CALLER = ("check_availability", "find_appointments")


def _signature_fn(name: str, model, salon, call: Callable[[dict], Any]):
    """Build a function whose signature mirrors the pydantic model, so MCP derives the schema."""
    params, annotations = [], {}
    for fname, f in model.model_fields.items():
        extra = dict(f.json_schema_extra or {}) if isinstance(f.json_schema_extra, dict) else {}
        source = extra.pop("enum_source", None)
        if source:
            extra["enum"] = _enum(source, salon)
        ann = Annotated[f.annotation, Field(description=f.description, json_schema_extra=extra or None)]
        default = inspect.Parameter.empty if f.is_required() else f.default
        params.append(inspect.Parameter(fname, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=ann))
        annotations[fname] = ann

    def fn(**kwargs) -> str:
        return json.dumps(call(kwargs), ensure_ascii=False, default=str)

    fn.__signature__ = inspect.Signature(params, return_annotation=str)
    fn.__annotations__ = {**annotations, "return": str}
    fn.__name__ = name
    return fn


def _registry_tool(server: MCPServer, svc: BookingService, spec: ToolSpec) -> None:
    def call(kwargs: dict) -> dict:
        args = spec.model.model_validate(kwargs)
        try:
            svc.sweep()
            return getattr(svc, spec.method)(**args.model_dump())
        except ToolError as exc:
            return exc.to_dict()

    server.add_tool(
        _signature_fn(spec.name, spec.model, svc.salon, call),
        name=spec.name,
        description=spec.description,
        annotations=ToolAnnotations(readOnlyHint=spec.read_only, destructiveHint=not spec.read_only,
                                    idempotentHint=spec.read_only, openWorldHint=False),
        structured_output=False,
    )


def build_mcp_server(svc: BookingService, scope: str, ai_tools=None) -> MCPServer:
    if scope == "caller":
        specs = CALLER_TOOLS
        instructions = (f"Booking tools of {svc.salon.name}. Times only come from check_availability; "
                        "book only after the caller confirmed.")
    elif scope == "owner":
        specs = tuple(t for t in CALLER_TOOLS if t.name in READ_ONLY_CALLER) + OWNER_TOOLS
        instructions = f"Owner tools of {svc.salon.name}: read appointments, waitlist and stats; AI text tools."
    else:
        raise ValueError(scope)
    server = MCPServer(name=f"salon-{scope}", version="1.0.0", instructions=instructions)
    for spec in specs:
        _registry_tool(server, svc, spec)
    if scope == "owner" and ai_tools is not None:
        ai_tools.register_mcp(server)
    return server


def mcp_asgi(server: MCPServer, expected_token: Callable[[], str]):
    """Streamable-HTTP app (stateless, JSON responses) behind a bearer/API-key check."""
    inner = server.streamable_http_app(
        streamable_http_path="/", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def app(scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            supplied = headers.get("x-api-key", "")
            auth = headers.get("authorization", "")
            if not supplied and auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
            token = expected_token()
            if token and not hmac.compare_digest(supplied.encode(), token.encode()):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
            if scope.get("path") in ("", None):
                scope = {**scope, "path": "/"}
        await inner(scope, receive, send)

    return app


# --------------------------------------------------------- Agents SDK adapter
try:
    from agents.mcp import MCPServer as AgentsMCPServer
except ImportError:  # agents are optional at runtime
    AgentsMCPServer = object  # type: ignore[misc,assignment]


class InProcessMCP(AgentsMCPServer):  # type: ignore[misc,valid-type]
    """Lets an Agents SDK agent use one of our MCP servers through the MCP protocol, in-process."""

    def __init__(self, server: MCPServer, label: str, **kwargs):
        super().__init__(**kwargs)
        self._server = server
        self._label = label
        self._client: Client | None = None

    @property
    def name(self) -> str:
        return self._label

    async def connect(self):
        self._client = Client(self._server)
        await self._client.__aenter__()

    async def cleanup(self):
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None

    async def list_tools(self, run_context=None, agent=None):
        assert self._client, "connect() first"
        return (await self._client.list_tools()).tools

    async def call_tool(self, tool_name, arguments, meta=None):
        assert self._client, "connect() first"
        return await self._client.call_tool(tool_name, arguments or {})

    async def list_prompts(self):
        assert self._client
        return await self._client.list_prompts()

    async def get_prompt(self, name, arguments=None):
        assert self._client
        return await self._client.get_prompt(name, arguments or {})
