import os
import sys
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calendarkeys  # noqa: E402
from calfeed import Occurrence, next_meeting, occurrences, parse_duration, parse_ics, split_line, unfold  # noqa: E402

TZ = ZoneInfo("Europe/Helsinki")
MON = date(2026, 11, 16)


def event(**kw):
    """A VEVENT block from properties, DTSTART/DTEND as raw lines."""
    lines = ["BEGIN:VEVENT", f"UID:{kw.pop('uid', 'u1')}"]
    for k, v in kw.items():
        lines.append(f"{k.replace('_', '-')}:{v}" if not v.startswith(";") else f"{k.replace('_', '-')}{v}")
    lines.append("END:VEVENT")
    return "\n".join(lines)


def feed(*events):
    return "BEGIN:VCALENDAR\nVERSION:2.0\n" + "\n".join(events) + "\nEND:VCALENDAR\n"


def at(day, hh, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)


class LineTest(unittest.TestCase):
    def test_unfold_and_split(self):
        lines = unfold("SUMMARY:Long\r\n  title\r\nDTSTART;TZID=Europe/Helsinki:20261116T100000\r\n")
        self.assertEqual(lines[0], "SUMMARY:Long title")
        self.assertEqual(split_line(lines[1]), ("DTSTART", {"TZID": "Europe/Helsinki"}, "20261116T100000"))
        self.assertEqual(split_line('X;A="q:1";B=2:v:w'), ("X", {"A": "q:1", "B": "2"}, "v:w"))

    def test_duration(self):
        self.assertEqual(parse_duration("PT1H30M"), timedelta(hours=1, minutes=30))
        self.assertEqual(parse_duration("P1DT2H"), timedelta(days=1, hours=2))
        self.assertEqual(parse_duration("-PT15M"), timedelta(minutes=-15))
        self.assertEqual(parse_duration("junk"), timedelta())


class ParseTest(unittest.TestCase):
    def test_timed_all_day_utc_and_floating(self):
        evs = parse_ics(feed(
            event(uid="a", DTSTART=";TZID=Europe/Helsinki:20261116T100000",
                  DTEND=";TZID=Europe/Helsinki:20261116T103000", SUMMARY="Stand-up\\, daily", LOCATION="Room 1"),
            event(uid="b", DTSTART=";VALUE=DATE:20261116", DTEND=";VALUE=DATE:20261117", SUMMARY="Away"),
            event(uid="c", DTSTART="20261116T080000Z", DURATION="PT45M", SUMMARY="UTC"),
            event(uid="d", DTSTART="20261116T150000", SUMMARY="floating, no end"),
        ), TZ)
        by = {e.uid: e for e in evs}
        self.assertEqual(by["a"].start, at(MON, 10))
        self.assertEqual(by["a"].summary, "Stand-up, daily")
        self.assertEqual(by["a"].location, "Room 1")
        self.assertTrue(by["b"].all_day)
        self.assertEqual(by["b"].end - by["b"].start, timedelta(days=1))
        self.assertEqual(by["c"].start, at(MON, 10))              # 08:00Z is 10:00 in Helsinki
        self.assertEqual(by["c"].end, at(MON, 10, 45))
        self.assertEqual(by["d"].start, at(MON, 15))
        self.assertEqual(by["d"].end, by["d"].start)

    def test_meeting_link(self):
        evs = parse_ics(feed(
            event(uid="g", DTSTART="20261116T100000", X_GOOGLE_CONFERENCE="https://meet.google.com/abc-defg-hij",
                  DESCRIPTION="Join: https://example.com/other"),
            event(uid="z", DTSTART="20261116T110000",
                  DESCRIPTION="Zoom link\\nhttps://us02web.zoom.us/j/123?pwd=x\\nSee you"),
            event(uid="u", DTSTART="20261116T120000", URL="https://example.com/event"),
            event(uid="n", DTSTART="20261116T130000", DESCRIPTION="https://example.com/notes"),
        ), TZ)
        by = {e.uid: e for e in evs}
        self.assertEqual(by["g"].url, "https://meet.google.com/abc-defg-hij")
        self.assertEqual(by["z"].url, "https://us02web.zoom.us/j/123?pwd=x")
        self.assertEqual(by["u"].url, "https://example.com/event")
        self.assertEqual(by["n"].url, "")

    def test_cancelled_and_unknown_zone(self):
        evs = parse_ics(feed(
            event(uid="x", DTSTART=";TZID=Nowhere/Land:20261116T100000", STATUS="CANCELLED"),
        ), TZ)
        self.assertTrue(evs[0].cancelled)
        self.assertEqual(evs[0].start, at(MON, 10))    # unknown zone: local


class RecurrenceTest(unittest.TestCase):
    def occ(self, day, *events):
        return occurrences(parse_ics(feed(*events), TZ), day, TZ)

    def test_weekly_byday(self):
        weekly = event(uid="w", DTSTART=";TZID=Europe/Helsinki:20260105T090000",
                       DTEND=";TZID=Europe/Helsinki:20260105T091500",
                       RRULE="FREQ=WEEKLY;BYDAY=MO,WE,FR", SUMMARY="Stand-up")
        self.assertEqual([o.start for o in self.occ(MON, weekly)], [at(MON, 9)])
        self.assertEqual(self.occ(MON + timedelta(days=1), weekly), [])
        wed = MON + timedelta(days=2)
        self.assertEqual([o.end for o in self.occ(wed, weekly)], [at(wed, 9, 15)])

    def test_weekly_interval_keeps_dst_wall_time(self):
        # every second Monday from a summer-time date: still 09:00 wall clock in November
        ev = event(uid="w", DTSTART=";TZID=Europe/Helsinki:20260824T090000", DURATION="PT1H",
                   RRULE="FREQ=WEEKLY;INTERVAL=2")
        self.assertEqual([o.start for o in self.occ(MON, ev)], [at(MON, 9)])            # 12 weeks later
        self.assertEqual(self.occ(MON - timedelta(days=7), ev), [])                     # 11 weeks: off week
        self.assertEqual(self.occ(MON + timedelta(days=7), ev), [])

    def test_daily_count_until_exdate(self):
        counted = event(uid="c", DTSTART="20261114T100000", RRULE="FREQ=DAILY;COUNT=3")
        self.assertEqual(len(self.occ(MON, counted)), 1)                       # 14th, 15th, 16th
        self.assertEqual(self.occ(MON + timedelta(days=1), counted), [])
        until = event(uid="u", DTSTART="20261110T100000", RRULE="FREQ=DAILY;UNTIL=20261115T235959Z")
        self.assertEqual(self.occ(MON, until), [])
        until_date = event(uid="d", DTSTART=";VALUE=DATE:20261110", RRULE="FREQ=DAILY;UNTIL=20261116")
        self.assertEqual(len(self.occ(MON, until_date)), 1)                    # UNTIL as a date includes that day
        skipped = event(uid="e", DTSTART="20261110T100000", RRULE="FREQ=DAILY",
                        EXDATE=";TZID=Europe/Helsinki:20261116T100000")
        self.assertEqual(self.occ(MON, skipped), [])
        self.assertEqual(len(self.occ(MON + timedelta(days=1), skipped)), 1)

    def test_edited_instance_replaces_the_rule(self):
        master = event(uid="m", DTSTART=";TZID=Europe/Helsinki:20261102T100000", DURATION="PT30M",
                       RRULE="FREQ=WEEKLY", SUMMARY="1:1")
        moved = event(uid="m", RECURRENCE_ID=";TZID=Europe/Helsinki:20261116T100000",
                      DTSTART=";TZID=Europe/Helsinki:20261116T140000", DURATION="PT30M", SUMMARY="1:1 (moved)")
        occs = self.occ(MON, master, moved)
        self.assertEqual([(o.start, o.summary) for o in occs], [(at(MON, 14), "1:1 (moved)")])
        gone = event(uid="m", RECURRENCE_ID=";TZID=Europe/Helsinki:20261116T100000",
                     DTSTART=";TZID=Europe/Helsinki:20261116T100000", STATUS="CANCELLED")
        self.assertEqual(self.occ(MON, master, gone), [])

    def test_monthly_and_yearly(self):
        third_mon = event(uid="a", DTSTART="20260119T120000", RRULE="FREQ=MONTHLY;BYDAY=3MO")
        self.assertEqual(len(self.occ(MON, third_mon)), 1)                     # 16 Nov 2026 is the third Monday
        self.assertEqual(self.occ(MON + timedelta(days=7), third_mon), [])
        last_fri = event(uid="b", DTSTART="20260130T120000", RRULE="FREQ=MONTHLY;BYDAY=-1FR")
        self.assertEqual(len(self.occ(date(2026, 11, 27), last_fri)), 1)
        self.assertEqual(self.occ(date(2026, 11, 20), last_fri), [])
        day16 = event(uid="c", DTSTART="20260116T120000", RRULE="FREQ=MONTHLY;INTERVAL=2")
        self.assertEqual(len(self.occ(MON, day16)), 1)                         # Jan, Mar, ..., Nov
        self.assertEqual(self.occ(date(2026, 10, 16), day16), [])
        birthday = event(uid="d", DTSTART=";VALUE=DATE:19901116", RRULE="FREQ=YEARLY", SUMMARY="Birthday")
        occs = self.occ(MON, birthday)
        self.assertEqual([o.summary for o in occs], ["Birthday"])
        self.assertTrue(occs[0].all_day)

    def test_first_instance_is_dtstart_even_off_rule(self):
        ev = event(uid="o", DTSTART="20261116T100000", RRULE="FREQ=WEEKLY;BYDAY=TU")
        self.assertEqual(len(self.occ(MON, ev)), 1)
        self.assertEqual(len(self.occ(MON + timedelta(days=1), ev)), 1)

    def test_day_order_and_multi_day(self):
        occs = self.occ(
            MON,
            event(uid="1", DTSTART="20261116T140000", DTEND="20261116T150000", SUMMARY="late"),
            event(uid="2", DTSTART="20261116T090000", DTEND="20261116T093000", SUMMARY="early"),
            event(uid="3", DTSTART=";VALUE=DATE:20261114", DTEND=";VALUE=DATE:20261118", SUMMARY="trip"),
            event(uid="4", DTSTART="20261115T230000", DTEND="20261116T010000", SUMMARY="overnight"),
            event(uid="5", DTSTART="20261117T090000", SUMMARY="tomorrow"),
        )
        self.assertEqual([o.summary for o in occs], ["trip", "overnight", "early", "late"])


class NextMeetingTest(unittest.TestCase):
    def occs(self):
        return [
            Occurrence(at(MON, 0), at(MON, 0) + timedelta(days=1), "Away", all_day=True),
            Occurrence(at(MON, 9), at(MON, 9, 30), "Stand-up"),
            Occurrence(at(MON, 10), at(MON, 11), "Planning", url="https://meet.google.com/x"),
            Occurrence(at(MON, 10, 30), at(MON, 10, 45), "Quick call"),
        ]

    def test_states(self):
        warn = 5 * 60
        self.assertEqual(next_meeting(self.occs(), at(MON, 8), warn), (self.occs()[1], "later"))
        self.assertEqual(next_meeting(self.occs(), at(MON, 9, 56), warn)[1], "soon")
        self.assertEqual(next_meeting(self.occs(), at(MON, 9, 10), warn)[0].summary, "Stand-up")
        self.assertEqual(next_meeting(self.occs(), at(MON, 9, 10), warn)[1], "now")
        # two going on: the one ending first
        self.assertEqual(next_meeting(self.occs(), at(MON, 10, 40), warn)[0].summary, "Quick call")
        self.assertEqual(next_meeting(self.occs(), at(MON, 12), warn), (None, ""))
        self.assertEqual(next_meeting([self.occs()[0]], at(MON, 8), warn), (None, ""))   # all-day is not a meeting


class FakeFeed:
    tz = TZ
    loaded = True
    error = ""

    def __init__(self, occs):
        self.occs = occs

    def today(self, now=None):
        return self.occs


class KeyTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        calendarkeys.launch = self.calls.append

    def key(self, occs, **cfg):
        k = calendarkeys.Calendar({"url": "https://example.com/x.ics", **cfg})
        k.feed = FakeFeed(occs)
        return k

    def test_shown_text_without_meetings(self):
        self.assertEqual(calendarkeys.Calendar({}).shown(at(MON, 8)), (None, "no feed"))
        self.assertEqual(self.key([]).shown(at(MON, 8)), (None, "free today"))
        k = self.key([Occurrence(at(MON, 9), at(MON, 10), "done")])
        self.assertEqual(k.shown(at(MON, 12)), (None, "no more today"))
        k.feed.loaded, k.feed.error = False, "calendar: down"
        self.assertEqual(k.shown(at(MON, 12)), (None, "offline"))

    def test_press_joins_only_when_on_or_soon(self):
        meet = Occurrence(at(MON, 10), at(MON, 11), "Planning", url="https://meet.google.com/x")
        k = self.key([meet])
        calendarkeys.datetime = FakeNow(at(MON, 8))
        try:
            k.press()
            self.assertIn("calendar.google.com", self.calls[-1])
            calendarkeys.datetime = FakeNow(at(MON, 9, 57))
            k.press()
            self.assertIn("meet.google.com/x", self.calls[-1])
        finally:
            calendarkeys.datetime = datetime

    def test_renders(self):
        k = self.key([Occurrence(at(MON, 10), at(MON, 11), "A long planning meeting title")])
        img = k.render(False)
        self.assertEqual(img.size, (calendarkeys.KEY_PX, calendarkeys.KEY_PX))
        page = calendarkeys.CalendarPage(k)
        keys = page.keys()
        self.assertEqual(type(keys[1]).__name__, "EventKey")
        keys[1].render(False)
        keys[1].press()
        self.assertIn("calendar.google.com", self.calls[-1])
        self.assertEqual(type(calendarkeys.CalendarPage(self.key([])).keys()[1]).__name__, "NoteKey")


class FakeNow:
    def __init__(self, now):
        self._now = now

    def now(self, tz=None):
        return self._now.astimezone(tz) if tz else self._now


if __name__ == "__main__":
    unittest.main()
