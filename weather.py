"""The weather for one place from Open-Meteo: what it is like now, the next
hours and the coming days. No API key. The coordinates are the only thing
sent, and only to api.open-meteo.com; a place name goes once to Open-Meteo's
geocoding service to become coordinates. A background thread fetches every
fifteen minutes and keeps the reply on disk, so the key shows right after
start and says "offline" while the network is down.

Run `python3 weather.py <place or lat,lon>` to print a forecast.
"""
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import fakes
from paths import CACHE_DIR

log = logging.getLogger("deck")

API = "https://api.open-meteo.com/v1/forecast"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
POLL_S = 15 * 60
RETRY_S = 60
DAYS = 10
UNITS = ("celsius", "fahrenheit")

# WMO weather interpretation codes, as Open-Meteo documents them: (text, glyph)
CONDITIONS = {
    0: ("Clear", "sun"), 1: ("Mostly clear", "sun"), 2: ("Partly cloudy", "sun_cloud"), 3: ("Overcast", "cloud"),
    45: ("Fog", "fog"), 48: ("Rime fog", "fog"),
    51: ("Light drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Heavy drizzle", "rain"),
    56: ("Freezing drizzle", "sleet"), 57: ("Freezing drizzle", "sleet"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "sleet"), 67: ("Freezing rain", "sleet"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"),
    80: ("Showers", "rain"), 81: ("Showers", "rain"), 82: ("Heavy showers", "rain"),
    85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "thunder"), 96: ("Thunder, hail", "thunder"), 99: ("Thunder, hail", "thunder"),
}
GLYPHS = ("sun", "moon", "sun_cloud", "moon_cloud", "cloud", "fog", "rain", "sleet", "snow", "thunder")


def condition(code, day=True):
    """(text, glyph) for a WMO code; the sun becomes the moon at night."""
    text, glyph = CONDITIONS.get(code, ("Unknown", "cloud"))
    if not day and glyph in ("sun", "sun_cloud"):
        glyph = "moon" if glyph == "sun" else "moon_cloud"
    return text, glyph


@dataclass
class Current:
    time: datetime
    temp: float
    feels: float
    code: int
    day: bool
    wind: float


@dataclass
class Hour:
    time: datetime
    temp: float
    code: int
    rain: int = 0        # chance of precipitation, percent


@dataclass
class Day:
    date: date
    code: int
    high: float
    low: float
    rain: int = 0


@dataclass
class Forecast:
    current: Current
    hours: list
    days: list
    tz: object
    unit: str = "°C"

    def next_hours(self, now, count=7):
        """The hours after the one going on."""
        return [h for h in self.hours if h.time > now][:count]

    def coming_days(self, today, count=DAYS):
        return [d for d in self.days if d.date >= today][:count]


# --- parsing -----------------------------------------------------------------

def parse_time(value, tz):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=tz)


def _num(value, default=0.0):
    return float(value) if value is not None else default


def _column(block, name, n, default=0):
    col = block.get(name)
    return list(col) if col else [default] * n


def parse_forecast(data):
    """A Forecast from Open-Meteo's reply. Times are in the place's own zone
    (the request asks for timezone=auto)."""
    try:
        tz = ZoneInfo(data.get("timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = datetime.now().astimezone().tzinfo
    cur = data.get("current") or {}
    temp = _num(cur.get("temperature_2m"))
    current = Current(parse_time(cur["time"], tz), temp, _num(cur.get("apparent_temperature"), temp),
                      int(cur.get("weather_code") or 0), bool(cur.get("is_day", 1)), _num(cur.get("wind_speed_10m")))
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    n = len(times)
    hours = [Hour(parse_time(t, tz), _num(temp), int(code or 0), int(rain or 0))
             for t, temp, code, rain in zip(times, _column(hourly, "temperature_2m", n),
                                            _column(hourly, "weather_code", n),
                                            _column(hourly, "precipitation_probability", n), strict=True)]
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    n = len(times)
    days = [Day(date.fromisoformat(t), int(code or 0), _num(hi), _num(lo), int(rain or 0))
            for t, code, hi, lo, rain in zip(times, _column(daily, "weather_code", n),
                                              _column(daily, "temperature_2m_max", n),
                                              _column(daily, "temperature_2m_min", n),
                                              _column(daily, "precipitation_probability_max", n), strict=True)]
    unit = (data.get("current_units") or {}).get("temperature_2m") or "°C"
    return Forecast(current, hours, days, tz, unit)


def shifted(fc, now):
    """The same forecast moved so that its current hour is now's hour, for
    canned data that would otherwise be in the past."""
    now = now.astimezone(fc.tz)
    delta = now.replace(minute=0, second=0, microsecond=0) - fc.current.time.replace(minute=0, second=0, microsecond=0)
    days = timedelta(days=(now.date() - fc.current.time.date()).days)
    return Forecast(replace(fc.current, time=fc.current.time + delta),
                    [replace(h, time=h.time + delta) for h in fc.hours],
                    [replace(d, date=d.date + days) for d in fc.days], fc.tz, fc.unit)


_COORDS = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*[, ]\s*([+-]?\d+(?:\.\d+)?)\s*$")


def coordinates(text):
    """(lat, lon) when the text is a pair of numbers, else None."""
    m = _COORDS.match(text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def parse_geocode(data, place):
    """(lat, lon, name) from the geocoding reply, or None when it knows no such place."""
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    return float(r["latitude"]), float(r["longitude"]), r.get("name") or place


# --- the network -------------------------------------------------------------

def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "deckmate"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch(lat, lon, units="celsius", timeout=20):
    """Open-Meteo's reply (a dict) for a place: now, hourly and daily for DAYS days."""
    canned = fakes.load_json("weather.json") if fakes.directory() else None
    if canned is not None:
        return canned
    params = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
              "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m",
              "hourly": "temperature_2m,weather_code,precipitation_probability",
              "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
              "timezone": "auto", "forecast_days": DAYS}
    if units == "fahrenheit":
        params.update(temperature_unit="fahrenheit", wind_speed_unit="mph")
    return get_json(API + "?" + urllib.parse.urlencode(params), timeout)


def geocode(place, timeout=20):
    """(lat, lon, name) for a place name, or None when the service knows no such place."""
    if fakes.directory():
        canned = fakes.load_json("weather.json") or {}
        return float(canned.get("latitude", 0)), float(canned.get("longitude", 0)), place
    query = urllib.parse.urlencode({"name": place, "count": 1, "language": "en", "format": "json"})
    return parse_geocode(get_json(GEOCODE + "?" + query, timeout), place)


class Weather:
    """One place: fetched in a background thread, cached on disk. forecast() is
    what the keys read; name is the place as the key should call it."""

    def __init__(self, place="", lat=None, lon=None, units="celsius", start=True):
        self.place = (place or "").strip()
        self.lat, self.lon = lat, lon
        if self.lat is None:
            found = coordinates(self.place)
            if found:
                self.lat, self.lon = found
        self.units = units if units in UNITS else "celsius"
        self.name = self.place or (f"{self.lat:.2f}, {self.lon:.2f}" if self.lat is not None else "")
        self.error = ""
        self.fetched_at = None
        self.loaded = False
        self._forecast = None
        self._lock = threading.Lock()
        ident = f"{self.place}|{self.lat}|{self.lon}|{self.units}"
        self.cache = os.path.join(CACHE_DIR, "weather-" + hashlib.sha1(ident.encode()).hexdigest()[:16] + ".json")
        self._load_cache()
        if start:
            threading.Thread(target=self._loop, daemon=True).start()

    @property
    def located(self):
        return self.lat is not None

    def _load_cache(self):
        try:
            with open(self.cache, encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("lat") is not None:
                self.lat, self.lon = float(saved["lat"]), float(saved["lon"])
            if saved.get("name"):
                self.name = saved["name"]
            self.set_data(saved["reply"])
            self.fetched_at = os.path.getmtime(self.cache)
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save_cache(self, reply):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = self.cache + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"lat": self.lat, "lon": self.lon, "name": self.name, "reply": reply}, f)
            os.replace(tmp, self.cache)
        except OSError as e:
            log.info(f"weather: cannot cache the forecast: {e}")

    def set_data(self, data, canned=False):
        fc = parse_forecast(data)
        if canned:
            fc = shifted(fc, datetime.now(fc.tz))
        with self._lock:
            self._forecast = fc
            self.loaded = True

    def forecast(self):
        with self._lock:
            return self._forecast

    def _locate(self):
        """True with coordinates in hand; geocodes the place name the first time."""
        if self.located:
            return True
        found = geocode(self.place)
        if found is None:
            self.error = f"weather: no place called {self.place!r}"
            return False
        self.lat, self.lon, self.name = found
        return True

    def _round(self):
        """One fetch. Returns how long to wait before the next."""
        try:
            if not self._locate():
                log.info(self.error)
                return POLL_S
            data = fetch(self.lat, self.lon, self.units)
            if "current" not in data:
                raise ValueError(data.get("reason") or "not a forecast")
            self.set_data(data, canned=bool(fakes.directory()))
            self.fetched_at = time.time()
            self.error = ""
            self._save_cache(data)
            return POLL_S
        except Exception as e:       # network down, HTTP error, odd reply
            self.error = f"weather: {e}"
            log.info(self.error)
            return RETRY_S

    def _loop(self):
        if not self.located and not self.place:
            return                       # nothing to look up; the key says "no place"
        while True:
            time.sleep(self._round())


_sources = {}


def weather_for(cfg):
    """The one Weather per place and units, shared by every key that shows it;
    None when the key names no place."""
    place = (cfg.get("place") or "").strip()
    lat, lon = cfg.get("lat"), cfg.get("lon")
    try:
        lat, lon = (float(lat), float(lon)) if lat is not None and lon is not None else (None, None)
    except (TypeError, ValueError):
        lat, lon = None, None
    units = cfg.get("units") or "celsius"
    if not place and lat is None:
        return None
    key = (place, lat, lon, units)
    if key not in _sources:
        _sources[key] = Weather(place, lat, lon, units)
    return _sources[key]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: weather.py <place or lat,lon>")
    w = Weather(sys.argv[1], start=False)
    if not w._locate():
        sys.exit(w.error)
    w.set_data(fetch(w.lat, w.lon, w.units))
    fc = w.forecast()
    now = datetime.now(fc.tz)
    c = fc.current
    print(f"{w.name} ({w.lat:.2f}, {w.lon:.2f}): {c.temp:.0f}{fc.unit}, feels {c.feels:.0f}{fc.unit}, "
          f"{condition(c.code, c.day)[0]}, wind {c.wind:.0f}")
    for h in fc.next_hours(now, 12):
        print(f"  {h.time:%H:%M}  {h.temp:4.0f}{fc.unit}  {condition(h.code)[0]:16} {h.rain:3d}% rain")
    for d in fc.coming_days(now.date()):
        print(f"  {d.date:%a %d.%m.}  {d.high:4.0f}/{d.low:.0f}{fc.unit}  {condition(d.code)[0]:16} {d.rain:3d}% rain")
