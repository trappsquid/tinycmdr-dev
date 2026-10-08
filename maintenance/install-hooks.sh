#!/usr/bin/env bash
# install-hooks.sh - arm this clone so a push cannot publish something private.
#
# WHY THIS EXISTS. A check that depends on a hand-typed step is a check that gets skipped:
# the hook was a `printf` in a docstring, and installing it was left to whoever remembered.
# The step lives in a script now, and `tinycmdr doctor` reports whether a clone is armed.
#
#     bash maintenance/install-hooks.sh                # the full cheap set (pre-push.sh)
#     bash maintenance/install-hooks.sh --leak-only    # just the leak gate (stdlib python)
#     bash maintenance/install-hooks.sh --force        # replace a hook this script did not write
#
# Env: TINYCMDR_HOOK_ROOT=<clone> installs into that clone instead of this one (a second
# working tree, or a throwaway clone a suite is grading).
# Idempotent: re-running rewrites the same file and changes nothing else.
set -uo pipefail
TARGET="${TINYCMDR_HOOK_ROOT:-$(dirname "$0")/..}"
cd "$TARGET" || { echo "install-hooks: no such clone: $TARGET" >&2; exit 2; }
ROOT="$(pwd)"
HOOK=".git/hooks/pre-push"
MARK="# tinycmdr leak gate (maintenance/install-hooks.sh)"
MODE="full"
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --leak-only) MODE="leak-only" ;;
        --force)     FORCE=1 ;;
        *) echo "install-hooks: unknown argument '$arg'" >&2; exit 2 ;;
    esac
done

[ -d .git ] || { echo "install-hooks: $ROOT is not a git clone (no .git)" >&2; exit 2; }
mkdir -p .git/hooks

if [ -f "$HOOK" ] && ! grep -qF "$MARK" "$HOOK" && [ "$FORCE" != 1 ]; then
    echo "install-hooks: $HOOK exists and was not written by this script:" >&2
    sed -n '1,8p' "$HOOK" >&2
    echo "  re-run with --force to replace it - a foreign hook may be doing real work." >&2
    exit 2
fi

if [ "$MODE" = "leak-only" ]; then
    CMD='exec python3 "$(git rev-parse --show-toplevel)/maintenance/leak-gate.py" --pre-push'
else
    CMD='exec bash "$(git rev-parse --show-toplevel)/maintenance/pre-push.sh"'
fi

{
    printf '#!/bin/sh\n%s\n%s\n' "$MARK" "$CMD"
} > "$HOOK"
chmod +x "$HOOK"
sh -n "$HOOK" || { echo "install-hooks: the hook I just wrote does not parse" >&2; exit 1; }

if [ "$MODE" = "leak-only" ]; then
    echo "installed: the leak gate only (leak-gate.py --pre-push; python3 and stdlib)"
else
    echo "installed: the full cheap set (leak gate, hygiene, measured numbers, where.py, status)"
    echo "  it needs a python that can import requests - ./venv/bin/python is preferred,"
    echo "  and the hook says so plainly if it cannot find one."
fi
echo "armed: $ROOT/$HOOK"
