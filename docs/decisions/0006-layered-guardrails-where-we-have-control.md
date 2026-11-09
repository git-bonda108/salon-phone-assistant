# 0006 · Guardrails in layers, placed where we actually have control

**Context.** The in-call model belongs to Suisse Line. A guardrail we cannot enforce is decoration.

**Decision.** Five layers, each tested:

1. **Tool boundary (deterministic):** caller can only act on bookings under their own number, input validation, idempotency, fail-closed calendar reads, API key + HMAC.
2. **Prompt rules** in the Suisse Line agent: confirm before booking, never invent times or prices, hand over to a human.
3. **Agent guardrails** (Agents SDK input/output guardrails): Azure AI Content Safety *Prompt Shields* and harm checks on input; on output, a grounding check that every time or price mentioned appears in a tool result.
4. **Post-call audit** of every Suisse Line transcript: personal-data redaction (Azure AI Language), prompt-attack and harm detection, claim verification against the calendar.
5. **Red-team evaluation:** attacker personas (injection, social engineering, abuse, data fishing) with measured pass rates in every evaluation run.

**Consequences.** The strongest controls (layer 1) do not depend on any model. Details: [guardrails](../safety/guardrails.md).
