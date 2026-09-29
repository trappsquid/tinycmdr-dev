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

say "every tracked path can survive a checkout"
# A filename with a space or a shell character is legal on macOS and Linux and REJECTED by git on
# Windows: measured 2026-09-29, a stray file created by a mis-quoted shell command ("%r % (c,
# T.destructive_risk(c)))\"") passed the leak gate, the measured block and the ledger, was
# committed by `git add -A`, was pushed - and broke the Windows CI job at CHECKOUT, before a
# single test ran. On this machine nothing objected.
if ! "$PY" - <<'PYEOF'
import subprocess
import sys
# -z, not the default: git C-QUOTES a path holding a quote or a backslash, and the quoted
# form is not a pathspec - `git rm -- "<that>"` answers "did not match any files".
raw = subprocess.run(["git", "ls-files", "-z"], capture_output=True).stdout
paths = [q.decode("utf-8", "surrogateescape") for q in raw.split(b"\0") if q]
bad = [p for p in paths if any(ch in p for ch in ' %()"\';|&')]
for p in bad:
    print("  %s" % p)
print("tracked paths: %d, carrying a space or a shell character: %d" % (len(paths), len(bad)))
sys.exit(1 if bad else 0)
PYEOF
then
    fail=1
fi

if [ "$fail" != 0 ]; then
    echo
    echo "pre-push: REFUSED. Fix the above, or override deliberately with --no-verify." >&2
    exit 1
fi
echo
echo "pre-push: clean"
