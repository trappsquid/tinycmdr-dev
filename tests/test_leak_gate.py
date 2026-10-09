"""The private-string gate runs in the gate, not only on a hand-typed command.

maintenance/leak-gate.py is the one check that keeps a host name, a LAN address, a token
or a private record out of the public repository, and it ran from nowhere automated: not
CI, not a suite, and the pre-push hook that calls it is installed by hand (so it was not
installed in the checkout that was audited). A leak that reaches main is a problem in
git history, where the only repairs are a rewrite or a permanent exception.

This suite is the automated call site. It grades the tree with the strongest rules this
checkout has - maintenance/private_rules.py on the host that owns the fleet, the shipped
example on a clone - and it proves the gate still REPORTS by running it against a planted
rule that must match.

    python tests/test_leak_gate.py

Falsification: before this landed, `grep -rn leak-gate tests/*.py` matched only a stub
file written by another suite, and no suite executed the script, so this suite is the
only runner-side check of the invariant.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
GATE = BASE / "maintenance" / "leak-gate.py"
REAL_RULES = BASE / "maintenance" / "private_rules.py"
EXAMPLE_RULES = BASE / "maintenance" / "private_rules.example.py"
FAILS = []


def check(what, ok, detail=""):
    # `(detail,)`: a check that hands a tuple as its detail (they all do) crashed the
    # SUITE with "not all arguments converted during string formatting" instead of
    # printing the FAIL it had just decided - measured 2026-10-09 on the Windows
    # nightly, where the crash hid the reason the hook check went red.
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
    if not ok:
        FAILS.append(what)


def run(rules, mode="--tree"):
    env = dict(os.environ)
    env["TINYCMDR_LEAK_RULES"] = str(rules)
    return subprocess.run([sys.executable, str(GATE), mode], cwd=str(BASE),
                          env=env, capture_output=True, text=True)


def _bash():
    """A bash that can actually run the repo's scripts.

    On Windows `bash` on PATH is often System32's WSL stub, which has no distribution
    installed: it exits 1 with a UTF-16 "Windows Subsystem for Linux has no installed
    distributions" notice and installs nothing (measured 2026-10-09 in the windows job,
    where the hook check had been grading the stub). Git for Windows ships the real one;
    probe the candidates and take the first that answers.
    """
    cands = ["bash"]
    if os.name == "nt":
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LOCALAPPDATA")):
            if root:
                cands.append(str(Path(root) / "Git" / "bin" / "bash.exe"))
                cands.append(str(Path(root) / "Programs" / "Git" / "bin" / "bash.exe"))
    for cand in cands:
        try:
            probe = subprocess.run([cand, "-c", "exit 0"], capture_output=True, timeout=60)
        except OSError:
            continue
        if probe.returncode == 0:
            return cand
    return cands[0]


def main():
    # Every mode of the gate reads the tree through git, so a tree without a .git has
    # nothing to grade: this suite would report on an empty set either way. exit 77 is the
    # project's "graded nothing" (run 23, A-2026-10-07-66 - in an rsync export it used to
    # report a WRONG verdict instead, "a planted rule makes leak-gate exit 1 <- exit 0").
    probe = subprocess.run(["git", "-C", str(BASE), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        print("skip: %s is not a git work tree - the gate scans through git, so there is "
              "nothing here to grade" % BASE)
        return 77
    rules = REAL_RULES if REAL_RULES.exists() else EXAMPLE_RULES
    check("a rules file exists to grade with", rules.exists(), str(rules))
    if not rules.exists():
        print("\n%d failed: %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("rules: %s" % rules.name)

    # (a) THE invariant: the tree carries nothing the rules forbid.
    done = run(rules)
    check("leak-gate --tree exits 0", done.returncode == 0,
          "exit %d: %s" % (done.returncode,
                           (done.stdout.strip() or done.stderr.strip())))

    # (b) The gate is not a program that always says clean: a rule that DOES match a
    # tracked string must be reported, with the file that carries it. Without this, (a)
    # would pass just as well against a gate that had stopped scanning.
    with tempfile.TemporaryDirectory(prefix="tinycmdr-leak-") as tmp:
        planted = Path(tmp) / "planted_rules.py"
        planted.write_text(
            "PUBLIC_RULES = ((r'\\btinycmdr\\b', 'a planted rule'),)\n"
            "PUBLIC_FORBIDDEN = ()\n", encoding="utf-8")
        done = run(planted)
        check("a planted rule makes leak-gate exit 1", done.returncode == 1,
              "exit %d: %s" % (done.returncode, done.stdout.strip()[:200]))
        check("the report names the rule", "a planted rule" in done.stdout,
              done.stdout.strip()[:200])

    # (c) Missing rules must not read as clean. The gate's own guard: without patterns it
    # refuses rather than reporting zero hits.
    with tempfile.TemporaryDirectory(prefix="tinycmdr-leak-") as tmp:
        empty = Path(tmp) / "empty_rules.py"
        empty.write_text("PUBLIC_RULES = ()\nPUBLIC_FORBIDDEN = ()\n", encoding="utf-8")
        done = run(empty)
        check("an empty rule set is refused, not reported clean",
              done.returncode != 0 and "clean" not in done.stdout,
              "exit %d: %s" % (done.returncode, (done.stdout + done.stderr).strip()[:200]))

    # ---- (d) --range: the mode CI and release.sh use, over what a push ADDS ------------
    # It is graded inside the throwaway repo of section (e), on purpose: an earlier version of
    # this section named a commit of THIS repo's history, which made the suite a thing that
    # only passes where that history exists - the defect that keeps a check from being carried
    # to a clean repo. A bare rev means that ONE commit (`rev^!`), not its whole lineage.
    done = subprocess.run([sys.executable, str(GATE), "--range"], cwd=str(BASE),
                          env={**os.environ, "TINYCMDR_LEAK_RULES": str(rules)},
                          capture_output=True, text=True)
    check("--range with no range is refused, not reported clean",
          done.returncode == 2 and "refusing to report clean" in done.stdout,
          (done.returncode, done.stdout.strip()[:160]))

    # ---- (e) the push path itself: an armed clone REFUSES a private string --------------
    # This is the permanent half: the gate is only as good as its arming, and the hook is not
    # tracked by git. So the suite grades a real push against a real bare remote.
    def _run(cmd, cwd, env=None):
        return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)

    with tempfile.TemporaryDirectory(prefix="tinycmdr-leakhook-") as tmp:
        tmpd = Path(tmp)
        origin, work = tmpd / "origin.git", tmpd / "work"
        planted_hook_rules = tmpd / "hook_rules.py"
        planted_hook_rules.write_text(
            "PUBLIC_RULES = ((r'BANANA-TOKEN-9', 'a planted secret'),)\n"
            "PUBLIC_FORBIDDEN = ()\n", encoding="utf-8")
        rules_env = {**os.environ, "TINYCMDR_LEAK_RULES": str(planted_hook_rules)}
        _run(["git", "init", "--bare", "-q", str(origin)], tmpd)
        _run(["git", "init", "-q", "-b", "main", str(work)], tmpd)
        _run(["git", "config", "user.email", "suite@example.invalid"], work)
        _run(["git", "config", "user.name", "suite"], work)
        (work / "maintenance").mkdir()
        shutil.copy2(GATE, work / "maintenance" / "leak-gate.py")
        (work / "README.md").write_text("clean\n", encoding="utf-8")
        _run(["git", "add", "-A"], work)
        _run(["git", "commit", "-qm", "first: nothing planted"], work)
        _run(["git", "remote", "add", "origin", str(origin)], work)
        first = _run(["git", "push", "-q", "-u", "origin", "main"], work, rules_env)
        check("a clean push lands before anything is armed", first.returncode == 0,
              first.stderr.strip()[:160])

        # The pattern list can also arrive as SOURCE TEXT in the environment - the door a CI
        # job uses to arm a repository secret without writing it to a runner's disk. This
        # clone has no maintenance/private_rules.py, so the environment is what it grades with.
        env_door = {k: v for k, v in os.environ.items() if k != "TINYCMDR_LEAK_RULES"}
        env_door["TINYCMDR_LEAK_PATTERNS"] = (
            "PUBLIC_RULES = ((r'\\btinycmdr\\b', 'the tree marker'),)\nPUBLIC_FORBIDDEN = ()\n")
        env_done = _run([sys.executable, str(work / "maintenance" / "leak-gate.py"), "--tree"],
                        work, env_door)
        check("the list can arrive through TINYCMDR_LEAK_PATTERNS (CI's secret door)",
              env_done.returncode == 1 and "the tree marker" in env_done.stdout,
              (env_done.returncode, env_done.stdout[:160]))
        no_rules = {k: v for k, v in os.environ.items()
                    if k not in ("TINYCMDR_LEAK_RULES", "TINYCMDR_LEAK_PATTERNS")}
        none_done = _run([sys.executable, str(work / "maintenance" / "leak-gate.py"), "--tree"],
                         work, no_rules)
        check("...and no list at all is a refusal, never a clean report",
              none_done.returncode != 0
              and "no pattern list" in (none_done.stdout + none_done.stderr),
              (none_done.returncode, (none_done.stdout + none_done.stderr)[:160]))

        bash = _bash()
        installed = _run([bash, str(BASE / "maintenance" / "install-hooks.sh"),
                          "--leak-only"], work,
                         {**os.environ, "TINYCMDR_HOOK_ROOT": str(work)})
        hook = work / ".git" / "hooks" / "pre-push"
        # The execute bit is a POSIX claim: git runs a hook by name on Windows and
        # os.access(X_OK) there asks PATHEXT, so a no-extension `pre-push` would read as
        # not executable.
        check("install-hooks.sh arms the clone", installed.returncode == 0 and hook.is_file()
              and (os.name == "nt" or os.access(str(hook), os.X_OK)),
              (installed.returncode, installed.stdout[:120]))
        before_bytes = hook.read_bytes() if hook.is_file() else b""
        again = _run([bash, str(BASE / "maintenance" / "install-hooks.sh"), "--leak-only"],
                     work, {**os.environ, "TINYCMDR_HOOK_ROOT": str(work)})
        check("...and is idempotent",
              again.returncode == 0 and hook.is_file() and hook.read_bytes() == before_bytes,
              (again.returncode, hook.is_file()))

        # 1. the token in a commit MESSAGE - the shape that actually reached the public repo
        (work / "README.md").write_text("clean\nmore\n", encoding="utf-8")
        _run(["git", "commit", "-qam", "fix: rotate BANANA-TOKEN-9 in the installer"], work)
        bad = _run(["git", "push", "-q", "origin", "main"], work, rules_env)
        check("an armed clone REFUSES a push whose MESSAGE carries a private string",
              bad.returncode != 0 and "BANANA-TOKEN-9" in (bad.stdout + bad.stderr),
              (bad.returncode, (bad.stdout + bad.stderr).strip()[:200]))
        remote_tip = _run(["git", "--git-dir", str(origin), "rev-parse", "main"], tmpd).stdout.strip()
        local_first = _run(["git", "rev-parse", "HEAD~1"], work).stdout.strip()
        check("...and the remote did not move", remote_tip == local_first,
              (remote_tip, local_first))

        # 2. the same string in a FILE, and the push is still refused
        _run(["git", "commit", "-q", "--amend", "-m", "fix: tidy the installer"], work)
        (work / "NOTES.md").write_text("token: BANANA-TOKEN-9\n", encoding="utf-8")
        _run(["git", "add", "NOTES.md"], work)
        _run(["git", "commit", "-qm", "chore: notes"], work)
        bad2 = _run(["git", "push", "-q", "origin", "main"], work, rules_env)
        check("...and one whose FILES carry it", bad2.returncode != 0, 
              (bad2.returncode, (bad2.stdout + bad2.stderr).strip()[:200]))

        # 3. clean work still pushes: the hook is a gate, not a wall
        _run(["git", "reset", "-q", "--hard", "HEAD~1"], work)
        _run(["git", "commit", "-q", "--allow-empty", "-m", "chore: clean again"], work)
        good = _run(["git", "push", "-q", "origin", "main"], work, rules_env)
        check("...while clean work still pushes", good.returncode == 0, good.stderr.strip()[:160])

        # 4. a foreign hook is not clobbered silently
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        refused = _run([bash, str(BASE / "maintenance" / "install-hooks.sh"), "--leak-only"],
                       work, {**os.environ, "TINYCMDR_HOOK_ROOT": str(work)})
        check("a foreign pre-push hook is refused, not overwritten",
              refused.returncode == 2 and "was not written by this script" in refused.stderr,
              (refused.returncode, refused.stderr.strip()[:160]))
        forced = _run([bash, str(BASE / "maintenance" / "install-hooks.sh"), "--leak-only",
                       "--force"], work, {**os.environ, "TINYCMDR_HOOK_ROOT": str(work)})
        check("...unless --force says so",
              forced.returncode == 0 and hook.is_file()
              and b"leak-gate" in hook.read_bytes(),
              (forced.returncode, hook.is_file()))

        # The range mode, on THIS repo's own commits: a bare rev is that one commit, its
        # message is graded, and a range that adds nothing does not re-litigate the past.
        gate = str(work / "maintenance" / "leak-gate.py")
        msg_rules = tmpd / "msg_rules.py"
        msg_rules.write_text("PUBLIC_RULES = ((r'rotate the token', 'a planted message "
                             "marker'),)\nPUBLIC_FORBIDDEN = ()\n", encoding="utf-8")
        _run(["git", "commit", "-q", "--allow-empty", "-m", "chore: rotate the token"], work)
        ranged = _run([sys.executable, gate, "--range", "HEAD^!"], work,
                      {**os.environ, "TINYCMDR_LEAK_RULES": str(msg_rules)})
        check("--range grades a commit MESSAGE it names",
              ranged.returncode == 1 and "a planted message marker" in ranged.stdout,
              (ranged.returncode, ranged.stdout[:200]))
        past = _run([sys.executable, gate, "--range", "HEAD~1^!"], work,
                    {**os.environ, "TINYCMDR_LEAK_RULES": str(msg_rules)})
        check("...and says nothing about a commit the range does not name",
              past.returncode == 0, (past.returncode, past.stdout[:160]))
        _run(["git", "reset", "-q", "--hard", "HEAD~1"], work)

    # ---- a tree with no .git must NOT read as clean ----------------------------------
    # Every mode scans through git (`ls-files`, `rev-list`, `log`), so in an rsync export the
    # gate printed "clean (N patterns)" having read no file at all - the one outcome this
    # project refuses. It now refuses instead, and this suite declares exit 77 up top when
    # the tree it is standing in has no .git (run 23, A-2026-10-07-66).
    with tempfile.TemporaryDirectory(prefix="tinycmdr-leak-nogit-") as tmp:
        clone = Path(tmp) / "maintenance"
        clone.mkdir()
        shutil.copy2(GATE, clone / "leak-gate.py")
        done = subprocess.run([sys.executable, str(clone / "leak-gate.py"), "--tree"],
                              capture_output=True, text=True,
                              env={**os.environ, "TINYCMDR_LEAK_RULES": str(rules)})
        check("a git-less tree is refused, not reported clean",
              done.returncode == 2 and "not a git work tree" in done.stdout
              and "leak-gate --tree: clean" not in done.stdout,
              "%s: %s" % (done.returncode, done.stdout.strip()[:200]))
    if FAILS:
        print("\n%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("\nall leak-gate checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
