#!/usr/bin/env bash
#
# release.sh - cut a published release from the current tree.
#
#   bash maintenance/release.sh <notes-file>
#
# Version bumps, the CHANGELOG entry and the README are NOT done here: they are the
# batch, and this only ships one. What it does do: build every shape the public
# needs, refuse a shape that is missing, push, tag, attach the versioned assets AND
# the stable alias names the README points at, then prove both with the asset check.
#
# The notes file becomes the release body verbatim. It must be written fresh and
# factual (title is exactly v<version>): the changelog stays the long-form record.
set -euo pipefail
cd "$(dirname "$0")/.."

NOTES="${1:-}"
[ -n "$NOTES" ] || { echo "usage: bash maintenance/release.sh <notes-file>" >&2; exit 2; }
[ -f "$NOTES" ] || { echo "no such notes file: $NOTES" >&2; exit 2; }

VER="$(python -c 'import re, pathlib
t = pathlib.Path("tinycmdr.py").read_text(encoding="utf-8", errors="replace")
print(re.search("^VERSION = \"(.*?)\"", t, re.M).group(1))')"
TAG="v$VER"
say() { printf '\n=== %s\n' "$*"; }

say "version in the tree: $VER"
if gh release view "$TAG" >/dev/null 2>&1; then
    echo "*** $TAG already exists - a published number is never rebuilt" >&2
    exit 1
fi

say "build the published shapes (win, linux, macos)"
# A FLEET KIT is a separate build: a plain run writes install/fleet-defaults.json
# from THIS box's config.json + .env, and is not published to GitHub.
python maintenance/build-package.py --public --macos
ls -1 dist/ | sed -n '1,12p'

say "the README's download names must exist in dist/ before anything is pushed"
# install.sh is a download the README names, so it belongs in dist/ and on the release.
cp install.sh dist/install.sh
cp install.ps1 dist/install.ps1
python maintenance/check-readme-assets.py --dist dist

say "push main, then publish"
git push origin main
gh release create "$TAG" \
    "dist/tinycmdr-$VER-win.zip" \
    "dist/tinycmdr-$VER-linux.tar.gz" \
    "dist/tinycmdr-$VER-macos.zip" \
    "dist/install.sh" \
    "dist/install.ps1" \
    --title "$TAG" --notes-file "$NOTES"

say "attach the stable names the README uses (the versioned files stay)"
gh release upload "$TAG" --clobber \
    "dist/tinycmdr-$VER-win.zip#tinycmdr-win.zip" \
    "dist/tinycmdr-$VER-linux.tar.gz#tinycmdr-linux.tar.gz" \
    "dist/tinycmdr-$VER-macos.zip#tinycmdr-macos.zip"

say "read the release back"
gh release view "$TAG" --json assets --jq '.assets[] | "\(.size)  \(.name)"'
python maintenance/check-readme-assets.py --tag "$TAG"
say "$TAG is published"
