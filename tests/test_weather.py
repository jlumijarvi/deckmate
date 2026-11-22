import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import weather  # noqa: E402
import weatherkeys  # noqa: E402
from deckhid import KEY_COUNT, KEY_PX  # noqa: E402
from pages import nav  # noqa: E402
from render import tile  # noqa: E402
from weather import (Day, Weather, condition, coordinates, parse_forecast, parse_geocode, shifted,  # noqa: E402
                     weather_for)

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "weather.json")
TZ = ZoneInfo("Europe/Helsinki")


def reply():
    with open(FIXTURE) as f:
        return json.load(f)


def stub_network(test, found=(60.17, 24.94, "Helsinki"), fail=False):
    """No network and no shared cache in the tests."""
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)   # a source's thread may still be writing its cache
    test.addCleanup(tmp.cleanup)
    saved = (weather.fetch, weather.geocode, weather.CACHE_DIR, dict(weather._sources))

    def fetch(lat, lon, units="celsius", timeout=20):
        if fail:
            raise OSError("no network")
        return reply()
    weather.fetch = fetch
    weather.geocode = lambda place, timeout=20: found
    weather.CACHE_DIR = tmp.name
    weather._sources.clear()

    def restore():
        weather.fetch, weather.geocode, weather.CACHE_DIR = saved[:3]
        weather._sources.clear()
        weather._sources.update(saved[3])
    test.addCleanup(restore)
    return tmp.name


class ConditionTest(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(condition(0), ("Clear", "sun"))
        self.assertEqual(condition(0, day=False), ("Clear", "moon"))
        self.assertEqual(condition(2, day=False)[1], "moon_cloud")
        self.assertEqual(condition(3, day=False)[1], "cloud")
        self.assertEqual(condition(95)[1], "thunder")
        self.assertEqual(condition(1234), ("Unknown", "cloud"))
        for text, glyph in weather.CONDITIONS.values():
            self.assertIn(glyph, weather.GLYPHS, text)

    def test_coordinates(self):
        self.assertEqual(coordinates("60.17, 24.94"), (60.17, 24.94))
        self.assertEqual(coordinates("60.17 24.94"), (60.17, 24.94))
        self.assertEqual(coordinates("-33.9,151.2"), (-33.9, 151.2))
        for bad in ("Helsinki", "91, 0", "0, 181", "", None):
            self.assertIsNone(coordinates(bad), bad)

    def test_geocode_reply(self):
        data = {"results": [{"latitude": "60.1", "longitude": 24.9, "name": "Helsinki", "country": "Finland"}]}
        self.assertEqual(parse_geocode(data, "helsinki"), (60.1, 24.9, "Helsinki"))
        self.assertIsNone(parse_geocode({"generationtime_ms": 0.1}, "nowhere"))
        self.assertEqual(parse_geocode({"results": [{"latitude": 1, "longitude": 2}]}, "x"), (1.0, 2.0, "x"))


class ParseTest(unittest.TestCase):
    def test_fixture(self):
        fc = parse_forecast(reply())
        self.assertEqual(fc.tz.key, "Europe/Helsinki")
        self.assertEqual(fc.current.time, datetime(2026, 11, 22, 22, 0, tzinfo=TZ))
        self.assertEqual((fc.current.temp, fc.current.feels, fc.current.day), (1.6, -3.2, False))
        self.assertEqual(len(fc.hours), 240)
        self.assertEqual(fc.hours[0].time, datetime(2026, 11, 22, 0, 0, tzinfo=TZ))
        self.assertEqual(len(fc.days), 10)
        self.assertEqual(fc.days[0].date, date(2026, 11, 22))
        self.assertEqual((fc.days[0].high, fc.days[0].low, fc.days[0].rain), (2.3, 0.6, 80))
        self.assertEqual(fc.unit, "°C")

    def test_next_hours_and_coming_days(self):
        fc = parse_forecast(reply())
        now = datetime(2026, 11, 22, 21, 30, tzinfo=TZ)
        self.assertEqual([h.time.hour for h in fc.next_hours(now, 3)], [22, 23, 0])
        self.assertEqual(fc.next_hours(now, 3)[2].time.date(), date(2026, 11, 23))
        self.assertEqual([h.time.hour for h in fc.next_hours(fc.current.time, 2)], [23, 0])
        self.assertEqual([d.date.day for d in fc.coming_days(date(2026, 11, 24), 2)], [24, 25])
        self.assertEqual(len(fc.coming_days(date(2026, 11, 22))), 10)
        self.assertEqual(fc.coming_days(date(2027, 1, 1)), [])

    def test_missing_columns_and_zone(self):
        fc = parse_forecast({"timezone": "Mars/Olympus", "current": {"time": "2026-01-01T00:00"},
                             "hourly": {"time": ["2026-01-01T00:00"]}, "daily": {}})
        self.assertEqual((fc.current.temp, fc.current.feels, fc.current.day), (0.0, 0.0, True))
        self.assertEqual((fc.hours[0].temp, fc.hours[0].rain), (0.0, 0))
        self.assertEqual(fc.days, [])

    def test_shifted_moves_the_current_hour_to_now(self):
        fc = parse_forecast(reply())
        now = datetime(2027, 3, 5, 8, 45, tzinfo=TZ)
        s = shifted(fc, now)
        self.assertEqual(s.current.time, datetime(2027, 3, 5, 8, 0, tzinfo=TZ))
        self.assertEqual(s.hours[0].time, datetime(2027, 3, 4, 10, 0, tzinfo=TZ))
        self.assertEqual(s.days[0].date, date(2027, 3, 5))
        self.assertEqual([h.time.hour for h in s.next_hours(now, 2)], [9, 10])
        self.assertEqual(fc.current.time.year, 2026)      # the original is untouched


class SourceTest(unittest.TestCase):
    def test_coordinates_in_the_place(self):
        stub_network(self)
        w = Weather("60.17, 24.94", start=False)
        self.assertTrue(w.located)
        self.assertEqual((w.lat, w.lon, w.name), (60.17, 24.94, "60.17, 24.94"))
        self.assertEqual(Weather("", 1.5, 2.5, start=False).name, "1.50, 2.50")
        self.assertFalse(Weather("", start=False).located)

    def test_a_round_fetches_and_caches(self):
        tmp = stub_network(self)
        w = Weather("Helsinki", units="fahrenheit", start=False)
        self.assertFalse(w.located)
        self.assertEqual(w._round(), weather.POLL_S)
        self.assertEqual((w.lat, w.lon, w.name), (60.17, 24.94, "Helsinki"))
        self.assertEqual(w.forecast().current.temp, 1.6)
        self.assertTrue(w.fetched_at and not w.error)
        self.assertTrue(os.path.isfile(w.cache) and w.cache.startswith(tmp))
        again = Weather("Helsinki", units="fahrenheit", start=False)   # a restart: loaded from the cache
        self.assertTrue(again.loaded and again.located)
        self.assertEqual((again.lat, again.name), (60.17, "Helsinki"))
        self.assertEqual(again.forecast().days[1].date, date(2026, 11, 23))

    def test_failures(self):
        stub_network(self, fail=True)
        w = Weather("60.17, 24.94", start=False)
        self.assertEqual(w._round(), weather.RETRY_S)
        self.assertEqual(w.error, "weather: no network")
        self.assertIsNone(w.forecast())
        stub_network(self, found=None)
        w = Weather("Atlantis", start=False)
        self.assertEqual(w._round(), weather.POLL_S)
        self.assertIn("no place called 'Atlantis'", w.error)

    def test_weather_for_shares_a_source_per_place(self):
        stub_network(self)
        self.assertIsNone(weather_for({}))
        self.assertIsNone(weather_for({"place": " ", "lat": "x"}))
        a = weather_for({"place": "Oulu"})
        self.assertIs(weather_for({"place": "Oulu", "units": "celsius"}), a)
        self.assertIsNot(weather_for({"place": "Oulu", "units": "fahrenheit"}), a)
        b = weather_for({"lat": "60.17", "lon": 24.94})
        self.assertTrue(b.located)
        for _ in range(100):
            if os.path.isfile(a.cache) and os.path.isfile(b.cache):
                break
            time.sleep(0.02)
        self.assertTrue(a.loaded and b.loaded)


def register(cfg, w):
    """Register a Weather under the key weather_for() derives from cfg, so a key finds it."""
    weather._sources[(cfg.get("place", ""), cfg.get("lat"), cfg.get("lon"), "celsius")] = w
    return w


def loaded_source(cfg, canned=True):
    """A Weather for cfg with the fixture in it, registered so that a key finds it."""
    w = Weather(cfg.get("place", ""), cfg.get("lat"), cfg.get("lon"), start=False)
    w.set_data(reply(), canned=canned)
    w.fetched_at = time.time()
    return register(cfg, w)


class KeyTest(unittest.TestCase):
    def setUp(self):
        stub_network(self)
        self.opened = []
        self._launch = weatherkeys.launch
        weatherkeys.launch = self.opened.append
        self.addCleanup(setattr, weatherkeys, "launch", self._launch)

    def render(self, key):
        for pressed in (False, True):
            self.assertEqual(key.render(pressed).size, (KEY_PX, KEY_PX))

    def test_renders_without_a_place_and_before_data(self):
        key = weatherkeys.WeatherKey({})
        self.assertEqual(key.reason(), "no place")
        self.render(key)
        w = register({"place": "60.17, 24.94"}, Weather("60.17, 24.94", start=False))
        key = weatherkeys.WeatherKey({"place": "60.17, 24.94"})
        self.assertIs(key.source, w)
        self.assertEqual(key.reason(), "loading")
        self.render(key)
        w.error = "weather: no network"
        self.assertEqual(key.reason(), "offline")
        self.assertFalse(key.stale())          # nothing fetched yet: nothing to be stale
        self.render(key)

    def test_renders_with_data_and_when_stale(self):
        w = loaded_source({"place": "Helsinki"})
        key = weatherkeys.WeatherKey({"place": "Helsinki", "title": "Home"})
        self.assertIs(key.source, w)
        self.render(key)
        self.assertEqual(key.render_held().size, (KEY_PX, KEY_PX))
        w.error = "weather: HTTP 500"
        self.assertFalse(key.stale())
        w.fetched_at -= weatherkeys.STALE_S + 1
        self.assertTrue(key.stale())
        self.render(key)

    def test_hold_opens_the_forecast_in_the_browser(self):
        w = loaded_source({"place": "60.17, 24.94"})
        key = weatherkeys.WeatherKey({"place": "60.17, 24.94"})
        self.assertIs(key.source, w)
        key.hold()
        weatherkeys.WeatherKey({"place": "60.17, 24.94", "open": "https://example.com/x y"}).hold()
        weatherkeys.WeatherKey({}).hold()
        self.assertEqual(self.opened, ["xdg-open https://www.yr.no/en/forecast/daily-table/60.1700,24.9400",
                                       "xdg-open 'https://example.com/x y'", "xdg-open https://www.yr.no/"])

    def test_press_opens_the_page(self):
        loaded_source({"place": "Helsinki"})
        key = weatherkeys.WeatherKey({"place": "Helsinki"})
        nav.reset([key])
        key.press()
        keys = nav.keys()
        self.assertEqual(len(keys), KEY_COUNT)
        self.assertEqual(type(keys[0]).__name__, "BackKey")
        self.assertEqual([type(k).__name__ for k in keys[1:5]], ["HourKey"] * 4)
        self.assertEqual([type(k).__name__ for k in keys[5:]], ["DayKey"] * 10)
        self.assertEqual([k.label() for k in keys[5:7]], ["Today", "Tomorrow"])
        now = datetime.now(TZ)
        self.assertGreater(keys[1].hour.time, now)
        self.assertLess((keys[1].hour.time - now).total_seconds(), 3600)
        for k in keys:
            self.render(k)
        nav.pop()
        weatherkeys.WeatherKey({}).press()                                 # no source: nothing happens
        self.assertEqual(nav.stack, [])
        self.assertEqual(self.opened, [])

    def test_page_without_a_forecast_says_why(self):
        w = register({"place": "60.17, 24.94"}, Weather("60.17, 24.94", start=False))
        w.error = "weather: no network"
        keys = weatherkeys.WeatherPage(weatherkeys.WeatherKey({"place": "60.17, 24.94"})).keys()
        self.assertEqual(type(keys[1]).__name__, "NoteKey")
        self.assertEqual(keys[1].text, "offline")
        self.render(keys[1])

    def test_day_labels_and_degrees(self):
        day = Day(date(2026, 11, 24), 0, 1.6, -0.4)
        self.assertEqual(weatherkeys.DayKey(day, date(2026, 11, 22)).label(), "Tue 24.")
        self.assertEqual(weatherkeys.DayKey(day, date(2026, 11, 24)).label(), "Today")
        self.assertEqual(weatherkeys.high_low(day), "↑2° ↓0°")
        self.assertEqual(weatherkeys.degrees(-3.5), "-4°")

    def test_every_glyph_draws(self):
        for glyph in weather.GLYPHS:
            for pressed in (False, True):
                img, d, fg = tile(weatherkeys.BG[glyph], pressed)
                weatherkeys.draw_glyph(d, glyph, 4, 4, 28, weatherkeys.BG[glyph], pressed)
                self.assertEqual(img.size, (KEY_PX, KEY_PX))


if __name__ == "__main__":
    unittest.main()
