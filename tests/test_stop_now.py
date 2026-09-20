"""`/stop` has to stop everything, immediately.

Run:  python tests/test_stop_now.py            (all tests)
      python tests/test_stop_now.py <substring> (one test)

Background (operator, 2026-09-18): a `/stop` was acknowledged, a `/restart force`
was answered with "Queued behind the task already running here", and nothing actually
stopped for 90 seconds — because the live run was inside `shell({"command": "sleep 280; ..."})`.
Two separate defects were behind that:

1. `run_capture` blocked in `proc.wait(timeout)` and could not be interrupted by
   anything except the clock, so a /stop only took effect at the run's next turn
   boundary. The run's cancel event had no path into a tool call already in flight.
2. Only the literal `/stop` was intercepted on the listener thread. `/restart` and
   `/restart force` queued behind the run they were meant to kill, so against a
   wedged run a forced restart was never read at all.

These tests pin both fixes, plus the regressions they could cause (normal completion
and the timeout path must still behave exactly as before).
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("tinycmdr_SRC", "tinycmdr.py")

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-stop"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
FIXTURE = Path(__file__).resolve().parent / "fixture-config.json"
if not FIXTURE.exists():
    sys.exit(f"missing test fixture: {FIXTURE}")
shutil.copy2(FIXTURE, STAGE / "config.json")

spec = importlib.util.spec_from_file_location("tinycmdr_under_test_stop",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_under_test_stop"] = fb
spec.loader.exec_module(fb)

PY = sys.executable
TMP = Path(tempfile.gettempdir()) / "tinycmdr-test-stop"
TMP.mkdir(parents=True, exist_ok=True)

PASSES, FAILURES, SKIPPED = [], [], []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"ok   {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def skip(name, why=""):
    SKIPPED.append(name)
    print(f"skip {name}" + (f" — {why}" if why else ""))


def heartbeat_child(path, step=0.2):
    """argv for a child that ticks a file for ever, so it is killable mid-flight."""
    code = (
        "import time, pathlib\n"
        f"p = pathlib.Path(r'{path}')\n"
        "i = 0\n"
        "while True:\n"
        "    i += 1\n"
        "    p.write_text(str(i))\n"
        f"    time.sleep({step})\n"
    )
    return [PY, "-c", code]


def freeze_probe(path, after=1.2):
    """Size now, size after a pause: unchanged means the writer is dead."""
    a = path.stat().st_size if path.exists() else -1
    time.sleep(after)
    b = path.stat().st_size if path.exists() else -1
    return a, b


# --- 1/2: the regressions ---------------------------------------------------

def test_run_capture_still_returns_output():
    rc, out, err, timed_out = fb.run_capture([PY, "-c", "print('fine')"], 30)
    check("run_capture still returns output", rc == 0 and "fine" in out
          and not timed_out, f"rc={rc} out={out!r} err={err!r}")


def test_timeout_still_bites():
    t0 = time.time()
    rc, _out, _err, timed_out = fb.run_capture(
        [PY, "-c", "import time; time.sleep(60)"], 2)
    dt = time.time() - t0
    check("the timeout path still kills and returns", timed_out and dt < 20,
          f"timed_out={timed_out} dt={dt:.1f}s")


# --- 3: the stop reaches a command already running --------------------------

def test_a_stop_kills_a_running_command():
    hb = TMP / "hb_run_capture.txt"
    if hb.exists():
        hb.unlink()
    ev = threading.Event()
    threading.Timer(0.7, ev.set).start()
    t0 = time.time()
    raised = False
    try:
        fb.run_capture(heartbeat_child(hb), 120, cancel=ev)
    except fb.OperatorStop:
        raised = True
    except BaseException as e:                       # noqa: BLE001 - reported
        check("a stop raises OperatorStop", False, f"raised {type(e).__name__}: {e}")
        return
    dt = time.time() - t0
    a, b = freeze_probe(hb)
    check("a stop kills the child before the timeout", raised and dt < 10,
          f"raised={raised} dt={dt:.1f}s (timeout was 120s)")
    check("the killed child is really gone", a >= 0 and a == b,
          f"heartbeat kept growing: {a} -> {b}")


def test_a_stop_survives_a_generic_except_exception():
    """OperatorStop must stay a BaseException: `except Exception` is everywhere on
    this path and a stop must not be retried as a recoverable error."""
    check("OperatorStop is still a BaseException",
          issubclass(fb.OperatorStop, BaseException)
          and not issubclass(fb.OperatorStop, Exception))


# --- 4/5: the tools report a stop, not an error -----------------------------

def test_tool_shell_reports_a_stop_not_an_error():
    ev = threading.Event()
    ev.set()
    out = fb.tool_shell({"command": "echo should-not-run", "timeout": 30},
                        {"cancel_event": ev})
    check("shell: a pre-set cancel answers STOPPED", out.startswith("STOPPED"),
          out[:120])
    check("shell: a stop is never reported as an ERROR",
          "ERROR" not in out[:40], out[:120])


def shell_run_py(script):
    """A shell command that runs a python file, quoted for that host's shell.

    Built from a FILE, not from a joined argv: the shell here is PowerShell, where a
    quoted interpreter path with spaces inside a -Command string is a syntax error
    (`At line:1 char:75`), and the earlier version of this helper failed for exactly
    that reason rather than because the feature under test was broken.
    """
    if fb.IS_WINDOWS:
        return f'& "{PY}" "{script}"'
    return f'"{PY}" "{script}"'


def write_heartbeat(path, script, step=0.2):
    Path(script).write_text(
        "import time, pathlib\n"
        f"p = pathlib.Path(r'{path}')\n"
        "i = 0\n"
        "while True:\n"
        "    i += 1\n"
        "    p.write_text(str(i))\n"
        f"    time.sleep({step})\n", encoding="utf-8")
    return script


def test_tool_shell_stops_mid_command():
    hb = TMP / "hb_shell.txt"
    script = TMP / "hb_shell.py"
    if hb.exists():
        hb.unlink()
    write_heartbeat(hb, script)
    ev = threading.Event()
    threading.Timer(0.7, ev.set).start()
    t0 = time.time()
    out = fb.tool_shell({"command": shell_run_py(script), "timeout": 120},
                        {"cancel_event": ev})
    dt = time.time() - t0
    a, b = freeze_probe(hb)
    check("shell: a live stop returns STOPPED quickly",
          out.startswith("STOPPED") and dt < 10, f"{out[:80]!r} dt={dt:.1f}s")
    check("shell: the stopped command is dead", a >= 0 and a == b,
          f"heartbeat kept growing: {a} -> {b}")


def test_tool_execute_code_stops_mid_run():
    hb = TMP / "hb_code.txt"
    if hb.exists():
        hb.unlink()
    ev = threading.Event()
    threading.Timer(0.7, ev.set).start()
    code = (
        "import time, pathlib\n"
        f"p = pathlib.Path(r'{hb}')\n"
        "i = 0\n"
        "while True:\n"
        "    i += 1\n"
        "    p.write_text(str(i))\n"
        "    time.sleep(0.2)\n"
    )
    t0 = time.time()
    out = fb.tool_execute_code({"code": code, "timeout": 120}, {"cancel_event": ev})
    dt = time.time() - t0
    a, b = freeze_probe(hb)
    check("execute_code: a live stop returns STOPPED quickly",
          out.startswith("STOPPED") and dt < 10, f"{out[:80]!r} dt={dt:.1f}s")
    check("execute_code: the stopped snippet is dead", a >= 0 and a == b,
          f"heartbeat kept growing: {a} -> {b}")


def test_tools_runs_are_bound_to_a_fresh_event_per_message():
    """run_capture must only ever see a per-run event: a leaking global would make
    one channel's stop kill another channel's work."""
    src = (BASE / os.environ.get("tinycmdr_SRC", "tinycmdr.py")).read_text(
        encoding="utf-8", errors="replace")
    check("the run hands its cancel event to the tools",
          '"cancel_event": cancel_event,' in src
          and 'cancel=(ctx or {}).get("cancel_event")' in src)
    check("both shell and execute_code are wired",
          src.count('cancel=(ctx or {}).get("cancel_event")') == 2,
          f"found {src.count('cancel=(ctx or {}).get(chr(34)+chr(34))')}")


# --- 6: the listener thread answers /stop and /restart force ------------------

class FakeMessage:
    def __init__(self, text, sender="david", channel="c1", mid=None):
        self.sender_name = sender
        self.channel_id = channel
        self.create_at = int(time.time() * 1000)
        self.id = mid or f"m{time.time()}"
        self.user_id = "u1"
        self.root_id = ""
        self.is_direct_message = True


def make_dispatcher():
    d = fb.MattermostDispatcher()
    d.bot_username = "bot"
    d.last_seen = {}
    d.running = {}
    d.active = set()
    d.queues = {}
    d.steering = {}
    d.dead_roots = {}
    posted = []
    d._post = lambda channel_id, root_id, text, color=None: posted.append(text)
    return d, posted


def with_allowed(sender="david"):
    cfg = fb.CONFIG["mattermost"]
    saved = cfg.get("allowed_users")
    cfg["allowed_users"] = [sender]
    return saved


def test_stop_is_answered_on_the_listener_thread():
    if not hasattr(fb, "MattermostDispatcher"):
        skip("listener-thread stop", "chatless build")
        return
    saved = with_allowed()
    saved_restart = fb.perform_restart
    calls = []
    fb.perform_restart = lambda *a, **kw: calls.append((a, kw))
    try:
        d, posted = make_dispatcher()
        ev = threading.Event()
        d.cancel_events["c1"] = {ev}
        d.running["c1"] = threading.current_thread()   # a run is in flight
        d.enqueue(FakeMessage("/stop"), "/stop")
        check("/stop lands with a run in flight and flags it", ev.is_set()
              and any("Stopping now" in p for p in posted), posted)
        check("/stop did not queue behind the run",
              not d.queues.get("c1") and not calls, f"queues={d.queues} calls={calls}")
    finally:
        fb.perform_restart = saved_restart
        fb.CONFIG["mattermost"]["allowed_users"] = saved


def test_restart_force_kills_now_instead_of_queueing():
    if not hasattr(fb, "MattermostDispatcher"):
        skip("listener-thread /restart force", "chatless build")
        return
    saved = with_allowed()
    saved_restart = fb.perform_restart
    calls = []
    fb.perform_restart = lambda *a, **kw: calls.append((a, kw))
    try:
        d, posted = make_dispatcher()
        ev = threading.Event()
        d.cancel_events["c1"] = {ev}
        d.running["c1"] = threading.current_thread()
        d.enqueue(FakeMessage("/restart force"), "/restart force")
        check("/restart force stops the live run before restarting", ev.is_set(),
              "the run's cancel event was left unset")
        check("/restart force restarts without queueing", len(calls) == 1
              and not d.queues.get("c1"), f"calls={calls} queues={d.queues}")
        check("/restart force never answers 'queued'",
              not any("Queued behind" in p for p in posted), posted)
    finally:
        fb.perform_restart = saved_restart
        fb.CONFIG["mattermost"]["allowed_users"] = saved


def test_a_bare_restart_still_refuses_while_running():
    if not hasattr(fb, "MattermostDispatcher"):
        skip("listener-thread /restart", "chatless build")
        return
    saved = with_allowed()
    saved_restart = fb.perform_restart
    calls = []
    fb.perform_restart = lambda *a, **kw: calls.append((a, kw))
    try:
        d, posted = make_dispatcher()
        d.running["c1"] = threading.current_thread()
        d.enqueue(FakeMessage("/restart"), "/restart")
        check("/restart (no force) still refuses a live run",
              not calls and any("still running" in p for p in posted), posted)

        # ... and works normally when the channel is idle
        d2, posted2 = make_dispatcher()
        d2.enqueue(FakeMessage("/restart"), "/restart")
        check("/restart works on an idle channel", len(calls) == 1, calls)
    finally:
        fb.perform_restart = saved_restart
        fb.CONFIG["mattermost"]["allowed_users"] = saved


def test_ordinary_text_is_still_steered_not_queued():
    if not hasattr(fb, "MattermostDispatcher"):
        skip("steering regression", "chatless build")
        return
    saved = with_allowed()
    try:
        d, posted = make_dispatcher()
        d.running["c1"] = threading.current_thread()
        d.enqueue(FakeMessage("actually, use port 4185"), "actually, use port 4185")
        check("a running channel still receives steering",
              len(d.steering.get("c1") or []) == 1 and not d.queues.get("c1"),
              f"steering={d.steering} queues={d.queues}")
    finally:
        fb.CONFIG["mattermost"]["allowed_users"] = saved


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for t in tests:
        if only and only not in t.__name__:
            continue
        try:
            t()
        except Exception as e:                        # noqa: BLE001 - reported
            import traceback
            FAILURES.append(f"{t.__name__} raised: {e}")
            traceback.print_exc()
    tail = f", {len(SKIPPED)} skipped" if SKIPPED else ""
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed{tail}")
    for f in FAILURES:
        print("  FAIL:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
