#!/bin/sh
# Run the checkout on the real deck in place of the installed package, and
# put the package back afterwards. Everything starts as a transient systemd
# user unit, so it gets the session's clean environment (a VS Code or snap
# shell would break GTK) and shows up in journalctl.
#
#   ./dev.sh start            the checkout's board instead of the installed one
#   ./dev.sh start --fakes    ... with made-up sessions and canned Slack, usage and calendar
#   ./dev.sh start --demo     ... with made-up sessions only
#   ./dev.sh stop             back to the installed board (and tray)
#   ./dev.sh settings         the checkout's settings window
#   ./dev.sh tray             the checkout's tray icon instead of the installed one
#   ./dev.sh log              follow the dev board's log
#   ./dev.sh status
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
UNIT=deckmate-dev
SERVICE=deckmate.service
FIXTURES="$HERE/tests/fixtures"
RUNTIME="${XDG_RUNTIME_DIR:-/tmp}/deckmate"

tray_pid() { cat "$RUNTIME/tray.pid" 2>/dev/null; }
tray_alive() { p=$(tray_pid); [ -n "$p" ] && [ -d "/proc/$p" ]; }

case "${1:-}" in
    start)
        shift
        ARGS=""
        ENV=""
        for a in "$@"; do
            case "$a" in
                --fakes) ARGS="$ARGS --demo"; ENV="--setenv=DECKMATE_FAKES=$FIXTURES" ;;
                *) ARGS="$ARGS $a" ;;
            esac
        done
        systemctl --user stop "$SERVICE" 2>/dev/null || true
        systemctl --user stop "$UNIT" 2>/dev/null || true
        # shellcheck disable=SC2086
        systemd-run --user --quiet --collect --unit="$UNIT" $ENV \
            -p WorkingDirectory="$HERE" /usr/bin/python3 -u "$HERE/deck.py" $ARGS
        echo "dev board running from $HERE${ENV:+ with fakes from $FIXTURES}; ./dev.sh log follows it, ./dev.sh stop puts the package back"
        ;;
    stop)
        systemctl --user stop "$UNIT" 2>/dev/null || true
        systemctl --user stop 'deckmate-dev-tray*' 2>/dev/null || true
        systemctl --user start "$SERVICE" 2>/dev/null || echo "no installed deckmate.service to start"
        if [ -f /usr/lib/deckmate/tray.py ] && ! tray_alive; then
            systemd-run --user --quiet --collect --unit="deckmate-tray-$(date +%s)" \
                /usr/bin/python3 /usr/lib/deckmate/tray.py
        fi
        echo "installed board back"
        ;;
    settings)
        systemd-run --user --quiet --collect --unit="deckmate-dev-settings-$(date +%s)" \
            -p WorkingDirectory="$HERE" /usr/bin/python3 "$HERE/settings.py"
        ;;
    tray)
        if tray_alive; then kill "$(tray_pid)"; sleep 1; fi
        systemd-run --user --quiet --collect --unit="deckmate-dev-tray-$(date +%s)" \
            -p WorkingDirectory="$HERE" /usr/bin/python3 "$HERE/tray.py"
        echo "dev tray running; ./dev.sh stop brings the installed one back"
        ;;
    log)
        exec journalctl --user -u "$UNIT" -f -n 50
        ;;
    status)
        systemctl --user --no-pager --no-legend list-units 'deckmate*'
        ;;
    *)
        sed -n '2,15p' "$0"
        exit 2
        ;;
esac
