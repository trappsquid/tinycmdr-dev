#!/usr/bin/env python3
"""The gate: run every tests/test_*.py, one process each, and answer with one exit code.

The suites in this directory are standalone scripts - each one a module-level
check(name, cond, detail), a main(), a non-zero exit. What the tree never had was
something that ran them all and told the truth about the result, so "45/45 green"
in the changelog was a hand-run with no exit code behind it and pytest, which three
suites advertised, collected nothing but the printouts their check() left behind
(a failing check was a line of text, not a failed test). This is that one command.

    python tests/run_all.py                  # every suite; 0 only if every one graded
    python tests/run_all.py --select 'tests/test_setup*.py'
    python tests/run_all.py --allow-skips    # the developer case, never CI
    python tests/run_all.py --verbose        # stream each suite's own output

Per suite: a fresh subprocess (`sys.executable`, cwd = repo root), its stdout and
stderr captured, a wall-clock timeout, its own process group so a suite that spawns
a helper (a stub server, a child) dies with it. Nothing is imported from the suites
into this process, so one suite cannot poison the next.

Exit code: 0 only when every discovered suite PASSed (or SKIPped under --allow-skips);
1 when anything FAILed, TIMEOUTed, was killed, or when nothing was discovered at all.
The per-suite skip convention is exit 77 (see SKIP_EXIT below) - the suite saying it
could not grade its subject on this host - which this runner prints as SKIP and counts
as red.

The envelope hook (static overhead <= budget, payload <= window, prefix reuse
>= 90 %, every must_gate verb gated) lives in tests/test_envelope.py and runs like any
other suite. It is not optional any more: a run that cannot find that file in the tree
exits red, so the assertions cannot be dropped by deleting them.

Which build the suites grade is printed in the header, and an explicitly-set
TINYCMDR_SRC is honoured rather than dropped: that variable is how a fix is falsified
against the pre-fix build, and swallowing it made such a run report green for a file the
caller had not asked about.
"""
import argparse
import fnmatch
import hashlib
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
DEFAULT_SELECT = "tests/test_*.py"
DEFAULT_TIMEOUT = 300.0
# Per-suite wall-clock overrides: a suite that legitimately needs longer than the
# default 300s. None do today.
SLOW_SUITES = {}


# A suite that cannot grade its subject here (a missing dependency, a build with no
# chat lane) exits this, never 0. Three suites used to print a skip line and return 0,
# so CI would have called an ungraded run green - and did.
SKIP_EXIT = 77

# The envelope assertions live in their own suite (see module docstring). It is discovered
# like any other suite; the constant only exists so a run can say the file is MISSING, which
# is a red gate rather than a quiet reduction of what the gate covers.
ENVELOPE_SUITE = "tests/test_envelope.py"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def discover(patterns):
    """Every tests/test_*.py matching one of the --select globs, sorted by path."""
    found = []
    for path in sorted(TESTS.glob("test_*.py")):
        rel = path.relative_to(REPO).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in patterns):
            found.append(path)
    return found


def _status_path(line):
    """The path part of a `git status --porcelain` line, unquoted."""
    path = line[3:]
    if path.startswith('"') and path.endswith('"'):
        try:
            return bytes(path[1:-1], "utf-8").decode("unicode_escape")
        except Exception:
            return path[1:-1]
    return path


def tree_state():
    """Tracked status plus ignored/untracked files, as a set of lines.

    Used to REPORT the leak rather than to fail on it: a live bot in the checkout rewrites its
    own tinycmdr.log and sessions/ every minute (see live_instance_here), so a red gate here
    would name innocent suites. Measured 2026-10-07: a full sweep leaves the tree alone - every
    suite writes in its own temp dir - so a line below is a regression in the suite named beside
    it. A tree with no `.git`, or no development tooling beside it, simply has nothing to
    fingerprint and reports nothing.

    Ignored/untracked entries also carry a size+mtime fingerprint, because `git status`
    alone cannot see a file that is REWRITTEN without changing its status: an ignored
    tinycmdr.log or state.json looks identical before and after a suite
    appended to it, and that is exactly how those writes stayed invisible.
    """
    try:
        out = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain",
                              "--ignored=matching", "--untracked-files=all"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    lines = set(out.stdout.splitlines())
    for line in list(lines):
        # IGNORED entries only: a rewritten ignored file is what `git status` cannot show
        # (tinycmdr.log, tools-provenance.json all live in the
        # checkout unnoticed). An untracked file that is rewritten is somebody editing a
        # working file - the authors' notes/, a new suite under tests/ - and reporting that
        # as a leak made the report point at the run instead of at a suite.
        if line[:2] != "!!":
            continue
        try:
            st = (REPO / _status_path(line)).stat()
        except OSError:
            continue
        lines.add("\u007e %s size=%d mtime=%d" % (_status_path(line), st.st_size,
                                                 st.st_mtime_ns))
    return lines


def live_instance_here():
    """True/False/None: does a LIVE bot hold this checkout's single-instance lock?

    A probe, not a claim: take the lock and give it straight back, exactly as
    `tinycmdr status` answers the question. The target mirrors the harness's own
    _lock_target() contract - on POSIX the INSTALL FOLDER itself is flocked, because a lock
    FILE is defeated by `rm`; on Windows it is tinycmdr.lock beside it - and it
    is mirrored rather than imported, because this runner imports nothing from the tree it
    grades.

    Why the leak report needs it: tree_state() fingerprints ignored files, and a bot
    running in this checkout rewrites tinycmdr.log and sessions/ every minute by
    itself. Measured 2026-09-27: a run with the live bot up reported "6 path(s),
    written by 3 suite(s)" and every one of them was the bot's own write - the leak list
    pointed at innocent suites, and a real leak could hide in that noise.
    """
    try:
        if os.name == "nt":
            target = REPO / "tinycmdr.lock"
            if not target.exists():
                return False
            fh = open(target, "a+b")
        else:
            fh = os.open(str(REPO), os.O_RDONLY)
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        import fcntl
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    except Exception:                                            # noqa: BLE001
        return None
    finally:
        try:
            if isinstance(fh, int):
                os.close(fh)
            else:
                fh.close()
        except Exception:                                        # noqa: BLE001
            pass


def _leak_path(line):
    """A status/fingerprint line as a path a reader can act on."""
    if line.startswith("\u007e "):
        return line[2:].split(" size=")[0]
    return _status_path(line)


def graded_source():
    """The build these suites actually grade, plus its sha256 ("" if it is not there).

    Every suite that imports the app picks its file with
    `BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")` - that is the handle a fix is
    falsified through against the pre-fix build. This runner used to pop TINYCMDR_SRC out
    of the child environment as "a stale pick from the caller's shell", so
    `TINYCMDR_SRC=… python tests/run_all.py` reported green for the checkout's own
    tinycmdr.py while the caller read it as a verdict on the build they had pointed at:
    the answer came back "the suite passes", which reads as "the test does not reproduce
    the bug" rather than "you handed it the wrong file".

    Honouring the pick is safe only because it is VISIBLE, so the header names the path
    and its digest.
    """
    pick = os.environ.get("TINYCMDR_SRC") or ""
    path = Path(pick) if pick else REPO / "tinycmdr.py"
    if not path.is_absolute():
        path = REPO / path
    try:
        return path, hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return path, ""


def child_env(path, logdir):
    """The environment a suite runs in. The no-browser guard lives here, not per suite."""
    env = dict(os.environ)
    env["TINYCMDR_NO_BROWSER"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"    # no __pycache__ in the checkout
    env.pop("TINYCMDR_TEST_APP", None)      # a stale pick from the caller's shell
    # TINYCMDR_SRC is NOT popped: it is how a caller points the gate at another build, and
    # main() prints the file it resolved so a wrong pick is visible in every report.
    # The app attaches its rotating log handler to <repo>/tinycmdr.log at import, before any
    # suite code runs, so this is the runner's job (the app honours the override for exactly
    # this reason). Without it, every suite that logs appends into the checkout and git
    # status cannot even show it: the file is ignored, so it stayed invisible.
    env["TINYCMDR_LOG_FILE"] = str(logdir / (path.stem + ".log"))
    # A suite that imports the app must not CREATE host state in the checkout. The app
    # materializes a missing theme.toml/soul.md from its shipped defaults at import, which
    # is right on a real box and wrong here: since both stopped being tracked (a clone has
    # neither), test_checkin's import wrote them into the tree and the leak report named
    # it. The materialization suite pops this because that behaviour is what it grades.
    env["TINYCMDR_NO_MATERIALIZE"] = "1"
    # A suite that starts the web lane must never open a real browser tab: a day of gate
    # runs on a Mac measured ~60 of them in the operator's browser. Per-suite guards
    # existed; the runner owning it means a suite added later cannot leak one.
    return env


def run_one(path, timeout, logdir, verbose):
    """Run one suite in its own process group; return (status, seconds, detail)."""
    rel = path.relative_to(REPO).as_posix()
    out_path = logdir / (path.name + ".out")
    err_path = logdir / (path.name + ".err")
    env = child_env(path, logdir)
    kwargs = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    started = time.time()
    with open(out_path, "wb") as out_fh, open(err_path, "wb") as err_fh:
        proc = subprocess.Popen(
            [sys.executable, str(path)], cwd=str(REPO), env=env,
            stdout=out_fh, stderr=err_fh, stdin=subprocess.DEVNULL, **kwargs)
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
    seconds = time.time() - started

    stdout = out_path.read_text(encoding="utf-8", errors="replace")
    stderr = err_path.read_text(encoding="utf-8", errors="replace")
    if verbose:
        sys.stdout.write(stdout)
        sys.stderr.write(stderr)

    if timed_out:
        return FAIL, seconds, "timeout after %.0fs (killed; see %s)" % (timeout, err_path)
    code = proc.returncode
    if code == 0:
        return PASS, seconds, ""
    if code == SKIP_EXIT:
        return SKIP, seconds, "suite reported it cannot grade here (exit 77)"
    if code is not None and code < 0:
        return FAIL, seconds, "killed by signal %d" % (-code)
    detail = _reason(stdout, stderr)
    if not _check_tail(stdout):
        # The suite never reached its own summary, so the report says what that means rather
        # than leaving a reader to assume the whole file graded (test_verbs.py
        # aborted mid-run and every check after it vanished without a word).
        detail += "  [died before its own summary: no count line]"
    return FAIL, seconds, detail


def _windows_kill_argv(pid):
    """Windows tree kill: `taskkill /T` walks the child's own children too.

    CREATE_NEW_PROCESS_GROUP - what run_one spawns with - only decides where Ctrl+Break
    is routed; it does not make a grandchild die with its parent. So on Windows a
    timed-out suite that had spawned a helper left it holding a loopback port, and the
    NEXT suite to bind that port went red with a bind error naming the wrong suite.
    /T kills the tree, /F makes it a kill rather than a request.
    """
    return ["taskkill", "/T", "/F", "/PID", str(pid)]


def _kill_tree(proc):
    """Kill the suite and anything it spawned (a child process, a stub server).

    POSIX: one killpg on the session run_one started. Windows: taskkill /T, because
    proc.kill() reaches the direct child only (see _windows_kill_argv) and the runner's
    own docstring promises the helper dies with its suite.
    """
    if os.name != "posix":
        try:
            subprocess.run(_windows_kill_argv(proc.pid), capture_output=True, timeout=30)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def _looks_failed(line):
    return (line.startswith("FAIL") or "FAILED" in line or "Error:" in line
            or line.startswith("Error") or "Traceback" in line
            or line.startswith("AssertionError") or line.startswith("Refusing")
            or ".py is missing or unreadable" in line)


def _check_tail(stdout):
    """The suite's own summary line, when it prints one.

    A suite's last word is its summary, and this tree spells it five ways - so the rule is
    "does the suite report on itself", not "did it use the spelling I expected". Only the
    first shape was recognised, and every other red was reported as "[died before its own
    summary: no count line]", which tells the reader the suite aborted mid-run and left
    checks ungraded. Measured 2026-09-30: test_plan's date-rot red (CI run 36778872020) and
    test_measured_doc's stale-numbers red both read exactly that way, and the second cost a
    detour into a crash that had not happened.

    The spellings, and who prints each:
      "N passed, M failed[, K skipped]"  most suites
      "N check(s) failed[: names]"       the check()-only suites (22 of them)
      "N failed: names"                  test_cross_process, test_profiles
      "N FAILED: names" / "FAILED: N"    test_measured_doc, test_installer_parity,
                                         test_llama_extensions, test_shim, test_atlas
    A traceback, or a run that stops after its FAIL lines, still matches nothing.
    """
    for line in reversed(stdout.splitlines()):
        m = re.match(r"^\s*(\d+) passed, (\d+) failed(?:, (\d+) skipped)?\s*$", line)
        if m:
            skipped = ", %s skipped" % m.group(3) if m.group(3) else ""
            return "%s passed, %s failed%s" % (m.group(1), m.group(2), skipped)
        # The colon-less forms carry the failed names inline, so only the head is matched.
        m = re.match(r"^\s*(\d+) check\(s?\) failed\b", line)
        if m:
            return "%s check(s) failed" % m.group(1)
        m = re.match(r"^\s*(\d+) failed\b", line)
        if m:
            return "%s failed" % m.group(1)
        m = re.match(r"^\s*(?:(\d+) +)?FAILED\b", line)
        if m:
            return "%sFAILED" % (m.group(1) + " " if m.group(1) else "")
        if re.match(r"^\s*failed:\s*\S", line):
            return "failed"
    return ""


def _reason(stdout, stderr):
    """One line that says why a suite went red, from its own output.

    The suites log to stderr while they run, so stderr's first line is usually a
    WARNING, not the failure; stderr's LAST line is the answer only when a traceback
    ended the run. Failing checks print "FAIL ..." on stdout, so that is the fallback.
    """
    err = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    out = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    reason = ""
    if "Traceback" in stderr:
        reason = err[-1] if err else "traceback"
    if not reason:
        reason = next((ln for ln in err if _looks_failed(ln)), "")
    if not reason:
        reason = next((ln for ln in out if _looks_failed(ln)), "")
    if not reason:
        reason = (err[-1] if err else (out[-1] if out else "no output"))
    tail = _check_tail(stdout)
    return ("%s  [%s]" % (reason[:170], tail)) if tail else reason[:200]


def main():
    if sys.version_info < (3, 10):
        sys.exit("run_all.py needs python 3.10+ (this is %d.%d); the suites under it "
                 "use Path.write_text(newline=) and friends"
                 % sys.version_info[:2])

    ap = argparse.ArgumentParser(description="run every tests/test_*.py and say so")
    ap.add_argument("--select", action="append", metavar="GLOB",
                    help="suite glob relative to the repo root (repeatable; "
                         "default %s)" % DEFAULT_SELECT)
    ap.add_argument("--exclude", action="append", metavar="GLOB",
                    help="drop suites matching this glob (repeatable) and SAY so in the "
                         "header. The product repo's Windows job uses it for the suites that "
                         "are red on that platform and are named in STATUS.json's "
                         "windows-ci-tier2 item; tests/test_contracts.py fails when the two "
                         "lists disagree, so an exclusion cannot be silent or permanent.")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SEC",
                    help="per-suite wall clock (default %g)" % DEFAULT_TIMEOUT)
    ap.add_argument("--allow-skips", action="store_true",
                    help="a SKIP suite does not make the run red (developer use)")
    ap.add_argument("--verbose", action="store_true",
                    help="stream every suite's output instead of folding it into logs")
    ap.add_argument("--list", action="store_true", help="print what would run, then exit")
    args = ap.parse_args()

    patterns = args.select or [DEFAULT_SELECT]
    suites = discover(patterns)
    dropped = []
    if args.exclude:
        def _matches(path):
            rel = path.relative_to(REPO).as_posix()
            return any(fnmatch.fnmatch(rel, pat) for pat in args.exclude)
        dropped = [p for p in suites if _matches(p)]
        suites = [p for p in suites if p not in dropped]
    if args.list:
        for path in suites:
            print(path.relative_to(REPO).as_posix())
        for path in dropped:
            print("excluded: %s" % path.relative_to(REPO).as_posix())
        return 0 if suites else 1
    if not suites:
        sys.exit("no suites match %s - a gate that discovers nothing is a red run"
                 % ", ".join(patterns))

    # The envelope suite is not a future hook any more: it is in the tree, discover() picks
    # it up like any other suite, and a tree that has LOST it is a red gate rather than a
    # quiet decrease in what the gate covers.
    if not (REPO / ENVELOPE_SUITE).is_file():
        sys.exit("%s is not in the tree - the envelope assertions must never be dropped "
                 "from what the gate covers" % ENVELOPE_SUITE)

    src, digest = graded_source()
    if not digest:
        sys.exit("nothing to grade: %s does not exist (an explicitly-set TINYCMDR_SRC "
                 "names no file - refusing to report a green gate for a build that is "
                 "not there)" % src)
    shown = src
    try:
        shown = src.relative_to(REPO)
    except ValueError:
        pass

    logdir = Path(tempfile.mkdtemp(prefix="tinycmdr-runall-"))
    print("running %d suite(s) under %s (timeout %gs, logs %s)\n"
          % (len(suites), sys.executable, args.timeout, logdir))
    print("grading %s (sha256 %s)\n" % (shown, digest))
    if dropped:
        # Named, never silent: an exclusion the run does not state is a quieter gate, and the
        # contract that keeps this list honest is tests/test_contracts.py against STATUS.json.
        print("excluded %d suite(s): %s\n"
              % (len(dropped), ", ".join(p.relative_to(REPO).as_posix() for p in dropped)))

    results = []
    leaks = []          # (suite, [paths it wrote into the checkout])
    started = time.time()
    for path in suites:
        rel = path.relative_to(REPO).as_posix()
        sys.stdout.write("%-40s ... " % rel)
        sys.stdout.flush()
        before = tree_state()
        status, seconds, detail = run_one(path, SLOW_SUITES.get(rel, args.timeout),
                                             logdir, args.verbose)
        after = tree_state()
        results.append((rel, status, seconds, detail))
        if before is not None and after is not None:
            wrote = sorted({_leak_path(ln) for ln in (after - before)})
            if wrote:
                leaks.append((rel, wrote))
        print("%-5s %6.1fs%s" % (status, seconds, "  " + detail if detail else ""))

    elapsed = time.time() - started
    reds = [r for r in results if r[1] in (FAIL, SKIP)]
    failed = [r for r in results if r[1] == FAIL]
    skipped = [r for r in results if r[1] == SKIP]

    print("\n%d passed, %d failed, %d skipped in %.1fs"
          % (len(results) - len(reds), len(failed), len(skipped), elapsed))

    if reds:
        print("\nwhat went red:")
        for rel, status, _s, detail in reds:
            print("  %-5s %-40s %s" % (status, rel, detail))

    # Second half: a suite grades the build, not the checkout it runs from, so anything
    # a suite writes into the tree is a defect in the SUITE (its own temp dir, or a staged
    # copy, is where that belongs). Measured here rather than enforced: the list below is
    # what the batch that owns each suite has to close, and this runner still exits 0 on a
    # green run with leaks in it.
    if leaks:
        # Grouped by WHAT was written: one path (an ignored log) is
        # usually written by many suites, and a reader needs the path first, the culprits
        # second.
        by_path = {}
        for rel, paths in leaks:
            for one in paths:
                by_path.setdefault(one, []).append(rel)
        print("\nrepo-tree writes during the run: %d path(s), written by %d suite(s) "
              "- each suite must own its own temp dir"
              % (sum(len(v) for v in by_path.values()), len(leaks)))
        if live_instance_here():
            print("  NOTE: a live bot is running in this checkout. It rewrites "
                  "tinycmdr.log and sessions/ itself, so the suite "
                  "names below are NOT reliable - stop the bot (or grade a copy of the "
                  "tree) to read this as suite isolation.")
        for one in sorted(by_path):
            who = sorted(by_path[one])
            print("  %-38s %2d suite(s): %s"
                  % (one, len(who), ", ".join(who[:4]) + (", ..." if len(who) > 4 else "")))
    else:
        print("\nrepo-tree writes during the run: none - every suite stayed in its "
              "own temp dir")

    if (REPO / ENVELOPE_SUITE) not in suites:
        print("\nnote: %s is in the tree but not in this run's --select - a full run "
              "includes it" % ENVELOPE_SUITE)
    print("logs: %s" % logdir)

    if failed or (skipped and not args.allow_skips):
        return 1
    if skipped:
        print("\nskips are allowed by --allow-skips; CI never passes it")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
