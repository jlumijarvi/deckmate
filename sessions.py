"""Find live Claude Code sessions and work out what each one is doing.

Everything is read from files Claude Code writes locally:
  ~/.claude/sessions/<pid>.json          one per running process (title, cwd, busy/idle)
  ~/.claude/projects/<cwd-mangled>/<sessionId>.jsonl   the transcript

States:
  waiting  idle, and the last assistant message stopped at a tool call that has no
           result yet: a permission prompt ('!') or a question / plan approval ('?')
  running  the process reports busy
  done     idle and the last turn ended normally
  stopped  the registry file is there but the process is gone
  failed   idle and the last assistant message is an API error

Run `python3 sessions.py` to print the sessions found.
"""
import glob
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

SESSIONS_DIR = os.path.expanduser("~/.claude/sessions")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
TAIL_BYTES = 256 * 1024

STATE_ORDER = {"waiting": 0, "running": 1, "done": 2, "stopped": 3, "failed": 4}
QUESTION_TOOLS = {"AskUserQuestion", "ExitPlanMode"}


@dataclass
class Session:
    pid: int
    session_id: str
    name: str
    cwd: str
    state: str            # waiting | running | done | stopped | failed
    waiting_kind: str     # '?' or '!' while waiting, else ''
    since: float          # epoch seconds the state began
    ctx_used: int
    ctx_window: int
    transcript: str

    @property
    def sort_key(self):
        return (STATE_ORDER.get(self.state, 9), self.name.lower())

    @property
    def ctx_fraction(self):
        return min(1.0, self.ctx_used / self.ctx_window) if self.ctx_window else 0.0


# --- processes ---------------------------------------------------------------

def proc_start(pid):
    """Start time of a process in clock ticks since boot (field 22 of /proc/pid/stat), or None."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read()
    except OSError:
        return None
    # the command name is in parentheses and may contain spaces; fields start after ')'
    fields = stat[stat.rindex(")") + 2:].split()
    return fields[19]  # field 22 overall; fields[0] is field 3


def is_alive(pid, proc_start_recorded):
    start = proc_start(pid)
    if start is None:
        return False
    return str(proc_start_recorded) == start


# --- transcript --------------------------------------------------------------

def transcript_path(cwd, session_id):
    folder = os.path.join(PROJECTS_DIR, re.sub(r"[^A-Za-z0-9]", "-", cwd))
    p = os.path.join(folder, f"{session_id}.jsonl")
    if os.path.exists(p):
        return p
    hits = glob.glob(os.path.join(PROJECTS_DIR, "*", f"{session_id}.jsonl"))
    return hits[0] if hits else None


def context_window_for(model):
    m = (model or "").lower()
    return 1_000_000 if any(t in m for t in ("fable", "mythos", "[1m]")) else 200_000


def _ts(entry):
    t = entry.get("timestamp")
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _is_human_message(entry):
    """A user entry typed by the person, as opposed to a tool result."""
    if entry.get("type") != "user":
        return False
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return True
    return any(b.get("type") == "text" for b in content or [])


def analyze_tail(lines):
    """Classify the current turn from transcript lines. Returns a dict."""
    conv = []
    ai_title = None
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("type")
        if t in ("user", "assistant") and not e.get("isSidechain"):
            conv.append(e)
        elif t == "ai-title" and e.get("aiTitle"):
            ai_title = e["aiTitle"]

    # the current turn starts at the last human message
    start = 0
    for i in range(len(conv) - 1, -1, -1):
        if _is_human_message(conv[i]):
            start = i
            break
    turn = conv[start:]

    assistants = [e for e in turn if e.get("type") == "assistant"]
    result = {"state": "done", "waiting_kind": "", "since": None,
              "ctx_used": 0, "ctx_window": 200_000, "model": None, "title": ai_title}
    if not assistants:
        # the person just sent something and no reply has been logged yet
        result["state"] = "running"
        result["since"] = _ts(turn[-1]) if turn else None
        return result

    last = assistants[-1]
    msg = last.get("message", {})
    usage = msg.get("usage") or {}
    result["model"] = msg.get("model")
    result["ctx_used"] = sum(int(usage.get(k, 0) or 0) for k in
                             ("input_tokens", "cache_read_input_tokens",
                              "cache_creation_input_tokens", "output_tokens"))
    window = context_window_for(msg.get("model"))
    if result["ctx_used"] > window:
        # the transcript never states the window; a model past 200k is a 1M one
        window = 1_000_000 if result["ctx_used"] <= 1_000_000 else result["ctx_used"]
    result["ctx_window"] = window
    result["since"] = _ts(last)

    if last.get("isApiErrorMessage"):
        result["state"] = "failed"
        return result

    # tool calls in this turn without a result yet
    last_id = msg.get("id")
    pending = {}
    for e in turn:
        m = e.get("message", {})
        content = m.get("content")
        if not isinstance(content, list):
            continue
        if e.get("type") == "assistant":
            for b in content:
                if b.get("type") == "tool_use":
                    pending[b.get("id")] = b.get("name", "")
        else:
            for b in content:
                if b.get("type") == "tool_result":
                    pending.pop(b.get("tool_use_id"), None)
    if pending:
        names = list(pending.values())
        result["state"] = "waiting"
        result["waiting_kind"] = "?" if any(n in QUESTION_TOOLS or n.endswith("wrap_up") for n in names) else "!"
        # the question was asked when the last block of the last message was written
        result["since"] = _ts(last)
    return result


class TranscriptReader:
    """Reads the tail of transcripts, re-parsing only when a file changed."""

    def __init__(self):
        self._cache = {}  # path -> ((size, mtime), result)

    def analyze(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (st.st_size, st.st_mtime_ns)
        cached = self._cache.get(path)
        if cached and cached[0] == key:
            return cached[1]
        with open(path, "rb") as f:
            if st.st_size > TAIL_BYTES:
                f.seek(st.st_size - TAIL_BYTES)
                f.readline()  # drop the partial line
            data = f.read()
        lines = data.decode("utf-8", "replace").splitlines()
        result = analyze_tail(lines)
        self._cache[path] = (key, result)
        return result


# --- registry ----------------------------------------------------------------

class SessionScanner:
    def __init__(self):
        self.reader = TranscriptReader()

    def scan(self):
        sessions = []
        for path in glob.glob(os.path.join(SESSIONS_DIR, "*.json")):
            try:
                with open(path) as f:
                    reg = json.load(f)
            except (OSError, ValueError):
                continue  # being written, or not a registry file
            pid = int(reg.get("pid", 0))
            session_id = reg.get("sessionId", "")
            cwd = reg.get("cwd", "")
            transcript = transcript_path(cwd, session_id) if session_id else None
            info = self.reader.analyze(transcript) if transcript else None
            # a name the person set wins; a derived one yields to the transcript's AI title
            name = reg.get("name") or ""
            if (not name or reg.get("nameSource") == "derived") and info and info.get("title"):
                name = info["title"]
            name = name or os.path.basename(cwd) or session_id[:8]

            alive = is_alive(pid, reg.get("procStart"))
            if not alive:
                state, kind = "stopped", ""
                since = os.path.getmtime(path)
            elif reg.get("status") == "busy":
                state, kind = "running", ""
                since = (reg.get("statusUpdatedAt") or reg.get("updatedAt") or 0) / 1000.0
            elif info:
                state, kind = info["state"], info["waiting_kind"]
                since = info["since"] or (reg.get("statusUpdatedAt") or 0) / 1000.0
            else:
                state, kind = "done", ""
                since = (reg.get("statusUpdatedAt") or 0) / 1000.0

            sessions.append(Session(
                pid=pid, session_id=session_id, name=name, cwd=cwd,
                state=state, waiting_kind=kind, since=since or time.time(),
                ctx_used=info["ctx_used"] if info else 0,
                ctx_window=info["ctx_window"] if info else 200_000,
                transcript=transcript or "",
            ))
        sessions.sort(key=lambda s: s.sort_key)
        return sessions


def elapsed_text(since, now=None):
    s = max(0, int((now or time.time()) - since))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return f"{h}h{m:02d}"
    return f"{s // 86400}d"


if __name__ == "__main__":
    for s in SessionScanner().scan():
        pct = int(round(100 * s.ctx_fraction))
        print(f"{s.state:8s} {s.waiting_kind or ' '} {elapsed_text(s.since):>6s}  {s.name:30s} "
              f"ctx {pct:3d}%  pid {s.pid}  {s.cwd}")
