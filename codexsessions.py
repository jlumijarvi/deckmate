"""OpenAI Codex sessions, read the same way as Claude Code's: from files only.

Codex (the CLI, 0.169 and later) runs an app-server daemon that owns every
open thread and appends its transcript, the "rollout", under
~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<thread id>.jsonl. The daemon
keeps a live thread's rollout open, so the rollouts among its open files are
the live sessions; its pid is in ~/.codex/app-server-daemon/daemon.pid.
The thread's name is in ~/.codex/session_index.jsonl, its folder in the
rollout's first line (session_meta). States come from the rollout's tail:
task_started opens a turn (running), task_complete or turn_aborted ends it
(done), an error event fails it, and a tool call that asked for escalated
permissions and has no output yet is the approval prompt (waiting, with the
model's justification as the activity line). The context bar uses the last
token_count event. Nothing is written.
"""
import glob
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from sessions import Session

log = logging.getLogger("deck")

CODEX_DIR = os.path.expanduser("~/.codex")
DAEMON_PID_FILE = os.path.join(CODEX_DIR, "app-server-daemon", "daemon.pid")
SESSION_INDEX = os.path.join(CODEX_DIR, "session_index.jsonl")
SESSIONS_DIR = os.path.join(CODEX_DIR, "sessions")
TAIL_BYTES = 256 * 1024
DEFAULT_WINDOW = 400_000

_JUSTIFICATION = re.compile(r'justification\s*:\s*"((?:[^"\\]|\\.)*)"')
_CMD = re.compile(r'\bcmd\s*:\s*"((?:[^"\\]|\\.)*)"')


def daemon_pid(path=DAEMON_PID_FILE):
    try:
        with open(path) as f:
            pid = int((json.load(f) or {}).get("pid", 0))
    except (OSError, ValueError, AttributeError):
        return None
    return pid if pid > 0 and os.path.exists(f"/proc/{pid}") else None


def open_rollouts(pid, fd_dir=None):
    """Rollout files the daemon holds open: the live threads."""
    fd_dir = fd_dir or f"/proc/{pid}/fd"
    out = []
    try:
        for fd in os.listdir(fd_dir):
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target.endswith(".jsonl") and "/sessions/" in target and os.path.basename(target).startswith("rollout-"):
                out.append(target)
    except OSError:
        pass
    return sorted(set(out))


def thread_names(path=SESSION_INDEX):
    """{thread id: name} from the session index; the last entry per thread wins."""
    names = {}
    try:
        with open(path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("id") and d.get("thread_name"):
                    names[d["id"]] = d["thread_name"]
    except OSError:
        pass
    return names


def _ts(entry):
    t = entry.get("timestamp")
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _unescape(s):
    return s.replace('\\"', '"').replace("\\n", " ")


def call_activity(name, text):
    """What a pending tool call is doing, from its input: the justification of an
    escalated command, else the command, else the tool's name."""
    m = _JUSTIFICATION.search(text or "")
    if m:
        return _unescape(m.group(1))
    m = _CMD.search(text or "")
    if m:
        return _unescape(m.group(1))
    return name or ""


def analyze_tail(lines):
    """State of a thread from its rollout's last entries. Returns a dict with
    state, waiting_kind, since, activity, ctx_used, ctx_window."""
    state, kind, since, activity = "done", "", None, ""
    ctx_used, ctx_window = 0, DEFAULT_WINDOW
    pending = {}       # call_id -> (tool name, input text, timestamp) of calls without an output yet
    turn_open = False
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        p = d.get("payload") or {}
        if not isinstance(p, dict):
            continue
        t, pt = d.get("type"), p.get("type")
        if t == "event_msg":
            if pt == "task_started":
                turn_open, pending = True, {}
                state, kind, since, activity = "running", "", _ts(d), ""
            elif pt in ("task_complete", "turn_aborted"):
                turn_open, pending = False, {}
                state, kind, since, activity = "done", "", _ts(d), ""
            elif pt == "error" and turn_open:
                turn_open, pending = False, {}
                state, kind, since, activity = "failed", "", _ts(d), ""
            elif pt == "token_count":
                info = p.get("info") or {}
                last = info.get("last_token_usage") or {}
                ctx_used = int(last.get("input_tokens") or 0) + int(last.get("output_tokens") or 0)
                ctx_window = int(info.get("model_context_window") or ctx_window)
        elif t == "response_item" and turn_open:
            if pt in ("custom_tool_call", "function_call"):
                text = p.get("input") if pt == "custom_tool_call" else p.get("arguments")
                pending[p.get("call_id")] = (p.get("name") or "", text or "", _ts(d))
            elif pt in ("custom_tool_call_output", "function_call_output"):
                pending.pop(p.get("call_id"), None)
    if turn_open and pending:
        name, text, at = list(pending.values())[-1]
        if "require_escalated" in text or "request_user_input" in name:
            state = "waiting"
            kind = "?" if "request_user_input" in name else "!"
            since = at or since
        activity = call_activity(name, text)
    return {"state": state, "waiting_kind": kind, "since": since, "activity": activity,
            "ctx_used": ctx_used, "ctx_window": ctx_window}


def read_meta(path):
    """(thread id, cwd) from the rollout's first line, or (None, '')."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            first = f.readline()
        d = json.loads(first)
        p = d.get("payload") or {}
        if d.get("type") == "session_meta":
            return p.get("id") or p.get("session_id"), p.get("cwd") or ""
    except (OSError, ValueError):
        pass
    return None, ""


def read_tail(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > TAIL_BYTES:
                f.seek(size - TAIL_BYTES)
                f.readline()           # the cut line
            return f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


_ROLLOUT_NAME = re.compile(r"^rollout-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d-(.+)\.jsonl$")


def thread_id_from_name(path):
    """rollout-2026-11-17T20-49-23-<id>.jsonl, or <parent id>_<id> for a fork."""
    m = _ROLLOUT_NAME.match(os.path.basename(path))
    stem = m.group(1) if m else os.path.basename(path)
    return stem.split("_")[-1]


class CodexScanner:
    """Live Codex threads as Session objects, client 'codex'."""

    def __init__(self):
        self._names_at = 0.0
        self._names = {}
        self._warned = False

    def names(self):
        now = time.monotonic()
        if now - self._names_at > 5:
            self._names, self._names_at = thread_names(), now
        return self._names

    def scan(self):
        pid = daemon_pid()
        if pid is None:
            return []
        sessions = []
        names = None
        for path in open_rollouts(pid):
            tid, cwd = read_meta(path)
            if not tid:
                tid = thread_id_from_name(path)
            if names is None:
                names = self.names()
            info = analyze_tail(read_tail(path))
            name = names.get(tid) or os.path.basename(cwd) or tid[:8]
            sessions.append(Session(
                pid=pid, session_id=tid, name=name, cwd=cwd, state=info["state"],
                waiting_kind=info["waiting_kind"], since=info["since"] or os.path.getmtime(path),
                ctx_used=info["ctx_used"], ctx_window=info["ctx_window"], transcript=path,
                activity=info["activity"], client="codex"))
        return sessions


def rollouts_on_disk():
    return sorted(glob.glob(os.path.join(SESSIONS_DIR, "*", "*", "*", "rollout-*.jsonl")))


if __name__ == "__main__":
    pid = daemon_pid()
    print("daemon:", pid or "not running")
    for s in CodexScanner().scan():
        print(f"{s.state:8} {s.waiting_kind:1} {s.name} ({s.cwd}) {s.activity!r} ctx {s.ctx_used}/{s.ctx_window}")
