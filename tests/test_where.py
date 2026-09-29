"""maintenance/where.py: the roles are declared once, and every fact is read from the tree.

This is the permanent half of the 2026-09-28/29 mix-up (the stale WHAT-IS-LIVE.md map, and a
`git pull` in the live tree blocked by an uncommitted backport while dev was 27 commits ahead).
The fix is a command that cannot drift rather than a document that does - so what has to be
graded is that the COMMAND is right, and that is done here on synthetic trees, which means this
suite runs anywhere, including CI on a machine that has none of the real trees.

Run:  python tests/test_where.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "maintenance" / "where.py"
FAILS = []


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def load_where():
    spec = importlib.util.spec_from_file_location("where_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["where_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def git(*args, cwd):
    p = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]
        + list(args),
        cwd=str(cwd), capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    if p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), p.stderr.strip()))
    return p.stdout


def make_tree(root, name, version, modified=False, untracked=False):
    """A real git tree with a real tinycmdr.py, so the facts come from git and not a stub."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "tinycmdr.py").write_text('VERSION = "%s"\n' % version, encoding="utf-8")
    (d / "tracked.txt").write_text("one\n", encoding="utf-8")
    git("init", "-q", cwd=d)
    git("add", "-A", cwd=d)
    git("commit", "-qm", "first", cwd=d)
    if modified:
        (d / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    if untracked:
        (d / "experiments.jsonl").write_text("{}\n", encoding="utf-8")
    return d


def main():
    where = load_where()
    tmp = Path(tempfile.mkdtemp(prefix="where-test-"))
    try:
        # ---- the declaration itself ------------------------------------------
        paths = [os.path.expanduser(r["path"]) for r in where.ROLES]
        check(len(paths) == len(set(paths)), "no two declared roles share a path")
        check(len({r["role"] for r in where.ROLES}) == len(where.ROLES),
              "each role is declared once")
        check(any(r["role"] == "live" and r.get("must_be_clean") for r in where.ROLES),
              "the live tree is the one declared must_be_clean")

        # ---- facts are read from the tree, not from a note --------------------
        live = make_tree(tmp, "live", "9.9.9")
        f = where.tree_facts({"role": "live", "path": str(live), "git": True,
                              "must_be_clean": True})
        check(f["exists"] and f["is_git"], "a real tree is seen as a git tree")
        check(f["version"] == "9.9.9", f"the version is read from its own tinycmdr.py ({f['version']})")
        check(f["dirty"] == 0 and not f["problems"], "a clean tree raises nothing")
        check(bool(f["head"]) and f["newest_tag"] == "", "the commit is read; no tag yet")

        # ---- untracked residue is NOT a modification --------------------------
        residue = make_tree(tmp, "residue", "9.9.9", untracked=True)
        f = where.tree_facts({"role": "live", "path": str(residue), "git": True,
                              "must_be_clean": True})
        check(f["dirty"] == 0 and f["untracked"] == 1,
              "an untracked file is counted separately from a change")
        check(not f["problems"],
              "  and does not fail must_be_clean - only a tracked change blocks a pull")

        # ---- a tracked change IS one -----------------------------------------
        dirty = make_tree(tmp, "dirty", "9.9.9", modified=True)
        f = where.tree_facts({"role": "live", "path": str(dirty), "git": True,
                              "must_be_clean": True})
        check(f["dirty"] == 1, "a tracked modification is counted")
        check(any("must be clean" in p for p in f["problems"]),
              "  and fails the live tree, which is the state that blocked the pull")
        f2 = where.tree_facts({"role": "dev", "path": str(dirty), "git": True,
                               "must_be_clean": False})
        check(not f2["problems"], "  while the SAME tree declared dev is allowed to be dirty")

        # ---- distance from origin, the "27 commits behind" fact ---------------
        bare = tmp / "origin.git"
        bare.mkdir()
        git("init", "-q", "--bare", cwd=bare)
        seed = make_tree(tmp, "seed", "1.0.0")
        git("remote", "add", "origin", str(bare), cwd=seed)
        git("push", "-q", "-u", "origin", "main", cwd=seed)
        clone = tmp / "clone"
        git("clone", "-q", str(bare), str(clone), cwd=tmp)
        (seed / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
        git("commit", "-qam", "second", cwd=seed)
        git("push", "-q", cwd=seed)
        git("fetch", "-q", cwd=clone)
        f = where.tree_facts({"role": "dev", "path": str(clone), "git": True})
        check(f["behind"] == 1,
              f"a clone one commit behind origin reports it ({f['behind']})")

        # ---- a missing tree is reported, not invented -------------------------
        f = where.tree_facts({"role": "backup", "path": str(tmp / "nope"), "git": True})
        check(not f["exists"] and f["version"] == "" and not f["problems"],
              "a tree that is not on this box is MISSING, not an error")

        # ---- a role that claims git but is not one ----------------------------
        plain = tmp / "plain"
        plain.mkdir()
        f = where.tree_facts({"role": "ops", "path": str(plain), "git": True})
        check(f["exists"] and not f["is_git"], "a folder with no .git is seen as such")
        check(any("declared a git tree" in p for p in f["problems"]),
              "  and a role that declares git says so")

        # ---- check() and the exit code ----------------------------------------
        roles = [{"role": "live", "path": str(live), "git": True, "must_be_clean": True},
                 {"role": "dev", "path": str(dirty), "git": True, "must_be_clean": False}]
        roles_file = tmp / "roles.json"
        roles_file.write_text(json.dumps(roles), encoding="utf-8")
        check(where.main(["--roles", str(roles_file), "--check"]) == 0,
              "--check passes when the live tree is clean (dirty dev is fine)")

        roles[0]["path"] = str(dirty)
        roles_file.write_text(json.dumps(roles), encoding="utf-8")
        check(where.main(["--roles", str(roles_file), "--check"]) == 1,
              "--check FAILS when the live tree carries a tracked change")

        roles = [{"role": "live", "path": str(live), "git": True},
                 {"role": "dev", "path": str(live), "git": True}]
        roles_file.write_text(json.dumps(roles), encoding="utf-8")
        problems = where.check([where.tree_facts(r) for r in roles], [])
        check(any("same path" in p for p in problems),
              "two roles pointing at one tree is a problem, not a silently shared answer")

        # ---- a bot running from a tree that is NOT the declared live one ------
        roles = [{"role": "live", "path": str(live), "git": True, "must_be_clean": True}]
        problems = where.check([where.tree_facts(r) for r in roles],
                               [{"pid": "1", "script": str(dirty / "tinycmdr.py")}])
        check(any("not from the tree declared live" in p for p in problems),
              "a bot running from somewhere else is caught")

        print()
        if FAILS:
            print(f"{len(FAILS)} failed")
            for f in FAILS:
                print("  - " + f)
            return 1
        print("all where-checks passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
