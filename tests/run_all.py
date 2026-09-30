#!/usr/bin/env python3
"""The gate: run every tests/test_*.py, one process each, and answer with one exit code.

The suites in this directory are standalone scripts - each one a module-level
check(name, cond, detail), a main(), a non-zero exit. What the tree never had was
something that ran them all and told the truth about the result, so "45/45 green"
in the changelog was a hand-run with no exit code behind it and pytest, which three
suites advertised, collected nothing but the printouts their check() left behind
(a failing check was a line of text, not a failed test). This is that one command.

    python tests/run_all.py                  # every suite; 0 only if every one graded
    python tests/run_all.py --select 'tests/test_ledger*.py'
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

G5 hook: PHASE1-TASKS G5 (static overhead <= budget, payload <= window, prefix reuse
>= 90 %, every must_gate verb gated) is owned by the envelope change, not by this
runner. When tests/test_envelope.py lands it is discovered like any other suite and
its non-zero exit fails the gate; --require-envelope makes its absence a failure too,
for the commit that flips the switch. Until then the summary prints it as pending.
"""
import argparse
import fnmatch
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

# G5 hook (see module docstring): the envelope assertions arrive as their own suite.
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

    Used to REPORT the leak G2 closes: the suites still write tasks.json, sessions/,
    tinycmdr.log and friends into the checkout. This runner must not fail on that - it is
    a measurement for the batch that makes them hermetic.

    Ignored/untracked entries also carry a size+mtime fingerprint, because `git status`
    alone cannot see a file that is REWRITTEN without changing its status: an ignored
    tinycmdr.log, tasks.journal.jsonl or state.json looks identical before and after a suite
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
        # (tinycmdr.log, tasks.journal.jsonl, tools-provenance.json all live in the
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
    FILE is defeated by `rm` (audit D5); on Windows it is tinycmdr.lock beside it - and it
    is mirrored rather than imported, because this runner imports nothing from the tree it
    grades.

    Why the leak report needs it: tree_state() fingerprints ignored files, and a bot
    running in this checkout rewrites tinycmdr.log and sessions/ every minute by
    itself. Measured 2026-09-27: a run with the live bot up reported "6 path(s),
    written by 3 suite(s)" and every one of them was the bot's own write - the G2 list
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


def run_one(path, timeout, logdir, verbose):
    """Run one suite in its own process group; return (status, seconds, detail)."""
    rel = path.relative_to(REPO).as_posix()
    out_path = logdir / (path.name + ".out")
    err_path = logdir / (path.name + ".err")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"    # no __pycache__ in the checkout
    env.pop("TINYCMDR_TEST_APP", None)      # a stale pick from the caller's shell
    env.pop("TINYCMDR_SRC", None)
    # The app attaches its rotating log handler to <repo>/tinycmdr.log at import, before any
    # suite code runs, so this is the runner's job (the app honours the override for exactly
    # this reason). Without it, every suite that logs appends into the checkout and git
    # status cannot even show it: the file is ignored, so it stayed invisible.
    env["TINYCMDR_LOG_FILE"] = str(logdir / (path.stem + ".log"))
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
        # than leaving a reader to assume the whole file graded (BUGREPORT T1: test_verbs.py
        # aborted mid-run and every check after it vanished without a word).
        detail += "  [died before its own summary: no count line]"
    return FAIL, seconds, detail


def _kill_tree(proc):
    """Kill the suite and anything it spawned (a child process, a stub server)."""
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
      "failed: names"                    test_ledger_journal, which prints no count at all
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
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SEC",
                    help="per-suite wall clock (default %g)" % DEFAULT_TIMEOUT)
    ap.add_argument("--allow-skips", action="store_true",
                    help="a SKIP suite does not make the run red (developer use)")
    ap.add_argument("--require-envelope", action="store_true",
                    help="fail if %s is absent (G5 switch)" % ENVELOPE_SUITE)
    ap.add_argument("--verbose", action="store_true",
                    help="stream every suite's output instead of folding it into logs")
    ap.add_argument("--list", action="store_true", help="print what would run, then exit")
    args = ap.parse_args()

    patterns = args.select or [DEFAULT_SELECT]
    suites = discover(patterns)
    if args.list:
        for path in suites:
            print(path.relative_to(REPO).as_posix())
        return 0 if suites else 1
    if not suites:
        sys.exit("no suites match %s - a gate that discovers nothing is a red run"
                 % ", ".join(patterns))

    have_envelope = (REPO / ENVELOPE_SUITE).is_file()
    if args.require_envelope and not have_envelope:
        sys.exit("--require-envelope: %s is not in the tree (G5 hook not filled in)"
                 % ENVELOPE_SUITE)

    logdir = Path(tempfile.mkdtemp(prefix="tinycmdr-runall-"))
    print("running %d suite(s) under %s (timeout %gs, logs %s)\n"
          % (len(suites), sys.executable, args.timeout, logdir))

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

    # G2, second half: a suite grades the build, not the checkout it runs from, so anything
    # a suite writes into the tree is a defect in the SUITE (its own temp dir, or a staged
    # copy, is where that belongs). Measured here rather than enforced: the list below is
    # what the batch that owns each suite has to close, and this runner still exits 0 on a
    # green run with leaks in it.
    if leaks:
        # Grouped by WHAT was written: one path (an ignored log, the ledger journal) is
        # usually written by many suites, and a reader needs the path first, the culprits
        # second.
        by_path = {}
        for rel, paths in leaks:
            for one in paths:
                by_path.setdefault(one, []).append(rel)
        print("\nrepo-tree writes during the run: %d path(s), written by %d suite(s) "
              "- each suite must own its own temp dir (G2)"
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
              "own temp dir (G2)")

    print("\nG5 envelope gate: %s" % (
        "%s is in the tree and runs like any other suite" % ENVELOPE_SUITE
        if have_envelope else
        "pending - the hook is here; drop %s in and it gates" % ENVELOPE_SUITE))
    print("logs: %s" % logdir)

    if failed or (skipped and not args.allow_skips):
        return 1
    if skipped:
        print("\nskips are allowed by --allow-skips; CI never passes it")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
