"""The Windows launch helper: it starts the bot, waits, and starts it again.

Linux and macOS do not use this file at all - systemd `Restart=always` and launchd
`KeepAlive` are the watchdog there, and the Linux installer stopped copying it - so
what is graded here is the whole of what it still does on Windows: spawn, wait,
relaunch, and the ONE pure decision about what an exit means.

    python tests/test_supervise.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SUPERVISOR = BASE / "tinycmdr-supervise.py"

PASSES, FAILS = [], []

STUB = '''import json, os, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
(here / "argv.json").write_text(json.dumps(sys.argv[1:]))
(here / "env.json").write_text(json.dumps(
    {"supervised": os.environ.get("TINYCMDR_SUPERVISED")}))
if os.environ.get("STUB_MODE") == "sleep":
    time.sleep(1)
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
'''


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        PASSES.append(what)
        print(f"ok   {what}")


def stage(dirpath):
    """A throwaway install: the shipped helper plus a stub bot in its place."""
    dirpath.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SUPERVISOR, dirpath / "tinycmdr-supervise.py")
    (dirpath / "tinycmdr.py").write_text(STUB, encoding="utf-8")
    (dirpath / "logs").mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location(
        "sup_" + dirpath.name, dirpath / "tinycmdr-supervise.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    work = Path(tempfile.mkdtemp(prefix="fbsup-"))
    try:
        # ---- what an exit MEANS: one pure decision, graded in every direction ----
        d = stage(work / "decide")
        R = d.respond_to_exit

        def fields(out):
            """A verdict as (failures, self_restarts, window_start, delay).

            The fix adds the middle two fields; the pre-fix 2-tuple has neither, so
            the checks that need them read None there and FAIL rather than raise.
            """
            if isinstance(out, tuple) and len(out) == 4:
                return out
            if isinstance(out, tuple) and len(out) == 2:
                return out[0], None, None, out[-1]
            return None, None, None, None

        def verdict(out):
            """Just (failures, delay) - the shape both builds share."""
            f = fields(out)
            return f[0], f[3]

        def R5(*args):
            """respond_to_exit with the state the fix threads; None when absent."""
            try:
                return R(*args)
            except TypeError:
                return None

        def mark(age=0):
            """Drop the bot's handover marker, as a fresh /restart would.

            The path is resolved defensively so the pre-fix helper (which has no
            RESTART_MARKER_FILE and no reader) still gets the file written; it just
            ignores it, and the checks below FAIL rather than raise.
            """
            MARKER.parent.mkdir(parents=True, exist_ok=True)
            MARKER.write_text(json.dumps({"at": int(time.time()) - age, "by": "operator"}),
                              encoding="utf-8")

        MARKER = getattr(d, "RESTART_MARKER_FILE", d.LOGS / "restart-requested.json")
        FRESH = getattr(d, "RESTART_MARKER_FRESH_S", 120)
        CEIL = getattr(d, "SELF_RESTARTS_BEFORE_STOP", 5)
        WINDOW = getattr(d, "SELF_RESTART_WINDOW_S", 30 * 60)

        check(hasattr(d, "SELF_RESTARTS_BEFORE_STOP") and
              hasattr(d, "SELF_RESTART_WINDOW_S") and
              (d.SELF_RESTARTS_BEFORE_STOP, d.SELF_RESTART_WINDOW_S) == (5, 30 * 60),
              f"the self-restart ceiling is named as {CEIL} per {WINDOW}s "
              "(pre-fix: absent)")

        # a /restart handover is exempt while its marker is FRESH - and consumed once
        mark()
        out = R(75, 2, 3)
        check(verdict(out) == (0, 1),
              f"a /restart handover is not a failure and relaunches at once ({out})")
        check(not MARKER.exists(),
              "the handover marker is consumed, so a later cycle cannot read it twice")

        check(verdict(R(3, 2, 2)) == (2, 30),
              f"another instance holding the bot lock is not our failure ({R(3, 2, 2)})")
        check(verdict(R(1, 1, 0)) == (1, 5),
              f"a crash counts and backs off from {d.BACKOFF_START}s ({R(1, 1, 0)})")
        check(verdict(R(1, 1, 3)) == (4, 40),
              f"the backoff grows with consecutive failures ({R(1, 1, 3)})")
        check(verdict(R(0, d.RAPID_EXIT_S + 1, 4)) == (0, d.BACKOFF_START),
              f"a lifetime past {d.RAPID_EXIT_S}s was healthy and resets the count "
              f"({R(0, d.RAPID_EXIT_S + 1, 4)})")
        # A-2026-10-08-170: the reset required code == 0, so a crash after days counted
        # as a failed START; crashes spread over weeks added up until one quick exit at
        # boot tripped the give-up and the bot stayed down.
        check(verdict(R(1, d.RAPID_EXIT_S + 1, 4)) == (0, d.BACKOFF_START),
              f"a CRASH after a long healthy run resets the count too "
              f"({R(1, d.RAPID_EXIT_S + 1, 4)})")
        check(verdict(R(1, d.RAPID_EXIT_S + 1, d.FAILED_STARTS_BEFORE_STOP - 1))
              == (0, d.BACKOFF_START),
              f"a crash after days is not one of the {d.FAILED_STARTS_BEFORE_STOP} "
              f"rapid failed starts ({R(1, d.RAPID_EXIT_S + 1, d.FAILED_STARTS_BEFORE_STOP - 1)})")
        check(d.next_backoff(20) == d.BACKOFF_MAX,
              f"the backoff is capped at {d.BACKOFF_MAX}s ({d.next_backoff(20)})")
        check(verdict(R(2, 0, d.FAILED_STARTS_BEFORE_STOP - 2))[1] > 0,
              f"a rapid failed start retries while under the give-up count "
              f"({R(2, 0, d.FAILED_STARTS_BEFORE_STOP - 2)})")
        check(verdict(R(2, 0, d.FAILED_STARTS_BEFORE_STOP - 1)) ==
              (d.FAILED_STARTS_BEFORE_STOP, 0),
              f"the {d.FAILED_STARTS_BEFORE_STOP}th rapid failed start gives up instead of "
              f"retrying for ever ({R(2, 0, d.FAILED_STARTS_BEFORE_STOP - 1)})")
        check(verdict(R(1, 300, 12))[1] > 0,
              f"a bot that RAN before each crash still retries - no give-up ({R(1, 300, 12)})")

        # ---- a 75 with NO marker is the bot restarting itself, and is counted ----
        check(fields(R(75, 2, 0))[1] == 1,
              f"a 75 with no marker counts as a self-restart ({R(75, 2, 0)})")
        mark(age=FRESH + 30)
        out = R(75, 2, 0)
        check(fields(out)[1] == 1,
              f"a STALE marker does not exempt the exit - counted too ({out})")
        check(not MARKER.exists(), "the stale marker is consumed as well")

        # the ladder itself: N self-restarts inside the window, then delay 0 (stop)
        failures, selfn, window = 0, 0.0, 0.0   # window start 0 doubles as "unset"
        now = time.time()
        delays = []
        for _ in range(CEIL):
            f = fields(R5(75, 2, failures, selfn, window, now))
            failures, selfn, window = f[0], f[1], f[2]
            delays.append(f[3])
        check(delays == [1] * (CEIL - 1) + [0],
              f"{CEIL} unrequested 75s inside "
              f"{WINDOW // 60} min stop the loop, delays {delays}")

        # ...and a human /restart resets that ladder, because a human is watching
        mark()
        out = R5(75, 2, failures, selfn, window, now)
        check(fields(out) == (0, 0, 0.0, 1),
              f"a requested handover resets the self-restart counter ({out})")

        # the window is a real bound: 30 quiet minutes and the count starts over
        f = fields(R5(75, 2, 0, CEIL - 1, now,
                      now + WINDOW + 1))
        check(f[1] == 1,
              f"the {WINDOW // 60} min window expires, it starts over ({f})")

        # ---- it really does start the bot, pass args, and mark it supervised -----
        d = stage(work / "run")
        os.environ["STUB_MODE"] = "exit"
        os.environ["STUB_EXIT"] = "7"
        d.CHILD_ARGS = ["--telegram", "extra"]
        code, uptime = d.run_once()
        check(code == 7, f"run_once returns the bot's exit code ({code})")
        check(uptime < d.RAPID_EXIT_S, f"...with the lifetime it measured ({uptime}s)")
        check(json.loads((work / "run" / "argv.json").read_text()) == ["--telegram", "extra"],
              "the child gets the supervisor's arguments")
        check(json.loads((work / "run" / "env.json").read_text())["supervised"] == "1",
              "the child is told it is supervised (so /restart hands over, not doubles)")
        log = (work / "run" / "logs" / "supervisor.log").read_text(encoding="utf-8")
        check("started bot: pid" in log, "each start is written to supervisor.log")

        # a lifetime that actually runs is timed, not assumed
        os.environ["STUB_MODE"] = "sleep"
        os.environ["STUB_EXIT"] = "0"
        d.CHILD_ARGS = []
        code, uptime = d.run_once()
        check(code == 0 and uptime >= 1,
              f"a 1s lifetime is measured as such ({code}, {uptime}s)")

        # ---- --once runs exactly one lifetime (the test/ops door) ---------------
        os.environ["STUB_MODE"] = "exit"
        os.environ["STUB_EXIT"] = "7"
        check(d.main(["--once"]) == 7, "--once returns one lifetime's exit code")
    finally:
        os.environ.pop("STUB_MODE", None)
        os.environ.pop("STUB_EXIT", None)
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
