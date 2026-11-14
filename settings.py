#!/usr/bin/env python3
"""Settings window for the Stream Deck board.

Edits keys.json (the running deck.py picks changes up within a second), starts
and stops the board, and can replace the picture stored inside the deck.

    python3 settings.py
"""
import json
import os
import signal
import subprocess
import sys
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from deckhid import Deck, UnsupportedDeck, find_deck, COLS, ROWS, KEY_COUNT  # noqa: E402
from slackstate import SlackState  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "keys.json")
EXAMPLE = os.path.join(HERE, "keys.example.json")
PID_FILE = os.path.join(HERE, ".deck.pid")
DECK_PY = os.path.join(HERE, "deck.py")

KEY_KINDS = [
    ("empty", "Empty"),
    ("command", "Button (runs a command)"),
    ("clock", "Clock"),
    ("mute_source", "Mic mute"),
    ("mute_sink", "Speaker mute"),
    ("volume_down", "Volume down"),
    ("volume_up", "Volume up"),
    ("session", "Claude session slot"),
    ("slack", "Slack unread badge"),
]
KIND_INDEX = {k: i for i, (k, _) in enumerate(KEY_KINDS)}
KIND_LABEL = dict(KEY_KINDS)


# --- config ------------------------------------------------------------------

def load_config():
    for path in (CONFIG, EXAMPLE):
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
    if t in ("clock", "slack"):
        return t, k
    return "command", k


def set_key(cfg, i, kind, label="", color="#444444", run="", team=None):
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
    if kind == "empty":
        return ""
    return KIND_LABEL[kind]


# --- the board process -------------------------------------------------------

def board_pid():
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if os.path.exists(f"/proc/{pid}") else None


SERVICE = "stream-deck.service"


def service_installed():
    return os.path.exists(os.path.expanduser(f"~/.config/systemd/user/{SERVICE}"))


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

    def update_rows(self):
        kind = self.kind()
        self.label_row.set_visible(kind in ("command", "mute_source", "mute_sink"))
        self.color_row.set_visible(kind == "command")
        self.run_row.set_visible(kind == "command")
        self.team_row.set_visible(kind == "slack")

    def on_save(self, *_):
        rgba = self.color_btn.get_rgba()
        color = "#%02x%02x%02x" % (round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255))
        team = self.workspaces[self.team_row.get_selected()]["id"] if self.workspaces else None
        set_key(self.window.cfg, self.index, self.kind(),
                label=self.label_row.get_text().replace("\\n", "\n"),
                color=color, run=self.run_row.get_text(), team=team)
        self.window.save()
        self.window.refresh_grid()
        self.close()


# --- main window -------------------------------------------------------------

class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Stream Deck settings")
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
        self.status_row = Adw.ActionRow(title="deck.py")
        self.start_btn = Gtk.Button(label="Start")
        self.start_btn.set_valign(Gtk.Align.CENTER)
        self.start_btn.connect("clicked", lambda *_: (start_board(), GLib.timeout_add(500, self.refresh_status)))
        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.set_valign(Gtk.Align.CENTER)
        self.stop_btn.connect("clicked", lambda *_: (stop_board(), self.refresh_status()))
        self.status_row.add_suffix(self.start_btn)
        self.status_row.add_suffix(self.stop_btn)
        g.add(self.status_row)
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
        return page

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
            lbl.set_markup(f"<small>{i}</small>\n{GLib.markup_escape_text(text)}" if text else f"<small>{i}</small>\n<i>empty</i>")
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

        g = Adw.PreferencesGroup(
            title="Stored image",
            description="The picture the deck shows on its own, before deck.py starts. It lives inside the "
                        "deck and cannot be read back, so the current one is lost when replaced. Elgato does not "
                        "say which of the stored slots is the one shown, so the first replacement tries the "
                        "slots one at a time and asks you what the deck shows.")
        self.stored_pic = Adw.ActionRow(title="Picture file")
        self.stored_pic.set_subtitle(self.cfg.get("stored_image", {}).get("image", "none chosen"))
        btn = Gtk.Button(label="Choose…")
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: choose_image(self, self.on_stored_picture))
        self.stored_pic.add_suffix(btn)
        g.add(self.stored_pic)

        self.slot_row = Adw.ActionRow(title="Slot that holds the shown image")
        self.update_slot_row()
        g.add(self.slot_row)

        self.replace_btn = Gtk.Button(label="Replace the stored image…")
        self.replace_btn.add_css_class("destructive-action")
        self.replace_btn.set_halign(Gtk.Align.START)
        self.replace_btn.set_margin_top(8)
        self.replace_btn.connect("clicked", self.on_replace_stored)
        g.add(self.replace_btn)
        page.add(g)
        return page

    def update_slot_row(self):
        slot = self.cfg.get("stored_image", {}).get("slot")
        self.slot_row.set_subtitle("not known yet" if slot is None else f"slot {slot}")

    def on_stored_picture(self, path):
        self.cfg.setdefault("stored_image", {})["image"] = path
        self.stored_pic.set_subtitle(path)
        self.save()

    def on_replace_stored(self, *_):
        path = self.cfg.get("stored_image", {}).get("image")
        if not path or not os.path.exists(path):
            ask(self, "Choose a picture first", "Pick the picture to store with the Choose button.", [("ok", "OK")], lambda r: None)
            return
        ask(self, "Replace the image stored in the deck?",
            "The current stored image cannot be backed up and will be lost.\n\n"
            "The board will be stopped while this runs and started again afterwards.",
            [("cancel", "Cancel"), ("replace", "Replace")],
            lambda r: self.replace_stored(path) if r == "replace" else None,
            destructive="replace")

    def replace_stored(self, path):
        from PIL import Image
        try:
            img = Image.open(path).convert("RGB")
        except (OSError, ValueError) as e:
            ask(self, "Cannot open the picture", str(e), [("ok", "OK")], lambda r: None)
            return
        from deckhid import LCD_W, LCD_H
        scale = max(LCD_W / img.width, LCD_H / img.height)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
        x, y = (img.width - LCD_W) // 2, (img.height - LCD_H) // 2
        data = Deck.encode_screen(img.crop((x, y, x + LCD_W, y + LCD_H)))

        was_running = board_pid() is not None
        stop_board()
        try:
            found = find_deck()
            if not found:
                raise OSError("no Stream Deck connected")
            deck = Deck(found[0])
        except (OSError, UnsupportedDeck) as e:
            ask(self, "Cannot open the deck", str(e), [("ok", "OK")], lambda r: None)
            if was_running:
                start_board()
            return

        known = self.cfg.get("stored_image", {}).get("slot")
        order = ([known] if known is not None else []) + [s for s in range(self.lcd_gallery) if s != known]

        def finish(slot):
            deck.close()
            if slot is not None:
                self.cfg.setdefault("stored_image", {})["slot"] = slot
                self.save()
                self.update_slot_row()
            if was_running:
                start_board()
            if slot is None:
                ask(self, "No slot changed what the deck shows",
                    "Every stored slot was written but the deck kept showing something else. "
                    "The firmware may keep its standby image elsewhere.", [("ok", "OK")], lambda r: None)

        def try_slot(n):
            if n >= len(order):
                finish(None)
                return
            slot = order[n]
            try:
                deck.set_background_jpeg(slot, data)
                time.sleep(0.3)
                deck.show_logo()
            except OSError as e:
                ask(self, "Writing failed", str(e), [("ok", "OK")], lambda r: finish(None))
                return
            ask(self, f"Slot {slot} written",
                "Look at the deck. Does it show your picture now?",
                [("no", "No, try the next slot"), ("yes", "Yes")],
                lambda r: finish(slot) if r == "yes" else try_slot(n + 1),
                default="yes")

        try_slot(0)


class App(Adw.Application):
    def __init__(self, screenshot=None, page=None, key=None):
        super().__init__(application_id="io.github.jlumijarvi.streamdeck.settings",
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
