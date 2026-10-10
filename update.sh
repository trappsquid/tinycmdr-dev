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
# The release base URL. TINYCMDR_UPDATE_URL overrides it the way install.sh's TINYCMDR_URL
# does: a mirror, and the only way this script can be driven end to end offline (its fetch
# half is otherwise hard-wired to GitHub, so no suite could execute it - measured
# 2026-10-07, run 23).
BASE="${TINYCMDR_UPDATE_URL:-https://github.com/$REPO/releases/latest/download}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "update: $DIR is $CUR; fetching the latest release for this host ($ASSET)"
fetch() {   # fetch <url> <dest>
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2"
    else
        wget -q "$1" -O "$2"
    fi
}
if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    echo "update: neither curl nor wget is available" >&2; exit 1
fi
fetch "$BASE/$ASSET" "$WORK/$ASSET" \
    || { echo "update: could not download $BASE/$ASSET" >&2
         echo "        check the network (or a proxy), or point TINYCMDR_UPDATE_URL at a" >&2
         echo "        mirror. Nothing was changed." >&2
         exit 1; }
fetch "$BASE/SHA256SUMS" "$WORK/SHA256SUMS" \
    || { echo "update: fetched the package but not $BASE/SHA256SUMS - without it nothing is" >&2
         echo "        installed. Re-run, or fetch both files by hand." >&2
         exit 1; }
# The download is verified BEFORE anything is touched: a truncated transfer must cost
# nothing, and this file cannot assume the installed code is there to check it.
WANT="$(grep " $ASSET\$" "$WORK/SHA256SUMS" | awk '{print $1}')"
[ -n "$WANT" ] || { echo "update: SHA256SUMS has no entry for $ASSET - the release is broken;" >&2
                    echo "        nothing was changed. Try again later, or install by hand." >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then
    GOT="$(sha256sum "$WORK/$ASSET" | awk '{print $1}')"
else
    GOT="$(shasum -a 256 "$WORK/$ASSET" | awk '{print $1}')"
fi
[ "$WANT" = "$GOT" ] || { echo "update: checksum mismatch - nothing was changed." >&2
                          echo "        Re-run; if it repeats, the transfer is being truncated (or" >&2
                          echo "        the release replaced)." >&2; exit 1; }

case "$ASSET" in
    *.zip)   unzip -q "$WORK/$ASSET" -d "$WORK/pkg" ;;
    *.tar.gz) mkdir -p "$WORK/pkg" && tar -xzf "$WORK/$ASSET" -C "$WORK/pkg" ;;
esac
SRC="$(find "$WORK/pkg" -maxdepth 3 -name tinycmdr.py -print -quit | xargs -r dirname)"
[ -n "$SRC" ] || { echo "update: the package has no tinycmdr.py" >&2; exit 1; }
NEW="$("$PY" -c 'import re,pathlib,sys;t=pathlib.Path(sys.argv[1]).read_text(encoding="utf-8",errors="replace");m=re.search("^VERSION = \"(.*?)\"",t,re.M);print(m.group(1) if m else "?")' "$SRC/tinycmdr.py")"

# Never DOWNGRADE. A re-pointed `latest` (or a mirror at an older release) used to be
# copied over the install silently; the in-app update verb refuses this by name, and
# the standalone updaters are the doors an OLD install reaches - so they must refuse
# it too (measured 2026-10-10; the same gap exists in update.ps1, fixed with this).
_newer() {  # _newer <a> <b> -> 0 when a is a NEWER x.y.z than b
    awk -v a="$1" -v b="$2" 'BEGIN {
        n = split(a, x, "."); m = split(b, y, ".")
        for (i = 1; i <= n || i <= m; i++) {
            xi = (i <= n) ? x[i] + 0 : 0
            yi = (i <= m) ? y[i] + 0 : 0
            if (xi > yi) exit 0
            if (xi < yi) exit 1
        }
        exit 1
    }'
}
if [ "$CUR" != "?" ] && [ "$NEW" != "?" ] && _newer "$CUR" "$NEW"; then
    echo "update: the published build is $NEW, OLDER than the $CUR installed here - refusing" >&2
    echo "        to replace a newer build with an older one. Nothing was changed." >&2
    exit 1
fi

# Host-owned paths: never overwrite, and never delete anything not in the package.
HOST_FILES="config.json .env soul.md notes.md notes-authored.json field-notes.md atlas.md experiments.jsonl web-sessions.json state.json jobs.json tasks.json tasks.journal.jsonl tasks.md confirm-allow.json tools-provenance.json theme.toml tinycmdr.log tinycmdr.lock"
HOST_DIRS="tools skills sessions snapshots logs spill venv dist .git tmp"
is_host() {
    for f in $HOST_FILES; do [ "$1" = "$f" ] && return 0; done
    for d in $HOST_DIRS; do case "$1" in "$d"/*) return 0 ;; esac; done
    return 1
}

cd "$SRC"
# The list goes through a temp file, NOT a `find | while` pipeline: a loop in a pipeline runs
# in a subshell, so COUNT died with it and the summary line could not print one - while
# update.ps1 has printed `($written file(s); ...)` all along (run 23, A-2026-10-07-67).
find . -type f | sed 's|^\./||' > "$WORK/filelist"
# What this host's requirements.txt held before the copy: a release that moved the
# dependency bounds must say so, the way the Python update verb does
# (A-2026-10-08-179).
OLD_REQ=""
[ -f "$DIR/requirements.txt" ] && OLD_REQ="$(cat "$DIR/requirements.txt")"
COUNT=0
while IFS= read -r rel; do
    is_host "$rel" && continue
    dest="$DIR/$rel"
    mkdir -p "$(dirname "$dest")"
    # BESIDE the file and renamed: a kill, a crash or a full disk mid-copy used to
    # leave a torn tinycmdr.py the next start cannot even read, with no rollback
    # (measured 2026-10-10). Each file now lands whole or not at all - an update
    # interrupted between files can still leave a mixed tree, and the installer is
    # the way to reconcile that.
    tmp="$dest.update-new"
    if cp -p "$rel" "$tmp" 2>/dev/null; then
        mv -f "$tmp" "$dest"
    else
        rm -f "$tmp"
        echo "update: could not write $dest - stopping; nothing else was changed" >&2
        exit 1
    fi
    COUNT=$((COUNT + 1))
done < "$WORK/filelist"
chmod +x "$DIR/tinycmdr" "$DIR/install/install-tinycmdr.sh" 2>/dev/null || true

if [ -f "$DIR/requirements.txt" ] && [ "$(cat "$DIR/requirements.txt")" != "$OLD_REQ" ]; then
    echo "update: dependencies changed in this release - reconcile the venv:"
    # The venv's OWN python, not $PY: $PY falls back to the system python3 when the
    # venv is missing, and that printed a command which would install the agent's
    # requirements into the system interpreter (measured 2026-10-10).
    echo "    $DIR/venv/bin/python -m pip install -r $DIR/requirements.txt"
    echo "    (a missing venv: re-run the installer in $DIR - this file needs no download)"
fi

echo "update: $CUR -> $NEW ($COUNT file(s); host-owned files left alone)"
if [ "$CUR" = "$NEW" ]; then
    echo "update: already current"
else
    echo "update: restart to run it - the bot: 'tinycmdr restart'; a terminal session: relaunch"
    # (the unix script needs no escaping here: sh has no backtick trap in double quotes)
fi

# ---- the page: an install that predates it has no token, and without one the server
# stays off (never an open port - and also no door). This script is the one updater that
# runs on ANY released version, so the ask belongs here: in the terminal the operator is
# standing at, with the mint as the default and their own token always an option.
ENVF="$DIR/.env"
if [ -f "$ENVF" ] && ! grep -q '^TINYCMDR_WEB_TOKEN=' "$ENVF"; then
    echo
    echo "This install has no page token yet (the browser page arrived in 1.0.67)."
    echo "Without one the page does not start, and nothing opens on its own."
    TOK=""
    MINTED=""
    if [ -t 0 ]; then
        printf "Page token (empty mints one, or paste your own): "
        IFS= read -r TOK || TOK=""
    fi
    if [ -z "$TOK" ]; then
        TOK="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')"
        MINTED=1
    fi
    # The file must END in a newline before appending: `>>` adds none, so an editor-saved
    # .env (no trailing newline - the common case after a manual edit) merged the key into
    # the last line: `TINYCMDR_MODEL_KEY=abcTINYCMDR_WEB_TOKEN=...` - the model key's value
    # corrupted, the token invisible to the guard above (so every later update minted
    # another) and the page never starting. `_env_set` in tinycmdr.py owns this rule for the
    # in-app writer; these standalone updaters are its twins (run 23, A-2026-10-07-63).
    if [ -s "$ENVF" ] && [ "$(tail -c 1 "$ENVF" | wc -l)" -eq 0 ]; then
        printf '\n' >> "$ENVF"
    fi
    printf 'TINYCMDR_WEB_TOKEN=%s\n' "$TOK" >> "$ENVF"
    chmod 600 "$ENVF" 2>/dev/null || true
    PORT="$("$PY" - "$DIR/config.json" <<'PYEOF'
import json, sys, pathlib
try:
    print(int((json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
               .get("web") or {}).get("port") or 8790))
except Exception:
    print(8790)
PYEOF
)"
    echo "page token written to $ENVF (mode 600 where the OS honours it)${MINTED:+ - minted for you}"
    echo "page link: http://127.0.0.1:${PORT}/#token=${TOK}"
    echo "  from another machine: ssh -N -L ${PORT}:127.0.0.1:${PORT} <user>@<box>"
    echo "  LAN access instead:   tinycmdr config set web.host 0.0.0.0   (then restart)"
    echo "  link again later:     tinycmdr web        (LAN/port wizard: tinycmdr setup)"
fi
