"""Folder keys: a key that opens a set of keys of its own (Elgato's "folder").
Key 0 inside is the back key; hold it to return to the main board from any depth."""
from deckhid import KEY_PX
from keys import Key
from pages import back_key, nav
from render import draw_centered_lines, fit_lines, hex_color, tile

FOLDER_COLOR = (74, 74, 90)


class FolderPage:
    def __init__(self, keys):
        self._keys = list(keys)
        self._keys[0] = back_key()

    def keys(self):
        return self._keys


class Folder(Key):
    """cfg: "title", "color" and "keys" (the folder's own keys.json-style map;
    key 0 is always the back key). Press opens the folder."""

    def __init__(self, cfg, keys):
        super().__init__(cfg)
        self.page = FolderPage(keys)

    def render(self, pressed):
        img, d, fg = tile(hex_color(self.cfg.get("color"), FOLDER_COLOR), pressed)
        lines, size = fit_lines(self.cfg.get("title") or "Folder")
        draw_centered_lines(d, lines, fg, size=size)
        # a small folder tab bottom right says there is more behind this key
        x, y = KEY_PX - 19, KEY_PX - 15
        d.rectangle([x, y - 2, x + 5, y], fill=fg)
        d.rectangle([x, y, x + 13, y + 9], outline=fg, width=1)
        return img

    def press(self):
        nav.push(self.page)
