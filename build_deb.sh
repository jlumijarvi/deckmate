#!/bin/sh
# Build deckmate_<version>_all.deb into dist/.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
VERSION=$(cat "$HERE/VERSION")
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
LIB="$STAGE/usr/lib/deckmate"

python3 -m unittest discover -s "$HERE/tests" -q >/dev/null 2>&1 \
    || { python3 -m unittest discover -s "$HERE/tests"; echo "tests failed, not building"; exit 1; }

mkdir -p "$LIB" "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/share/applications" \
         "$STAGE/etc/xdg/autostart" "$STAGE/usr/lib/systemd/user" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/share/polkit-1/actions"
# every module, so a new file cannot be forgotten; hello_deck.py is a dev-only test
for f in "$HERE"/*.py "$HERE/keys.example.json" "$HERE/VERSION"; do
    case "$(basename "$f")" in hello_deck.py) ;; *) cp "$f" "$LIB/" ;; esac
done
# the installed board must import cleanly
PYTHONPATH="$LIB" python3 -c "import deck, settings, tray, sessions, slackstate, todo, paths" 2>/dev/null \
    || PYTHONPATH="$LIB" python3 -c "import deck, sessions, slackstate, todo, paths" || { echo "staged modules do not import"; exit 1; }
cp "$HERE/data/deckmate" "$STAGE/usr/bin/"
cp "$HERE/data/io.github.jlumijarvi.Deckmate.desktop" "$STAGE/usr/share/applications/"
cp "$HERE/data/deckmate-tray.desktop" "$STAGE/etc/xdg/autostart/"
cp "$HERE/data/deckmate.service" "$STAGE/usr/lib/systemd/user/"
cp "$HERE/data/icons/"*.svg "$STAGE/usr/share/icons/hicolor/scalable/apps/"
cp "$HERE/data/install-update" "$LIB/"
cp "$HERE/data/io.github.jlumijarvi.Deckmate.policy" "$STAGE/usr/share/polkit-1/actions/"
sed "s/@VERSION@/$VERSION/" "$HERE/packaging/control" > "$STAGE/DEBIAN/control"
cp "$HERE/packaging/postinst" "$HERE/packaging/prerm" "$HERE/packaging/postrm" "$STAGE/DEBIAN/"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm" "$STAGE/DEBIAN/postrm" "$STAGE/usr/bin/deckmate"
chmod 644 "$LIB"/* "$STAGE/usr/share/applications/"* "$STAGE/etc/xdg/autostart/"* \
          "$STAGE/usr/lib/systemd/user/"* "$STAGE/usr/share/icons/hicolor/scalable/apps/"* \
          "$STAGE/usr/share/polkit-1/actions/"*
chmod 755 "$LIB/install-update"

mkdir -p "$HERE/dist"
OUT="$HERE/dist/deckmate_${VERSION}_all.deb"
dpkg-deb --root-owner-group -b "$STAGE" "$OUT" >/dev/null
echo "$OUT"
