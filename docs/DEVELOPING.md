# Developing Deckmate

## Run from a checkout

```
sudo apt install python3-pil python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
                 gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 fonts-dejavu-core
python3 deck.py            # run the board, Ctrl-C resets the deck
python3 deck.py --demo     # same, with made-up Claude sessions in every state
python3 deck.py --preview board.png --seconds 3   # render one frame to a PNG
python3 deck.py --saver    # start in screen-saver mode
python3 settings.py        # settings window
python3 tray.py            # top-bar icon
python3 sessions.py        # list the Claude Code sessions it would show
python3 hello_deck.py      # hardware smoke test: numbered tiles, prints key presses
```

The checkout and the installed package use the same config directory,
`~/.config/deckmate`, so stop the packaged board first
(`systemctl --user stop deckmate`) or the two fight over the deck. The board
logs to stdout and to `~/.local/state/deckmate/deck.log`.

## Files

| File | What it is |
| --- | --- |
| `deckhid.py` | the driver: find the deck, set brightness, paint keys, read presses |
| `deck.py` | the main loop: connect, paint, presses and holds, screen saver, seat changes |
| `layout.py` | builds the board from keys.json: one key object per position plus the shared providers; folders call the same builder for their own map |
| `keys.py` | the `Key` base class and the plain types: blank, command, clock, mute, volume |
| `render.py` | drawing helpers: fonts, colours, label wrapping, the `>_` corner mark, preview image |
| `pages.py` | folders and pages: the navigator stack and the back / page keys |
| `folderkeys.py` | the folder key and its page |
| `sessionkeys.py` | Claude Code session slots, counters, pagers, the hidden-sessions page |
| `statskeys.py` | the stats page and the stats key |
| `slackkey.py`, `todokeys.py` | the Slack key; the to-do key, its sub-deck and item keys |
| `mediakeys.py`, `mpris.py` | Spotify (any MPRIS player) keys; the D-Bus side with the cover cache |
| `vpnkey.py` | the VPN key over nmcli |
| `saver.py` | the screen saver: picture or big clock |
| `board.py` | pure logic with no drawing: press and hold detection, dismissals, filter, paging, pulses, scrolling, breathing |
| `sessions.py` | finds live Claude Code sessions and classifies their state from the transcript |
| `stats.py`, `claudeusage.py` | the plan limits (live API, Claude Code's cache, desktop app, newest wins) and the token tally for the stats page |
| `gitinfo.py` | git state of session folders, in a background thread |
| `slackstate.py` | reads Slack's local state file for the unread badge |
| `todo.py` | parses and watches the Markdown to-do list |
| `system.py` | shell helpers and which account owns the screen (logind) |
| `paths.py` | where config, state, pid and log files live; migration from the old name |
| `settings.py` | settings window (GTK 4 + libadwaita) |
| `tray.py` | top-bar icon (GTK 3 + AppIndicator) with status, start/stop, settings and updates |
| `keys.example.json` | the starting layout, used until keys.json exists |
| `tests/` | unit tests, `python3 -m unittest discover -s tests` |
| `build_deb.sh`, `packaging/`, `data/` | the Debian package, desktop files, icons, polkit action, systemd unit |
| `release.sh`, `changelog.sh`, `CHANGELOG.md` | releasing |
| `.github/workflows/ci.yml` | lint, tests, package build and release publishing on GitHub |

The GTK 3 tray and the GTK 4 settings window cannot share a process, which is
why they are separate programs and why CI imports them separately.

Keys are plain classes with `render(pressed)`, `press()` and, when `holdable`,
`hold()` and `render_held()`; `dynamic` keys are re-rendered every refresh
tick. A page (sub-deck) is anything with a `keys()` method returning 15 keys;
`pages.nav` is the one navigator, keys push pages onto it and set `nav.dirty`
when the deck needs a full repaint. Providers (session board, stats, to-do
lists) are refreshed once per tick before the keys render.

Settings-window rule: the General page holds only board-wide settings;
anything about one key lives in that key's dialog.

Wording rule: the code and state.json say "dismissed"; everything the user
sees (deck, settings hints, log, README) says "hidden".

Terminology: use Elgato's words where they have one, so a Stream Deck user
finds what they expect. Key (a button), title (the text on a key), press and
hold, page with Previous Page / Next Page keys, folder (a key that opens
another set of keys, back key top left), profile (a layout for one device,
reserved for when layouts become switchable), screen saver, sleep after,
brightness, device. Deckmate's own words, with no Elgato equivalent: board
(the running program and its main keys), session slot, counter, pager (our
name for a Previous / Next Page pair), hidden session, stats. In keys.json the
text on a key was `label` until 0.7; `paths.read_layout` migrates it to
`title` on first read.

## Tests and lint

```
python3 -m unittest discover -s tests
ruff check .
```

The tests cover press and hold detection, dismissal and return, filters,
paging, the state store, pulse detection and timing, scrolling and breathing,
the session state classifier, the token tally and plan usage, git probing, the
to-do parser, the Slack parser, and the layout loader (every key type and page
renders). They need the DejaVu fonts but no deck. `build_deb.sh` runs them and
refuses to build on a failure. Ruff's configuration is in `pyproject.toml`;
CI runs it with `pipx run ruff`.

## Packaging and CI

`./build_deb.sh` builds `dist/deckmate_<version>_all.deb`: every `*.py` except
`hello_deck.py` goes to `/usr/lib/deckmate`, plus the launcher
`/usr/bin/deckmate`, the desktop files, the systemd user unit (enabled for
all accounts), the tray autostart entry, the icons, the polkit action and the
update helper. The staged modules must import before the package is built.

The GitHub Actions workflow in `.github/workflows/ci.yml` runs ruff, the unit
tests under coverage, the import check, builds the `.deb` and installs it on
the runner on every push. On main it also writes the coverage percentage as a
shields.io endpoint file to the `badges` branch, which the README's coverage
badge reads; `pipx run coverage run -m unittest discover -s tests` followed by
`pipx run coverage report` gives the same number locally. On a `v*` tag it also checks that the tag matches `VERSION` and
that `CHANGELOG.md` has a section for it, then publishes the GitHub release
with the package attached and the changelog section as the notes. That release
is what the tray icons on installed machines look for.

## Releasing

1. Describe the changes under `## Unreleased` in `CHANGELOG.md` as you go.
2. Commit everything, then `./release.sh 0.7.0`.

The script runs the build (and so the tests), turns the Unreleased section
into `## 0.7.0 (date)`, bumps `VERSION`, commits, tags `v0.7.0`, pushes, and
waits for the CI run that publishes the release. `./changelog.sh 0.7.0` prints
the notes it will use.

## Hardware notes

The driver speaks the stock Stream Deck HID protocol: 72x72 JPEG key images
in 1024-byte reports, a 480x272 full-screen JPEG, brightness and sleep timer
feature reports, and a key state report. Only the Original V2 (0fd9:006d) and
the MK.2 (0fd9:0080) are known to work; the deck is found through
`/dev/hidraw*` and the logind seat ACL gives the logged-in account access.

### The image stored inside the deck

The deck shows a stored picture (the boot logo) whenever no program is driving
it. Nothing can read it back, and writing it needs Elgato's "Update Boot Logo"
command, which is not published for this model, so the board leaves it alone.
The four "background" slots the deck reports are a separate, unused image
memory on this model: "Show Background by Index" changes nothing on it. The
board works around this by leaving the screen saver on the screen when it
quits, which the deck keeps showing until it loses power.
