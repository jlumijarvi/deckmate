"""Media keys: play / pause with the track and its album art, next, previous.
Any MPRIS player; Spotify by default."""
from PIL import ImageDraw

from deckhid import KEY_PX
from keys import Key
from mpris import player
from render import dim, draw_centered_lines, fit_lines, tile
from system import launch

ACTIONS = ("playpause", "next", "previous")
PLAYER_NAMES = {"spotify": "Spotify"}
PLAYER_COLORS = {"spotify": (30, 215, 96)}    # Spotify green
GLYPH = (235, 235, 240)


def play_glyph(d, cx, cy, size, color):
    d.polygon([(cx - size * 0.45, cy - size * 0.6), (cx + size * 0.6, cy), (cx - size * 0.45, cy + size * 0.6)],
              fill=color)


def pause_glyph(d, cx, cy, size, color):
    w = size * 0.35
    d.rectangle([cx - size * 0.6, cy - size * 0.6, cx - size * 0.6 + w, cy + size * 0.6], fill=color)
    d.rectangle([cx + size * 0.6 - w, cy - size * 0.6, cx + size * 0.6, cy + size * 0.6], fill=color)


def skip_glyph(d, cx, cy, size, color, forward=True):
    s = size if forward else -size
    d.polygon([(cx - s * 0.9, cy - size * 0.6), (cx + s * 0.1, cy), (cx - s * 0.9, cy + size * 0.6)], fill=color)
    d.polygon([(cx - s * 0.1, cy - size * 0.6), (cx + s * 0.9, cy), (cx - s * 0.1, cy + size * 0.6)], fill=color)
    bar = [cx + s * 0.9, cy - size * 0.6, cx + s * 1.15, cy + size * 0.6]
    d.rectangle([min(bar[0], bar[2]), bar[1], max(bar[0], bar[2]), bar[3]], fill=color)


class Media(Key):
    """action playpause (default): the track, album art when there is one, a play or
    pause glyph; press toggles. next / previous: plain skip keys. When the player
    is not running the play/pause key says so and a press launches it."""
    dynamic = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self.action = cfg.get("action") or "playpause"
        if self.action not in ACTIONS:
            self.action = "playpause"
        self.player_name = cfg.get("player") or "spotify"
        self.player = player(self.player_name)

    @property
    def label(self):
        return self.cfg.get("title") or PLAYER_NAMES.get(self.player_name, self.player_name)

    def render(self, pressed):
        p = self.player
        p.refresh()
        color = PLAYER_COLORS.get(self.player_name, (90, 90, 100))
        if self.action != "playpause":
            img, d, fg = tile(dim(color, 0.3 if p.running else 0.15), pressed)
            skip_glyph(d, KEY_PX / 2, KEY_PX / 2 - 4, 16, (0, 0, 0) if pressed else GLYPH,
                       forward=self.action == "next")
            draw_centered_lines(d, ["next" if self.action == "next" else "previous"],
                                (200, 200, 210) if not pressed else fg, size=10, top=52, bold=False)
            return img
        if not p.running:
            img, d, fg = tile(dim(color, 0.15), pressed)
            lines, size = fit_lines(self.label, max_lines=1, sizes=(14, 12, 11, 10, 9))
            draw_centered_lines(d, lines, fg, size=size, top=18)
            draw_centered_lines(d, [p.error or "not running"], (160, 160, 170) if not pressed else fg,
                                size=10, top=42, bold=False)
            return img
        art = p.artwork()
        if art is not None and not pressed:
            img = art.copy()
            d = ImageDraw.Draw(img)
            # a dark corner so the glyph reads on any cover
            d.rectangle([KEY_PX - 24, KEY_PX - 24, KEY_PX, KEY_PX], fill=(0, 0, 0))
            fg = GLYPH
        else:
            img, d, fg = tile(dim(color, 0.3), pressed)
            t = p.track
            lines, size = fit_lines(t.title or self.label, max_lines=2, sizes=(12, 11, 10, 9), width=KEY_PX - 6)
            draw_centered_lines(d, lines, fg, size=size, top=8)
            if t.artist:
                al, asize = fit_lines(t.artist, max_lines=1, sizes=(10, 9), width=KEY_PX - 6)
                draw_centered_lines(d, al, (200, 200, 210) if not pressed else fg, size=asize, top=40, bold=False)
            d.rectangle([KEY_PX - 24, KEY_PX - 24, KEY_PX, KEY_PX], fill=(0, 0, 0) if not pressed else (255, 255, 255))
        glyph_color = (0, 0, 0) if pressed else GLYPH
        if p.status == "Playing":
            pause_glyph(d, KEY_PX - 12, KEY_PX - 12, 9, glyph_color)
        else:
            play_glyph(d, KEY_PX - 12, KEY_PX - 12, 9, glyph_color)
        return img

    def press(self):
        p = self.player
        if self.action == "next":
            p.next()
        elif self.action == "previous":
            p.previous()
        elif p.running:
            p.play_pause()
        else:
            launch(self.cfg.get("launch") or self.player_name)
