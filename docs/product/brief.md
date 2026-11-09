# Product brief: Lina, the phone assistant for Salon Edelweiss

## The ask, read carefully

The role is *"turn ideas into real IT products – independently: concept → build with AI coding agents → test → launch."* Challenge A asks for four concrete things, and the interview asks for two more:

| # | Asked for | How it is answered | Evidence | Status |
|---|---|---|---|---|
| A1 | Create a free trial account at suisse-line.ch | The assistant runs **on Suisse Line**: prompt, knowledge base, voice, phone number and live tools are configured in the portal. Suisse Line is not replaced by another voice stack | [`suisse-line/`](../../suisse-line), [setup guide](../operations/setup-suisse-line.md), live phone number | in setup |
| A2 | Phone assistant for a small business that answers calls and books appointments | A hair salon with 3 stylists, 8 services and real constraints: durations, skills, working days, buffers, lead time. Callers can book, move and cancel, and ask about prices and policies in DE-CH, DE, FR, IT and EN | [`app/`](../../app), [call simulator](../../scripts/simulate_call.py), test calls | built, tested |
| A3 | Connect it to any calendar | **Google Calendar**, one calendar per stylist, which stays the single source of truth. A **Microsoft 365 / Outlook** adapter (Microsoft Graph) plugs into the same six-method interface | [calendar adapters](../../app/calendars), [setup](../operations/google-calendar-setup.md) | Google + Outlook built, tested |
| A4 | Add **one** feature of your own that makes it more useful | **Empty-Chair Recovery**: when the salon is full, callers join a waitlist; when someone cancels, the freed slot is offered to the best-matching person by an outbound call, held 20 minutes, and passed on if they decline | [feature section](#the-one-feature-empty-chair-recovery), [tests](../../tests/test_waitlist.py) | built, tested |
| I1 | Show how you built it | Built with AI coding agents. Architecture, decisions and trade-offs are documented as decision records | [architecture](../architecture/overview.md), [decisions](../decisions/) | documented |
| I2 | Show how you tested it | Four levels: deterministic tests in CI, platform-contract tests, AI-driven conversation evaluation with attacker personas, and real calls on Suisse Line with a measured test matrix | [evaluation](../evaluation/strategy.md), [safety](../safety/guardrails.md) | levels 1–2 in CI (85 tests) · levels 3–4 in progress |
| R1 | *…and launch it* | Deployed in **Azure Switzerland North** with infrastructure as code, keyless deployment, monitoring, a runbook and a live phone number | [operations](../operations/) | infrastructure built · deployment pending |

Two readings shaped every design decision:

1. **"One feature" means one.** Everything else in this repository serves *building, testing, operating or securing* the assistant. None of it is a second feature competing for attention.
2. **Suisse IT sells Swiss trust.** Their products promise Swiss hosting, DSG compliance and no training on customer data. A solution that quietly sends salon customers' data abroad would contradict the product it runs on. Hence the decision to [process only in Switzerland](../decisions/0004-switzerland-only-processing.md).

## Users and their jobs

| Who | Job to be done | Pain today |
| --- | --- | --- |
| **Caller** (salon customer) | Get an appointment quickly, in their language, at any hour | The salon doesn't answer while cutting; callbacks are missed |
| **Salon owner** (Lara) | Keep chairs full without being on the phone | Interrupted work, and last-minute cancellations become lost revenue |
| **Stylists** | Keep using their calendar as they always have | New tools that need double entry |
| **Waitlisted customer** | Get the slot they wanted when it frees up | Nobody remembers to call them |

## The one feature: Empty-Chair Recovery

**Why this feature and not another.** I compared the candidates by value to the salon, fit with Suisse Line, and how measurable the result is:

| Candidate | Value to the salon | Uses Suisse Line strengths | Measurable in CHF |
| --- | --- | --- | --- |
| SMS reminders | fewer no-shows | partly (SMS on request) | indirectly |
| Caller recognition | nicer experience | yes | no |
| No-show deposit | fewer no-shows | no (payment flow) | yes |
| **Empty-Chair Recovery** | **turns cancellations into bookings** | **yes: inbound + outbound agents, webhooks** | **yes: every recovered slot has a price** |

The assistant moves from *saving time* to *earning money*. At Suisse Line's entry price of CHF 89 a month, one recovered balayage (CHF 249) pays for almost three months.

**How it works:** waitlist entries record service, stylist, date range and time of day. A cancellation or move frees time, and an exact match is computed: the service fits the gap, the stylist offers it, the preferences match. The slot is held for the first match, who gets an outbound call. If they decline or don't answer, it cascades to the next person, and nobody is offered the same slot twice.

## Success metrics

| Metric | Target (pilot salon, 4 weeks) |
| --- | --- |
| Calls answered | 100 % (vs. missed calls today) |
| Booking completion (calls that want a booking → booked) | ≥ 85 % |
| Wrong bookings (wrong day, time, stylist or service) | **0** |
| Median time to book | < 2 min |
| Cancelled slots re-filled via waitlist | ≥ 30 % |
| Calls needing a human | < 10 % |

## Scope

**In:** booking, moving, cancelling, FAQ, waitlist and recovery, owner dashboard, post-call review, multilingual.
**Out (consciously):** payments and deposits, colour consultations by phone (handed to a human), multi-location chains, a CRM.

## From idea to launch

| Phase | Output |
| --- | --- |
| Concept | this brief, [architecture](../architecture/overview.md), [decision records](../decisions/) |
| Build | deterministic booking core first, then the Suisse Line agent, then AI around it (review, evaluation, safety) |
| Test | [evaluation strategy](../evaluation/strategy.md): CI → conversation evaluation → real calls |
| Launch | Azure Switzerland North, live phone number, monitoring, [runbook](../operations/) |
