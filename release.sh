#!/bin/sh
# Publish a release: ./release.sh 0.2.0
# Bumps VERSION, commits, tags, builds the .deb and creates the GitHub release
# with the .deb attached. The tray icon on every machine then offers the update.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
NEW="$1"
[ -n "$NEW" ] || { echo "usage: $0 <version>   (current: $(cat "$HERE/VERSION"))"; exit 2; }
cd "$HERE"
[ -z "$(git status --porcelain)" ] || { echo "commit or stash your changes first"; exit 1; }
echo "$NEW" > VERSION
git add VERSION
git diff --cached --quiet || git commit -q -m "Release $NEW"
git tag -a "v$NEW" -m "Release $NEW"
DEB=$(./build_deb.sh)
git push -q && git push -q --tags
gh release create "v$NEW" "$DEB" --title "v$NEW" --notes "stream-deck $NEW" --repo jlumijarvi/stream-deck
echo "released v$NEW: $DEB"
