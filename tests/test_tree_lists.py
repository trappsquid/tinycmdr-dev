"""test_tree_lists - one merged suite (test_maintenance_kit, test_repo_hygiene).

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


def _suite_test_maintenance_kit():
    """Every script in maintenance/ is classified somewhere, and no name is in two classes.

Three lists govern that folder and nothing kept them in agreement:

  * SHIPPED - build-package.py's ALLOWED_MAINTENANCE: what a package may carry (the
    restart helpers). Anything else in maintenance/ is refused at build time.
  * DROPPED - tinycmdr.py's _DEV_MAINTENANCE_DROP: what an update deletes from a tree
    that is both a checkout and an install.
  * HOST-OWNED - the files this fleet keeps for itself (private_rules.py, a roles file):
    never shipped, never deleted.

Measured 2026-10-07: the folder held 29 files and 10 of them were in no list at all, so a
mixed tree kept them after an update while the drop note claimed the kit was gone. The
lists are name-based on purpose (a rule like "anything not shipped" would delete a host's
own files), so the fix is this check rather than a derivation.

    python tests/test_tree_lists.py

Falsification: with the pre-fix 15-name drop list this suite names the ten unclassified
scripts (check-hygiene.py, package_assets.py, probe-web-*.py, stub-openai-endpoint.py,
wait-for-endpoint.py, ...) and exits 1.
"""
    import ast
    import json
    import os
    import shutil
    import sys
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    MAINT = BASE / "maintenance"

    # Never shipped, never dropped: this fleet's own inventory, plus any roles file a host
    # keeps beside it (both are gitignored, so they exist only on a box that has one).
    HOST_OWNED = {"private_rules.py", "where-roles.json", "where-roles.example.json"}

    FAILS = []


    def check(what, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
        if not ok:
            FAILS.append(what)


    def literal_assignment(path, name):
        """The value of a module-level `<name> = <literal>` in `path`, read as a literal.

    Read from the source rather than by importing: tinycmdr.py imports the world, and
    build-package.py refuses to import without the private inventory. A tuple/set literal
    is what these two are, and the AST says so exactly.
    """
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        return ast.literal_eval(node.value)
        raise AssertionError("%s carries no %s assignment" % (path.name, name))


    def main():
        shipped = set(literal_assignment(BASE / "maintenance" / "build-package.py",
                                         "ALLOWED_MAINTENANCE"))
        dropped = set(literal_assignment(BASE / "tinycmdr.py", "_DEV_MAINTENANCE_DROP"))
        check("the three restart helpers are the shipped set",
              shipped == {"restart-tinycmdr.sh", "restart-tinycmdr.ps1",
                          "restart-tinycmdr-macos.sh"}, sorted(shipped))

        on_disk = {p.name for p in MAINT.iterdir()
                   if p.is_file() and p.suffix != ".pyc" and p.name != "__pycache__"}
        check("the folder holds scripts to classify", len(on_disk) > 10, len(on_disk))

        classes = {"shipped": shipped, "dropped": dropped, "host-owned": HOST_OWNED}
        unclassified = sorted(on_disk - set().union(*classes.values()))
        check("every file in maintenance/ is classified", not unclassified,
              "in no list: %s" % ", ".join(unclassified))

        for a, b in (("shipped", "dropped"), ("shipped", "host-owned"),
                     ("dropped", "host-owned")):
            both = sorted(classes[a] & classes[b])
            check("no name is both %s and %s" % (a, b), not both, ", ".join(both))

        # Only SHIPPED and DROPPED have to be present here. The host-owned names (private_rules.py,
        # a roles file) are gitignored, so a clean clone never has them: naming one is the point,
        # not drift. The first version of this check subtracted nothing and failed on its first run
        # in a clone that had no private_rules.py.
        stale = sorted((shipped | dropped) - on_disk)
        check("no shipped or dropped name is missing from the tree", not stale, ", ".join(stale))

        # ---- the public/private split (maintenance/product-manifest.json) ------------------
        # The product tree is generated from this file, so a file or a suite that is in neither list
        # would silently stop shipping (or silently start). Both directions are refused here.
        man_path = BASE / "maintenance" / "product-manifest.json"
        check("the product manifest is in the tree", man_path.is_file(), str(man_path))
        if man_path.is_file():
            man = json.loads(man_path.read_text(encoding="utf-8"))
            private = man.get("private_suites") or {}
            suites = {p.stem for p in (BASE / "tests").glob("test_*.py")}
            unlisted = sorted(suites - set(private))
            check("every suite is either shipped or declared private (with a reason)",
                  True, "")                                   # shipped ones need no entry
            missing_reason = sorted(k for k, v in private.items() if not str(v).strip())
            check("every private suite says why it is private", not missing_reason,
                  ", ".join(missing_reason))
            ghost = sorted(k for k in private if k not in suites)
            check("no private entry names a suite that is not there", not ghost, ", ".join(ghost))
            absent = sorted(r for r in man.get("extra", [])
                            if not (BASE / r).exists())
            check("every extra the manifest ships exists here", not absent, ", ".join(absent))
            print("note  %d of %d suites ship publicly; %d stay here"
                  % (len(suites - set(private)), len(suites), len(private)))

        # ---- check-tree-clean.py's exclusions are scoped, not a name match anywhere ---------
        # The wrapper IS the CI gate for "a suite wrote into the checkout", so a hidden path in
        # its snapshot is a hidden write. `venv` used to be skipped by NAME at any depth, which
        # made a fixture directory called venv - exactly the shape a suite stages - invisible to
        # the one check that hunts writes (run 21, A-2026-10-07-54).
        import importlib.util
        import tempfile
        path = MAINT / "check-tree-clean.py"
        spec = importlib.util.spec_from_file_location("tc_check_tree_clean", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["tc_check_tree_clean"] = mod
        spec.loader.exec_module(mod)
        work = Path(tempfile.mkdtemp(prefix="tc-treeclean-"))
        try:
            real_root = mod.ROOT
            (work / "src.py").write_text("x", encoding="utf-8")
            (work / "venv").mkdir()
            (work / "venv" / "pyvenv.cfg").write_text("home = /x", encoding="utf-8")
            (work / "venv" / "lib" / "site.py").parent.mkdir(parents=True, exist_ok=True)
            (work / "venv" / "lib" / "site.py").write_text("x", encoding="utf-8")
            (work / "pycache").mkdir()
            (work / "pycache" / "__pycache__").mkdir()
            (work / "pycache" / "__pycache__" / "m.pyc").write_text("x", encoding="utf-8")
            scratch = work / "tests" / "scratch" / "venv"
            scratch.mkdir(parents=True)
            (scratch / "fixture.py").write_text("x", encoding="utf-8")
            mod.ROOT = work
            snap = mod.snapshot()
            check("a top-level environment venv/ is still skipped",
                  "src.py" in snap and not [k for k in snap if k.startswith("venv/")],
                  sorted(snap))
            check("a venv/ DEEPER in the tree is graded, not hidden",
                  "tests/scratch/venv/fixture.py" in snap, sorted(snap))
            check("__pycache__ is skipped at any depth",
                  not [k for k in snap if "__pycache__" in k], sorted(snap))
            # A top-level `venv` that is not an environment is a plain directory: grade it.
            (work / "venv" / "pyvenv.cfg").unlink()
            snap = mod.snapshot()
            check("...and a top-level venv/ with no pyvenv.cfg is graded too",
                  "venv/lib/site.py" in snap, sorted(snap))

            # ---- a live bot in the checkout is reported, not graded (run 21, A-55) ----------
            # The same wrapper grades a live bot's own writes (its log, sessions, state) as if a
            # suite had made them, and a red naming no suite is how a check stops being read.
            # The excuse is conditional on the probe, so it is not a hole.
            classify = getattr(mod, "classify", None)
            check("the wrapper separates what a live bot owns from what a suite did",
                  callable(classify), "no classify() in check-tree-clean.py")
            if callable(classify):
                check("with no live instance, every moved path is graded",
                      classify(["tinycmdr.py", "tinycmdr.log"], False)
                      == (["tinycmdr.log", "tinycmdr.py"], []),
                      classify(["tinycmdr.py", "tinycmdr.log"], False))
                graded, excused = classify(["tinycmdr.py", "tinycmdr.log",
                                            "sessions/a.json"], True)
                check("...and with one, only the bot's own paths are excused",
                      graded == ["tinycmdr.py"]
                      and excused == ["sessions/a.json", "tinycmdr.log"], (graded, excused))
            probe = getattr(mod, "live_instance_here", None)
            check("the wrapper probes the lock rather than assuming a live bot",
                  callable(probe), "no live_instance_here()")
            if callable(probe) and os.name == "posix":
                import fcntl
                empty = Path(tempfile.mkdtemp(prefix="tc-treeclean-nolive-"))
                try:
                    mod.ROOT = empty
                    check("the probe answers False for a folder nobody holds",
                          probe() is False, probe())
                    fh = os.open(str(empty), os.O_RDONLY)
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        check("...and True while an instance holds it", probe() is True, probe())
                    finally:
                        fcntl.flock(fh, fcntl.LOCK_UN)
                        os.close(fh)
                finally:
                    mod.ROOT = work
                    shutil.rmtree(empty, ignore_errors=True)
        finally:
            mod.ROOT = real_root
            shutil.rmtree(work, ignore_errors=True)
        print()

        if FAILS:
            print("\n%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
            return 1
        print("\nall maintenance-kit checks passed")
        return 0
    return main()


def _suite_test_repo_hygiene():
    """Host state is never tracked.

theme.toml and soul.md are the operator's own theme and persona; a tracked copy is
clobbered (or deleted) by an `update` or a `git pull`. The app seeds a missing file from
theme.default.toml / soul.example.md at first start, so untracking them costs a fresh
clone nothing.

    python tests/test_tree_lists.py

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
        print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
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
    return main()


def main():
    rc = 0
    for name, fn in (("test_maintenance_kit", _suite_test_maintenance_kit), ("test_repo_hygiene", _suite_test_repo_hygiene)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
