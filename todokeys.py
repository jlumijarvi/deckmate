"""To-do keys: the badge on the main board, the list as a folder, one key per item."""
import logging
import os
import shlex

from deckhid import KEY_COUNT, KEY_PX
from keys import Blank, Key
from pages import NavKey, back_key, nav
from render import dim, draw_centered_lines, fit_lines, font, tile
from system import launch
from todo import due_text

log = logging.getLogger("deck")


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


def is_web_link(link):
    return link.lower().startswith(("http://", "https://"))


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
        draw_centered_lines(d, [self.cfg.get("title") or "To do"], fg, size=12, top=9)
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

    def press(self):
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

    def press(self):
        link = self.item.link
        if not link:
            return
        if is_web_link(link):
            launch(f"xdg-open {shlex.quote(link)}")
            return
        path = os.path.expanduser(link)
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
        keys[0] = back_key()
        for n, item in enumerate(shown, start=1):
            keys[n] = TodoItemKey(item, self.todo, self.cfg)
        if paged:
            keys[13] = NavKey("<", f"{self.index + 1}/{pages}", lambda: self.turn(-1))
            keys[14] = NavKey(">", f"{self.index + 1}/{pages}", lambda: self.turn(1))
        return keys

    def turn(self, step):
        self.index = max(0, self.index + step)
        nav.dirty = True
