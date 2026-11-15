# Stream Deck board for Linux

A small Python program that drives an Elgato Stream Deck (Original V2 or MK.2,
15 keys) on Ubuntu, with no Elgato software and no libraries beyond Pillow.
It talks the deck's stock USB HID protocol through `/dev/hidraw*`.

## Install (Ubuntu)

Download the `.deb` from the latest
[release](https://github.com/jlumijarvi/stream-deck/releases) and install it:

```
sudo apt install ./stream-deck_<version>_all.deb
```

Then log out and in. Every account on the machine gets the board at login, a
top-bar icon with a menu (board status, start/stop, settings, updates), and a
"Stream Deck" entry in the app grid that opens the settings. Each account has
its own layout in `~/.config/stream-deck/keys.json`.

The top-bar icon needs GNOME's AppIndicator extension, which Ubuntu enables by
default. The tray icon checks GitHub every six hours for a newer release and
installs it on its own (switch "Install updates automatically" in the settings;
off means it only offers the update in its menu). The install runs the root
helper `/usr/lib/stream-deck/install-update` through pkexec; the polkit action
shipped with the package lets a logged-in user do that without a password, and
the helper accepts nothing but a package named stream-deck. While the
repository is private, the check goes through the `gh` tool, so run
`gh auth login` once in each account.

Command line: `stream-deck settings | board | sessions | tray | --version`.

## Releasing a new version

Develop in a git checkout, commit, then `./release.sh 0.2.0`. That bumps
VERSION, tags and pushes. The GitHub Actions workflow in
`.github/workflows/ci.yml` runs the unit tests on every push, builds the
`.deb`, installs it on the runner, and on a `v*` tag publishes the GitHub
release with the package attached, which is what the tray icons look for.
The script waits for that run and prints the release link.

## Files

| File | What it is |
| --- | --- |
| `deckhid.py` | the driver: find the deck, set brightness, paint keys, read presses |
| `deck.py` | the board: reads `keys.json`, paints keys, runs actions |
| `~/.config/stream-deck/keys.json` | your layout, reloaded automatically when saved |
| `keys.example.json` | the starting layout, used until keys.json exists |
| `sessions.py` | finds live Claude Code sessions and their state |
| `slackstate.py` | reads Slack's local state file for the unread badge |
| `todo.py` | parses and watches the Markdown to-do list |
| `settings.py` | settings window (GTK): edit keys.json, start/stop the board, replace the stored image |
| `tray.py` | top-bar icon (GTK 3 + AppIndicator) with status, start/stop, settings and updates |
| `paths.py` | where config, pid and status files live |
| `install_service.sh` | git-checkout alternative to the package: a per-user login service |
| `build_deb.sh`, `release.sh`, `packaging/`, `data/` | the Debian package, desktop files, icons |
| `.github/workflows/ci.yml` | tests, package build and release publishing on GitHub |
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

## The board service

The package installs a systemd user service, enabled for all accounts, that
starts the board with the graphical session and stops at logout:

```
systemctl --user status stream-deck
journalctl --user -u stream-deck -f
```

Running from a git checkout instead: `./install_service.sh` installs the same
kind of service for the current account only (`--remove` undoes it). Do not
use both.

Several Linux accounts: each has its own keys.json. Only the account that
currently owns the screen drives the deck; when you switch user, the other
account's board lets go within two seconds and the new one takes over.

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
| `slack` | workspace name and a badge with the number of conversations that have unread messages, plus `@n` for mentions and DMs | opens that workspace in Slack |
| `todo` | number of open to-do items, red when something is overdue, amber when something is due | opens the list as a sub-deck |
| `counter` (`state`: waiting, running or done) | how many sessions are in that state | filters the board to that state, bright border while on; hold on the done counter hides every done session |
| `pager` (`dir`: prev or next) | page number of the session slots | prev: previous page, or the stats page from the first; hold shows hidden sessions. next: next page; hold cycles brightness 30 / 60 / 100 |
| `stats` | percent left in the five-hour plan window | opens the stats page |

`sessions.keys` lists the keys given to Claude Code sessions, in the order
sessions fill them.

The `slack` key takes `"team": "<workspace id>"`; without it the first workspace
in Slack's data is used. The settings window lists the workspaces by name. The
numbers come from Slack's own state file (`root-state.json` under
`~/snap/slack/`, `~/.config/Slack/` or the flatpak folder), which holds only
per-workspace totals, so a per-channel badge is not possible from it. Nothing
is written to Slack's files and no Slack login is used.

## To-do list

The `todo` key reads a Markdown task list, by default
`~/.config/stream-deck/todo.md` (created with an example on first use). One
item per line:

```markdown
- [ ] Renew passport  @2026-11-20  [notes](~/Documents/notes/passport.md)
- [ ] Call the garage  @2026-11-16
- [ ] Sort out the backup disk
- [x] Done items are hidden
```

`@date` is the due date, a Markdown link (or a bare path ending in .md) is
opened when the item's key is tapped. The file is watched, so saving it in an
editor updates the deck within a second. Holding an item's key for about half
a second ticks its box in the file (`- [ ]` becomes `- [x]`), which is the only
write the deck ever makes to it; the switch "Hold an item's key to mark it
done" in the settings turns that off.

Tapping the key opens the list as a sub-deck: key 0 goes back, the other keys
are the items, overdue first, then by due date, then undated; with more than
14 items keys 13 and 14 turn the pages. The settings live on the key itself
(click it in the settings window): `file`, `label`, `open_with` (default
`code`), `soon_days` (0 means only items due today count as "due soon"),
`hold_done` (default true). Two to-do keys with different files give two lists.

## Stats page

Reached from the previous-page key on the first page, or from a `stats` key.
Tiles: percent left in the five-hour and seven-day plan windows as the Claude
desktop app last sampled them (`~/.config/Claude/plan-usage-history.json`; the
app must have been running recently for the numbers to be fresh), tokens today
(output large, input and cache writes small), cache reads today with the number
of replies, and the fullest context among the sessions on the board. Tokens
are summed over every transcript since local midnight, counted once per
message id, with per-file read offsets so refreshes are incremental. When a
plan window resets (the used percent drops by more than 20 between samples)
the deck pulses cyan and the tile shows "new window" with a cyan border until
the next sample.

Session keys also carry a small git dot bottom right when the session's folder
is a repository: hollow for a dirty worktree, filled for clean but ahead of
upstream, none otherwise. Git runs in a background thread every 30 seconds.

Every key that is about Claude Code (session slots, the counters, the pagers,
the stats key and the stats tiles) wears a small terracotta sunburst in its
bottom-left corner, so they stand out from ordinary buttons.

## Log

The board logs to the journal (`journalctl --user -u stream-deck`) and to
`~/.local/state/stream-deck/deck.log`, rotated at 1 MB, three files kept.

## Tests

```
python3 -m unittest discover -s tests
```

Covers press and hold detection, dismissal and return, filters, paging, the
state store, pulse detection and timing, scrolling and breathing, the session
state classifier, the token tally and plan usage, git probing, the to-do parser
and the Slack parser. `build_deb.sh` runs them and refuses to build on a failure.

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

The deck shows a stored picture (the boot logo) whenever no program is driving
it. Nothing can read it back, and writing it needs Elgato's "Update Boot Logo"
command, which is not published for this model, so the board leaves it alone.
The four "background" slots the deck reports are a separate, unused image
memory on this model: "Show Background by Index" changes nothing on it.


## Licence

MIT, see [LICENSE](LICENSE).
