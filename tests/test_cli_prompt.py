"""A question at the console must reach the reader, not race it.

Operator, 2026-09-22: "the cli version ... asks the user for an answer ask_user and the
cmd window freezes and doesnt accept any input at all".

What happened: `_cli_reader` owns stdin for the whole interactive session, and the
question read the terminal itself (`input()`), so the reader took the typed line, filed
it as steering, and the run waited for ever - the window looked frozen and accepted
nothing. The door was not even wired for ask_user there (`drive_run` got no ask_door),
so the tool answered "nothing in this run can reach a human" while chat and the web page
could both ask.

What this pins:
  * the answer reaches the question, and the typed line does NOT become steering
  * ask_user works at the console at all (it used to be refused, not parked)
  * a question is closed again afterwards (no box left open to eat the next request)
  * /stop while a question is open releases the run as a STOP, not a timeout
  * `--once` has no reader thread, so a question there reads stdin itself
  * the generated console build carries all of it

    python tests/test_cli_prompt.py            (all checks)
    python tests/test_cli_prompt.py <substring>

Order matters: test_1 runs before any reader thread exists (it grades the no-reader
path), so the tests are numbered.
"""
import importlib.util
import io
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["TINYCMDR_PLAIN"] = "1"      # no screen: the reader must use plain stdin
BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-cliprompt"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
FIXTURE = Path(__file__).resolve().parent / "fixture-config.json"
if not FIXTURE.exists():
    sys.exit(f"missing test fixture: {FIXTURE}")
shutil.copy2(FIXTURE, STAGE / "config.json")

spec = importlib.util.spec_from_file_location("tinycmdr_under_test_cliprompt",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_under_test_cliprompt"] = fb
spec.loader.exec_module(fb)

PASSES, FAILURES, SKIPPED = [], [], []
REAL_STDIN = sys.stdin
PIPE = {}                      # the fake terminal: r/w file descriptors


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


def wait_for(pred, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def type_at_the_terminal(line):
    os.write(PIPE["w"], (line + "\n").encode())


def ask_in_background(fn, out):
    """Run one blocking question on its own thread; returns (thread, result, out)."""
    def run():
        try:
            out["value"] = fn()
        except BaseException as e:                  # noqa: BLE001 - reported
            out["error"] = repr(e)
    t = threading.Thread(target=run)
    t.start()
    return t


def ensure_reader():
    """The interactive shape: queues, a stop event, and the reader owning stdin."""
    if PIPE:
        return
    r, w = os.pipe()
    PIPE["r"], PIPE["w"] = r, w
    sys.stdin = os.fdopen(r, "r", encoding="utf-8", errors="replace")
    fb._CLI["inbox"] = queue.Queue()
    fb._CLI["steer"] = queue.Queue()
    fb._CLI["ask"] = None
    fb._CLI["leave"] = False
    fb._CLI["reader"] = True
    fb._CLI["stop"] = threading.Event()      # a run is in flight
    threading.Thread(target=fb._cli_reader, daemon=True).start()


# --- 1. with no reader thread, the question reads stdin itself (--once) ---------

def test_1_a_once_run_reads_stdin_itself():
    fb._CLI["reader"] = False
    sys.stdin = io.StringIO("yes\n")
    try:
        line = fb.cli_ask_line("confirm: run it?", ["yes", "no"], 5, out=io.StringIO())
    finally:
        sys.stdin = REAL_STDIN
    check("no reader thread: the question reads the terminal itself", line == "yes",
          repr(line))

    fb._CLI["reader"] = False
    sys.stdin = io.StringIO("")            # a closed pipe / a cron job
    try:
        line = fb.cli_ask_line("confirm?", None, 5, out=io.StringIO())
    finally:
        sys.stdin = REAL_STDIN
    check("a closed stdin is not an answer, and does not hang", line is None, repr(line))

    fb._CLI["reader"] = True


# --- 2. the reported freeze -----------------------------------------------------

def test_2_the_reader_hands_the_answer_over():
    ensure_reader()
    out = io.StringIO()
    dest = fb.CliDestination(colour=False, out=out)
    got = {}
    t = ask_in_background(lambda: dest.ask("Which port should the job use?", None, 8.0), got)
    check("the question is published for the reader", wait_for(lambda: fb._CLI["ask"] is not None),
          fb._CLI.get("ask"))
    type_at_the_terminal("4185")
    t.join(15)
    check("the typed answer reached the question", got.get("value") == "4185", got)
    check("the answer did NOT become steering", fb._CLI["steer"].empty(),
          list(fb._CLI["steer"].queue))
    check("the answer did NOT queue a turn", fb._CLI["inbox"].empty(),
          list(fb._CLI["inbox"].queue))
    check("the question is closed again", fb._CLI["ask"] is None, fb._CLI.get("ask"))
    check("the question was printed with a prompt", "Which port" in out.getvalue(),
          out.getvalue()[:120])


def test_3_ask_user_works_at_the_console():
    ensure_reader()
    out = io.StringIO()
    dest = fb.CliDestination(colour=False, out=out)
    got = {}
    t = ask_in_background(
        lambda: fb.ask_operator("cli-prompt-ask", "Which port?", ctx={"ask_door": dest},
                                timeout=8), got)
    check("ask_user publishes the question", wait_for(lambda: fb._CLI["ask"] is not None),
          fb._CLI.get("ask"))
    type_at_the_terminal("4185")
    t.join(15)
    status, text = got.get("value") or ("<none>", "")
    check("ask_user is ANSWERED at a terminal (it used to be refused)", status == "answered"
          and text == "4185", got)
    check("the ask_user question reached the terminal", "Which port?" in out.getvalue(),
          out.getvalue()[:120])


def test_4_stop_releases_a_parked_question():
    ensure_reader()
    cancel = fb._CLI["stop"] = threading.Event()
    out = io.StringIO()
    dest = fb.CliDestination(colour=False, out=out)
    got = {}
    t = ask_in_background(
        lambda: fb.ask_operator("cli-prompt-stop", "Which port?",
                                ctx={"ask_door": dest, "cancel_event": cancel},
                                timeout=30), got)
    check("/stop: the question is open first", wait_for(lambda: fb._CLI["ask"] is not None),
          fb._CLI.get("ask"))
    type_at_the_terminal("/stop")
    t.join(15)
    status = (got.get("value") or ("<none>", ""))[0]
    check("/stop releases the parked run as a STOP, not a timeout", status == "stopped",
          got)


def test_5_the_generated_console_build_carries_it():
    gen = BASE / "tinycmdr-cli.py"
    if not gen.exists():
        skip("generated console build", "not in this tree")
        return
    body = gen.read_text(encoding="utf-8", errors="replace")
    check("the question channel survives the cut", "def cli_ask_line(" in body
          and "def cli_question_answer(" in body)
    check("the reader answers an open question", 'if _CLI.get("ask") is not None:' in body)
    check("the console wires the door", "ask_door=reporter.dest" in body)
    check("--once starts no reader thread", '_CLI["reader"] = not once' in body)
    check("CliDestination is ask_user's door there too",
          "def opener(self, question, options, wait, label=None):" in body)


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
