"""Board logic that does not touch the device, so it can be unit tested:
press/hold detection, the session view (dismissal, filter, paging) and the
small state file that remembers dismissals.
"""
import json
import os

STATE_ORDER = {"waiting": 0, "running": 1, "done": 2, "stopped": 3, "failed": 4}
FINISHED = ("done", "stopped", "failed")


class PressTracker:
    """Turns raw key-down / key-up events into taps and holds.

    Keys that are not holdable fire 'tap' on press. Holdable keys fire 'hold'
    once the key has been down for hold_s, or 'tap' on release before that.
    feed() returns a list of (action, key_index, key_object) tuples.
    """

    def __init__(self, hold_s=0.6):
        self.hold_s = hold_s
        self.down = {}      # index -> (key object, press time)
        self.fired = set()  # indices whose hold already fired

    def feed(self, index, key, is_down, now):
        events = []
        if is_down:
            if getattr(key, "holdable", False):
                self.down[index] = (key, now)
                self.fired.discard(index)
            else:
                events.append(("tap", index, key))
        else:
            entry = self.down.pop(index, None)
            if entry is not None:
                k, _ = entry
                if index in self.fired:
                    self.fired.discard(index)
                    events.append(("release-after-hold", index, k))
                else:
                    events.append(("tap", index, k))
        return events

    def tick(self, now):
        """Call often; returns ('hold', index, key) for keys held long enough."""
        events = []
        for index, (key, t0) in self.down.items():
            if index not in self.fired and now - t0 >= self.hold_s:
                self.fired.add(index)
                events.append(("hold", index, key))
        return events

    def reset(self):
        self.down.clear()
        self.fired.clear()


class StateStore:
    """state.json: {"dismissed": {session_id: state}}. Atomic writes."""

    def __init__(self, path):
        self.path = path
        self.data = {"dismissed": {}}
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.data = {"dismissed": {}, **data}
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass


class SessionView:
    """What the session slots show: sessions minus dismissed ones, optionally
    filtered to one state, split into pages of `slots` keys."""

    def __init__(self, slots, store=None):
        self.slots = max(1, slots)
        self.store = store
        self.dismissed = dict(store.data.get("dismissed", {})) if store else {}
        self.sessions = []
        self.filter = None
        self.page = 0

    # -- input --
    def update(self, sessions):
        """New scan result. Clears a dismissal when the session is in a different
        state than it was dismissed in, and drops dismissals of vanished sessions."""
        self.sessions = sorted(sessions, key=lambda s: (STATE_ORDER.get(s.state, 9), s.name.lower()))
        present = {s.session_id: s for s in self.sessions}
        changed = False
        for sid, state in list(self.dismissed.items()):
            s = present.get(sid)
            if s is None or s.state != state:
                del self.dismissed[sid]
                changed = True
        if changed:
            self._persist()
        self.page = min(self.page, max(0, self.page_count() - 1))

    # -- queries --
    def hidden(self, s):
        return self.dismissed.get(s.session_id) == s.state

    def shown(self):
        out = [s for s in self.sessions if not self.hidden(s)]
        if self.filter:
            out = [s for s in out if s.state == self.filter]
        return out

    def page_count(self):
        return max(1, -(-len(self.shown()) // self.slots))

    def visible(self):
        """The sessions on the current page, one per slot (None for empty slots)."""
        shown = self.shown()
        start = self.page * self.slots
        row = shown[start:start + self.slots]
        return row + [None] * (self.slots - len(row))

    def count(self, state):
        return sum(1 for s in self.sessions if s.state == state and not self.hidden(s))

    def dismissed_sessions(self):
        return [s for s in self.sessions if self.hidden(s)]

    # -- actions --
    def dismiss(self, s):
        if s is None or s.state == "running":
            return False
        self.dismissed[s.session_id] = s.state
        self._persist()
        return True

    def dismiss_all_done(self):
        n = 0
        for s in self.sessions:
            if s.state == "done" and not self.hidden(s):
                self.dismissed[s.session_id] = s.state
                n += 1
        if n:
            self._persist()
        return n

    def restore(self, session_id):
        if session_id in self.dismissed:
            del self.dismissed[session_id]
            self._persist()
            return True
        return False

    def toggle_filter(self, state):
        self.filter = None if self.filter == state else state
        self.page = 0

    def turn(self, step):
        self.page = max(0, min(self.page_count() - 1, self.page + step))

    def _persist(self):
        if self.store is not None:
            self.store.data["dismissed"] = dict(self.dismissed)
            self.store.save()
