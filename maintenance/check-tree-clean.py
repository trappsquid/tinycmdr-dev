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

One thing here is NOT the run's: a bot installed in this same folder rewrites its own log,
sessions and state every minute (the shape `~/tinycmdr` has), and this wrapper used to grade
those as if a suite had written them - a red job naming no suite, which is how a check stops
being read. So it probes the same single-instance lock the runner's own report uses, and only
when a live instance actually holds it are the paths a live bot owns reported instead of
graded (run 21, A-2026-10-07-55). CI has no live instance, so CI stays strict.

Exit 0 = the tree came back unchanged; 1 = the runner failed, or something in the tree moved;
2 = the run itself could not happen. Run it with the interpreter the suites use (the runner
inherits sys.executable).
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Windows consoles and CI captures default to a legacy code page (cp1252/cp437), and this
# runner prints what SUITES hand it: a detail line can carry "·", an em dash or a box glyph.
# Measured 2026-10-08 on windows-latest: `UnicodeEncodeError: 'charmap' codec can't encode
# characters in position 118-119` killed the report BEFORE the "what went red" block, so the
# job failed with no failure list at all. tinycmdr.py hardens its own streams at import for
# the same reason; the gate has to be at least as robust as the thing it grades.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                       # not a TextIOWrapper, or closed
        pass

ROOT = Path(__file__).resolve().parent.parent
# By NAME, at any depth: Python's own artifacts, plus the repository.
SKIP_ANY_DEPTH = (".git", "__pycache__")
# By POSITION, and only when it really is an environment - see the docstring.
SKIP_ENV_DIRS = ("venv",)
# What a bot RUNNING IN THIS FOLDER rewrites by itself. Graded normally unless the probe
# below says a live instance is up: a static allowance here would be a hole in the check.
LIVE_OWNED = ("tinycmdr.log", "sessions/", "logs/", "spill/", "memory/", "state.json",
              "tools-provenance.json", "web-sessions.json", "jobs.json", "notes.md",
              "atlas.md", "field-notes.md", "tinycmdr.lock")


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
        # .as_posix(): every other spelling of a tree-relative path here (the LIVE_OWNED
        # prefixes, run_all's writers inventory, pre-push.sh's git ls-files) is forward-slashed,
        # and on Windows str() would hand back "sessions\\x.json" - matching no prefix, so a live
        # bot's own writes read as unexplained, and the inventory matches nothing at all.
        # Measured 2026-10-08 on windows-latest: test_maintenance_kit's venv/-deeper check
        # caught it, this file having normalised at the other site (see report_leaks).
        out[p.relative_to(ROOT).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def live_instance_here():
    """True/False/None: does a LIVE bot hold this folder's single-instance lock?

    A probe, not a claim: take the lock and give it straight back. The target mirrors the
    harness's `_lock_target()` contract - on POSIX the install FOLDER itself is flocked,
    because a lock FILE is defeated by `rm`; on Windows it is tinycmdr.lock beside it - and it
    is mirrored rather than imported, because this wrapper imports nothing from the tree it
    grades (the same rule tests/run_all.py states for its own copy of the probe).
    """
    try:
        if os.name == "nt":
            target = ROOT / "tinycmdr.lock"
            if not target.exists():
                return False
            fh = open(target, "a+b")
        else:
            fh = os.open(str(ROOT), os.O_RDONLY)
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        import fcntl
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    except Exception:                                            # noqa: BLE001
        return None
    finally:
        try:
            if os.name == "nt":
                fh.close()
            else:
                os.close(fh)
        except OSError:
            pass


def _live_owned(rel):
    """True when `rel` is a path a bot running in this folder writes by itself."""
    rel = str(rel).replace(os.sep, "/")
    return any(rel == name.rstrip("/") or rel.startswith(name.rstrip("/") + "/")
               or (name.endswith("/") and rel.startswith(name)) for name in LIVE_OWNED)


def classify(moved, live):
    """Split the moved paths into (graded, excused).

    `excused` is non-empty only when the probe found a live instance: those files are the
    bot's own, not a suite's, and are reported so the operator can see what was let past.
    """
    if not live:
        return sorted(moved), []
    excused = sorted(p for p in moved if _live_owned(p))
    graded = sorted(p for p in moved if not _live_owned(p))
    return graded, excused


def main():
    ap = argparse.ArgumentParser(description="a gate run must not touch the tree")
    ap.add_argument("--select", default="",
                    help="passed through to tests/run_all.py --select (a glob)")
    ap.add_argument("--jobs", "-j", type=int, default=1,
                    help="passed through to tests/run_all.py --jobs. The sweep IS the job: "
                         "measured 2026-10-08, the suites are 391s of a 399s ubuntu job. The "
                         "before/after fingerprint below is global, so parallelism costs this "
                         "wrapper nothing (what it grades is whether the TREE moved at all).")
    args = ap.parse_args()
    live = live_instance_here()
    if live:
        print("a LIVE instance holds this folder's lock: the paths it owns by itself "
              "(%s) are reported, not graded" % ", ".join(LIVE_OWNED))
    elif live is None:
        print("could not probe the single-instance lock: every moved path is graded")
    cmd = [sys.executable, "tests/run_all.py"]
    if args.select:
        cmd += ["--select", args.select]
    if args.jobs > 1:
        cmd += ["--jobs", str(args.jobs)]
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
    graded, excused = classify(created + deleted + changed, live)
    for name in excused:
        print("  reported (a live instance owns this): %s" % name)
    if not (created or deleted or changed):
        print("  the tree came back unchanged")
    if rc != 0:
        print("the RUNNER failed (exit %s) - fix that before reading the rest" % rc)
        return 2 if rc == 2 else 1
    return 1 if graded else 0


if __name__ == "__main__":
    sys.exit(main())
