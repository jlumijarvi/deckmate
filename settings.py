#!/usr/bin/env python3
"""Settings window for the Deckmate board.

Edits keys.json (the running deck.py picks changes up within a second), starts
and stops the board, and can replace the picture stored inside the deck.

    python3 settings.py
"""
import json
import os
import shutil
import signal
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

from paths import HERE, CONFIG, EXAMPLE, PID_FILE, TRAY_PID_FILE, config_path, version  # noqa: E402

DECK_PY = os.path.join(HERE, "deck.py")

KEY_KINDS = [
    ("empty", "Empty"),
    ("command", "Button"),
    ("clock", "Clock"),
    ("mute_source", "Mic mute"),
    ("mute_sink", "Speaker mute"),
    ("volume_down", "Volume down"),
    ("volume_up", "Volume up"),
    ("brightness", "Deck brightness"),
    ("slack", "Slack unread badge"),
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
    "command": "Runs a shell command when tapped",
    "mute_source": "Toggles the microphone; the key shows its state",
    "mute_sink": "Toggles the speakers; the key shows its state",
    "slack": "Unread count and @mentions of one Slack workspace",
    "todo": "Open items in a Markdown task list; tap opens them as a sub-deck",
    "session": "One running Claude Code session. Tap opens its folder in VS Code, hold hides it",
    "counter_waiting": "Sessions asking you something. Tap filters the slots to them",
    "counter_running": "Sessions still working. Tap filters the slots to them",
    "counter_done": "Finished sessions. Tap filters, hold hides them all",
    "pager_prev": "Previous page of sessions. Hold: hidden sessions",
    "pager_next": "Next page of sessions",
    "brightness": "Shows the deck's brightness; tap steps it 30 / 60 / 100",
    "stats": "Opens the stats page: plan quota, tokens today, cache reads, fullest context",
}


# --- config ------------------------------------------------------------------

def load_config():
    for path in (config_path(), EXAMPLE):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


def save_config(cfg):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG)


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
    if t in ("clock", "slack", "todo", "stats", "brightness"):
        return t, k
    if t == "counter":
        return "counter_" + (k.get("state") or "waiting"), k
    if t == "pager":
        return "pager_" + (k.get("dir") or "next"), k
    return "command", k


def set_key(cfg, i, kind, label="", color="#444444", run="", team=None, todo=None):
    keys = cfg.setdefault("keys", {})
    sess = [s for s in session_keys(cfg) if s != i]
    keys.pop(str(i), None)
    if kind == "session":
        sess.append(i)
    elif kind == "command":
        keys[str(i)] = {"label": label, "color": color, "run": run}
    elif kind == "clock":
        keys[str(i)] = {"type": "clock"}
    elif kind == "slack":
        keys[str(i)] = {"type": "slack", **({"team": team} if team else {})}
    elif kind == "todo":
        keys[str(i)] = {"type": "todo", **(todo or {})}
    elif kind == "stats":
        keys[str(i)] = {"type": "stats"}
    elif kind == "brightness":
        keys[str(i)] = {"type": "brightness"}
    elif kind.startswith("counter_"):
        keys[str(i)] = {"type": "counter", "state": kind.split("_", 1)[1]}
    elif kind.startswith("pager_"):
        keys[str(i)] = {"type": "pager", "dir": kind.split("_", 1)[1]}
    elif kind == "mute_source":
        keys[str(i)] = {"type": "mute", "target": "source", "label": label or "Mic"}
    elif kind == "mute_sink":
        keys[str(i)] = {"type": "mute", "target": "sink", "label": label or "Speaker"}
    elif kind == "volume_down":
        keys[str(i)] = {"type": "volume", "step": -5}
    elif kind == "volume_up":
        keys[str(i)] = {"type": "volume", "step": 5}
    cfg.setdefault("sessions", {})["keys"] = sorted(sess)
    cfg["keys"] = {k: keys[k] for k in sorted(keys, key=int)}


def key_summary(cfg, i):
    kind, k = key_kind(cfg, i)
    if kind == "command":
        return k.get("label", "").replace("\n", " ") or "(no label)"
    if kind in ("mute_source", "mute_sink"):
        return k.get("label") or KIND_LABEL[kind]
    if kind == "session":
        return "Claude\nsession"
    if kind == "slack":
        return "Slack\n" + (k.get("team") or "")
    if kind == "todo":
        return "To-do\n" + os.path.basename(k.get("file") or "todo.md")
    if kind.startswith("counter_"):
        return "Count\n" + kind.split("_", 1)[1]
    if kind == "stats":
        return "Stats"
    if kind == "brightness":
        return "Bright-\nness"
    if kind == "pager_prev":
        return "< page"
    if kind == "pager_next":
        return "page >"
    if kind == "empty":
        return ""
    return KIND_LABEL[kind]


# --- the board process -------------------------------------------------------

def pid_from(path):
    try:
        with open(path) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if os.path.exists(f"/proc/{pid}") else None


def board_pid():
    return pid_from(PID_FILE)


def tray_pid():
    return pid_from(TRAY_PID_FILE)


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


SERVICE = "deckmate.service"


def service_installed():
    """True when systemd knows a deckmate.service for this user."""
    try:
        out = subprocess.run(["systemctl", "--user", "show", SERVICE, "-p", "LoadState", "--value"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return out == "loaded"


def start_board():
    if service_installed():
        subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)
        return
    subprocess.Popen([sys.executable, DECK_PY], cwd=HERE, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_board(timeout=5.0):
    pid = board_pid()
    if service_installed():
        subprocess.run(["systemctl", "--user", "stop", SERVICE], check=False)
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    end = time.monotonic() + timeout
    while time.monotonic() < end and os.path.exists(f"/proc/{pid}"):
        time.sleep(0.1)


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

        kind, k = key_kind(window.cfg, index)
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        page.add(group)

        self.kind_row = Adw.ComboRow(title="Type")
        self.kind_row.set_model(Gtk.StringList.new([label for _, label in KEY_KINDS]))
        self.kind_row.set_selected(KIND_INDEX[kind])
        self.kind_row.connect("notify::selected", lambda *_: self.update_rows())
        group.add(self.kind_row)

        self.hint = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"],
                              margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        group.add(self.hint)

        self.label_row = Adw.EntryRow(title="Label (use \\n for a line break)")
        self.label_row.set_text(k.get("label", "").replace("\n", "\\n"))
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

        # to-do list rows
        self.todo_file = Adw.ActionRow(title="List file")
        self.todo_file.set_subtitle(k.get("file") or "~/.config/deckmate/todo.md")
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
        return KEY_KINDS[self.kind_row.get_selected()][0]

    def on_todo_file(self, path):
        self.todo_file.set_subtitle(path)

    def update_rows(self):
        kind = self.kind()
        self.hint.set_text(KIND_HINT.get(kind, ""))
        self.hint.set_visible(kind in KIND_HINT)
        self.label_row.set_visible(kind in ("command", "mute_source", "mute_sink", "todo"))
        self.color_row.set_visible(kind == "command")
        self.run_row.set_visible(kind == "command")
        self.team_row.set_visible(kind == "slack")
        for row in (self.todo_file, self.todo_open, self.todo_soon, self.todo_hold):
            row.set_visible(kind == "todo")

    def on_save(self, *_):
        rgba = self.color_btn.get_rgba()
        color = "#%02x%02x%02x" % (round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255))
        team = self.workspaces[self.team_row.get_selected()]["id"] if self.workspaces else None
        label = self.label_row.get_text().replace("\\n", "\n")
        todo = {
            "file": self.todo_file.get_subtitle(),
            "open_with": self.todo_open.get_text().strip() or "code",
            "soon_days": int(self.todo_soon.get_value()),
            "hold_done": bool(self.todo_hold.get_active()),
            **({"label": label} if label.strip() else {}),
        }
        set_key(self.window.cfg, self.index, self.kind(),
                label=label, color=color, run=self.run_row.get_text(), team=team, todo=todo)
        self.window.save()
        self.window.refresh_grid()
        self.close()


# --- main window -------------------------------------------------------------

class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=f"Deckmate settings  ·  v{version()}")
        self.set_default_size(620, 680)
        self.cfg = load_config()
        self._save_pending = None
        self._loading = True

        stack = self.stack = Adw.ViewStack()
        stack.add_titled_with_icon(self.build_general(), "general", "General", "preferences-system-symbolic")
        stack.add_titled_with_icon(self.build_keys(), "keys", "Keys", "input-keyboard-symbolic")
        stack.add_titled_with_icon(self.build_deck(), "deck", "Deck", "drive-removable-media-symbolic")

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
        self.saver_timeout.set_title("Idle minutes before the screen saver")
        self.saver_timeout.set_subtitle("0 turns the screen saver off")
        self.saver_timeout.set_value(float(saver.get("timeout_min", 5) or 0))
        self.saver_timeout.connect("notify::value", lambda *_: self.on_saver_number("timeout_min", self.saver_timeout))
        g.add(self.saver_timeout)

        self.saver_sleep = Adw.SpinRow.new_with_range(0, 1440, 5)
        self.saver_sleep.set_title("Idle minutes before the backlight goes off")
        self.saver_sleep.set_subtitle("The deck's own timer. 0 means never. A key press wakes it.")
        self.saver_sleep.set_value(float(saver.get("sleep_min", 0) or 0))
        self.saver_sleep.connect("notify::value", lambda *_: self.on_saver_number("sleep_min", self.saver_sleep))
        g.add(self.saver_sleep)
        page.add(g)
        self.update_saver_rows()

        g = Adw.PreferencesGroup(title="Updates")
        self.auto_update = Adw.SwitchRow(title="Install updates automatically")
        self.auto_update.set_subtitle("The tray icon checks GitHub every hour and installs a newer "
                                      "release without asking. Off: it only offers the update in its menu.")
        self.auto_update.set_active(bool(self.cfg.get("updates", {}).get("auto", True)))
        self.auto_update.connect("notify::active", self.on_auto_update)
        g.add(self.auto_update)
        page.add(g)

        return page

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
        how = " (login service)" if service_installed() else ""
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
        for i, (b, lbl) in enumerate(self.grid_buttons):
            kind, _ = key_kind(self.cfg, i)
            text = key_summary(self.cfg, i)
            body = GLib.markup_escape_text(text) if text else "<i>empty</i>"
            lbl.set_markup(f"<small>{i}</small>\n{body}")
            for c in ("session-key", "command-key"):
                b.remove_css_class(c)
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
    def __init__(self, screenshot=None, page=None, key=None):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.screenshot = screenshot
        self.page = page
        self.key = key

    def do_activate(self):
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
    App(screenshot=opt("--screenshot"), page=opt("--page"),
        key=int(key) if key is not None else None).run([])
