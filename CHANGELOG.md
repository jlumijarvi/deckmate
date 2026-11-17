# Changelog

Each release has a section here; the CI workflow uses the section for the
release notes on GitHub. Dates are the release dates.

## 0.9.6 (2026-11-17)

- Tray icon: 0.9.5 crashed at login (a method lost its name in a rename);
  it starts again. A new test checks that every attribute a class reads
  exists, so a reference left behind by a rename fails the build.
- The login screen no longer starts a board of its own: with nobody logged in
  the deck keeps the screen saver the last board left on it instead of the
  example keys.

## 0.9.5 (2026-11-16)

- Update helper: takes a release tag, fetches the release from GitHub itself
  and verifies the package against GitHub's SHA-256 digest before installing;
  the tray no longer downloads or hands over a file (#14).
- Settings: re-saving a key keeps the fields its dialog does not show
  (brightness steps, a volume step, the media player); a to-do key saved with
  the default list file gets the sample list as documented.
- Session key hold on a running session says "running, not hidden".
- CI measures test coverage and the README shows it as a badge, next to the
  build status.
- README: the update check no longer needs `gh`; the repository is public.

## 0.9.4 (2026-11-16)

- When another account takes the screen, the board leaves the screen saver on
  the deck before letting go, so the login screen no longer shows the last
  board's keys.
- Stats: no false "new window" pulse when two sources report the same reset
  time a second apart.

## 0.9.3 (2026-11-16)

- Stats: the limits come live from Anthropic's usage API, the call Claude
  Code's own panel makes, every five minutes with Claude Code's login token;
  the file caches are fallbacks. The session limit is right again.
- Stats: the limits show percent used, as Claude Code's usage panel does, not
  percent left; amber from 75%, red from 90%.

## 0.9.2 (2026-11-16)

- Stats: a session limit reported without a window shows "—" instead of
  100% left; the key shows the data's age after ten minutes and greys it after
  two hours (the cache refreshes at an irregular pace).
- The back key in folders and pages is an arrow, without text.

## 0.9.1 (2026-11-16)

- Slack key: the workspace name is a small caption under the badge instead of
  a title-sized label.

## 0.9.0 (2026-11-16)

- New `folder` key type: a key that opens a set of keys of its own, key 0
  inside goes back and a hold on it returns to the main board. The settings
  window edits a folder's keys in the same grid (#4).
- Session keys: the title is one line and a second line says what the session
  is doing or asking: the tool and its description, the question's header,
  "approve plan", or how many subagents are running (#12).
- New `vpn` key type: a NetworkManager VPN connection, on or off at a glance,
  press switches it (#10).
- Stats: the limits come from Claude Code's own cache (`~/.claude.json`),
  with the desktop app's file as the fallback when it is newer; the key shows
  session, week and the model-scoped weekly limit as three bars, the page one
  tile per limit with its reset time plus a data-age tile; a window reset is
  taken from its reset time, not guessed from a drop (no more cyan pulse on
  every start); stale data is greyed with its age. The tokens, cache reads and
  fullest-context tiles are switches in the stats key's dialog, off by
  default (#9).
- Context bars: every current model has a 1M window; only Haiku 4.5 and the
  4.5-and-older generation are taken as 200k.
- Tokens today also counts subagent transcripts.
- Slack key: the @mentions line no longer overlaps the workspace name (#7).
- Settings: launching the settings again raises the open window instead of
  opening another (#8).
- To-do: web links open in the browser, a URL may contain parentheses, and the
  sample list is only created for the default file (#13).

## 0.8.0 (2026-11-15)

- New `media` key type for Spotify (or any MPRIS player): play/pause with the
  track and its cover, next track, previous track. Over the session D-Bus, no
  login.
- Elgato's words where they have one: the text on a key is its "title" (the
  keys.json field `label` is migrated to `title` on first read), a to-do list
  opens as a "folder", keys are "pressed" rather than tapped, the backlight
  timer is "Sleep after", and the settings window's Deck tab is "Device".

## 0.7.3 (2026-11-15)

- Settings: the tray icon's Show button starts it as a transient user service,
  so it gets a clean session environment whatever started the window.

## 0.7.2 (2026-11-15)

- Settings: a Version row on the Deck page, and a "Tray icon" row on the
  General page with a Show button that brings the icon back after "Quit tray
  icon".
- The tray checks for updates every hour instead of every six.

## 0.7.1 (2026-11-15)

- New `brightness` key type: shows the level, tap steps it 30 / 60 / 100 (or
  the key's own `steps`). The next pager's hold no longer cycles brightness.
- A counter filter now shows on the board: empty slots say "waiting only" (or
  running, done), the pager label names the state, a counter at zero cannot
  filter, and the filter clears itself when its state empties.
- One word for a session taken off the board: "hidden" on the deck, in the
  settings hints and in the log (the hints and log said "dismissed").
- The prev pager key only turns pages; the stats page is opened by the `stats`
  key alone. (Before, a tap on the first page opened the stats page.)
- CI: the release job checks out the repository before downloading the built
  package, so the package is attached (0.7.0's was attached by hand).

## 0.7.0 (2026-11-15)

- The board is split into modules: `render.py` (drawing helpers), `keys.py`
  (plain key types), `pages.py` (sub-decks), `sessionkeys.py`, `statskeys.py`,
  `slackkey.py`, `todokeys.py`, `saver.py` and `layout.py`; `deck.py` keeps the
  main loop. No behaviour change.
- The corner mark on Claude Code keys is now a small terminal prompt (`>_`)
  instead of a sunburst.
- Fixed: with a to-do key on the board, holding the next-page key tried to
  write the brightness into the to-do file instead of keys.json.
- Removed `install_service.sh`; the package is the only supported install.
- Settings: the board status row in General is labelled "Status", not "deck.py".
- Development: ruff runs in CI (configuration in `pyproject.toml`), the
  layout loader has tests that render every key type, the README is split
  into a user guide and `docs/DEVELOPING.md`, and this changelog provides the
  release notes.

## 0.6.0 (2026-11-15)

- The project is now called Deckmate. The package replaces `stream-deck` and
  copies the old settings from `~/.config/stream-deck` on first start.
- MIT licence.

## 0.5.7 (2026-11-15)

- Tray: the settings window opens in its own systemd scope, so GNOME does not
  file it under the tray's process.

## 0.5.6 (2026-11-15)

- Automatic updates: the tray installs new releases on its own through a root
  helper with a polkit action (switch "Install updates automatically").

## 0.5.5 (2026-11-15)

- Settings window has an application id and a matching desktop file, so it
  gets its own icon in the dock.

## 0.5.4 (2026-11-15)

- Settings: shorter key type names in the dropdown, with a hint line under the
  type row.

## 0.5.3 (2026-11-15)

- Every key about Claude Code wears a small mark in its bottom-left corner.

## 0.5.2 (2026-11-15)

- GitHub Actions CI: tests, package build and install on every push, release
  publishing on `v*` tags.

## 0.5.1 (2026-11-14)

- Token tally reads transcripts in 1 MB chunks (a 187 MB transcript used
  200 MB of memory).

## 0.5.0 (2026-11-14)

- Stats page: plan windows, tokens today, cache reads, fullest context, with a
  cyan pulse when a plan window resets. Stats key. Git dot on session keys.

## 0.4.0 (2026-11-14)

- Pulses on session state changes, breathing running keys, scrolling long
  titles, fast frames while something animates.

## 0.3.0 (2026-11-14)

- Session dismissal (hold a session key), counter and pager keys, brightness
  cycle, rotating log file, unit tests.

## 0.2.4 (2026-11-14)

- Settings: the stored-image action is gone; the boot logo needs an
  unpublished command and the background slots are unused on this model.

## 0.2.3 (2026-11-14)

- Settings: to-do options live on the key (per-key list file); the General
  page keeps only board-wide settings.

## 0.2.2 (2026-11-14)

- To-do: hold an item's key to mark it done (configurable). Hold support for keys.

## 0.2.1 (2026-11-14)

- Package every module and check imports when building (0.2.0 shipped without
  todo.py).

## 0.2.0 (2026-11-14)

- To-do list key and sub-deck from a watched Markdown task list; pages for the board.

## 0.1.2 (2026-11-14)

- README: the update check needs `gh` while the repository is private.

## 0.1.1 (2026-11-14)

- Tray: update check and download through `gh` for a private repository.

## 0.1.0 (2026-11-14)

- First packaged release: hidraw driver, button board, Claude Code session
  keys, screen saver, settings window, Slack unread badge, Debian package,
  top-bar tray icon with update check, login service that follows the active
  account.
