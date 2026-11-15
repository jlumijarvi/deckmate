"""Media players over MPRIS (org.mpris.MediaPlayer2) on the session D-Bus, the
interface Spotify and most Linux players publish and GNOME's own media controls
use. Read-only apart from the play / pause / next / previous calls. No login.

A player is addressed by the last part of its bus name: "spotify" for
org.mpris.MediaPlayer2.spotify. Metadata keys used: xesam:title, xesam:artist
(a list; Spotify leaves it empty for podcasts), xesam:album, mpris:artUrl (an
https URL for Spotify, file:// for most other players).
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

    def _call(self, iface, method, args=None, timeout_ms=800):
        bus = self._connection()
        if bus is None:
            return None
        return bus.call_sync(self.bus_name, OBJECT_PATH, iface, method, args, None,
                             Gio.DBusCallFlags.NO_AUTO_START, timeout_ms, None)

    def refresh(self):
        now = time.monotonic()
        if now - self._last < REFRESH_S:
            return
        self._last = now
        if Gio is None:
            self.error, self.running = "no D-Bus", False
            return
        try:
            res = self._call(PROPS_IFACE, "GetAll", GLib.Variant("(s)", (PLAYER_IFACE,)))
        except GLib.Error:            # not running (ServiceUnknown), or no reply
            self.running, self.status = False, ""
            return
        if res is None:
            self.running = False
            return
        props = res.unpack()[0]
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
