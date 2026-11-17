# Deckmate

[![CI](https://github.com/jlumijarvi/deckmate/actions/workflows/ci.yml/badge.svg)](https://github.com/jlumijarvi/deckmate/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjlumijarvi%2Fdeckmate%2Fbadges%2Fcoverage.json)](https://github.com/jlumijarvi/deckmate/actions/workflows/ci.yml)

A small Python program that drives an Elgato Stream Deck (Original V2 or MK.2,
15 keys) on Ubuntu, with no Elgato software and no libraries beyond Pillow.
It talks the deck's stock USB HID protocol through `/dev/hidraw*`. Besides
plain buttons, folders, a clock and audio keys it shows one key per running
Claude Code session with its state and your plan limits, a to-do list, a
Spotify key with the cover, a Slack unread badge, a VPN switch and a screen
saver. A settings window and a top-bar icon come with it.

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
action shipped with the package lets the user at the desktop do that without
a password. The helper takes only a release tag: it fetches that release from
GitHub itself, checks the package against the SHA-256 digest GitHub publishes
for it, and installs nothing else, so no file a user hands in ever reaches
root.

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
| `calendar` (`url`: an iCalendar feed) | time and date, and the next meeting today: its start, or "now" and its end; red when it starts within `warn_min` minutes | opens the meeting's link when it is on or about to start, otherwise the calendar in the browser; hold opens today's meetings as a folder |
| `mute` | mic or speaker, on or MUTED | toggles it (PipeWire, `wpctl`) |
| `volume` | speaker volume | changes it by `step` percent |
| `brightness` | the deck's brightness | steps it 30 / 60 / 100 (or the key's `steps` list), written to keys.json |
| `media` (`action`: playpause, next or previous) | play/pause: the playing track with its cover and a play or pause glyph; next and previous: skip arrows | play/pause toggles, or launches Spotify when it is not running; the others skip |
| `vpn` (`connection`: a NetworkManager connection name) | on, off or connecting | turns the VPN on or off (`nmcli connection up` / `down`) |
| `slack` | workspace name and a badge with the number of conversations that have unread messages: red when any are mentions or DMs (plus `@n`), blue otherwise, like Slack's own icon | opens that workspace in Slack |
| `todo` | number of open to-do items, red when something is overdue, amber when something is due | opens the list as a folder (a set of keys with a back key top left) |
| `counter` (`state`: waiting, running or done) | how many sessions are in that state | shows only that state: bright border on the counter, empty slots and the pager name the filter, and it clears itself when no session is in that state any more; hold on the done counter hides every done session |
| `pager` (`dir`: prev or next) | page number of the session slots | prev: previous page; hold shows hidden sessions. next: next page; hold starts pin mode (five seconds in which pressing a session key pins or unpins it) |
| `stats` | percent used of the session, week and model limits, one row each, like Claude Code's usage panel | opens the stats page |

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

Holding the next-page key starts *pin mode*: for five seconds every session
key wears a white frame, and pressing one pins it (a star top left) or
unpins it. A pinned session sorts first, cannot be hidden (holding its key
unpins it instead), and stays on the board after its Claude Code process has
ended, greyed and marked "gone", so a press still opens its folder in VS Code
where the session can be resumed from Claude Code's panel. Pins are kept in
`~/.config/deckmate/state.json`, with the session's title and folder.

Every key that is about Claude Code (session slots, the counters, the pagers,
the stats key and the stats tiles) wears a small orange `>_` in its
bottom-left corner, so they stand out from ordinary buttons.

### Stats page

Opened by a `stats` key. The key itself shows how much of the three plan
limits is used (the five-hour session, the week, and the weekly limit of one
model such as Fable), each as a percent and a bar that fills up, the same way
Claude Code's own usage panel shows them, with the session's reset time
bottom right. Amber from 75% used, red from 90%. The page has one tile per limit with its reset time, and a
"data" tile saying how old the numbers are and where they came from. Three
more tiles can be turned on in the key's dialog: tokens today (output large,
input and cache writes small), cache reads today with the number of replies,
and the fullest context among the sessions on the board. Tokens are summed
over every transcript on this account since local midnight, subagents
included; they measure this account's activity, not the plan.

The numbers are the ones Claude Code's own usage panel shows: Deckmate makes
the same call to Anthropic's usage API every five minutes, with the login
token Claude Code keeps in `~/.claude/.credentials.json`. The token is sent to
api.anthropic.com and nowhere else, the call reports percentages and is not a
model request, so it uses no plan quota. Without a login, or while the call
fails, the key falls back to Claude Code's own cache in `~/.claude.json` and
the desktop app's file, whichever is newer; those lag, and the cache reports
the session limit without a window even mid-session, which shows as "—".
The key shows the numbers' age once they are over ten minutes old and greys
them after two hours. When a window's reset time passes the
deck pulses cyan and the limit reads "new" (0% used) until fresh numbers arrive.

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

## Calendar

The `calendar` key reads an iCalendar feed. For Google Calendar: calendar
settings, "Integrate calendar", then the *secret address in iCal format*,
which is shown as dots until you click the eye icon next to it; copy it and
paste it into the key's dialog. It ends in `/private-…/basic.ics`. The two
public addresses above it do not work unless the calendar is public (the key
then says "offline", and the log shows 401 or 404). It works with any `.ics` address (Outlook's
published calendar, Nextcloud, a Fastmail calendar). The address is the
only thing sent anywhere, and only to that server; the feed is fetched every
five minutes and kept in `~/.cache/deckmate/`, so the key works right after
a start and offline. Treat the address as a password: anyone who has it can
read the calendar.

The key shows the clock and date on top and, below, the next meeting today:
its start time and title, or "now → 11:30" while one is going on. From
`warn_min` minutes before the start (default 5) the key turns red; during
the meeting it is green. All-day events are not meetings and stay off the
key. Pressing the key opens the meeting's video link (Google Meet, Zoom,
Teams, or the event's URL) when the meeting is on or about to start; at
other times it opens Google Calendar in the browser (`"open"` on the key
replaces that address). Holding the key opens today's events as a folder,
all-day ones first: each key shows the time and title with a band in the
colour of its state, and pressing one opens its link.

Repeating events are expanded from the feed's rules (daily, weekly, monthly,
yearly, with intervals, counts, end dates, weekday lists and exceptions), and
an instance that was moved or edited replaces the regular one, so the key
shows what Google shows. Time zones follow the feed; the key shows local time.

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
The badge counts conversations with unread messages and follows the colours
of Slack's own icon: red when any of them are mentions or DMs (`@n` says how
many), blue when only channels are unread, grey when nothing is. Nothing is
written to Slack's files and no Slack login is used.

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
| stats page, stats key | Anthropic's usage API (`/api/oauth/usage`, the call Claude Code's panel makes) with the login token from `~/.claude/.credentials.json`; fallbacks: `cachedUsageUtilization` in `~/.claude.json` and `~/.config/Claude/plan-usage-history.json` from the desktop app, newest wins; tokens are summed from the transcripts above since local midnight | no login or the call fails (the fallbacks lag and grey out with their age), or the token file or API reply changes shape |
| calendar | the `.ics` feed at the key's `url`, fetched every five minutes and cached in `~/.cache/deckmate/`; repeating events are expanded by Deckmate itself | the address stops working (Google lets you reset it); the key says "offline" until the next successful fetch, and a rule part Deckmate does not know (BYSETPOS, BYHOUR) makes that event's instances land on the wrong days |
| slack | `root-state.json` under `~/snap/slack/`, `~/.config/Slack/` or the flatpak folder, which holds per-workspace unread totals (so a per-channel badge is not possible) | a Slack update that moves or restructures the file; the key shows "no data" |
| media | the player's MPRIS properties on the session D-Bus (PlaybackStatus, Metadata), plus one https fetch per cover | the player is not running (the key says so) or drops MPRIS; Spotify leaves the artist empty for podcasts |
| vpn | `nmcli connection show`, polled once a second; NetworkManager lets the user at the active seat switch connections without a password | a VPN set up outside NetworkManager (OpenVPN by hand, Tailscale) is not seen; the key then says "not found" |
| mute, volume | `wpctl` (PipeWire) | a system without PipeWire; the key shows "?" |
| git dot | `git status` in the session's folder, every 30 seconds in a background thread | never fatal; no dot when git is missing |

A broken source never stops the board: the scan is wrapped, the key shows what
it can, and the log says what went wrong.

## Licence

MIT, see [LICENSE](LICENSE).
