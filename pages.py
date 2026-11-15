"""Folders: a stack of pages opened on top of the main board, and the key
that goes back or turns a page."""
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
    """Back / previous page / next page."""

    def __init__(self, symbol, label, action):
        super().__init__({})
        self.symbol, self.label, self.action = symbol, label, action

    def render(self, pressed):
        img, d, fg = tile((36, 36, 44), pressed)
        draw_centered_lines(d, [self.symbol], fg, size=30, top=10)
        draw_centered_lines(d, [self.label], (170, 170, 180) if not pressed else fg, size=11, top=50, bold=False)
        return img

    def press(self):
        self.action()
