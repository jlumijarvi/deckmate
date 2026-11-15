#!/usr/bin/env python3
"""Step 2: a Stream Deck board of plain buttons, configured in keys.json.

Key types (see keys.json):
  command  (default)  label, color, run       tap runs the shell command
  clock               shows time and date, updates every second
  mute                target "source" (mic) or "sink" (speaker); shows state, tap toggles
  volume              step (+/- percent); shows the speaker volume, tap changes it
  slack               unread badge for one workspace ("team": id, default: the first
                      workspace found); tap opens that workspace in Slack
  todo                open items in a to-do list (a Markdown task list, see todo.py),
                      coloured by urgency; tap opens the list as a sub-deck, one key per
                      item, tap an item to open its linked file, hold it to mark it done.
                      Per key: "file" (default ~/.config/stream-deck/todo.md), "label",
                      "open_with" (default "code"), "soon_days" (0), "hold_done" (true)

A "screensaver" entry: {"image": "clock" | "/path/to/picture", "timeout_min": 5,
"sleep_min": 0}. After timeout_min idle minutes the whole screen shows the picture
or a big clock; any key press brings the board back without running that key.
sleep_min is the deck's own backlight-off timer (0 = never). On exit the saver is
painted one last time, so it stays on the deck's screen instead of the stored image.

A "sessions" entry claims a list of keys for live Claude Code sessions, one per
key, sorted waiting / running / done / stopped / failed. Tap opens the session's
folder in VS Code, which raises the window that already has it open.

Usage:
    python3 deck.py                       # run until Ctrl-C
    python3 deck.py --config keys.json
    python3 deck.py --seconds 5 --preview board.png
    python3 deck.py --saver --preview saver.png   # start in screen-saver mode
"""
import argparse
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from deckhid import Deck, UnsupportedDeck, find_deck, COLS, ROWS, KEY_COUNT, KEY_PX, LCD_W, LCD_H
from sessions import Session, SessionScanner, elapsed_text
from slackstate import SlackState, SNAP_ICON
from todo import TodoList, due_text
from paths import CONFIG_DIR

import ctypes
import ctypes.util

from paths import HERE, PID_FILE, STATUS_FILE, STATE_FILE, LOG_FILE, config_path, ensure_dirs
from board import PressTracker, SessionView, StateStore, Effects, detect_pulses, scroll_offset, breath
from stats import StatsProvider, fmt_tokens
from gitinfo import GitWatcher, dot_kind
import logging
import logging.handlers

log = logging.getLogger("deck")


def setup_logging():
    """stdout (the journal under systemd) plus a rotating file under ~/.local/state."""
    ensure_dirs()
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(out)
    try:
        fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
RECONNECT_S = 2.0
REFRESH_S = 1.0      # data refresh and static repaint
FRAME_S = 0.05       # repaint interval while something animates
SCROLL_PX_S = 30.0
HOLD_S = 0.6     # a press this long is a hold, for keys that have a hold action

_fonts = {}


def font(size, bold=True):
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    return _fonts[key]


def hex_color(s, default=(60, 60, 60)):
    if not s:
        return default
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def sh(cmd):
    """Run a shell command, return stdout (text), '' on failure."""
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def launch(cmd):
    """Start a command detached from the deck process so it outlives us."""
    subprocess.Popen(cmd, shell=True, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_libsystemd = None


def seat_active_uid():
    """uid of the account whose session currently owns the screen (seat0), or None
    if that cannot be determined. With several Linux accounts logged in, only the
    active one should drive the deck."""
    global _libsystemd
    if _libsystemd is None:
        name = ctypes.util.find_library("systemd")
        _libsystemd = ctypes.CDLL(name) if name else False
    if _libsystemd:
        uid = ctypes.c_uint()
        session = ctypes.c_char_p()
        if _libsystemd.sd_seat_get_active(b"seat0", ctypes.byref(session), ctypes.byref(uid)) >= 0:
            return uid.value
        return None
    try:  # fallback: logind's seat file
        with open("/run/systemd/seats/seat0") as f:
            for line in f:
                if line.startswith("ACTIVE_UID="):
                    return int(line.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return None


# --- drawing -----------------------------------------------------------------

def tile(bg, pressed=False):
    img = Image.new("RGB", (KEY_PX, KEY_PX), (255, 255, 255) if pressed else bg)
    return img, ImageDraw.Draw(img), (0, 0, 0) if pressed else (255, 255, 255)


def draw_centered_lines(d, lines, fg, size=14, top=None, bold=True, left=0):
    """Centred in the key, or in the part right of `left` (room for a corner mark)."""
    f = font(size, bold)
    line_h = size + 3
    total = line_h * len(lines)
    y = (KEY_PX - total) / 2 if top is None else top
    for line in lines:
        w = d.textlength(line, font=f)
        d.text((left + (KEY_PX - left - w) / 2, y), line, font=f, fill=fg)
        y += line_h


def fit_lines(label, max_lines=3, sizes=(14, 13, 12, 11, 10, 9), width=KEY_PX - 6):
    """Split a label into lines and pick a font size where every line fits.
    Explicit newlines are kept; otherwise wrap after spaces, hyphens, underscores
    and slashes. Among readable sizes (11 px and up) the fewest lines win, then the
    largest size; smaller sizes are a last resort. Returns (lines, size)."""
    def wrap(size):
        f = font(size)
        out = []
        for raw in label.split("\n"):
            tokens = [t for t in re.split(r"(?<=[ \-_/])", raw) if t]
            cur = ""
            for t in tokens:
                cand = cur + t
                if cur and f.getlength(cand.rstrip()) > width:
                    out.append(cur.rstrip())
                    cur = t.lstrip()
                else:
                    cur = cand
            out.append(cur.rstrip())
        fits = len(out) <= max_lines and all(f.getlength(line) <= width for line in out)
        return out, fits

    candidates = []
    for size in sizes:
        out, fits = wrap(size)
        if fits:
            candidates.append((len(out) + (1 if size < 11 else 0), -size, out, size))
    if candidates:
        _, _, out, size = min(candidates)
        return out, size
    out, _ = wrap(sizes[-1])
    if len(out) > max_lines:
        out = out[:max_lines]
        out[-1] = out[-1].rstrip() + "…"
    return out, sizes[-1]


# --- key classes -------------------------------------------------------------

class Key:
    dynamic = False   # True: re-render on every refresh tick
    holdable = False  # True: tap fires on release, hold() fires after HOLD_S

    def __init__(self, cfg):
        self.cfg = cfg

    def render(self, pressed):
        raise NotImplementedError

    def tap(self):
        pass

    def hold(self):
        pass

    def render_held(self):
        """What to show right after hold() ran."""
        return self.render(True)


class Blank(Key):
    def render(self, pressed):
        return Image.new("RGB", (KEY_PX, KEY_PX), (0, 0, 0))


class Command(Key):
    def render(self, pressed):
        img, d, fg = tile(hex_color(self.cfg.get("color")), pressed)
        lines, size = fit_lines(self.cfg.get("label", ""))
        draw_centered_lines(d, lines, fg, size=size)
        return img

    def tap(self):
        launch(self.cfg["run"])


class Clock(Key):
    dynamic = True

    def render(self, pressed):
        now = datetime.now()
        img, d, fg = tile((20, 20, 30), pressed)
        draw_centered_lines(d, [now.strftime("%H:%M")], fg, size=22, top=12)
        draw_centered_lines(d, [now.strftime("%a %-d.%-m.")], (190, 190, 200) if not pressed else fg,
                            size=12, top=44, bold=False)
        return img


class Mute(Key):
    dynamic = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self.target = "@DEFAULT_AUDIO_SOURCE@" if cfg.get("target") == "source" else "@DEFAULT_AUDIO_SINK@"

    def muted(self):
        return "[MUTED]" in sh(f"wpctl get-volume {self.target}")

    def render(self, pressed):
        muted = self.muted()
        img, d, fg = tile((180, 40, 40) if muted else (40, 120, 60), pressed)
        draw_centered_lines(d, [self.cfg.get("label", "Audio")], fg, size=14, top=14)
        draw_centered_lines(d, ["MUTED" if muted else "on"], fg, size=12, top=40, bold=False)
        return img

    def tap(self):
        sh(f"wpctl set-mute {self.target} toggle")


class Volume(Key):
    dynamic = True

    def volume(self):
        out = sh("wpctl get-volume @DEFAULT_AUDIO_SINK@")
        try:
            return int(round(float(out.split()[1]) * 100))
        except (IndexError, ValueError):
            return None

    def render(self, pressed):
        step = int(self.cfg.get("step", 5))
        vol = self.volume()
        img, d, fg = tile((50, 60, 90), pressed)
        draw_centered_lines(d, ["Vol +" if step > 0 else "Vol -"], fg, size=16, top=12)
        draw_centered_lines(d, ["?" if vol is None else f"{vol}%"], fg, size=13, top=42, bold=False)
        return img

    def tap(self):
        step = int(self.cfg.get("step", 5))
        sign = "+" if step > 0 else "-"
        sh(f"wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ {abs(step)}%{sign}")



CYAN = (0, 200, 220)

STATE_COLORS = {
    "waiting": (245, 166, 35),
    "running": (52, 140, 235),
    "done": (46, 180, 100),
    "stopped": (120, 120, 120),
    "failed": (220, 60, 60),
}


def dim(color, f=0.22):
    return tuple(int(c * f) for c in color)


CLAUDE = (217, 119, 87)   # the terracotta of the Claude logo
MARK_W = 17               # width the corner mark takes from the left edge


def claude_mark(d, pressed, cx=9, cy=KEY_PX - 10):
    """A small sunburst in the bottom-left corner: this key is about Claude Code."""
    color = (0, 0, 0) if pressed else CLAUDE
    for i in range(8):
        a = math.pi / 4 * i
        r = 5.5 if i % 2 == 0 else 3.5
        d.line([(cx, cy), (cx + r * math.cos(a), cy + r * math.sin(a))], fill=color, width=1)
    d.ellipse([cx - 1.5, cy - 1.5, cx + 1.5, cy + 1.5], fill=color)


DEMO = False


def demo_sessions():
    now = time.time()
    phase = int(time.monotonic() // 10) % 3   # the first session cycles running -> done -> waiting
    mk = lambda name, state, kind, ago, ctx: Session(
        pid=0, session_id="demo-" + name, name=name, cwd="", state=state, waiting_kind=kind,
        since=now - ago, ctx_used=ctx, ctx_window=1_000_000, transcript="")
    return sorted([
        mk("Korg CX-3 web MIDI app", ("running", "done", "waiting")[phase], ("", "", "!")[phase], 95, 240_000),
        mk("Yamaha CK remaining work", "waiting", "?", 12, 610_000),
        mk("Stream Deck tweaking project overview", "running", "", 240, 120_000),
        mk("Encrypted SSD migration to Ubuntu", "done", "", 4000, 900_000),
        mk("vire", "stopped", "", 90000, 50_000),
        mk("gearbasement", "failed", "", 700, 300_000),
    ], key=lambda s: s.sort_key)


class SessionBoard:
    """Shared scanner plus the SessionView (dismissals, filter, paging); refreshed
    once per tick, read by every SessionSlot, CounterKey and PagerKey."""

    def __init__(self, slots):
        self.scanner = SessionScanner()
        self.store = StateStore(STATE_FILE)
        self.view = SessionView(slots, self.store)
        self.git = GitWatcher()
        self.sessions = []
        self.known_states = {}
        self.pending_pulses = []      # (session, state) found by the last scan
        self.first_seen = {}          # session_id -> monotonic time, for the scroll clock
        self._last_scan = 0.0

    def refresh(self):
        now = time.monotonic()
        if now - self._last_scan < REFRESH_S:
            return
        self._last_scan = now
        if DEMO:
            self.sessions = demo_sessions()
        else:
            try:
                self.sessions = self.scanner.scan()
            except Exception as e:  # a half-written file must not kill the board
                log.info(f"session scan failed: {e}")
        pulses, self.known_states = detect_pulses(self.known_states, self.sessions)
        self.pending_pulses.extend(pulses)
        for sess in self.sessions:
            self.first_seen.setdefault(sess.session_id, now)
            if sess.cwd:
                self.git.want(sess.cwd)
        self.view.update(self.sessions)

    def animating(self):
        """True while a visible session key needs frequent repaints."""
        for sess in self.view.visible():
            if sess is None:
                continue
            if sess.state == "running":
                return True
            if title_scrolls(sess.name):
                return True
        return False


def title_layout(name):
    """(lines, size) for a static title, or None when it should scroll instead:
    anything that needs more than two lines or less than 11 px."""
    lines, size = fit_lines(name, max_lines=2, sizes=(13, 12, 11))
    f = font(size)
    truncated = lines and lines[-1].endswith("…") and not name.endswith("…")
    if not truncated and len(lines) <= 2 and all(f.getlength(line) <= KEY_PX - 6 for line in lines):
        return lines, size
    return None


_title_strips = {}


def title_strip(name, size=13):
    """One-line rendering of a title, cached: (RGBA image, width)."""
    key = (name, size)
    if key not in _title_strips:
        f = font(size)
        w = int(f.getlength(name)) + 2
        img = Image.new("RGBA", (w, size + 6), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((1, 0), name, font=f, fill=(255, 255, 255, 255))
        _title_strips[key] = (img, w)
    return _title_strips[key]


def title_scrolls(name):
    return title_layout(name) is None


def render_session(s, pressed, hidden=False, scroll_t=0.0, now=None, git=None):
    """The key image for one session (None -> empty slot)."""
    if s is None:
        return Image.new("RGB", (KEY_PX, KEY_PX), (12, 12, 14))
    color = STATE_COLORS.get(s.state, (120, 120, 120))
    factor = 0.12 if hidden else 0.22
    if s.state == "running" and not hidden and not pressed:
        factor = breath(now if now is not None else time.monotonic())   # slow breathing blue
    img, d, fg = tile(dim(color, factor), pressed)
    if not pressed:
        d.rectangle([0, 0, KEY_PX, 4], fill=dim(color, 0.5) if hidden else color)
    info = font(11)
    if s.waiting_kind:
        d.text((4, 6), s.waiting_kind, font=font(13), fill=color if not pressed else fg)
    t = elapsed_text(s.since)
    d.text((KEY_PX - 4 - d.textlength(t, font=info), 7), t, font=info,
           fill=(200, 200, 200) if not pressed else fg)
    title_fg = (150, 150, 160) if hidden and not pressed else fg
    layout = title_layout(s.name)
    if layout is not None:
        lines, size = layout
        draw_centered_lines(d, lines, title_fg, size=size, top=21 if len(lines) > 1 else 29)
    else:
        strip, w = title_strip(s.name)
        off = scroll_offset(scroll_t, w, KEY_PX - 6, speed=SCROLL_PX_S)
        view = strip.crop((off, 0, off + KEY_PX - 6, strip.height))
        tinted = Image.new("RGB", view.size, title_fg)
        img.paste(tinted, (3, 29), view)
    frac = s.ctx_fraction
    bar = (110, 110, 110) if frac < 0.6 else (245, 166, 35) if frac < 0.85 else (220, 60, 60)
    d.rectangle([0, KEY_PX - 3, KEY_PX, KEY_PX], fill=(30, 30, 30))
    d.rectangle([0, KEY_PX - 3, int(KEY_PX * frac), KEY_PX], fill=bar)
    # git: hollow dot = dirty worktree, filled = clean but ahead of upstream
    kind = dot_kind(git)
    if kind:
        r = 4
        cx, cy = KEY_PX - 8, KEY_PX - 11
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(200, 200, 210) if kind == "filled" else None,
                  outline=(200, 200, 210), width=1)
    claude_mark(d, pressed)
    return img


class SessionSlot(Key):
    dynamic = True
    holdable = True   # hold dismisses

    def __init__(self, board, index):
        super().__init__({})
        self.board = board
        self.index = index
        self.dismissed_ok = False

    @property
    def session(self):
        return self.board.view.visible()[self.index]

    def render(self, pressed):
        s = self.session
        if s is None:
            return render_session(None, pressed)
        now = time.monotonic()
        return render_session(s, pressed, scroll_t=now - self.board.first_seen.get(s.session_id, now), now=now,
                              git=self.board.git.state(s.cwd))

    def tap(self):
        s = self.session
        if s and s.cwd:
            launch("code " + shlex.quote(s.cwd))

    def hold(self):
        s = self.session
        self.dismissed_ok = self.board.view.dismiss(s)
        if self.dismissed_ok:
            log.info(f"dismissed: {s.name} ({s.state})")
            nav.dirty = True

    def render_held(self):
        s = self.session
        if s is not None and s.state == "running":
            img, d, fg = tile((60, 60, 70), False)
            draw_centered_lines(d, ["running,", "not", "dismissed"], fg, size=12, top=14)
            return img
        img, d, fg = tile((40, 40, 50), False)
        draw_centered_lines(d, ["hidden"], fg, size=16, top=26)
        return img


class CounterKey(Key):
    """Number of sessions in one state; tap filters the board to that state
    (bright border while on); hold on the done counter dismisses every done session."""
    dynamic = True

    def __init__(self, board, state):
        super().__init__({})
        self.board, self.state = board, state
        self.holdable = state == "done"
        self.last_dismissed = 0

    def render(self, pressed):
        v = self.board.view
        n = v.count(self.state)
        color = STATE_COLORS[self.state]
        img, d, fg = tile(dim(color, 0.35 if n else 0.15), pressed)
        if v.filter == self.state and not pressed:
            d.rectangle([0, 0, KEY_PX - 1, KEY_PX - 1], outline=color, width=3)
        draw_centered_lines(d, [self.state], fg if n else (150, 150, 160), size=12, top=9)
        draw_centered_lines(d, [str(n)], fg if n else (150, 150, 160), size=30, top=26)
        claude_mark(d, pressed)
        return img

    def tap(self):
        self.board.view.toggle_filter(self.state)
        nav.dirty = True

    def hold(self):
        self.last_dismissed = self.board.view.dismiss_all_done()
        nav.dirty = True

    def render_held(self):
        img, d, fg = tile((40, 40, 50), False)
        draw_centered_lines(d, [f"{self.last_dismissed}", "hidden"], fg, size=16, top=18)
        return img


class PagerKey(Key):
    """prev: previous session page, from the first page the stats page; hold shows the
    dismissed sessions. next: next page; hold cycles brightness 30 / 60 / 100."""
    dynamic = True
    holdable = True

    def __init__(self, board, direction, on_brightness=None):
        super().__init__({})
        self.board, self.direction, self.on_brightness = board, direction, on_brightness

    def render(self, pressed):
        v = self.board.view
        pages = v.page_count()
        img, d, fg = tile((36, 36, 44), pressed)
        symbol = "<" if self.direction == "prev" else ">"
        draw_centered_lines(d, [symbol], fg, size=30, top=10)
        if self.direction == "prev" and v.page == 0:
            label = "stats"
        else:
            label = f"{v.page + 1}/{pages}"
        draw_centered_lines(d, [label], (170, 170, 180) if not pressed else fg, size=11, top=50, bold=False)
        claude_mark(d, pressed)
        return img

    def tap(self):
        v = self.board.view
        if self.direction == "prev":
            if v.page == 0:
                nav.push(StatsPage(self.board))
            else:
                v.turn(-1)
        else:
            v.turn(1)
        nav.dirty = True

    def hold(self):
        if self.direction == "prev":
            nav.push(DismissedPage(self.board))
        elif self.on_brightness:
            self.on_brightness()
        nav.dirty = True

    def render_held(self):
        return self.render(True)


class DismissedSessionKey(Key):
    """A dismissed session on the dismissed page; tap brings it back."""

    def __init__(self, board, s):
        super().__init__({})
        self.board, self.s = board, s

    def render(self, pressed):
        return render_session(self.s, pressed, hidden=True)

    def tap(self):
        self.board.view.restore(self.s.session_id)
        log.info(f"restored: {self.s.name}")
        nav.dirty = True


class DismissedPage:
    def __init__(self, board):
        self.board = board

    def keys(self):
        keys = [Blank({}) for _ in range(KEY_COUNT)]
        keys[0] = NavKey("<", "back", nav.pop)
        hidden = self.board.view.dismissed_sessions()
        if not hidden:
            keys[2] = Blank({})
            img_key = NavKey("", "nothing hidden", lambda: None)
            keys[2] = img_key
        for n, s in enumerate(hidden[:KEY_COUNT - 1], start=1):
            keys[n] = DismissedSessionKey(self.board, s)
        return keys


_stats_provider = None


def stats_provider():
    global _stats_provider
    if _stats_provider is None:
        _stats_provider = StatsProvider()
    return _stats_provider


class StatsPage:
    """Plan quota windows, tokens today, cache reads, fullest context."""

    def __init__(self, board):
        self.board = board

    def keys(self):
        st = stats_provider()
        plan, (tin, tout, tcache, msgs) = st.plan, st.tokens()
        keys = [Blank({}) for _ in range(KEY_COUNT)]
        keys[0] = NavKey("<", "back", nav.pop)
        asof = time.strftime("%-d.%-m. %H:%M", time.localtime(plan.sampled_at)) if plan.sampled_at else "no data"
        for n, (name, title) in enumerate((("fh", "5 hours"), ("sd", "7 days")), start=1):
            left = plan.left(name)
            color = CYAN if plan.new_window[name] else (52, 140, 235)
            keys[n] = StatTile(title, "—" if left is None else f"{left}%",
                               color, "new window" if plan.new_window[name] else f"left, {asof}",
                               border=plan.new_window[name])
        keys[3] = StatTile("tokens today", fmt_tokens(tout), (46, 180, 100),
                           f"out · in {fmt_tokens(tin)}")
        keys[4] = StatTile("cache reads", fmt_tokens(tcache), (120, 120, 140), f"today, {msgs} replies")
        fullest = max((s for s in (self.board.sessions if self.board else []) if s), key=lambda s: s.ctx_fraction, default=None)
        if fullest:
            frac = fullest.ctx_fraction
            color = (110, 110, 110) if frac < 0.6 else (245, 166, 35) if frac < 0.85 else (220, 60, 60)
            keys[5] = StatTile("context", f"{int(frac * 100)}%", color, fullest.name)
        else:
            keys[5] = StatTile("context", "—", (90, 90, 100), "no sessions")
        return keys


class StatsKey(Key):
    """Opens the stats page; shows the five-hour window at a glance."""
    dynamic = True

    def __init__(self, cfg, board):
        super().__init__(cfg)
        self.board = board

    def render(self, pressed):
        plan = stats_provider().plan
        left = plan.left("fh")
        color = CYAN if plan.new_window["fh"] else (52, 140, 235)
        img, d, fg = tile(dim(color, 0.3), pressed)
        if not pressed:
            d.rectangle([0, 0, KEY_PX, 4], fill=color)
        draw_centered_lines(d, [self.cfg.get("label") or "stats"], fg, size=12, top=9)
        draw_centered_lines(d, ["—" if left is None else f"{left}%"], fg, size=26, top=24)
        draw_centered_lines(d, ["5h left"], (200, 200, 210) if not pressed else fg, size=10, top=56, bold=False,
                            left=MARK_W)
        claude_mark(d, pressed)
        return img

    def tap(self):
        nav.push(StatsPage(self.board))


class StatTile(Key):
    def __init__(self, title, value, color=(90, 90, 100), foot="", border=False):
        super().__init__({})
        self.title, self.value, self.color, self.foot, self.border = title, value, color, foot, border

    def render(self, pressed):
        img, d, fg = tile(dim(self.color, 0.3), pressed)
        if not pressed:
            d.rectangle([0, 0, KEY_PX, 4], fill=self.color)
            if self.border:
                d.rectangle([0, 0, KEY_PX - 1, KEY_PX - 1], outline=self.color, width=3)
        tl, tsize = fit_lines(self.title, max_lines=1, sizes=(11, 10, 9))
        draw_centered_lines(d, tl, fg, size=tsize, top=9)
        lines, size = fit_lines(self.value, max_lines=1, sizes=(26, 22, 18, 14, 11))
        draw_centered_lines(d, lines, fg, size=size, top=24)
        if self.foot:
            lines, fsize = fit_lines(self.foot, max_lines=1, sizes=(10, 9), width=KEY_PX - MARK_W - 4)
            draw_centered_lines(d, lines, (200, 200, 210) if not pressed else fg, size=fsize, top=56, bold=False,
                                left=MARK_W)
        claude_mark(d, pressed)
        return img


_slack_state = None


def slack_state():
    global _slack_state
    if _slack_state is None:
        _slack_state = SlackState()
    return _slack_state


class Slack(Key):
    """Workspace logo, name, and a badge with the number of conversations that
    have unread messages. Mentions and DMs are shown separately when non-zero."""
    dynamic = True
    _logo = None

    def workspace(self):
        state = slack_state()
        state.refresh()
        wss = state.workspaces()
        team = self.cfg.get("team")
        if team:
            for w in wss:
                if w["id"] == team:
                    return w
            return {"id": team, "name": team, "domain": ""}
        return wss[0] if wss else None

    @classmethod
    def logo(cls):
        if cls._logo is None:
            try:
                cls._logo = Image.open(SNAP_ICON).convert("RGBA").resize((26, 26))
            except OSError:
                cls._logo = False
        return cls._logo or None

    def render(self, pressed):
        img, d, fg = tile((74, 21, 75), pressed)   # Slack aubergine
        ws = self.workspace()
        logo = self.logo()
        if logo:
            img.paste(logo, (6, 6), logo)
        else:
            d.text((6, 6), "S", font=font(22), fill=fg)
        if ws is None:
            draw_centered_lines(d, ["Slack", "no data"], fg, size=12, top=36)
            return img
        counts = slack_state().unreads(ws["id"])
        unread, mentions = counts if counts else (None, None)
        # badge top-right
        text = "?" if unread is None else str(unread)
        badge = (225, 40, 40) if unread else (90, 90, 100)
        f = font(16 if len(text) < 3 else 12)
        w = d.textlength(text, font=f)
        r = 13
        cx, cy = KEY_PX - 6 - r, 6 + r
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=badge if not pressed else (0, 0, 0))
        box = f.getbbox(text)
        d.text((cx - w / 2, cy - (box[3] - box[1]) / 2 - box[1]), text, font=f, fill=(255, 255, 255))
        if mentions:
            d.text((6, 36), f"@{mentions}", font=font(12), fill=(255, 200, 80) if not pressed else fg)
        lines, size = fit_lines(ws["name"], max_lines=1, sizes=(13, 12, 11, 10, 9))
        draw_centered_lines(d, lines, fg, size=size, top=KEY_PX - size - 16)
        return img

    def tap(self):
        ws = self.workspace()
        if ws:
            launch(f"xdg-open 'slack://open?team={ws['id']}'")



# --- pages (sub-decks) ---------------------------------------------------------

class Navigator:
    """The main board plus a stack of pages opened on top of it."""

    def __init__(self, main_keys):
        self.main = main_keys
        self.stack = []
        self.dirty = False

    def keys(self):
        return self.stack[-1].keys() if self.stack else self.main

    def push(self, page):
        self.stack.append(page)
        self.dirty = True

    def pop(self):
        if self.stack:
            self.stack.pop()
            self.dirty = True

    def home(self):
        self.stack.clear()
        self.dirty = True


nav = None  # set in main()


class NavKey(Key):
    """Back / previous page / next page."""

    def __init__(self, symbol, label, action):
        super().__init__({})
        self.symbol, self.label, self.action = symbol, label, action

    def render(self, pressed):
        img, d, fg = tile((36, 36, 44), pressed)
        draw_centered_lines(d, [self.symbol], fg, size=30, top=10)
        draw_centered_lines(d, [self.label], (170, 170, 180) if not pressed else fg, size=11, top=50, bold=False)
        return img

    def tap(self):
        self.action()


# --- to-do list ----------------------------------------------------------------

URGENCY_COLORS = {
    "overdue": (220, 60, 60),
    "soon": (245, 166, 35),
    "ok": (52, 140, 235),
    "empty": (110, 110, 120),
}


def item_urgency(item, soon_days):
    n = item.days_left()
    if n is None:
        return "none"
    if n < 0:
        return "overdue"
    if n <= soon_days:
        return "soon"
    return "ok"


class TodoKey(Key):
    """Badge: number of open items, coloured by the most urgent one."""
    dynamic = True

    def __init__(self, cfg, todo):
        super().__init__(cfg)
        self.todo = todo

    def render(self, pressed):
        t = self.todo
        urgency = t.urgency()
        color = URGENCY_COLORS[urgency]
        img, d, fg = tile(dim(color, 0.35), pressed)
        if not pressed:
            d.rectangle([0, 0, KEY_PX, 4], fill=color)
        draw_centered_lines(d, [self.cfg.get("label") or "To do"], fg, size=12, top=9)
        if not t.exists:
            draw_centered_lines(d, ["no file"], fg, size=11, top=40, bold=False)
            return img
        draw_centered_lines(d, [str(len(t.items))], fg, size=28, top=24)
        late, soon = len(t.overdue()), len(t.soon())
        if late:
            foot = f"{late} late"
        elif soon:
            foot = "due today" if t.soon_days == 0 else f"{soon} due soon"
        elif t.next_due():
            foot = "next " + due_text(t.next_due())
        else:
            foot = ""
        draw_centered_lines(d, [foot], (230, 230, 235) if not pressed else fg, size=10, top=56, bold=False)
        return img

    def tap(self):
        nav.push(TodoPage(self.todo, self.cfg))


class TodoItemKey(Key):
    def __init__(self, item, todo, cfg):
        super().__init__(cfg)
        self.item, self.todo = item, todo
        self.holdable = bool(cfg.get("hold_done", True))
        self.done = False

    def render(self, pressed):
        urgency = item_urgency(self.item, self.todo.soon_days)
        color = URGENCY_COLORS.get(urgency, (90, 90, 100))
        img, d, fg = tile((24, 24, 30), pressed)
        if not pressed:
            d.rectangle([0, 0, KEY_PX, 4], fill=color)
        lines, size = fit_lines(self.item.title, max_lines=3, sizes=(13, 12, 11, 10, 9))
        draw_centered_lines(d, lines, fg, size=size, top=8)
        foot = due_text(self.item.due) if self.item.due else ""
        if foot:
            f = font(10, False)
            d.text((4, KEY_PX - 14), foot, font=f, fill=color if not pressed else fg)
        if self.item.link:
            f = font(11)
            d.text((KEY_PX - 4 - d.textlength("»", font=f), KEY_PX - 15), "»", font=f,
                   fill=(170, 170, 180) if not pressed else fg)
        return img

    def tap(self):
        if not self.item.link:
            return
        path = os.path.expanduser(self.item.link)
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(self.todo.path), path)
        opener = self.cfg.get("open_with") or "code"
        launch(f"{opener} {shlex.quote(path)}")

    def hold(self):
        self.done = self.todo.mark_done(self.item)
        log.info(f"todo: {'done' if self.done else 'could not mark'}: {self.item.title}")

    def render_held(self):
        img, d, fg = tile((40, 120, 60) if self.done else (120, 40, 40), False)
        draw_centered_lines(d, ["done" if self.done else "not", "" if self.done else "changed"], fg, size=16, top=20)
        return img


class TodoPage:
    """One key per open item, overdue first. Key 0 goes back; with more items than
    fit, keys 13 and 14 page through them."""
    PER_PAGE_FULL = KEY_COUNT - 1
    PER_PAGE_PAGED = KEY_COUNT - 3

    def __init__(self, todo, cfg):
        self.todo, self.cfg, self.index = todo, cfg, 0

    def keys(self):
        items = self.todo.items
        paged = len(items) > self.PER_PAGE_FULL
        per = self.PER_PAGE_PAGED if paged else self.PER_PAGE_FULL
        pages = max(1, -(-len(items) // per))
        self.index = min(self.index, pages - 1)
        shown = items[self.index * per:(self.index + 1) * per]
        keys = [Blank({}) for _ in range(KEY_COUNT)]
        keys[0] = NavKey("<", "back", nav.pop)
        for n, item in enumerate(shown, start=1):
            keys[n] = TodoItemKey(item, self.todo, self.cfg)
        if paged:
            keys[13] = NavKey("<", f"{self.index + 1}/{pages}", lambda: self.turn(-1))
            keys[14] = NavKey(">", f"{self.index + 1}/{pages}", lambda: self.turn(1))
        return keys

    def turn(self, step):
        self.index = max(0, self.index + step)
        nav.dirty = True


KEY_TYPES = {"command": Command, "clock": Clock, "mute": Mute, "volume": Volume, "slack": Slack}


BRIGHTNESS_STEPS = (30, 60, 100)


def cycle_brightness(config_file):
    """Hold on the next-page key: 30 -> 60 -> 100 -> 30, written to the layout file,
    which the running board reloads."""
    try:
        with open(config_file) as f:
            cfg = json.load(f)
        cur = int(cfg.get("brightness", 60))
        nxt = next((b for b in BRIGHTNESS_STEPS if b > cur), BRIGHTNESS_STEPS[0])
        cfg["brightness"] = nxt
        tmp = config_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        os.replace(tmp, config_file)
        log.info(f"brightness {cur} -> {nxt}")
    except (OSError, ValueError) as e:
        log.info(f"brightness change failed: {e}")


def load_board(path):
    with open(path) as f:
        cfg = json.load(f)
    keys = [Blank({}) for _ in range(KEY_COUNT)]
    for idx, kcfg in cfg.get("keys", {}).items():
        i = int(idx)
        if not 0 <= i < KEY_COUNT:
            log.info(f"keys.json: ignoring key {idx}, valid keys are 0..{KEY_COUNT - 1}")
            continue
        ktype = kcfg.get("type", "command")
        if ktype in ("todo", "counter", "pager", "stats"):
            keys[i] = (ktype, kcfg)  # resolved below, once the shared objects exist
            continue
        if ktype not in KEY_TYPES:
            log.info(f"keys.json: key {idx} has unknown type {ktype!r}")
            continue
        keys[i] = KEY_TYPES[ktype](kcfg)
    providers = []
    region = [int(x) for x in cfg.get("sessions", {}).get("keys", []) if 0 <= int(x) < KEY_COUNT]
    board = None
    if region or any(isinstance(k, tuple) and k[0] in ("counter", "pager", "stats") for k in keys):
        board = SessionBoard(len(region) or 1)
        providers.append(board)
        for n, idx in enumerate(region):
            keys[idx] = SessionSlot(board, n)
    for i, k in enumerate(keys):
        if isinstance(k, tuple) and k[0] == "counter":
            keys[i] = CounterKey(board, k[1].get("state", "waiting"))
        elif isinstance(k, tuple) and k[0] == "pager":
            keys[i] = PagerKey(board, k[1].get("dir", "next"), on_brightness=lambda: cycle_brightness(path))
        elif isinstance(k, tuple) and k[0] == "stats":
            keys[i] = StatsKey(k[1], board)
    if board is not None:
        providers.append(stats_provider())   # plan usage: watched for window resets (cyan pulse)
    defaults = cfg.get("todo", {})           # optional board-wide defaults for todo keys
    lists = {}                               # one TodoList per file, shared by keys on the same file
    for i, k in enumerate(keys):
        if isinstance(k, tuple) and k[0] == "todo":
            kcfg = {**defaults, **k[1]}
            path = os.path.expanduser(kcfg.get("file") or os.path.join(CONFIG_DIR, "todo.md"))
            if path not in lists:
                lists[path] = TodoList(path, kcfg.get("soon_days", 0))
                try:
                    lists[path].ensure_file()
                except OSError as e:
                    log.info(f"todo: cannot create {path}: {e}")
                providers.append(lists[path])
            keys[i] = TodoKey(kcfg, lists[path])
    return keys, int(cfg.get("brightness", 60)), providers, ScreenSaver(cfg.get("screensaver", {}))


def board_image(images):
    gap = 10
    board = Image.new("RGB", (COLS * KEY_PX + (COLS + 1) * gap,
                              ROWS * KEY_PX + (ROWS + 1) * gap), (20, 20, 20))
    for k, img in enumerate(images):
        board.paste(img, (gap + (k % COLS) * (KEY_PX + gap), gap + (k // COLS) * (KEY_PX + gap)))
    return board



# --- screen saver -------------------------------------------------------------

class ScreenSaver:
    """Renders the idle picture: a file, or a big clock."""

    def __init__(self, cfg):
        self.image_setting = cfg.get("image", "clock") or "clock"
        self.timeout_s = float(cfg.get("timeout_min", 0) or 0) * 60
        self.sleep_s = int(float(cfg.get("sleep_min", 0) or 0) * 60)
        self._picture = None
        self._picture_err = None

    @property
    def enabled(self):
        return self.timeout_s > 0

    def picture(self):
        if self._picture is None and self._picture_err is None:
            try:
                img = Image.open(os.path.expanduser(self.image_setting)).convert("RGB")
                # cover: scale to fill 480x272, crop the overflow in the middle
                scale = max(LCD_W / img.width, LCD_H / img.height)
                img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
                x = (img.width - LCD_W) // 2
                y = (img.height - LCD_H) // 2
                self._picture = img.crop((x, y, x + LCD_W, y + LCD_H))
            except (OSError, ValueError) as e:
                self._picture_err = str(e)
                log.info(f"screensaver: cannot load {self.image_setting}: {e}; using the clock")
        return self._picture

    @property
    def is_tiles(self):
        """True when the saver is drawn as 15 key images (the clock) rather than one
        full-screen picture. Key images cannot be cut by the bezels between keys."""
        return self.image_setting == "clock" or self.picture() is None

    def render(self, final=False):
        """Full-screen frame, for picture savers only."""
        return self.picture()

    @staticmethod
    def _tile(text="", size=64, fg=(235, 235, 240), bold=True, dy=0):
        img = Image.new("RGB", (KEY_PX, KEY_PX), (8, 8, 12))
        if text:
            d = ImageDraw.Draw(img)
            while size > 10 and d.textlength(text, font=font(size, bold)) > KEY_PX - 6:
                size -= 2
            f = font(size, bold)
            w = d.textlength(text, font=f)
            # centre on the glyph box so digits of different heights sit on one line
            box = f.getbbox(text)
            h = box[3] - box[1]
            d.text(((KEY_PX - w) / 2, (KEY_PX - h) / 2 - box[1] + dy), text, font=f, fill=fg)
        return img

    def render_tiles(self, final=False):
        """The clock as 15 key images: date on the top row, HH:MM on the middle row.
        final=True (left behind on exit) drops the time, which would freeze."""
        now = datetime.now()
        t = self._tile
        dim = (150, 150, 165)
        tiles = [t() for _ in range(KEY_COUNT)]
        date_parts = [now.strftime("%a"), now.strftime("%-d.%-m."), now.strftime("%Y")]
        # one size for all three date tiles, the largest where the widest part fits
        size = 30
        probe = ImageDraw.Draw(tiles[0])
        while size > 10 and max(probe.textlength(x, font=font(size, False)) for x in date_parts) > KEY_PX - 6:
            size -= 2
        for i, text in enumerate(date_parts, start=1):
            tiles[i] = t(text, size=size, fg=dim, bold=False)
        if not final:
            hh, mm = now.strftime("%H"), now.strftime("%M")
            tiles[5] = t(hh[0])
            tiles[6] = t(hh[1])
            tiles[7] = t(":", dy=-4)
            tiles[8] = t(mm[0])
            tiles[9] = t(mm[1])
        return tiles


# --- main loop ---------------------------------------------------------------

def open_deck():
    found = find_deck()
    if not found:
        return None
    node, pid, _ = found
    deck = Deck(node)
    log.info(f"connected to {node} (serial {deck.serial()}, firmware {deck.firmware()})")
    return deck


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=None,
                    help="layout file (default: keys.json, or keys.example.json if that is missing)")
    ap.add_argument("--seconds", type=float, default=None, help="exit after N seconds")
    ap.add_argument("--preview", metavar="PNG", help="save the board as an image once painted")
    ap.add_argument("--demo", action="store_true", help="show made-up sessions in every state")
    ap.add_argument("--saver", action="store_true", help="start in screen-saver mode")
    args = ap.parse_args()
    global DEMO
    DEMO = args.demo
    setup_logging()

    if args.config is None:
        args.config = config_path()
    log.info(f"layout: {args.config}")
    keys, brightness, providers, saver = load_board(args.config)
    config_mtime = os.path.getmtime(args.config)
    global nav
    nav = Navigator(keys)
    ensure_dirs()
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    last_status = None

    def write_status(connected):
        """A small file for the tray icon: is the deck connected, how many sessions wait."""
        nonlocal last_status
        counts = {"waiting": 0, "running": 0, "done": 0}
        for p in providers:
            view = getattr(p, "view", None)
            if view is not None:
                for st in counts:
                    counts[st] += view.count(st)
        status = {"deck": connected, **counts}
        if status != last_status:
            last_status = status
            try:
                tmp = STATUS_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(status, f)
                os.replace(tmp, STATUS_FILE)
            except OSError:
                pass

    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("now", True))

    deck = None
    pressed = [False] * KEY_COUNT
    presses = PressTracker(HOLD_S)
    sent = [None] * KEY_COUNT        # JPEG bytes last sent per key
    next_open = 0.0
    next_refresh = 0.0
    deadline = time.monotonic() + args.seconds if args.seconds else None
    previewed = False
    last_activity = time.monotonic()
    saver_active = False
    saver_sent = None
    seat_paused = False
    next_seat_check = 0.0

    effects = Effects(COLS)

    def apply_overlays(images, now):
        """Pulse bands and glows on top of the rendered keys."""
        for i in range(KEY_COUNT):
            for kind, color, strength in effects.overlays(now, i):
                base = images[i]
                if kind == "band":
                    layer = Image.new("RGB", base.size, color)
                    images[i] = Image.blend(base, layer, 0.55 * strength)
                elif kind == "glow":
                    d = ImageDraw.Draw(images[i])
                    width = max(1, int(round(4 * strength)))
                    d.rectangle([0, 0, KEY_PX - 1, KEY_PX - 1], outline=color, width=width)
        return images

    def paint(force=False):
        """Render every key, send those that changed."""
        nonlocal sent
        now = time.monotonic()
        for p in providers:
            p.refresh()
        # new pulses found by a scan: sweep the deck, glow the key when it is on the board
        for p in providers:
            for sess, state in getattr(p, "pending_pulses", []):
                idx = next((i for i, k in enumerate(nav.keys())
                            if isinstance(k, SessionSlot) and k.session is sess), None)
                effects.pulse(now, STATE_COLORS[state], idx)
                log.info(f"pulse: {sess.name} -> {state}")
            if hasattr(p, "pending_pulses"):
                p.pending_pulses.clear()
            plan = getattr(p, "plan", None)
            if plan is not None and plan.reset_events:
                effects.pulse(now, CYAN)
                log.info(f"plan window reset: {', '.join(plan.reset_events)}")
        if nav.dirty:
            nav.dirty = False
            sent = [None] * KEY_COUNT
        images = [k.render(pressed[i]) for i, k in enumerate(nav.keys())]
        if effects.active(now):
            images = apply_overlays(images, now)
        if deck is not None:
            for i, img in enumerate(images):
                data = Deck.encode_image(img)
                if force or data != sent[i]:
                    deck.set_key_jpeg(i, data)
                    sent[i] = data
        return images

    try:
        while not stop["now"]:
            now = time.monotonic()
            if deadline and now >= deadline:
                break

            # reload config when edited
            try:
                m = os.path.getmtime(args.config)
            except OSError:
                m = config_mtime
            if m != config_mtime:
                config_mtime = m
                try:
                    keys, brightness, providers, saver = load_board(args.config)
                    nav = Navigator(keys)
                    log.info("config reloaded")
                    if deck is not None:
                        deck.set_brightness(brightness)
                        deck.set_sleep_timeout(saver.sleep_s)
                    sent = [None] * KEY_COUNT
                    saver_sent = None
                except (OSError, ValueError) as e:
                    log.info(f"config reload failed: {e}")

            # another account owns the screen: let go of the deck, its own deck.py takes over
            if now >= next_seat_check:
                next_seat_check = now + 2.0
                active = seat_active_uid()
                inactive = active is not None and active != os.getuid()
                if inactive and not seat_paused:
                    log.info("another account is active, releasing the deck")
                    seat_paused = True
                    if deck is not None:
                        deck.close()
                        deck = None
                elif seat_paused and not inactive:
                    log.info("this account is active again")
                    seat_paused = False
                    last_activity = now
            if seat_paused:
                time.sleep(0.5)
                continue

            # (re)connect
            if deck is None and now >= next_open:
                next_open = now + RECONNECT_S
                try:
                    deck = open_deck()
                    if deck is None:
                        log.info("no deck found, retrying...")
                    else:
                        deck.fill_color(0, 0, 0)
                        deck.set_brightness(brightness)
                        deck.set_sleep_timeout(saver.sleep_s)
                        sent = [None] * KEY_COUNT
                        saver_sent = None
                        saver_active = args.saver and saver.enabled
                        if not saver_active:
                            paint(force=True)
                        next_refresh = now + REFRESH_S
                except UnsupportedDeck as e:
                    sys.exit(str(e))
                except OSError as e:
                    log.info(f"open failed: {e}")
                    deck = None

            if deck is None:
                write_status(False)
                time.sleep(0.2)
                continue

            try:
                if saver.enabled and not saver_active and now - last_activity >= saver.timeout_s:
                    saver_active = True
                    saver_sent = None
                    deck.fill_color(0, 0, 0)
                    log.info("screen saver on")

                if now >= next_refresh:
                    animating = effects.active(now) or any(getattr(p, "animating", lambda: False)() for p in providers)
                    next_refresh = now + (FRAME_S if animating and not saver_active else REFRESH_S)
                    if saver_active:
                        if saver.is_tiles:
                            tiles = saver.render_tiles()
                            if not isinstance(saver_sent, list):
                                saver_sent = [None] * KEY_COUNT
                            for i, img in enumerate(tiles):
                                data = Deck.encode_image(img)
                                if data != saver_sent[i]:
                                    deck.set_key_jpeg(i, data)
                                    saver_sent[i] = data
                            if args.preview and not previewed:
                                board_image(tiles).save(args.preview)
                                log.info(f"preview saved to {args.preview}")
                                previewed = True
                        else:
                            data = Deck.encode_screen(saver.render())
                            if data != saver_sent:
                                deck.set_screen_jpeg(data)
                                saver_sent = data
                            if args.preview and not previewed:
                                saver.render().save(args.preview)
                                log.info(f"preview saved to {args.preview}")
                                previewed = True
                    else:
                        images = paint()
                        if args.preview and not previewed:
                            board_image(images).save(args.preview)
                            log.info(f"preview saved to {args.preview}")
                            previewed = True
                    write_status(True)

                # holds: a holdable key kept down past HOLD_S fires once
                for _, i, k in presses.tick(time.monotonic()):
                    log.info(f"key {i} hold")
                    try:
                        k.hold()
                    except Exception as e:
                        log.info(f"key {i} hold failed: {e}")
                    data = Deck.encode_image(k.render_held())
                    deck.set_key_jpeg(i, data)
                    sent[i] = data

                states = deck.read_keys(0.05)
                if states is not None:
                    if any(states):
                        last_activity = time.monotonic()
                    if saver_active:
                        if any(states):
                            # wake up: back to the board, and swallow this press
                            saver_active = False
                            saver_sent = None
                            log.info("screen saver off")
                            deck.fill_color(0, 0, 0)
                            sent = [None] * KEY_COUNT
                            paint(force=True)
                            pressed = states
                            presses.reset()
                            next_refresh = time.monotonic() + REFRESH_S
                        continue
                    for i, (was, is_now) in enumerate(zip(pressed, states)):
                        if was == is_now:
                            continue
                        pressed[i] = is_now
                        for action, _, k in presses.feed(i, nav.keys()[i], is_now, time.monotonic()):
                            if action == "tap":
                                log.info(f"key {i} tap")
                                try:
                                    k.tap()
                                except Exception as e:  # a bad command must not kill the board
                                    log.info(f"key {i} action failed: {e}")
                            elif action == "release-after-hold":
                                sent[i] = None    # drop the hold feedback
                        if nav.dirty:
                            paint()        # a page changed: redraw everything now
                            continue
                        data = Deck.encode_image(nav.keys()[i].render(is_now))
                        if data != sent[i]:
                            deck.set_key_jpeg(i, data)
                            sent[i] = data
            except OSError as e:
                log.info(f"deck lost ({e}), waiting for it to come back")
                deck.close()
                deck = None
                next_open = time.monotonic() + RECONNECT_S
    except KeyboardInterrupt:
        pass
    finally:
        if deck is not None:
            try:
                if saver.enabled and saver.is_tiles:
                    deck.fill_color(0, 0, 0)
                    for i, img in enumerate(saver.render_tiles(final=True)):
                        deck.set_key_image(i, img)
                elif saver.enabled:
                    deck.set_screen_image(saver.render(final=True))
                else:
                    deck.reset()
            except OSError:
                pass
            deck.close()
        for f in (PID_FILE, STATUS_FILE):
            try:
                os.remove(f)
            except OSError:
                pass
        log.info("bye")


if __name__ == "__main__":
    main()
