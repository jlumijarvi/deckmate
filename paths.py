"""Where the program keeps its files, for both the git checkout and the installed copy.

  program files   next to this file (/usr/lib/stream-deck when installed)
  config          ~/.config/stream-deck/keys.json   (per Linux account)
  runtime         $XDG_RUNTIME_DIR/stream-deck/     (pid and status files)
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "jlumijarvi/stream-deck"

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "stream-deck")
CONFIG = os.path.join(CONFIG_DIR, "keys.json")
EXAMPLE = os.path.join(HERE, "keys.example.json")

RUNTIME_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/stream-deck-{os.getuid()}", "stream-deck")
PID_FILE = os.path.join(RUNTIME_DIR, "deck.pid")
STATUS_FILE = os.path.join(RUNTIME_DIR, "status.json")

STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
LOG_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "stream-deck")
LOG_FILE = os.path.join(LOG_DIR, "deck.log")

INSTALLED = HERE.startswith("/usr/")


def version():
    try:
        with open(os.path.join(HERE, "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def ensure_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)


def config_path():
    """keys.json in the config dir, created from an older checkout copy or the example."""
    ensure_dirs()
    if not os.path.exists(CONFIG):
        old = os.path.join(HERE, "keys.json")
        src = old if os.path.exists(old) else EXAMPLE
        try:
            shutil.copy(src, CONFIG)
            print(f"created {CONFIG} from {src}")
        except OSError:
            return src
    return CONFIG
