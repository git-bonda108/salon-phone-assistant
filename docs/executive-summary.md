# Executive summary

**Lina is an AI receptionist for small Swiss service businesses, built on Suisse Line.** The reference customer is Salon Edelweiss, a hair salon in Zürich with three stylists. Lina answers every call in Swiss German, German, French, Italian or English, and books, moves and cancels appointments directly in the stylists' own calendars. Her extra feature is **Empty-Chair Recovery**: when a client cancels, the freed slot is offered to the waitlist by phone, so a cancellation becomes a booking.

| | |
| --- | --- |
| **Live demo** | <https://ca-lina-yrrwxlzlnd7w2.wittyisland-bad0e48d.switzerlandnorth.azurecontainerapps.io> (synthetic data) |
| **Code and documentation** | <https://github.com/git-bonda108/salon-phone-assistant> |
| **Challenge B concept** | [Voice profiles for Suisse Meets](voice-profiles/concept.md) |

## The business case

| For | Value |
| --- | --- |
| **The salon owner** | No missed calls while working; no double entry, because staff keep their calendar. Cancelled slots are re-sold automatically, with the result shown in Swiss francs |
| **The caller** | Gets through at any hour, in their own language, and books in under two minutes |
| **Suisse Line** | A vertical template ("salon") with a measurable revenue feature that uses its own outbound calls. It is onboarded by editing one configuration file, not code |

One recovered balayage (CHF 249) pays for almost three months of Suisse Line's entry plan (CHF 89 per month).

## How it works

* **Voice: Suisse Line.** Inbound and outbound agents, Swiss-German recognition and dialect voice, live tools, signed webhooks. The assistant is configured from [`suisse-line/`](../suisse-line).
* **Decisions: deterministic code.** Availability (service durations, stylist skills and days, buffers, lead time) and booking (check and book under a lock, idempotent, fail-closed) are plain, tested code. Models never decide a booking.
* **Judgement around the call: agents.** A call reviewer after every call, a prompt lab to test prompt changes, and a read-only owner copilot. They run on the OpenAI Agents SDK with Azure OpenAI in Switzerland North, and use the booking tools over MCP and Azure AI Content Safety and Language as tools.
* **Everything in Switzerland.** Suisse Line's Swiss data centres plus Azure Switzerland North, regional model deployment, local agent traces, keyless access, and secrets in Key Vault.

## How it is assured

| Assurance | Evidence |
| --- | --- |
| Bookings are always correct | 85 automated tests on every push: availability, booking races, waitlist cascade, both calendars, MCP scopes, agents with a scripted model |
| Guardrails are real | Five layers. Each agent guardrail was switched off on purpose to confirm its test fails |
| It fits the platform | Tool definitions checked against Suisse Line's documented limits; infrastructure compiles; container starts, all in CI |
| It runs in production | Keyless deployment pipeline to Azure Switzerland North with a post-deployment verification |

## What makes it different

1. **One feature, chosen for revenue.** Empty-Chair Recovery turns the assistant from a cost saver into a revenue source, and its effect is measurable.
2. **Correctness by construction.** The model talks; code decides. A model cannot double-book.
3. **Swiss trust carried through.** The product it runs on promises Swiss hosting, and every added component keeps that promise.
4. **Built to be operated.** Infrastructure as code, keyless deployment, monitoring, a runbook, and a web cockpit for the owner.

## Status

| Done | Next |
| --- | --- |
| Suisse Line agent configuration, booking core, Empty-Chair Recovery, Google and Microsoft 365 calendars, MCP servers, agents and guardrails, Azure deployment, web cockpit | Live phone number on a Suisse Line trial with real test calls; evaluation lab with AI caller personas; Outlook calendar connected in the live deployment |
