#!/usr/bin/env python3
"""Host state is never tracked.

    python maintenance/check-hygiene.py

A host writes these files for itself (its logs, sessions, secrets, notes, theme and
persona). The same tree deploys to many hosts, so a host-state file that is tracked
reads as canonical and an update overwrites the copy that host owns. `theme.toml` and
`soul.md` are the sharp case: the app seeds a missing one from `theme.default.toml` /
`soul.example.md` at first start, while a tracked copy would be clobbered or deleted.

Exit 0 when no host-state file is tracked, 1 when any is; every offender is printed so
one run names all of them.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Names a host owns. A directory name means nothing under it may be tracked.
HOST_STATE = (
    "theme.toml", "soul.md", "config.json", ".env", "confirm-allow.json",
    "field-notes.md", "notes.md", "notes-authored.json", "atlas.md",
    "experiments.jsonl", "web-sessions.json", "state.json", "jobs.json",
    "tasks.json", "tools-provenance.json",
    "sessions", "logs", "uploads", "spill", "dist", "venv",
)


def tracked_paths():
    """Every path git tracks, or None when this is not a work tree."""
    try:
        done = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                              capture_output=True, text=True)
    except OSError:
        return None
    return done.stdout.splitlines() if done.returncode == 0 else None


def is_host_state(path):
    return path in HOST_STATE or any(p in HOST_STATE for p in path.split("/"))


def main():
    tracked = tracked_paths()
    if tracked is None:
        print("ok   no git work tree here: nothing to check")
        return 0
    bad = [t for t in tracked if is_host_state(t)]
    for name in HOST_STATE:
        hit = any(t == name or t.startswith(name + "/") for t in bad)
        print(("FAIL %s is tracked" if hit else "ok   %s") % name)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
