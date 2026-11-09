# Interview: 20-minute presentation plan

**1. The problem (2 min)**
A hair salon owner has scissors in hand all day. Calls go unanswered, and each missed call can be a lost booking. A last-minute cancellation leaves an empty chair, even though someone was turned away earlier that week.

**2. Live demo: booking (5 min)**
* Call the Suisse Line number on speaker: Swiss German, "Damehaarschnitt morn am Namittag". Show ≤ 3 options, confirmation, booking.
* Show the event appearing in Lara's Google Calendar (phone number in the description).
* Call again from the same phone: greeted by name, offered the usual stylist, move the appointment by 30 min.

**3. My feature: Empty-Chair Recovery (4 min)**
* Second phone: ask for Saturday with Marco (pre-filled as full) → waitlist.
* First phone: cancel a Saturday appointment → `waitlist.offer` fires → outbound call / SMS → accept.
* Show `/admin`: waitlist, offer with hold and expiry, outbox, call log from the webhook.
* Backup if the network is bad: `python scripts/simulate_call.py waitlist`.

**4. How I built it (4 min)**
* Started from Suisse Line's own docs: integration paths (live tools, signed webhooks) and the voice-agent limits (JSON-Schema subset, 30 s tool timeout, 16k prompt budget). Those shaped the design.
* Built with an AI coding agent (Claude Code): I set the architecture and the voice-specific tool design (flat enums, speakable `message`, max 3 options, idempotency, fail-closed), the agent wrote code and tests, and I reviewed and steered.
* The calendar stays the single source of truth; one JSON file onboards a new business.

**5. How I tested it (4 min)**
* `pytest`: 39 tests in < 1 s. Show the double-booking and waitlist-cascade tests, and the contract test that checks `tools.json` against Suisse Line's documented limits.
* Mutation check: removing the slot hold breaks the tests.
* `agent_eval.py`: scripted conversations with the real agent in Swiss German, French and English, including adversarial ones (no booking without a yes, no cancelling a friend's appointment).
* Manual call matrix (15 cases) with results and metrics: wrong bookings 0, call duration, human hand-over rate.

**6. Limitations and next steps (1 min)**
Caller-ID injection plus SMS verification for sensitive actions, day-before reminder SMS, an Outlook adapter, and a weekly owner report showing recovered revenue in CHF.
