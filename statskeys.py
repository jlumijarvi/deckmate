"""The stats page and the key that opens it: plan windows, tokens today, cache
reads, fullest context."""
import time

from deckhid import KEY_COUNT, KEY_PX
from keys import Blank, Key
from pages import NavKey, nav
from render import CYAN, MARK_W, code_mark, dim, draw_centered_lines, fit_lines, tile
from stats import StatsProvider, fmt_tokens


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
        sessions = self.board.sessions if self.board else []
        fullest = max((s for s in sessions if s), key=lambda s: s.ctx_fraction, default=None)
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
        draw_centered_lines(d, [self.cfg.get("title") or "stats"], fg, size=12, top=9)
        draw_centered_lines(d, ["—" if left is None else f"{left}%"], fg, size=26, top=24)
        draw_centered_lines(d, ["5h left"], (200, 200, 210) if not pressed else fg, size=10, top=56, bold=False,
                            left=MARK_W)
        code_mark(d, pressed)
        return img

    def press(self):
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
        code_mark(d, pressed)
        return img
