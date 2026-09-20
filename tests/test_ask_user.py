"""ask_user: a run that needs the operator's decision STOPS and asks.

Run:  python tests/test_ask_user.py            (all tests)
      python tests/test_ask_user.py <substring> (one test)

Background: every door into a running conversation was one-way (steering only arrives
if the operator types; the confirm prompt is yes/no and only for confirm_patterns), so a
run that reached a fork it could not decide could only guess or end its turn. This suite
pins the third move and, more importantly, the ways it must NOT work:

  * nobody to ask (a sub-agent, a job with no channel, a box with the feature off):
    refuse immediately - a wait with no human behind it is a stalled run;
  * the wait is always bounded, however long the model asks for;
  * a /stop releases a parked run at once, not at the end of its window;
  * one question per session at a time.

It runs against a fake dispatcher exactly like tests/test_stall.py: no network, no model,
and the ledger is never touched.
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

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-ask"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
FIXTURE = Path(__file__).resolve().parent / "fixture-config.json"
if not FIXTURE.exists():
    sys.exit(f"missing test fixture: {FIXTURE}")
shutil.copy2(FIXTURE, STAGE / "config.json")

spec = importlib.util.spec_from_file_location("tinycmdr_under_test_ask",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_under_test_ask"] = fb
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


class FakeDispatcher(fb.MattermostDispatcher):
    """Dispatcher with the network stubbed out; records what it posted."""

    def __init__(self):
        super().__init__()
        self.posted = []

    def _post(self, channel_id, root_id, text, color=None):
        self.posted.append((channel_id, text))
        self._touch(channel_id)
        return "post-%d" % len(self.posted)

    def _handle(self, channel_id, sender, text, msg_id, thread_root, is_dm, gen=0):
        pass            # never let a test worker reach the real agent


class _FakeMsg:
    def __init__(self, channel_id, text, sender="david", mid="m1"):
        self.sender_name = sender
        self.channel_id = channel_id
        self.text = text
        self.create_at = time.time() * 1000
        self.id = mid
        self.root_id = ""
        self.user_id = "david-id"
        self.is_direct_message = True


def dispatcher():
    d = FakeDispatcher()
    fb.CONFIG["mattermost"]["allowed_users"] = ["david", "david-id"]
    return d


def in_thread(fn, *a, **kw):
    """Run fn in a thread; return (join, box) where box['out'] holds the result."""
    box = {}

    def run():
        try:
            box["out"] = fn(*a, **kw)
        except BaseException as e:      # a raised exception is itself a finding
            box["err"] = repr(e)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t.join, box


def wait_for(pred, timeout=8.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


# --------------------------------------------------------------- the tool surface

def test_the_tool_exists_and_is_visible():
    check("ask_user is a core tool", "ask_user" in fb.CORE_TOOLS)
    schemas = {s["function"]["name"]: s for s in fb.select_tool_schemas(None)}
    check("ask_user is actually offered to the model", "ask_user" in schemas,
          sorted(schemas)[:6])
    schema = schemas.get("ask_user", {}).get("function", {})
    check("question is required",
          (schema.get("parameters") or {}).get("required") == ["question"],
          schema.get("parameters", {}).get("required"))
    check("it is in the always-visible set",
          "ask_user" in fb.core_tool_names(),
          fb.core_tool_names()[:14])
    check("its description says it blocks",
          "WAIT" in (schema.get("description") or ""),
          (schema.get("description") or "")[:60])


def test_config_gate_is_off_by_default():
    saved = fb.CONFIG["agent"].get("ask_user")
    try:
        fb.CONFIG["agent"]["ask_user"] = False
        out = fb.tool_ask_user({"question": "which way?"},
                               {"session_key": "s-off", "ask_door": {"post": 1, "answer": 1}})
        check("off by default the tool refuses", "switched off" in out, out[:120])
        check("and points at the fallback", "assumption" in out, out[:120])
        t0 = time.time()
        out2 = fb.tool_ask_user({"question": "which way?"}, {"session_key": "s-off2"})
        check("with no door it does not wait", time.time() - t0 < 1.0,
              "%.1fs" % (time.time() - t0))
        check("and says why", "switched off" in out2, out2[:100])
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved


def test_a_subagent_has_nobody_to_ask():
    saved = fb.CONFIG["agent"].get("ask_user")
    fb.CONFIG["agent"]["ask_user"] = True
    try:
        t0 = time.time()
        out = fb.tool_ask_user({"question": "which endpoint?"},
                               {"session_key": "sub-1", "depth": 1,
                                "ask_door": {"post": lambda *a: None,
                                             "answer": lambda *a: True}})
        check("a sub-agent is refused", "sub-agent has nobody to ask" in out, out[:120])
        check("and never blocked", time.time() - t0 < 1.0, "%.1fs" % (time.time() - t0))
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved


def test_an_unroutable_question_does_not_wait():
    """The shape a scheduled job takes when it has no channel to report into, and the
    shape any future run takes if its opener forgets a door: unreadable, immediately."""
    saved = fb.CONFIG["agent"].get("ask_user")
    fb.CONFIG["agent"]["ask_user"] = True
    try:
        t0 = time.time()
        out = fb.tool_ask_user({"question": "deploy now?"}, {"session_key": "s-nodoor"})
        dt = time.time() - t0
        check("no door -> no wait", dt < 1.0, "%.1fs" % dt)
        check("and it says nobody is reachable", "COULD NOT ASK A HUMAN" in out, out[:160])
        check("and tells the model to carry on", "carry on" in out, out[:160])
        st, _ = fb.ask_operator("s-nodoor2", "hello?")
        check("ask_operator agrees (unreadable)", st == "unreadable", st)
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved


# ------------------------------------------------------------------- the waiting

def test_an_answer_releases_the_run():
    saved = fb.CONFIG["agent"].get("ask_user")
    saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
    fb.CONFIG["agent"]["ask_user"] = True
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = 600
    d = dispatcher()
    try:
        door = d.ask_door_factory("chan-a", "sess-a")
        join, box = in_thread(fb.tool_ask_user,
                              {"question": "Restart host-a or host-b first?",
                               "options": ["host-a", "host-b"]},
                              {"session_key": "sess-a", "ask_door": door})
        check("the question is posted", wait_for(lambda: any(
            "Restart host-a or host-b first?" in t for _, t in d.posted), 3.0),
            d.posted)
        check("the options are offered", any(
            "host-b" in t for _, t in d.posted), d.posted)
        check("a row is open for the session",
              wait_for(lambda: d.pending_asks.get("chan-a") is not None, 3.0),
              list(d.pending_asks))
        claimed = d.reply_ask("sess-a", "host-b first")
        check("the answer is claimed (not queued as steering)", claimed is True)
        join(5)
        out = box.get("out", box.get("err", ""))
        check("the run resumes with the operator's answer",
              "OPERATOR ANSWER: host-b first" in out, out[:200])
        check("and the answer is marked as an instruction",
              "direct instruction" in out, out[:220])
        check("the pending row is cleared after the answer",
              d.pending_asks.get("chan-a") is None, list(d.pending_asks))
        check("a second message is not swallowed as an answer",
              d.reply_ask("sess-a", "also do the other one") is False)
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait


def test_a_message_in_the_channel_is_claimed_by_enqueue():
    """The listener-thread path: this is what actually happens live, and it must beat
    the queue (a queued message is not read until the parked run is over - which is
    exactly the run that is waiting for it)."""
    saved = fb.CONFIG["agent"].get("ask_user")
    saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
    fb.CONFIG["agent"]["ask_user"] = True
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = 600
    d = dispatcher()
    try:
        door = d.ask_door_factory("chan-e", "sess-e")
        join, box = in_thread(fb.tool_ask_user, {"question": "Which host first?"},
                              {"session_key": "sess-e", "ask_door": door})
        check("waiting", wait_for(lambda: d.pending_asks.get("chan-e") is not None, 3.0))
        d.enqueue(_FakeMsg("chan-e", "the host-b one"), "the host-b one")
        join(5)
        out = box.get("out", box.get("err", ""))
        check("enqueue delivered it as the ANSWER, not to the queue",
              "OPERATOR ANSWER: the host-b one" in out, out[:200])
        q = d.queues.get("chan-e")
        check("nothing was queued behind the parked run",
              q is None or q.empty(), "queue size %s" % (q.qsize() if q else 0))
        check("the operator is told it landed",
              any("Got it" in t for _, t in d.posted), [t for _, t in d.posted][-3:])
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait


def test_stop_releases_a_parked_run_at_once():
    saved = fb.CONFIG["agent"].get("ask_user")
    saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
    fb.CONFIG["agent"]["ask_user"] = True
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = 900
    d = dispatcher()
    try:
        door = d.ask_door_factory("chan-s", "sess-s")
        cancel = threading.Event()
        t0 = time.time()
        join, box = in_thread(fb.tool_ask_user, {"question": "Ship it?",
                                                 "timeout": "10m"},
                              {"session_key": "sess-s", "ask_door": door,
                               "cancel_event": cancel})
        check("waiting long", wait_for(lambda: d.pending_asks.get("chan-s") is not None, 3.0))
        d._stop_channel("chan-s", None)
        join(5)
        dt = time.time() - t0
        out = box.get("out", box.get("err", ""))
        check("the parked run came back at once, not after 10 minutes", dt < 5.0,
              "%.1fs" % dt)
        # This check used to assert "NO ANSWER in out" - i.e. it encoded the bug: a stop
        # fell through to the timeout branch and the model was told to apply its own
        # judgment and carry on, which is the opposite of /stop.
        check("and it was told to STOP, not to carry on with a guess",
              "STOPPED" in out and "NO ANSWER" not in out, out[:200])
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait


def test_a_timeout_hands_the_decision_back():
    saved = fb.CONFIG["agent"].get("ask_user")
    saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
    fb.CONFIG["agent"]["ask_user"] = True
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = 6      # the whole point: bounded
    d = dispatcher()
    try:
        door = d.ask_door_factory("chan-t", "sess-t")
        t0 = time.time()
        out = fb.tool_ask_user({"question": "Which of the two?",
                                "options": ["a", "b"]},
                               {"session_key": "sess-t", "ask_door": door})
        dt = time.time() - t0
        check("the wait is bounded by the config", 5.0 < dt < 12.0, "%.1fs" % dt)
        check("the model is told nobody answered", "NO ANSWER" in out, out[:160])
        check("and to state its assumption", "assumption" in out, out[:200])
        check("and not to ask again", "Do not wait again" in out, out[:220])
        check("the operator is told what happened",
              any("No answer" in t for _, t in d.posted), [t for _, t in d.posted][-2:])
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait


def test_the_model_cannot_grant_itself_a_longer_wait():
    saved = fb.CONFIG["agent"].get("ask_user")
    saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
    fb.CONFIG["agent"]["ask_user"] = True
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = 6
    d = dispatcher()
    try:
        door = d.ask_door_factory("chan-c", "sess-c")
        t0 = time.time()
        fb.tool_ask_user({"question": "anyone?", "timeout": "4h"},
                         {"session_key": "sess-c", "ask_door": door})
        dt = time.time() - t0
        check("a 4h request is still capped by the config", dt < 12.0, "%.1fs" % dt)
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait


def test_a_prose_timeout_from_a_door_does_not_raise():
    """ask_operator is called by the tool, the web door and the scheduler alike. A door
    that hands over "5m" used to hit float("5m") and raise out of the call instead of
    becoming a bounded wait."""
    saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = 6
    d = dispatcher()
    try:
        door = d.ask_door_factory("chan-p", "sess-p")
        t0 = time.time()
        status, text = fb.ask_operator("sess-p", "which one?", door=door, timeout="5m")
        dt = time.time() - t0
        check("a prose timeout is parsed, not raised on", status == "timeout",
              (status, text))
        check("and it is still bounded by the config", dt < 12.0, "%.1fs" % dt)
    finally:
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait


def test_an_empty_reply_is_not_an_answer():
    """A door that sets the event with "" (a stray newline, a row claimed with no text)
    must fall through to the no-answer path, not hand the model an empty
    "OPERATOR ANSWER:" instruction to follow."""
    saved = fb.CONFIG["agent"].get("ask_user")
    saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
    fb.CONFIG["agent"]["ask_user"] = True
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = 5

    def opener(q, opts, wait, label):
        row = {"ev": threading.Event(), "answer": "", "question": q,
               "options": list(opts)}
        row["ev"].set()
        return row

    def noop(*a, **kw):
        return None

    try:
        out = fb.tool_ask_user({"question": "proceed?"},
                               {"session_key": "sess-e",
                                "ask_door": {"opener": opener, "post": noop,
                                             "post_done": noop, "close_question": noop}})
        check("an empty reply does not become an answer",
              "OPERATOR ANSWER" not in out, out[:160])
        check("it falls through to the no-answer path", "NO ANSWER" in out, out[:200])
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait


def test_one_question_per_session():
    saved = fb.CONFIG["agent"].get("ask_user")
    saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
    fb.CONFIG["agent"]["ask_user"] = True
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = 600
    d = dispatcher()
    try:
        door = d.ask_door_factory("chan-o", "sess-o")
        join, box = in_thread(fb.tool_ask_user, {"question": "first?"},
                              {"session_key": "sess-o", "ask_door": door})
        check("first question is open",
              wait_for(lambda: d.pending_asks.get("chan-o") is not None, 3.0))
        t0 = time.time()
        second = fb.tool_ask_user({"question": "second?"},
                                  {"session_key": "sess-o", "ask_door": door})
        check("a second question is refused, not queued", "already open" in second,
              second[:140])
        check("and it did not wait", time.time() - t0 < 1.0)
        d.reply_ask("sess-o", "yes")
        join(5)
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait


def test_a_web_run_object_is_a_door():
    """The web UI's door is an OBJECT (WebRun), not a dict: a dict-only check silently
    reduced the browser to 'nobody can answer' - caught here, not in production."""
    saved = fb.CONFIG["agent"].get("ask_user")
    saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
    fb.CONFIG["agent"]["ask_user"] = True
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = 600
    try:
        run = fb.WebRun("r1", "web")
        t0 = time.time()
        join, box = in_thread(fb.ask_operator, "web", "Which branch?",
                              {"session_key": "web", "ask_door": run})
        check("the question is drawn in the run's stream",
              wait_for(lambda: any("Which branch?" in l["text"] for l in run.lines), 3.0),
              [l["text"] for l in run.lines])
        check("the run publishes the row the POST answers",
              wait_for(lambda: getattr(run, "asked", None) is not None, 3.0))
        check("the web door never falls through to nobody-can-answer",
              box.get("out") is None, box)
        row = run.asked
        row["answer"] = "main"
        row["ev"].set()
        join(5)
        status, text = box.get("out", ("?", ""))
        check("an /api/steer POST answered it", (status, text) == ("answered", "main"),
              (status, text))
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait


def test_the_stall_watchdog_does_not_eat_a_question():
    """Asking IS progress: the question post has to touch the channel's activity, or
    the run waiting for the answer is abandoned for silence - the exact drop the
    question existed to avoid."""
    saved = fb.CONFIG["agent"].get("ask_user")
    saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
    fb.CONFIG["agent"]["ask_user"] = True
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = 6
    d = dispatcher()
    try:
        d.active["chan-w"] = {"started": time.time() - 3600, "last": time.time() - 3000,
                              "warned": False, "gen": 1}
        door = d.ask_door_factory("chan-w", "sess-w")
        in_thread(fb.tool_ask_user, {"question": "quick?"},
                  {"session_key": "sess-w", "ask_door": door})
        check("the question post counts as progress",
              wait_for(lambda: d.active["chan-w"]["last"] > time.time() - 5, 3.0),
              d.active["chan-w"])
        # and the watchdog, run at that instant, must not abandon the channel
        before = len(d.posted)
        d._stall_tick(now=time.time())
        check("the watchdog leaves it alone",
              not any("wedged" in t for _, t in d.posted[before:]),
              [t for _, t in d.posted[before:]])
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait


def test_status_reports_the_feature_and_the_wait():
    saved = fb.CONFIG["agent"].get("ask_user")
    fb.CONFIG["agent"]["ask_user"] = True
    try:
        s = fb.status_text("sx")
        check("/status names ask_user", "ask_user:" in s,
              [l for l in s.splitlines() if "ask_user" in l])
        check("/status shows the wait bound", "wait" in s, s[-200:])
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved


def test_duration_parsing():
    cases = {"5m": 300, "90": 90, "90s": 90, "2 hours": 7200, "half an hour": 1800,
             "1h": 3600, "45 minutes": 2700, "3d": 259200}
    bad = []
    for text, want in cases.items():
        got = fb.parse_duration(text)
        if got != want:
            bad.append((text, got, want))
    check("plain durations parse", not bad, bad)
    check("nothing parses to None", fb.parse_duration(None) is None)
    check("prose with no number is None", fb.parse_duration("whenever you like") is None)


# ------------------------------------------------- how the answer is given back

def test_options_are_numbered_for_the_operator():
    """Reported live after the first real question: the options were inline code, so
    answering meant retyping one verbatim. A number has to be a valid answer."""
    text = fb._ask_format("Patch which host first?", ["host-b", "host-c", "host-a"])
    check("the question is in the post", "Patch which host first?" in text, text)
    check("options are numbered", "**1.** host-b" in text and "**3.** host-a" in text, text)
    check("the numbering is explained", "number" in text.lower(), text)


def test_a_question_with_no_options_still_renders():
    text = fb._ask_format("Which one?", [])
    check("no options -> just the question", text.strip().endswith("**"), text)
    check("and no empty list", "1." not in text, text)


def test_a_number_is_the_selector():
    row = {"options": ["host-b", "host-c", "host-a"]}
    cases = {"1": "host-b", "2": "host-c", "3.": "host-a", "3)": "host-a",
             "option 2": "host-c",
             "1 - do it now": "host-b - with this too: do it now",
             "2 but not the third host": "host-c - with this too: but not the third host"}
    bad = []
    for said, want in cases.items():
        got = fb._ask_record_choice(row, said)
        if got != want:
            bad.append((said, got, want))
    check("a number resolves to its option, and a clause is kept", not bad, bad)


def test_non_selectors_are_left_as_prose():
    row = {"options": ["host-b", "host-c"]}
    cases = ["9", "skytch first", "the second one", ""]
    bad = [(c, fb._ask_record_choice(row, c)) for c in cases
           if fb._ask_record_choice(row, c) is not None]
    check("only a real selector resolves", not bad, bad)
    check("no options means nothing to resolve",
          fb._ask_record_choice({"options": []}, "1") is None)


def test_a_numbered_answer_reaches_the_model_resolved():
    """End to end through the door: the operator types '2', the model is told WHICH
    option that was, so it cannot re-ask a decision it already has."""
    saved = fb.CONFIG["agent"].get("ask_user")
    saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
    fb.CONFIG["agent"]["ask_user"] = True
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = 120
    d = dispatcher()
    try:
        door = d.ask_door_factory("chan-n", "sess-n")
        join, box = in_thread(fb.tool_ask_user,
                              {"question": "Which host first?",
                               "options": ["host-b", "host-c"]},
                              {"session_key": "sess-n", "ask_door": door})
        check("the numbered list is posted",
              wait_for(lambda: any("**1.** host-b" in t for _, t in d.posted), 3.0),
              [t for _, t in d.posted][:2])
        d.enqueue(_FakeMsg("chan-n", "2"), "2")
        join(5)
        out = box.get("out", box.get("err", ""))
        check("the model is told the option, not just the digit",
              "OPERATOR ANSWER: host-c" in out, out[:220])
        check("and which one it read", "reads as" in out, out[:280])
        check("the operator gets the echo, so a misfire is visible",
              any("Got it: host-c" in t for _, t in d.posted),
              [t for _, t in d.posted][-2:])
    finally:
        fb.CONFIG["agent"]["ask_user"] = saved
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait


def test_the_stage_two_post_exists_and_counts_as_progress():
    """door_post is the watchdog's view of a question. The door called it but nothing
    defined it: the call raised, ask_operator swallowed it at debug level, and the
    channel recorded no activity while the run sat parked - exactly the state the stall
    watchdog abandons runs for."""
    d = dispatcher()
    check("the door has a real post hook", callable(getattr(d, "door_post", None)))
    d.active["chan-p"] = {"started": time.time(), "last": 0.0, "warned": False, "gen": 1}
    check("it answers True", d.door_post("chan-p", "q", ["a"], 30) is True)
    check("and it counts as progress",
          d.active["chan-p"]["last"] > 0, d.active["chan-p"])


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for fn in TESTS:
        if only and only not in fn.__name__:
            continue
        print(f"--- {fn.__name__}")
        fn()
    print()
    for f in FAILURES:
        print("FAILED:", f)
    print(f"{len(PASSES)} passed, {len(FAILURES)} failed, {len(SKIPPED)} skipped")
    sys.exit(1 if FAILURES else 0)
