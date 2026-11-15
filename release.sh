#!/bin/sh
# Publish a release: ./release.sh 0.2.0
# Dates the Unreleased changelog section, bumps VERSION, commits, tags and
# pushes. The CI workflow then runs the tests, builds the .deb and creates the
# GitHub release with it attached, the changelog section as its notes. The tray
# icon on every machine offers the update once the release exists.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
NEW="$1"
[ -n "$NEW" ] || { echo "usage: $0 <version>   (current: $(cat "$HERE/VERSION"))"; exit 2; }
cd "$HERE"
[ -z "$(git status --porcelain)" ] || { echo "commit or stash your changes first"; exit 1; }
./build_deb.sh >/dev/null   # runs the tests; fail here rather than in CI
# the Unreleased section becomes this version's; CI puts it in the release notes
grep -q "^## Unreleased" CHANGELOG.md || { echo "CHANGELOG.md has no '## Unreleased' section to release"; exit 1; }
sed -i "s/^## Unreleased\$/## $NEW ($(date +%F))/" CHANGELOG.md
./changelog.sh "$NEW" >/dev/null
echo "$NEW" > VERSION
git add VERSION CHANGELOG.md
git diff --cached --quiet || git commit -q -m "Release $NEW"
git tag -a "v$NEW" -m "Release $NEW"
git push -q && git push -q --tags
echo "pushed v$NEW, waiting for CI to publish the release"
sleep 10
RUN=$(gh run list --repo jlumijarvi/deckmate --branch "v$NEW" --limit 1 --json databaseId -q '.[0].databaseId')
[ -n "$RUN" ] && gh run watch "$RUN" --repo jlumijarvi/deckmate --exit-status
echo "released v$NEW: https://github.com/jlumijarvi/deckmate/releases/tag/v$NEW"
