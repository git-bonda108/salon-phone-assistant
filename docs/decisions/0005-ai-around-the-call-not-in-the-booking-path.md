# 0005 · Agents work around the call, not in the booking path

**Context.** It is tempting to add agents everywhere. Each one adds latency, cost and a failure mode. A salon pays from CHF 89 a month for Suisse Line, so per-call cost matters.

**Decision.** Agents (OpenAI Agents SDK, Azure OpenAI in Switzerland North) are used only where judgement adds value and nobody waits on the phone:

| Agent | When it runs | Why an agent |
| --- | --- | --- |
| Call reviewer | after each call (webhook) | reads a messy transcript, checks the assistant's claims against the calendar, redacts personal data, flags problems |
| Prompt lab | on demand | the phone prompt and tools in text form, so prompt changes can be regression-tested cheaply |
| Simulated callers + judge | in evaluation runs | realistic, adversarial, multilingual callers at scale; rubric-based scoring |
| Owner copilot | in the web app | questions about bookings and the waitlist; any change needs explicit approval |

**Consequences.** If every agent is switched off, the phone assistant still books correctly. Review cost per call is tracked and the reviewer can be disabled per salon.
