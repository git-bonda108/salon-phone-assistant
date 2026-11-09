# 0002 · Bookings are decided by deterministic code; the calendar is the source of truth

**Context.** A wrong booking (wrong day, double booking, a 3-hour service in a 1-hour gap) is the most expensive failure a salon assistant can make. LLMs are good at conversation and bad at guaranteeing arithmetic on calendars.

**Decision.** Availability, booking, moving, cancelling and waitlist matching are plain, tested Python ([`app/slots.py`](../../app/slots.py), [`app/booking.py`](../../app/booking.py)). The language model only chooses *which* tool to call and *how to say* the result. Google Calendar (or Microsoft 365) holds every appointment; staff keep using it as before, and anything they enter blocks time.

**Consequences.** Correctness is provable in milliseconds with unit tests. Tools return at most three options and a speakable `message`, so the model has little room to improvise. The trade-off is that new business rules need code, not just prompt changes.

**Rejected.** Letting the model read free/busy data and pick slots itself: untestable, and it fails silently.
