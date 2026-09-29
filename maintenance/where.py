#!/usr/bin/env python3
"""Where everything is: what is live, what is dev, what is on disk - computed, never remembered.

WHY THIS EXISTS. This has gone wrong twice, in the same way. On 2026-09-28 a review found a
hand-generated map kept beside the ops notes - outside this repository and outside every gate -
two releases and three facts out of date; it still named a scratch tree that had been deleted.
On 2026-09-29 a `git pull` in the LIVE tree was blocked by an uncommitted backport nobody
remembered had been applied, while the dev tree was 27 commits ahead, and the question "which
tree is which" had to be answered by hand again.

The lesson is not "write the map more carefully". A DOCUMENT cannot be the answer: prose has no
way to disagree with the repository, so it drifts and nobody notices. So the roles are declared
ONCE, in ROLES below, and every fact about each tree - version, commit, tag, dirty paths, distance
from origin, whether a bot is running from it - is read from that tree at the moment you ask.
There is nothing to keep in sync, which is the point.

    python3 maintenance/where.py           the table
    python3 maintenance/where.py --check   non-zero when a declared role is violated
    python3 maintenance/where.py --json    the same facts, for a script

--check is wired into maintenance/pre-push.sh. The one hard rule it enforces is that a tree
declared `must_be_clean` (the live install) carries no uncommitted change to a tracked file: that
is exactly the state that blocked the pull, and it is invisible until someone tries to update the
box.

ROLES FOR THIS BOX ONLY. The two roles below are true of any install. A box that also keeps a
backup clone or an ops workspace declares them in `maintenance/where-roles.json`, which is
gitignored - a path on somebody's share does not belong in a public repository. Same file
documented in the README:

    [{"role": "backup", "path": "~/somewhere/tinycmdr", "why": "..."}]

Point it at a whole different declaration (used by tests/test_where.py) with

    TINYCMDR_WHERE_ROLES=/path/to/roles.json  python3 maintenance/where.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# The declaration. The ONLY hand-maintained fact in this file, and the reason
# there is one place to look. Edit here, not in a note beside it. Only roles
# true of ANY box belong here: host-specific trees go in where-roles.json.
# ---------------------------------------------------------------------------
ROLES = [
    {
        "role": "live",
        "path": "~/tinycmdr",
        "why": "the install the bot actually runs from (launchd/systemd) - never edit by hand",
        "git": True,
        "must_be_clean": True,
    },
    {
        "role": "dev",
        "path": "~/tinycmdr-dev",
        "why": "the release line: all code work happens here",
        "git": True,
        "must_be_clean": False,
    },
]

# Host-specific roles, gitignored: a box's own trees and where they live. Absent is normal.
HOST_ROLES = Path(__file__).resolve().parent / "where-roles.json"

VERSION_RE = re.compile(r'^VERSION\s*=\s*["\']([^"\']+)["\']', re.M)


def roles_from(env_or_arg=None):
    """The declared roles: this repo's two, plus this host's own, or an explicit override."""
    src = env_or_arg or os.environ.get("TINYCMDR_WHERE_ROLES")
    if src:
        raw = json.loads(Path(src).read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            raise ValueError("roles file must be a non-empty JSON list")
        return raw
    roles = [dict(r) for r in ROLES]
    if HOST_ROLES.exists():
        try:
            extra = json.loads(HOST_ROLES.read_text(encoding="utf-8"))
            if isinstance(extra, list):
                named = {r.get("role") for r in roles}
                roles += [r for r in extra
                          if isinstance(r, dict) and r.get("role") not in named]
        except Exception as e:                      # a host file that is broken must not
            print("where: ignoring %s (%s)" % (HOST_ROLES, e), file=sys.stderr)
    return roles


def _run(argv, timeout=20):
    """Run a command, return (rc, stdout). Never raises: a fact we cannot read is not a crash."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "")
    except Exception:
        return 1, ""


def git_out(path, *args):
    rc, out = _run(["git", "-C", str(path)] + list(args))
    return out.strip() if rc == 0 else ""


def parse_version(path):
    """The version the tree CLAIMS, read from its own tinycmdr.py - not from a note."""
    f = Path(path) / "tinycmdr.py"
    try:
        m = VERSION_RE.search(f.read_text(encoding="utf-8", errors="replace")[:200000])
    except OSError:
        return ""
    return m.group(1) if m else ""


def tree_facts(spec):
    """Everything knowable about one declared tree, read from the tree."""
    path = Path(os.path.expanduser(str(spec.get("path") or "")))
    facts = {
        "role": spec.get("role") or "?",
        "why": spec.get("why") or "",
        "path": str(path),
        "declared_git": bool(spec.get("git", True)),
        "must_be_clean": bool(spec.get("must_be_clean", False)),
        "exists": path.exists(),
        "is_git": (path / ".git").exists(),
        "version": "",
        "head": "",
        "describe": "",
        "dirty": 0,
        "untracked": 0,
        "ahead": None,
        "behind": None,
        "newest_tag": "",
        "problems": [],
    }
    if not facts["exists"]:
        return facts
    facts["version"] = parse_version(path)
    if not facts["is_git"]:
        if facts["declared_git"]:
            facts["problems"].append("declared a git tree; no .git beside it")
        return facts
    facts["head"] = git_out(path, "log", "-1", "--format=%h %ci")
    facts["describe"] = git_out(path, "describe", "--tags", "--always")
    facts["newest_tag"] = git_out(path, "describe", "--tags", "--abbrev=0")
    porcelain = git_out(path, "status", "--porcelain")
    all_paths = [ln for ln in porcelain.splitlines() if ln.strip()]
    # TRACKED changes and untracked residue are different problems, and only the first is what
    # blocks a pull: ` M tinycmdr.py` is an edit somebody made, `?? experiments.jsonl` is a file
    # the run wrote. Measured 2026-09-29: the blocked pull in the live tree was the first kind
    # while the second kind sat beside it and looked identical in a one-line count.
    tracked = [ln for ln in all_paths if not ln.startswith("??")]
    facts["dirty"] = len(tracked)
    facts["untracked"] = len(all_paths) - len(tracked)
    counts = git_out(path, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    if counts:
        try:
            left, right = (int(n) for n in counts.split()[:2])
            facts["ahead"], facts["behind"] = left, right
        except ValueError:
            pass
    if facts["must_be_clean"] and facts["dirty"]:
        facts["problems"].append(
            "%d uncommitted CHANGE(s) to tracked files in a tree that must be clean - this is "
            "what blocks a `git pull` here" % facts["dirty"])
    return facts


def running_bots():
    """Every tinycmdr.py process on this box, with the script path it was started from.

    Read from `ps`, which is the only thing that cannot be out of date. Discovery only: a box
    where ps is unavailable reports none, and the table says so instead of guessing.
    """
    rc, out = _run(["ps", "-Ao", "pid=,command="])
    if rc != 0:
        return []
    found = []
    for line in out.splitlines():
        if "tinycmdr.py" not in line:
            continue
        pid, _, cmd = line.strip().partition(" ")
        m = re.search(r"(\S*tinycmdr\.py)", cmd)
        if m:
            found.append({"pid": pid, "script": m.group(1)})
    return found


def origin_facts(path):
    """Newest tag and main's sha as THIS clone sees them. Local refs only: no network."""
    return {
        "newest_tag": git_out(path, "describe", "--tags", "--abbrev=0", "origin/main")
        if git_out(path, "rev-parse", "--verify", "-q", "origin/main") else "",
        "main": git_out(path, "log", "-1", "--format=%h %s", "origin/main") if
        git_out(path, "rev-parse", "--verify", "-q", "origin/main") else "",
    }


def dist_facts(path):
    """What is ON DISK to install: the archives and whether their manifest is there."""
    d = Path(path) / "dist"
    if not d.is_dir():
        return None
    archives = sorted(p.name for p in d.glob("tinycmdr-*.zip")) + \
               sorted(p.name for p in d.glob("tinycmdr-*.tar.gz"))
    return {"dir": str(d), "archives": archives,
            "sha256sums": (d / "SHA256SUMS").exists()}


def render(facts, bots, origin=None, dist=None, out=sys.stdout):
    w = out.write
    w("\nDECLARED TREES  (maintenance/where.py - the one place the roles are stated)\n")
    w("  %-8s %-32s %-9s %-9s %-14s %s\n"
      % ("role", "path", "version", "commit", "tag", "state"))
    for f in facts:
        if not f["exists"]:
            state = "MISSING (not mounted / not on this box)"
        elif not f["is_git"]:
            state = "not a git repo"
        else:
            bits = []
            if f["dirty"]:
                bits.append("%d modified" % f["dirty"])
            else:
                bits.append("clean")
            if f.get("untracked"):
                bits.append("+%d untracked" % f["untracked"])
            if f["behind"]:
                bits.append("%d behind" % f["behind"])
            if f["ahead"]:
                bits.append("%d ahead" % f["ahead"])
            state = ", ".join(bits)
        w("  %-8s %-32s %-9s %-9s %-14s %s\n"
          % (f["role"], f["path"].replace(str(Path.home()), "~"), f["version"],
             (f["head"].split() or [""])[0], f["newest_tag"] or "-", state))
        if f["why"]:
            w("  %-8s   %s\n" % ("", f["why"]))
    w("\nRUNNING NOW\n")
    if not bots:
        w("  no tinycmdr.py process found on this box\n")
    for b in bots:
        role = next((f["role"] for f in facts
                     if f["exists"] and b["script"].startswith(f["path"])), "?")
        w("  pid %-7s %s   -> role: %s\n" % (b["pid"], b["script"], role))
    if origin:
        w("\nORIGIN (as this clone sees it)\n")
        w("  newest tag  %s\n" % (origin["newest_tag"] or "-"))
        w("  main        %s\n" % (origin["main"] or "-"))
    if dist:
        w("\nON DISK\n")
        w("  %s\n    %s\n    SHA256SUMS: %s\n"
          % (dist["dir"], "\n    ".join(dist["archives"]) or "(no archives)",
             "yes" if dist["sha256sums"] else "MISSING"))
    w("\n")


def check(facts, bots):
    """The declared roles, held against the trees. Return a list of failures."""
    bad = []
    paths = [f["path"] for f in facts]
    dupes = {p for p in paths if paths.count(p) > 1}
    for p in sorted(dupes):
        bad.append("two roles declare the same path: %s" % p)
    for f in facts:
        for problem in f["problems"]:
            bad.append("%s (%s): %s" % (f["role"], f["path"], problem))
        if f["must_be_clean"] and f["behind"]:
            # Not a failure: being one release behind between releases is normal. Said loudly
            # because the reverse assumption - "the live box is already current" - is what
            # makes somebody edit the wrong tree.
            print("note: the %s tree is %d commit(s) behind origin/main"
                  % (f["role"], f["behind"]), file=sys.stderr)
    live = next((f for f in facts if f["role"] == "live"), None)
    if live and live["exists"] and live["is_git"] and bots:
        scripts = [b["script"] for b in bots]
        if not any(s.startswith(live["path"]) for s in scripts):
            bad.append("a bot is running (%s) but not from the tree declared live (%s)"
                       % (", ".join(scripts), live["path"]))
    return bad


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    do_check = "--check" in argv
    roles_arg = None
    if "--roles" in argv:
        roles_arg = argv[argv.index("--roles") + 1]
    try:
        specs = roles_from(roles_arg)
    except Exception as e:
        print("where: bad role declaration: %s" % e, file=sys.stderr)
        return 2
    facts = [tree_facts(s) for s in specs]
    # The running-bot rule compares a live process against the tree declared `live`. With an
    # OVERRIDDEN declaration (a test, another box) that comparison is between two different
    # worlds, so it is only applied to this repo's own table.
    overridden = bool(roles_arg or os.environ.get("TINYCMDR_WHERE_ROLES"))
    bots = [] if overridden else running_bots()
    live_dir = next((f["path"] for f in facts if f["role"] == "live" and f["exists"]), None)
    origin = origin_facts(live_dir) if live_dir and (Path(live_dir) / ".git").exists() else None
    dist = dist_facts(live_dir) if live_dir else None
    problems = check(facts, bots)
    if as_json:
        print(json.dumps({"trees": facts, "running": bots, "origin": origin,
                          "dist": dist, "problems": problems}, indent=2))
    else:
        render(facts, bots, origin, dist)
        for p in problems:
            print("PROBLEM: %s" % p, file=sys.stderr)
    if do_check and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
