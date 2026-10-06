"""Host state is never tracked.

theme.toml and soul.md are the operator's own theme and persona; a tracked copy is
clobbered (or deleted) by an `update` or a `git pull`. The app seeds a missing file from
theme.default.toml / soul.example.md at first start, so untracking them costs a fresh
clone nothing.

    python tests/test_repo_hygiene.py

Falsification: on the tree BEFORE the untracking commit, `git ls-files` DOES list both
names, so the tracked-file check below fails. That is the intended red - it goes green
only after `git rm --cached theme.toml soul.md`.
"""
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FAILS = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % detail))
    if not ok:
        FAILS.append(what)


def main():
    # (a) the hygiene check runs as a subprocess and reports a clean tree
    done = subprocess.run([sys.executable,
                           str(BASE / "maintenance" / "check-hygiene.py")],
                          capture_output=True, text=True)
    check("maintenance/check-hygiene.py exits 0", done.returncode == 0,
          "exit %d: %s" % (done.returncode,
                           (done.stdout.strip() or done.stderr.strip())))

    # (b) .gitignore carries both host files on a line of their own
    ignore = [(line.split("#", 1)[0]).strip()
              for line in (BASE / ".gitignore").read_text(encoding="utf-8").splitlines()]
    for name in ("theme.toml", "soul.md"):
        check(".gitignore ignores %s" % name, name in ignore)

    # (c) git does not track either name
    out = subprocess.run(["git", "-C", str(BASE), "ls-files"],
                         capture_output=True, text=True).stdout.splitlines()
    for name in ("theme.toml", "soul.md"):
        check("git does not track %s" % name, name not in out)

    if FAILS:
        print("\n%d failed: %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
