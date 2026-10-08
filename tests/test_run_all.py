"""The gate's own controls: what it grades, and that the tree cannot shrink the sweep.

Three properties of tests/run_all.py that no suite graded, each one measured wrong once:

  * WHICH BUILD the suites grade. Every suite picks its file with
    `BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")`, and the runner used to pop that
    variable out of the child environment as a stale shell pick - so
    `TINYCMDR_SRC=<pre-fix build> python tests/run_all.py` answered green for the
    checkout's own tinycmdr.py. The falsification step read as "the test does not
    reproduce the bug".
  * THE TREE KILL. run_one spawns each suite in its own process group and promises a
    helper dies with it; on Windows proc.kill() reached the direct child only.
  * THE ENVELOPE. A missing tests/test_envelope.py was a note, not a red gate.

    python tests/test_run_all.py

Falsification: against the pre-fix tests/run_all.py, graded_source() does not exist (so
the import-and-call checks fail), _windows_kill_argv does not exist, and a run with
TINYCMDR_SRC pointing at a missing file goes on to grade the tree's own build instead of
exiting 2.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RUN_ALL = BASE / "tests" / "run_all.py"
ENVELOPE = BASE / "tests" / "test_envelope.py"
FAILS = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % detail))
    if not ok:
        FAILS.append(what)


def load_runner():
    """tests/run_all.py as a module. Its module level is imports and constants only."""
    spec = importlib.util.spec_from_file_location("tinycmdr_run_all", RUN_ALL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha12(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def gone(pid, seconds=10.0):
    """True once `pid` is no longer signalable (killed and reaped by init)."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        time.sleep(0.1)
    return False


def main():
    mod = load_runner()

    # (d) --exclude drops a suite and SAYS so. The install surface's Windows job uses it for the
    # suites that are red there (named in STATUS.json and kept in step by tests/test_contracts.py),
    # so the mechanism itself is graded: it must drop exactly what it is told, keep everything
    # else, and print the exclusion instead of shrinking the sweep quietly.
    def listing(*args):
        proc = subprocess.run([sys.executable, str(RUN_ALL), "--list", *args],
                              capture_output=True, text=True, cwd=str(BASE))
        rows = proc.stdout.splitlines()
        return (proc.returncode, [r for r in rows if r.startswith("tests/")],
                [r[len("excluded: "):] for r in rows if r.startswith("excluded: ")])

    rc0, whole, _ = listing()
    check("--list names every suite and nothing is excluded by default",
          rc0 == 0 and len(whole) > 50 and not listing()[2], len(whole))
    rc1, reduced, dropped = listing("--exclude", "tests/test_tui.py")
    check("--exclude drops exactly the named suite, and says which",
          rc1 == 0 and dropped == ["tests/test_tui.py"]
          and "tests/test_tui.py" not in reduced and len(reduced) == len(whole) - 1,
          (dropped, len(reduced), len(whole)))
    rc2, globbed, dropped2 = listing("--exclude", "tests/test_st*py")
    check("a glob drops every match, each one named",
          rc2 == 0 and len(dropped2) > 1 and all(d.startswith("tests/test_st") for d in dropped2)
          and len(globbed) == len(whole) - len(dropped2),
          (dropped2, len(globbed), len(whole)))

    # (a) no env pick: the tree's own build, with its digest.
    saved = os.environ.pop("TINYCMDR_SRC", None)
    try:
        path, digest = mod.graded_source()
        check("unset TINYCMDR_SRC grades the tree's own tinycmdr.py",
              path == BASE / "tinycmdr.py" and digest == sha12(BASE / "tinycmdr.py"),
              "%s (%s)" % (path, digest))

        # (b) an explicit pick is the file that gets graded, and its digest is that file's.
        with tempfile.TemporaryDirectory(prefix="tinycmdr-src-") as tmp:
            pick = Path(tmp) / "pre-fix.py"
            pick.write_text("# a pre-fix build\n", encoding="utf-8")
            os.environ["TINYCMDR_SRC"] = str(pick)
            got_path, got_digest = mod.graded_source()
            check("an explicit TINYCMDR_SRC is the graded source",
                  got_path == pick and got_digest == sha12(pick),
                  "%s (%s)" % (got_path, got_digest))
            check("its digest is not the tree's",
                  got_digest != sha12(BASE / "tinycmdr.py"))
            env = mod.child_env(BASE / "tests" / "test_scrub.py", Path(tmp))
            check("child_env passes TINYCMDR_SRC through",
                  env.get("TINYCMDR_SRC") == str(pick), repr(env.get("TINYCMDR_SRC")))
    finally:
        os.environ.pop("TINYCMDR_SRC", None)
        if saved is not None:
            os.environ["TINYCMDR_SRC"] = saved

    # (c) end to end: a pick that names no file is a red run, not a quiet grading of the
    # tree's own build (which is what the popped variable produced).
    env = dict(os.environ)
    env["TINYCMDR_SRC"] = str(BASE / "tests" / "no-such-build.py")
    done = subprocess.run([sys.executable, str(RUN_ALL), "--select",
                           "tests/test_envelope.py"], cwd=str(BASE), env=env,
                          capture_output=True, text=True, timeout=120)
    check("a TINYCMDR_SRC that names no file is a red run", done.returncode != 0,
          "exit %d: %s" % (done.returncode, (done.stdout + done.stderr).strip()[-200:]))
    check("it says nothing could be graded",
          "nothing to grade" in (done.stdout + done.stderr))

    # (d) the Windows kill is a tree kill.
    argv = mod._windows_kill_argv(4321)
    check("the Windows kill names the process tree",
          "/T" in argv and argv[-1] == "4321", " ".join(argv))

    # (e) POSIX: a helper the suite spawned dies with the suite.
    if os.name == "posix":
        grandchild_code = ("import subprocess, sys, time\n"
                           "p = subprocess.Popen([sys.executable, '-c', "
                           "'import time; time.sleep(120)'])\n"
                           "print(p.pid, flush=True)\n"
                           "time.sleep(120)\n")
        proc = subprocess.Popen([sys.executable, "-c", grandchild_code],
                                stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                text=True, start_new_session=True)
        try:
            line = proc.stdout.readline().strip()
            grandchild = int(line)
        except (ValueError, AttributeError):
            grandchild = 0
        check("the probe suite spawned a helper", grandchild > 0, line)
        if grandchild:
            mod._kill_tree(proc)
            check("_kill_tree kills the helper too", gone(grandchild))

    # (f) the envelope assertions cannot be dropped from the tree.
    check("tests/test_envelope.py is in the tree", ENVELOPE.is_file())
    done = subprocess.run([sys.executable, str(RUN_ALL), "--require-envelope"],
                          cwd=str(BASE), capture_output=True, text=True, timeout=60)
    check("the retired --require-envelope switch is gone",
          done.returncode == 2 and "unrecognized arguments" in done.stderr,
          "exit %d: %s" % (done.returncode, done.stderr.strip()[-160:]))

    if FAILS:
        print("\n%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("\nall runner checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
