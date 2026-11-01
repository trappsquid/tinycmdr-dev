#!/usr/bin/env bash
# smoke-service.sh - install from the built package, register the launchd agent, prove
# it is loaded and answering health, then boot it out.
#
# WHY. The install job proves the package installs and runs, but it passes --no-launchd,
# and the installer suites stub launchctl, so nothing executes the path that starts the
# bot at login. A change to the plist, the label or the load fallback could ship green
# and only surface after a reboot - the same shape as the Windows restart defect that
# leaves a box deaf until the supervisor is started. This is the real door, on a real
# Mac: label and install dir are temporary, and everything is removed.
#
#   bash maintenance/smoke-service.sh                 # newest dist archive, else build
#   TINYCMDR_SERVICE_PORT=18790 bash ...              # page port (default 18790)
#   TINYCMDR_BUILD_PYTHON=./venv/bin/python bash ...  # interpreter to build with
#
# It needs a GUI session: `launchctl bootstrap gui/$UID`. A Mac reached only over SSH
# fails at that line with the installer's own message, which is a fact about the
# session, not about the package.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.tinycmdr.service-smoke"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${TINYCMDR_SERVICE_PORT:-18790}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/tc-service.XXXXXX")"
KEEP=0
[ "${TINYCMDR_SERVICE_KEEP:-0}" = "1" ] && KEEP=1

say()  { printf '\n=== %s\n' "$*"; }
die()  { printf '\n*** %s\n' "$*" >&2; exit 1; }

cleanup() {
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl unload -w "$PLIST" 2>/dev/null || true
    # Only this temp tree: the live install must never be touched.
    pkill -f "$WORK/inst/tinycmdr.py" 2>/dev/null || true
    # The plist is the registration; removing it is what lets the next run reuse the
    # label. KEEP keeps the workdir for logs, never the plist.
    rm -f "$PLIST"
    if [ "$KEEP" = 1 ]; then
        printf '\nkept for inspection: %s\n' "$WORK" >&2
        return
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT

[ "$(uname -s)" = "Darwin" ] || die "launchd is macOS-only (this is $(uname -s))"

PY="${TINYCMDR_BUILD_PYTHON:-$ROOT/venv/bin/python}"
if [ ! -x "$PY" ]; then
    PY="$(command -v python3.12 || command -v python3.11 || command -v python3.10 || true)"
fi
[ -n "$PY" ] && [ -x "$PY" ] || die "no python 3.10-3.12 to build with"
PY_ABS="$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY")"

say "build the public package (macOS)"
if [ ! -f "$ROOT/maintenance/private_rules.py" ]; then
    mkdir -p "$WORK/rules"
    cp "$ROOT/maintenance/private_rules.example.py" "$WORK/rules/private_rules.py"
    export PYTHONPATH="$WORK/rules${PYTHONPATH:+:$PYTHONPATH}"
fi
# Build every run: a reused dist archive was how the first version tested a package
# built before the installer fix and looked green/red for the wrong reason (2026-10-31).
"$PY_ABS" "$ROOT/maintenance/build-package.py" --public --macos
ASSET="$(ls -t "$ROOT"/dist/tinycmdr-*-macos.zip 2>/dev/null | head -1 || true)"
[ -n "$ASSET" ] || die "the build produced no dist/tinycmdr-*-macos.zip"

say "unpack $(basename "$ASSET")"
mkdir -p "$WORK/pkg"
unzip -q "$ASSET" -d "$WORK/pkg"
PKG="$(find "$WORK/pkg" -maxdepth 1 -mindepth 1 -type d | head -1)"
[ -n "$PKG" ] || die "the archive has no top-level directory"

say "install --no-start (files and plist, no load)"
# --no-start is the point: the plist is written and the label recorded, but the
# registration is done below, by this script, so a failed load names itself here.
bash "$PKG/install/install-tinycmdr-macos.sh" -y --no-path --no-start --label "$LABEL" \
    --web-host 127.0.0.1 --web-port "$PORT" --python "$PY_ABS" --install-dir "$WORK/inst"

[ -f "$PLIST" ] || die "the installer did not write $PLIST"
plutil -lint "$PLIST" >/dev/null || die "the installer wrote an invalid plist"
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    die "--no-start left $LABEL loaded"
fi
# The summary prints the flag; the config is what the bot obeys. A port the installer
# reports but does not write is how this smoke failed on 2026-10-31 (the bot bound
# 8790, the live bot's port, and exited saying the page was already served).
got_port="$(cd "$WORK/inst" && "$WORK/inst/venv/bin/python" -c \
    'import json; print((json.load(open("config.json")).get("web") or {}).get("port"))')"
[ "$got_port" = "$PORT" ] || die "--web-port $PORT did not land in config.json (it says $got_port)"

say "bootstrap $LABEL"
if ! launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>"$WORK/bootstrap.err"; then
    cat "$WORK/bootstrap.err" >&2
    launchctl load -w "$PLIST" 2>/dev/null \
        || die "launchctl could not load $PLIST (the plist is valid; see $WORK/bootstrap.err)"
fi

loaded=0
for _ in $(seq 1 30); do
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then loaded=1; break; fi
    sleep 1
done
[ "$loaded" = 1 ] || die "$LABEL is not loaded after 30s"

say "health from the installed tree"
up=0
for _ in $(seq 1 40); do
    # cwd is the INSTALL, not this checkout: config.json and the single-instance lock are
    # resolved from the working directory, and health run from the repo read the repo's
    # defaults and reported "not running" for a bot that was.
    if out="$(cd "$WORK/inst" && "$WORK/inst/venv/bin/python" tinycmdr.py health 2>&1)"; then
        if printf '%s' "$out" | grep -q " up " && ! printf '%s' "$out" | grep -q " is DOWN"; then
            up=1; break
        fi
    fi
    sleep 1
done
if [ "$up" != 1 ]; then
    printf '%s\n' "${out:-no health output}" >&2
    printf '\n--- launchctl print %s\n' "$LABEL" >&2
    launchctl print "gui/$(id -u)/$LABEL" 2>&1 | head -40 >&2 || true
    printf '\n--- processes under %s\n' "$WORK" >&2
    pgrep -fl "$WORK/inst" >&2 || true
    for _log in "$WORK/inst/logs/launchd.err.log" "$WORK/inst/logs/launchd.out.log" \
                "$WORK/inst/tinycmdr.log"; do
        [ -f "$_log" ] && { printf '\n--- %s\n' "$_log" >&2; tail -n 25 "$_log" >&2; }
    done
    die "health did not report up"
fi

say "bootout $LABEL"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload -w "$PLIST" 2>/dev/null || true
gone=0
for _ in $(seq 1 20); do
    if ! launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 \
        && ! pgrep -f "$WORK/inst/tinycmdr.py" >/dev/null 2>&1; then gone=1; break; fi
    sleep 1
done
[ "$gone" = 1 ] || die "$LABEL or its process is still running after bootout"

say "smoke-service: OK"
