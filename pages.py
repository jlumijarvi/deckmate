"""Folders: a stack of pages opened on top of the main board, and the key
that goes back or turns a page."""
from deckhid import KEY_PX
from keys import Key
from render import draw_centered_lines, tile


class Navigator:
    """The main board plus a stack of pages opened on top of it."""

    def __init__(self, main_keys=None):
        self.main = main_keys
        self.stack = []
        self.dirty = False

    def reset(self, main_keys):
        """A new main board (start, or the layout file changed): drop every open page."""
        self.main = main_keys
        self.stack.clear()
        self.dirty = True

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


nav = Navigator()   # the one navigator; deck.py gives it the main board


class NavKey(Key):
    """Back / previous page / next page. With hold_action, a hold runs that instead."""

    def __init__(self, symbol, label, action, hold_action=None):
        super().__init__({})
        self.symbol, self.label, self.action = symbol, label, action
        self.hold_action = hold_action
        self.holdable = hold_action is not None

    def render(self, pressed):
        img, d, fg = tile((36, 36, 44), pressed)
        draw_centered_lines(d, [self.symbol], fg, size=30, top=10)
        draw_centered_lines(d, [self.label], (170, 170, 180) if not pressed else fg, size=11, top=50, bold=False)
        return img

    def press(self):
        self.action()

    def hold(self):
        if self.hold_action:
            self.hold_action()


class BackKey(NavKey):
    """Key 0 of every folder and page: an arrow, no text. Press goes back one
    level, hold goes to the main board."""

    def render(self, pressed):
        img, d, fg = tile((36, 36, 44), pressed)
        cx, cy = KEY_PX / 2, KEY_PX / 2
        # a left-pointing arrow: head and shaft
        d.polygon([(cx - 18, cy), (cx - 4, cy - 12), (cx - 4, cy + 12)], fill=fg)
        d.rectangle([cx - 6, cy - 4, cx + 18, cy + 4], fill=fg)
        return img


def back_key():
    return BackKey("", "", nav.pop, hold_action=nav.home)


class NoteKey(Key):
    """A line or two of grey text on a page: "nothing today", "offline"."""

    def __init__(self, text):
        super().__init__({})
        self.text = text

    def render(self, pressed):
        img, d, fg = tile((24, 24, 30), pressed)
        draw_centered_lines(d, self.text.split("\n"), (170, 170, 180) if not pressed else fg, size=11, bold=False)
        return img
