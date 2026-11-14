#!/usr/bin/env python3
"""Step 2: a Stream Deck board of plain buttons, configured in keys.json.

Key types (see keys.json):
  command  (default)  label, color, run       tap runs the shell command
  clock               shows time and date, updates every second
  mute                target "source" (mic) or "sink" (speaker); shows state, tap toggles
  volume              step (+/- percent); shows the speaker volume, tap changes it
  slack               unread badge for one workspace ("team": id, default: the first
                      workspace found); tap opens that workspace in Slack

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

import ctypes
import ctypes.util

from paths import HERE, PID_FILE, STATUS_FILE, config_path, ensure_dirs

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
RECONNECT_S = 2.0
REFRESH_S = 1.0

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


def draw_centered_lines(d, lines, fg, size=14, top=None, bold=True):
    f = font(size, bold)
    line_h = size + 3
    total = line_h * len(lines)
    y = (KEY_PX - total) / 2 if top is None else top
    for line in lines:
        w = d.textlength(line, font=f)
        d.text(((KEY_PX - w) / 2, y), line, font=f, fill=fg)
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
    dynamic = False  # True: re-render on every refresh tick

    def __init__(self, cfg):
        self.cfg = cfg

    def render(self, pressed):
        raise NotImplementedError

    def tap(self):
        pass


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



STATE_COLORS = {
    "waiting": (245, 166, 35),
    "running": (52, 140, 235),
    "done": (46, 180, 100),
    "stopped": (120, 120, 120),
    "failed": (220, 60, 60),
}


def dim(color, f=0.22):
    return tuple(int(c * f) for c in color)


DEMO = False


def demo_sessions():
    now = time.time()
    mk = lambda name, state, kind, ago, ctx: Session(
        pid=0, session_id="", name=name, cwd="", state=state, waiting_kind=kind,
        since=now - ago, ctx_used=ctx, ctx_window=1_000_000, transcript="")
    return sorted([
        mk("Korg CX-3 web MIDI app", "waiting", "!", 95, 240_000),
        mk("Yamaha CK remaining work", "waiting", "?", 12, 610_000),
        mk("Stream Deck tweaking project overview", "running", "", 240, 120_000),
        mk("Encrypted SSD migration to Ubuntu", "done", "", 4000, 900_000),
        mk("vire", "stopped", "", 90000, 50_000),
        mk("gearbasement", "failed", "", 700, 300_000),
    ], key=lambda s: s.sort_key)


class SessionBoard:
    """Shared scanner; refreshed once per tick, read by every SessionSlot."""

    def __init__(self):
        self.scanner = SessionScanner()
        self.sessions = []

    def refresh(self):
        if DEMO:
            self.sessions = demo_sessions()
            return
        try:
            self.sessions = self.scanner.scan()
        except Exception as e:  # a half-written file must not kill the board
            print(f"session scan failed: {e}")


class SessionSlot(Key):
    dynamic = True

    def __init__(self, board, index):
        super().__init__({})
        self.board = board
        self.index = index

    @property
    def session(self):
        return self.board.sessions[self.index] if self.index < len(self.board.sessions) else None

    def render(self, pressed):
        s = self.session
        if s is None:
            return Image.new("RGB", (KEY_PX, KEY_PX), (12, 12, 14))
        color = STATE_COLORS.get(s.state, (120, 120, 120))
        img, d, fg = tile(dim(color), pressed)
        if not pressed:
            d.rectangle([0, 0, KEY_PX, 4], fill=color)          # state strip
        # info line: waiting marker left, time in state right
        info = font(11)
        if s.waiting_kind:
            d.text((4, 6), s.waiting_kind, font=font(13), fill=color if not pressed else fg)
        t = elapsed_text(s.since)
        d.text((KEY_PX - 4 - d.textlength(t, font=info), 7), t, font=info,
               fill=(200, 200, 200) if not pressed else fg)
        # title
        lines, size = fit_lines(s.name, sizes=(13, 12, 11, 10, 9))
        draw_centered_lines(d, lines, fg, size=size, top=21)
        # context bar
        frac = s.ctx_fraction
        bar = (110, 110, 110) if frac < 0.6 else (245, 166, 35) if frac < 0.85 else (220, 60, 60)
        d.rectangle([0, KEY_PX - 3, KEY_PX, KEY_PX], fill=(30, 30, 30))
        d.rectangle([0, KEY_PX - 3, int(KEY_PX * frac), KEY_PX], fill=bar)
        return img

    def tap(self):
        s = self.session
        if s and s.cwd:
            launch("code " + shlex.quote(s.cwd))



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
        draw_centered_lines(d, lines, fg, size=size, top=KEY_PX - size - 8)
        return img

    def tap(self):
        ws = self.workspace()
        if ws:
            launch(f"xdg-open 'slack://open?team={ws['id']}'")


KEY_TYPES = {"command": Command, "clock": Clock, "mute": Mute, "volume": Volume, "slack": Slack}


def load_board(path):
    with open(path) as f:
        cfg = json.load(f)
    keys = [Blank({}) for _ in range(KEY_COUNT)]
    for idx, kcfg in cfg.get("keys", {}).items():
        i = int(idx)
        if not 0 <= i < KEY_COUNT:
            print(f"keys.json: ignoring key {idx}, valid keys are 0..{KEY_COUNT - 1}")
            continue
        ktype = kcfg.get("type", "command")
        if ktype not in KEY_TYPES:
            print(f"keys.json: key {idx} has unknown type {ktype!r}")
            continue
        keys[i] = KEY_TYPES[ktype](kcfg)
    providers = []
    region = cfg.get("sessions", {}).get("keys", [])
    if region:
        board = SessionBoard()
        providers.append(board)
        for n, idx in enumerate(region):
            if 0 <= int(idx) < KEY_COUNT:
                keys[int(idx)] = SessionSlot(board, n)
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
                print(f"screensaver: cannot load {self.image_setting}: {e}; using the clock")
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
    print(f"connected to {node} (serial {deck.serial()}, firmware {deck.firmware()})")
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

    if args.config is None:
        args.config = config_path()
    print(f"layout: {args.config}")
    keys, brightness, providers, saver = load_board(args.config)
    config_mtime = os.path.getmtime(args.config)
    ensure_dirs()
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    last_status = None

    def write_status(connected):
        """A small file for the tray icon: is the deck connected, how many sessions wait."""
        nonlocal last_status
        counts = {"waiting": 0, "running": 0, "done": 0}
        for p in providers:
            for s in getattr(p, "sessions", []):
                if s.state in counts:
                    counts[s.state] += 1
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

    def paint(force=False):
        """Render every key, send those that changed."""
        for p in providers:
            p.refresh()
        images = [k.render(pressed[i]) for i, k in enumerate(keys)]
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
                    print("config reloaded")
                    if deck is not None:
                        deck.set_brightness(brightness)
                        deck.set_sleep_timeout(saver.sleep_s)
                    sent = [None] * KEY_COUNT
                    saver_sent = None
                except (OSError, ValueError) as e:
                    print(f"config reload failed: {e}")

            # another account owns the screen: let go of the deck, its own deck.py takes over
            if now >= next_seat_check:
                next_seat_check = now + 2.0
                active = seat_active_uid()
                inactive = active is not None and active != os.getuid()
                if inactive and not seat_paused:
                    print("another account is active, releasing the deck")
                    seat_paused = True
                    if deck is not None:
                        deck.close()
                        deck = None
                elif seat_paused and not inactive:
                    print("this account is active again")
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
                        print("no deck found, retrying...")
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
                    print(f"open failed: {e}")
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
                    print("screen saver on")

                if now >= next_refresh:
                    next_refresh = now + REFRESH_S
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
                                print(f"preview saved to {args.preview}")
                                previewed = True
                        else:
                            data = Deck.encode_screen(saver.render())
                            if data != saver_sent:
                                deck.set_screen_jpeg(data)
                                saver_sent = data
                            if args.preview and not previewed:
                                saver.render().save(args.preview)
                                print(f"preview saved to {args.preview}")
                                previewed = True
                    else:
                        images = paint()
                        if args.preview and not previewed:
                            board_image(images).save(args.preview)
                            print(f"preview saved to {args.preview}")
                            previewed = True
                    write_status(True)

                states = deck.read_keys(0.05)
                if states is not None:
                    if any(states):
                        last_activity = time.monotonic()
                    if saver_active:
                        if any(states):
                            # wake up: back to the board, and swallow this press
                            saver_active = False
                            saver_sent = None
                            print("screen saver off")
                            deck.fill_color(0, 0, 0)
                            sent = [None] * KEY_COUNT
                            paint(force=True)
                            pressed = states
                            next_refresh = time.monotonic() + REFRESH_S
                        continue
                    for i, (was, is_now) in enumerate(zip(pressed, states)):
                        if was != is_now:
                            pressed[i] = is_now
                            if is_now:
                                print(f"key {i} tap")
                                try:
                                    keys[i].tap()
                                except Exception as e:  # a bad command must not kill the board
                                    print(f"key {i} action failed: {e}")
                            data = Deck.encode_image(keys[i].render(is_now))
                            deck.set_key_jpeg(i, data)
                            sent[i] = data
            except OSError as e:
                print(f"deck lost ({e}), waiting for it to come back")
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
        print("bye")


if __name__ == "__main__":
    main()
