#!/usr/bin/env bash
#
# finish-release.sh - the TAIL of a cut, on its own.
#
#   bash maintenance/finish-release.sh [<notes-file>] [--dry-run]
#
# release.sh does everything up to and including the tag: it also builds the published shapes
# and pushes, but the release object itself and its assets come after the tag exists - and once
# the tag exists release.sh refuses to re-enter on purpose ("a published number is never
# rebuilt"). That left a cut that failed in its tail - a red install-surface CI, a missing asset
# check, the host changing a file mid-cut (measured 2026-10-08: the tail was re-typed by hand,
# twice) - with no path forward but the steps below.
#
# Without a notes file the body is the version's own CHANGELOG section: one sentence per item,
# which is what the release body has to be.
#
# --dry-run does everything except the two `gh release` calls, so the artifact half can be
# checked against a number before the number is attached to it.
set -euo pipefail
cd "$(dirname "$0")/.."

DRY=0
NOTES=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        *) NOTES="$arg" ;;
    esac
done

command -v gh >/dev/null 2>&1 || { echo "gh is not installed. This script attaches assets to a
release with it: install it (brew install gh) and authenticate (gh auth login)." >&2; exit 2; }
gh auth status >/dev/null 2>&1 || { echo "gh is installed but not authenticated. Run
'gh auth login' first: it needs Contents: read/write." >&2; exit 2; }
[ -f maintenance/private_rules.py ] || { echo "maintenance/private_rules.py is missing: the
public build refuses to run without this fleet's inventory." >&2; exit 2; }

PY=""
for cand in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "no Python 3.10-3.12 on PATH: install one, or pass it in PATH" >&2; exit 2; }

VER="$("$PY" -c 'import re, pathlib
t = pathlib.Path("tinycmdr.py").read_text(encoding="utf-8", errors="replace")
print(re.search("^VERSION = \"(.*?)\"", t, re.M).group(1))')"
TAG="v$VER"
RELEASE_REPO="${TINYCMDR_RELEASE_REPO:-trappsquid/tinycmdr}"
PRODUCT_DIR="${TINYCMDR_PRODUCT_DIR:-../tinycmdr-product}"
export GH_REPO="$RELEASE_REPO"
say() { printf '\n=== %s\n' "$*"; }

say "finishing $TAG (release -> $RELEASE_REPO)"

# ---- the guards release.sh applies before this point, re-applied --------------------------
# The tag must exist (release.sh makes it; this path only finishes one), the tree must be clean
# (the artifact has to match the tag it ships under), the release must not exist (a published
# number is never rebuilt) and the product tree must be one.
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || {
    echo "*** $TAG does not exist in this tree - cutting it is release.sh's job" >&2; exit 2; }
dirty="$(git status --porcelain --untracked-files=no)"
if [ -n "$dirty" ]; then
    echo "*** refusing to finish: these tracked files differ from HEAD:" >&2
    printf '%s\n' "$dirty" >&2
    exit 2
fi
if gh release view "$TAG" >/dev/null 2>&1; then
    echo "*** $TAG already has a release - a published number is never rebuilt" >&2
    exit 1
fi
[ -f "$PRODUCT_DIR/tinycmdr.py" ] && [ -f "$PRODUCT_DIR/README.md" ] || {
    echo "*** $PRODUCT_DIR is not a product tree - run release.sh, which regenerates it" >&2
    exit 2; }

if [ -z "$NOTES" ]; then
    NOTES="$(mktemp -t "tinycmdr-notes-$VER")"
    "$PY" - "$VER" "$NOTES" <<'PYEOF'
import pathlib, re, sys
ver, out = sys.argv[1], sys.argv[2]
text = pathlib.Path("CHANGELOG.md").read_text(encoding="utf-8")
found = re.search(r"## \[%s\][^\n]*\n(.*?)(?=\n## \[)" % re.escape(ver), text, re.S)
if not found:
    sys.exit("no CHANGELOG section for %s - write the notes file by hand" % ver)
pathlib.Path(out).write_text(found.group(1).strip() + "\n", encoding="utf-8")
print("notes taken from the CHANGELOG: %s" % out)
PYEOF
fi
[ -f "$NOTES" ] || { echo "no such notes file: $NOTES" >&2; exit 2; }
# A path in MSYS form (/c/Users/...) is not translated for gh, a NATIVE binary, so
# `--notes-file` fails AFTER the tag exists - the half-cut shape this script exists for.
if command -v cygpath >/dev/null 2>&1; then
    NOTES="$(cygpath -m "$NOTES" 2>/dev/null || printf '%s' "$NOTES")"
fi

say "build the published shapes (win, linux, macos)"
# A FLEET KIT is a separate build: a plain run writes install/fleet-defaults.json from THIS
# box's config.json + .env, and is not published to GitHub.
"$PY" maintenance/build-package.py --public --macos
cp install.sh dist/install.sh
cp install.ps1 dist/install.ps1
cp update.sh dist/update.sh
cp update.ps1 dist/update.ps1
cp "dist/tinycmdr-$VER-win.zip" dist/tinycmdr-win.zip
cp "dist/tinycmdr-$VER-linux.tar.gz" dist/tinycmdr-linux.tar.gz
cp "dist/tinycmdr-$VER-macos.zip" dist/tinycmdr-macos.zip
"$PY" maintenance/check-readme-assets.py --dist dist
"$PY" maintenance/check-package-assets.py --dist dist
"$PY" maintenance/check-package-page.py --dist dist

say "SHA256SUMS over the published files"
if command -v sha256sum >/dev/null 2>&1; then SUM="sha256sum"; else SUM="shasum -a 256"; fi
( cd dist && rm -f SHA256SUMS && $SUM \
    "tinycmdr-$VER-win.zip" "tinycmdr-$VER-linux.tar.gz" "tinycmdr-$VER-macos.zip" \
    tinycmdr-win.zip tinycmdr-linux.tar.gz tinycmdr-macos.zip \
    install.sh install.ps1 update.sh update.ps1 > SHA256SUMS )
cat dist/SHA256SUMS

if [ "$DRY" = "1" ]; then
    say "dry run: built and checked; NOT creating $TAG"
    exit 0
fi

say "create the release on the install surface, at the tag that tree carries"
# `--verify-tag`: the release attaches to the tag the install surface already has, instead of
# gh inventing one from that repo's default branch.
gh release create "$TAG" \
    "dist/tinycmdr-$VER-win.zip" \
    "dist/tinycmdr-$VER-linux.tar.gz" \
    "dist/tinycmdr-$VER-macos.zip" \
    "dist/install.sh" \
    "dist/install.ps1" \
    "dist/update.sh" \
    "dist/update.ps1" \
    --verify-tag --title "$TAG" --notes-file "$NOTES"

say "attach the stable names the README uses (the versioned files stay)"
# `gh release upload <tag> <file>#<label>` sets a LABEL, not the asset NAME: the asset would
# keep the versioned name and the README link would stay 404 (measured 2026-09-26, v1.0.19).
gh release upload "$TAG" --clobber \
    dist/tinycmdr-win.zip dist/tinycmdr-linux.tar.gz dist/tinycmdr-macos.zip \
    dist/SHA256SUMS

say "read the release back"
gh release view "$TAG" --json assets --jq '.assets[] | "\(.size)  \(.name)"'
"$PY" maintenance/check-readme-assets.py --tag "$TAG"
say "done: $TAG is published"
