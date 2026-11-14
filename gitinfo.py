"""Git state of session folders, gathered in a background thread so the board
never waits on git: is the folder a repository, is the worktree dirty, how many
commits is it ahead of its upstream."""
import subprocess
import threading
import time
from dataclasses import dataclass


@dataclass
class GitState:
    is_repo: bool
    dirty: bool
    ahead: int
    checked: float


def probe(cwd, timeout=5):
    """Run git in cwd. Returns a GitState (is_repo False when not a repository)."""
    def run(*args):
        return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=timeout)
    try:
        r = run("rev-parse", "--is-inside-work-tree")
        if r.returncode != 0 or r.stdout.strip() != "true":
            return GitState(False, False, 0, time.time())
        dirty = bool(run("status", "--porcelain", "--untracked-files=normal").stdout.strip())
        r = run("rev-list", "--count", "@{upstream}..HEAD")
        ahead = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else 0
        return GitState(True, dirty, ahead, time.time())
    except (OSError, subprocess.SubprocessError, ValueError):
        return GitState(False, False, 0, time.time())


def dot_kind(state):
    """What to draw: 'hollow' (dirty), 'filled' (clean but ahead), or None."""
    if state is None or not state.is_repo:
        return None
    if state.dirty:
        return "hollow"
    if state.ahead > 0:
        return "filled"
    return None


class GitWatcher:
    """Call want(cwd) for every folder on the board; state(cwd) returns the last
    probe. A daemon thread re-probes wanted folders every `interval` seconds."""

    def __init__(self, interval=30.0):
        self.interval = interval
        self._lock = threading.Lock()
        self._wanted = {}    # cwd -> last wanted time
        self._states = {}
        self._thread = None

    def want(self, cwd):
        if not cwd:
            return
        with self._lock:
            self._wanted[cwd] = time.time()
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def state(self, cwd):
        with self._lock:
            return self._states.get(cwd)

    def _loop(self):
        while True:
            with self._lock:
                now = time.time()
                # forget folders nobody asked about for a while
                self._wanted = {c: t for c, t in self._wanted.items() if now - t < 10 * self.interval}
                due = [c for c in self._wanted
                       if c not in self._states or now - self._states[c].checked >= self.interval]
            for cwd in due:
                st = probe(cwd)
                with self._lock:
                    self._states[cwd] = st
            time.sleep(1.0)
