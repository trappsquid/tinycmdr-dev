#!/usr/bin/env bash
#
# smoke-install.sh - build the package, install FROM it, then run it. One command, and it
# is the same command the install job in .github/workflows/ci.yml runs.
#
# WHY THIS EXISTS. tests/test_installer_*.py install from a tree the suite builds itself,
# so nothing graded the artifact build-package.py actually produces - the zip and tarball
# every other host downloads. This closes that loop: it builds the public package, unpacks
# that archive into a temp dir, installs from it headless, and hands the install to
# maintenance/smoke-install.py (which drives doctor, health and a --once turn against a
# model endpoint). A green run is "the thing we publish installs and runs", not "the
# installer's own fixtures do".
#
#   bash maintenance/smoke-install.sh            # the package for THIS host
#   bash maintenance/smoke-install.sh --keep     # leave the temp tree and say where
#   TINYCMDR_BUILD_PYTHON=/opt/homebrew/bin/python3.12 bash maintenance/smoke-install.sh
#
# The model endpoint: maintenance/smoke-install.py starts a hermetic stub unless
# TINYCMDR_SMOKE_BASE_URL is set (with TINYCMDR_SMOKE_MODEL, default "main"). CI passes a
# repository secret here, so no LAN address ever lands in a tracked file - which is what
# maintenance/leak-gate.py would refuse.
#
# It writes nothing into the checkout: the interpreter that runs build-package.py is the
# one that needs private_rules.py, and when the tree does not carry one the example is
# staged on PYTHONPATH in the temp dir instead.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${TINYCMDR_BUILD_PYTHON:-python3}"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

say() { printf '\n=== %s\n' "$*"; }
die() { printf '\n*** %s\n' "$*" >&2; exit 1; }

case "$(uname -s)" in
    Darwin) OS=macos ;;
    Linux)  OS=linux ;;
    *)      die "smoke-install.sh covers macOS and Linux; on Windows the ci.yml job calls install-tinycmdr.ps1 directly" ;;
esac
command -v "$PY" >/dev/null 2>&1 || die "no $PY (set TINYCMDR_BUILD_PYTHON=...)"
PY_ABS="$(command -v "$PY")"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/tc-smoke.XXXXXX")"
if [ "$KEEP" = 1 ]; then
    say "workdir kept: $WORK"
else
    trap 'rm -rf "$WORK"' EXIT
fi

# The builder imports private_rules at module load. A clean clone has only the example:
# stage it beside the temp workdir and put it on the path, never into the tree.
if [ ! -f "$ROOT/maintenance/private_rules.py" ]; then
    mkdir -p "$WORK/rules"
    cp "$ROOT/maintenance/private_rules.example.py" "$WORK/rules/private_rules.py"
    export PYTHONPATH="$WORK/rules${PYTHONPATH:+:$PYTHONPATH}"
fi

say "build the public package ($OS)"
if [ "$OS" = macos ]; then
    "$PY_ABS" "$ROOT/maintenance/build-package.py" --public --macos
else
    "$PY_ABS" "$ROOT/maintenance/build-package.py" --public
fi

if [ "$OS" = macos ]; then
    ASSET="$(ls -t "$ROOT"/dist/tinycmdr-*-macos.zip 2>/dev/null | head -1)"
else
    ASSET="$(ls -t "$ROOT"/dist/tinycmdr-*-linux.tar.gz 2>/dev/null | head -1)"
fi
[ -n "$ASSET" ] || die "build produced no $OS archive under $ROOT/dist"

say "unpack $(basename "$ASSET")"
mkdir -p "$WORK/pkg"
if [ "$OS" = macos ]; then
    unzip -q "$ASSET" -d "$WORK/pkg"
else
    tar -xzf "$ASSET" -C "$WORK/pkg"
fi
PKG="$(find "$WORK/pkg" -maxdepth 1 -mindepth 1 -type d | head -1)"
[ -f "$PKG/tinycmdr.py" ] || die "the archive has no tinycmdr.py at its top level"

say "install from the unpacked package into $WORK/inst"
if [ "$OS" = macos ]; then
    # --no-launchd: files only, do not register an agent (the launchd analogue of -SkipTask)
    bash "$PKG/install/install-tinycmdr-macos.sh" -y --no-launchd --no-path \
        --label com.tinycmdr.smoke --python "$PY_ABS" --install-dir "$WORK/inst"
else
    # A user install insists on a systemd session bus, and a CI runner has none. The
    # installer's check is only "is there a socket at $XDG_RUNTIME_DIR/bus" (the same
    # stand-in tests/test_installer_unix.py plants), so plant one - nothing is started
    # (--no-start) and nothing outside $WORK is touched.
    export XDG_RUNTIME_DIR="$WORK/run"
    mkdir -p "$XDG_RUNTIME_DIR"
    "$PY_ABS" - "$XDG_RUNTIME_DIR/bus" <<'PY'
import socket, sys
s = socket.socket(socket.AF_UNIX)
s.bind(sys.argv[1])
s.close()
PY
    TINYCMDR_PYTHON="$PY_ABS" bash "$PKG/install/install-tinycmdr.sh" -y --mode user \
        --no-deps --no-sudoers --no-start --install-dir "$WORK/inst"
fi

say "smoke the install"
"$PY_ABS" "$ROOT/maintenance/smoke-install.py" --install-dir "$WORK/inst"

say "smoke-install: OK"
