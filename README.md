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
General: start or stop the board, brightness, screen saver, sleep. Keys: a grid
laid out like the deck; click a key to set its type and the settings that belong
to that key. Device: the connected Stream Deck and the installed version. Changes are written to keys.json straight
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
    "1": {"title": "Terminal", "color": "#2d333b", "run": "ptyxis --new-window"},
    "5": {"type": "mute", "target": "source", "title": "Mic"},
    "6": {"type": "mute", "target": "sink", "title": "Speaker"},
    "7": {"type": "volume", "step": -5},
    "8": {"type": "volume", "step": 5}
  },
  "sessions": {"keys": [10, 11, 12, 13, 14]},
  "screensaver": {"image": "clock", "timeout_min": 5, "sleep_min": 0}
}
```

| Type | Shows | Press |
| --- | --- | --- |
| (none) | its title | runs `run` in a shell, detached |
| `folder` | its title, with a small folder tab | opens the folder: its own `keys` map, key 0 is the back key (press: one level up, hold: the main board) |
| `clock` | time and date | nothing |
| `mute` | mic or speaker, on or MUTED | toggles it (PipeWire, `wpctl`) |
| `volume` | speaker volume | changes it by `step` percent |
| `brightness` | the deck's brightness | steps it 30 / 60 / 100 (or the key's `steps` list), written to keys.json |
| `media` (`action`: playpause, next or previous) | play/pause: the playing track with its cover and a play or pause glyph; next and previous: skip arrows | play/pause toggles, or launches Spotify when it is not running; the others skip |
| `vpn` (`connection`: a NetworkManager connection name) | on, off or connecting | turns the VPN on or off (`nmcli connection up` / `down`) |
| `slack` | workspace name and a badge with the number of conversations that have unread messages, plus `@n` for mentions and DMs | opens that workspace in Slack |
| `todo` | number of open to-do items, red when something is overdue, amber when something is due | opens the list as a folder (a set of keys with a back key top left) |
| `counter` (`state`: waiting, running or done) | how many sessions are in that state | shows only that state: bright border on the counter, empty slots and the pager name the filter, and it clears itself when no session is in that state any more; hold on the done counter hides every done session |
| `pager` (`dir`: prev or next) | page number of the session slots | prev: previous page; hold shows hidden sessions. next: next page |
| `stats` | percent left of the session, week and model limits, one row each | opens the stats page |

`sessions.keys` lists the keys given to Claude Code sessions, in the order
sessions fill them. Titles that do not fit wrap and shrink
automatically.

## Folders

A `folder` key opens a set of keys of its own, like Elgato's folders: key 0
inside goes back (hold it to return to the main board from any depth), the
other fourteen are any key types except session slots, folders included.
In keys.json a folder carries its own `keys` map:

```json
"3": {"type": "folder", "title": "Work", "color": "#2d5a7a",
      "keys": {"1": {"title": "Jira", "run": "xdg-open https://jira.example.com"},
               "2": {"type": "vpn", "connection": "Office"}}}
```

In the settings window, save a key as a folder, then open it: the key grid
shows the folder's keys with a "Back to parent" button above it.

## Claude Code sessions

Every running Claude Code session gets a key, sorted waiting, running, done,
stopped, failed. The colour bar on top and the background show the state:
amber for a session that waits for you (`!` for a permission prompt, `?` for a
question or plan approval), blue and slowly breathing while it works, green
when the last turn finished, grey when the process is gone, red after an API
error. The key also shows how long the session has been in that state, the
session title on one line (scrolling when it does not fit), a second line
with what the session is doing or asking (the tool and its description, the
question's header, "approve plan", or "3 agents" while subagents run), and a
bar along the bottom with how full the context window is. A small git dot bottom right appears
when the session's folder is a repository: hollow for a dirty worktree,
filled for clean but ahead of upstream.

Press opens the session's folder in VS Code, which raises the window that
already has it open. Hold (about half a second) hides the session from the
board; a running session cannot be hidden. The previous-page key, held, shows
the hidden sessions, and a press on one brings it back. A session that changes
state sweeps a band of its new colour across the deck.

When there are more sessions than slots, the pager keys turn pages. The
counters show how many sessions are in each state and filter the board to one
state when pressed.

Every key that is about Claude Code (session slots, the counters, the pagers,
the stats key and the stats tiles) wears a small orange `>_` in its
bottom-left corner, so they stand out from ordinary buttons.

### Stats page

Opened by a `stats` key. The key itself shows what is left of the three plan
limits (the five-hour session, the week, and the weekly limit of one model
such as Fable), each as a percent and a bar, with the session's reset time
bottom right. The page has one tile per limit with its reset time, and a
"data" tile saying how old the numbers are and where they came from. Three
more tiles can be turned on in the key's dialog: tokens today (output large,
input and cache writes small), cache reads today with the number of replies,
and the fullest context among the sessions on the board. Tokens are summed
over every transcript on this account since local midnight, subagents
included; they measure this account's activity, not the plan.

The limits come from Claude Code's own cache, refreshed while Claude Code
runs, at its own irregular pace; the desktop app's file is used when it is
newer. The key shows the numbers' age once they are over ten minutes old and
greys them after two hours. A session limit the source reports without a
window (nothing used, no reset time) shows as "—", since Claude Code's cache
says that even mid-session. When a window's reset time passes the
deck pulses cyan and the limit reads "new" until fresh numbers arrive.

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

`@date` is the due date. A Markdown link is opened when the item's key is
pressed: a web link (`[docs](https://example.com/page)`) with the default
browser, a file path (absolute, `~`, or relative to the list file) with
`open_with`; a bare path ending in .md works as a link too. The default list
is created with an example on first use; a list at another path is left for
you (or another program) to create and shows "no file" until then. The file is watched, so saving it in an
editor updates the deck within a second. Holding an item's key for about half
a second ticks its box in the file (`- [ ]` becomes `- [x]`), which is the only
write the deck ever makes to it; the switch "Hold an item's key to mark it
done" in the settings turns that off.

Pressing the key opens the list as a folder: key 0 goes back, the other keys
are the items, overdue first, then by due date, then undated; with more than
14 items keys 13 and 14 turn the pages. The settings live on the key itself
(click it in the settings window): `file`, `title`, `open_with` (default
`code`), `soon_days` (0 means only items due today count as "due soon"),
`hold_done` (default true). Two to-do keys with different files give two lists.

## Spotify

The `media` keys talk to the Spotify app over MPRIS, the media-player
interface on the session D-Bus that GNOME's own media controls use. Nothing
is sent to Spotify's servers except the one fetch of the cover picture, which
is cached per track. No Spotify login or API key is involved. `"player"` names
another MPRIS player instead, for example `"chromium"`, since the interface is
the same for all of them; the settings window calls the keys Spotify because
that is the player they were made for.

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

`sleep_min` ("Sleep after" in the settings) is the deck's own timer for
switching the backlight off when idle; 0 means never. A key press wakes it.

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
| stats page, stats key | `cachedUsageUtilization` in `~/.claude.json`, Claude Code's own cache of the plan limits (session, week, per-model week, reset times), and `~/.config/Claude/plan-usage-history.json` from the desktop app when that is newer; tokens are summed from the transcripts above since local midnight | neither program has run lately (numbers grey out with their age) or changes its file |
| slack | `root-state.json` under `~/snap/slack/`, `~/.config/Slack/` or the flatpak folder, which holds per-workspace unread totals (so a per-channel badge is not possible) | a Slack update that moves or restructures the file; the key shows "no data" |
| media | the player's MPRIS properties on the session D-Bus (PlaybackStatus, Metadata), plus one https fetch per cover | the player is not running (the key says so) or drops MPRIS; Spotify leaves the artist empty for podcasts |
| vpn | `nmcli connection show`, polled once a second; NetworkManager lets the user at the active seat switch connections without a password | a VPN set up outside NetworkManager (OpenVPN by hand, Tailscale) is not seen; the key then says "not found" |
| mute, volume | `wpctl` (PipeWire) | a system without PipeWire; the key shows "?" |
| git dot | `git status` in the session's folder, every 30 seconds in a background thread | never fatal; no dot when git is missing |

A broken source never stops the board: the scan is wrapped, the key shows what
it can, and the log says what went wrong.

## Licence

MIT, see [LICENSE](LICENSE).
