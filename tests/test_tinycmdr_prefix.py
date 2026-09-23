"""`/tinycmdr <verb>`: the one command trigger that always gets through.

Why this exists: a chat client never sends a message that starts with "/" unless it
matches a REGISTERED custom command, and the server refuses to register its own
trigger words - so `/help` and `/status` can never reach a bot, and a bare `/model`
only arrives because the relay has a row for it. `/tinycmdr` is one registered
trigger that carries anything, and the build accepts it in every lane it serves
(chat, web page, Telegram DM, console) while the bare words keep working: the
relay's per-verb rows post those.

The operator's rule (2026-09-22): ONE word, three places - `tinycmdr status` in a
shell, `/tinycmdr status` in a chat or a console session. `/cmdr` existed for part
of one day, and a line typed with it is answered with a pointer, not "unknown".

What must hold, and what this pins:
  * the mapping, including the shapes that must NOT be treated as a prefix
    (`/tinycmdrmodel`, `/tinycmdrfoo`, a task that merely mentions it)
  * the chat handler: `/tinycmdr new`, `/tinycmdr help`, an unknown verb answered locally
    (nothing queued, no model call)
  * the LISTENER thread: `/tinycmdr stop` and `/tinycmdr restart force` are read while a run
    owns the channel - the whole point of that early path
  * the web lane and the console lane
  * the generated console build carries the helper and calls it (it is above the
    generator's cut on purpose)

    python tests/test_cmdr.py            (all checks)
    python tests/test_cmdr.py <substring>
"""
import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-cmdr"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
FIXTURE = Path(__file__).resolve().parent / "fixture-config.json"
if not FIXTURE.exists():
    sys.exit(f"missing test fixture: {FIXTURE}")
shutil.copy2(FIXTURE, STAGE / "config.json")

spec = importlib.util.spec_from_file_location("tinycmdr_under_test_cmdr",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_under_test_cmdr"] = fb
spec.loader.exec_module(fb)

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


# --- the mapping -------------------------------------------------------------

def test_the_prefix_maps_onto_the_bare_verb():
    cases = [
        ("/tinycmdr model list", "/model list"),
        ("/tinycmdr model", "/model"),
        ("/tinycmdr  model  list", "/model  list"),
        ("/tinycmdr stop", "/stop"),
        ("/tinycmdr /model list", "/model list"),
        ("  /tinycmdr status  ", "/status"),
        ("/TINYCMDR status", "/status"),
        ("/tinycmdr", "/help"),
        ("/tinycmdr   ", "/help"),
    ]
    for given, want in cases:
        got = fb.cmdr_strip(given)
        check(f"{given!r} -> {want!r}", got == want, f"got {got!r}")


def test_shapes_that_are_not_a_prefix():
    for given in ("/tinycmdrmodel", "/tinycmdrfoo x", "/tiny", "", "   ",
                  "tell me about /tinycmdr", "the retired /cmdr prefix",
                  "/model list", "/new"):
        got = fb.cmdr_strip(given)
        check(f"{given!r} is untouched", got == given.strip(), f"got {got!r}")
    check("a None text does not raise", fb.cmdr_strip(None) == "")


def test_the_retired_prefix_gets_a_pointer():
    """`/cmdr` was live for one day. A near-miss must say what to type now, in every lane."""
    if not hasattr(fb, "MattermostDispatcher"):
        skip("legacy prefix", "chatless build")
        return
    d, posted = make_dispatcher()
    d._handle("c1", "david", "/cmdr status", "m9", "m9", True)
    text = " ".join(posted)
    check("chat answers a /cmdr line with the new prefix",
          "/tinycmdr" in text and "Unknown command" not in text, text[:200])
    check("...and does not send it to the model", not d.queues.get("c1"), d.queues)
    if hasattr(fb, "_web_command"):
        kind, reply = fb._web_command("/cmdr status", "web")
        check("the page answers it the same way",
              kind == "reply" and "/tinycmdr" in reply and "Unknown" not in reply,
              reply[:160])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fb._cli_command("/cmdr status")
    check("the console answers it the same way",
          "/tinycmdr" in out.getvalue(), out.getvalue()[:160])


# --- the chat handler -------------------------------------------------------

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


def test_chat_handler_routes_a_prefixed_command():
    if not hasattr(fb, "MattermostDispatcher"):
        skip("chat handler", "chatless build")
        return
    d, posted = make_dispatcher()
    d._handle("c1", "david", "/tinycmdr new", "m1", "m1", True)
    check("/tinycmdr new clears the session locally",
          any("Session cleared" in p for p in posted), posted)
    check("/tinycmdr new did not queue a task", not d.queues.get("c1"), d.queues)

    d2, posted2 = make_dispatcher()
    d2._handle("c1", "david", "/tinycmdr help", "m2", "m2", True)
    text = " ".join(posted2)
    check("/tinycmdr help answers with the command list",
          "**Commands**" in text and "/tinycmdr help" in text, text[:200])
    check("/tinycmdr help queued nothing", not d2.queues.get("c1"), d2.queues)

    d3, posted3 = make_dispatcher()
    d3._handle("c1", "david", "/tinycmdr wibble", "m3", "m3", True)
    check("an unknown verb is answered locally, never sent to the model",
          any("Unknown command" in p for p in posted3) and not d3.queues.get("c1"),
          posted3)


def test_listener_thread_reads_a_prefixed_command():
    if not hasattr(fb, "MattermostDispatcher"):
        skip("listener thread", "chatless build")
        return
    saved_cfg = fb.CONFIG["mattermost"].get("allowed_users")
    saved_restart = fb.perform_restart
    calls = []
    fb.perform_restart = lambda *a, **kw: calls.append((a, kw))
    fb.CONFIG["mattermost"]["allowed_users"] = ["david"]
    try:
        class FakeMessage:
            def __init__(self, text, mid):
                self.sender_name = "david"
                self.channel_id = "c1"
                self.create_at = 0
                self.id = mid
                self.user_id = "u1"
                self.root_id = ""
                self.is_direct_message = True

        d, posted = make_dispatcher()
        ev = threading.Event()
        d.cancel_events["c1"] = {ev}
        d.running["c1"] = threading.current_thread()   # a run is in flight
        d.enqueue(FakeMessage("/tinycmdr stop", "m1"), "/tinycmdr stop")
        check("/tinycmdr stop lands with a run in flight and flags it",
              ev.is_set() and any("Stopping now" in p for p in posted), posted)
        check("/tinycmdr stop did not queue behind the run",
              not d.queues.get("c1") and not calls, f"queues={d.queues}")

        d2, posted2 = make_dispatcher()
        ev2 = threading.Event()
        d2.cancel_events["c1"] = {ev2}
        d2.running["c1"] = threading.current_thread()
        d2.enqueue(FakeMessage("/tinycmdr restart force", "m2"), "/tinycmdr restart force")
        check("/tinycmdr restart force kills the live run then restarts",
              ev2.is_set() and len(calls) == 1 and not d2.queues.get("c1"),
              f"calls={calls} queues={d2.queues}")
        check("/tinycmdr restart force never answers 'queued'",
              not any("Queued behind" in p for p in posted2), posted2)
    finally:
        fb.perform_restart = saved_restart
        fb.CONFIG["mattermost"]["allowed_users"] = saved_cfg


# --- the other lanes --------------------------------------------------------

def test_web_lane():
    if not hasattr(fb, "_web_command"):
        skip("web lane", "chatless build")
        return
    kind, reply = fb._web_command("/tinycmdr status", "web")
    check("/tinycmdr status is a local reply, not a task", kind == "reply", kind)
    kind, reply = fb._web_command("/tinycmdr help", "web")
    check("/tinycmdr help lists the page's commands",
          kind == "reply" and "/tinycmdr" in reply, reply[:160])
    kind, reply = fb._web_command("/tinycmdr wibble", "web")
    check("an unknown web verb is answered locally",
          kind == "reply" and "Unknown command" in reply, reply[:160])
    check("the page's command list advertises the prefix",
          any(c.startswith("/tinycmdr") for c, _h in fb.WEB_COMMANDS), fb.WEB_COMMANDS)


def test_console_lane():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        keep = fb._cli_command("/tinycmdr help")
    printed = out.getvalue()
    check("/tinycmdr help in the console prints the command list",
          keep is True and "/tinycmdr help" in printed, printed[:160])

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fb._cli_command("/tinycmdr wibble")
    check("an unknown console verb says so",
          "not a command" in out.getvalue(), out.getvalue()[:120])

    check("the console help text documents the prefix",
          "/tinycmdr help" in fb.HELP_TEXT, fb.HELP_TEXT[:80])

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fb._cli_while_running("/tinycmdr wibble")
    check("a prefixed verb is understood by the mid-run reader",
          "not a command" not in out.getvalue(), out.getvalue()[:120])


def test_the_generated_console_build_carries_it():
    gen = BASE / "tinycmdr-cli.py"
    if not gen.exists():
        skip("generated console build", "not in this tree")
        return
    body = gen.read_text(encoding="utf-8", errors="replace")
    check("the helper survives the cut", "def cmdr_strip(" in body
          and "def cmdr_legacy_prefix(" in body)
    start = body.find("def _cli_command(")
    end = body.find("\nFAST_VERBS", start)
    region = body[start:end] if start >= 0 and end > start else ""
    check("the console lane calls it", "cmdr_strip(text)" in region,
          region[:200])
    check("nothing cut still references it",
          body.count("cmdr_strip(") >= 3, body.count("cmdr_strip("))


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
