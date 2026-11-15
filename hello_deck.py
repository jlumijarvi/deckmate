#!/usr/bin/env python3
"""Hardware check: say hello to an Elgato Stream Deck (Original V2 / MK.2) on Linux.

Talks the stock HID protocol straight through /dev/hidrawN, no libraries
besides Pillow. Paints a numbered colour tile on every key, reports key
presses, and resets the deck to the Elgato splash on exit.

Usage:
    python3 hello_deck.py                 # run until Ctrl-C
    python3 hello_deck.py --seconds 10    # run for 10 s, then exit
    python3 hello_deck.py --preview out.png   # also save the board as an image
    python3 hello_deck.py --brightness 40
"""
import argparse
import signal
import sys
import time

from PIL import Image, ImageDraw, ImageFont

from deckhid import (Deck, UnsupportedDeck, find_deck,
                     COLS, ROWS, KEY_COUNT, KEY_PX, PRODUCT_IDS)

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


# --- drawing -----------------------------------------------------------------

PALETTE = [
    (230, 76, 60), (243, 156, 18), (241, 196, 15), (46, 204, 113), (26, 188, 156),
    (52, 152, 219), (155, 89, 182), (236, 112, 99), (255, 140, 0), (0, 150, 136),
    (63, 81, 181), (233, 30, 99), (0, 188, 212), (139, 195, 74), (121, 85, 72),
]


def render_key(key, pressed=False):
    bg = (255, 255, 255) if pressed else PALETTE[key % len(PALETTE)]
    fg = (0, 0, 0) if pressed else (255, 255, 255)
    img = Image.new("RGB", (KEY_PX, KEY_PX), bg)
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype(FONT_PATH, 30)
    small = ImageFont.truetype(FONT_PATH, 12)
    label = str(key)
    w = d.textlength(label, font=big)
    d.text(((KEY_PX - w) / 2, 14), label, font=big, fill=fg)
    pos = f"r{key // COLS} c{key % COLS}"
    w = d.textlength(pos, font=small)
    d.text(((KEY_PX - w) / 2, 52), pos, font=small, fill=fg)
    return img


def render_board(pressed):
    gap = 10
    board = Image.new("RGB", (COLS * KEY_PX + (COLS + 1) * gap,
                              ROWS * KEY_PX + (ROWS + 1) * gap), (20, 20, 20))
    for k in range(KEY_COUNT):
        x = gap + (k % COLS) * (KEY_PX + gap)
        y = gap + (k // COLS) * (KEY_PX + gap)
        board.paste(render_key(k, pressed[k]), (x, y))
    return board


# --- main --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=float, default=None,
                    help="exit after this many seconds (default: run until Ctrl-C)")
    ap.add_argument("--brightness", type=int, default=60)
    ap.add_argument("--preview", metavar="PNG", help="save the board as an image too")
    args = ap.parse_args()

    try:
        found = find_deck()
    except UnsupportedDeck as e:
        sys.exit(str(e))
    if not found:
        sys.exit("No Stream Deck found. Is it plugged in? (lsusb should list 0fd9:006d or 0fd9:0080)")
    node, pid, name = found

    try:
        deck = Deck(node)
    except PermissionError:
        sys.exit(f"Cannot open {node}: permission denied. Log in at the local seat, "
                 f"or add a udev rule giving your user access.")
    print(f"{PRODUCT_IDS[pid]} ({name}) at {node}")
    print(f"  product id {pid:#06x}, serial {deck.serial()}, firmware {deck.firmware()}")

    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("now", True))

    pressed = [False] * KEY_COUNT
    try:
        deck.reset()
        deck.set_brightness(args.brightness)
        t0 = time.monotonic()
        for k in range(KEY_COUNT):
            deck.set_key_image(k, render_key(k))
        print(f"  painted {KEY_COUNT} keys in {time.monotonic() - t0:.2f}s, brightness {args.brightness}%")
        if args.preview:
            render_board(pressed).save(args.preview)
            print(f"  preview saved to {args.preview}")
        print("Press keys on the deck. Ctrl-C to quit.")

        deadline = time.monotonic() + args.seconds if args.seconds else None
        while not stop["now"]:
            if deadline and time.monotonic() >= deadline:
                break
            states = deck.read_keys(0.2)
            if states is None:
                continue
            for k, (was, now) in enumerate(zip(pressed, states, strict=True)):
                if was != now:
                    print(f"  key {k:2d} {'pressed ' if now else 'released'}  (row {k // COLS}, col {k % COLS})")
                    deck.set_key_image(k, render_key(k, now))
            pressed = states
    except KeyboardInterrupt:
        pass
    finally:
        try:
            deck.reset()  # back to the Elgato splash
        except OSError:
            pass
        deck.close()
        print("Deck reset, bye.")


if __name__ == "__main__":
    main()
