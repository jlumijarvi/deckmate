"""Board logic that does not touch the device, so it can be unit tested:
press/hold detection, the session view (dismissal, filter, paging) and the
small state file that remembers dismissals.
"""
import json
import os
import time

from sessions import STATE_ORDER, Session


class PressTracker:
    """Turns raw key-down / key-up events into taps and holds.

    Keys that are not holdable fire 'press' as soon as they go down. Holdable keys
    fire 'hold' once the key has been down for hold_s, or 'press' on release before that.
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
                events.append(("press", index, key))
        else:
            entry = self.down.pop(index, None)
            if entry is not None:
                k, _ = entry
                if index in self.fired:
                    self.fired.discard(index)
                    events.append(("release-after-hold", index, k))
                else:
                    events.append(("press", index, k))
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
    """state.json: {"dismissed": {session_id: state}, "pinned": {session_id: {name, cwd, gone_at?}}}.
    Atomic writes."""

    def __init__(self, path):
        self.path = path
        self.data = {"dismissed": {}, "pinned": {}}
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.data = {"dismissed": {}, "pinned": {}, **data}
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
    filtered to one state, split into pages of `slots` keys. Pinned sessions
    sort first, cannot be hidden, and stay on the board as "gone" after their
    process has ended, so the folder is one press away."""
    PIN_MODE_S = 5.0

    def __init__(self, slots, store=None):
        self.slots = max(1, slots)
        self.store = store
        self.dismissed = dict(store.data.get("dismissed", {})) if store else {}
        self.pinned = {sid: dict(rec) for sid, rec in (store.data.get("pinned", {}) if store else {}).items()}
        self.sessions = []
        self.filter = None
        self.page = 0
        self.pin_mode_until = 0.0

    # -- input --
    def update(self, sessions):
        """New scan result. Clears a dismissal when the session is in a different
        state than it was dismissed in, and drops dismissals of vanished sessions."""
        sessions = list(sessions)
        live = {s.session_id: s for s in sessions}
        changed = False
        for sid, rec in self.pinned.items():
            s = live.get(sid)
            if s is not None:                     # keep the record current: the title can change
                cwd = getattr(s, "cwd", "")
                if rec.get("name") != s.name or rec.get("cwd") != cwd or "gone_at" in rec:
                    rec.update(name=s.name, cwd=cwd)
                    rec.pop("gone_at", None)
                    changed = True
            else:
                if "gone_at" not in rec:
                    rec["gone_at"] = time.time()
                    changed = True
                sessions.append(Session(pid=0, session_id=sid, name=rec.get("name", ""), cwd=rec.get("cwd", ""),
                                        state="gone", waiting_kind="", since=rec["gone_at"], ctx_used=0,
                                        ctx_window=0, transcript=""))
        self.sessions = sorted(sessions, key=self.sort_key)
        present = {s.session_id: s for s in self.sessions}
        for sid, state in list(self.dismissed.items()):
            s = present.get(sid)
            if s is None or s.state != state:
                del self.dismissed[sid]
                changed = True
        if changed:
            self._persist()
        if self.filter and self.count(self.filter) == 0:
            self.filter = None    # nothing left to show: an all-empty board would look broken
        self.page = min(self.page, max(0, self.page_count() - 1))

    def sort_key(self, s):
        return (s.session_id not in self.pinned, STATE_ORDER.get(s.state, 9), s.name.lower())

    # -- queries --
    def hidden(self, s):
        return s.session_id not in self.pinned and self.dismissed.get(s.session_id) == s.state

    def is_pinned(self, s):
        return s is not None and s.session_id in self.pinned

    def pin_mode(self, now):
        """True while the next-pager hold has the slots toggling pins instead of opening folders."""
        return now < self.pin_mode_until

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
        if s is None or s.state == "running" or self.is_pinned(s):
            return False
        self.dismissed[s.session_id] = s.state
        self._persist()
        return True

    def dismiss_all_done(self):
        n = 0
        for s in self.sessions:
            if s.state == "done" and not self.hidden(s) and not self.is_pinned(s):
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

    def start_pin_mode(self, now):
        self.pin_mode_until = now + self.PIN_MODE_S

    def toggle_pin(self, s):
        """Pin a session, or unpin it. Returns True when it is pinned afterwards."""
        if s is None:
            return False
        if s.session_id in self.pinned:
            del self.pinned[s.session_id]
            if s.state == "gone":                 # nothing keeps it on the board now
                self.sessions = [x for x in self.sessions if x.session_id != s.session_id]
            pinned = False
        else:
            if s.state == "gone":
                return False
            self.pinned[s.session_id] = {"name": s.name, "cwd": getattr(s, "cwd", "")}
            pinned = True
        self.sessions = sorted(self.sessions, key=self.sort_key)
        self.page = min(self.page, max(0, self.page_count() - 1))
        self._persist()
        return pinned

    def toggle_filter(self, state):
        """Press on a counter: show only that state, or show everything again. A
        counter at zero does nothing, so the board can never filter down to nothing."""
        if self.filter != state and self.count(state) == 0:
            return
        self.filter = None if self.filter == state else state
        self.page = 0

    def turn(self, step):
        self.page = max(0, min(self.page_count() - 1, self.page + step))

    def _persist(self):
        if self.store is not None:
            self.store.data["dismissed"] = dict(self.dismissed)
            self.store.data["pinned"] = {sid: dict(rec) for sid, rec in self.pinned.items()}
            self.store.save()


# --- animation helpers (pure functions of time) ---------------------------------

def detect_pulses(previous, sessions):
    """Compare the last known state per session with the new scan. Returns
    ([(session, new_state)], new_states_dict). A pulse is due when a known session
    becomes done, waiting, failed, stopped or gone; starting to run and first
    sightings are silent."""
    pulses = []
    current = {s.session_id: s.state for s in sessions}
    for s in sessions:
        before = previous.get(s.session_id)
        if before is not None and before != s.state and s.state in ("done", "waiting", "failed", "stopped", "gone"):
            pulses.append((s, s.state))
    return pulses, current


class Effects:
    """Short visual effects: a colour band sweeping across the columns and a glow
    on one key. Pure bookkeeping; the renderer asks what to draw at time t."""
    SWEEP_S = 0.6
    GLOW_S = 1.0

    def __init__(self, cols=5):
        self.cols = cols
        self.items = []   # (start, kind, color, key_index)

    def pulse(self, now, color, key_index=None):
        self.items.append((now, "sweep", color, None))
        if key_index is not None:
            self.items.append((now, "glow", color, key_index))

    def active(self, now):
        self.items = [i for i in self.items if now - i[0] < max(self.SWEEP_S, self.GLOW_S)]
        return bool(self.items)

    def overlays(self, now, key_index):
        """[(kind, color, strength 0..1)] to draw on key_index at time now."""
        out = []
        col = key_index % self.cols
        for start, kind, color, idx in self.items:
            t = now - start
            if kind == "sweep" and 0 <= t < self.SWEEP_S:
                pos = t / self.SWEEP_S * self.cols          # 0..cols, the band's leading edge
                dist = pos - col                             # how far the band is past this column
                if 0 <= dist < 1.6:
                    out.append(("band", color, 1.0 - dist / 1.6))
            elif kind == "glow" and idx == key_index and 0 <= t < self.GLOW_S:
                out.append(("glow", color, 1.0 - t / self.GLOW_S))
        return out


def scroll_offset(elapsed, text_w, view_w, speed=30.0, pause=2.0, gap=24.0, step=2):
    """Horizontal offset for a scrolling title, in pixels, quantised to `step`.
    Stays at 0 for `pause` seconds, scrolls left at `speed` px/s until the text
    plus `gap` has passed, then repeats. Returns 0 when the text fits."""
    if text_w <= view_w:
        return 0
    travel = text_w + gap
    cycle = pause + travel / speed
    t = elapsed % cycle
    if t < pause:
        return 0
    off = (t - pause) * speed
    off = int(off // step) * step
    return off if off < travel else 0


def breath(now, period=3.0, low=0.16, high=0.30, steps=12):
    """A slow brightness factor between low and high, quantised so a key is only
    re-sent `steps` times per period."""
    import math
    phase = (now % period) / period
    v = (1 - math.cos(phase * 2 * math.pi)) / 2       # 0..1..0
    q = round(v * steps) / steps
    return low + (high - low) * q
