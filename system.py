"""Talking to the rest of the system: shell commands, detached launches, which
Linux account currently owns the screen, and the board's own process (pid files,
the login service), shared by the settings window and the tray icon."""
import ctypes
import ctypes.util
import os
import shlex
import signal
import subprocess
import sys
import time

from paths import HERE, PID_FILE, TRAY_PID_FILE

SERVICE = "deckmate.service"


def sh(cmd):
    """Run a shell command, return stdout (text), '' on failure."""
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def notify(title, body=""):
    """A desktop notification from the board (notify-send; nothing happens without it)."""
    launch(f"notify-send -a Deckmate -i deckmate {shlex.quote(title)} {shlex.quote(body)}")


def launch(cmd):
    """Start a command detached from the deck process so it outlives us."""
    subprocess.Popen(cmd, shell=True, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_libsystemd = None


def seat_active_uid():
    """uid of the account whose session currently owns the screen (seat0), or None
    if that cannot be determined. With several Linux accounts logged in, only the
    active one should drive the deck."""
    global _libsystemd
    if _libsystemd is None:
        name = ctypes.util.find_library("systemd")
        _libsystemd = ctypes.CDLL(name) if name else False
    if _libsystemd:
        uid = ctypes.c_uint()
        session = ctypes.c_char_p()
        if _libsystemd.sd_seat_get_active(b"seat0", ctypes.byref(session), ctypes.byref(uid)) >= 0:
            return uid.value
        return None
    try:  # fallback: logind's seat file
        with open("/run/systemd/seats/seat0") as f:
            for line in f:
                if line.startswith("ACTIVE_UID="):
                    return int(line.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return None


# --- the board process ---------------------------------------------------------

def run(*cmd, timeout=10):
    """Run a command given as arguments, return the CompletedProcess or None."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def pid_from(path):
    try:
        with open(path) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if os.path.exists(f"/proc/{pid}") else None


def claim_pid(path):
    """Write this process's pid to the file. False when the file names a process
    that is still alive: a second copy must not start (two tray icons)."""
    other = pid_from(path)
    if other is not None and other != os.getpid():
        return False
    with open(path, "w") as f:
        f.write(str(os.getpid()))
    return True


def release_pid(path):
    """Remove the pid file, but only when it is still ours: a copy that refused
    to start, or a later process, must not delete the running one's file."""
    try:
        with open(path) as f:
            mine = int(f.read().strip()) == os.getpid()
    except (OSError, ValueError):
        return
    if mine:
        try:
            os.remove(path)
        except OSError:
            pass


def board_pid():
    return pid_from(PID_FILE)


def tray_pid():
    return pid_from(TRAY_PID_FILE)


def service_loaded():
    """True when systemd knows a deckmate.service for this user (the package installs one)."""
    r = run("systemctl", "--user", "show", SERVICE, "-p", "LoadState", "--value")
    return bool(r) and r.stdout.strip() == "loaded"


DEV_UNIT = "deckmate-dev.service"    # dev.sh runs the checkout's board under this name


def dev_board_active():
    """True while dev.sh has the checkout's board on the deck. An install must
    not start the packaged board next to it: two boards on one deck flicker."""
    return sh(f"systemctl --user is-active {DEV_UNIT}").strip() == "active"


def start_board():
    if service_loaded():
        run("systemctl", "--user", "start", SERVICE)
        return
    subprocess.Popen([sys.executable, os.path.join(HERE, "deck.py")], cwd=HERE, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_board(timeout=5.0):
    """Stop the service and the process, and wait up to timeout for it to be gone."""
    pid = board_pid()
    if service_loaded():
        run("systemctl", "--user", "stop", SERVICE)
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    end = time.monotonic() + timeout
    while time.monotonic() < end and os.path.exists(f"/proc/{pid}"):
        time.sleep(0.1)
