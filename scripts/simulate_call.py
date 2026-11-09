"""Replay realistic phone calls against the backend — exactly the tool calls the
Suisse Line agent makes — and print them as a readable call log.

    python scripts/simulate_call.py            # all scenarios
    python scripts/simulate_call.py waitlist   # one scenario

Runs fully in memory with a fixed clock (Monday 9 Nov 2026, 10:00), so the
output is deterministic and safe to show in a live demo.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.calendars.base import NewAppointment  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

logging.disable(logging.INFO)  # keep the demo output clean
TZ = ZoneInfo("Europe/Zurich")
DIM, BOLD, CYAN, GREEN, MAG, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[32m", "\033[35m", "\033[0m"
if not sys.stdout.isatty():
    DIM = BOLD = CYAN = GREEN = MAG = RESET = ""


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)

    def __call__(self):
        return self.now


clock = Clock()
client = TestClient(create_app(Settings(database_path=":memory:", sweep_interval_seconds=0), clock=clock))
svc = client.app.state.svc


def header(title):
    print(f"\n{BOLD}{'═' * 72}\n  {title}\n{'═' * 72}{RESET}")


def caller(text):
    print(f"{CYAN}Caller:{RESET} {text}")


def lina(text):
    print(f"{MAG}Lina:{RESET}   {text}")


def call(name, **args):
    r = client.post(f"/tools/{name}", json=args).json()
    shown = {k: v for k, v in args.items() if v}
    print(f"{DIM}        ↳ {name}({json.dumps(shown, ensure_ascii=False)}){RESET}")
    print(f"{GREEN}        ↲ {r['message']}{RESET}")
    return r


def scenario_booking():
    header("Call 1 — new customer books a women's cut (Swiss German)")
    lina("Grüezi, Salon Edelweiss, da isch d Lina. Wie chan ich Ihne hälfe?")
    call("lookup_caller", phone="+41791234567")
    caller("Grüezi, ich hett gern en Termin zum Haar schniide, morn am Namittag, öppe am zwei.")
    lina("Gern! Isch das en Damehaarschnitt mit Föhne?")
    caller("Ja genau.")
    r = call("check_availability", service="womens_cut", date="tomorrow", preferred_time="14:00")
    o = r["options"][0]
    lina(f"Morn am zwei bi de {o['stylist_name']} wär frei. Passt Ihne das?")
    caller("Super, ja.")
    lina("Uf welle Name dörf ich das iitrage?")
    caller("Anna Meier.")
    lina("Und d Nummer, wo Sie grad aalüüted, null sibe nüün – eis zwei drü – vier füf – sächs sibe, isch die guet?")
    caller("Ja.")
    lina(f"Also: Damehaarschnitt morn am zwei bi de {o['stylist_name']}, uf Anna Meier. Söll ich das so buche?")
    caller("Ja, gern.")
    call("book_appointment", service="womens_cut", start=o["start"], stylist=o["stylist"],
         customer_name="Anna Meier", phone="+41791234567")
    lina("Perfekt, isch iitreit. Es choschtet 89 Franke. Bitte chömed Sie 5 Minute früener. Merci und adieu!")


def scenario_returning():
    header("Call 2 — returning customer is recognised and moves her appointment (English)")
    r = call("lookup_caller", phone="+41791234567")
    appt = r["upcoming"][0]
    lina(f"Grüezi Frau Meier! How can I help you?")
    caller("Hi, can I move my appointment tomorrow to half past three?")
    call("check_availability", service="womens_cut", date="tomorrow", stylist="lara", preferred_time="15:30")
    lina("Yes, 15:30 with Lara is free. Shall I move it?")
    caller("Yes please.")
    call("reschedule_appointment", phone="+41791234567", appointment_id=appt["appointment_id"],
         new_start=appt["start"][:11] + "15:30")
    lina("Done — you're now booked tomorrow at 15:30 with Lara. See you then!")


def scenario_waitlist():
    header("Call 3 — Saturday is full → waitlist → cancellation → automatic offer (own feature)")
    sat = datetime(2026, 11, 14, tzinfo=TZ)
    # Marco is fully booked on Saturday except Ben's 11:00 appointment.
    for a, b in (((8, 0), (10, 50)), ((11, 40), (16, 0))):
        svc.calendar.create(NewAppointment("marco", "block", sat.replace(hour=a[0], minute=a[1]),
                                           sat.replace(hour=b[0], minute=b[1]), "Booked", "+41000000000"))
    ben = client.post("/tools/book_appointment", json=dict(
        service="mens_cut", start="2026-11-14T11:00", stylist="marco", customer_name="Ben Keller",
        phone="+41785556677")).json()

    call("lookup_caller", phone="+41764443322")
    caller("Hoi, hetted Sie am Samschtig no öppis frei für en Herrehaarschnitt bim Marco?")
    call("check_availability", service="mens_cut", date="2026-11-14", stylist="marco")
    lina("De Samschtig isch leider uusbuechet. Am Zischtig hetts no Platz – oder söll ich Sie uf d Warteliste neh?")
    caller("Warteliste, bitte. Nur de Samschtig.")
    call("join_waitlist", phone="+41764443322", customer_name="Carla Rossi", service="mens_cut",
         date_from="2026-11-14", date_to="2026-11-14", stylist="marco")
    lina("Erledigt. Sobald am Samschtig öppis frei wird, lüüted mir Ihne aa. Adieu!")

    clock.now += timedelta(days=2)
    header("Two days later — Ben cancels his Saturday 11:00")
    call("lookup_caller", phone="+41785556677")
    caller("I have to cancel Saturday, sorry.")
    call("cancel_appointment", phone="+41785556677", appointment_id=ben["appointment"]["appointment_id"])
    lina("No problem, it's cancelled. Thanks for letting us know!")

    out = [json.loads(o["payload"]) for o in svc.store.outbox() if o["kind"] == "waitlist.offer"][0]
    print(f"\n{BOLD}  → webhook 'waitlist.offer' fired:{RESET} {DIM}{json.dumps(out, ensure_ascii=False)}{RESET}")
    header("Outbound call by the offer agent to Carla (slot held for 20 minutes)")
    lina("Grüezi Frau Rossi, da isch d Lina vom Salon Edelweiss. Sie sind uf de Warteliste – am Samschtig am "
         "elfi bim Marco isch grad öppis frei worde. Wänd Sie dä Termin?")
    caller("Ja, super!")
    call("accept_offer", phone="+41764443322", offer_id=out["offer_id"])
    lina("Wunderbar, isch buechet. Bis am Samschtig!")
    print(f"\n{BOLD}  Result:{RESET} the cancelled slot was re-booked within minutes instead of staying empty.")


SCENARIOS = {"booking": scenario_booking, "returning": scenario_returning, "waitlist": scenario_waitlist}

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(SCENARIOS)
    if "returning" in wanted and "booking" not in wanted:
        wanted.insert(0, "booking")
    for name in wanted:
        SCENARIOS[name]()
    print()
