#!/usr/bin/env bash
#
# pre-push.sh - the checks a push must pass locally, before it can leave this machine.
#
#   bash maintenance/pre-push.sh
#
# WHY THIS EXISTS. On 2026-09-29 two pushes went out with a stale measured block: two edits to
# tests/test_status.py had added lines, the doc's committed figure still said 22,330 where the tree
# read 22,349, and main was RED for two commits before anyone looked. Both checks below decide it in
# well under a second. A push must not be able to leave main red for something a local command can
# settle - and the fleet gate found that one only because it recomputes the numbers on another
# machine, which is a slow way to learn something a 0.3s check knew.
#
# The heavy gate (tests/run_all.py, about two minutes) is deliberately NOT here: it belongs in CI
# and in the release checklist. This is the cheap set - the things that go stale because a file
# changed and nothing regenerated what depends on it.
#
# Install it once per clone:
#     printf '#!/bin/sh\nexec bash "$(git rev-parse --show-toplevel)/maintenance/pre-push.sh"\n' \
#         > .git/hooks/pre-push && chmod +x .git/hooks/pre-push
#
# Override deliberately with `git push --no-verify`; say why in the commit if you do.
set -uo pipefail
cd "$(dirname "$0")/.."

# This tree's own venv first: the README tells a reader to make one, and the band-resolved python
# on a stock macOS has no `requests` (measured 2026-09-29 - python3.12 from Homebrew died with
# ModuleNotFoundError on the measured-block check while the venv was fine). Only then fall back.
PY=""
for cand in "./venv/bin/python" "./venv/Scripts/python.exe"; do
    if [ -x "$cand" ]; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
    for cand in python3.12 python3.11 python3.10 python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
    done
fi
[ -n "$PY" ] || { echo "pre-push: no python on PATH" >&2; exit 2; }
# Say what is missing rather than dying inside somebody else's traceback.
if ! "$PY" -c 'import requests' >/dev/null 2>&1; then
    echo "pre-push: $PY cannot import requests. Make the venv the README describes:" >&2
    echo "    python3.12 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-test.txt" >&2
    exit 2
fi

fail=0
say() { printf '\n=== %s\n' "$*"; }

say "leak gate: files, commit messages, reachable blobs"
"$PY" maintenance/leak-gate.py --pre-push || fail=1

say "the published numbers are regenerated from the tree"
"$PY" maintenance/measured-block.py || fail=1

say "the work ledger's anchors agree with the repository"
"$PY" tests/test_status.py || fail=1

if [ "$fail" != 0 ]; then
    echo
    echo "pre-push: REFUSED. Fix the above, or override deliberately with --no-verify." >&2
    exit 1
fi
echo
echo "pre-push: clean"
