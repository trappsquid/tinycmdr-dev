"""The scheduler: what a cron job may and may not do to an unattended box.

    python tests/test_schedule.py

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

print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
