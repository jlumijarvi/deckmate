#!/bin/sh
# Build stream-deck_<version>_all.deb into dist/.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
VERSION=$(cat "$HERE/VERSION")
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
LIB="$STAGE/usr/lib/stream-deck"

mkdir -p "$LIB" "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/share/applications" \
         "$STAGE/etc/xdg/autostart" "$STAGE/usr/lib/systemd/user" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps"
for f in deckhid.py deck.py sessions.py slackstate.py settings.py tray.py paths.py keys.example.json VERSION; do
    cp "$HERE/$f" "$LIB/"
done
cp "$HERE/data/stream-deck" "$STAGE/usr/bin/"
cp "$HERE/data/stream-deck.desktop" "$STAGE/usr/share/applications/"
cp "$HERE/data/stream-deck-tray.desktop" "$STAGE/etc/xdg/autostart/"
cp "$HERE/data/stream-deck.service" "$STAGE/usr/lib/systemd/user/"
cp "$HERE/data/icons/"*.svg "$STAGE/usr/share/icons/hicolor/scalable/apps/"
sed "s/@VERSION@/$VERSION/" "$HERE/packaging/control" > "$STAGE/DEBIAN/control"
cp "$HERE/packaging/postinst" "$HERE/packaging/prerm" "$HERE/packaging/postrm" "$STAGE/DEBIAN/"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm" "$STAGE/DEBIAN/postrm" "$STAGE/usr/bin/stream-deck"
chmod 644 "$LIB"/* "$STAGE/usr/share/applications/"* "$STAGE/etc/xdg/autostart/"* \
          "$STAGE/usr/lib/systemd/user/"* "$STAGE/usr/share/icons/hicolor/scalable/apps/"*

mkdir -p "$HERE/dist"
OUT="$HERE/dist/stream-deck_${VERSION}_all.deb"
dpkg-deb --root-owner-group -b "$STAGE" "$OUT" >/dev/null
echo "$OUT"
