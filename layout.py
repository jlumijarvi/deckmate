"""Build the board from keys.json: one Key object per position, the shared
data providers, brightness and the screen saver."""
import logging
import os

from deckhid import KEY_COUNT
from keys import Blank, Brightness, Clock, Command, Mute, Volume
from mediakeys import Media
from paths import CONFIG_DIR, read_layout
from saver import ScreenSaver
from sessionkeys import CounterKey, PagerKey, SessionBoard, SessionSlot
from slackkey import Slack
from statskeys import StatsKey, stats_provider
from todo import TodoList
from todokeys import TodoKey
from vpnkey import Vpn

log = logging.getLogger("deck")


KEY_TYPES = {"command": Command, "clock": Clock, "mute": Mute, "volume": Volume, "slack": Slack, "media": Media,
             "vpn": Vpn}


def load_board(path):
    cfg = read_layout(path)
    keys = [Blank({}) for _ in range(KEY_COUNT)]
    for idx, kcfg in cfg.get("keys", {}).items():
        i = int(idx)
        if not 0 <= i < KEY_COUNT:
            log.info(f"keys.json: ignoring key {idx}, valid keys are 0..{KEY_COUNT - 1}")
            continue
        ktype = kcfg.get("type", "command")
        if ktype in ("todo", "counter", "pager", "stats"):
            keys[i] = (ktype, kcfg)  # resolved below, once the shared objects exist
            continue
        if ktype == "brightness":
            keys[i] = Brightness(kcfg, int(cfg.get("brightness", 60)), path)
            continue
        if ktype not in KEY_TYPES:
            log.info(f"keys.json: key {idx} has unknown type {ktype!r}")
            continue
        keys[i] = KEY_TYPES[ktype](kcfg)
    providers = []
    region = [int(x) for x in cfg.get("sessions", {}).get("keys", []) if 0 <= int(x) < KEY_COUNT]
    board = None
    if region or any(isinstance(k, tuple) and k[0] in ("counter", "pager", "stats") for k in keys):
        board = SessionBoard(len(region) or 1)
        providers.append(board)
        for n, idx in enumerate(region):
            keys[idx] = SessionSlot(board, n)
    for i, k in enumerate(keys):
        if isinstance(k, tuple) and k[0] == "counter":
            keys[i] = CounterKey(board, k[1].get("state", "waiting"))
        elif isinstance(k, tuple) and k[0] == "pager":
            keys[i] = PagerKey(board, k[1].get("dir", "next"))
        elif isinstance(k, tuple) and k[0] == "stats":
            keys[i] = StatsKey(k[1], board)
    if board is not None:
        providers.append(stats_provider())   # limits: watched for window resets (cyan pulse)
    defaults = cfg.get("todo", {})           # optional board-wide defaults for todo keys
    lists = {}                               # one TodoList per file, shared by keys on the same file
    for i, k in enumerate(keys):
        if isinstance(k, tuple) and k[0] == "todo":
            kcfg = {**defaults, **k[1]}
            file = os.path.expanduser(kcfg.get("file") or os.path.join(CONFIG_DIR, "todo.md"))
            if file not in lists:
                lists[file] = TodoList(file, kcfg.get("soon_days", 0))
                if not kcfg.get("file"):   # the sample list only for the default file
                    try:
                        lists[file].ensure_file()
                    except OSError as e:
                        log.info(f"todo: cannot create {file}: {e}")
                providers.append(lists[file])
            keys[i] = TodoKey(kcfg, lists[file])
    return keys, int(cfg.get("brightness", 60)), providers, ScreenSaver(cfg.get("screensaver", {}))
