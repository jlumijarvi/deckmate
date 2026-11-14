#!/bin/sh
# Start the Stream Deck board at login for the account that runs this script.
#   ./install_service.sh           install and start
#   ./install_service.sh --remove  stop and remove
# For running from a git checkout. The .deb package installs its own unit for
# every account instead; do not use both.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/stream-deck.service"

if [ "$1" = "--remove" ]; then
    systemctl --user disable --now stream-deck.service 2>/dev/null || true
    rm -f "$UNIT"
    systemctl --user daemon-reload
    echo "removed $UNIT"
    exit 0
fi

python3 -c "import PIL" 2>/dev/null || { echo "Pillow is missing: sudo apt install python3-pil"; exit 1; }

mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<UNIT
[Unit]
Description=Stream Deck board (deck.py)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$HERE
ExecStart=/usr/bin/python3 -u $HERE/deck.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now stream-deck.service
echo "installed $UNIT and started it"
echo "  status:  systemctl --user status stream-deck"
echo "  log:     journalctl --user -u stream-deck -f"
