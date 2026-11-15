"""A to-do list kept in a Markdown task list, watched for changes. The only write
is mark_done(), which ticks one item's box when a key is held.

File format, one item per line:
  - [ ] Title  @2026-11-20  [notes](~/Documents/notes/x.md)
  - [x] Done items are ignored
Title is everything else on the line; the due date is a YYYY-MM-DD after '@';
the link is a Markdown link (its text is ignored) or a bare path ending in .md.
"""
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime

ITEM_RE = re.compile(r"^\s*[-*+]\s+\[( |x|X)\]\s+(.*)$")
DUE_RE = re.compile(r"@(\d{4}-\d{2}-\d{2})")
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
BARE_PATH_RE = re.compile(r"(?<!\S)((?:~|\.{0,2})?/[^\s]+\.md)(?!\S)")

SAMPLE = """# To do

One item per line. A checked box hides the item. `@date` is the due date and a
Markdown link opens that file when the key is tapped.

- [ ] Try the to-do key on the deck  @{today}
- [ ] Sort out the backup disk  [plan](~/Documents/backup-plan.md)
- [x] Install Deckmate
"""


@dataclass
class Item:
    title: str
    due: date | None
    link: str | None
    line: int

    def days_left(self, today=None):
        if self.due is None:
            return None
        return (self.due - (today or date.today())).days

    @property
    def sort_key(self):
        return (self.due is None, self.due or date.max, self.line)


def parse(text):
    items = []
    for n, raw in enumerate(text.splitlines(), start=1):
        m = ITEM_RE.match(raw)
        if not m or m.group(1).lower() == "x":
            continue
        rest = m.group(2)
        due = None
        dm = DUE_RE.search(rest)
        if dm:
            try:
                due = datetime.strptime(dm.group(1), "%Y-%m-%d").date()
            except ValueError:
                due = None
            rest = rest[:dm.start()] + rest[dm.end():]
        link = None
        lm = LINK_RE.search(rest)
        if lm:
            link = lm.group(2).strip()
            rest = rest[:lm.start()] + rest[lm.end():]
        else:
            bm = BARE_PATH_RE.search(rest)
            if bm:
                link = bm.group(1)
                rest = rest[:bm.start()] + rest[bm.end():]
        title = " ".join(rest.split()).strip(" -:")
        if title:
            items.append(Item(title=title, due=due, link=link, line=n))
    items.sort(key=lambda i: i.sort_key)
    return items


class TodoList:
    """Re-parses the file only when it changed; keeps the last good reading."""

    def __init__(self, path, soon_days=0):
        self.path = os.path.expanduser(path)
        self.soon_days = int(soon_days)
        self._key = None
        self.items = []
        self.exists = False

    def ensure_file(self):
        if not os.path.exists(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                f.write(SAMPLE.format(today=date.today().isoformat()))

    def refresh(self):
        try:
            st = os.stat(self.path)
        except OSError:
            self.exists = False
            self.items = []
            return self.items
        self.exists = True
        key = (st.st_size, st.st_mtime_ns)
        if key == self._key:
            return self.items
        try:
            with open(self.path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return self.items
        self._key = key
        self.items = parse(text)
        return self.items

    def mark_done(self, item):
        """Tick the item's box in the file: '- [ ]' -> '- [x]' on its line. Returns
        True when the line was found unchanged and rewritten."""
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.read().split("\n")
        except OSError:
            return False
        if item.line - 1 >= len(lines):
            return False
        raw = lines[item.line - 1]
        m = ITEM_RE.match(raw)
        if not m or m.group(1).lower() == "x" or item.title not in " ".join(m.group(2).split()):
            return False  # the file changed under us; do not touch the wrong line
        box = raw.index("[ ]")
        lines[item.line - 1] = raw[:box] + "[x]" + raw[box + 3:]
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.replace(tmp, self.path)
        except OSError:
            return False
        self._key = None  # re-read on the next refresh
        return True

    # -- summary for the badge key --
    def overdue(self, today=None):
        return [i for i in self.items if i.due and i.days_left(today) < 0]

    def soon(self, today=None):
        return [i for i in self.items if i.due and 0 <= i.days_left(today) <= self.soon_days]

    def urgency(self, today=None):
        """'overdue' | 'soon' | 'ok' | 'empty'"""
        if not self.items:
            return "empty"
        if self.overdue(today):
            return "overdue"
        if self.soon(today):
            return "soon"
        return "ok"

    def next_due(self):
        dated = [i for i in self.items if i.due]
        return min(dated, key=lambda i: i.due).due if dated else None


def due_text(d, today=None):
    """Short label for a due date: 'today', 'tomorrow', '3d late', 'Mon 20.11.'."""
    today = today or date.today()
    n = (d - today).days
    if n == 0:
        return "today"
    if n == 1:
        return "tomorrow"
    if n < 0:
        return f"{-n}d late"
    if n < 7:
        return d.strftime("%a %-d.%-m.")
    return d.strftime("%-d.%-m.")


if __name__ == "__main__":
    import sys
    t = TodoList(sys.argv[1] if len(sys.argv) > 1 else "~/.config/deckmate/todo.md")
    t.refresh()
    print(f"{t.path}: {len(t.items)} open, urgency {t.urgency()}")
    for i in t.items:
        print(f"  {i.title:40s} {due_text(i.due) if i.due else '':12s} {i.link or ''}")
