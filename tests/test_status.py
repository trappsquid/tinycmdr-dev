"""test_status - one merged suite (test_status, test_where).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: no member needed a namespace rewrite.
"""
import os
import sys


def _run(name, fn):
    """One member, its own snapshot: env, cwd and sys.path restored afterwards."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_path = list(sys.path)
    print("== member %s: start" % name)
    try:
        rc = fn()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
        sys.path[:] = saved_path
    rc = int(rc or 0)
    print("== member %s: exit %d" % (name, rc))
    return rc


def _suite_test_status():
    """Every work item's state is anchored to something the repository can check.

Why this exists. A 2026-09-28 review of this project's own notes found three items describing
their work as "BUILT, unshipped (commit a4722fb, not pushed; no release cut)" for a change that
had shipped in 1.0.37, one item headed as an outage while its own body recorded the fix, two
headed as closed inside a section called "Still open", and two cross-references pointing at the
wrong item. None of that was lying. The record was prose, and prose has no way to disagree with
the repository - so a reader had to re-derive every claim by hand, and a reader who did not
bothered was misled.

STATUS.json is the machine-readable half. This suite is what makes it checkable:

  1. the ledger parses and every item is well formed (id, title, a state from the allowed set)
  2. ids are unique, so an item can be referred to by name
  3. every commit anchor exists
  4. an item marked expect=tagged IS reachable from a tag - the work shipped
  5. an item marked expect=untagged is reachable from NO tag - it has not
  6. every file anchor exists

On CI. This needs tag history and actions/checkout defaults to fetch-depth: 1, which brings no
tags; the workflow therefore checks out with fetch-depth: 0. A clone with no tags fails check 4
loudly rather than passing quietly, because `git tag` is how "shipped" is decided here and a
green run that graded nothing is the failure mode this project keeps catching.

    python tests/test_status.py
"""
    import json
    import subprocess
    import sys
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    LEDGER = BASE / "STATUS.json"
    # The vocabulary the record, AGENTS.md and `tc-status --legend` share. `unreleased` is work that
    # IS committed and belongs to the next release: its anchor is in no tag, which is exactly what the
    # mismatch check below demands of it. It was missing from this tuple while the anchor requirement
    # (`state in ("shipped", "unreleased")`) and the mismatch map already expected it, so a release
    # batch could not state the truth in the window between its commit and its tag (measured
    # 2026-10-07, cutting 1.0.88).
    STATES = ("open", "blocked", "wish", "unreleased", "shipped", "closed")

    PASSES, FAILS = [], []


    def check(name, cond, detail=""):
        (PASSES if cond else FAILS).append(name)
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


    def git(*args):
        # A host with no git at all - a Windows install - raises FileNotFoundError from exec,
        # not a non-zero exit. That crashed this suite with WinError 2 before it graded anything.
        try:
            p = subprocess.run(["git", "-C", str(BASE), *args], capture_output=True, text=True)
        except OSError:
            return 127, ""
        return p.returncode, p.stdout.strip()


    def main():
        check("the ledger is in the tree", LEDGER.exists(), LEDGER)
        if not LEDGER.exists():
            print()
            print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
            return 1
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        items = data.get("items") or []
        check("it holds items", bool(items), len(items))

        ids = [it.get("id") for it in items]
        check("every item has an id and a title",
              all(it.get("id") and it.get("title") for it in items),
              [it.get("id") for it in items if not (it.get("id") and it.get("title"))])
        check("ids are unique", len(ids) == len(set(ids)),
              sorted({i for i in ids if ids.count(i) > 1}))
        check("every state is one this file knows", all(it.get("state") in STATES for it in items),
              [(it.get("id"), it.get("state")) for it in items if it.get("state") not in STATES])

        # An EXPORT has no history at all - a package, or `git archive` - and that is a normal way to
        # deploy: the fleet gate runs from one because the Windows host has no git. There the ledger's
        # shape and its file anchors are still graded, and the commit anchors cannot be; the run says
        # which and how many. A CHECKOUT with no tags is a different thing - a shallow clone or a
        # missing fetch - and that fails, because git tag is how "shipped" is decided here.
        is_checkout = (BASE / ".git").exists()
        _, taglist = git("tag", "-l") if is_checkout else (0, "")
        tags = [t for t in taglist.splitlines() if t.strip()]
        if is_checkout:
            check("this checkout carries tag history, so 'shipped' can be decided", bool(tags),
                  "no tags: actions/checkout needs fetch-depth: 0, or run `git fetch --tags`")
        else:
            print("note  no .git in this tree: it is an export, not a checkout - file anchors are "
                  "graded below, commit anchors cannot be")

        bad_file, bad_commit, wrong_expect, unverified = [], [], [], 0
        # A misspelled anchor key is worse than a missing one: measured 2026-10-06, ten items
        # from one batch wrote `anchors` (plural), every per-item check below read
        # `it.get("anchor") or {}`, skipped them, and the suite still went green - so an item
        # claiming shipped carried no verified anchor at all. Both shapes fail here now.
        unanchored = [it.get("id") for it in items
                      if it.get("state") in ("shipped", "unreleased")
                      and not isinstance(it.get("anchor"), dict)]
        stray = sorted({k for it in items for k in it
                        if isinstance(k, str) and k.startswith("anchor") and k != "anchor"})
        check("every shipped/unreleased item carries an 'anchor' object", not unanchored,
              unanchored[:8])
        check("no item carries a misspelled anchor key", not stray, stray)
        for it in items:
            a = it.get("anchor") or {}
            if "file" in a:
                if not (BASE / a["file"]).exists():
                    bad_file.append("%s -> %s" % (it["id"], a["file"]))
                continue
            commit = a.get("commit")
            if not commit:
                continue
            if not is_checkout:
                unverified += 1
                continue
            rc, _ = git("cat-file", "-e", commit + "^{commit}")
            if rc != 0:
                bad_commit.append("%s -> %s" % (it["id"], commit))
                continue
            _, containing = git("tag", "--contains", commit)
            in_tag = bool(containing.strip())
            if a.get("expect") == "tagged" and not in_tag:
                wrong_expect.append("%s says shipped, but no tag contains %s" % (it["id"], commit))
            if a.get("expect") == "untagged" and in_tag:
                wrong_expect.append("%s says unreleased, but %s is in %s"
                                    % (it["id"], commit, containing.split()[0]))

        check("every file anchor exists", not bad_file, bad_file)
        check("every commit anchor exists", not bad_commit, bad_commit)
        check("every 'shipped'/'unreleased' claim agrees with the tags", not wrong_expect, wrong_expect)
        # ...and the STATE has to agree with the anchor, or the record contradicts itself: a `shipped`
        # item whose anchor no tag contains is a claim nobody can check, and an `unreleased` item whose
        # commit IS tagged says the opposite. This is the check that was missing on 2026-10-07, when 39
        # shipped items were left with anchors no tag contained and the suite stayed green.
        mismatched = []
        for it in items:
            a = it.get("anchor") or {}
            if not a.get("commit"):
                continue
            want = {"shipped": "tagged", "unreleased": "untagged"}.get(it.get("state"))
            if want and a.get("expect") != want:
                mismatched.append("%s is %s but its anchor says expect: %s"
                                  % (it.get("id"), it.get("state"), a.get("expect")))
        check("a shipped item's anchor is tagged, an unreleased one's is not", not mismatched,
              mismatched[:6])
        if unverified:
            print("note  %d commit anchor(s) NOT verified: this tree carries no git history" % unverified)

        print()
        print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
        return 1 if FAILS else 0
    return main()


def _suite_test_where():
    """maintenance/where.py: the roles are declared once, and every fact is read from the tree.

This is the permanent half of the 2026-09-28/29 mix-up (the stale WHAT-IS-LIVE.md map, and a
`git pull` in the live tree blocked by an uncommitted backport while dev was 27 commits ahead).
The fix is a command that cannot drift rather than a document that does - so what has to be
graded is that the COMMAND is right, and that is done here on synthetic trees, which means this
suite runs anywhere, including CI on a machine that has none of the real trees.

Run:  python tests/test_status.py
"""
    import contextlib
    import importlib.util
    import io
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

            # ---- is the tree I am looking at RELEASED? ---------------------------
            # The first question a drop-in reader has (review, handoff), and one the tag line cannot
            # answer alone: `git describe` names the nearest tag HEAD can reach, so a tree that is
            # three commits PAST a release still prints that release's name and looks current.
            tagged = make_tree(tmp, "tagged", "1.0.0")
            f = where.origin_facts(str(tagged))
            check(f["unreleased"] is None and f["head_tag"] == "",
                  "a tree with no tag reports no release claim at all (%s)" % f)
            git("tag", "v1.0.0", cwd=tagged)
            f = where.origin_facts(str(tagged))
            check(f["unreleased"] == 0 and f["head_tag"] == "v1.0.0",
                  "at its tag, nothing is unreleased (%s)" % f)
            for i in range(3):
                (tagged / "tracked.txt").write_text("one\n%d\n" % i, encoding="utf-8")
                git("commit", "-qam", "change %d" % i, cwd=tagged)
            f = where.origin_facts(str(tagged))
            check(f["unreleased"] == 3 and f["head_tag"] == "v1.0.0",
                  "three commits past it report 3, and still name that tag (%s)" % f)
            check(f["head"] == git("rev-parse", "--short", "HEAD", cwd=tagged).strip(),
                  "  and the head sha is the tree's own (%s)" % f)

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

            # ---- one tree may hold two roles, if it SAYS so -----------------------
            # The arrangement a box that develops in place needs. Declared, it is not the
            # duplicate-path mix-up; undeclared it still is (checked just above).
            one = make_tree(tmp, "one-tree", "9.9.9")
            spec = {"role": "dev", "path": str(one), "git": True, "same_tree_as": "live"}
            facts = [where.tree_facts({"role": "live", "path": str(one), "git": True,
                                       "must_be_clean": True}),
                     where.tree_facts(spec)]
            check(where.check(facts, []) == [],
                  "a role declaring `same_as live` on one tree raises nothing")
            co = tmp / "roles-colocated.json"
            co.write_text(json.dumps([
                {"role": "live", "path": str(one), "git": True, "must_be_clean": True},
                {"role": "dev", "same_as": "live", "why": "one tree"}]), encoding="utf-8")
            check(where.main(["--roles", str(co), "--check"]) == 0,
                  "  and --check passes on a clean co-located pair")

            # ---- same_as inherits the path, so the two can never disagree ---------
            roles = where.roles_from(str(co))
            dev = next(r for r in roles if r["role"] == "dev")
            check(dev["path"] == str(one), "same_as takes its path from the role it names")
            check(dev["same_tree_as"] == "live", "  and records which tree it shares")

            # a same_as that names nothing, or contradicts the path it names, is refused
            bad = tmp / "roles-bad.json"
            bad.write_text(json.dumps([{"role": "dev", "same_as": "live"}]), encoding="utf-8")
            try:
                where.roles_from(str(bad))
                check(False, "same_as naming an undeclared role is refused")
            except ValueError as e:
                check("not declared" in str(e), "same_as naming an undeclared role is refused")
            facts = [where.tree_facts({"role": "live", "path": str(one), "git": True}),
                     where.tree_facts({"role": "dev", "path": str(live), "git": True,
                                       "same_tree_as": "live"})]
            check(any("different paths" in p for p in where.check(facts, [])),
                  "a same_as that contradicts the tree it names is caught")

            # ---- the host file OVERRIDES a shipped role, and says so --------------
            host = tmp / "where-roles.json"
            host.write_text(json.dumps([{"role": "dev", "path": str(one), "why": "this box"}]),
                            encoding="utf-8")
            saved_host, saved_env = where.HOST_ROLES, os.environ.pop("TINYCMDR_WHERE_ROLES", None)
            where.HOST_ROLES = host
            try:
                roles = where.roles_from()
                dev = next(r for r in roles if r["role"] == "dev")
                check(dev["path"] == str(one) and dev.get("overridden"),
                      "a host entry overrides a shipped role by name, and is marked as an override")
                shipped_live = next(r for r in where.ROLES if r["role"] == "live")["path"]
                check(next(r for r in roles if r["role"] == "live")["path"] == shipped_live,
                      "  and a role it does not name is left exactly as shipped")
            finally:
                where.HOST_ROLES = saved_host
                if saved_env is not None:
                    os.environ["TINYCMDR_WHERE_ROLES"] = saved_env

            # ---- the GitHub view degrades instead of failing ----------------------
            f = where.github_facts(str(plain))
            check(not f["available"] and f["why_not"],
                  "asking GitHub about a tree with no origin says why, rather than dying")
            check(where.ref_age(str(one)) == "",
                  "a tree nobody has fetched says so, instead of implying its refs are fresh")

            # ---- --json still parses, and admits github was not asked for ---------
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = where.main(["--roles", str(co), "--json"])
            payload = json.loads(buf.getvalue())
            check(rc == 0 and payload.get("github") is None and payload["trees"],
                  "--json is still valid JSON, with github null unless --remote was asked for")

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
    return main()


def main():
    rc = 0
    for name, fn in (("test_status", _suite_test_status), ("test_where", _suite_test_where)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
