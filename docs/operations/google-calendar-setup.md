# Connecting Google Calendar

The salon keeps working in Google Calendar exactly as before. The assistant reads busy times and writes bookings.

1. **One calendar per stylist.** In Google Calendar → *Other calendars → + → Create new calendar*: `Lara`, `Marco`, `Sina`. (A stylist's own calendar works too.)
2. **Service account.** In Google Cloud Console: new project → enable the *Google Calendar API* → *IAM → Service accounts → Create* → *Keys → Add key → JSON*. Keep the file secret.
3. **Share each stylist calendar** with the service account's e-mail (`…@….iam.gserviceaccount.com`) with permission *Make changes to events*.
4. **Calendar IDs:** each calendar's *Settings → Integrate calendar → Calendar ID*.
5. **Configure the backend:**
   ```
   CALENDAR_BACKEND=google
   GOOGLE_SERVICE_ACCOUNT_JSON=/secrets/service-account.json   # or the JSON content itself
   GOOGLE_CALENDAR_IDS={"lara":"…@group.calendar.google.com","marco":"…","sina":"…"}
   ```
6. **Smoke test:** add a "Lunch" event 12:00–13:00 to Lara's calendar for tomorrow, then
   ```bash
   curl -s -X POST https://<backend>/tools/check_availability \
     -H "X-Api-Key: $TOOL_API_KEY" -H "Content-Type: application/json" \
     -d '{"service":"womens_cut","date":"tomorrow","stylist":"lara","preferred_time":"12:00"}'
   ```
   12:00 must *not* be offered. Book one slot with `book_appointment` and check that the event appears in Lara's calendar with the phone number in its description.

How it works: busy times come from the **FreeBusy API**, so every event blocks time, including ones staff create by hand. Bookings store phone, service and customer in *private extended properties*, which lets the assistant find a caller's appointments again without parsing titles. If Google cannot be read, the backend fails closed: it offers nothing rather than risking a double booking.

**Other calendars:** the adapter interface (`app/calendars/base.py`) has six methods. Microsoft 365 / Outlook (Graph `getSchedule` + events) or Cal.com would be another ~150-line adapter.
