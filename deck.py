#!/usr/bin/env python3
"""Deckmate: the board. Keys configured in keys.json, painted on an Elgato Stream Deck.

Key types (see keys.json):
  command  (default)  title, color, run       press runs the shell command
  clock               shows time and date, updates every second
  mute                target "source" (mic) or "sink" (speaker); shows state, press toggles
  volume              step (+/- percent); shows the speaker volume, press changes it
  brightness          shows the deck's brightness; press steps it 30 / 60 / 100 ("steps")
  media               action "playpause" (default) | "next" | "previous"; "player" (default
                      spotify, any MPRIS player). play/pause shows the track and its cover
  slack               unread badge for one workspace ("team": id, default: the first
                      workspace found); press opens that workspace in Slack
  todo                open items in a to-do list (a Markdown task list, see todo.py),
                      coloured by urgency; press opens the list as a folder, one key per
                      item, press an item to open its linked file, hold it to mark it done.
                      Per key: "file" (default ~/.config/deckmate/todo.md), "title",
                      "open_with" (default "code"), "soon_days" (0), "hold_done" (true)

A "screensaver" entry: {"image": "clock" | "/path/to/picture", "timeout_min": 5,
"sleep_min": 0}. After timeout_min idle minutes the whole screen shows the picture
or a big clock; any key press brings the board back without running that key.
sleep_min is the deck's own backlight-off timer (0 = never). On exit the saver is
painted one last time, so it stays on the deck's screen instead of the stored image.

  counter             "state" waiting | running | done: how many sessions; press filters
  pager               "dir" prev | next: pages of session slots; hold prev: hidden sessions
  stats               percent left of the session, week and model limits; press opens the
                      stats page; "tiles": ["tokens", "cache", "context"] adds those tiles

A "sessions" entry claims a list of keys for live Claude Code sessions, one per
key, sorted waiting / running / done / stopped / failed. Press opens the session's
folder in VS Code, which raises the window that already has it open.

Usage:
    python3 deck.py                       # run until Ctrl-C
    python3 deck.py --config keys.json
    python3 deck.py --seconds 5 --preview board.png
    python3 deck.py --saver --preview saver.png   # start in screen-saver mode
"""
import argparse
import json
import logging
import logging.handlers
import os
import signal
import sys
import time

from PIL import Image, ImageDraw

import sessionkeys
from board import Effects, PressTracker
from deckhid import COLS, KEY_COUNT, KEY_PX, Deck, UnsupportedDeck, find_deck
from keys import FRAME_S, HOLD_S, REFRESH_S
from layout import load_board
from pages import nav
from paths import LOG_FILE, PID_FILE, STATUS_FILE, config_path, ensure_dirs
from render import CYAN, STATE_COLORS, board_image
from sessionkeys import SessionSlot
from system import seat_active_uid

log = logging.getLogger("deck")
RECONNECT_S = 2.0


def setup_logging():
    """stdout (the journal under systemd) plus a rotating file under ~/.local/state."""
    ensure_dirs()
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(out)
    try:
        fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass


# --- main loop ---------------------------------------------------------------

def open_deck():
    found = find_deck()
    if not found:
        return None
    node, pid, _ = found
    deck = Deck(node)
    log.info(f"connected to {node} (serial {deck.serial()}, firmware {deck.firmware()})")
    return deck


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=None,
                    help="layout file (default: keys.json, or keys.example.json if that is missing)")
    ap.add_argument("--seconds", type=float, default=None, help="exit after N seconds")
    ap.add_argument("--preview", metavar="PNG", help="save the board as an image once painted")
    ap.add_argument("--demo", action="store_true", help="show made-up sessions in every state")
    ap.add_argument("--saver", action="store_true", help="start in screen-saver mode")
    args = ap.parse_args()
    sessionkeys.DEMO = args.demo
    setup_logging()

    if args.config is None:
        args.config = config_path()
    log.info(f"layout: {args.config}")
    keys, brightness, providers, saver = load_board(args.config)
    config_mtime = os.path.getmtime(args.config)
    nav.reset(keys)
    ensure_dirs()
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    last_status = None

    def write_status(connected):
        """A small file for the tray icon: is the deck connected, how many sessions wait."""
        nonlocal last_status
        counts = {"waiting": 0, "running": 0, "done": 0}
        for p in providers:
            view = getattr(p, "view", None)
            if view is not None:
                for st in counts:
                    counts[st] += view.count(st)
        status = {"deck": connected, **counts}
        if status != last_status:
            last_status = status
            try:
                tmp = STATUS_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(status, f)
                os.replace(tmp, STATUS_FILE)
            except OSError:
                pass

    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("now", True))

    deck = None
    pressed = [False] * KEY_COUNT
    presses = PressTracker(HOLD_S)
    sent = [None] * KEY_COUNT        # JPEG bytes last sent per key
    next_open = 0.0
    next_refresh = 0.0
    deadline = time.monotonic() + args.seconds if args.seconds else None
    previewed = False
    last_activity = time.monotonic()
    saver_active = False
    saver_sent = None
    seat_paused = False
    next_seat_check = 0.0

    effects = Effects(COLS)

    def apply_overlays(images, now):
        """Pulse bands and glows on top of the rendered keys."""
        for i in range(KEY_COUNT):
            for kind, color, strength in effects.overlays(now, i):
                base = images[i]
                if kind == "band":
                    layer = Image.new("RGB", base.size, color)
                    images[i] = Image.blend(base, layer, 0.55 * strength)
                elif kind == "glow":
                    d = ImageDraw.Draw(images[i])
                    width = max(1, int(round(4 * strength)))
                    d.rectangle([0, 0, KEY_PX - 1, KEY_PX - 1], outline=color, width=width)
        return images

    def paint(force=False):
        """Render every key, send those that changed."""
        nonlocal sent
        now = time.monotonic()
        for p in providers:
            p.refresh()
        # new pulses found by a scan: sweep the deck, glow the key when it is on the board
        for p in providers:
            for sess, state in getattr(p, "pending_pulses", []):
                idx = next((i for i, k in enumerate(nav.keys())
                            if isinstance(k, SessionSlot) and k.session is sess), None)
                effects.pulse(now, STATE_COLORS[state], idx)
                log.info(f"pulse: {sess.name} -> {state}")
            if hasattr(p, "pending_pulses"):
                p.pending_pulses.clear()
            usage = getattr(p, "usage", None)
            if usage is not None and usage.reset_events:
                effects.pulse(now, CYAN)
                log.info(f"limit window reset: {', '.join(usage.reset_events)}")
        if nav.dirty:
            nav.dirty = False
            sent = [None] * KEY_COUNT
        images = [k.render(pressed[i]) for i, k in enumerate(nav.keys())]
        if effects.active(now):
            images = apply_overlays(images, now)
        if deck is not None:
            for i, img in enumerate(images):
                data = Deck.encode_image(img)
                if force or data != sent[i]:
                    deck.set_key_jpeg(i, data)
                    sent[i] = data
        return images

    try:
        while not stop["now"]:
            now = time.monotonic()
            if deadline and now >= deadline:
                break

            # reload config when edited
            try:
                m = os.path.getmtime(args.config)
            except OSError:
                m = config_mtime
            if m != config_mtime:
                config_mtime = m
                try:
                    keys, brightness, providers, saver = load_board(args.config)
                    nav.reset(keys)
                    log.info("config reloaded")
                    if deck is not None:
                        deck.set_brightness(brightness)
                        deck.set_sleep_timeout(saver.sleep_s)
                    sent = [None] * KEY_COUNT
                    saver_sent = None
                except (OSError, ValueError) as e:
                    log.info(f"config reload failed: {e}")

            # another account owns the screen: let go of the deck, its own deck.py takes over
            if now >= next_seat_check:
                next_seat_check = now + 2.0
                active = seat_active_uid()
                inactive = active is not None and active != os.getuid()
                if inactive and not seat_paused:
                    log.info("another account is active, releasing the deck")
                    seat_paused = True
                    if deck is not None:
                        deck.close()
                        deck = None
                elif seat_paused and not inactive:
                    log.info("this account is active again")
                    seat_paused = False
                    last_activity = now
            if seat_paused:
                time.sleep(0.5)
                continue

            # (re)connect
            if deck is None and now >= next_open:
                next_open = now + RECONNECT_S
                try:
                    deck = open_deck()
                    if deck is None:
                        log.info("no deck found, retrying...")
                    else:
                        deck.fill_color(0, 0, 0)
                        deck.set_brightness(brightness)
                        deck.set_sleep_timeout(saver.sleep_s)
                        sent = [None] * KEY_COUNT
                        saver_sent = None
                        saver_active = args.saver and saver.enabled
                        if not saver_active:
                            paint(force=True)
                        next_refresh = now + REFRESH_S
                except UnsupportedDeck as e:
                    sys.exit(str(e))
                except OSError as e:
                    log.info(f"open failed: {e}")
                    deck = None

            if deck is None:
                write_status(False)
                time.sleep(0.2)
                continue

            try:
                if saver.enabled and not saver_active and now - last_activity >= saver.timeout_s:
                    saver_active = True
                    saver_sent = None
                    deck.fill_color(0, 0, 0)
                    log.info("screen saver on")

                if now >= next_refresh:
                    animating = effects.active(now) or any(getattr(p, "animating", lambda: False)() for p in providers)
                    next_refresh = now + (FRAME_S if animating and not saver_active else REFRESH_S)
                    if saver_active:
                        if saver.is_tiles:
                            tiles = saver.render_tiles()
                            if not isinstance(saver_sent, list):
                                saver_sent = [None] * KEY_COUNT
                            for i, img in enumerate(tiles):
                                data = Deck.encode_image(img)
                                if data != saver_sent[i]:
                                    deck.set_key_jpeg(i, data)
                                    saver_sent[i] = data
                            if args.preview and not previewed:
                                board_image(tiles).save(args.preview)
                                log.info(f"preview saved to {args.preview}")
                                previewed = True
                        else:
                            data = Deck.encode_screen(saver.render())
                            if data != saver_sent:
                                deck.set_screen_jpeg(data)
                                saver_sent = data
                            if args.preview and not previewed:
                                saver.render().save(args.preview)
                                log.info(f"preview saved to {args.preview}")
                                previewed = True
                    else:
                        images = paint()
                        if args.preview and not previewed:
                            board_image(images).save(args.preview)
                            log.info(f"preview saved to {args.preview}")
                            previewed = True
                    write_status(True)

                # holds: a holdable key kept down past HOLD_S fires once
                for _, i, k in presses.tick(time.monotonic()):
                    log.info(f"key {i} hold")
                    try:
                        k.hold()
                    except Exception as e:
                        log.info(f"key {i} hold failed: {e}")
                    data = Deck.encode_image(k.render_held())
                    deck.set_key_jpeg(i, data)
                    sent[i] = data

                states = deck.read_keys(0.05)
                if states is not None:
                    if any(states):
                        last_activity = time.monotonic()
                    if saver_active:
                        if any(states):
                            # wake up: back to the board, and swallow this press
                            saver_active = False
                            saver_sent = None
                            log.info("screen saver off")
                            deck.fill_color(0, 0, 0)
                            sent = [None] * KEY_COUNT
                            paint(force=True)
                            pressed = states
                            presses.reset()
                            next_refresh = time.monotonic() + REFRESH_S
                        continue
                    for i, (was, is_now) in enumerate(zip(pressed, states, strict=True)):
                        if was == is_now:
                            continue
                        pressed[i] = is_now
                        for action, _, k in presses.feed(i, nav.keys()[i], is_now, time.monotonic()):
                            if action == "press":
                                log.info(f"key {i} press")
                                try:
                                    k.press()
                                except Exception as e:  # a bad command must not kill the board
                                    log.info(f"key {i} action failed: {e}")
                            elif action == "release-after-hold":
                                sent[i] = None    # drop the hold feedback
                        if nav.dirty:
                            paint()        # a page changed: redraw everything now
                            continue
                        data = Deck.encode_image(nav.keys()[i].render(is_now))
                        if data != sent[i]:
                            deck.set_key_jpeg(i, data)
                            sent[i] = data
            except OSError as e:
                log.info(f"deck lost ({e}), waiting for it to come back")
                deck.close()
                deck = None
                next_open = time.monotonic() + RECONNECT_S
    except KeyboardInterrupt:
        pass
    finally:
        if deck is not None:
            try:
                if saver.enabled and saver.is_tiles:
                    deck.fill_color(0, 0, 0)
                    for i, img in enumerate(saver.render_tiles(final=True)):
                        deck.set_key_image(i, img)
                elif saver.enabled:
                    deck.set_screen_image(saver.render(final=True))
                else:
                    deck.reset()
            except OSError:
                pass
            deck.close()
        for f in (PID_FILE, STATUS_FILE):
            try:
                os.remove(f)
            except OSError:
                pass
        log.info("bye")


if __name__ == "__main__":
    main()
