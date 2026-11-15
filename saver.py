"""The screen saver: a picture across the whole screen, or a big clock drawn
one character per key."""
import logging
import os
from datetime import datetime

from PIL import Image, ImageDraw

from deckhid import KEY_COUNT, KEY_PX, LCD_H, LCD_W
from render import font

log = logging.getLogger("deck")


class ScreenSaver:
    """Renders the idle picture: a file, or a big clock."""

    def __init__(self, cfg):
        self.image_setting = cfg.get("image", "clock") or "clock"
        self.timeout_s = float(cfg.get("timeout_min", 0) or 0) * 60
        self.sleep_s = int(float(cfg.get("sleep_min", 0) or 0) * 60)
        self._picture = None
        self._picture_err = None

    @property
    def enabled(self):
        return self.timeout_s > 0

    def picture(self):
        if self._picture is None and self._picture_err is None:
            try:
                img = Image.open(os.path.expanduser(self.image_setting)).convert("RGB")
                # cover: scale to fill 480x272, crop the overflow in the middle
                scale = max(LCD_W / img.width, LCD_H / img.height)
                img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
                x = (img.width - LCD_W) // 2
                y = (img.height - LCD_H) // 2
                self._picture = img.crop((x, y, x + LCD_W, y + LCD_H))
            except (OSError, ValueError) as e:
                self._picture_err = str(e)
                log.info(f"screensaver: cannot load {self.image_setting}: {e}; using the clock")
        return self._picture

    @property
    def is_tiles(self):
        """True when the saver is drawn as 15 key images (the clock) rather than one
        full-screen picture. Key images cannot be cut by the bezels between keys."""
        return self.image_setting == "clock" or self.picture() is None

    def render(self, final=False):
        """Full-screen frame, for picture savers only."""
        return self.picture()

    @staticmethod
    def _tile(text="", size=64, fg=(235, 235, 240), bold=True, dy=0):
        img = Image.new("RGB", (KEY_PX, KEY_PX), (8, 8, 12))
        if text:
            d = ImageDraw.Draw(img)
            while size > 10 and d.textlength(text, font=font(size, bold)) > KEY_PX - 6:
                size -= 2
            f = font(size, bold)
            w = d.textlength(text, font=f)
            # centre on the glyph box so digits of different heights sit on one line
            box = f.getbbox(text)
            h = box[3] - box[1]
            d.text(((KEY_PX - w) / 2, (KEY_PX - h) / 2 - box[1] + dy), text, font=f, fill=fg)
        return img

    def render_tiles(self, final=False):
        """The clock as 15 key images: date on the top row, HH:MM on the middle row.
        final=True (left behind on exit) drops the time, which would freeze."""
        now = datetime.now()
        t = self._tile
        dim = (150, 150, 165)
        tiles = [t() for _ in range(KEY_COUNT)]
        date_parts = [now.strftime("%a"), now.strftime("%-d.%-m."), now.strftime("%Y")]
        # one size for all three date tiles, the largest where the widest part fits
        size = 30
        probe = ImageDraw.Draw(tiles[0])
        while size > 10 and max(probe.textlength(x, font=font(size, False)) for x in date_parts) > KEY_PX - 6:
            size -= 2
        for i, text in enumerate(date_parts, start=1):
            tiles[i] = t(text, size=size, fg=dim, bold=False)
        if not final:
            hh, mm = now.strftime("%H"), now.strftime("%M")
            tiles[5] = t(hh[0])
            tiles[6] = t(hh[1])
            tiles[7] = t(":", dy=-4)
            tiles[8] = t(mm[0])
            tiles[9] = t(mm[1])
        return tiles
