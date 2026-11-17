"""Keys for live Claude Code sessions: the shared SessionBoard, one slot per
session, the counters, the pagers and the page of dismissed sessions.

"Dismissed" in the code is "hidden" everywhere the user sees it (deck, settings,
log, README): the session is off the board and can be brought back."""
import logging
import shlex
import time

from PIL import Image, ImageDraw

from board import SessionView, StateStore, breath, detect_pulses, scroll_offset
from deckhid import KEY_COUNT, KEY_PX
from gitinfo import GitWatcher, dot_kind
from keys import REFRESH_S, SCROLL_PX_S, Blank, Key
from pages import NavKey, back_key, nav
from paths import STATE_FILE
from render import MARK_W, STATE_COLORS, code_mark, dim, draw_centered_lines, fit_lines, font, tile
from sessions import Session, SessionScanner, elapsed_text
from system import launch

log = logging.getLogger("deck")


DEMO = False


def demo_sessions():
    now = time.time()
    phase = int(time.monotonic() // 10) % 3   # the first session cycles running -> done -> waiting
    mk = lambda name, state, kind, ago, ctx, activity="", agents=0: Session(
        pid=0, session_id="demo-" + name, name=name, cwd="", state=state, waiting_kind=kind,
        since=now - ago, ctx_used=ctx, ctx_window=1_000_000, transcript="", activity=activity, agents=agents)
    return sorted([
        mk("Web MIDI synth editor", ("running", "done", "waiting")[phase], ("", "", "!")[phase], 95, 240_000,
           ("Run the unit tests", "", "Run the unit tests")[phase]),
        mk("Invoice service remaining work", "waiting", "?", 12, 610_000, "Approach"),
        mk("Deckmate project overview", "running", "", 240, 120_000, "Edit deck.py", agents=3),
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
    """(lines, size) for a static one-line title, or None when it should scroll
    instead: anything that does not fit on one line at 11 px or more."""
    lines, size = fit_lines(name, max_lines=1, sizes=(13, 12, 11))
    f = font(size)
    truncated = lines and lines[-1].endswith("…") and not name.endswith("…")
    if not truncated and len(lines) == 1 and f.getlength(lines[0]) <= KEY_PX - 6:
        return lines, size
    return None


def activity_line(s):
    """The second line: subagents while running, else what the session does or asks."""
    if s.state == "running" and s.agents:
        return f"{s.agents} agent" + ("s" if s.agents != 1 else "")
    if s.state in ("running", "waiting"):
        return s.activity
    if s.state == "gone":
        return "gone"
    return ""


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


PIN = "★"


def render_session(s, pressed, hidden=False, scroll_t=0.0, now=None, git=None, filter=None, pinned=False,
                   pin_mode=False):
    """The key image for one session (None -> empty slot, which names the filter
    when one is on so the empty board explains itself). A pinned session wears a
    star top left; in pin mode every session key gets a white frame."""
    if s is None:
        img = Image.new("RGB", (KEY_PX, KEY_PX), (12, 12, 14))
        if filter:
            d = ImageDraw.Draw(img)
            draw_centered_lines(d, [filter, "only"], dim(STATE_COLORS.get(filter, (120, 120, 120)), 0.6),
                                size=11, top=24, bold=False)
        return img
    color = STATE_COLORS.get(s.state, (120, 120, 120))
    factor = 0.12 if hidden else 0.22
    if s.state == "running" and not hidden and not pressed:
        factor = breath(now if now is not None else time.monotonic())   # slow breathing blue
    img, d, fg = tile(dim(color, factor), pressed)
    if not pressed:
        d.rectangle([0, 0, KEY_PX, 4], fill=dim(color, 0.5) if hidden else color)
    info = font(11)
    x = 4
    if pinned:
        d.text((x, 5), PIN, font=font(12), fill=(255, 215, 90) if not pressed else fg)
        x += 15
    if s.waiting_kind:
        d.text((x, 6), s.waiting_kind, font=font(13), fill=color if not pressed else fg)
    t = elapsed_text(s.since)
    d.text((KEY_PX - 4 - d.textlength(t, font=info), 7), t, font=info,
           fill=(200, 200, 200) if not pressed else fg)
    title_fg = (150, 150, 160) if hidden and not pressed else fg
    layout = title_layout(s.name)
    if layout is not None:
        lines, size = layout
        draw_centered_lines(d, lines, title_fg, size=size, top=22)
    else:
        strip, w = title_strip(s.name)
        off = scroll_offset(scroll_t, w, KEY_PX - 6, speed=SCROLL_PX_S)
        view = strip.crop((off, 0, off + KEY_PX - 6, strip.height))
        tinted = Image.new("RGB", view.size, title_fg)
        img.paste(tinted, (3, 22), view)
    activity = activity_line(s)
    if activity and not hidden:
        lines, size = fit_lines(activity, max_lines=1, sizes=(10, 9), width=KEY_PX - 6)
        act_fg = fg if pressed else color if s.state == "waiting" else (190, 190, 200)
        draw_centered_lines(d, lines, act_fg, size=size, top=40, bold=False)
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
    if pin_mode and not pressed:
        d.rectangle([0, 0, KEY_PX - 1, KEY_PX - 1], outline=(255, 255, 255), width=2)
    return img


class SessionSlot(Key):
    dynamic = True
    holdable = True   # hold dismisses

    def __init__(self, board, index):
        super().__init__({})
        self.board = board
        self.index = index
        self.dismissed_ok = False
        self.unpinned = False

    @property
    def session(self):
        return self.board.view.visible()[self.index]

    def render(self, pressed):
        s = self.session
        v = self.board.view
        now = time.monotonic()
        if s is None:
            return render_session(None, pressed, filter=v.filter)
        return render_session(s, pressed, scroll_t=now - self.board.first_seen.get(s.session_id, now), now=now,
                              git=self.board.git.state(s.cwd), pinned=v.is_pinned(s), pin_mode=v.pin_mode(now))

    def press(self):
        s = self.session
        v = self.board.view
        if v.pin_mode(time.monotonic()):
            if s is not None:
                pinned = v.toggle_pin(s)
                log.info(f"{'pinned' if pinned else 'unpinned'}: {s.name}")
                nav.dirty = True
            return
        if s and s.cwd:
            launch("code " + shlex.quote(s.cwd))

    def hold(self):
        s = self.session
        v = self.board.view
        self.unpinned = False
        if v.is_pinned(s):                        # a pinned session is never hidden: the hold unpins it
            v.toggle_pin(s)
            self.unpinned = True
            log.info(f"unpinned: {s.name}")
            nav.dirty = True
            return
        self.dismissed_ok = v.dismiss(s)
        if self.dismissed_ok:
            log.info(f"hidden: {s.name} ({s.state})")
            nav.dirty = True

    def render_held(self):
        s = self.session
        if self.unpinned:
            img, d, fg = tile((40, 40, 50), False)
            draw_centered_lines(d, ["unpinned"], fg, size=14, top=27)
            return img
        if s is not None and s.state == "running":
            img, d, fg = tile((60, 60, 70), False)
            draw_centered_lines(d, ["running,", "not", "hidden"], fg, size=12, top=14)
            return img
        img, d, fg = tile((40, 40, 50), False)
        draw_centered_lines(d, ["hidden"], fg, size=16, top=26)
        return img


class CounterKey(Key):
    """Number of sessions in one state; press filters the board to that state
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

    def press(self):
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
    """prev: previous session page; hold shows the dismissed sessions. next: next
    page; hold starts pin mode, five seconds in which a press on a session key
    pins or unpins it."""
    dynamic = True
    holdable = True

    def __init__(self, board, direction):
        super().__init__({})
        self.board, self.direction = board, direction

    def render(self, pressed):
        v = self.board.view
        pages = v.page_count()
        img, d, fg = tile((36, 36, 44), pressed)
        symbol = "<" if self.direction == "prev" else ">"
        draw_centered_lines(d, [symbol], fg, size=30, top=10)
        label_fg = (170, 170, 180) if not pressed else fg
        if v.filter:   # "waiting" over "1/2", right of the corner mark
            draw_centered_lines(d, [v.filter, f"{v.page + 1}/{pages}"], label_fg, size=10, top=44, bold=False,
                                left=MARK_W)
        else:
            draw_centered_lines(d, [f"{v.page + 1}/{pages}"], label_fg, size=11, top=50, bold=False)
        code_mark(d, pressed)
        return img

    def press(self):
        self.board.view.turn(-1 if self.direction == "prev" else 1)
        nav.dirty = True

    def hold(self):
        if self.direction == "prev":
            nav.push(DismissedPage(self.board))
        else:
            self.board.view.start_pin_mode(time.monotonic())
            nav.dirty = True

    def render_held(self):
        if self.direction == "next":
            img, d, fg = tile((36, 36, 44), False)
            draw_centered_lines(d, [PIN], (255, 215, 90), size=22, top=8)
            draw_centered_lines(d, ["press a", "session"], fg, size=11, top=38, bold=False)
            return img
        return self.render(True)


class DismissedSessionKey(Key):
    """A dismissed session on the dismissed page; press brings it back."""

    def __init__(self, board, s):
        super().__init__({})
        self.board, self.s = board, s

    def render(self, pressed):
        return render_session(self.s, pressed, hidden=True)

    def press(self):
        self.board.view.restore(self.s.session_id)
        log.info(f"unhidden: {self.s.name}")
        nav.dirty = True


class DismissedPage:
    def __init__(self, board):
        self.board = board

    def keys(self):
        keys = [Blank({}) for _ in range(KEY_COUNT)]
        keys[0] = back_key()
        hidden = self.board.view.dismissed_sessions()
        if not hidden:
            keys[2] = Blank({})
            img_key = NavKey("", "nothing hidden", lambda: None)
            keys[2] = img_key
        for n, s in enumerate(hidden[:KEY_COUNT - 1], start=1):
            keys[n] = DismissedSessionKey(self.board, s)
        return keys
