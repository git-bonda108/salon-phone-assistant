"""Agent guardrails (layer L3).

* Input: Azure AI Content Safety **Prompt Shields** + harm categories on the user's
  message. Runs *before* the model (``run_in_parallel=False``): an attack costs no model call.
* Output: **grounding**. Every time (HH:MM) and every CHF amount in the agent's reply
  must appear in evidence from this conversation: tool results, the knowledge base,
  or the user's own words. Deterministic, no second model needed.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from agents import GuardrailFunctionOutput, RunContextWrapper, RunHooks, input_guardrail, output_guardrail

from ..ai_tools import AzureAITools

TIME_RX = re.compile(r"(?<![\d:])([01]?\d|2[0-3])[:.]([0-5]\d)(?![\d])")
CHF_RX = re.compile(r"(?:CHF|Fr\.)\s?(\d{1,4})(?:[.,](?:\d{2}|-|–))?|(\d{1,4})(?:[.,](?:\d{2}|-|–))?\s?(?:CHF|Franken|Franke|francs?|franchi)",
                    re.IGNORECASE)


@dataclass
class AgentContext:
    caller_phone: str = ""
    knowledge: str = ""
    evidence: list[str] = field(default_factory=list)   # tool results of this run
    user_messages: list[str] = field(default_factory=list)


class EvidenceHooks(RunHooks):
    """Collects tool results as evidence for the grounding guardrail."""

    async def on_tool_end(self, context: RunContextWrapper[AgentContext], agent, tool, result) -> None:
        if isinstance(context.context, AgentContext):
            context.context.evidence.append(str(result))


def extract_facts(text: str) -> tuple[set[str], set[str]]:
    times = {f"{int(h):02d}:{m}" for h, m in TIME_RX.findall(text)}
    prices = {a or b for a, b in CHF_RX.findall(text)}
    return times, prices


def ungrounded_facts(reply: str, evidence: list[str]) -> list[str]:
    """Times and prices the reply states that appear nowhere in the evidence."""
    r_times, r_prices = extract_facts(reply)
    e_times, e_prices = set(), set()
    for ev in evidence:
        t, p = extract_facts(ev)
        e_times |= t
        e_prices |= p
        e_prices |= set(re.findall(r'"price_chf":\s*(\d+)', ev))
    return sorted([f"time {t}" for t in r_times - e_times] + [f"CHF {p}" for p in r_prices - e_prices])


def _last_user_text(inp) -> str:
    if isinstance(inp, str):
        return inp
    for item in reversed(inp or []):
        if isinstance(item, dict) and item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def make_input_guardrail(ai: AzureAITools):
    @input_guardrail(name="prompt_shields_and_harm", run_in_parallel=False)
    async def shields(ctx: RunContextWrapper[AgentContext], agent, inp) -> GuardrailFunctionOutput:
        text = _last_user_text(inp)
        shield, harm = await asyncio.gather(asyncio.to_thread(ai.prompt_shield, text),
                                            asyncio.to_thread(ai.moderate_text, text))
        if isinstance(ctx.context, AgentContext):
            ctx.context.user_messages.append(text)
        return GuardrailFunctionOutput(
            output_info={"prompt_shield": shield, "harm": harm},
            tripwire_triggered=bool(shield["attack_detected"] or harm["flagged"]),
        )

    return shields


@output_guardrail(name="grounding")
async def grounding(ctx: RunContextWrapper[AgentContext], agent, output) -> GuardrailFunctionOutput:
    c = ctx.context if isinstance(ctx.context, AgentContext) else AgentContext()
    text = output if isinstance(output, str) else str(output)
    missing = ungrounded_facts(text, [*c.evidence, c.knowledge, *c.user_messages])
    return GuardrailFunctionOutput(output_info={"ungrounded": missing}, tripwire_triggered=bool(missing))
