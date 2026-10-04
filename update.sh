#!/bin/sh
# tinycmdr update.sh - the ONE update path, version-independent.
#
#   sh update.sh [install-dir]
#
# The rule this file exists for: every user, on every released version, types
# `tinycmdr update` (or `/tinycmdr update` in chat) and it works. An installed copy is
# whatever version it is - possibly one whose own updater is old, broken, or predates the
# release package entirely - so the repair must not depend on it. This script is fetched
# from the LATEST release and does the whole job itself:
#
#   1. fetch the release package for this host (Linux/macOS) and its SHA256SUMS
#   2. verify the checksum before touching anything
#   3. extract to a temp dir, then copy over the install - never overwriting a host-owned
#      file (config.json, .env, soul.md, notes.md, tools/, skills/, sessions/, state, jobs,
#      tasks, logs, spill, venv, theme.toml) and never deleting anything else
#   4. say the version it moved from and to, and how to restart
#
# No git, ever: a dirty, pruned or gitless checkout is not a blocker (that was the 1.0.46
# dead end). Exit codes: 0 updated or already current, 1 refused/failed (install untouched).
set -eu

REPO="trappsquid/tinycmdr"
DIR="${1:-$(cd "$(dirname "$0")" && pwd)}"
DIR="$(cd "$DIR" && pwd)"
[ -f "$DIR/tinycmdr.py" ] || { echo "update: $DIR is not a tinycmdr install (no tinycmdr.py)" >&2; exit 1; }

PY="${TINYCMDR_PYTHON:-$DIR/venv/bin/python}"
[ -x "$PY" ] || PY=python3
CUR="$("$PY" -c 'import re,pathlib,sys;t=pathlib.Path(sys.argv[1]).read_text(encoding="utf-8",errors="replace");m=re.search("^VERSION = \"(.*?)\"",t,re.M);print(m.group(1) if m else "?")' "$DIR/tinycmdr.py")"

case "$(uname -s)" in
    Darwin) ASSET="tinycmdr-macos.zip" ;;
    *)      ASSET="tinycmdr-linux.tar.gz" ;;
esac
BASE="https://github.com/$REPO/releases/latest/download"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "update: $DIR is $CUR; fetching the latest release for this host ($ASSET)"
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$BASE/$ASSET" -o "$WORK/$ASSET"
    curl -fsSL "$BASE/SHA256SUMS" -o "$WORK/SHA256SUMS"
elif command -v wget >/dev/null 2>&1; then
    wget -q "$BASE/$ASSET" -O "$WORK/$ASSET"
    wget -q "$BASE/SHA256SUMS" -O "$WORK/SHA256SUMS"
else
    echo "update: neither curl nor wget is available" >&2; exit 1
fi
# The download is verified BEFORE anything is touched: a truncated transfer must cost
# nothing, and this file cannot assume the installed code is there to check it.
WANT="$(grep " $ASSET\$" "$WORK/SHA256SUMS" | awk '{print $1}')"
[ -n "$WANT" ] || { echo "update: SHA256SUMS has no entry for $ASSET" >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then
    GOT="$(sha256sum "$WORK/$ASSET" | awk '{print $1}')"
else
    GOT="$(shasum -a 256 "$WORK/$ASSET" | awk '{print $1}')"
fi
[ "$WANT" = "$GOT" ] || { echo "update: checksum mismatch - nothing was changed" >&2; exit 1; }

case "$ASSET" in
    *.zip)   unzip -q "$WORK/$ASSET" -d "$WORK/pkg" ;;
    *.tar.gz) mkdir -p "$WORK/pkg" && tar -xzf "$WORK/$ASSET" -C "$WORK/pkg" ;;
esac
SRC="$(find "$WORK/pkg" -maxdepth 3 -name tinycmdr.py -print -quit | xargs -r dirname)"
[ -n "$SRC" ] || { echo "update: the package has no tinycmdr.py" >&2; exit 1; }
NEW="$("$PY" -c 'import re,pathlib,sys;t=pathlib.Path(sys.argv[1]).read_text(encoding="utf-8",errors="replace");m=re.search("^VERSION = \"(.*?)\"",t,re.M);print(m.group(1) if m else "?")' "$SRC/tinycmdr.py")"

# Host-owned paths: never overwrite, and never delete anything not in the package.
HOST_FILES="config.json .env soul.md notes.md notes-authored.json field-notes.md atlas.md experiments.jsonl web-sessions.json state.json jobs.json tasks.json tasks.journal.jsonl tasks.md confirm-allow.json tools-provenance.json theme.toml tinycmdr.log tinycmdr.lock"
HOST_DIRS="tools skills sessions logs spill venv dist .git"
is_host() {
    for f in $HOST_FILES; do [ "$1" = "$f" ] && return 0; done
    for d in $HOST_DIRS; do case "$1" in "$d"/*) return 0 ;; esac; done
    return 1
}

cd "$SRC"
COUNT=0
find . -type f | sed 's|^\./||' | while IFS= read -r rel; do
    is_host "$rel" && continue
    dest="$DIR/$rel"
    mkdir -p "$(dirname "$dest")"
    cp -p "$rel" "$dest"
    COUNT=$((COUNT + 1))
done
chmod +x "$DIR/tinycmdr" "$DIR/install/install-tinycmdr.sh" 2>/dev/null || true

echo "update: $CUR -> $NEW (host-owned files left alone)"
if [ "$CUR" = "$NEW" ]; then
    echo "update: already current"
else
    echo "update: restart to run it - the bot: 'tinycmdr restart'; a terminal session: relaunch"
    # (the unix script needs no escaping here: sh has no backtick trap in double quotes)
fi
