"""Media players over MPRIS (org.mpris.MediaPlayer2) on the session D-Bus, the
interface Spotify and most Linux players publish and GNOME's own media controls
use. Read-only apart from the play / pause / next / previous calls. No login.

A player is addressed by the part of its bus name after the prefix: "spotify"
for org.mpris.MediaPlayer2.spotify. Browsers add an instance to theirs
(org.mpris.MediaPlayer2.firefox.instance_1_23, ...chromium.instance559273;
Chrome calls itself chromium) and publish one only while a tab has media, so a
player name also matches names with a suffix, and "browser" matches any
browser. With several instances the one playing wins, then a paused one.
Metadata keys used: xesam:title, xesam:artist (a list; Spotify leaves it empty
for podcasts; a browser puts the channel or site there), xesam:album,
mpris:artUrl (an https URL for Spotify and Firefox, file:// for Chrome and
most other players).
"""
import io
import threading
import time
import urllib.request
from dataclasses import dataclass

try:
    from gi.repository import Gio, GLib
except (ImportError, ValueError):   # no python3-gi: the key shows "no D-Bus"
    Gio = GLib = None

BUS_PREFIX = "org.mpris.MediaPlayer2."
OBJECT_PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
REFRESH_S = 1.0
BROWSERS = ("firefox", "chromium", "chrome", "brave", "vivaldi", "opera", "epiphany", "librewolf")
ART_PX = 72
ART_CACHE = 8


@dataclass
class Track:
    title: str = ""
    artist: str = ""
    album: str = ""
    art_url: str = ""


def parse_metadata(md):
    """Track from an MPRIS Metadata dict (already unpacked to Python values)."""
    md = md or {}
    artist = md.get("xesam:artist") or ""
    if isinstance(artist, (list, tuple)):
        artist = ", ".join(a for a in artist if a)
    return Track(title=str(md.get("xesam:title") or ""), artist=str(artist),
                 album=str(md.get("xesam:album") or ""), art_url=str(md.get("mpris:artUrl") or ""))


def candidates(names, player):
    """The bus names among `names` that are this player: the exact name, or one
    with an instance suffix; "browser" takes any browser."""
    out = []
    for n in names:
        if not n.startswith(BUS_PREFIX):
            continue
        stem = n[len(BUS_PREFIX):].split(".", 1)[0]
        if stem == player or (player == "browser" and stem in BROWSERS):
            out.append(n)
    return sorted(out)


def pick(statuses):
    """Of several instances ({bus name: PlaybackStatus}) the one playing, else a
    paused one, else the first by name; None without any."""
    rank = {"Playing": 0, "Paused": 1}
    return min(statuses, key=lambda n: (rank.get(statuses[n], 2), n)) if statuses else None


def is_browser(player):
    return player == "browser" or player in BROWSERS


def cover(img, px=ART_PX):
    """Scale to fill a px square and crop the middle."""
    scale = max(px / img.width, px / img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
    x, y = (img.width - px) // 2, (img.height - px) // 2
    return img.crop((x, y, x + px, y + px))


class ArtCache:
    """Album art by URL, fetched in a background thread; get() returns None until
    the picture is there (or for good, when it cannot be fetched)."""

    def __init__(self, fetch=None):
        self.fetch = fetch or self._fetch
        self._images = {}        # url -> PIL image or False
        self._pending = set()
        self._lock = threading.Lock()

    @staticmethod
    def _fetch(url):
        if url.startswith("file://"):
            with open(urllib.request.url2pathname(url[7:]), "rb") as f:
                return f.read()
        req = urllib.request.Request(url, headers={"User-Agent": "deckmate"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()

    def get(self, url):
        if not url:
            return None
        with self._lock:
            if url in self._images:
                return self._images[url] or None
            if url in self._pending:
                return None
            self._pending.add(url)
        threading.Thread(target=self._load, args=(url,), daemon=True).start()
        return None

    def _load(self, url):
        from PIL import Image
        try:
            img = cover(Image.open(io.BytesIO(self.fetch(url))).convert("RGB"))
        except Exception:
            img = False
        with self._lock:
            if len(self._images) >= ART_CACHE:
                self._images.pop(next(iter(self._images)))
            self._images[url] = img
            self._pending.discard(url)


class MediaPlayer:
    """One MPRIS player, polled at most once a second."""

    def __init__(self, name="spotify", art=None):
        self.name = name
        self.bus_name = BUS_PREFIX + name
        self.art = art or ArtCache()
        self.running = False
        self.status = ""          # Playing | Paused | Stopped | ''
        self.track = Track()
        self.error = ""           # why nothing can be read ("no D-Bus")
        self._bus = None
        self._last = 0.0

    def _connection(self):
        if self._bus is None and Gio is not None:
            try:
                self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            except GLib.Error as e:
                self.error = f"no D-Bus: {e.message}"
                self._bus = False
        return self._bus or None

    def _call(self, iface, method, args=None, timeout_ms=800, bus_name=None):
        bus = self._connection()
        if bus is None:
            return None
        return bus.call_sync(bus_name or self.bus_name, OBJECT_PATH, iface, method, args, None,
                             Gio.DBusCallFlags.NO_AUTO_START, timeout_ms, None)

    def _names(self):
        """Every name on the session bus."""
        bus = self._connection()
        if bus is None:
            return []
        res = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                            None, None, Gio.DBusCallFlags.NONE, 800, None)
        return list(res.unpack()[0])

    def _player_props(self):
        """The Player properties of the instance to show, or None when none runs."""
        found = {}
        for name in candidates(self._names(), self.name):
            try:
                res = self._call(PROPS_IFACE, "GetAll", GLib.Variant("(s)", (PLAYER_IFACE,)), bus_name=name)
            except GLib.Error:            # gone between the listing and the call
                continue
            if res is not None:
                found[name] = res.unpack()[0]
        chosen = pick({n: str(p.get("PlaybackStatus") or "") for n, p in found.items()})
        if chosen is None:
            return None
        self.bus_name = chosen
        return found[chosen]

    def refresh(self):
        now = time.monotonic()
        if now - self._last < REFRESH_S:
            return
        self._last = now
        if Gio is None:
            self.error, self.running = "no D-Bus", False
            return
        try:
            props = self._player_props()
        except GLib.Error:            # no reply from the bus
            self.running, self.status = False, ""
            return
        if props is None:
            self.running, self.status = False, ""
            return
        self.running = True
        self.status = str(props.get("PlaybackStatus") or "")
        self.track = parse_metadata(props.get("Metadata"))

    def artwork(self):
        return self.art.get(self.track.art_url) if self.running else None

    def _action(self, method):
        try:
            self._call(PLAYER_IFACE, method)
        except GLib.Error:
            pass
        self._last = 0.0      # re-read on the next tick

    def play_pause(self):
        self._action("PlayPause")

    def next(self):
        self._action("Next")

    def previous(self):
        self._action("Previous")


_players = {}


def player(name):
    if name not in _players:
        _players[name] = MediaPlayer(name)
    return _players[name]
