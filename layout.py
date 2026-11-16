"""Build the board from keys.json: one Key object per position, the shared
data providers, brightness and the screen saver."""
import logging
import os

from deckhid import KEY_COUNT
from folderkeys import Folder
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


class BuildContext:
    """What the keys of one layout share: the layout file, the session board
    (made on first use), the to-do lists by file, and the providers to refresh."""

    def __init__(self, path, cfg, slots):
        self.path, self.cfg, self.slots = path, cfg, slots
        self.providers = []
        self.lists = {}
        self._board = None

    def board(self):
        if self._board is None:
            self._board = SessionBoard(self.slots or 1)
            self.providers.append(self._board)
            self.providers.append(stats_provider())   # limits: watched for window resets (cyan pulse)
        return self._board

    def todo_key(self, kcfg):
        kcfg = {**self.cfg.get("todo", {}), **kcfg}    # board-wide defaults, then the key's own
        file = os.path.expanduser(kcfg.get("file") or os.path.join(CONFIG_DIR, "todo.md"))
        if file not in self.lists:
            self.lists[file] = TodoList(file, kcfg.get("soon_days", 0))
            if not kcfg.get("file"):   # the sample list only for the default file
                try:
                    self.lists[file].ensure_file()
                except OSError as e:
                    log.info(f"todo: cannot create {file}: {e}")
            self.providers.append(self.lists[file])
        return TodoKey(kcfg, self.lists[file])


def build_keys(keys_cfg, ctx, where="keys.json", reserved=()):
    """KEY_COUNT Key objects from a keys map ({"3": {...}}). Folders call this
    for their own map, with key 0 reserved for the back key."""
    keys = [Blank({}) for _ in range(KEY_COUNT)]
    for idx, kcfg in (keys_cfg or {}).items():
        try:
            i = int(idx)
        except ValueError:
            log.info(f"{where}: ignoring key {idx!r}")
            continue
        if not 0 <= i < KEY_COUNT or i in reserved:
            log.info(f"{where}: ignoring key {idx}, valid keys are {min(set(range(KEY_COUNT)) - set(reserved))}"
                     f"..{KEY_COUNT - 1}")
            continue
        if not isinstance(kcfg, dict):
            continue
        ktype = kcfg.get("type", "command")
        if ktype == "folder":
            keys[i] = Folder(kcfg, build_keys(kcfg.get("keys"), ctx, where=f"{where} folder {idx}", reserved=(0,)))
        elif ktype == "brightness":
            keys[i] = Brightness(kcfg, int(ctx.cfg.get("brightness", 60)), ctx.path)
        elif ktype == "counter":
            keys[i] = CounterKey(ctx.board(), kcfg.get("state", "waiting"))
        elif ktype == "pager":
            keys[i] = PagerKey(ctx.board(), kcfg.get("dir", "next"))
        elif ktype == "stats":
            keys[i] = StatsKey(kcfg, ctx.board())
        elif ktype == "todo":
            keys[i] = ctx.todo_key(kcfg)
        elif ktype in KEY_TYPES:
            keys[i] = KEY_TYPES[ktype](kcfg)
        else:
            log.info(f"{where}: key {idx} has unknown type {ktype!r}")
    return keys


def load_board(path):
    cfg = read_layout(path)
    region = [int(x) for x in cfg.get("sessions", {}).get("keys", []) if 0 <= int(x) < KEY_COUNT]
    ctx = BuildContext(path, cfg, len(region))
    keys = build_keys(cfg.get("keys"), ctx)
    for n, idx in enumerate(region):
        keys[idx] = SessionSlot(ctx.board(), n)
    return keys, int(cfg.get("brightness", 60)), ctx.providers, ScreenSaver(cfg.get("screensaver", {}))
