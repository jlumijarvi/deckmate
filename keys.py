"""The key base class and the plain key types: blank, command, clock, mute, volume."""
from datetime import datetime

from PIL import Image

from deckhid import KEY_PX
from render import draw_centered_lines, fit_lines, hex_color, tile
from system import launch, sh

REFRESH_S = 1.0      # data refresh and static repaint
FRAME_S = 0.05       # repaint interval while something animates
SCROLL_PX_S = 30.0   # scrolling title speed
HOLD_S = 0.6         # a press this long is a hold, for keys that have a hold action


class Key:
    dynamic = False   # True: re-render on every refresh tick
    holdable = False  # True: tap fires on release, hold() fires after HOLD_S

    def __init__(self, cfg):
        self.cfg = cfg

    def render(self, pressed):
        raise NotImplementedError

    def tap(self):
        pass

    def hold(self):
        pass

    def render_held(self):
        """What to show right after hold() ran."""
        return self.render(True)


class Blank(Key):
    def render(self, pressed):
        return Image.new("RGB", (KEY_PX, KEY_PX), (0, 0, 0))


class Command(Key):
    def render(self, pressed):
        img, d, fg = tile(hex_color(self.cfg.get("color")), pressed)
        lines, size = fit_lines(self.cfg.get("label", ""))
        draw_centered_lines(d, lines, fg, size=size)
        return img

    def tap(self):
        launch(self.cfg["run"])


class Clock(Key):
    dynamic = True

    def render(self, pressed):
        now = datetime.now()
        img, d, fg = tile((20, 20, 30), pressed)
        draw_centered_lines(d, [now.strftime("%H:%M")], fg, size=22, top=12)
        draw_centered_lines(d, [now.strftime("%a %-d.%-m.")], (190, 190, 200) if not pressed else fg,
                            size=12, top=44, bold=False)
        return img


class Mute(Key):
    dynamic = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self.target = "@DEFAULT_AUDIO_SOURCE@" if cfg.get("target") == "source" else "@DEFAULT_AUDIO_SINK@"

    def muted(self):
        return "[MUTED]" in sh(f"wpctl get-volume {self.target}")

    def render(self, pressed):
        muted = self.muted()
        img, d, fg = tile((180, 40, 40) if muted else (40, 120, 60), pressed)
        draw_centered_lines(d, [self.cfg.get("label", "Audio")], fg, size=14, top=14)
        draw_centered_lines(d, ["MUTED" if muted else "on"], fg, size=12, top=40, bold=False)
        return img

    def tap(self):
        sh(f"wpctl set-mute {self.target} toggle")


class Volume(Key):
    dynamic = True

    def volume(self):
        out = sh("wpctl get-volume @DEFAULT_AUDIO_SINK@")
        try:
            return int(round(float(out.split()[1]) * 100))
        except (IndexError, ValueError):
            return None

    def render(self, pressed):
        step = int(self.cfg.get("step", 5))
        vol = self.volume()
        img, d, fg = tile((50, 60, 90), pressed)
        draw_centered_lines(d, ["Vol +" if step > 0 else "Vol -"], fg, size=16, top=12)
        draw_centered_lines(d, ["?" if vol is None else f"{vol}%"], fg, size=13, top=42, bold=False)
        return img

    def tap(self):
        step = int(self.cfg.get("step", 5))
        sign = "+" if step > 0 else "-"
        sh(f"wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ {abs(step)}%{sign}")
