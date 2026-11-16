"""The VPN key: one NetworkManager VPN (or WireGuard) connection, on or off.
State comes from `nmcli connection show`, polled once a second and shared by
every VPN key; press runs `nmcli connection up` or `down`, which NetworkManager
allows for the user at the active seat without a password."""
import shlex
import time

from deckhid import KEY_PX
from keys import Key
from render import draw_centered_lines, fit_lines, tile
from system import launch, sh

VPN_TYPES = ("vpn", "wireguard")
REFRESH_S = 1.0


def parse_connections(text):
    """{name: (type, state)} from `nmcli -t -f NAME,TYPE,STATE connection show`.
    nmcli escapes ':' inside values, so split from the right."""
    out = {}
    for line in (text or "").splitlines():
        parts = line.rsplit(":", 2)
        if len(parts) != 3:
            continue
        name, ctype, state = parts
        out[name.replace("\\:", ":")] = (ctype, state)
    return out


class Connections:
    def __init__(self):
        self.all = {}
        self._last = 0.0
        self.available = True

    def refresh(self):
        now = time.monotonic()
        if now - self._last < REFRESH_S:
            return
        self._last = now
        text = sh("nmcli -t -f NAME,TYPE,STATE connection show")
        self.available = bool(text)
        if text:
            self.all = parse_connections(text)

    def vpns(self):
        return [name for name, (ctype, _) in self.all.items() if ctype in VPN_TYPES]

    def state(self, name):
        """'activated', 'activating', 'deactivating', '' (off) or None (no such connection)."""
        if name not in self.all:
            return None
        return self.all[name][1]


_connections = None


def connections():
    global _connections
    if _connections is None:
        _connections = Connections()
    return _connections


def vpn_connections():
    """Names of the VPN connections NetworkManager knows, for the settings window."""
    found = parse_connections(sh("nmcli -t -f NAME,TYPE,STATE connection show"))
    return sorted((name for name, (ctype, _) in found.items() if ctype in VPN_TYPES), key=str.lower)


class Vpn(Key):
    """cfg "connection": the NetworkManager connection name. Shows on / off /
    connecting; press turns it on or off."""
    dynamic = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self.name = cfg.get("connection") or ""

    def render(self, pressed):
        c = connections()
        c.refresh()
        state = c.state(self.name) if self.name else None
        if state == "activated":
            bg, text = (40, 120, 60), "on"
        elif state in ("activating", "deactivating"):
            bg, text = (170, 120, 30), "…"
        elif state is None:
            bg, text = (60, 50, 50), ("no nmcli" if not c.available else "not found" if self.name else "no connection")
        else:
            bg, text = (50, 50, 60), "off"
        img, d, fg = tile(bg, pressed)
        lines, size = fit_lines(self.cfg.get("title") or self.name or "VPN", max_lines=2, sizes=(13, 12, 11, 10, 9))
        draw_centered_lines(d, lines, fg, size=size, top=10)
        draw_centered_lines(d, [text], fg, size=14 if len(text) <= 3 else 10, top=KEY_PX - 28, bold=len(text) <= 3)
        return img

    def press(self):
        if not self.name:
            return
        state = connections().state(self.name)
        verb = "down" if state in ("activated", "activating") else "up"
        launch(f"nmcli connection {verb} {shlex.quote(self.name)}")
