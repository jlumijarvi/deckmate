#!/bin/sh
# Print the CHANGELOG.md section for one version: ./changelog.sh 0.6.1
# Exits 1 when the section is missing, which release.sh and CI use as a check.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
V="$1"
[ -n "$V" ] || { echo "usage: $0 <version>"; exit 2; }
OUT=$(awk -v v="$V" '
    /^## / { on = ($2 == v) ; next }
    on { print }
' "$HERE/CHANGELOG.md" | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}')
[ -n "$OUT" ] || { echo "CHANGELOG.md has no section '## $V'" >&2; exit 1; }
printf '%s\n' "$OUT"
