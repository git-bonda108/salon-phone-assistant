from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.salon import load_salon
from app.slots import free_slots_for_day, pick_options

from .conftest import TZ

SALON = load_salon(Path(__file__).resolve().parent.parent / "data" / "salon.json")
MON_EARLY = datetime(2026, 11, 9, 8, 0, tzinfo=TZ)
TUE = date(2026, 11, 10)
SAT = date(2026, 11, 14)


def slots(service, day, busy=None, now=MON_EARLY, stylist=None, window=(time(0), time(23, 59))):
    st = [SALON.stylist(stylist)] if stylist else None
    return free_slots_for_day(SALON, SALON.service(service), day, busy or {}, now, st, window)


def at(day, hh, mm=0):
    return datetime.combine(day, time(hh, mm), TZ)


def test_closed_on_monday_and_sunday():
    assert slots("mens_cut", date(2026, 11, 9)) == []
    assert slots("mens_cut", date(2026, 11, 15)) == []


def test_service_must_finish_before_closing():
    s = slots("balayage", SAT, stylist="lara")  # Sat 08:00-16:00, 180 min
    assert s[0].start == at(SAT, 8)
    assert s[-1].start == at(SAT, 13)
    assert all(x.end <= at(SAT, 16) for x in s)


def test_only_capable_stylists():
    assert {x.stylist_id for x in slots("beard", TUE)} == {"marco"}
    assert {x.stylist_id for x in slots("balayage", TUE)} == {"lara"}


def test_stylist_working_days():
    assert slots("womens_cut", TUE, stylist="sina") == []  # Sina works Wed-Sat
    assert slots("womens_cut", TUE + timedelta(days=1), stylist="sina")


def test_busy_blocks_with_cleanup_buffer():
    busy = {"marco": [(at(TUE, 10), at(TUE, 11))]}
    starts = {x.start for x in slots("mens_cut", TUE, busy, stylist="marco")}
    assert at(TUE, 9, 15) in starts            # ends 9:45, +10 buffer = 9:55 < 10:00
    assert at(TUE, 9, 30) not in starts        # ends 10:00, no buffer left
    assert at(TUE, 11) not in starts           # inside buffer after the block
    assert at(TUE, 11, 15) in starts


def test_minimum_lead_time():
    now = at(TUE, 9, 0)
    starts = [x.start for x in slots("mens_cut", TUE, now=now, stylist="marco")]
    assert starts[0] == at(TUE, 10)  # 60 min lead


def test_time_of_day_window():
    s = slots("mens_cut", TUE, stylist="marco", window=(time(17), time(23, 59)))
    assert s and all(x.start.hour >= 17 for x in s)


def test_pick_options_spread_and_preferred():
    s = slots("mens_cut", TUE, stylist="marco")
    opts = pick_options(s)
    assert len(opts) == 3
    assert all(b.start - a.start >= timedelta(hours=1) for a, b in zip(opts, opts[1:]))
    pref = pick_options(s, preferred=time(14, 30))
    assert pref[0].start == at(TUE, 14, 30)


def test_dst_change_day_keeps_wall_clock_hours():
    # 28 March 2027 is the DST switch (Sunday, closed); check the Saturday before and Tuesday after.
    for d in (date(2027, 3, 27), date(2027, 3, 30)):
        s = slots("mens_cut", d, now=datetime(2027, 3, 1, tzinfo=TZ), stylist="marco")
        assert s[0].start.hour in (8, 9)
        assert s[0].start.utcoffset() == s[-1].start.utcoffset()
