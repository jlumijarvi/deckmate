# Changelog

Each release has a section here; the CI workflow uses the section for the
release notes on GitHub. Dates are the release dates.

## Unreleased

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
