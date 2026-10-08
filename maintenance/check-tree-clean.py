#!/usr/bin/env python3
"""Does a full gate run leave the tree alone?

    python maintenance/check-tree-clean.py
    python maintenance/check-tree-clean.py --select 'tests/test_*ledger*.py'

The gap: the gate was green while suites wrote state into the repository - the ledger,
its journal and its .md mirror, tinycmdr.log, tools-provenance.json, probe files under
tests/sessions/ - and nothing measured it. This measures it at the level that matters: ANY
file created, deleted or changed by a run whose whole job is to report the state of the code.

__pycache__ is ignored (a Python import artifact, regenerated at will), .git obviously is,
and so is a local venv/: it is 3,415 of this checkout's 3,688 files, and a `pip install` in
another terminal during the run was reported as a write the gate caused. That exclusion is
TOP-LEVEL and only for a real environment (a `pyvenv.cfg` beside it): a bare name match hid a
`venv/` anywhere in the tree, which is a blind spot in the one tool whose job is finding
writes - a suite staging a fixture directory of that name was invisible to it (run 21,
A-2026-10-07-54). The rule is about the SOURCE tree, and "the tree" means what
`git status --ignored=matching` sees.

Exit 0 = the tree came back unchanged; 1 = the runner failed, or something in the tree moved;
2 = the run itself could not happen. Run it with the interpreter the suites use (the runner
inherits sys.executable).
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# By NAME, at any depth: Python's own artifacts, plus the repository.
SKIP_ANY_DEPTH = (".git", "__pycache__")
# By POSITION, and only when it really is an environment - see the docstring.
SKIP_ENV_DIRS = ("venv",)


def _skipped(p):
    """True when `p` is one of the artifacts this check must not grade."""
    try:
        rel = p.relative_to(ROOT)
    except ValueError:
        return False
    if any(part in SKIP_ANY_DEPTH for part in rel.parts):
        return True
    return bool(rel.parts) and rel.parts[0] in SKIP_ENV_DIRS \
        and (ROOT / rel.parts[0] / "pyvenv.cfg").exists()


def snapshot():
    out = {}
    for p in ROOT.rglob("*"):
        if _skipped(p):
            continue
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:                     # vanished under us; the next snapshot decides
            continue
        out[str(p.relative_to(ROOT))] = (st.st_size, st.st_mtime_ns)
    return out


def main():
    ap = argparse.ArgumentParser(description="a gate run must not touch the tree")
    ap.add_argument("--select", default="",
                    help="passed through to tests/run_all.py --select (a glob)")
    args = ap.parse_args()
    cmd = [sys.executable, "tests/run_all.py"]
    if args.select:
        cmd += ["--select", args.select]
    print("running: %s" % " ".join(cmd))
    before = snapshot()
    t0 = time.time()
    rc = subprocess.call(cmd, cwd=str(ROOT))
    dt = time.time() - t0
    after = snapshot()
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    print("\ngate exit %s in %.1fs (%d files watched)" % (rc, dt, len(after)))
    for label, items in (("created", created), ("deleted", deleted), ("changed", changed)):
        for name in items:
            print("  %s: %s" % (label, name))
    if not (created or deleted or changed):
        print("  the tree came back unchanged")
    if rc != 0:
        print("the RUNNER failed (exit %s) - fix that before reading the rest" % rc)
        return 2 if rc == 2 else 1
    return 1 if (created or deleted or changed) else 0


if __name__ == "__main__":
    sys.exit(main())
