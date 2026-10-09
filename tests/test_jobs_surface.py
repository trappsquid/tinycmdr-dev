"""test_jobs_surface - one merged suite (test_schedule, test_job_control).

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


def _suite_test_schedule():
    """The scheduler: what a cron job may and may not do to an unattended box.

    python tests/test_jobs_surface.py

The scheduler is the one lane with nobody watching it start, so what matters is
the properties that keep a box honest while nobody is looking: a job never
overlaps itself, the name the operator typed is the name the job has, and a job
that cannot report its answer says so instead of spending tokens on a reply
nobody reads.

It imports the build from a staged copy (a config.json beside it, the way the
installer writes one) and replaces the model call and the reporter; nothing here
reaches a model, a chat server or the clock.
"""
    import importlib.util
    import json
    import logging
    import os
    import shutil
    import sys
    import tempfile
    import threading
    import time
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")


    STAGE = Path(tempfile.mkdtemp(prefix="tinycmdr-test-stage-schedule"))
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
                 STAGE / "config.json")
    spec = importlib.util.spec_from_file_location("tinycmdr_schedule_under_test",
                                                  STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_schedule_under_test"] = fb
    spec.loader.exec_module(fb)

    PASSES, FAILS = [], []


    def check(name, cond, detail=""):
        (PASSES if cond else FAILS).append(name)
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))


    def _until(pred, secs=5.0):
        """Wait for a background thread to get somewhere, without a fixed sleep."""
        deadline = time.time() + secs
        while time.time() < deadline:
            if pred():
                return True
            time.sleep(0.02)
        return bool(pred())


    class _LogCapture(logging.Handler):
        """What the build says to its own log, so a claim to report something is graded."""

        def __init__(self):
            super().__init__()
            self.lines = []

        def emit(self, record):
            self.lines.append(record.getMessage())


    class _SlowModel:
        """drive_run, replaced: records the session key and blocks until released."""

        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.keys = []
            self.lock = threading.Lock()

        def __call__(self, key, text, rep, **kw):
            with self.lock:
                self.keys.append(key)
            self.started.set()
            self.release.wait(20)
            return "done"


    def _scheduler(name):
        sched = fb.Scheduler(STAGE / name)
        sched._stop.set()          # this suite fires jobs by hand, never by the clock
        sched.dispatcher = None    # no chat layer: the run reports nowhere
        return sched


    def _fire(sched, name, wait=True):
        t = threading.Thread(target=sched._fire,
                             args=(name, {"task": "check the disk", "channel_id": None}),
                             daemon=True)
        t.start()
        if wait:
            t.join(10)
        return t


    # ------------------------------------------------- a job never overlaps itself
    # A `* * * * *` job whose work takes minutes started one thread per tick: five
    # concurrent runs on the SAME session key (sched-<name>), so they interleaved
    # into one transcript, spent the tokens five times over, and each fire's
    # ask-door row and AGENT.model_overrides entry overwrote the others' (the first
    # to finish popped the override the rest were still running on). Measured
    # 2026-10-06 against this build with a stubbed drive_run and a 0.5s interval:
    # five fires live at once.
    slow = _SlowModel()
    saved_drive, saved_report = fb.drive_run, fb.report
    fb.drive_run = slow
    fb.report = lambda token, text: None
    sched = _scheduler("jobs-overlap.json")
    try:
        cap = _LogCapture()
        fb.log.addHandler(cap)
        first = _fire(sched, "slow-job", wait=False)
        check("the job reaches the model", slow.started.wait(10),
              "Scheduler._fire never started the run")
        overlap = _fire(sched, "slow-job", wait=False)
        overlap.join(5)
        check("a fire while the job is still running returns at once",
              not overlap.is_alive(), "the second fire is still alive")
        check("...and starts no second run of the same job",
              len(slow.keys) == 1, slow.keys)
        check("...and says so in the log, naming the job",
              any("slow-job" in line and "skip" in line.lower() for line in cap.lines),
              cap.lines)
        fb.log.removeHandler(cap)
        other = _fire(sched, "other-job", wait=False)
        check("a DIFFERENT job is not serialized behind the one still running",
              _until(lambda: "sched-other-job" in slow.keys), slow.keys)
        slow.release.set()
        first.join(10)
        other.join(10)
        _fire(sched, "slow-job")            # joins: the release is already set
        check("the fire that was skipped leaves the job free to run again",
              slow.keys.count("sched-slow-job") == 2, slow.keys)
        check("...and the in-flight bookkeeping is empty once every fire is over",
              not sched._running, sched._running)
    finally:
        slow.release.set()
        fb.drive_run, fb.report = saved_drive, saved_report

    # ---------------------------------------- the name you typed is the name it has
    # `add` sanitised the name and `remove` did not, and `add` wrote straight over a
    # job that already held the sanitised name: "check disk" and "check.disk" are one
    # job, the second add silently deleted the first, and `remove "check disk"` answered
    # with a name the operator never typed. Measured 2026-10-06.
    sched = _scheduler("jobs-names.json")
    ctx = {"channel_id": "chan-1"}
    added = sched.tool_action({"action": "add", "name": "check disk",
                               "cron": "0 7 * * *", "task": "df -h"}, ctx)
    check("a job name with a space is accepted", added.startswith("OK:"), added)
    clash = sched.tool_action({"action": "add", "name": "check.disk",
                               "cron": "30 3 * * *", "task": "tar /var"}, ctx)
    check("a second name that sanitises to the same key is refused, not written over",
          clash.startswith("ERROR:") and "already" in clash, clash)
    check("...so the job that was there survives",
          list(sched.jobs) == ["check_disk"]
          and sched.jobs["check_disk"]["task"] == "df -h", dict(sched.jobs))
    check("...and the refusal names what already runs and what to do about it",
          "check_disk" in clash and "remove" in clash.lower(), clash)
    again = sched.tool_action({"action": "add", "name": "check disk",
                               "cron": "0 7 * * *", "task": "df -h"}, ctx)
    check("restating the SAME job is not an error", again.startswith("OK:"), again)
    check("...and still one job", len(sched.jobs) == 1, dict(sched.jobs))
    removed = sched.tool_action({"action": "remove", "name": "check disk"}, ctx)
    check("the job is removed with the name the operator typed",
          removed.startswith("OK:"), removed)
    check("...and it is really gone", not sched.jobs, dict(sched.jobs))
    check("removing it twice says so, naming the stored spelling",
          sched.tool_action({"action": "remove", "name": "check disk"}, ctx)
          == "ERROR: no job named 'check_disk'.",
          sched.tool_action({"action": "remove", "name": "check disk"}, ctx))
    sched._stop.set()

    # ------------------------------------ a job with nowhere to report says so
    # Created from a terminal, the page or `--once` there is no Mattermost channel to
    # record, so the answer lands on report()'s print() - the service's log - and the
    # operator never hears from a job that runs every night.
    sched = _scheduler("jobs-nowhere.json")
    silent = sched.tool_action({"action": "add", "name": "nightly",
                                "cron": "0 7 * * *", "task": "df -h"}, {})
    check("a job with no reporting channel is still scheduled",
          silent.startswith("OK:"), silent)
    check("...and says its answer goes to the log only",
          "no channel to report to" in silent and "log" in silent, silent)
    loud = sched.tool_action({"action": "add", "name": "morning", "cron": "0 7 * * *",
                              "task": "df -h", "channel_id": "chan-1"},
                             {"channel_id": "chan-2"})
    check("a job that has a channel gets no warning", "no channel" not in loud, loud)
    sched._stop.set()
    # ------------------------------------------- only the bot fires the folder's jobs
    # Every process that imports this module builds a Scheduler with a loop of its own,
    # and the single-instance lock is taken only by the service doors. A `--cli` session
    # alive across a cron boundary used to take the job: it ran in a process where
    # SCHEDULER.dispatcher is None (the answer went to print() on a terminal, not the
    # job's channel), as a daemon thread that died when the session closed - and the bot
    # adopted the advanced `next` out of jobs.json and never ran that occurrence at all.
    # Measured 2026-10-06 with a real second process: `cli fired sched-nightly`, next
    # moved a full minute on.
    sched = _scheduler("jobs-owner.json")
    sched.jobs["nightly"] = {"cron": "* * * * *", "task": "say hi",
                             "channel_id": None, "model": None,
                             "next": time.time() + 3600}
    sched._save()
    sched._mtime = sched._disk_mtime()      # the file is exactly what memory holds
    saved_next = sched.jobs["nightly"]["next"]
    # The state a running bot reaches when the clock passes a job's `next`.
    sched.jobs["nightly"]["next"] = time.time() - 1
    fired = []
    saved_drive = fb.drive_run
    fb.drive_run = lambda key, text, rep, **kw: (fired.append(key), "done")[1]
    try:
        check("a process that does not hold the folder's instance lock fires nothing",
              sched._tick() is False and not fired, fired)
        check("...and leaves the job due, so the bot still gets it",
              sched.jobs["nightly"]["next"] <= time.time()
              and json.loads((STAGE / "jobs-owner.json").read_text())["nightly"]["next"]
              == saved_next, (sched.jobs["nightly"]["next"], saved_next))
        got = fb.acquire_single_instance_lock()
        check("the process holding the folder's instance lock is the one that fires",
              got and fb.holds_instance_lock() and sched._tick() is True,
              (got, fb.holds_instance_lock()))
        check("...and the job runs there", _until(lambda: fired == ["sched-nightly"]),
              fired)
        check("...once: the next tick has nothing due", sched._tick() is True
              and len(fired) == 1, fired)
    finally:
        fb.drive_run = saved_drive
        fb._release_lock()
        check("a process that has released the lock stops firing",
              fb.holds_instance_lock() is False, fb.holds_instance_lock())

    # ------------------------------- a save is an edit of the file, not a blind overwrite
    # A tick wrote its WHOLE in-memory dict back to jobs.json. A `--once` run - the normal
    # way a job is added from a terminal - that added a job between the tick's reload and
    # its save was overwritten by that dict and gone for good, after the operator had
    # already been answered "OK: job '...' scheduled". Measured 2026-10-07 against this
    # build: 276 of 1989 concurrent adds lost (84 of 659 tick saves clobbering the file).
    # The interleave is staged on the one seam between the tick's read and its write.
    jobs_file = STAGE / "jobs-merge.json"
    sched = _scheduler("jobs-merge.json")
    sched.jobs["nightly"] = {"cron": "* * * * *", "task": "say hi",
                             "channel_id": None, "model": None, "next": 0.0}
    sched._save()
    sched._mtime = sched._disk_mtime()          # the file is exactly what memory holds
    saved_next = fb.Scheduler.__dict__["_next_run"]
    saved_fire, saved_drive, saved_report = sched._fire, fb.drive_run, fb.report
    once = []
    def _next_run_with_once(cron, base=None):
        if not once:                            # between the tick's reload and its save
            once.append(1)
            # what the `--once` process left on disk: its own add, on top of the file
            on_disk = json.loads(jobs_file.read_text(encoding="utf-8"))
            on_disk["from_once"] = {"cron": "0 7 * * *", "task": "df -h",
                                   "channel_id": None, "model": None,
                                   "next": time.time() + 3600}
            jobs_file.write_text(json.dumps(on_disk), encoding="utf-8")
        return saved_next(cron, base)
    try:
        fb.Scheduler._next_run = staticmethod(_next_run_with_once)
        sched._fire = lambda name, job: None    # this grades the write, not the run
        fb.drive_run = lambda key, text, rep, **kw: "done"
        fb.report = lambda token, text: None
        sched.jobs["nightly"]["next"] = time.time() - 1
        got = fb.acquire_single_instance_lock()
        check("the merge is graded while the instance lock is held", got)
        sched._tick()
        merged = json.loads(jobs_file.read_text(encoding="utf-8"))
    finally:
        fb.Scheduler._next_run = saved_next
        sched._fire, fb.drive_run, fb.report = saved_fire, saved_drive, saved_report
        fb._release_lock()
        sched._stop.set()
    check("...and the other process's add landed before the save", once == [1], once)
    check("a job another process added mid-tick survives that tick's save",
          "from_once" in merged, sorted(merged))
    check("...and the tick still moved the job it saved",
          merged.get("nightly", {}).get("next", 0) > time.time(),
          merged.get("nightly"))

    # ------------------- A-2026-10-08-153: an adopt does not eat a due occurrence ---------
    # jobs.json's mtime changes whenever another process writes it (a --once `schedule
    # add`/`remove`), and _load's start-up overdue-skip then pushed a job that had come due
    # since the last tick forward before the due check - the occurrence never fired though
    # nothing was down.
    jobs_file = STAGE / "jobs-adopt.json"
    sched = _scheduler("jobs-adopt.json")
    sched.jobs["nightly"] = {"cron": "* * * * *", "task": "say hi",
                             "channel_id": None, "model": None, "next": 0.0}
    sched._save()
    sched._mtime = sched._disk_mtime()
    on_disk = json.loads(jobs_file.read_text(encoding="utf-8"))
    on_disk["nightly"]["next"] = time.time() - 30        # came due since the last tick
    time.sleep(0.01)
    jobs_file.write_text(json.dumps(on_disk), encoding="utf-8")
    sched._reload_if_changed()
    check("an adopted jobs.json keeps a due `next` (the occurrence is not skipped)",
          sched.jobs["nightly"]["next"] <= time.time(), sched.jobs["nightly"]["next"])
    sched._load()                                        # the start-up call
    check("...while the start-up load still skips missed runs",
          sched.jobs["nightly"]["next"] > time.time(), sched.jobs["nightly"]["next"])
    sched._stop.set()

    # ------------------- A-2026-10-08-154: no croniter, no firing --------------------------
    # __init__ disables the schedule and tool_action refuses, but _tick kept FIRING every job
    # whose next passed: _next_run raised ImportError, the except re-armed the job an hour
    # out, and a weekly job ran every hour while the log said scheduling was disabled.
    sched = _scheduler("jobs-nocroniter.json")
    sched.jobs["nightly"] = {"cron": "0 9 * * 1", "task": "say hi",
                             "channel_id": None, "model": None, "next": time.time() - 1}
    sched._save()
    saved_next2 = sched.jobs["nightly"]["next"]
    fired = []
    saved_ok, saved_fire = sched.ok, sched._fire
    try:
        sched.ok = False
        sched._fire = lambda name, job: fired.append(name)
        got = fb.acquire_single_instance_lock()
        check("the no-croniter tick is graded while the instance lock is held", got)
        check("with croniter missing the tick reports no fire",
              sched._tick() is False, fired)
        _until(lambda: fired or sched.jobs.get("nightly", {}).get("next") != saved_next2, 1.0)
        check("...and fires nothing, leaving `next` alone",
              fired == [] and sched.jobs.get("nightly", {}).get("next") == saved_next2,
              (fired, sched.jobs.get("nightly", {}).get("next"), saved_next2))
    finally:
        sched.ok, sched._fire = saved_ok, saved_fire
        fb._release_lock()
        sched._stop.set()

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    sys.exit(1 if FAILS else 0)


def _suite_test_job_control():
    """Background jobs: a real exit code across restarts, readiness, stdin, and a push.

The process table said "finished (code not captured)" for any job that outlived the
process that started it, had no readiness condition (start-and-poll was the only way to
know a service was up), could not answer a prompt (stdin was DEVNULL), and a job that
ended was only discoverable by asking. No broker: the wrapper and the spool are plain
files under logs/.

 additions, each graded against the pre-fix tool
(TINYCMDR_SRC=/tmp/pre-process.py makes the file under test the snapshot; the checks
below then fail):

  A-124  output leaked the log handle (5 ResourceWarnings; on Windows the log stayed
         locked until the GC ran) and A-125 echoed the wrapper's `__EXIT__` marker as
         job output, because it bypassed read_job_log();
  A-126  a non-string command was accepted at start (a dict iterated its keys, a number
         raised TypeError) and then killed `list` for good on `job['command'][:70]`;
  A-127  an unknown (or missing) action answered `ERROR: no job ''` instead of naming
         the action vocabulary;
  A-128  wait_for values reached int() and re.compile() unguarded (ValueError/re.error),
         after the job had already been spawned;
  A-129  kill reported "killed" when all it could confirm was that the wrapper died (a
         process the job detached itself is outside taskkill's/killpg's tree);
  A-130  the job table had no lock, so concurrent starts read one table and shared one
         id and one log (measured: 6 starts -> 3 rows).

    python tests/test_jobs_surface.py                     [TINYCMDR_SRC=<process.py>]
"""
    import gc
    import json
    import os
    import re
    import shutil
    import subprocess
    import sys
    import tempfile
    import threading
    import time
    import warnings
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    # The tool under test. TINYCMDR_SRC points the suite at a snapshot (the pre-fix
    # tools/process.py) so every check below can be watched going red.
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tools/process.py")
    TESTS = BASE / "tests"
    sys.path.insert(0, str(TESTS))

    import run_scenario  # noqa: E402

    FAILS = []


    def check(cond, what, detail=""):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}: {detail}")
        else:
            print(f"ok   {what}")


    def safe(fn, args, ctx):
        """run(), with a crash reported as its own outcome: an unvalidated argument used to
    raise straight out of run() (A-126/A-128), and the suite must grade that rather than
    die on it."""
        try:
            return fn(args, ctx)
        except Exception as e:                                       # noqa: BLE001
            return "RAISED %s: %s" % (type(e).__name__, e)


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbjobs-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            # A staged install carries no tools/; the process tool is a drop-in, so it must be
            # in place BEFORE the module import constructs the registry.
            (workdir / "tools").mkdir(exist_ok=True)
            shutil.copy2(SRC, workdir / "tools" / "process.py")
            fb = run_scenario.load(workdir)
            proc = fb.REGISTRY.get("process")
            check(proc is not None, "the process tool is loaded")
            if proc is None:
                return 1
            run_proc = proc["fn"]
            ctx = {"session_key": "jobs-test"}
            py = sys.executable

            # ---- start / wait / real exit code
            out = run_proc({"action": "start",
                            "command": "%s -u -c \"print('UP',flush=True)\"" % py}, ctx)
            jid = out.split()[1]
            check(jid.startswith("b"), "start returns an id", out[:60])
            time.sleep(1.0)
            st = run_proc({"action": "wait", "id": jid, "timeout": 10}, ctx)
            check("exit 0" in st, "wait reports the real exit code", st)

            # ---- the code survives the collector (simulated restart)
            run_proc.__globals__["_PROCS"].clear()
            st = run_proc({"action": "status", "id": jid}, ctx)
            check("exit 0" in st,
                  "after the collector is gone, the log's marker still has the code", st)

            # ---- a failing job reports its non-zero code too
            out = run_proc({"action": "start",
                            "command": "%s -u -c \"import sys;sys.exit(3)\"" % py}, ctx)
            jid3 = out.split()[1]
            time.sleep(1.0)
            st = run_proc({"action": "status", "id": jid3}, ctx)
            check("exit 3" in st, "a failing job reports exit 3", st)

            # ---- readiness: start blocks until the condition holds
            out = run_proc({"action": "start",
                            "command": "%s -u -c \"import time;time.sleep(1);"
                                       "print('SERVING',flush=True);time.sleep(4)\"" % py,
                            "wait_for": {"log": "SERVING", "timeout": 10}}, ctx)
            check("readiness: ready" in out,
                  "wait_for blocks until the log matches", out[-200:])
            jid2 = out.split()[1]
            # 300s, and not a number that could elapse: the "still-running job is NOT
            # announced" check below runs after the stdin step, and a short sleep raced it
            # TWICE under a loaded gate (5s, then 30s) - the job settled first and the check
            # failed on nothing but wall-clock. It is killed at the end of the test, so the
            # long sleep costs nothing but cannot make this assertion nondeterministic.
            out = run_proc({"action": "start",
                            "command": "%s -u -c \"import time;time.sleep(300)\"" % py,
                            "wait_for": {"log": "never", "timeout": 1}}, ctx)
            check("NOT ready" in out and "still running" in out,
                  "an unmet condition reports timedOut without killing the job", out[-200:])
            jid4 = out.split()[1]

            # ---- stdin: send answers a prompt
            out = run_proc({"action": "start",
                            "command": "%s -u -c \"print(input())\"" % py}, ctx)
            jid5 = out.split()[1]
            time.sleep(0.6)
            sent = run_proc({"action": "send", "id": jid5, "text": "hello-stdin"}, ctx)
            check(sent.startswith("sent"), "send writes to the job's stdin", sent)
            time.sleep(1.2)
            log = run_proc({"action": "output", "id": jid5, "lines": 5}, ctx)
            check("hello-stdin" in log, "the job read what was sent", log)

            # ---- the push: a settled job is announced once
            block = fb.settled_jobs_block()
            # Match the announcement LINE, not a raw substring: the block carries the log
            # path of every announced job, and the staged install's temp directory can
            # contain a job id by chance (TMPDIR=/tmp/b4check reproduced it), so
            # `jid4 not in block` failed on a path that merely contained "b4".
            def _announced(block_text, job_id):
                return re.search(r"(?m)^- %s finished," % re.escape(job_id), block_text)

            check(_announced(block, jid) and "exit 0" in block,
                  "a finished job is announced", block)
            check(fb.settled_jobs_block() == "", "...once")
            check(not _announced(block, jid4),
                  "a still-running job is NOT announced", block)

            # ---- auto-background: a slow shell call returns an id instead of blocking
            fb.CONFIG["agent"]["auto_background_seconds"] = 1
            t0 = time.time()
            out = fb.tool_shell({"command": "%s -u -c \"import time;time.sleep(3);"
                                            "print('DONE')\"" % py}, ctx)
            took = time.time() - t0
            check("background table" in out,
                  "a slow command returns the background pointer", out[-220:])
            check(took < 2.5, "and it did not hold the turn (%.1fs)" % took, took)
            check(bool([ln for ln in out.splitlines() if ln.startswith("started ")]),
                  "the pointer names the job", out[-160:])
            # a fast command still comes back inline, same shape as the blocking path
            out = fb.tool_shell({"command": "echo fast"}, ctx)
            check(out.startswith("exit_code=0") and "fast" in out,
                  "a fast command still returns inline output", out[:80])
            # wait:true opts out
            t0 = time.time()
            out = fb.tool_shell({"command": "sleep 2; echo late", "wait": True,
                                 "timeout": 20}, ctx)
            check("late" in out and time.time() - t0 >= 2,
                  "wait=true blocks regardless", out[:80])
            fb.CONFIG["agent"]["auto_background_seconds"] = 0

            # =============== (A-124.. A-130) ===============

            # ---- A-127: an unusable action names the vocabulary, before any job lookup
            out = safe(run_proc, {"action": "frobnicate"}, ctx)
            check(out.startswith("ERROR") and "unknown action" in out and "status" in out,
                  "an unknown action names the action vocabulary", out)
            out = safe(run_proc, {}, ctx)
            check(out.startswith("ERROR") and "unknown action" in out,
                  "a missing action is refused the same way", out)

            # ---- A-126: a command that is neither a shell string nor an argv list of
            #             strings is refused up front, with the intended ERROR line (an int
            #             raised TypeError here, a dict iterated its keys and was accepted)
            for bad in (5, {"a": "b"}):
                out = safe(run_proc, {"action": "start", "command": bad}, ctx)
                check(out.startswith("ERROR") and "needs a command" in out,
                      "start refuses command=%r" % (bad,), out)

            # ---- A-124/A-125: output must not leak the log handle, nor echo the wrapper's
            #                   `__EXIT__<rc>` marker back as if the job had printed it
            out = run_proc({"action": "start",
                            "command": [py, "-u", "-c", "print('MARKER-TEST')"]}, ctx)
            jid6 = out.split()[1]
            time.sleep(1.0)
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                for _ in range(5):                     # the five the audit measured
                    log = run_proc({"action": "output", "id": jid6, "lines": 20}, ctx)
                gc.collect()
            leaked = [w for w in seen if w.category is ResourceWarning
                      and ("proc-%s.log" % jid6) in str(w.message)]
            check("__EXIT__" not in log and "MARKER-TEST" in log,
                  "output does not echo the wrapper's __EXIT__ marker", log)
            check(not leaked, "output closes the log it read",
                  [str(w.message) for w in leaked][:2])

            # ---- A-126 cont.: a legacy row with a non-string command cannot kill `list`
            jobs_file = workdir / "logs" / "process-jobs.json"
            table = json.loads(jobs_file.read_text(encoding="utf-8"))
            table["b99"] = {"pid": 999999, "command": 5, "log": "", "started": "old"}
            jobs_file.write_text(json.dumps(table), encoding="utf-8")
            out = safe(run_proc, {"action": "list"}, ctx)
            check("b99" in out and not out.startswith("RAISED"),
                  "list survives a legacy row whose command is not a string", out)

            # ---- A-128: a wait_for spec is validated before the job is spawned
            for wf in ({"log": "("}, {"timeout": "abc"}, {"port": "nope"}, "soon",
                       {"port": 70000}):
                out = safe(run_proc, {"action": "start", "command": [py, "-c", "pass"],
                                      "wait_for": wf}, ctx)
                check(out.startswith("ERROR") and "wait_for" in out and '"log"' in out
                      and '"port"' in out,
                      "wait_for %r is refused, naming the accepted shape" % (wf,), out)

            # ---- A-129: kill reports what it confirmed, not more
            out = run_proc({"action": "start",
                            "command": [py, "-u", "-c", "import time;time.sleep(300)"]}, ctx)
            jid7 = out.split()[1]
            time.sleep(0.5)
            killed = safe(run_proc, {"action": "kill", "id": jid7}, ctx)
            check("the wrapper is gone" in killed,
                  "kill says exactly what it confirmed (the wrapper is gone)", killed)

            # ---- A-130: concurrent starts never share an id or a log. Six threads first
            #             (one process), then six separate processes (the lock file).
            before_rows = len(json.loads(jobs_file.read_text(encoding="utf-8")))
            results = []
            gate = threading.Barrier(6)

            def _concurrent_start():
                gate.wait()
                results.append(safe(run_proc, {"action": "start",
                                               "command": [py, "-u", "-c",
                                                           "import time;time.sleep(20)"]},
                                    ctx))

            threads = [threading.Thread(target=_concurrent_start) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            ids = [r.split()[1] for r in results if r.startswith("started")]
            check(len(ids) == 6 and len(set(ids)) == 6,
                  "6 concurrent starts in one process get 6 distinct ids", results)

            # Six separate processes, released together by wall clock: only the lock file can
            # serialize these, and without it they all read one table and pick the same id.
            script = (
                "import importlib.util, os, sys, time\n"
                "spec = importlib.util.spec_from_file_location('proc_under_test', sys.argv[1])\n"
                "mod = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(mod)\n"
                "time.sleep(max(0.0, float(os.environ['FB_GO']) - time.time()))\n"
                "print(mod.run({'action': 'start', 'command': [sys.executable, '-u', '-c',"
                " 'import time;time.sleep(20)']}, {}))\n")
            env = dict(os.environ, FB_GO=str(time.time() + 1.5))
            kids = [subprocess.Popen(
                [py, "-c", script, str(workdir / "tools" / "process.py")],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True) for _ in range(6)]
            outs = [k.communicate(timeout=60)[0] for k in kids]
            pids6 = [o.split()[1] for o in outs if o.startswith("started")]
            check(len(pids6) == 6 and len(set(pids6)) == 6,
                  "6 concurrent starts in 6 processes get 6 distinct ids", outs)
            after_rows = len(json.loads(jobs_file.read_text(encoding="utf-8")))
            check(after_rows == before_rows + 12,
                  "...and each start is a row of its own", (before_rows, after_rows))

            # ---- A-2026-10-08-108: the autobg wait kills through the ONE helper
            # (_kill_tree), whose Windows taskkill carries hidden_proc_kwargs() - the two
            # inline copies had drifted from it and flashed a console window under pythonw.
            killed = []
            real_kill = fb._kill_tree
            fb._kill_tree = lambda p: (killed.append(p.pid), real_kill(p))[1]
            cancel = threading.Event()
            threading.Timer(0.6, cancel.set).start()
            try:
                out = fb._shell_autobg(('"%s" -u -c "import time; time.sleep(30)"' % py),
                                       {"cancel_event": cancel}, 3.0)
            finally:
                fb._kill_tree = real_kill
            check(out.startswith("STOPPED") and killed,
                  "a cancelled autobg command is killed through _kill_tree", out[:120])

            # ---- A-2026-10-08-100: a limit AT the window is a ceiling, not a promotion
            # The wait ended at the threshold only, so with limit == threshold - the default
            # for every cost-guarded scan (search_timeout 60 == auto_background_seconds 60) -
            # the kill check sat one tick past the exit: the scan was promoted to the
            # background table, whose adopted jobs had no timeout, and the run was charged
            # only the window. A promoted job carries its ceiling now.
            _slow100 = '"%s" -u -c "import time; time.sleep(30)"' % py
            _t0_100 = time.time()
            _r_short = fb._shell_autobg(_slow100, {}, 60, 2)
            check(isinstance(_r_short, str) and _r_short.startswith("TIMEOUT after 2s")
                  and time.time() - _t0_100 < 20,
                  "a timeout shorter than the window still kills", str(_r_short)[:160])
            _t0_100 = time.time()
            _r_tie = fb._shell_autobg(_slow100, {}, 2, 2)
            check(isinstance(_r_tie, str) and _r_tie.startswith("TIMEOUT after 2s")
                  and time.time() - _t0_100 < 8,
                  "a limit equal to the window is enforced, not promoted", str(_r_tie)[:160])
            _t0_100 = time.time()
            _r_pr = fb._shell_autobg(_slow100, {}, 1, 4)
            _jid_pr = (_r_pr.split("started ", 1)[1].split(" ", 1)[0]
                       if isinstance(_r_pr, str) and "started " in _r_pr else "")
            _gone_pr = False
            _deadline_pr = time.time() + 9
            while _jid_pr and time.time() < _deadline_pr:
                if "running" not in str(run_proc({"action": "status", "id": _jid_pr}, ctx)):
                    _gone_pr = True
                    break
                time.sleep(0.25)
            try:
                check(bool(_jid_pr) and _gone_pr
                      and "killed at its 4s ceiling" in str(_r_pr)
                      and time.time() - _t0_100 < 12,
                      "a promoted command is killed at its ceiling too",
                      (str(_r_pr)[:140], _jid_pr, _gone_pr))
            finally:
                if _jid_pr and not _gone_pr:
                    try:
                        run_proc({"action": "kill", "id": _jid_pr}, ctx)
                    except Exception:                            # noqa: BLE001
                        pass

            for j in ("b99",) + tuple(ids) + tuple(pids6):
                run_proc({"action": "kill", "id": j}, ctx)

            for j in (jid2, jid4, jid5):
                run_proc({"action": "kill", "id": j}, ctx)
            time.sleep(0.5)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print("all job-control checks passed")
        return 0
    return main()


def main():
    rc = 0
    for name, fn in (("test_schedule", _suite_test_schedule), ("test_job_control", _suite_test_job_control)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
