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
    python maintenance/leak-gate.py --pre-push      # the commits a push would add (hook mode)

Exit 0 = clean. Exit 1 = something private is reachable; the report names the file or
the commit so it can be fixed before it reaches the remote.

Install the hook once per clone (hooks are not tracked by git):

    printf '#!/bin/sh\nexec python "$(git rev-parse --show-toplevel)/maintenance/leak-gate.py" --pre-push\n' \\
      > .git/hooks/pre-push && chmod +x .git/hooks/pre-push
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "maintenance" / "private_rules.py"

# Only what a leak report needs: the pattern and its label.
_PATTERNS: list[tuple[str, str]] = []


def load_patterns() -> list[tuple[str, str]]:
    """Every pattern the packager refuses, plus the doc-rewrite host rules."""
    if not RULES.exists():
        raise SystemExit(
            "maintenance/private_rules.py is missing (%s).\n"
            "It holds the fleet's private inventory and the pattern lists; copy\n"
            "private_rules.example.py to private_rules.py and fill it in."
            % RULES)
    ns: dict = {}
    exec(compile(RULES.read_text(encoding="utf-8", errors="replace"), str(RULES), "exec"), ns)
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
    shas: set[str] = set()
    for rng in ranges:
        out = git("rev-list", "--objects", rng)
        for line in out.stdout.split("\n"):
            if line.strip():
                shas.add(line.split(" ")[0])
    problems += scan_tree()
    problems += scan_blobs(sorted(shas))
    for rng in ranges:
        for line in git("log", "--format=%H%x09%s", rng).stdout.split("\n"):
            if not line.strip():
                continue
            sha, _, subject = line.partition("\t")
            for h in hits(git("log", "-1", "--format=%B", sha).stdout):
                problems.append(f"commit {sha[:8]} ({subject[:60]}): {h}")
    return problems


def main() -> int:
    global _PATTERNS
    _PATTERNS = load_patterns()
    mode = sys.argv[1] if len(sys.argv) > 1 else "--tree"
    where = {"--tree": scan_tree, "--history": scan_history, "--pre-push": scan_pre_push}.get(mode)
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
