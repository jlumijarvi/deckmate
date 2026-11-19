# Fixtures

Canned replies for the sources that need a network or an account, used by
`./dev.sh start --fakes` (which sets `DECKMATE_FAKES` to this folder) and by
the tests. `fakes.py` documents each file. Times are relative to the board's
start: the usage fixture's 5-hour window is 97% used and resets two minutes
in, so the cyan sweep and the reset notification can be watched; the
calendar has a meeting running now and one starting in 14 minutes, so the
calendar key turns red one minute later.

`keys.json` is a layout with every such key on it, for `./dev.sh start --fakes --config tests/fixtures/keys.json`.
