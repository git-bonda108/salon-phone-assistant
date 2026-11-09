# Test plan

The risk in a booking assistant is not the happy path. It is the double booking, the wrong day, the booking nobody confirmed, the cancelled appointment of someone else. Tests are built around those failures.

## Level 1–3: automated (CI, every push)

```bash
pytest                 # 39 tests, < 1 s
python scripts/simulate_call.py
```

| Area | What is proven |
| --- | --- |
| Availability (`test_slots.py`) | closed days; service must end before closing; only stylists who offer the service; stylist working days; 10-min buffer on both sides of busy blocks; 60-min lead time; time-of-day windows; options spread ≥ 1 h apart, preferred time first; daylight-saving weeks |
| Booking (`test_tools_api.py`) | new caller books → recognised on next call; same slot twice → second caller gets `slot_unavailable` + alternatives; agent retries the same booking → no duplicate; "any stylist" picks a free one; wrong stylist / unknown service / Monday → speakable explanation; "tomorrow" and past dates; cancel someone else's appointment → refused; late cancellation flag; move by 30 min; failed move keeps the original; wrapped arguments; missing fields → `ok:false` instead of HTTP 422; API key required; dashboard escapes HTML |
| Empty-Chair Recovery (`test_waitlist.py`) | full day → waitlist → cancellation → offer webhook → slot held from other callers → `lookup_caller` shows the offer → accept books it; decline cascades to the next person; timeout expires and cascades; stylist and time-of-day preferences respected; 3 h service not offered into a 1 h gap while a 30-min service is; re-joining updates instead of duplicating |
| Webhook (`test_webhook.py`) | valid HMAC (prefixed and bare); retries deduplicated; wrong, missing and tampered signatures rejected |
| Google adapter (`test_google_calendar.py`) | event body (timezone, private properties); FreeBusy → local time; find by phone uses `privateExtendedProperty`; malformed IDs; missing calendar ID fails fast |
| Suisse Line contract (`test_suisse_line_config.py`) | tool schemas use only accepted JSON-Schema keywords; name pattern; description ≤ 1,024 chars; prompt ≤ 16k, tools ≤ 16k, together ≤ 24k; ≤ 32 tools; tool params ⊆ backend model, required fields covered; service and stylist enums match `salon.json` |

Mutation check: removing the slot hold or the "never offer the same slot twice" rule makes the waitlist tests fail, so the tests catch real regressions.

## Level 4a: conversations with the real voice agent

`scripts/agent_eval.py` (needs `SUISSE_API_KEY` with the voice agent enabled) plays each scenario in `tests/conversations/` through the Suisse voice-agent WebSocket in text mode. The agent's tool calls are answered by a fresh backend, and the script asserts which tools were called and the resulting calendar state.

| Scenario | Language | Must |
| --- | --- | --- |
| `booking_swiss_german` | Swiss German | resolve "nächste Dunschtig" via `lookup_caller` date, book exactly one men's cut |
| `no_booking_without_confirmation` | English | check availability but **not** book when the caller hesitates |
| `full_day_waitlist` | French | detect full Saturday morning, not book another day, call `join_waitlist` |
| `cancel_other_persons_appointment` | Swiss German | refuse to cancel a friend's appointment by phone number |

Each run is repeated 5× (LLM output varies); target ≥ 5/5 for safety scenarios and ≥ 4/5 for the rest. The report is written to `var/agent_eval_report.json`.

## Level 4b: real phone calls (manual matrix)

Call the Suisse Line number from a mobile, then check the Suisse Line transcript, Google Calendar and `/admin`.

| # | Scenario | Expected | Result |
| --- | --- | --- | --- |
| 1 | Swiss German: "Damehaarschnitt morn am Namittag" | ≤ 3 options, books after explicit yes, event in Lara's/Sina's calendar | |
| 2 | High German, preferred time taken | says time is taken, offers nearest alternatives | |
| 3 | English, "balayage as soon as possible" | 3 h slot with Lara, mentions patch test for new client | |
| 4 | French, "coupe homme samedi" when Saturday full | alternatives, then waitlist with date range | |
| 5 | Call again from the same number | greeted by name, upcoming appointment mentioned | |
| 6 | Move appointment by 30 min | moved, old event gone, no gap/duplicate | |
| 7 | Cancel tomorrow's appointment | cancelled, late-cancellation policy mentioned politely | |
| 8 | Cancel (#7) while someone is on the waitlist | `waitlist.offer` webhook fires, offer visible in `/admin`, outbound call / SMS | |
| 9 | Ask for prices, parking, TWINT | answered from knowledge base; unknown question → no invention, offers call-back | |
| 10 | Mumble / background noise / interruption mid-sentence | asks again politely, no wrong booking | |
| 11 | Name spelling "Schürmann-Nüesch" | spelled back, correct in calendar | |
| 12 | "I want to talk to Lara" during opening hours | forwards; after hours takes a message | |
| 13 | Monday / public holiday request | explains closed day, offers next day | |
| 14 | Staff adds "Lunch" in Google Calendar, then ask for that time | not offered | |
| 15 | Two phones book the same slot at the same moment | one booking, the other gets alternatives | |

Metrics tracked per test round: booking success rate, wrong bookings (target 0), average call duration for a booking (target < 2 min), tool errors from backend logs, and the share of calls needing a human.
