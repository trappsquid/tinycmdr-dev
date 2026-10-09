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

    python tests/test_job_control.py                     [TINYCMDR_SRC=<process.py>]
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


if __name__ == "__main__":
    sys.exit(main())
