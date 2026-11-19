#!/usr/bin/env python3
"""Settings window for the Deckmate board.

Edits keys.json (the running board picks changes up within a second), starts
and stops the board and the tray icon, and shows the connected deck.

    python3 settings.py
"""
import os
import shutil
import subprocess
import sys
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

# must equal the desktop file's name so GNOME groups the window under its own
# dock icon instead of guessing (it ended up in the VS Code group)
APP_ID = "io.github.jlumijarvi.Deckmate"
GLib.set_prgname(APP_ID)

from deckhid import Deck, UnsupportedDeck, find_deck, COLS, KEY_COUNT  # noqa: E402
from slackstate import SlackState  # noqa: E402
from system import board_pid, service_loaded, start_board, stop_board, tray_pid  # noqa: E402
from calendarkeys import DEFAULT_WARN_MIN  # noqa: E402
from vpnkey import vpn_connections  # noqa: E402

from paths import HERE, CONFIG, EXAMPLE, config_path, read_layout, version, write_layout  # noqa: E402


KEY_KINDS = [
    ("empty", "Empty"),
    ("command", "Button"),
    ("folder", "Folder"),
    ("clock", "Clock"),
    ("calendar", "Calendar"),
    ("mute_source", "Mic mute"),
    ("mute_sink", "Speaker mute"),
    ("volume_down", "Volume down"),
    ("volume_up", "Volume up"),
    ("brightness", "Deck brightness"),
    ("vpn", "VPN on/off"),
    ("slack", "Slack unread badge"),
    ("media_playpause", "Spotify: play/pause"),
    ("media_next", "Spotify: next track"),
    ("media_previous", "Spotify: previous track"),
    ("todo", "To-do list"),
    ("session", "Claude: session slot"),
    ("counter_waiting", "Claude: waiting count"),
    ("counter_running", "Claude: running count"),
    ("counter_done", "Claude: done count"),
    ("pager_prev", "Claude: previous page"),
    ("pager_next", "Claude: next page"),
    ("stats", "Claude: stats"),
]
KIND_INDEX = {k: i for i, (k, _) in enumerate(KEY_KINDS)}
KIND_LABEL = dict(KEY_KINDS)
# shown under the type row; the short labels above must fit the dropdown
KIND_HINT = {
    "command": "Runs a shell command when pressed",
    "folder": "Opens a set of keys of its own; key 0 inside goes back, hold it for the main board. "
              "Save, then open the folder to fill it",
    "mute_source": "Toggles the microphone; the key shows its state",
    "mute_sink": "Toggles the speakers; the key shows its state",
    "calendar": "Clock and date with the next meeting from an iCalendar feed; red when one is about to start. "
                "Press opens the meeting's link, hold opens today's list",
    "slack": "Unread count of one Slack workspace: red with mentions or DMs, blue otherwise",
    "media_playpause": "The playing track with its cover; press plays or pauses, or launches Spotify",
    "media_next": "Skips to the next track",
    "media_previous": "Goes back to the previous track",
    "todo": "Open items in a Markdown task list; press opens them as a folder",
    "session": "One running Claude Code session. Press opens its folder in VS Code, hold hides it (or unpins it)",
    "counter_waiting": "Sessions asking you something. Press filters the slots to them",
    "counter_running": "Sessions still working. Press filters the slots to them",
    "counter_done": "Finished sessions. Press filters, hold hides them all",
    "pager_prev": "Previous page of sessions. Hold: hidden sessions",
    "pager_next": "Next page of sessions. Hold: pin mode, then press a session to pin or unpin it",
    "brightness": "Shows the deck's brightness; press steps it 30 / 60 / 100",
    "vpn": "A NetworkManager VPN connection: shows on or off, press switches it",
    "stats": "How much of the session, week and model limits is used; press opens the stats page",
}


# --- config ------------------------------------------------------------------

def load_config():
    for path in (config_path(), EXAMPLE):
        try:
            return read_layout(path)
        except (OSError, ValueError):
            continue
    return {}


def save_config(cfg):
    write_layout(CONFIG, cfg)


def session_keys(cfg):
    return sorted({int(x) for x in cfg.get("sessions", {}).get("keys", [])})


def key_kind(cfg, i):
    """(kind id, raw entry) for key i."""
    if i in session_keys(cfg):
        return "session", {}
    k = cfg.get("keys", {}).get(str(i))
    if not k:
        return "empty", {}
    t = k.get("type", "command")
    if t == "mute":
        return ("mute_source" if k.get("target") == "source" else "mute_sink"), k
    if t == "volume":
        return ("volume_up" if int(k.get("step", 5)) > 0 else "volume_down"), k
    if t in ("clock", "calendar", "slack", "todo", "stats", "brightness", "vpn", "folder"):
        return t, k
    if t == "counter":
        return "counter_" + (k.get("state") or "waiting"), k
    if t == "pager":
        return "pager_" + (k.get("dir") or "next"), k
    if t == "media":
        return "media_" + (k.get("action") or "playpause"), k
    return "command", k


def set_key(cfg, i, kind, label="", color="#444444", run="", team=None, todo=None, tiles=None, connection=None,
            calendar=None):
    keys = cfg.setdefault("keys", {})
    sess = [s for s in session_keys(cfg) if s != i]
    old = keys.pop(str(i), None) or {}
    if kind == "session":
        sess.append(i)
    elif kind == "command":
        keys[str(i)] = {"title": label, "color": color, "run": run}
    elif kind == "folder":   # keep the folder's own keys across a re-save
        inside = old.get("keys") if old.get("type") == "folder" else {}
        keys[str(i)] = {"type": "folder", "title": label, "color": color, "keys": inside or {}}
    elif kind == "clock":
        keys[str(i)] = {"type": "clock"}
    elif kind == "calendar":
        keys[str(i)] = {"type": "calendar", **(calendar or {})}
    elif kind == "slack":
        keys[str(i)] = {"type": "slack", **({"team": team} if team else {})}
    elif kind == "todo":
        keys[str(i)] = {"type": "todo", **(todo or {})}
    elif kind == "stats":
        keys[str(i)] = {"type": "stats", **({"tiles": list(tiles)} if tiles else {})}
    elif kind == "brightness":
        keys[str(i)] = {"type": "brightness"}
    elif kind == "vpn":
        keys[str(i)] = {"type": "vpn", **({"connection": connection} if connection else {}),
                        **({"title": label} if label.strip() else {})}
    elif kind.startswith("counter_"):
        keys[str(i)] = {"type": "counter", "state": kind.split("_", 1)[1]}
    elif kind.startswith("pager_"):
        keys[str(i)] = {"type": "pager", "dir": kind.split("_", 1)[1]}
    elif kind.startswith("media_"):
        keys[str(i)] = {"type": "media", "action": kind.split("_", 1)[1]}
    elif kind == "mute_source":
        keys[str(i)] = {"type": "mute", "target": "source", "title": label or "Mic"}
    elif kind == "mute_sink":
        keys[str(i)] = {"type": "mute", "target": "sink", "title": label or "Speaker"}
    elif kind == "volume_down":
        keys[str(i)] = {"type": "volume", "step": -5}
    elif kind == "volume_up":
        keys[str(i)] = {"type": "volume", "step": 5}
    new = keys.get(str(i))
    if new is not None and old and old.get("type", "command") == new.get("type", "command"):
        keys[str(i)] = {**old, **new}      # keep fields the dialog does not show (steps, step, player, ...)
    if sess or "sessions" in cfg:   # a folder has no session slots: do not give it a "sessions" entry
        cfg.setdefault("sessions", {})["keys"] = sorted(sess)
    cfg["keys"] = {k: keys[k] for k in sorted(keys, key=int)}


def key_summary(cfg, i):
    kind, k = key_kind(cfg, i)
    if kind == "command":
        return k.get("title", "").replace("\n", " ") or "(no title)"
    if kind == "folder":
        return "Folder\n" + (k.get("title") or "")
    if kind in ("mute_source", "mute_sink"):
        return k.get("title") or KIND_LABEL[kind]
    if kind == "session":
        return "Claude\nsession"
    if kind == "slack":
        return "Slack\n" + (k.get("team") or "")
    if kind == "calendar":
        return "Calendar\n" + ("feed set" if k.get("url") else "no feed")
    if kind == "todo":
        return "To-do\n" + os.path.basename(k.get("file") or "todo.md")
    if kind.startswith("counter_"):
        return "Count\n" + kind.split("_", 1)[1]
    if kind == "stats":
        return "Stats"
    if kind == "brightness":
        return "Bright-\nness"
    if kind == "vpn":
        return "VPN\n" + (k.get("connection") or "")
    if kind == "pager_prev":
        return "< page"
    if kind == "pager_next":
        return "page >"
    if kind.startswith("media_"):
        return "Spotify\n" + {"playpause": "play/pause", "next": "next", "previous": "previous"}[kind[6:]]
    if kind == "empty":
        return ""
    return KIND_LABEL[kind]


# --- the board process -------------------------------------------------------

DEFAULT_TODO = "~/.config/deckmate/todo.md"   # the list a to-do key gets without a "file" of its own


def start_tray():
    """Start the top-bar icon again after "Quit tray icon". As a transient user
    service, not a child of this window: it gets the session environment from the
    user manager (a --scope would keep this process's, which breaks GTK 3 when the
    window was started from inside a snap such as VS Code's terminal)."""
    cmd = [sys.executable, os.path.join(HERE, "tray.py")]
    if shutil.which("systemd-run"):
        unit = f"deckmate-tray-{os.getpid()}-{int(time.time())}"
        r = subprocess.run(["systemd-run", "--user", "--quiet", "--collect", "--unit", unit] + cmd,
                           cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if r.returncode == 0:
            return
    subprocess.Popen(cmd, cwd=HERE, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- dialogs -----------------------------------------------------------------

def ask(parent, heading, body, responses, callback, destructive=None, default=None):
    """Adw.AlertDialog wrapper. responses: list of (id, label). callback(response_id)."""
    d = Adw.AlertDialog.new(heading, body)
    for rid, label in responses:
        d.add_response(rid, label)
    if destructive:
        d.set_response_appearance(destructive, Adw.ResponseAppearance.DESTRUCTIVE)
    d.set_default_response(default or responses[0][0])
    d.set_close_response(responses[0][0])
    d.choose(parent, None, lambda dlg, res: callback(dlg.choose_finish(res)))


def choose_image(parent, callback):
    dlg = Gtk.FileDialog()
    dlg.set_title("Choose a picture")
    f = Gtk.FileFilter()
    f.set_name("Pictures")
    f.add_mime_type("image/*")
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(f)
    dlg.set_filters(filters)
    dlg.set_default_filter(f)

    def done(d, res):
        try:
            gfile = d.open_finish(res)
        except GLib.Error:
            return
        if gfile is not None:
            callback(gfile.get_path())
    dlg.open(parent, None, done)


def choose_file(parent, callback, title="Choose a file"):
    dlg = Gtk.FileDialog()
    dlg.set_title(title)

    def done(d, res):
        try:
            gfile = d.open_finish(res)
        except GLib.Error:
            return
        if gfile is not None:
            callback(gfile.get_path())
    dlg.open(parent, None, done)


class KeyDialog(Adw.Dialog):
    def __init__(self, window, index):
        super().__init__()
        self.window = window
        self.index = index
        self.set_title(f"Key {index}  (row {index // COLS + 1}, column {index % COLS + 1})")
        self.set_content_width(440)

        self.cfg = window.level_cfg()
        kind, k = key_kind(self.cfg, index)
        self.is_folder = k.get("type") == "folder"
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        page.add(group)

        # session slots live on the main board only
        self.kinds = [kk for kk in KEY_KINDS if not window.level or kk[0] != "session"]
        self.kind_row = Adw.ComboRow(title="Type")
        self.kind_row.set_model(Gtk.StringList.new([label for _, label in self.kinds]))
        self.kind_row.set_selected(next((n for n, kk in enumerate(self.kinds) if kk[0] == kind), 0))
        self.kind_row.connect("notify::selected", lambda *_: self.update_rows())
        group.add(self.kind_row)

        self.open_row = Adw.ActionRow(title="Keys inside the folder",
                                      subtitle=f"{len((k.get('keys') or {}))} set" if self.is_folder else "")
        btn = Gtk.Button(label="Open folder")
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: (self.close(), self.window.enter_folder(self.index)))
        self.open_row.add_suffix(btn)
        group.add(self.open_row)

        self.hint = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"],
                              margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        group.add(self.hint)

        self.label_row = Adw.EntryRow(title="Title (use \\n for a line break)")
        self.label_row.set_text(k.get("title", "").replace("\n", "\\n"))
        group.add(self.label_row)

        self.color_row = Adw.ActionRow(title="Colour")
        self.color_btn = Gtk.ColorDialogButton.new(Gtk.ColorDialog())
        rgba = Gdk.RGBA()
        rgba.parse(k.get("color", "#444444"))
        self.color_btn.set_rgba(rgba)
        self.color_btn.set_valign(Gtk.Align.CENTER)
        self.color_row.add_suffix(self.color_btn)
        group.add(self.color_row)

        self.run_row = Adw.EntryRow(title="Command")
        self.run_row.set_text(k.get("run", ""))
        group.add(self.run_row)

        state = SlackState()
        state.refresh()
        self.workspaces = state.workspaces()
        self.team_row = Adw.ComboRow(title="Workspace")
        names = [f"{w['name']} ({w['domain']})" for w in self.workspaces] or ["none found in Slack's data"]
        self.team_row.set_model(Gtk.StringList.new(names))
        ids = [w["id"] for w in self.workspaces]
        self.team_row.set_selected(ids.index(k["team"]) if k.get("team") in ids else 0)
        group.add(self.team_row)

        self.cal_url = Adw.EntryRow(title="Feed address (.ics)")
        self.cal_url.set_text(k.get("url", ""))
        group.add(self.cal_url)
        self.cal_warn = Adw.SpinRow.new_with_range(0, 60, 1)
        self.cal_warn.set_title("Minutes before a meeting when the key turns red")
        self.cal_warn.set_value(float(k.get("warn_min", DEFAULT_WARN_MIN) or 0))
        group.add(self.cal_warn)

        self.mark_row = Adw.SwitchRow(title="Corner mark (>_) on Claude and Codex keys")
        self.mark_row.set_subtitle("One setting for every session, counter, pager and stats key. "
                                   "Off when the mark tells you nothing, say with Claude alone")
        self.mark_row.set_active(bool(self.window.cfg.get("sessions", {}).get("mark", True)))
        group.add(self.mark_row)

        self.vpn_names = vpn_connections()
        self.vpn_row = Adw.ComboRow(title="Connection")
        self.vpn_row.set_model(Gtk.StringList.new(self.vpn_names or ["no VPN connections in NetworkManager"]))
        self.vpn_row.set_selected(self.vpn_names.index(k["connection"])
                                  if k.get("connection") in self.vpn_names else 0)
        group.add(self.vpn_row)

        # to-do list rows
        self.todo_file = Adw.ActionRow(title="List file")
        self.todo_file.set_subtitle(k.get("file") or DEFAULT_TODO)
        btn = Gtk.Button(label="Choose…")
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: choose_file(self.window, self.on_todo_file, "Choose the to-do list file"))
        self.todo_file.add_suffix(btn)
        group.add(self.todo_file)
        self.todo_open = Adw.EntryRow(title="Open linked files with")
        self.todo_open.set_text(k.get("open_with") or "code")
        group.add(self.todo_open)
        self.todo_soon = Adw.SpinRow.new_with_range(0, 30, 1)
        self.todo_soon.set_title("Days ahead that count as due soon")
        self.todo_soon.set_subtitle("0: only items due today turn the key amber")
        self.todo_soon.set_value(float(k.get("soon_days", 0) or 0))
        group.add(self.todo_soon)
        self.todo_hold = Adw.SwitchRow(title="Hold an item's key to mark it done")
        self.todo_hold.set_subtitle("Ticks the item's box in the list file")
        self.todo_hold.set_active(bool(k.get("hold_done", True)))
        group.add(self.todo_hold)

        # stats page: optional tiles
        self.stats_tiles = {}
        on = set(k.get("tiles") or [])
        for name, title, subtitle in (
                ("tokens", "Tokens today tile", "Output and input tokens of this account's sessions since midnight"),
                ("cache", "Cache reads tile", "Cache read tokens today and the number of replies"),
                ("context", "Fullest context tile", "The session whose context window is fullest")):
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.set_active(name in on)
            group.add(row)
            self.stats_tiles[name] = row

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self.on_save)
        header.pack_end(save)
        view.add_top_bar(header)
        view.set_content(page)
        self.set_child(view)
        self.update_rows()

    def kind(self):
        return self.kinds[self.kind_row.get_selected()][0]

    def on_todo_file(self, path):
        self.todo_file.set_subtitle(path)

    def update_rows(self):
        kind = self.kind()
        self.hint.set_text(KIND_HINT.get(kind, ""))
        self.hint.set_visible(kind in KIND_HINT)
        self.label_row.set_visible(kind in ("command", "folder", "mute_source", "mute_sink", "todo", "vpn"))
        self.color_row.set_visible(kind in ("command", "folder"))
        self.open_row.set_visible(kind == "folder" and self.is_folder)
        self.run_row.set_visible(kind == "command")
        self.team_row.set_visible(kind == "slack")
        self.vpn_row.set_visible(kind == "vpn")
        self.mark_row.set_visible(kind in ("session", "stats") or kind.startswith(("counter_", "pager_")))
        self.cal_url.set_visible(kind == "calendar")
        self.cal_warn.set_visible(kind == "calendar")
        for row in (self.todo_file, self.todo_open, self.todo_soon, self.todo_hold):
            row.set_visible(kind == "todo")
        for row in self.stats_tiles.values():
            row.set_visible(kind == "stats")

    def on_save(self, *_):
        rgba = self.color_btn.get_rgba()
        color = "#%02x%02x%02x" % (round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255))
        team = self.workspaces[self.team_row.get_selected()]["id"] if self.workspaces else None
        label = self.label_row.get_text().replace("\\n", "\n")
        file = self.todo_file.get_subtitle()
        todo = {
            **({"file": file} if file and file != DEFAULT_TODO else {}),
            "open_with": self.todo_open.get_text().strip() or "code",
            "soon_days": int(self.todo_soon.get_value()),
            "hold_done": bool(self.todo_hold.get_active()),
            **({"title": label} if label.strip() else {}),
        }
        tiles = [name for name, row in self.stats_tiles.items() if row.get_active()]
        connection = self.vpn_names[self.vpn_row.get_selected()] if self.vpn_names else None
        calendar = {"url": self.cal_url.get_text().strip(), "warn_min": int(self.cal_warn.get_value())}
        set_key(self.cfg, self.index, self.kind(), label=label, color=color, run=self.run_row.get_text(),
                team=team, todo=todo, tiles=tiles, connection=connection, calendar=calendar)
        if self.mark_row.get_visible():
            self.window.cfg.setdefault("sessions", {})["mark"] = bool(self.mark_row.get_active())
        self.window.save()
        self.window.refresh_grid()
        self.close()


# --- main window -------------------------------------------------------------

class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=f"Deckmate settings  ·  v{version()}")
        self.set_default_size(620, 680)
        self.cfg = load_config()
        self.level = []          # indices of the folders the key grid is inside, outermost first
        self._save_pending = None
        self._loading = True

        stack = self.stack = Adw.ViewStack()
        stack.add_titled_with_icon(self.build_general(), "general", "General", "preferences-system-symbolic")
        stack.add_titled_with_icon(self.build_keys(), "keys", "Keys", "input-keyboard-symbolic")
        stack.add_titled_with_icon(self.build_deck(), "device", "Device", "drive-removable-media-symbolic")

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        header = Adw.HeaderBar()
        header.set_title_widget(switcher)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(stack)
        self.set_content(view)

        self._loading = False
        self.refresh_status()
        GLib.timeout_add(1000, self.refresh_status)

    # -- folders --
    def level_cfg(self):
        """The config dict the key grid shows: the layout, or a folder's entry inside it."""
        cfg = self.cfg
        for idx in self.level:
            cfg = (cfg.get("keys") or {}).get(str(idx)) or {}
        return cfg

    def enter_folder(self, index):
        self.level.append(index)
        self.refresh_grid()

    def leave_folder(self):
        if self.level:
            self.level.pop()
            self.refresh_grid()

    # -- saving --
    def save(self):
        save_config(self.cfg)

    def save_soon(self):
        """Debounced save for spin buttons and the like."""
        if self._loading:
            return
        if self._save_pending:
            GLib.source_remove(self._save_pending)
        self._save_pending = GLib.timeout_add(400, self._do_save)

    def _do_save(self):
        self._save_pending = None
        self.save()
        return False

    # -- general page --
    def build_general(self):
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title="Board")
        self.status_row = Adw.ActionRow(title="Status")
        self.start_btn = Gtk.Button(label="Start")
        self.start_btn.set_valign(Gtk.Align.CENTER)
        self.start_btn.connect("clicked", lambda *_: (start_board(), GLib.timeout_add(500, self.refresh_status)))
        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.set_valign(Gtk.Align.CENTER)
        self.stop_btn.connect("clicked", lambda *_: (stop_board(), self.refresh_status()))
        self.status_row.add_suffix(self.start_btn)
        self.status_row.add_suffix(self.stop_btn)
        g.add(self.status_row)
        self.tray_row = Adw.ActionRow(title="Tray icon")
        self.tray_btn = Gtk.Button(label="Show")
        self.tray_btn.set_valign(Gtk.Align.CENTER)
        self.tray_btn.connect("clicked", lambda *_: (start_tray(), GLib.timeout_add(1500, self.refresh_status)))
        self.tray_row.add_suffix(self.tray_btn)
        g.add(self.tray_row)
        page.add(g)

        g = Adw.PreferencesGroup(title="Display")
        self.brightness = Adw.SpinRow.new_with_range(10, 100, 10)
        self.brightness.set_title("Brightness (percent)")
        self.brightness.set_value(int(self.cfg.get("brightness", 60)))
        self.brightness.connect("notify::value", self.on_brightness)
        g.add(self.brightness)
        page.add(g)

        g = Adw.PreferencesGroup(title="Screen saver",
                                 description="Shown after the deck has been idle. Any key press brings the board back.")
        saver = self.cfg.setdefault("screensaver", {"image": "clock", "timeout_min": 5, "sleep_min": 0})
        self.saver_kind = Adw.ComboRow(title="Idle screen")
        self.saver_kind.set_model(Gtk.StringList.new(["Clock", "Picture"]))
        self.saver_kind.set_selected(0 if saver.get("image", "clock") == "clock" else 1)
        self.saver_kind.connect("notify::selected", self.on_saver_kind)
        g.add(self.saver_kind)

        self.saver_pic = Adw.ActionRow(title="Picture file")
        self.saver_pic.set_subtitle(saver.get("image") if saver.get("image") != "clock" else "none chosen")
        btn = Gtk.Button(label="Choose…")
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: choose_image(self, self.on_saver_picture))
        self.saver_pic.add_suffix(btn)
        g.add(self.saver_pic)

        self.saver_timeout = Adw.SpinRow.new_with_range(0, 240, 1)
        self.saver_timeout.set_title("Screen saver after (minutes)")
        self.saver_timeout.set_subtitle("0 turns the screen saver off")
        self.saver_timeout.set_value(float(saver.get("timeout_min", 5) or 0))
        self.saver_timeout.connect("notify::value", lambda *_: self.on_saver_number("timeout_min", self.saver_timeout))
        g.add(self.saver_timeout)

        self.saver_sleep = Adw.SpinRow.new_with_range(0, 1440, 5)
        self.saver_sleep.set_title("Sleep after (minutes)")
        self.saver_sleep.set_subtitle("The deck's own timer. 0 means never. A key press wakes it.")
        self.saver_sleep.set_value(float(saver.get("sleep_min", 0) or 0))
        self.saver_sleep.connect("notify::value", lambda *_: self.on_saver_number("sleep_min", self.saver_sleep))
        g.add(self.saver_sleep)
        page.add(g)
        self.update_saver_rows()

        g = Adw.PreferencesGroup(title="Notifications")
        self.notify_resets = Adw.SwitchRow(title="Notify when a nearly used-up Claude limit resets")
        self.notify_resets.set_subtitle("A desktop notification when a 5-hour or weekly window that was "
                                        "90% used or more opens again. Every reset still sweeps the deck.")
        self.notify_resets.set_active(bool(self.cfg.get("notify", {}).get("resets", True)))
        self.notify_resets.connect("notify::active", self.on_notify_resets)
        g.add(self.notify_resets)
        page.add(g)

        g = Adw.PreferencesGroup(title="Updates")
        self.auto_update = Adw.SwitchRow(title="Install updates automatically")
        self.auto_update.set_subtitle("The tray icon checks GitHub every hour and installs a newer "
                                      "release without asking. Off: it only offers the update in its menu.")
        self.auto_update.set_active(bool(self.cfg.get("updates", {}).get("auto", True)))
        self.auto_update.connect("notify::active", self.on_auto_update)
        g.add(self.auto_update)
        page.add(g)

        return page

    def on_notify_resets(self, *_):
        self.cfg.setdefault("notify", {})["resets"] = self.notify_resets.get_active()
        self.save_soon()

    def on_auto_update(self, *_):
        self.cfg.setdefault("updates", {})["auto"] = self.auto_update.get_active()
        self.save_soon()

    def on_brightness(self, *_):
        self.cfg["brightness"] = int(self.brightness.get_value())
        self.save_soon()

    def on_saver_kind(self, *_):
        saver = self.cfg["screensaver"]
        if self.saver_kind.get_selected() == 0:
            self._picture_path = saver.get("image") if saver.get("image") != "clock" else None
            saver["image"] = "clock"
        else:
            saver["image"] = getattr(self, "_picture_path", None) or saver.get("image") or "clock"
            if saver["image"] == "clock":
                choose_image(self, self.on_saver_picture)
        self.update_saver_rows()
        self.save_soon()

    def on_saver_picture(self, path):
        self.cfg["screensaver"]["image"] = path
        self._picture_path = path
        self.saver_kind.set_selected(1)
        self.update_saver_rows()
        self.save_soon()

    def on_saver_number(self, field, row):
        self.cfg["screensaver"][field] = int(row.get_value())
        self.save_soon()

    def update_saver_rows(self):
        image = self.cfg["screensaver"].get("image", "clock")
        self.saver_pic.set_visible(self.saver_kind.get_selected() == 1)
        self.saver_pic.set_subtitle(image if image != "clock" else "none chosen")

    def refresh_status(self):
        pid = board_pid()
        how = " (login service)" if service_loaded() else ""
        self.status_row.set_subtitle((f"running, pid {pid}" if pid else "not running") + how)
        self.start_btn.set_sensitive(pid is None)
        self.stop_btn.set_sensitive(pid is not None)
        tray = tray_pid()
        self.tray_row.set_subtitle("in the top bar" if tray else "not running (starts again at login)")
        self.tray_btn.set_sensitive(tray is None)
        return True

    # -- keys page --
    def build_keys(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Keys", description="Click a key to change what it does. "
                                 "The grid is laid out like the deck.")
        bar = Gtk.Box(spacing=8, margin_bottom=8)
        self.level_label = Gtk.Label(xalign=0, hexpand=True, css_classes=["heading"])
        self.up_btn = Gtk.Button(label="Back to parent", visible=False)
        self.up_btn.connect("clicked", lambda *_: self.leave_folder())
        bar.append(self.level_label)
        bar.append(self.up_btn)
        g.add(bar)
        self.grid = Gtk.Grid(row_spacing=8, column_spacing=8, halign=Gtk.Align.CENTER)
        self.grid_buttons = []
        for i in range(KEY_COUNT):
            b = Gtk.Button()
            b.set_size_request(104, 88)
            lbl = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True, max_width_chars=12)
            b.set_child(lbl)
            b.connect("clicked", lambda _b, idx=i: KeyDialog(self, idx).present(self))
            self.grid.attach(b, i % COLS, i // COLS, 1, 1)
            self.grid_buttons.append((b, lbl))
        g.add(self.grid)
        page.add(g)
        self.refresh_grid()
        return page

    def refresh_grid(self):
        cfg = self.level_cfg()
        names = ["Main board"]
        walk = self.cfg
        for idx in self.level:
            walk = (walk.get("keys") or {}).get(str(idx)) or {}
            names.append(walk.get("title") or f"folder {idx}")
        self.level_label.set_text("  ›  ".join(names))
        self.up_btn.set_visible(bool(self.level))
        for i, (b, lbl) in enumerate(self.grid_buttons):
            for c in ("session-key", "command-key"):
                b.remove_css_class(c)
            if self.level and i == 0:
                lbl.set_markup("<small>0</small>\nback")
                b.set_sensitive(False)
                continue
            b.set_sensitive(True)
            kind, _ = key_kind(cfg, i)
            text = key_summary(cfg, i)
            body = GLib.markup_escape_text(text) if text else "<i>empty</i>"
            lbl.set_markup(f"<small>{i}</small>\n{body}")
            if kind == "session":
                b.add_css_class("session-key")
            elif kind != "empty":
                b.add_css_class("command-key")

    # -- deck page --
    def build_deck(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Device")
        self.device_row = Adw.ActionRow(title="Stream Deck")
        g.add(self.device_row)
        page.add(g)
        try:
            found = find_deck()
            if found:
                d = Deck(found[0])
                info = d.unit_info()
                self.device_row.set_subtitle(f"{found[2]}, serial {d.serial()}, firmware {d.firmware()}, "
                                             f"{info['lcd_gallery']} stored screen images")
                self.lcd_gallery = info["lcd_gallery"]
                d.close()
            else:
                self.device_row.set_subtitle("not connected")
                self.lcd_gallery = 4
        except (OSError, UnsupportedDeck) as e:
            self.device_row.set_subtitle(str(e))
            self.lcd_gallery = 4

        g = Adw.PreferencesGroup(title="Deckmate")
        row = Adw.ActionRow(title="Version", subtitle=f"{version()}, installed in {HERE}")
        g.add(row)
        page.add(g)

        g = Adw.PreferencesGroup(
            title="Stored image (boot logo)",
            description="The picture the deck shows on its own, before the board starts. It lives inside "
                        "the deck and cannot be read back. Replacing it needs the 'Update Boot Logo' command, "
                        "which Elgato has not published for this model, so this is not available yet. "
                        "The deck's four 'background' slots are a different thing and do nothing on this model.")
        page.add(g)
        return page

class App(Adw.Application):
    def __init__(self, screenshot=None, page=None, key=None, folder=None):
        # one instance per session: launching again raises the open window.
        # A screenshot run must not land in a window that is already open.
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE if screenshot else Gio.ApplicationFlags.FLAGS_NONE)
        self.screenshot = screenshot
        self.page = page
        self.key = key
        self.folder = folder

    def do_activate(self):
        win = self.get_active_window()
        if win is not None:
            win.present()
            return
        css = Gtk.CssProvider()
        css.load_from_string("""
            .session-key { background: alpha(#3b82f6, 0.35); }
            .command-key { background: alpha(#22c55e, 0.25); }
        """)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win = SettingsWindow(self)
        win.present()
        if self.page:
            win.stack.set_visible_child_name(self.page)
        if self.folder is not None:      # --folder N: show the key grid inside folder N
            win.enter_folder(self.folder)
        if self.key is not None:
            KeyDialog(win, self.key).present(win)
        if self.screenshot:
            GLib.timeout_add(1500, self.take_screenshot, win)

    def take_screenshot(self, win):
        paintable = Gtk.WidgetPaintable.new(win)
        snap = Gtk.Snapshot.new()
        paintable.snapshot(snap, win.get_width(), win.get_height())
        node = snap.to_node()
        texture = win.get_native().get_renderer().render_texture(node, None)
        texture.save_to_png(self.screenshot)
        print(f"screenshot saved to {self.screenshot}")
        self.quit()
        return False


if __name__ == "__main__":
    def opt(name):
        return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None
    key = opt("--key")
    App(screenshot=opt("--screenshot"), page=opt("--page"), folder=opt("--folder") and int(opt("--folder")),
        key=int(key) if key is not None else None).run([])
