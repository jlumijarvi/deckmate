"""Talking to the rest of the system: shell commands, detached launches, and
which Linux account currently owns the screen."""
import ctypes
import ctypes.util
import subprocess


def sh(cmd):
    """Run a shell command, return stdout (text), '' on failure."""
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


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
