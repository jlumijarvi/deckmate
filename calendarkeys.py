"""The calendar key and its page: clock and date with the next meeting on the
key, today's meetings as a folder on hold."""
import logging
import shlex
from datetime import datetime

from calfeed import feed, next_meeting
from deckhid import KEY_COUNT, KEY_PX
from keys import Blank, Key
from pages import NavKey, NoteKey, back_key, nav
from render import dim, draw_centered_lines, fit_lines, font, tile
from system import launch

log = logging.getLogger("deck")

DEFAULT_WARN_MIN = 5
CALENDAR_WEB = "https://calendar.google.com/"

STATE_COLORS = {
    "past": (90, 90, 100),
    "now": (52, 160, 90),
    "soon": (220, 60, 60),
    "later": (52, 140, 235),
    "all_day": (150, 90, 200),
}
CLOCK_BG = (20, 20, 30)


def time_text(occ, tz):
    if occ.all_day:
        return "all day"
    return f"{occ.start.astimezone(tz):%H:%M}-{occ.end.astimezone(tz):%H:%M}"


def open_link(occ, fallback=CALENDAR_WEB):
    launch(f"xdg-open {shlex.quote(occ.url if occ and occ.url else fallback)}")


class Calendar(Key):
    """cfg "url": the iCalendar feed; "warn_min": minutes before a meeting when
    the key turns red (default 5). Press opens the meeting's link when one is
    on or about to start, otherwise the calendar in the browser; hold opens
    today's list."""
    dynamic = True
    holdable = True

    def __init__(self, cfg):
        super().__init__(cfg)
        url = (cfg.get("url") or "").strip()
        self.feed = feed(url) if url else None
        self.warn_s = float(cfg.get("warn_min", DEFAULT_WARN_MIN) or 0) * 60

    def shown(self, now):
        """(occurrence, state) the key shows, or (None, reason text)."""
        if self.feed is None:
            return None, "no feed"
        occs = self.feed.today(now)
        occ, state = next_meeting(occs, now, self.warn_s)
        if occ is not None:
            return occ, state
        if not self.feed.loaded:
            return None, "offline" if self.feed.error else "loading"
        timed = [o for o in occs if not o.all_day]
        return None, "no more today" if timed else "free today"

    def render(self, pressed):
        tz = self.feed.tz if self.feed else None
        now = datetime.now(tz).astimezone()
        occ, state = self.shown(now)
        bg = dim(STATE_COLORS[state], 0.5) if occ is not None and state in ("now", "soon") else CLOCK_BG
        img, d, fg = tile(bg, pressed)
        if occ is not None and not pressed:
            d.rectangle([0, 0, KEY_PX, 3], fill=STATE_COLORS[state])
        # the clock stays small: the meeting is the point of the key
        draw_centered_lines(d, [now.strftime("%H:%M")], fg, size=13, top=4)
        draw_centered_lines(d, [now.strftime("%a %-d.%-m.")], (190, 190, 200) if not pressed else fg,
                            size=9, top=19, bold=False)
        if occ is None:
            draw_centered_lines(d, [state], (170, 170, 180) if not pressed else fg, size=10, top=42, bold=False)
            return img
        start = occ.start.astimezone(now.tzinfo)
        end = occ.end.astimezone(now.tzinfo)
        head = f"now → {end:%H:%M}" if state == "now" else f"{start:%H:%M}"
        draw_centered_lines(d, [head], fg, size=10, top=32)
        lines, size = fit_lines(occ.summary or "(no title)", max_lines=2, sizes=(11, 10, 9))
        draw_centered_lines(d, lines, fg, size=size, top=44, bold=False)
        return img

    def press(self):
        now = datetime.now().astimezone()
        occ, state = self.shown(now)
        if occ is not None and state in ("now", "soon") and occ.url:
            open_link(occ)
        else:
            open_link(None, self.cfg.get("open") or CALENDAR_WEB)

    def hold(self):
        if self.feed is not None:
            nav.push(CalendarPage(self))

    def render_held(self):
        return self.render(True)


class EventKey(Key):
    """One meeting on the page: time, title, a band in the colour of its state.
    Press opens its link, or the calendar when it has none."""

    def __init__(self, occ, warn_s, open_url):
        super().__init__({})
        self.occ, self.warn_s, self.open_url = occ, warn_s, open_url

    def render(self, pressed):
        now = datetime.now().astimezone()
        occ = self.occ
        state = "all_day" if occ.all_day else occ.state(now, self.warn_s)
        color = STATE_COLORS[state]
        img, d, fg = tile((24, 24, 30) if state != "past" else (18, 18, 22), pressed)
        if not pressed:
            d.rectangle([0, 0, KEY_PX, 4], fill=color)
        muted = (150, 150, 160) if state == "past" and not pressed else fg
        draw_centered_lines(d, [time_text(occ, now.tzinfo)], muted, size=10, top=8)
        lines, size = fit_lines(occ.summary or "(no title)", max_lines=3, sizes=(12, 11, 10, 9))
        draw_centered_lines(d, lines, muted, size=size, top=22)
        if occ.url:
            f = font(11)
            d.text((KEY_PX - 4 - d.textlength("»", font=f), KEY_PX - 15), "»", font=f,
                   fill=(170, 170, 180) if not pressed else fg)
        return img

    def press(self):
        open_link(self.occ, self.open_url)


class CalendarPage:
    """Today's events in order, all-day ones first. Key 0 goes back; with more
    than fit, keys 13 and 14 page through them."""
    PER_PAGE_FULL = KEY_COUNT - 1
    PER_PAGE_PAGED = KEY_COUNT - 3

    def __init__(self, key):
        self.key, self.index = key, 0

    def keys(self):
        now = datetime.now().astimezone()
        items = self.key.feed.today(now)
        keys = [Blank({}) for _ in range(KEY_COUNT)]
        keys[0] = back_key()
        if not items:
            keys[1] = NoteKey("nothing\ntoday")
            return keys
        paged = len(items) > self.PER_PAGE_FULL
        per = self.PER_PAGE_PAGED if paged else self.PER_PAGE_FULL
        pages = max(1, -(-len(items) // per))
        self.index = min(self.index, pages - 1)
        shown = items[self.index * per:(self.index + 1) * per]
        open_url = self.key.cfg.get("open") or CALENDAR_WEB
        for n, occ in enumerate(shown, start=1):
            keys[n] = EventKey(occ, self.key.warn_s, open_url)
        if paged:
            keys[13] = NavKey("<", f"{self.index + 1}/{pages}", lambda: self.turn(-1))
            keys[14] = NavKey(">", f"{self.index + 1}/{pages}", lambda: self.turn(1))
        return keys

    def turn(self, step):
        self.index = max(0, self.index + step)
        nav.dirty = True
