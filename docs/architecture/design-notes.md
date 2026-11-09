# Design notes

## Principles
1. **The calendar is the truth.** No shadow booking database. Staff keep their Google Calendar habits, and the assistant adapts to them.
2. **Tools are designed for a voice LLM, not for a frontend.**
   * flat, simple parameters with enums (service and stylist IDs) instead of free text,
   * every answer contains a short `message` the agent can paraphrase. This keeps prompts short and behaviour consistent,
   * expected problems return `ok:false` plus a speakable reason (HTTP 200). A raw 4xx/5xx only lets the agent say "something went wrong",
   * at most 3 options per answer, because nobody can remember 12 times read aloud,
   * idempotent booking, since LLM agents sometimes repeat a tool call.
3. **Fail closed.** If Google cannot be read, nothing is offered. Double bookings in a salon are worse than a call-back.
4. **One config file per business.** `data/salon.json` and the prompt/knowledge base are the only things that change for a new customer.

## Key decisions
| Decision | Why | Trade-off |
| --- | --- | --- |
| Own backend via live tools instead of only Suisse Line's built-in calendar connection | durations per service, stylist skills and days, buffers, waitlist state | one small service to host |
| Google FreeBusy for availability | respects every event incl. manual ones; no title parsing | one API call per stylist per day checked |
| Phone number in private extended properties | find a caller's bookings reliably | bookings created by hand in Google are not findable by phone (they still block time) |
| Reschedule = free old → book new → restore old on failure (under lock) | moving by 30 min overlaps the old slot | the restored event gets a new ID (internal only) |
| Offer hold of 20 min | fair and fast; long enough for a call or SMS reply | the chair may stay held 20 min if nobody answers |
| SQLite + process lock | simple, zero-ops for one salon | one instance per salon; for multi-tenant scale → Postgres with row locks / unique constraint on (stylist, start) |

## Known limitations
* **Caller identity.** The phone number reaches the backend as a tool argument chosen by the LLM. The prompt forbids managing other people's bookings, and `cancel_other_persons_appointment` tests that, but a prompt is not a security boundary. Hardening, in order: (1) have Suisse Line inject the caller ID as a fixed tool variable or header the model cannot change; (2) for cancellations from a different number, send an SMS code to the number on the booking.
* **Waitlist offer delivery** depends on an automation (n8n/Make/Zapier) to start the outbound call, because the exact Suisse Line outbound endpoint is only documented for accounts with API access. The backend side (signed webhook, payload, hold, cascade) is complete and tested.
* **Not tested against a live Google account and the live agent here.** The adapter is tested against a fake client that mirrors the Google API calls. A real smoke test is in `google-calendar-setup.md`.
* Prices "from …" depend on hair length; the assistant quotes the base price and says the final price is confirmed in the salon.

## What I would build next
1. SMS/WhatsApp confirmation and reminder the day before (fewer no-shows; the reminder can offer "reply 2 to cancel", which feeds the waitlist).
2. Caller-ID injection plus SMS verification for sensitive actions.
3. Outlook/Microsoft 365 adapter.
4. Weekly owner report from the call log: calls, bookings, waitlist conversions, recovered revenue in CHF.
5. Multi-tenant: one backend for many salons, config per tenant.
