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

# Every prerequisite, checked BEFORE anything is built or pushed. This script's own incident
# (v1.0.21) was a half-cut release: main was pushed, then the tool it needed was missing, so
# the tag went out with no assets. Failing here costs nothing; failing after the push cannot
# be undone.
command -v gh >/dev/null 2>&1 || { echo "gh is not installed. This script tags, publishes and
uploads assets with it: install it (brew install gh) and authenticate (gh auth login), or cut
the release through the API and attach the assets by hand." >&2; exit 2; }
gh auth status >/dev/null 2>&1 || { echo "gh is installed but not authenticated. Run
'gh auth login' first: it needs Contents: read/write, plus Workflows: read/write if this
release adds or changes a file under .github/workflows/." >&2; exit 2; }
[ -f maintenance/private_rules.py ] || { echo "maintenance/private_rules.py is missing: the
public build refuses to run without this fleet's inventory. Copy private_rules.example.py and
fill it in." >&2; exit 2; }

# `python` is not on a stock macOS PATH (it is on the fleet's Windows boxes), and every call
# below used to say exactly that: the version parse returned nothing, and the build and both
# asset checks died on "python: command not found" - after the push, which is how a cut goes
# out half-done. Resolve once, preferring this project's band, and use it everywhere.
PY=""
for cand in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "no Python 3.10-3.12 on PATH: install one, or pass it in PATH" >&2; exit 2; }
echo "interpreter: $PY ($($PY -V 2>&1))"

VER="$("$PY" -c 'import re, pathlib
t = pathlib.Path("tinycmdr.py").read_text(encoding="utf-8", errors="replace")
print(re.search("^VERSION = \"(.*?)\"", t, re.M).group(1))')"
TAG="v$VER"
say() { printf '\n=== %s\n' "$*"; }

say "version in the tree: $VER"

# ---- nothing private in the tree that is about to be shipped --------------------------
# The same check pre-push runs, on the last path before publish: a host name, a LAN address
# or a token that reaches main is a problem in git history, where the only repairs are a
# rewrite or a permanent exception. Measured 2026-10-07: 1.2s over this tree.
"$PY" maintenance/leak-gate.py --tree || {
    echo "*** refusing to cut: the tree carries something private (listed above)" >&2; exit 2; }

# ---- nothing private in what this push is about to publish ----------------------------
# --tree above grades the working tree; this grades the commits and blobs the push itself
# carries - the half that would have caught a token typed into a commit MESSAGE, which is
# how one reached the public repo (measured 2026-10-07). Range = everything not on
# origin/main yet, so a stale remote-tracking ref only ever scans MORE than gets pushed.
"$PY" maintenance/leak-gate.py --range origin/main..HEAD || {
    echo "*** refusing to cut: this push adds something private (listed above)" >&2; exit 2; }

# ---- the tree doing the cutting must be the tree that gets tagged ---------------------
# build-package.py reads the WORKING TREE while the push and the tag describe HEAD, so an
# uncommitted edit to a tracked file ships inside the release and exists in no commit:
# SHA256SUMS then describes bytes nobody can regenerate from the tag, and the green gate the
# script waits for graded the commit rather than the archive. Measured 2026-10-07 (twice in
# one afternoon): 19 tracked files were dirty one commit after a cut, and this path had no
# check at all - where.py --check guards the tree declared `live`, which this is not.
# Override deliberately with TINYCMDR_SKIP_TREE_CHECK=1 and say why in the notes.
if [ "${TINYCMDR_SKIP_TREE_CHECK:-0}" = "1" ]; then
    echo "*** TINYCMDR_SKIP_TREE_CHECK=1: cutting from a tree that may differ from HEAD" >&2
else
    dirty="$(git status --porcelain --untracked-files=no)"
    if [ -n "$dirty" ]; then
        echo "*** refusing to cut: these tracked files differ from $(git rev-parse --short HEAD):" >&2
        printf '%s\n' "$dirty" >&2
        echo "    commit (or revert) them first, so the artifact matches the tag it ships under." >&2
        exit 2
    fi
fi
if gh release view "$TAG" >/dev/null 2>&1; then
    echo "*** $TAG already exists - a published number is never rebuilt" >&2
    exit 1
fi

say "build the published shapes (win, linux, macos)"
# A FLEET KIT is a separate build: a plain run writes install/fleet-defaults.json
# from THIS box's config.json + .env, and is not published to GitHub.
"$PY" maintenance/build-package.py --public --macos
ls -1 dist/ | sed -n '1,12p'

say "the README's download names must exist in dist/ before anything is pushed"
# Every name the README uses must exist as a FILE here first: install.sh/install.ps1 ride
# as themselves, and each archive gets a copy under its stable name.
cp install.sh dist/install.sh
cp install.ps1 dist/install.ps1
# The version-independent updater, published beside the installer: an install too old to
# update itself (or one whose own updater is broken) is repaired with this.
cp update.sh dist/update.sh
cp update.ps1 dist/update.ps1
cp "dist/tinycmdr-$VER-win.zip" dist/tinycmdr-win.zip
cp "dist/tinycmdr-$VER-linux.tar.gz" dist/tinycmdr-linux.tar.gz
cp "dist/tinycmdr-$VER-macos.zip" dist/tinycmdr-macos.zip
"$PY" maintenance/check-readme-assets.py --dist dist
# ...and the built archives carry every asset the page's routes serve, byte-identical to
# the tree. 1.0.68-1.0.70 shipped without assets/webui.css at all (the page rendered
# unstyled on every install); this is the artifact half of that check, derived from the
# code - see maintenance/package_assets.py.
"$PY" maintenance/check-package-assets.py --dist dist
# ...and the page FROM the archive: served, fetched, every referenced asset 200, the
# API reporting the version it was built as. Nothing before this had ever loaded the
# page a user installs (1.0.68-1.0.70 shipped without the stylesheet).
"$PY" maintenance/check-package-page.py --dist dist

say "SHA256SUMS over the published files"
# Everything a downloader can fetch, listed once, so `sha256sum -c SHA256SUMS` works in
# the folder they downloaded into. The versioned file and its stable alias are the same
# bytes, and both are listed: the sum a reader checks is the one for the name they took.
if command -v sha256sum >/dev/null 2>&1; then SUM="sha256sum"; else SUM="shasum -a 256"; fi
( cd dist && rm -f SHA256SUMS && $SUM \
    "tinycmdr-$VER-win.zip" "tinycmdr-$VER-linux.tar.gz" "tinycmdr-$VER-macos.zip" \
    tinycmdr-win.zip tinycmdr-linux.tar.gz tinycmdr-macos.zip \
    install.sh install.ps1 update.sh update.ps1 > SHA256SUMS )
cat dist/SHA256SUMS

say "push main, then publish"
# gh is a NATIVE binary and a path in MSYS form (/c/Users/...) is not translated for
# it, so `--notes-file "$NOTES"` fails with "The system cannot find the path
# specified" AFTER the push has gone out - a half-cut release, and the tag has no
# assets (measured 2026-09-26, v1.0.21). Convert once, here, where it is cheap.
if command -v cygpath >/dev/null 2>&1; then
    NOTES="$(cygpath -m "$NOTES" 2>/dev/null || printf '%s' "$NOTES")"
fi
git push origin main

# ---- the gate must be GREEN on this exact commit before it becomes a release ----------
# Doctrine (docs/development.md §7): a release flows from green CI, and a published number
# is never rebuilt. This script used to push and tag in one breath, so a commit whose gate
# had not finished - or had failed - could still be published, and once published the
# assets stay published. Push first, WAIT for the gate run on this sha, then tag.
# Override deliberately with TINYCMDR_SKIP_CI_GATE=1 and say why in the notes.
if [ "${TINYCMDR_SKIP_CI_GATE:-0}" = "1" ]; then
    echo "*** TINYCMDR_SKIP_CI_GATE=1: tagging $(git rev-parse --short HEAD) WITHOUT a green gate" >&2
else
    SHA="$(git rev-parse HEAD)"
    say "the gate must be green on $SHA before the tag exists"
    tries="${TINYCMDR_CI_WAIT_TRIES:-90}"      # 90 x 20s = 30 minutes
    while :; do
        rows="$(gh run list --commit "$SHA" --workflow gate --limit 10 \
                    --json status,conclusion \
                    --jq '.[] | "\(.status) \(.conclusion // "-")"' 2>/dev/null || true)"
        if printf '%s\n' "$rows" | grep -qE '^completed (failure|cancelled|timed_out|startup_failure|action_required)'; then
            echo "*** the gate FAILED on $SHA - refusing to tag it. Fix and cut again:" >&2
            printf '%s\n' "$rows" >&2
            exit 1
        fi
        if printf '%s\n' "$rows" | grep -q '^completed success'; then
            echo "gate: green on $SHA"
            break
        fi
        tries=$((tries - 1))
        if [ "$tries" -le 0 ]; then
            echo "*** no green gate run for $SHA after the wait - refusing to tag an ungraded commit." >&2
            exit 1
        fi
        sleep 20
    done
fi

gh release create "$TAG" \
    "dist/tinycmdr-$VER-win.zip" \
    "dist/tinycmdr-$VER-linux.tar.gz" \
    "dist/tinycmdr-$VER-macos.zip" \
    "dist/install.sh" \
    "dist/install.ps1" \
    "dist/update.sh" \
    "dist/update.ps1" \
    --title "$TAG" --notes-file "$NOTES"

say "attach the stable names the README uses (the versioned files stay)"
# `gh release upload <tag> <file>#<label>` sets a LABEL, not the asset NAME: the asset
# would keep the versioned name and the README link would stay 404 (measured 2026-09-26,
# v1.0.19 published without its aliases). Upload the copies as plain files.
gh release upload "$TAG" --clobber \
    dist/tinycmdr-win.zip dist/tinycmdr-linux.tar.gz dist/tinycmdr-macos.zip \
    dist/SHA256SUMS

say "read the release back"
gh release view "$TAG" --json assets --jq '.assets[] | "\(.size)  \(.name)"'
"$PY" maintenance/check-readme-assets.py --tag "$TAG"

# The tag is created by gh, on the remote, so without this the tree that cut the release does
# not know it exists. That has bitten twice: v1.0.37's tag was missing from this clone until a
# fetch pulled it, and v1.0.38's was missing until the status ledger checked an anchor against
# it on 2026-09-29.
# Every "has this shipped?" question asked of this clone reads the LOCAL tag list - and the
# ledger's whole job is to answer that - so leave it correct at the end of the cut.
say "fetch the tag this cut created, so this tree knows its own release"
git fetch --tags

# The release commit was pushed BEFORE this tag existed, so its ledger items could not claim
# `expect: tagged` (no tag yet) and could not claim `untagged` either (this cut falsifies it) -
# the pushed release commit then failed CI in all three jobs because a ledger item still said
# its commit was unreleased while that commit sat in the new tag. Now that the tag exists, state
# the claim while it is checkable.
say "promote the ledger for the tag just cut, so the record and the release agree"
# A published number is never rebuilt, so a failure here cannot stop the release - but it
# must not end with the word "published" either. It used to: `|| echo` swallowed the failure,
# the diff was empty, nothing was committed, and the red arrived minutes later in CI because
# a ledger item still said its commit was unreleased while that commit sat in the new tag.
# Say it loudly, and PROVE the record agrees with the tag before claiming success.
if ! "$PY" maintenance/ledger-tag.py "$TAG"; then
    echo "*** the ledger was NOT promoted for $TAG. This is what turns CI red on the commit" >&2
    echo "    this script just pushed: promote STATUS.json for $TAG, commit and push." >&2
    exit 1
fi
if ! git diff --quiet -- STATUS.json; then
    git add STATUS.json
    git commit -q -m "status: $TAG released, and the ledger says so"
    git push origin main
fi
# The promotion says it worked; this says the repository agrees, the way the asset check
# proves the published files rather than trusting the upload.
"$PY" tests/test_status.py || {
    echo "*** STATUS.json and $TAG still disagree (above) - fix that before announcing" >&2
    exit 1; }

say "$TAG is published"
