"""The agents (OpenAI Agents SDK) and the services that run them.

| Agent | Tools (MCP, in-process) | Guardrails |
| --- | --- | --- |
| Prompt lab (Lina in text) | caller scope, on a **sandbox** calendar | Prompt Shields + harm (input), grounding (output) |
| Call reviewer | owner scope (read-only + AI tools) | deterministic redaction, shields and claim check run before it |
| Owner copilot | owner scope (read-only + AI tools) | Prompt Shields + harm (input), grounding (output) |

Agents work around the call, never in the booking path (ADR 0005). With every
agent switched off, Lina still books correctly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Literal

from agents import (
    Agent,
    InputGuardrailTripwireTriggered,
    ModelSettings,
    OutputGuardrailTripwireTriggered,
    RunConfig,
    Runner,
    SQLiteSession,
    trace,
)
from pydantic import BaseModel, Field

from ..ai_tools import AzureAITools
from ..booking import BookingService
from ..calendars.sqlite_calendar import SqliteCalendar
from ..mcp_server import InProcessMCP, build_mcp_server
from ..notifier import Notifier
from ..store import Store
from .guardrails import AgentContext, EvidenceHooks, grounding, make_input_guardrail

log = logging.getLogger("agents")
ROOT = Path(__file__).resolve().parent.parent.parent
SYSTEM_PROMPT = (ROOT / "suisse-line" / "system-prompt.md").read_text(encoding="utf-8")
KNOWLEDGE = (ROOT / "suisse-line" / "knowledge-base" / "salon-edelweiss.md").read_text(encoding="utf-8")

BLOCKED_INPUT = ("I can only help with appointments and questions about Salon Edelweiss. "
                 "Ich cha Ihne gern bi Termin und Frage zum Salon hälfe.")
BLOCKED_OUTPUT = ("Let me check that in the calendar first, so I don't tell you anything wrong. "
                  "Which day would suit you?")

TEXT_MODE = """

# Text mode (prompt lab)
This is a text chat used to test the phone assistant. Caller ID: {phone}.
Behave exactly as on the phone, but reply in plain text. Use the tools; never state a time or price
that is not in a tool result or the knowledge base below.

# Knowledge base
{knowledge}
"""

REVIEWER_INSTRUCTIONS = """You review completed phone calls of Salon Edelweiss for the salon owner.
You receive: the redacted transcript, the caller's phone number, Suisse Line's own summary, and facts that
deterministic checks already established (bookings recorded during the call, prompt-attack and harm results).
Use the tools to look up the caller's appointments when the transcript mentions booking, moving or cancelling,
and check that what the assistant SAID it did really happened.
Return the structured review. Write summary_de in German, 1–2 sentences, for a busy salon owner.
Set needs_owner_attention when a claim is not verified, the caller was unhappy, wanted a human, mentioned
something the salon must act on (complaint, allergy, colour correction), or a safety flag is present.
Never repeat personal data beyond first names."""

COPILOT_INSTRUCTIONS = """You are the owner's assistant for Salon Edelweiss (Zürich). Answer questions about
appointments, the waitlist, recovered slots and calls, using the tools. You can only read; for changes, tell the
owner what to do (the calendar stays the place where staff edit). Be brief and concrete, numbers first.
Answer in the owner's language. Never invent numbers, times or prices."""


class CallReview(BaseModel):
    outcome: Literal["booked", "rescheduled", "cancelled", "waitlisted", "offer_accepted", "information",
                     "handed_over", "no_outcome", "abusive", "other"]
    summary_de: str = Field(description="1-2 sentences in German for the salon owner")
    assistant_claims: list[str] = Field(description="What the assistant said it did, e.g. 'booked Tue 14:00 with Lara'")
    claims_verified: bool = Field(description="True if every claim was confirmed with the tools")
    issues: list[str] = Field(description="Problems: unverified claim, misunderstanding, wrong info, unhappy caller")
    follow_up: str | None = Field(description="What the salon should do next, or null")
    needs_owner_attention: bool


BOOKING_CLAIM_RX = re.compile(
    r"\b(gebucht|buechet|iitreit|eingetragen|booked|you're booked|réservé|enregistré|prenotato|verschobe|verschoben|"
    r"moved|abgesagt|storniert|cancelled|canceled|annulé)\b", re.IGNORECASE)


def _transcript_text(payload: dict) -> str:
    t = payload.get("transcript", "")
    if isinstance(t, list):
        return "\n".join(f"{x.get('role', '')}: {x.get('text', '')}" for x in t if isinstance(x, dict))
    if isinstance(t, dict):
        segs = t.get("segments") or []
        return "\n".join(f"{s.get('speakerName') or s.get('speaker', '')}: {s.get('text', '')}" for s in segs)
    return str(t or "")


def sandbox_service(salon, clock) -> BookingService:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    store = Store(conn)
    return BookingService(salon, SqliteCalendar(conn, salon.tz), store, Notifier(store), clock=clock)


class AgentTeam:
    def __init__(self, svc: BookingService, ai: AzureAITools, model=None, judge_model=None):
        self.svc = svc
        self.ai = ai
        self.model = model
        self.judge_model = judge_model or model
        self.sandbox = sandbox_service(svc.salon, svc._clock)
        self._sessions: dict[str, tuple[SQLiteSession, AgentContext]] = {}
        self._mcp_sandbox = build_mcp_server(self.sandbox, "caller")
        self._mcp_owner = build_mcp_server(svc, "owner", ai_tools=ai)
        self._shields = make_input_guardrail(ai)

    @property
    def online(self) -> bool:
        return self.model is not None

    def reset_sandbox(self) -> None:
        self.sandbox = sandbox_service(self.svc.salon, self.svc._clock)
        self._mcp_sandbox = build_mcp_server(self.sandbox, "caller")
        self._sessions.clear()

    # ------------------------------------------------------------ agents
    def _receptionist(self, mcp, phone: str) -> Agent:
        return Agent(
            name="Lina (prompt lab)",
            instructions=SYSTEM_PROMPT + TEXT_MODE.format(phone=phone or "unknown", knowledge=KNOWLEDGE),
            model=self.model, mcp_servers=[mcp],
            model_settings=ModelSettings(temperature=0.2),
            input_guardrails=[self._shields], output_guardrails=[grounding],
        )

    def _reviewer(self, mcp) -> Agent:
        return Agent(name="Call reviewer", instructions=REVIEWER_INSTRUCTIONS, model=self.judge_model,
                     mcp_servers=[mcp], output_type=CallReview, model_settings=ModelSettings(temperature=0))

    def _copilot(self, mcp) -> Agent:
        return Agent(name="Owner copilot", instructions=COPILOT_INSTRUCTIONS, model=self.model, mcp_servers=[mcp],
                     model_settings=ModelSettings(temperature=0.1),
                     input_guardrails=[self._shields], output_guardrails=[grounding])

    async def _chat(self, kind: str, agent_factory, server, session_id: str, message: str, phone: str = "") -> dict:
        if not self.online:
            return {"ok": False, "engine": "offline", "message": "AI model not configured (offline mode)."}
        key = f"{kind}:{session_id}"
        if key not in self._sessions:
            self._sessions[key] = (SQLiteSession(key), AgentContext(caller_phone=phone, knowledge=KNOWLEDGE))
        session, ctx = self._sessions[key]
        mcp = InProcessMCP(server, f"salon-{kind}")
        await mcp.connect()
        guardrail, reply, trace_id = None, "", None
        try:
            with trace(f"{kind} chat", group_id=key) as t:
                trace_id = t.trace_id
                try:
                    result = await Runner.run(agent_factory(mcp, phone) if kind == "prompt-lab" else agent_factory(mcp),
                                              message, context=ctx, session=session, hooks=EvidenceHooks(),
                                              run_config=RunConfig(workflow_name=f"{kind} chat"))
                    reply = str(result.final_output)
                except InputGuardrailTripwireTriggered as exc:
                    guardrail = {"layer": "L3", "type": "input", "info": exc.guardrail_result.output.output_info}
                    reply = BLOCKED_INPUT
                except OutputGuardrailTripwireTriggered as exc:
                    guardrail = {"layer": "L3", "type": "output", "info": exc.guardrail_result.output.output_info}
                    reply = BLOCKED_OUTPUT
        finally:
            await mcp.cleanup()
        if guardrail:
            self.svc.store.add_event("guardrail_blocked", phone or None, None, {"agent": kind, **guardrail})
        return {"ok": True, "reply": reply, "guardrail": guardrail, "trace_id": trace_id}

    async def prompt_lab(self, session_id: str, message: str, phone: str = "+41790000000") -> dict:
        return await self._chat("prompt-lab", self._receptionist, self._mcp_sandbox, session_id, message, phone)

    async def copilot(self, session_id: str, message: str) -> dict:
        return await self._chat("copilot", lambda mcp, *_: self._copilot(mcp), self._mcp_owner, session_id, message)

    # ------------------------------------------------------------ call review
    async def review_call(self, call_id: str, payload: dict) -> dict:
        """Deterministic checks always; the agent adds judgement when a model is configured."""
        phone = payload.get("phoneNumber", "") or ""
        text = _transcript_text(payload)
        redaction, shield, harm, sentiment = await asyncio.gather(
            asyncio.to_thread(self.ai.redact_pii, text),
            asyncio.to_thread(self.ai.prompt_shield, text),
            asyncio.to_thread(self.ai.moderate_text, text),
            asyncio.to_thread(self.ai.analyze_sentiment, text),
        )
        from ..timeutil import normalize_phone
        p = normalize_phone(phone)
        recent = [e for e in self.svc.store.events_since(self.svc.now().replace(hour=0, minute=0))
                  if e["phone"] == p and e["kind"] in ("booked", "rescheduled", "cancelled", "waitlist_joined", "offer_accepted")]
        claimed = bool(BOOKING_CLAIM_RX.search(text))
        flags: list[str] = []
        if shield["attack_detected"]:
            flags.append("prompt_attack")
        if harm["flagged"]:
            flags.append("harmful_content")
        if claimed and not recent:
            flags.append("claim_without_record")  # assistant said booked/moved/cancelled, nothing recorded today
        if sentiment.get("sentiment") == "negative":
            flags.append("negative_sentiment")
        facts = {"recorded_today": [{"kind": e["kind"], "payload": json.loads(e["payload"])} for e in recent],
                 "booking_language_in_transcript": claimed, "prompt_shield": shield["attack_detected"],
                 "harm_max_severity": harm["max_severity"], "sentiment": sentiment.get("sentiment")}
        review: dict = {"outcome": "other", "summary_de": payload.get("summary", ""), "assistant_claims": [],
                        "claims_verified": not ("claim_without_record" in flags), "issues": [], "follow_up": None,
                        "needs_owner_attention": bool(flags)}
        engine, trace_id = "deterministic", None
        if self.online and text.strip():
            mcp = InProcessMCP(self._mcp_owner, "salon-owner")
            await mcp.connect()
            try:
                prompt = json.dumps({"phone": p, "suisse_line_summary": payload.get("summary", ""),
                                     "deterministic_facts": facts, "redacted_transcript": redaction["redacted_text"]},
                                    ensure_ascii=False)
                with trace("call review", group_id=call_id) as t:
                    trace_id = t.trace_id
                    result = await Runner.run(self._reviewer(mcp), prompt, run_config=RunConfig(workflow_name="call review"))
                review = result.final_output.model_dump()
                engine = "agent+deterministic"
            except Exception:
                log.exception("review agent failed; keeping deterministic review")
            finally:
                await mcp.cleanup()
        # deterministic flags always win: the model can add attention, never remove it
        needs = bool(review.get("needs_owner_attention")) or bool(flags)
        if not review.get("claims_verified", True) and "claim_unverified" not in flags:
            flags.append("claim_unverified")
            needs = True
        review["needs_owner_attention"] = needs
        record = {"call_id": call_id, "engine": engine, "flags": flags, "facts": facts, "review": review,
                  "ai_engines": {"redaction": redaction["engine"], "shield": shield["engine"], "harm": harm["engine"]}}
        self.svc.store.save_review(call_id, p, engine, needs, flags, record, redaction["redacted_text"], trace_id)
        return record
