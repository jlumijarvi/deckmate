# Deckmate

A small Python program that drives an Elgato Stream Deck (Original V2 or MK.2,
15 keys) on Ubuntu, with no Elgato software and no libraries beyond Pillow.
It talks the deck's stock USB HID protocol through `/dev/hidraw*`. Besides
plain buttons, a clock and audio keys it shows one key per running Claude
Code session, a to-do list, a Slack unread badge and a screen saver.

Until 0.6 the project was called stream-deck; the package replaces the old
one and copies your settings over on first start.

For running from a git checkout, the module layout, tests and releasing, see
[docs/DEVELOPING.md](docs/DEVELOPING.md). Release notes are in
[CHANGELOG.md](CHANGELOG.md).

## Install (Ubuntu)

Download the `.deb` from the latest
[release](https://github.com/jlumijarvi/deckmate/releases) and install it:

```
sudo apt install ./deckmate_<version>_all.deb
```

Then log out and in. Every account on the machine gets the board at login, a
top-bar icon with a menu (board status, start/stop, settings, updates), and a
"Deckmate" entry in the app grid that opens the settings. Each account has
its own layout in `~/.config/deckmate/keys.json`.

The top-bar icon needs GNOME's AppIndicator extension, which Ubuntu enables by
default. The deck must be plugged in and you must be logged in at the desktop,
which is what gives your account access to the device node; no udev rule is
needed.

Command line: `deckmate settings | board | sessions | tray | --version`.
"Quit tray icon" in the icon's menu only ends the icon; the board keeps
running, and the icon comes back at the next login or with the "Show" button
on the settings window's General page.

## Updates

The top-bar icon checks GitHub every hour for a newer release and
installs it on its own (switch "Install updates automatically" in the
settings; off means it only offers the update in its menu). The install runs
the root helper `/usr/lib/deckmate/install-update` through pkexec; the polkit
action shipped with the package lets a logged-in user do that without a
password, and the helper accepts nothing but a package named deckmate. While
the repository is private, the check goes through the `gh` tool, so run
`gh auth login` once in each account.

## The board service

The package installs a systemd user service, enabled for all accounts, that
starts the board with the graphical session and stops at logout:

```
systemctl --user status deckmate
journalctl --user -u deckmate -f
```

The board also logs to `~/.local/state/deckmate/deck.log`, rotated at 1 MB,
three files kept.

Several Linux accounts: each has its own keys.json. Only the account that
currently owns the screen drives the deck; when you switch user, the other
account's board lets go within two seconds and the new one takes over.

## Settings window

The "Deckmate" app, or `deckmate settings`, opens a window with three pages.
General: start or stop the board, brightness, screen saver. Keys: a grid laid
out like the deck; click a key to set its type and the settings that belong to
that key. Deck: the device details. Changes are written to keys.json straight
away and the running board picks them up within a second.

Everything the window does can also be done by editing keys.json by hand.

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
| `brightness` | the deck's brightness | steps it 30 / 60 / 100 (or the key's `steps` list), written to keys.json |
| `slack` | workspace name and a badge with the number of conversations that have unread messages, plus `@n` for mentions and DMs | opens that workspace in Slack |
| `todo` | number of open to-do items, red when something is overdue, amber when something is due | opens the list as a sub-deck |
| `counter` (`state`: waiting, running or done) | how many sessions are in that state | shows only that state: bright border on the counter, empty slots and the pager name the filter, and it clears itself when no session is in that state any more; hold on the done counter hides every done session |
| `pager` (`dir`: prev or next) | page number of the session slots | prev: previous page; hold shows hidden sessions. next: next page |
| `stats` | percent left in the five-hour plan window | opens the stats page |

`sessions.keys` lists the keys given to Claude Code sessions, in the order
sessions fill them. Keys that need a wider label wrap and shrink the text
automatically.

## Claude Code sessions

Every running Claude Code session gets a key, sorted waiting, running, done,
stopped, failed. The colour bar on top and the background show the state:
amber for a session that waits for you (`!` for a permission prompt, `?` for a
question or plan approval), blue and slowly breathing while it works, green
when the last turn finished, grey when the process is gone, red after an API
error. The key also shows how long the session has been in that state, the
session title (scrolling when it does not fit), and a bar along the bottom
with how full the context window is. A small git dot bottom right appears
when the session's folder is a repository: hollow for a dirty worktree,
filled for clean but ahead of upstream.

Tap opens the session's folder in VS Code, which raises the window that
already has it open. Hold (about half a second) hides the session from the
board; a running session cannot be hidden. The previous-page key, held, shows
the hidden sessions, and a tap on one brings it back. A session that changes
state sweeps a band of its new colour across the deck.

When there are more sessions than slots, the pager keys turn pages. The
counters show how many sessions are in each state and filter the board to one
state when tapped.

Every key that is about Claude Code (session slots, the counters, the pagers,
the stats key and the stats tiles) wears a small orange `>_` in its
bottom-left corner, so they stand out from ordinary buttons.

### Stats page

Opened by a `stats` key. Tiles: percent left in the five-hour and seven-day plan windows as the Claude
desktop app last sampled them, tokens today (output large, input and cache
writes small), cache reads today with the number of replies, and the fullest
context among the sessions on the board. When a plan window resets the deck
pulses cyan and the tile shows "new window" with a cyan border until the next
sample.

## To-do list

The `todo` key reads a Markdown task list, by default
`~/.config/deckmate/todo.md` (created with an example on first use). One
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

## Slack

The `slack` key takes `"team": "<workspace id>"`; without it the first workspace
in Slack's data is used. The settings window lists the workspaces by name.
The badge counts conversations with unread messages; `@n` is mentions and
DMs. Nothing is written to Slack's files and no Slack login is used.

## Screen saver

After `timeout_min` minutes without a key press the deck shows either a big
clock (`"image": "clock"`, one character per key so the bezels between keys do
not cut the digits) or a picture (`"image": "~/Pictures/x.jpg"`, painted across
the whole 480x272 screen, scaled to fill and cropped in the middle; the bezels
will hide strips of it). Any key press brings the board back; that press does
nothing else. `timeout_min: 0` turns the saver off.

`sleep_min` is the deck's own timer for switching the backlight off when idle;
0 means never. A key press wakes it.

When the board stops it leaves the saver on the screen (the clock version
shows only the date, since the time would freeze). The deck keeps showing it
until it loses power, so the picture stored inside the deck only appears for
the moment between plugging in and the board starting. Nothing is ever written
to the deck's stored picture; see
[docs/DEVELOPING.md](docs/DEVELOPING.md#the-image-stored-inside-the-deck) for why.

## Where the data comes from

The live keys read files that other programs write for their own use. None of
these files is a documented interface, so a new version of that program can
change them, and then the key shows stale numbers or "no data" until Deckmate
is updated. Everything is read-only; nothing is sent anywhere.

| Key | Source | When it breaks |
| --- | --- | --- |
| sessions, counters, pagers | `~/.claude/sessions/<pid>.json` (one per running Claude Code process: title, folder, busy or idle) and the transcript `~/.claude/projects/<folder>/<session>.jsonl`, whose last entries decide waiting / done / failed | a Claude Code update that changes either file's layout; sessions then show as done or vanish |
| stats page, stats key | `~/.config/Claude/plan-usage-history.json`, written by the Claude desktop app when it is running; tokens are summed from the transcripts above since local midnight | the desktop app is closed (numbers go stale, the tile shows when they were sampled) or changes the file |
| slack | `root-state.json` under `~/snap/slack/`, `~/.config/Slack/` or the flatpak folder, which holds per-workspace unread totals (so a per-channel badge is not possible) | a Slack update that moves or restructures the file; the key shows "no data" |
| mute, volume | `wpctl` (PipeWire) | a system without PipeWire; the key shows "?" |
| git dot | `git status` in the session's folder, every 30 seconds in a background thread | never fatal; no dot when git is missing |

A broken source never stops the board: the scan is wrapped, the key shows what
it can, and the log says what went wrong.

## Licence

MIT, see [LICENSE](LICENSE).
