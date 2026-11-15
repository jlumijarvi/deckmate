"""Where the program keeps its files, for both the git checkout and the installed copy.

  program files   next to this file (/usr/lib/deckmate when installed)
  config          ~/.config/deckmate/keys.json   (per Linux account)
  runtime         $XDG_RUNTIME_DIR/deckmate/     (pid and status files)
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "jlumijarvi/deckmate"

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "deckmate")
CONFIG = os.path.join(CONFIG_DIR, "keys.json")
EXAMPLE = os.path.join(HERE, "keys.example.json")

RUNTIME_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/deckmate-{os.getuid()}", "deckmate")
PID_FILE = os.path.join(RUNTIME_DIR, "deck.pid")
TRAY_PID_FILE = os.path.join(RUNTIME_DIR, "tray.pid")
STATUS_FILE = os.path.join(RUNTIME_DIR, "status.json")

STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
LOG_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "deckmate")
LOG_FILE = os.path.join(LOG_DIR, "deck.log")

INSTALLED = HERE.startswith("/usr/")


def version():
    try:
        with open(os.path.join(HERE, "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def ensure_dirs():
    migrate_old_config()
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)


OLD_CONFIG_DIR = os.path.join(os.path.dirname(CONFIG_DIR), "stream-deck")   # the name before 0.6


def migrate_old_config():
    """Copy keys.json, state.json and todo.md from the pre-0.6 folder the first time,
    and point paths inside keys.json at the new folder."""
    if os.path.isdir(CONFIG_DIR) or not os.path.isdir(OLD_CONFIG_DIR):
        return
    try:
        os.makedirs(CONFIG_DIR)
        for name in ("keys.json", "state.json", "todo.md"):
            old = os.path.join(OLD_CONFIG_DIR, name)
            if os.path.exists(old):
                shutil.copy(old, os.path.join(CONFIG_DIR, name))
        if os.path.exists(CONFIG):
            with open(CONFIG) as f:
                text = f.read()
            with open(CONFIG, "w") as f:
                text = text.replace(OLD_CONFIG_DIR, CONFIG_DIR)
                f.write(text.replace("~/.config/stream-deck/", "~/.config/deckmate/"))
        print(f"copied the settings from {OLD_CONFIG_DIR} to {CONFIG_DIR}")
    except OSError as e:
        print(f"could not copy the old settings: {e}")


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


def migrate_layout(cfg):
    """Bring old field names in a layout up to date. Returns True when something changed.
    0.8: the text on a key is its "title" (Elgato's word); it used to be "label"."""
    changed = False
    entries = list(cfg.get("keys", {}).values()) + [cfg.get("todo", {})]
    for k in entries:
        if isinstance(k, dict) and "label" in k:
            v = k.pop("label")
            k.setdefault("title", v)
            changed = True
    return changed


def write_layout(path, cfg):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def read_layout(path):
    """keys.json as a dict. Old field names are migrated and written back once."""
    with open(path) as f:
        cfg = json.load(f)
    if migrate_layout(cfg):
        try:
            write_layout(path, cfg)
            print(f"updated {path} to the current field names")
        except OSError:
            pass
    return cfg
