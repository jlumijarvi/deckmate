"""The stats key and page: what is left of the session, weekly and model-scoped
limits, with optional tiles for tokens today, cache reads and the fullest context."""
from deckhid import KEY_COUNT, KEY_PX
from keys import Blank, Key
from pages import NavKey, nav
from render import CYAN, MARK_W, code_mark, dim, draw_centered_lines, fit_lines, font, tile
from stats import StatsProvider, age_text, fmt_tokens

LIMIT_ORDER = ("session", "week", "scoped")
OPTIONAL_TILES = ("tokens", "cache", "context")
BLUE = (52, 140, 235)
AMBER = (245, 166, 35)
RED = (220, 60, 60)
GREY = (120, 120, 130)

_stats_provider = None


def stats_provider():
    global _stats_provider
    if _stats_provider is None:
        _stats_provider = StatsProvider()
    return _stats_provider


def limit_color(left, new_window=False, stale=False):
    if stale:
        return GREY
    if new_window:
        return CYAN
    if left is None:
        return GREY
    return RED if left < 10 else AMBER if left < 25 else BLUE


def context_color(frac):
    return (110, 110, 110) if frac < 0.6 else AMBER if frac < 0.85 else RED


class StatsKey(Key):
    """Three rows, one per limit: its name, the percent left and a thin bar.
    Bottom right: when the session window resets, or the age of stale data.
    Press opens the stats page. cfg "tiles" lists the optional page tiles."""
    dynamic = True

    def __init__(self, cfg, board):
        super().__init__(cfg)
        self.board = board

    def render(self, pressed):
        usage = stats_provider().usage
        img, d, fg = tile((22, 26, 34), pressed)
        y = 6
        for name in LIMIT_ORDER:
            lim = usage.limits.get(name)
            if lim is None:
                continue
            left = usage.left(name)
            color = limit_color(left, usage.new_window.get(name), usage.stale(name))
            label_fg = (200, 200, 210) if not pressed else fg
            label = "5 h" if name == "session" else lim.label[:7]
            d.text((4, y), label, font=font(9, False), fill=label_fg)
            value = "new" if usage.new_window.get(name) else "—" if left is None else f"{left}%"
            f = font(11)
            d.text((KEY_PX - 4 - d.textlength(value, font=f), y - 1), value, font=f, fill=fg)
            d.rectangle([4, y + 12, KEY_PX - 4, y + 14], fill=(40, 44, 52) if not pressed else (220, 220, 220))
            if left is not None:
                d.rectangle([4, y + 12, 4 + int((KEY_PX - 8) * left / 100), y + 14],
                            fill=color if not pressed else (0, 0, 0))
            y += 18
        if not usage.limits:
            draw_centered_lines(d, ["no data"], fg, size=12, top=28)
        foot, foot_fg = "", (170, 170, 180)
        if usage.stale("session"):
            foot = f"{age_text(usage.age('session'))} old" if usage.age("session") is not None else ""
            foot_fg = GREY
        elif usage.reset_text("session"):
            foot = "→ " + usage.reset_text("session")
        if foot:
            d.text((MARK_W + 2, KEY_PX - 13), foot, font=font(9, False), fill=foot_fg if not pressed else fg)
        code_mark(d, pressed)
        return img

    def press(self):
        nav.push(StatsPage(self.board, self.cfg.get("tiles") or ()))


class StatsPage:
    """Back key, one tile per limit, a tile for the data's age and source, then
    the optional tiles the stats key's settings turned on."""

    def __init__(self, board, tiles=()):
        self.board = board
        self.tiles = [t for t in tiles if t in OPTIONAL_TILES]

    def keys(self):
        st = stats_provider()
        usage = st.usage
        keys = [Blank({}) for _ in range(KEY_COUNT)]
        keys[0] = NavKey("<", "back", nav.pop)
        n = 1
        for name in LIMIT_ORDER:
            lim = usage.limits.get(name)
            if lim is None:
                continue
            left = usage.left(name)
            new = usage.new_window.get(name)
            stale = usage.stale(name)
            color = limit_color(left, new, stale)
            if new:
                foot = "new window"
            elif stale and usage.age(name) is not None:
                foot = f"{age_text(usage.age(name))} old"
            elif usage.reset_text(name):   # "→ 22:36" for the session, "Tue 06:00" for the week
                foot = ("→ " if name == "session" else "") + usage.reset_text(name)
            elif name == "session" and not (lim.used or 0):
                foot = "idle"
            else:
                foot = "left"
            title = {"session": "session · 5 h", "week": "week"}.get(name, lim.label + " · week")
            keys[n] = StatTile(title, "100%" if new else "—" if left is None else f"{left}%", color, foot, border=new)
            n += 1
        newest = max((lim for lim in usage.limits.values() if lim.at), key=lambda lim: lim.at, default=None)
        if newest is not None:
            age = usage.age(newest.name)
            keys[n] = StatTile("Claude Code" if newest.source == "code" else "desktop app", age_text(age),
                               GREY if age is not None and age > 30 * 60 else (90, 110, 130), "data age")
            n += 1
        tin, tout, tcache, msgs = st.tokens()
        for name in self.tiles:
            if n >= KEY_COUNT:
                break
            if name == "tokens":
                keys[n] = StatTile("tokens today", fmt_tokens(tout), (46, 180, 100), f"out · in {fmt_tokens(tin)}")
            elif name == "cache":
                keys[n] = StatTile("cache reads", fmt_tokens(tcache), (120, 120, 140), f"today, {msgs} replies")
            elif name == "context":
                sessions = self.board.sessions if self.board else []
                fullest = max((s for s in sessions if s), key=lambda s: s.ctx_fraction, default=None)
                if fullest:
                    frac = fullest.ctx_fraction
                    keys[n] = StatTile("context", f"{int(frac * 100)}%", context_color(frac), fullest.name)
                else:
                    keys[n] = StatTile("context", "—", (90, 90, 100), "no sessions")
            n += 1
        return keys


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
        code_mark(d, pressed)
        return img
