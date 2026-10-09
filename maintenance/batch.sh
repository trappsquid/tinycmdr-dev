#!/usr/bin/env bash
# batch.sh - the ONE verb that lands a batch: render the published numbers from the tree,
# run the whole gate, commit, push.
#
#   bash maintenance/batch.sh -m "scope: what it does" [-m "why / measured / the test"]
#
# -m repeats like git's: the first line is the subject (<= 50 chars - state what changed,
# never the story), the rest is the body. What it replaces: the four hand steps every
# batch used to repeat - measured-block.py --write, chase the prose numbers it did not
# own, run tests/run_all.py, then add/commit/push - each a step that could be skipped and
# was. The renderer now owns every number in the doc that the gate grades, including the
# prose ones.
#
# The gate runs in parallel (--jobs 4; TINYCMDR_JOBS overrides): the shape CI's ubuntu job
# uses, ~100 s against ~6 min serial. What it gives up is per-suite attribution in the
# tree-write report (one union line instead of a name); when that attribution is what you
# are looking at, run `venv/bin/python tests/run_all.py` yourself first.
#
# The push goes through the pre-push hook when this clone has it armed
# (`bash maintenance/install-hooks.sh`).
set -euo pipefail
cd "$(dirname "$0")/.."

# This tree's own venv first: a stock `python3` on macOS is 3.9 and the suites need 3.10+.
PY=""
for cand in "./venv/bin/python" "./venv/Scripts/python.exe"; do
    if [ -x "$cand" ]; then PY="$cand"; break; fi
done
[ -n "$PY" ] || {
    echo "batch: no venv python - make one: python3 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-test.txt" >&2
    exit 2
}

msgs=()
while getopts "m:" opt; do
    case "$opt" in
        m) msgs+=("$OPTARG") ;;
        *) echo 'usage: bash maintenance/batch.sh -m "subject" [-m "body..."]' >&2; exit 2 ;;
    esac
done
[ "${#msgs[@]}" -ge 1 ] || {
    echo 'batch: give the message: -m "scope: what it does" [-m "why / measured / the test"]' >&2
    exit 2
}
subject="${msgs[0]}"
if [ "${#subject}" -gt 50 ]; then
    echo "batch: the subject is ${#subject} chars; the rule is <= 50 (state what changed, not the story)" >&2
    exit 2
fi

JOBS="${TINYCMDR_JOBS:-4}"
if [ ! -x .git/hooks/pre-push ]; then
    echo "batch: note - the pre-push hook is not armed in this clone (bash maintenance/install-hooks.sh)"
fi

say() { printf '\n=== %s\n' "$*"; }

say "render the published numbers from the tree"
"$PY" maintenance/measured-block.py --write

say "the gate (--jobs $JOBS; TINYCMDR_JOBS overrides)"
"$PY" tests/run_all.py --jobs "$JOBS"

say "what is about to be committed"
git status --porcelain --untracked-files=all

if [ -n "$(git status --porcelain)" ]; then
    git add -A
    commit_args=()
    for m in "${msgs[@]}"; do commit_args+=(-m "$m"); done
    git commit "${commit_args[@]}"
else
    echo "batch: nothing to commit - pushing whatever is already ahead"
fi

say "push"
git push
