# Stream Deck board for Linux

A small Python program that drives an Elgato Stream Deck (Original V2 or MK.2,
15 keys) on Ubuntu, with no Elgato software and no libraries beyond Pillow.
It talks the deck's stock USB HID protocol through `/dev/hidraw*`.

## Files

| File | What it is |
| --- | --- |
| `deckhid.py` | the driver: find the deck, set brightness, paint keys, read presses |
| `deck.py` | the board: reads `keys.json`, paints keys, runs actions |
| `keys.json` | your layout, reloaded automatically when saved |
| `sessions.py` | finds live Claude Code sessions and their state |
| `settings.py` | settings window (GTK): edit keys.json, start/stop the board, replace the stored image |
| `hello_deck.py` | the first test: numbered colour tiles, prints key presses |

## Run

```
python3 settings.py        # settings window: keys, screen saver, start/stop the board
python3 deck.py            # run the board, Ctrl-C resets the deck
python3 deck.py --demo     # same, with made-up Claude sessions in every state
python3 sessions.py        # list the Claude Code sessions it would show
python3 deck.py --preview board.png --seconds 3   # render one frame to a PNG
python3 deck.py --saver    # start in screen-saver mode
```

The deck must be plugged in and you must be logged in at the desktop, which is
what gives your user access to the device node.

## keys.json

Keys are numbered 0 to 14, left to right, top to bottom. Each entry is either
a plain button with a shell command, or a live key:

```json
{
  "brightness": 60,
  "keys": {
    "0": {"type": "clock"},
    "1": {"label": "Terminal", "color": "#2d333b", "run": "ptyxis --new-window"},
    "5": {"type": "mute", "target": "source", "label": "Mic"},
    "6": {"type": "mute", "target": "sink", "label": "Speaker"},
    "7": {"type": "volume", "step": -5},
    "8": {"type": "volume", "step": 5}
  },
  "sessions": {"keys": [10, 11, 12, 13, 14]},
  "screensaver": {"image": "clock", "timeout_min": 5, "sleep_min": 0}
}
```

| Type | Shows | Tap |
| --- | --- | --- |
| (none) | the label | runs `run` in a shell, detached |
| `clock` | time and date | nothing |
| `mute` | mic or speaker, on or MUTED | toggles it (PipeWire, `wpctl`) |
| `volume` | speaker volume | changes it by `step` percent |

`sessions.keys` lists the keys given to Claude Code sessions, in the order
sessions fill them.

## Screen saver

After `timeout_min` minutes without a key press the deck shows either a big
clock (`"image": "clock"`, one character per key so the bezels between keys do
not cut the digits) or a picture (`"image": "~/Pictures/x.jpg"`, painted across
the whole 480x272 screen, scaled to fill and cropped in the middle; the bezels
will hide strips of it). Any key press brings the
board back; that press does nothing else. `timeout_min: 0` turns the saver off.

`sleep_min` is the deck's own timer for switching the backlight off when idle;
0 means never. A key press wakes it.

When deck.py quits it leaves the saver on the screen (the clock version shows
only the date, since the time would freeze). The deck keeps showing it until it
loses power, so the image stored inside the deck only appears for the moment
between plugging in and starting deck.py. Nothing is ever written to the deck's
stored image.

`python3 deck.py --saver` starts straight in saver mode, for checking the look.

## Settings window

`python3 settings.py` opens a GTK window with three pages. General: start or
stop the board, brightness, screen saver. Keys: a grid laid out like the deck,
click a key to set its type, label, colour and command. Deck: the device
details and the stored-image action below. Changes are written to keys.json
straight away and the running board picks them up.

## The image stored inside the deck

The deck shows a stored picture whenever no program is driving it. Replacing it
is possible with a documented command, but Elgato does not say which of the
stored slots is the one shown, and nothing can read a slot back. The Deck page
of the settings window therefore stops the board, writes your picture to one
slot at a time, shows the stored image and asks whether the deck now shows your
picture, then remembers the slot in keys.json for later changes. The previous
picture is gone once its slot is written.

## Claude Code session keys

One key per running Claude Code session, sorted so the ones that need you come
first. The strip along the top and the tint give the state:

| Colour | State | Meaning |
| --- | --- | --- |
| amber `!` | waiting | a permission prompt |
| amber `?` | waiting | a question or a plan to approve |
| blue | running | Claude is working |
| green | done | the turn ended, the result is waiting to be read |
| grey | stopped | the process is gone |
| red | failed | the last reply was an API error |

Top right is how long the session has been in that state. The thin bar along
the bottom is how full the context window is (grey, amber over 60 percent, red
over 85). Tap opens the session's folder with `code`, which raises the VS Code
window that already has it.

Everything comes from files Claude Code writes locally, read only:
`~/.claude/sessions/<pid>.json` for the live processes, and the transcript in
`~/.claude/projects/` for the state, title and token usage. Nothing calls an
API or reads credentials.
