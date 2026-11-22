"""The weather key and its page: conditions and temperature on the key; on
press, a folder with the next hours and the coming ten days."""
import math
import shlex
import time
from datetime import datetime

from deckhid import KEY_COUNT, KEY_PX
from keys import Blank, Key
from pages import NoteKey, back_key, nav
from render import draw_centered_lines, fit_lines, font, tile
from system import launch
from weather import POLL_S, condition, weather_for

WEATHER_WEB = "https://www.yr.no/en/forecast/daily-table/{lat:.4f},{lon:.4f}"   # yr.no takes coordinates
HOURS = 4     # keys 1-4 of the page, next to the back key
DAYS = 10     # keys 5-14, the two lower rows
STALE_S = 4 * POLL_S

# tile colours by glyph
BG = {"sun": (40, 110, 180), "moon": (26, 30, 60), "sun_cloud": (60, 105, 150), "moon_cloud": (40, 44, 70),
      "cloud": (70, 76, 92), "fog": (84, 84, 96), "rain": (40, 72, 110), "sleet": (52, 76, 110),
      "snow": (86, 98, 130), "thunder": (60, 44, 92)}
SUN = (255, 200, 60)
MOON = (235, 235, 205)
CLOUD = (228, 230, 236)
RAIN = (120, 185, 255)
SNOW = (245, 248, 255)
BOLT = (255, 215, 70)
DIM = (190, 190, 200)
PLAIN = (40, 44, 56)


# --- glyphs ------------------------------------------------------------------

def _sun(d, cx, cy, r, color):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    w = max(1, round(r / 4))
    for k in range(8):
        a = k * math.pi / 4
        d.line([cx + math.cos(a) * r * 1.4, cy + math.sin(a) * r * 1.4,
                cx + math.cos(a) * r * 1.9, cy + math.sin(a) * r * 1.9], fill=color, width=w)


def _moon(d, cx, cy, r, color, bg, bite=(0.35, -0.55)):
    """A crescent: a disc with a bite of the background taken out, offset by bite (in radii)."""
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    bx, by = cx + r * bite[0], cy + r * bite[1]
    d.ellipse([bx - r * 0.8, by - r * 0.8, bx + r * 0.8, by + r * 0.8], fill=bg)


def _cloud(d, cx, base, w, color):
    """A cloud w wide whose flat bottom is at base."""
    d.rounded_rectangle([cx - w / 2, base - w * 0.24, cx + w / 2, base], radius=w * 0.12, fill=color)
    d.ellipse([cx - w * 0.40, base - w * 0.46, cx - w * 0.02, base - w * 0.06], fill=color)
    d.ellipse([cx - w * 0.16, base - w * 0.60, cx + w * 0.34, base - w * 0.08], fill=color)


def draw_glyph(d, glyph, x, y, s, bg, pressed=False):
    """A weather glyph in the s x s box at (x, y). Pressed keys draw it in black."""
    sun, moon, cloud, rain, snow, bolt = (SUN, MOON, CLOUD, RAIN, SNOW, BOLT) if not pressed else [(0, 0, 0)] * 6
    if pressed:
        bg = (255, 255, 255)
    if glyph == "sun":
        _sun(d, x + s / 2, y + s / 2, s * 0.26, sun)
        return
    if glyph == "moon":
        _moon(d, x + s / 2, y + s / 2, s * 0.3, moon, bg)
        return
    if glyph == "sun_cloud":
        _sun(d, x + s * 0.64, y + s * 0.34, s * 0.17, sun)
    elif glyph == "moon_cloud":
        _moon(d, x + s * 0.68, y + s * 0.3, s * 0.24, moon, bg, bite=(-0.6, 0.45))   # the crescent clears the cloud
    base = y + s * (0.86 if glyph in ("sun_cloud", "moon_cloud", "cloud") else 0.58)
    _cloud(d, x + s * (0.46 if glyph in ("sun_cloud", "moon_cloud") else 0.5), base, s * 0.84, cloud)
    w = max(1, round(s / 12))
    if glyph == "fog":
        for yy in (0.72, 0.86):
            d.line([x + s * 0.14, y + s * yy, x + s * 0.86, y + s * yy], fill=cloud, width=w)
    elif glyph in ("rain", "sleet", "snow"):
        for n, xx in enumerate((0.3, 0.5, 0.7)):
            px, py = x + s * xx, y + s * 0.7
            if glyph == "rain" or (glyph == "sleet" and n != 1):
                d.line([px, py, px - s * 0.07, py + s * 0.22], fill=rain, width=w)
            else:
                r = s * 0.06
                d.ellipse([px - r, py + s * 0.08 - r, px + r, py + s * 0.08 + r], fill=snow)
    elif glyph == "thunder":
        d.polygon([(x + s * 0.56, y + s * 0.5), (x + s * 0.38, y + s * 0.76), (x + s * 0.5, y + s * 0.76),
                   (x + s * 0.42, y + s), (x + s * 0.66, y + s * 0.68), (x + s * 0.54, y + s * 0.68),
                   (x + s * 0.64, y + s * 0.5)], fill=bolt)


def degrees(value):
    return f"{round(value):d}°"


def high_low(day):
    return f"↑{degrees(day.high)} ↓{degrees(day.low)}"


def text_right(d, text, size, top, fg, left, right=KEY_PX - 4):
    """Text centred between left and right."""
    f = font(size)
    w = d.textlength(text, font=f)
    d.text((left + (right - left - w) / 2, top), text, font=f, fill=fg)


# --- the key -----------------------------------------------------------------

class WeatherKey(Key):
    """cfg "place": a town, or "lat, lon" (or "lat" and "lon" fields); "units":
    celsius (default) or fahrenheit; "title" replaces the condition text (for
    two keys with two places). Press opens a folder with the next hours and the
    coming ten days; hold opens the forecast in the browser ("open" on the key
    replaces the address)."""
    dynamic = True
    holdable = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self.source = weather_for(cfg)

    def reason(self):
        """Why there is no forecast to show."""
        if self.source is None:
            return "no place"
        return "offline" if self.source.error else "loading"

    def stale(self, now=None):
        """True when the latest fetch failed and the data shown is old."""
        s = self.source
        return bool(s and s.error and s.fetched_at and (now or time.time()) - s.fetched_at > STALE_S)

    def render(self, pressed):
        fc = self.source.forecast() if self.source else None
        if fc is None:
            img, d, fg = tile(PLAIN, pressed)
            draw_centered_lines(d, [self.cfg.get("title") or "Weather"], fg, size=13, top=14)
            draw_centered_lines(d, [self.reason()], DIM if not pressed else fg, size=11, top=40, bold=False)
            return img
        cur = fc.current
        text, glyph = condition(cur.code, cur.day)
        bg = BG[glyph]
        img, d, fg = tile(bg, pressed)
        draw_glyph(d, glyph, 4, 4, 28, bg, pressed)
        text_right(d, degrees(cur.temp), 22, 6, fg, left=34)
        lines, size = fit_lines(self.cfg.get("title") or text, max_lines=1, sizes=(11, 10, 9))
        draw_centered_lines(d, lines, fg, size=size, top=36)
        today = fc.coming_days(cur.time.date(), 1)
        if self.stale():
            foot, color = "offline", DIM
        elif today:
            foot, color = high_low(today[0]), DIM
        else:
            foot, color = f"feels {degrees(cur.feels)}", DIM
        draw_centered_lines(d, [foot], color if not pressed else fg, size=10, top=54, bold=False)
        return img

    def press(self):
        if self.source is not None:
            nav.push(WeatherPage(self))

    def hold(self):
        url = self.cfg.get("open")
        if not url:
            s = self.source
            url = WEATHER_WEB.format(lat=s.lat, lon=s.lon) if s and s.located else "https://www.yr.no/"
        launch(f"xdg-open {shlex.quote(url)}")

    def render_held(self):
        return self.render(True)


# --- the page ----------------------------------------------------------------

class HourKey(Key):
    """One hour: the time, the glyph, the temperature, the chance of rain."""

    def __init__(self, hour, tz):
        super().__init__({})
        self.hour, self.tz = hour, tz

    def render(self, pressed):
        h = self.hour
        local = h.time.astimezone(self.tz)
        text, glyph = condition(h.code, 7 <= local.hour < 19)
        bg = BG[glyph]
        img, d, fg = tile(bg, pressed)
        draw_centered_lines(d, [f"{local:%H:%M}"], DIM if not pressed else fg, size=10, top=3, bold=False)
        draw_glyph(d, glyph, 5, 17, 26, bg, pressed)
        text_right(d, degrees(h.temp), 17, 21, fg, left=34)
        foot = f"{h.rain}% rain" if h.rain else text
        lines, size = fit_lines(foot, max_lines=1, sizes=(10, 9, 8))
        draw_centered_lines(d, lines, DIM if not pressed else fg, size=size, top=55, bold=False)
        return img


class DayKey(Key):
    """One day: its name, the glyph, high and low, the chance of rain."""

    def __init__(self, day, today):
        super().__init__({})
        self.day, self.today = day, today

    def label(self):
        delta = (self.day.date - self.today).days
        return "Today" if delta == 0 else "Tomorrow" if delta == 1 else f"{self.day.date:%a %-d.}"

    def render(self, pressed):
        day = self.day
        text, glyph = condition(day.code)
        bg = BG[glyph]
        img, d, fg = tile(bg, pressed)
        lines, size = fit_lines(self.label(), max_lines=1, sizes=(10, 9))
        draw_centered_lines(d, lines, fg, size=size, top=3)
        draw_glyph(d, glyph, 5, 17, 26, bg, pressed)
        text_right(d, degrees(day.high), 17, 21, fg, left=34)
        foot = f"↓{degrees(day.low)}" + (f"  {day.rain}%" if day.rain else "")
        draw_centered_lines(d, [foot], DIM if not pressed else fg, size=10, top=55, bold=False)
        return img


class WeatherPage:
    """Key 0 goes back, keys 1-4 are the next hours, keys 5-14 the coming ten days."""

    def __init__(self, key):
        self.key = key

    def keys(self):
        keys = [Blank({}) for _ in range(KEY_COUNT)]
        keys[0] = back_key()
        fc = self.key.source.forecast()
        if fc is None:
            keys[1] = NoteKey(self.key.reason().replace(" ", "\n"))
            return keys
        now = datetime.now(fc.tz)
        for n, hour in enumerate(fc.next_hours(now, HOURS), start=1):
            keys[n] = HourKey(hour, fc.tz)
        for n, day in enumerate(fc.coming_days(now.date(), DAYS), start=1 + HOURS):
            keys[n] = DayKey(day, now.date())
        return keys
