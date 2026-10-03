"""Background jobs: a real exit code across restarts, readiness, stdin, and a push.

The process table said "finished (code not captured)" for any job that outlived the
process that started it, had no readiness condition (start-and-poll was the only way to
know a service was up), could not answer a prompt (stdin was DEVNULL), and a job that
ended was only discoverable by asking. No broker: the wrapper and the spool are plain
files under logs/.

    python tests/test_job_control.py
"""
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
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


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbjobs-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        # A staged install carries no tools/; the process tool is a drop-in, so it must be
        # in place BEFORE the module import constructs the registry.
        (workdir / "tools").mkdir(exist_ok=True)
        shutil.copy2(BASE / "tools" / "process.py", workdir / "tools" / "process.py")
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
        # 30s, not 5: the "still-running job is NOT announced" check below runs after the
        # stdin step (~3s of work), and a 5s sleep raced it - under a loaded gate the job
        # had already settled and the check failed on nothing but wall-clock (measured
        # 2026-10-03, one flake in a green run of the same tree). It is killed at the end.
        out = run_proc({"action": "start",
                        "command": "%s -u -c \"import time;time.sleep(30)\"" % py,
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
        check(jid in block and "exit 0" in block,
              "a finished job is announced", block)
        check(fb.settled_jobs_block() == "", "...once")
        check(jid4 not in block, "a still-running job is NOT announced", block)

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
