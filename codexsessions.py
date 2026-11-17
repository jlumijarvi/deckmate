"""OpenAI Codex sessions, read the same way as Claude Code's: from files only.

The Codex CLI appends each thread's transcript, the "rollout", under
~/.codex/sessions/YYYY/MM/DD/rollout-<start time>-<thread id>.jsonl, named
with the moment the client started and carrying the working folder in its
first line (session_meta). A live thread is matched to a running codex
client process through those two: the process's start time (/proc) and
folder, against the rollout's name and folder, the rollout being written
since the process started. The thread's name is in
~/.codex/session_index.jsonl. States come from the rollout's tail:
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


_ROLLOUT_NAME = re.compile(r"^rollout-(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d)-(.+)\.jsonl$")


def client_processes(proc="/proc"):
    """[(start epoch seconds, working folder)] of running codex client processes:
    the binary named codex that is not the app server."""
    out = []
    for pid in os.listdir(proc):
        if not pid.isdigit():
            continue
        try:
            with open(f"{proc}/{pid}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
            if not argv or os.path.basename(argv[0]) != b"codex" or argv[1:2] == [b"app-server"]:
                continue
            out.append((os.stat(f"{proc}/{pid}").st_ctime, os.readlink(f"{proc}/{pid}/cwd")))
        except OSError:
            continue
    return out


def name_time(path):
    """The client start time a rollout is named with, as epoch seconds (local time), or None."""
    m = _ROLLOUT_NAME.match(os.path.basename(path))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%S").timestamp()
    except ValueError:
        return None


def recent_rollouts(since, root=SESSIONS_DIR):
    """Rollouts modified since `since`: [(path, mtime)]."""
    out = []
    for path in glob.glob(os.path.join(root, "*", "*", "*", "rollout-*.jsonl")):
        try:
            m = os.path.getmtime(path)
        except OSError:
            continue
        if m >= since:
            out.append((path, m))
    return out


def match(processes, rollouts, slack=5.0):
    """Pair client processes with rollouts: [(path, process start)]. A process
    takes the rollout in its folder written since it started whose name is
    closest to its start time; each rollout goes to one process."""
    taken = set()
    out = []
    for start, cwd in sorted(processes):
        best = None
        for path, mtime, folder, named in rollouts:
            if path in taken or folder != cwd or mtime < start - slack:
                continue
            distance = abs((named or mtime) - start)
            if best is None or distance < best[0]:
                best = (distance, path)
        if best is not None:
            taken.add(best[1])
            out.append((best[1], start))
    return out


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


def thread_id_from_name(path):
    """rollout-2026-11-17T20-49-23-<id>.jsonl, or <parent id>_<id> for a fork."""
    m = _ROLLOUT_NAME.match(os.path.basename(path))
    stem = m.group(2) if m else os.path.basename(path)
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
        processes = client_processes()
        if not processes:
            return []
        earliest = min(start for start, _ in processes) - 5
        rollouts = []
        for path, mtime in recent_rollouts(earliest):
            tid, cwd = read_meta(path)
            rollouts.append((path, mtime, cwd, name_time(path)))
        sessions = []
        names = self.names()
        for path, start in match(processes, rollouts):
            tid, cwd = read_meta(path)
            tid = tid or thread_id_from_name(path)
            info = analyze_tail(read_tail(path))
            name = names.get(tid) or os.path.basename(cwd) or tid[:8]
            sessions.append(Session(
                pid=0, session_id=tid, name=name, cwd=cwd, state=info["state"],
                waiting_kind=info["waiting_kind"], since=info["since"] or os.path.getmtime(path),
                ctx_used=info["ctx_used"], ctx_window=info["ctx_window"], transcript=path,
                activity=info["activity"], client="codex"))
        return sessions


if __name__ == "__main__":
    procs = client_processes()
    print("codex clients:", len(procs))
    for s in CodexScanner().scan():
        print(f"{s.state:8} {s.waiting_kind:1} {s.name} ({s.cwd}) {s.activity!r} ctx {s.ctx_used}/{s.ctx_window}")
