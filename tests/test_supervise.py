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
        check(R(75, 2, 3) == (0, 1),
              f"a /restart handover is not a failure and relaunches at once ({R(75, 2, 3)})")
        check(R(3, 2, 2) == (2, 30),
              f"another instance holding the bot lock is not our failure ({R(3, 2, 2)})")
        check(R(1, 1, 0) == (1, 5),
              f"a crash counts and backs off from {d.BACKOFF_START}s ({R(1, 1, 0)})")
        check(R(1, 1, 3) == (4, 40),
              f"the backoff grows with consecutive failures ({R(1, 1, 3)})")
        check(R(0, d.RAPID_EXIT_S + 1, 4) == (0, d.BACKOFF_START),
              f"a lifetime past {d.RAPID_EXIT_S}s was healthy and resets the count "
              f"({R(0, d.RAPID_EXIT_S + 1, 4)})")
        check(d.next_backoff(20) == d.BACKOFF_MAX,
              f"the backoff is capped at {d.BACKOFF_MAX}s ({d.next_backoff(20)})")

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
