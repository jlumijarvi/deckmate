"""Minimal Elgato Stream Deck (Original V2 / MK.2) driver over Linux hidraw.

Stock HID protocol only, the same reports the Elgato software sends.
No dependencies besides Pillow for the key images.
"""
import fcntl
import glob
import io
import os
import select

VENDOR_ID = 0x0FD9
PRODUCT_IDS = {0x006D: "Stream Deck Original V2", 0x0080: "Stream Deck MK.2"}

COLS, ROWS = 5, 3
KEY_COUNT = COLS * ROWS
KEY_PX = 72
LCD_W, LCD_H = 480, 272   # the whole screen behind the keys

IMAGE_REPORT_LEN = 1024
IMAGE_HEADER_LEN = 8
IMAGE_PAYLOAD_LEN = IMAGE_REPORT_LEN - IMAGE_HEADER_LEN
FEATURE_LEN = 32
INPUT_LEN = 512


def _ioc(direction, type_char, nr, size):
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | nr


def _hidiocsfeature(size):
    return _ioc(3, "H", 0x06, size)  # _IOC_WRITE | _IOC_READ


def _hidiocgfeature(size):
    return _ioc(3, "H", 0x07, size)


class UnsupportedDeck(Exception):
    pass


def find_deck():
    """Return (devnode, product_id, hid_name) for the first supported deck, or None.

    Raises UnsupportedDeck if an Elgato device is present but not a 15-key deck.
    """
    unsupported = None
    for uevent in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        info = {}
        with open(uevent) as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    info[k] = v
        parts = info.get("HID_ID", "").split(":")
        if len(parts) != 3:
            continue
        vid, pid = int(parts[1], 16), int(parts[2], 16)
        if vid != VENDOR_ID:
            continue
        node = "/dev/" + uevent.split("/")[4]
        if pid not in PRODUCT_IDS:
            unsupported = (node, pid)
            continue
        return node, pid, info.get("HID_NAME", "?")
    if unsupported:
        node, pid = unsupported
        raise UnsupportedDeck(f"Elgato device at {node} with product id {pid:#06x} is not "
                              f"supported; only {', '.join(PRODUCT_IDS.values())} are.")
    return None


class Deck:
    def __init__(self, node):
        self.node = node
        self.fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    # --- feature reports ---
    def set_feature(self, data):
        buf = bytearray(FEATURE_LEN)
        buf[: len(data)] = data
        fcntl.ioctl(self.fd, _hidiocsfeature(FEATURE_LEN), buf)

    def get_feature(self, report_id):
        buf = bytearray(FEATURE_LEN)
        buf[0] = report_id
        fcntl.ioctl(self.fd, _hidiocgfeature(FEATURE_LEN), buf)
        return bytes(buf)

    @staticmethod
    def _cstr(b):
        return b.split(b"\x00", 1)[0].decode("ascii", "replace")

    def serial(self):
        return self._cstr(self.get_feature(0x06)[2:])

    def firmware(self):
        return self._cstr(self.get_feature(0x05)[6:])

    def reset(self):
        """Clear all keys; the deck shows the Elgato splash."""
        self.set_feature(b"\x03\x02")

    def set_brightness(self, percent):
        percent = max(0, min(100, int(percent)))
        self.set_feature(bytes([0x03, 0x08, percent]))

    def fill_color(self, r, g, b):
        """Fill the whole screen with one colour (documented feature 0x03 0x05)."""
        self.set_feature(bytes([0x03, 0x05, r, g, b]))

    def set_sleep_timeout(self, seconds):
        """Idle seconds before the deck switches its backlight off; 0 disables."""
        self.set_feature(bytes([0x03, 0x0D]) + int(seconds).to_bytes(4, "little", signed=True))

    def get_sleep_timeout(self):
        b = self.get_feature(0x0A)
        return int.from_bytes(b[2:6], "little", signed=True)

    # --- key images ---
    @staticmethod
    def encode_image(image, quality=90):
        """PIL image (as the user should see it) -> JPEG bytes ready for the deck."""
        img = image.convert("RGB")
        if img.size != (KEY_PX, KEY_PX):
            img = img.resize((KEY_PX, KEY_PX))
        buf = io.BytesIO()
        img.rotate(180).save(buf, "JPEG", quality=quality)
        return buf.getvalue()

    def set_key_jpeg(self, key, data):
        page = 0
        while data:
            chunk, data = data[:IMAGE_PAYLOAD_LEN], data[IMAGE_PAYLOAD_LEN:]
            is_last = 0 if data else 1
            header = bytes([0x02, 0x07, key, is_last,
                            len(chunk) & 0xFF, len(chunk) >> 8,
                            page & 0xFF, page >> 8])
            self._write_all(header + chunk + bytes(IMAGE_PAYLOAD_LEN - len(chunk)))
            page += 1

    def set_key_image(self, key, image):
        self.set_key_jpeg(key, self.encode_image(image))

    # --- whole screen ---
    @staticmethod
    def encode_screen(image, quality=90):
        """PIL image -> 480x272 JPEG bytes for the full-screen command."""
        img = image.convert("RGB")
        if img.size != (LCD_W, LCD_H):
            img = img.resize((LCD_W, LCD_H))
        buf = io.BytesIO()
        img.rotate(180).save(buf, "JPEG", quality=quality)
        return buf.getvalue()

    def set_screen_jpeg(self, data):
        """Paint one picture across the whole screen (output 0x02 0x08)."""
        page = 0
        while data:
            chunk, data = data[:IMAGE_PAYLOAD_LEN], data[IMAGE_PAYLOAD_LEN:]
            is_last = 0 if data else 1
            header = bytes([0x02, 0x08, 0x00, is_last,
                            len(chunk) & 0xFF, len(chunk) >> 8,
                            page & 0xFF, page >> 8])
            self._write_all(header + chunk + bytes(IMAGE_PAYLOAD_LEN - len(chunk)))
            page += 1

    def set_screen_image(self, image):
        self.set_screen_jpeg(self.encode_screen(image))

    def show_logo(self):
        """Show the image stored inside the deck (same report as reset)."""
        self.reset()

    # --- stored images ---
    def set_background_jpeg(self, index, data):
        """Store a 480x272 JPEG inside the deck at background slot `index`
        (documented output report 0x02 0x0D). This overwrites what the slot held
        and cannot be undone. Note the chunk index comes before the size here,
        the reverse of the key and screen image headers."""
        page = 0
        while data:
            chunk, data = data[:IMAGE_PAYLOAD_LEN], data[IMAGE_PAYLOAD_LEN:]
            is_last = 0 if data else 1
            header = bytes([0x02, 0x0D, index, is_last,
                            page & 0xFF, page >> 8,
                            len(chunk) & 0xFF, len(chunk) >> 8])
            self._write_all(header + chunk + bytes(IMAGE_PAYLOAD_LEN - len(chunk)))
            page += 1

    def unit_info(self):
        """Decoded 'Get Unit Information' report."""
        b = self.get_feature(0x08)
        return {
            "rows": b[1], "cols": b[2],
            "key_w": int.from_bytes(b[3:5], "little"), "key_h": int.from_bytes(b[5:7], "little"),
            "lcd_w": int.from_bytes(b[7:9], "little"), "lcd_h": int.from_bytes(b[9:11], "little"),
            "key_gallery": b[13], "lcd_gallery": b[14], "demo_frames": b[15],
        }

    def _write_all(self, report):
        while True:
            try:
                n = os.write(self.fd, report)
                if n != len(report):
                    raise OSError(f"short write {n}/{len(report)}")
                return
            except BlockingIOError:
                select.select([], [self.fd], [], 0.1)

    # --- key states ---
    def read_keys(self, timeout):
        """List of KEY_COUNT bools (True = pressed), or None if nothing arrived."""
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return None
        data = os.read(self.fd, INPUT_LEN)
        if len(data) < 4 + KEY_COUNT or data[0] != 0x01:
            return None
        return [b != 0 for b in data[4: 4 + KEY_COUNT]]
