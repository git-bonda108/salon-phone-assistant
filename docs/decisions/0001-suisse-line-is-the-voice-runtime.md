# 0001 · Suisse Line is the voice runtime

**Context.** The challenge is to build the assistant *on Suisse Line*. Alternatives such as OpenAI Realtime, Azure Voice Live or Twilio + LLM could also answer calls.

**Decision.** The live conversation (speech recognition, voice, dialogue, phone number, forwarding, outbound calls) runs on Suisse Line. This repository supplies what Suisse Line deliberately leaves open: the business logic behind its live tools, the waitlist, and everything used to test and operate the assistant.

**Why.** Suisse Line's Swiss-German recognition and dialect voices are its core asset, and the client of this work is the Suisse Line team. Rebuilding the voice layer would answer a different question.

**Consequences.** We don't control the in-call model, so guardrails sit at the tool boundary and after the call ([0006](0006-layered-guardrails-where-we-have-control.md)). Tool schemas must respect Suisse Line's documented limits; a contract test enforces them.
