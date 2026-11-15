#!/usr/bin/env python3
"""Top-bar icon for the Stream Deck board (GTK 3 + AppIndicator).

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

from paths import HERE, INSTALLED, PID_FILE, REPO, STATUS_FILE, config_path, ensure_dirs, version  # noqa: E402

SERVICE = "stream-deck.service"
UPDATE_CHECK_S = 6 * 3600
INSTALL_HELPER = "/usr/lib/stream-deck/install-update"   # root helper, passwordless through polkit
ICON = "stream-deck-symbolic"
ICON_ATTENTION = "stream-deck-attention-symbolic"
ICON_OFF = "stream-deck-off-symbolic"


def sh(*cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def service_loaded():
    r = sh("systemctl", "--user", "show", SERVICE, "-p", "LoadState", "--value")
    return bool(r) and r.stdout.strip() == "loaded"


def board_pid():
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if os.path.exists(f"/proc/{pid}") else None


def read_status():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def notify(title, body=""):
    subprocess.Popen(["notify-send", "-a", "Stream Deck", "-i", "stream-deck", title, body],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def adopt_service():
    """Installed from the package: make sure the board service runs for this
    account, and retire a per-user unit left by install_service.sh."""
    old = os.path.expanduser(f"~/.config/systemd/user/{SERVICE}")
    if INSTALLED and os.path.exists(old):
        with open(old) as f:
            if HERE not in f.read():
                sh("systemctl", "--user", "disable", "--now", SERVICE)
                os.remove(old)
                for link in (os.path.expanduser("~/.config/systemd/user/graphical-session.target.wants/" + SERVICE),):
                    if os.path.islink(link):
                        os.remove(link)
                sh("systemctl", "--user", "daemon-reload")
    if service_loaded():
        sh("systemctl", "--user", "start", SERVICE)


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
        self.ind = AppIndicator.Indicator.new("stream-deck", ICON, AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_title("Stream Deck")
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
        self.version_item = Gtk.MenuItem(label=f"Version {version()}")
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
        if service_loaded():
            sh("systemctl", "--user", "start", SERVICE)
        else:
            subprocess.Popen([sys.executable, os.path.join(HERE, "deck.py")], cwd=HERE, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self):
        if service_loaded():
            sh("systemctl", "--user", "stop", SERVICE)
        pid = board_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    def open_settings(self):
        # In a systemd scope of its own, like the desktop launches apps: a plain child
        # would inherit the tray's process group, and a tray started from a terminal
        # inside VS Code's snap sits in VS Code's group, under which GNOME then files
        # the settings window in the dock (it checks the group before the app id).
        cmd = [sys.executable, os.path.join(HERE, "settings.py")]
        if shutil.which("systemd-run"):
            unit = f"app-stream-deck-settings-{os.getpid()}-{int(time.time())}"
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
                                                       "User-Agent": "stream-deck-tray"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r), False
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403, 404) or not shutil.which("gh"):
                raise
        r = sh("gh", "api", f"repos/{REPO}/releases/latest", timeout=30)
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
                notify("Stream Deck: no update information", error or "no releases found")
            return False
        if version_tuple(tag) > version_tuple(version()) and asset:
            self.update = (tag, asset["browser_download_url"], asset["name"], via_gh)
            self.update_item.set_label(f"Update to {tag}…")
            if quiet and auto_update_enabled():
                notify(f"Stream Deck {tag} is available", "Installing it now.")
                self.install_update()
            else:
                notify(f"Stream Deck {tag} is available", "Use the tray menu to install it.")
        else:
            self.update = None
            self.update_item.set_label("Check for updates")
            if not quiet:
                notify("Stream Deck is up to date", f"version {version()}")
        return False

    def install_update(self):
        tag, url, name, via_gh = self.update
        cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "stream-deck")
        os.makedirs(cache, exist_ok=True)
        path = os.path.join(cache, name)

        def work():
            try:
                if via_gh:
                    r = sh("gh", "release", "download", tag, "--repo", REPO, "--pattern", name,
                           "--dir", cache, "--clobber", timeout=120)
                    if r is None or r.returncode != 0:
                        raise RuntimeError((r.stderr if r else "gh failed").strip()[-200:])
                else:
                    urllib.request.urlretrieve(url, path)
            except Exception as e:
                GLib.idle_add(notify, "Stream Deck update failed", f"download: {e}")
                return
            # the packaged helper needs no password (polkit action); a git checkout
            # falls back to apt-get, and pkexec asks for the password in a dialog
            if os.path.exists(INSTALL_HELPER):
                cmd = ["pkexec", INSTALL_HELPER, path]
            else:
                cmd = ["pkexec", "apt-get", "install", "-y", path]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                GLib.idle_add(notify, "Stream Deck update failed", (r.stderr or r.stdout).strip()[-300:])
                return
            GLib.idle_add(self.after_update, tag)
        threading.Thread(target=work, daemon=True).start()

    def after_update(self, tag):
        notify(f"Stream Deck updated to {tag}", "Restarting the board.")
        if service_loaded():
            sh("systemctl", "--user", "restart", SERVICE)
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
        return False


def main():
    ensure_dirs()
    adopt_service()
    Tray()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Gtk.main()


if __name__ == "__main__":
    main()
