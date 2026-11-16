"""Data for the stats key and page: how much is left of the plan limits, and
tokens used today across the transcripts on this account. Read-only.

Limits come from Claude Code's own cache in ~/.claude.json
(cachedUsageUtilization: session, weekly, and a weekly limit scoped to one
model, each with the time the window resets). The Claude desktop app's
~/.config/Claude/plan-usage-history.json is the fallback for session and week
when it is the newer of the two; it is only written while that app runs.
Both are undocumented files of those programs.
"""
import glob
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime

CLAUDE_JSON = os.path.expanduser("~/.claude.json")
PLAN_USAGE = os.path.expanduser("~/.config/Claude/plan-usage-history.json")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
STALE_S = 2 * 3600     # older data is shown greyed; its age is shown from AGE_SHOWN_S on
AGE_SHOWN_S = 10 * 60


@dataclass
class Limit:
    name: str                 # session | week | scoped
    label: str                # what the key says: session, week, or the model's name
    used: int | None          # percent used
    resets_at: float | None   # epoch seconds when the window ends
    source: str               # "code" (Claude Code's cache) or "desktop" (the desktop app)
    at: float | None          # epoch seconds when the number was sampled

    @property
    def idle(self):
        """No window running: the source reports nothing used and no reset time.
        Claude Code's cache says this for the session limit even mid-session, so
        the number is shown as unknown rather than as 100% left."""
        return self.resets_at is None and not self.used

    @property
    def left(self):
        if self.used is None or self.idle:
            return None
        return max(0, 100 - int(self.used))


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_usage_cache(cache):
    """Limits from Claude Code's cachedUsageUtilization: session, week, and the
    first model-scoped weekly limit (labelled with the model's display name)."""
    cache = cache or {}
    ut = cache.get("utilization") or {}
    at = (cache.get("fetchedAtMs") or 0) / 1000.0 or None
    out = {}
    for lim in ut.get("limits") or []:
        if not isinstance(lim, dict):
            continue
        kind, pct, reset = lim.get("kind"), lim.get("percent"), parse_iso(lim.get("resets_at"))
        if kind == "session":
            out["session"] = Limit("session", "session", pct, reset, "code", at)
        elif kind == "weekly_all":
            out["week"] = Limit("week", "week", pct, reset, "code", at)
        elif kind == "weekly_scoped" and "scoped" not in out:
            model = ((lim.get("scope") or {}).get("model") or {}).get("display_name") or "model"
            out["scoped"] = Limit("scoped", model, pct, reset, "code", at)
    for name, key in (("session", "five_hour"), ("week", "seven_day")):   # older caches
        v = ut.get(key)
        if name not in out and isinstance(v, dict):
            out[name] = Limit(name, name, v.get("utilization"), parse_iso(v.get("resets_at")), "code", at)
    return out


class CachedFile:
    """A JSON file re-read only when its size or mtime changes; the last good
    content is kept over a half-written one."""

    def __init__(self, path):
        self.path = path
        self._key = None
        self.data = None

    def read(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return self.data
        key = (st.st_size, st.st_mtime_ns)
        if key == self._key:
            return self.data
        try:
            with open(self.path) as f:
                self.data = json.load(f)
            self._key = key
        except (OSError, ValueError):
            pass
        return self.data


class PlanUsage:
    """The desktop app's samples: five-hour (fh) and seven-day (sd) percent used."""

    def __init__(self, path=PLAN_USAGE):
        self.file = CachedFile(path)
        self.fh = None
        self.sd = None
        self.sampled_at = None

    def refresh(self):
        data = self.file.read()
        samples = data.get("samples") if isinstance(data, dict) else data
        if not samples:
            return
        self.apply(samples)

    def apply(self, samples):
        last = samples[-1]
        u = last.get("u") or {}
        self.fh, self.sd = u.get("fh"), u.get("sd")
        self.sampled_at = (last.get("t") or 0) / 1000.0 or None

    def limit(self, name):
        used = self.fh if name == "session" else self.sd
        if used is None:
            return None
        return Limit(name, name, used, None, "desktop", self.sampled_at)


def age_text(seconds):
    if seconds is None:
        return "no data"
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} d"


class Usage:
    """The limits as the board shows them: Claude Code's cache merged with the
    desktop app's samples (newer wins for session and week), plus window resets
    worked out from resets_at rather than guessed from a drop in the numbers."""

    def __init__(self, cache_path=CLAUDE_JSON, desktop_path=PLAN_USAGE, clock=time.time):
        self.cache = CachedFile(cache_path)
        self.desktop = PlanUsage(desktop_path)
        self.clock = clock
        self.limits = {}          # name -> Limit
        self.new_window = {}      # name -> True from the reset until newer data arrives
        self.reset_events = []    # names whose window ended since the last refresh
        self._window_end = {}     # name -> the resets_at last seen
        self._started = False

    def refresh(self):
        self.reset_events = []
        data = self.cache.read()
        merged = parse_usage_cache((data or {}).get("cachedUsageUtilization") if isinstance(data, dict) else None)
        self.desktop.refresh()
        for name in ("session", "week"):
            d = self.desktop.limit(name)
            c = merged.get(name)
            if d is not None and (c is None or (d.at or 0) > (c.at or 0)):
                merged[name] = Limit(name, name, d.used, c.resets_at if c else None, "desktop", d.at)
        now = self.clock()
        for name, lim in merged.items():
            end = self._window_end.get(name)
            if lim.resets_at and (end is None or lim.resets_at > end):
                if end is not None and self._started and not self.new_window.get(name):
                    self.reset_events.append(name)      # a new window, seen in the data first
                self._window_end[name] = end = lim.resets_at
                self.new_window[name] = False
            if end and now >= end and not self.new_window.get(name) and (lim.at or 0) < end:
                self.new_window[name] = True             # the clock passed the end of the window
                if self._started:
                    self.reset_events.append(name)
            if self.new_window.get(name) and lim.at and end and lim.at >= end:
                self.new_window[name] = False            # fresh data from after the reset
        self.limits = merged
        self._started = True

    def left(self, name):
        lim = self.limits.get(name)
        if lim is None:
            return None
        return 100 if self.new_window.get(name) else lim.left

    def age(self, name):
        lim = self.limits.get(name)
        return None if lim is None or lim.at is None else max(0.0, self.clock() - lim.at)

    def stale(self, name):
        a = self.age(name)
        return a is None or a > STALE_S

    def reset_text(self, name):
        lim = self.limits.get(name)
        if lim is None or not lim.resets_at:
            return ""
        fmt = "%H:%M" if name == "session" else "%a %H:%M"
        return time.strftime(fmt, time.localtime(lim.resets_at))


def _local_midnight():
    now = datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def parse_usage_line(line):
    """(message id, timestamp seconds, in, out, cache_read) for an assistant line, else None."""
    try:
        e = json.loads(line)
    except ValueError:
        return None
    if e.get("type") != "assistant":
        return None
    m = e.get("message") or {}
    u = m.get("usage") or {}
    mid = m.get("id")
    if not mid or not u:
        return None
    t = e.get("timestamp")
    try:
        ts = datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() if t else 0
    except ValueError:
        ts = 0
    tin = int(u.get("input_tokens", 0) or 0) + int(u.get("cache_creation_input_tokens", 0) or 0)
    tout = int(u.get("output_tokens", 0) or 0)
    tcache = int(u.get("cache_read_input_tokens", 0) or 0)
    return mid, ts, tin, tout, tcache


class TokenTally:
    """Tokens since local midnight across all transcripts on this account,
    subagent transcripts included (<project>/<session>/subagents/*.jsonl). The
    transcript writes one line per content block with the same usage repeated,
    so entries are counted once per message id. Per-file read offsets make
    refreshes incremental."""

    def __init__(self, projects_dir=PROJECTS_DIR):
        self.projects_dir = projects_dir
        self.day_start = _local_midnight()
        self.offsets = {}        # path -> bytes already read
        self.seen = set()        # message ids counted today
        self.tokens_in = 0
        self.tokens_out = 0
        self.cache_read = 0
        self.messages = 0

    def reset_day(self):
        self.day_start = _local_midnight()
        self.offsets.clear()
        self.seen.clear()
        self.tokens_in = self.tokens_out = self.cache_read = self.messages = 0

    def refresh(self):
        if _local_midnight() != self.day_start:
            self.reset_day()
        for path in glob.glob(os.path.join(self.projects_dir, "**", "*.jsonl"), recursive=True):
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime < self.day_start:
                continue                      # nothing written today
            offset = self.offsets.get(path, 0)
            if st.st_size <= offset:
                if st.st_size < offset:
                    offset = 0                # truncated or rewritten
                else:
                    continue
            self._read_from(path, offset)

    CHUNK = 1 << 20   # read big transcripts a megabyte at a time, not whole

    def _read_from(self, path, offset):
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                while True:
                    data = f.read(self.CHUNK)
                    if not data:
                        break
                    end = data.rfind(b"\n")
                    if end == -1:
                        # a line longer than the chunk: skip ahead to its end
                        rest = f.readline()
                        data = data + rest
                        end = data.rfind(b"\n")
                        if end == -1:
                            break             # unfinished last line: wait for the newline
                    for raw in data[:end].split(b"\n"):
                        if b'"assistant"' not in raw:
                            continue
                        parsed = parse_usage_line(raw.decode("utf-8", "replace"))
                        if parsed is not None:
                            self.add(*parsed)
                    offset += end + 1
                    self.offsets[path] = offset
                    f.seek(offset)            # re-read the partial tail next round
        except OSError:
            return

    def add(self, mid, ts, tin, tout, tcache):
        if ts < self.day_start or mid in self.seen:
            return
        self.seen.add(mid)
        self.messages += 1
        self.tokens_in += tin
        self.tokens_out += tout
        self.cache_read += tcache


class StatsProvider:
    """Board-side wrapper: the limits checked every refresh (cheap stats), the
    token tally kept up to date by a background thread."""

    def __init__(self):
        self.usage = Usage()
        self.tally = TokenTally()
        self._lock = threading.Lock()
        self._snapshot = (0, 0, 0, 0)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            try:
                self.tally.refresh()
                with self._lock:
                    self._snapshot = (self.tally.tokens_in, self.tally.tokens_out,
                                      self.tally.cache_read, self.tally.messages)
            except Exception:
                pass
            time.sleep(2.0)

    def refresh(self):
        self.usage.refresh()

    def tokens(self):
        with self._lock:
            return self._snapshot


def fmt_tokens(n):
    if n >= 10_000_000:
        return f"{n / 1_000_000:.0f}M"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)
