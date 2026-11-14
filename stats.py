"""Data for the stats page: plan quota windows as the Claude desktop app last
sampled them, and tokens used today across all transcripts. Read-only."""
import glob
import json
import os
import threading
import time
from datetime import datetime

PLAN_USAGE = os.path.expanduser("~/.config/Claude/plan-usage-history.json")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
RESET_DROP = 20   # a used-percent drop larger than this between samples means a new window


class PlanUsage:
    """Latest five-hour (fh) and seven-day (sd) utilisation in percent, and whether
    a window has just reset (used percent dropped by more than RESET_DROP)."""

    def __init__(self, path=PLAN_USAGE):
        self.path = path
        self._key = None
        self.fh = None
        self.sd = None
        self.sampled_at = None
        self.new_window = {"fh": False, "sd": False}
        self.reset_events = []   # window names whose reset was seen by the last refresh

    def refresh(self):
        self.reset_events = []
        try:
            st = os.stat(self.path)
        except OSError:
            return
        key = (st.st_size, st.st_mtime_ns)
        if key == self._key:
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        self._key = key
        samples = data.get("samples") if isinstance(data, dict) else data
        if not samples:
            return
        self.apply(samples)

    def apply(self, samples):
        last = samples[-1]
        prev = samples[-2] if len(samples) > 1 else None
        u = last.get("u") or {}
        fh, sd = u.get("fh"), u.get("sd")
        for name, value in (("fh", fh), ("sd", sd)):
            before = (prev.get("u") or {}).get(name) if prev else None
            reset = before is not None and value is not None and before - value > RESET_DROP
            if reset:
                self.reset_events.append(name)
            # the marker stays until the next sample arrives
            self.new_window[name] = reset
        self.fh, self.sd = fh, sd
        self.sampled_at = (last.get("t") or 0) / 1000.0 or None

    def left(self, name):
        v = self.fh if name == "fh" else self.sd
        return None if v is None else max(0, 100 - int(v))


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
    """Tokens since local midnight across all transcripts. The transcript writes
    one line per content block with the same usage repeated, so entries are
    counted once per message id. Per-file read offsets make refreshes incremental."""

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
        for path in glob.glob(os.path.join(self.projects_dir, "*", "*.jsonl")):
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

    def _read_from(self, path, offset):
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
        except OSError:
            return
        # keep a trailing partial line for next time
        end = data.rfind(b"\n")
        if end == -1:
            return
        self.offsets[path] = offset + end + 1
        for raw in data[:end].split(b"\n"):
            if b'"assistant"' not in raw:
                continue
            parsed = parse_usage_line(raw.decode("utf-8", "replace"))
            if parsed is None:
                continue
            self.add(*parsed)

    def add(self, mid, ts, tin, tout, tcache):
        if ts < self.day_start or mid in self.seen:
            return
        self.seen.add(mid)
        self.messages += 1
        self.tokens_in += tin
        self.tokens_out += tout
        self.cache_read += tcache


class StatsProvider:
    """Board-side wrapper: plan usage checked every refresh (cheap stat), the
    token tally kept up to date by a background thread."""

    def __init__(self):
        self.plan = PlanUsage()
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
        self.plan.refresh()

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
