#!/usr/bin/env python3
"""leak-gate - refuse to publish anything private, in the tree OR in the history.

The packager audits PACKAGES. That is not a leak gate: the downloads stayed clean
while the repo carried fleet host names in test comments and a private chat domain
in an installer comment, because tests are not shipped and the installer comment was
not in a pattern list. This gate covers what a reader of github.com can actually get:
every tracked file, every commit message, and every blob reachable from any ref.

Patterns come from maintenance/private_rules.py (gitignored, local-only): the same
file the packager loads. Nothing private is written down here - this script is public,
so it must stay shape-only.

    python maintenance/leak-gate.py                 # the working tree (default)
    python maintenance/leak-gate.py --history       # every commit + every reachable blob
    python maintenance/leak-gate.py --range A..B    # what one push/release adds (CI, release.sh)
    python maintenance/leak-gate.py --pre-push      # hook mode: the same check, fed by git

Exit 0 = clean. Exit 1 = something private is reachable; the report names the file or
the commit so it can be fixed before it reaches the remote.

Arm it in a clone with maintenance/install-hooks.sh: hooks are not tracked by git, and a
hand-typed "install it once" is the step that gets skipped. `tinycmdr doctor` reports
whether this clone is armed.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The inventory lives in a gitignored file on the host that owns this fleet. The override
# exists for two callers: tests/test_leak_gate.py, which grades this gate on a clone (where
# private_rules.py is absent) against the shipped example, and a host whose rules live
# somewhere other than this folder. Without it the gate is unrunnable wherever the private
# inventory is not, which is every clone and every CI runner.
RULES = Path(os.environ.get("TINYCMDR_LEAK_RULES")
             or ROOT / "maintenance" / "private_rules.py")

# Only what a leak report needs: the pattern and its label.
_PATTERNS: list[tuple[str, str]] = []


def load_patterns() -> list[tuple[str, str]]:
    """Every pattern the packager refuses, plus the doc-rewrite host rules.

    The inventory is maintenance/private_rules.py (gitignored, local-only). When that file is
    absent - every clone, every CI runner - the same SOURCE TEXT may arrive as
    TINYCMDR_LEAK_PATTERNS instead, which is how a repository secret can arm the CI job
    without the list ever being written to a runner's disk or to the repo. No list at all is a
    refusal, never a clean report: a gate that grades nothing must not answer 0.
    """
    if RULES.exists():
        label, text = str(RULES), RULES.read_text(encoding="utf-8", errors="replace")
    else:
        label = "TINYCMDR_LEAK_PATTERNS"
        text = os.environ.get(label) or ""
    if not text.strip():
        raise SystemExit(
            "no pattern list: %s is missing and %s is unset.\n"
            "It holds the fleet's private inventory; copy private_rules.example.py to\n"
            "private_rules.py and fill it in, or set the environment variable."
            % (RULES, label))
    ns: dict = {}
    exec(compile(text, label, "exec"), ns)
    out = [(p, "forbidden string") for p in ns.get("PUBLIC_FORBIDDEN", ())]
    for pat, label in ns.get("PUBLIC_RULES", ()):
        out.append((pat, label))
    if not out:
        raise SystemExit("private_rules.py carries no patterns - refusing to report clean.")
    return out


def hits(text: str) -> list[str]:
    found: list[str] = []
    for pat, label in _PATTERNS:
        for m in set(re.findall(pat, text)):
            found.append(f"{m!r} ({label})")
    return sorted(set(found))


def git(*args: str, text: bool = True):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                          text=text, check=False)


def scan_tree() -> list[str]:
    problems = []
    for rel in git("ls-files").stdout.split("\n"):
        if not rel or rel.startswith("maintenance/private_rules"):
            continue
        p = ROOT / rel
        try:
            body = p.read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        for h in hits(body):
            problems.append(f"{rel}: {h}")
    return problems


def _looks_binary(body: bytes) -> bool:
    return b"\0" in body[:4096]


def scan_blobs(shas: list[str], seen: dict[str, list[str]] | None = None) -> list[str]:
    """Scan blob contents; `seen` maps a blob sha to the paths that carry it."""
    seen = seen or {}
    problems = []
    for sha in shas:
        body = git("cat-file", "blob", sha, text=False).stdout
        if _looks_binary(body):
            continue
        for h in hits(body.decode("utf-8", "replace")):
            where = ", ".join(seen.get(sha, [])[:3])
            problems.append(f"blob {sha[:8]} ({where}): {h}")
    return problems


def scan_history() -> list[str]:
    problems = []
    log = git("log", "--all", "--format=%H%x09%s").stdout
    for line in log.split("\n"):
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        body = git("log", "-1", "--format=%B", sha).stdout
        for h in hits(body):
            problems.append(f"commit {sha[:8]} message ({subject[:60]}): {h}")
    # every blob reachable from every ref, with the paths that name it
    seen: dict[str, list[str]] = {}
    for line in git("rev-list", "--all", "--objects").stdout.split("\n"):
        if not line.strip():
            continue
        sha, _, path = line.partition(" ")
        seen.setdefault(sha, []).append(path or "(tree)")
    problems += scan_blobs([s for s in seen if not git("cat-file", "-t", s).stdout.startswith("tree")], seen)
    return problems


def scan_range(ranges: list[str]) -> list[str]:
    """The gate over what a set of rev ranges ADDS: the tree, their blobs, their messages.

    One implementation for the two callers that ask "is what I am about to publish clean?" -
    the pre-push hook (ranges parsed from git's stdin) and `--range` (ranges named on the
    command line, which is what CI and release.sh can supply). It deliberately does not say
    anything about commits this range does not touch: that is what makes it usable on a repo
    whose older history is already a closed, documented exposure.
    """
    problems = scan_tree()
    # A bare rev means THAT COMMIT (`rev^!`), not everything it can reach: a caller that
    # wants a whole lineage says so with a range. Without this, "scan the tip" walked all of
    # history, which on a repo with an older, closed exposure is both slow (measured 2m07s)
    # and noisy, so nobody could arm it on the push path.
    ranges = [r if (".." in r or r.endswith("^!")) else r + "^!" for r in ranges]
    shas: set[str] = set()
    for rng in ranges:
        for line in git("rev-list", "--objects", rng).stdout.split("\n"):
            if line.strip():
                shas.add(line.split(" ")[0])
    problems += scan_blobs(sorted(shas))
    for rng in ranges:
        for line in git("log", "--format=%H%x09%s", rng).stdout.split("\n"):
            if not line.strip():
                continue
            sha, _, subject = line.partition("\t")
            for h in hits(git("log", "-1", "--format=%B", sha).stdout):
                problems.append(f"commit {sha[:8]} ({subject[:60]}): {h}")
    return problems


def scan_pre_push() -> list[str]:
    """Hook mode: scan the commits (messages + blobs) this push would add."""
    problems = []
    ranges = []
    for line in sys.stdin.read().split("\n"):
        parts = line.split()
        if len(parts) != 4:
            continue
        local, remote_sha = parts[1], parts[3]
        if local.count("0") == len(local):        # a deletion: nothing new to scan
            continue
        ranges.append(remote_sha + ".." + local if remote_sha.count("0") != len(remote_sha)
                      else local)
    if not ranges:
        return scan_tree()
    return scan_range(ranges)


def main() -> int:
    global _PATTERNS
    _PATTERNS = load_patterns()
    mode = sys.argv[1] if len(sys.argv) > 1 else "--tree"
    if mode == "--range":
        ranges = [a for a in sys.argv[2:] if a.strip()]
        if not ranges:
            # Naming no range must not read as clean: an unarmed check that answers 0 is
            # worse than no check at all (the same rule the pre-push mode follows).
            print("leak-gate --range needs at least one <before>..<after> or <rev>; "
                  "refusing to report clean")
            return 2
        where = lambda: scan_range(ranges)                            # noqa: E731
    else:
        where = {"--tree": scan_tree, "--history": scan_history,
                 "--pre-push": scan_pre_push}.get(mode)
    if where is None:
        print(__doc__.strip())
        return 2
    problems = where()
    if not problems:
        print(f"leak-gate {mode}: clean ({len(_PATTERNS)} patterns)")
        return 0
    print(f"leak-gate {mode}: {len(problems)} hit(s) - nothing private may reach the remote")
    for p in problems:
        print("  " + p)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
