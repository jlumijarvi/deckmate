"""Keys for live Claude Code sessions: the shared SessionBoard, one slot per
session, the counters, the pagers and the page of dismissed sessions."""
import logging
import shlex
import time

from PIL import Image, ImageDraw

from board import SessionView, StateStore, breath, detect_pulses, scroll_offset
from deckhid import KEY_COUNT, KEY_PX
from gitinfo import GitWatcher, dot_kind
from keys import REFRESH_S, SCROLL_PX_S, Blank, Key
from pages import NavKey, nav
from paths import STATE_FILE
from render import STATE_COLORS, code_mark, dim, draw_centered_lines, fit_lines, font, tile
from sessions import Session, SessionScanner, elapsed_text
from system import launch

log = logging.getLogger("deck")


DEMO = False


def demo_sessions():
    now = time.time()
    phase = int(time.monotonic() // 10) % 3   # the first session cycles running -> done -> waiting
    mk = lambda name, state, kind, ago, ctx: Session(
        pid=0, session_id="demo-" + name, name=name, cwd="", state=state, waiting_kind=kind,
        since=now - ago, ctx_used=ctx, ctx_window=1_000_000, transcript="")
    return sorted([
        mk("Web MIDI synth editor", ("running", "done", "waiting")[phase], ("", "", "!")[phase], 95, 240_000),
        mk("Invoice service remaining work", "waiting", "?", 12, 610_000),
        mk("Deckmate project overview", "running", "", 240, 120_000),
        mk("Laptop migration to Ubuntu", "done", "", 4000, 900_000),
        mk("blog", "stopped", "", 90000, 50_000),
        mk("garage-sale", "failed", "", 700, 300_000),
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
    code_mark(d, pressed)
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
        code_mark(d, pressed)
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
    """prev: previous session page; hold shows the dismissed sessions.
    next: next page; hold cycles brightness 30 / 60 / 100."""
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
        label = f"{v.page + 1}/{pages}"
        draw_centered_lines(d, [label], (170, 170, 180) if not pressed else fg, size=11, top=50, bold=False)
        code_mark(d, pressed)
        return img

    def tap(self):
        self.board.view.turn(-1 if self.direction == "prev" else 1)
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
