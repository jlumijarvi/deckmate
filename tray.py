#!/usr/bin/env python3
"""Top-bar icon for the Deckmate board (GTK 3 + AppIndicator).

Menu: board status, Start / Stop, Settings, Check for updates, Quit.
The icon switches to the attention variant while a Claude session waits for you.
Starts at login from /etc/xdg/autostart when installed from the package.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import GLib, Gtk  # noqa: E402
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402

from paths import HERE, INSTALLED, REPO, STATUS_FILE, TRAY_PID_FILE, config_path, ensure_dirs, version  # noqa: E402
from system import (SERVICE, board_pid, claim_pid, dev_board_active, release_pid, run,  # noqa: E402
                    service_loaded, start_board, stop_board)

UPDATE_CHECK_S = 3600
INSTALL_HELPER = "/usr/lib/deckmate/install-update"   # root helper, passwordless through polkit
ICON = "deckmate-symbolic"
ICON_ATTENTION = "deckmate-attention-symbolic"
ICON_OFF = "deckmate-off-symbolic"


def read_status():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def notify(title, body=""):
    subprocess.Popen(["notify-send", "-a", "Deckmate", "-i", "deckmate", title, body],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def adopt_service():
    """Installed from the package: make sure the board service runs for this
    account, and retire a per-user unit left by an old git-checkout install."""
    old = os.path.expanduser(f"~/.config/systemd/user/{SERVICE}")
    if INSTALLED and os.path.exists(old):
        with open(old) as f:
            if HERE not in f.read():
                run("systemctl", "--user", "disable", "--now", SERVICE)
                os.remove(old)
                for link in (os.path.expanduser("~/.config/systemd/user/graphical-session.target.wants/" + SERVICE),):
                    if os.path.islink(link):
                        os.remove(link)
                run("systemctl", "--user", "daemon-reload")
    if service_loaded():
        run("systemctl", "--user", "start", SERVICE)


def auto_update_enabled():
    """The "Install updates automatically" switch in the settings (default on)."""
    try:
        with open(config_path()) as f:
            return bool(json.load(f).get("updates", {}).get("auto", True))
    except (OSError, ValueError):
        return True


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


class Tray:
    def __init__(self):
        self.ind = AppIndicator.Indicator.new("deckmate", ICON, AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_title("Deckmate")
        self.ind.set_attention_icon_full(ICON_ATTENTION, "A Claude session is waiting for you")
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.update = None  # (tag, deb url, asset name, via_gh) when a newer release is known

        self.menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="Board: …")
        self.status_item.set_sensitive(False)
        self.menu.append(self.status_item)
        self.sessions_item = Gtk.MenuItem(label="")
        self.sessions_item.set_sensitive(False)
        self.menu.append(self.sessions_item)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.start_item = Gtk.MenuItem(label="Start board")
        self.start_item.connect("activate", lambda *_: self.start())
        self.menu.append(self.start_item)
        self.stop_item = Gtk.MenuItem(label="Stop board")
        self.stop_item.connect("activate", lambda *_: self.stop())
        self.menu.append(self.stop_item)
        self.menu.append(Gtk.SeparatorMenuItem())
        item = Gtk.MenuItem(label="Settings…")
        item.connect("activate", lambda *_: self.open_settings())
        self.menu.append(item)
        self.update_item = Gtk.MenuItem(label="Check for updates")
        self.update_item.connect("activate", lambda *_: self.on_update_item())
        self.menu.append(self.update_item)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.started_version = version()
        self.version_item = Gtk.MenuItem(label=f"Version {self.started_version}")
        self.version_item.set_sensitive(False)
        self.menu.append(self.version_item)
        item = Gtk.MenuItem(label="Quit tray icon")
        item.connect("activate", lambda *_: Gtk.main_quit())
        self.menu.append(item)
        self.menu.show_all()
        self.ind.set_menu(self.menu)

        self.refresh()
        GLib.timeout_add(2000, self.refresh)
        GLib.timeout_add_seconds(30, self.check_updates_later)

    # -- status --
    def refresh(self):
        installed = version()
        if installed != self.started_version:    # updated behind our back (by hand, or apt): run the new code
            self.after_update(f"v{installed}")
            return False
        pid = board_pid()
        status = read_status() if pid else None
        if pid is None:
            text, icon = "Board: not running", ICON_OFF
        elif status and not status.get("deck", False):
            text, icon = "Board: running, deck not connected", ICON_OFF
        else:
            text, icon = "Board: running", ICON
        self.status_item.set_label(text)
        waiting = status.get("waiting", 0) if status else 0
        running = status.get("running", 0) if status else 0
        done = status.get("done", 0) if status else 0
        if status:
            self.sessions_item.set_label(f"Claude: {waiting} waiting, {running} running, {done} done")
            self.sessions_item.show()
        else:
            self.sessions_item.hide()
        self.start_item.set_sensitive(pid is None)
        self.stop_item.set_sensitive(pid is not None)
        if waiting:
            self.ind.set_status(AppIndicator.IndicatorStatus.ATTENTION)
        else:
            self.ind.set_icon_full(icon, text)
            self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        return True

    # -- actions --
    def start(self):
        start_board()

    def stop(self):
        stop_board(timeout=2.0)

    def open_settings(self):
        # In a systemd scope of its own, like the desktop launches apps: a plain child
        # would inherit the tray's process group, and a tray started from a terminal
        # inside VS Code's snap sits in VS Code's group, under which GNOME then files
        # the settings window in the dock (it checks the group before the app id).
        cmd = [sys.executable, os.path.join(HERE, "settings.py")]
        if shutil.which("systemd-run"):
            unit = f"app-deckmate-settings-{os.getpid()}-{int(time.time())}"
            p = subprocess.Popen(["systemd-run", "--user", "--scope", "--quiet", "--collect", "--unit", unit] + cmd,
                                 cwd=HERE, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                p.wait(timeout=1.5)          # still running: the scope was created and settings is up
            except subprocess.TimeoutExpired:
                return
            if p.returncode == 0:
                return
        subprocess.Popen(cmd, cwd=HERE, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # -- updates --
    def check_updates_later(self):
        self.check_updates(quiet=True)
        GLib.timeout_add_seconds(UPDATE_CHECK_S, self.check_updates_later)
        return False

    def on_update_item(self):
        if self.update:
            self.install_update()
        else:
            self.check_updates(quiet=False)

    @staticmethod
    def latest_release():
        """(release json, via_gh). Public repo: the GitHub API. Private repo: the gh
        tool, if it is installed and logged in (`gh auth login`)."""
        url = f"https://api.github.com/repos/{REPO}/releases/latest"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                       "User-Agent": "deckmate-tray"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r), False
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403, 404) or not shutil.which("gh"):
                raise
        r = run("gh", "api", f"repos/{REPO}/releases/latest", timeout=30)
        if r is None or r.returncode != 0:
            raise RuntimeError("private repository: run `gh auth login` once, or make it public")
        return json.loads(r.stdout), True

    def check_updates(self, quiet):
        def work():
            try:
                rel, via_gh = self.latest_release()
                tag = rel.get("tag_name", "")
                asset = next((a for a in rel.get("assets", []) if a.get("name", "").endswith(".deb")), None)
                GLib.idle_add(self.update_result, tag, asset, via_gh, quiet, None)
            except Exception as e:  # network down, no releases yet, rate limit
                GLib.idle_add(self.update_result, None, None, False, quiet, str(e))
        threading.Thread(target=work, daemon=True).start()

    def update_result(self, tag, asset, via_gh, quiet, error):
        if error or not tag:
            if not quiet:
                notify("Deckmate: no update information", error or "no releases found")
            return False
        if version_tuple(tag) > version_tuple(version()) and asset:
            self.update = (tag, asset["browser_download_url"], asset["name"], via_gh)
            self.update_item.set_label(f"Update to {tag}…")
            if quiet and auto_update_enabled():
                notify(f"Deckmate {tag} is available", "Installing it now.")
                self.install_update()
            else:
                notify(f"Deckmate {tag} is available", "Use the tray menu to install it.")
        else:
            self.update = None
            self.update_item.set_label("Check for updates")
            if not quiet:
                notify("Deckmate is up to date", f"version {version()}")
        return False

    def install_update(self):
        """The packaged root helper fetches the release named by its tag from GitHub
        itself and verifies the package against the digest GitHub publishes; this
        process never downloads or hands over a file. Needs no password (polkit)."""
        tag, url, name, via_gh = self.update
        if not os.path.exists(INSTALL_HELPER):        # a git checkout: no helper, no root
            notify(f"Deckmate {tag} is available", f"Install the package by hand: {url}")
            return
        if dev_board_active():                        # installing would start the package's board next to it
            notify(f"Deckmate {tag} not installed", "A dev board is on the deck. Run ./dev.sh stop, then "
                   "install from the tray menu.")
            return

        def work():
            r = subprocess.run(["pkexec", INSTALL_HELPER, tag], capture_output=True, text=True)
            if r.returncode != 0:
                GLib.idle_add(notify, "Deckmate update failed", (r.stderr or r.stdout).strip()[-300:])
                return
            GLib.idle_add(self.after_update, tag)
        threading.Thread(target=work, daemon=True).start()

    def after_update(self, tag):
        notify(f"Deckmate updated to {tag}", "Restarting the board.")
        if service_loaded():
            run("systemctl", "--user", "restart", SERVICE)
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
        return False


def main():
    ensure_dirs()
    if not claim_pid(TRAY_PID_FILE):    # the file lets the settings window see that the icon is up
        print("deckmate tray: already running", file=sys.stderr)
        return
    adopt_service()
    Tray()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())    # so a stop still removes the pid file
    try:
        Gtk.main()
    finally:
        release_pid(TRAY_PID_FILE)


if __name__ == "__main__":
    main()
