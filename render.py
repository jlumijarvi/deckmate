"""Drawing helpers shared by every key: fonts, colours, text layout, the corner
mark, and the board preview image."""
import re

from PIL import Image, ImageDraw, ImageFont

from deckhid import COLS, KEY_PX, ROWS

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


_fonts = {}


def font(size, bold=True):
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    return _fonts[key]


def hex_color(s, default=(60, 60, 60)):
    if not s:
        return default
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


# --- drawing -----------------------------------------------------------------

def tile(bg, pressed=False):
    img = Image.new("RGB", (KEY_PX, KEY_PX), (255, 255, 255) if pressed else bg)
    return img, ImageDraw.Draw(img), (0, 0, 0) if pressed else (255, 255, 255)


def draw_centered_lines(d, lines, fg, size=14, top=None, bold=True, left=0):
    """Centred in the key, or in the part right of `left` (room for a corner mark)."""
    f = font(size, bold)
    line_h = size + 3
    total = line_h * len(lines)
    y = (KEY_PX - total) / 2 if top is None else top
    for line in lines:
        w = d.textlength(line, font=f)
        d.text((left + (KEY_PX - left - w) / 2, y), line, font=f, fill=fg)
        y += line_h


def fit_lines(label, max_lines=3, sizes=(14, 13, 12, 11, 10, 9), width=KEY_PX - 6):
    """Split a label into lines and pick a font size where every line fits.
    Explicit newlines are kept; otherwise wrap after spaces, hyphens, underscores
    and slashes. Among readable sizes (11 px and up) the fewest lines win, then the
    largest size; smaller sizes are a last resort. Returns (lines, size)."""
    def wrap(size):
        f = font(size)
        out = []
        for raw in label.split("\n"):
            tokens = [t for t in re.split(r"(?<=[ \-_/])", raw) if t]
            cur = ""
            for t in tokens:
                cand = cur + t
                if cur and f.getlength(cand.rstrip()) > width:
                    out.append(cur.rstrip())
                    cur = t.lstrip()
                else:
                    cur = cand
            out.append(cur.rstrip())
        fits = len(out) <= max_lines and all(f.getlength(line) <= width for line in out)
        return out, fits

    candidates = []
    for size in sizes:
        out, fits = wrap(size)
        if fits:
            candidates.append((len(out) + (1 if size < 11 else 0), -size, out, size))
    if candidates:
        _, _, out, size = min(candidates)
        return out, size
    out, _ = wrap(sizes[-1])
    if len(out) > max_lines:
        out = out[:max_lines]
        out[-1] = out[-1].rstrip() + "…"
    return out, sizes[-1]


CYAN = (0, 200, 220)

STATE_COLORS = {
    "waiting": (245, 166, 35),
    "running": (52, 140, 235),
    "done": (46, 180, 100),
    "stopped": (120, 120, 120),
    "failed": (220, 60, 60),
    "gone": (90, 90, 100),
}


def dim(color, f=0.22):
    return tuple(int(c * f) for c in color)


MARK = (217, 119, 87)     # warm orange, the colour of the corner mark
MARK_W = 17               # width the corner mark takes from the left edge


def code_mark(d, pressed, x=4, cy=KEY_PX - 10):
    """A small terminal prompt (>_) in the bottom-left corner: this key is about
    Claude Code sessions, not an ordinary button."""
    color = (0, 0, 0) if pressed else MARK
    d.line([(x, cy - 5), (x + 4, cy), (x, cy + 5)], fill=color, width=2, joint="curve")
    d.line([(x + 7, cy + 5), (x + 12, cy + 5)], fill=color, width=2)


def board_image(images):
    gap = 10
    board = Image.new("RGB", (COLS * KEY_PX + (COLS + 1) * gap,
                              ROWS * KEY_PX + (ROWS + 1) * gap), (20, 20, 20))
    for k, img in enumerate(images):
        board.paste(img, (gap + (k % COLS) * (KEY_PX + gap), gap + (k // COLS) * (KEY_PX + gap)))
    return board
