"""test_interaction_surface - one merged suite (test_ask_user, test_cli_prompt, test_stop_now, test_tinycmdr_prefix).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: test_ask_user: globals()-> _ns; test_cli_prompt: globals()-> _ns; test_stop_now: globals()-> _ns; test_tinycmdr_prefix: globals()-> _ns.
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


def _suite_test_ask_user():
    """ask_user: a run that needs the operator's decision STOPS and asks.

Run:  python tests/test_interaction_surface.py            (all tests)
      python tests/test_interaction_surface.py <substring> (one test)

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
and no durable state is touched.
"""
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    import threading
    import time
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

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

    # This suite is about dedup, the wait cap, the one-option rule and /stop. The timeout
    # semantics have their own test, which pops this key itself (review fix, 2026-09-21).
    fb.CONFIG["agent"]["ask_timeout_continues"] = True

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
            # Every test gets a FRESH box. The dispatcher carries the ids of the posts
            # it has handled in state.json (so a message posted during a downtime is not
            # replayed as new work), and this suite reuses one synthetic id for every fake
            # message - a shared stage file would drop the next test's message as a
            # duplicate. The suite's environment is not a clean one.
            (STAGE / "state.json").unlink(missing_ok=True)
            super().__init__()
            self.posted = []

        def _post(self, channel_id, root_id, text, color=None, draft_id=None):
            if draft_id:
                return draft_id        # the real door edits the run's own draft in place
            self.posted.append((channel_id, text))
            self._touch(channel_id)
            return "post-%d" % len(self.posted)

        def _handle(self, channel_id, sender, text, msg_id, thread_root, is_dm, gen=0):
            pass            # never let a test worker reach the real agent


    class _FakeMsg:
        def __init__(self, channel_id, text, sender="alice", mid="m1"):
            self.sender_name = sender
            self.channel_id = channel_id
            self.text = text
            self.create_at = time.time() * 1000
            self.id = mid
            self.root_id = ""
            self.user_id = "alice-id"
            self.is_direct_message = True


    def dispatcher():
        d = FakeDispatcher()
        fb.CONFIG["mattermost"]["allowed_users"] = ["alice", "alice-id"]
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


    def test_a_scheduled_jobs_question_comes_back_from_its_channel():
        """A job's ask_user posts into its channel and promises "Answer here" - but the row
    was filed only in SCHEDULER.pending_asks, while the listener that turns a channel
    message into an answer reads the DISPATCHER's rows (keyed by channel). Measured
    2026-10-07: reply_ask answered False, the job's wait expired, and it carried on with
    its OWN judgment on exactly the decision it had asked about."""
        saved = fb.CONFIG["agent"].get("ask_user")
        saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
        fb.CONFIG["agent"]["ask_user"] = True
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = 600
        d = dispatcher()
        saved_disp, saved_reporter = fb.SCHEDULER.dispatcher, fb.REPORTER
        fb.SCHEDULER.dispatcher = d
        fb.REPORTER = lambda cid, text: d._post(cid, None, text)
        chan, key = "chan-job", "sched-nightly"
        # The door _run_job builds for a job that has a reporting channel.
        door = {"label": "answer in this channel",
                "opener": lambda q, opts, w, l: fb.SCHEDULER._open_in_channel(
                    chan, key, q, opts, w),
                "post": lambda q, opts, w, l: True,
                "post_done": lambda text: None,
                "close_question": lambda answered: fb.SCHEDULER.close_question(key,
                                                                              answered)}
        try:
            join, box = in_thread(fb.tool_ask_user, {"question": "Restart prod or wait?"},
                                  {"session_key": key, "ask_door": door})
            check("the job posts its question into its channel",
                  wait_for(lambda: any("Restart prod" in t for _, t in d.posted), 3.0),
                  d.posted)
            check("the row sits where the listener reads answers",
                  d.pending_asks.get(chan) is not None, list(d.pending_asks))
            d.enqueue(_FakeMsg(chan, "wait"), "wait")
            join(5)
            out = box.get("out", box.get("err", ""))
            check("the job resumes with the operator's answer, not its own judgment",
                  "OPERATOR ANSWER: wait" in out, out[:200])
            q = d.queues.get(chan)
            check("...and the reply did not also land as a new run",
                  q is None or q.empty(), "queue size %s" % (q.qsize() if q else 0))
            check("...and the channel's slot is free again",
                  d.pending_asks.get(chan) is None, list(d.pending_asks))

            # One reply serves one question: while a CHAT run is waiting in that channel, the
            # job must not take the slot or post a question that reply cannot serve.
            chat_door = d.ask_door_factory(chan, "sess-chat")
            chat_door["opener"]("chat question?", [], 5, "reply here")
            posted = len(d.posted)
            refused = fb.SCHEDULER._open_in_channel(chan, "sched-other", "mine?", [], 5)
            check("a job does not take a channel whose question is still waiting",
                  refused is None and len(d.posted) == posted, (refused, d.posted[posted:]))
            check("...and the waiting question is untouched",
                  d.pending_asks.get(chan) is not None, list(d.pending_asks))
            d.reply_ask("sess-chat", "the chat answer")
        finally:
            fb.SCHEDULER.dispatcher, fb.REPORTER = saved_disp, saved_reporter
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


    def test_an_unanswered_question_stops_the_run():
        """The review's finding, 2026-09-21: a run must not invent the operator's intent for
    the very decisions that get asked about. In the campaign that was an unapproved
    production restart, and then an outage."""
        saved = fb.CONFIG["agent"].get("ask_user")
        saved_wait = fb.CONFIG["agent"].get("ask_user_wait_seconds")
        fb.CONFIG["agent"]["ask_user"] = True
        fb.CONFIG["agent"]["ask_user_wait_seconds"] = 6      # the whole point: bounded
        fb.CONFIG["agent"].pop("ask_timeout_continues", None)
        d = dispatcher()
        try:
            door = d.ask_door_factory("chan-t", "sess-t")
            t0 = time.time()
            raised, out = None, ""
            try:
                out = fb.tool_ask_user({"question": "Which of the two?",
                                        "options": ["a", "b"]},
                                       {"session_key": "sess-t", "ask_door": door})
            except fb.OperatorStop as e:
                raised = e
            dt = time.time() - t0
            check("the wait is bounded by the config", 5.0 < dt < 12.0, "%.1fs" % dt)
            check("an unanswered question STOPS the run instead of guessing",
                  raised is not None, out[:160])
            check("and the stop says why", "nobody answered" in str(raised), str(raised)[:120])
            check("the operator is told what happened",
                  any("No answer" in t for _, t in d.posted), [t for _, t in d.posted][-2:])
            # The stop costs CONTEXT, and that is what the next run paid for on the live box
            # (2026-09-29): it spent its first calls re-deriving the task out of its own session
            # files. The question is kept durably and surfaced in the block the next run reads -
            # ONCE. Measured 2026-10-03: a reminder that repeats made five consecutive runs
            # restate the same assumption and pay the tokens each time.
            _shown = fb.volatile_context(session_key="sess-t")
            check("the unanswered question rides the trailing block, so the task is not re-derived",
                  "Which of the two?" in _shown, _shown[-200:])
            check("...and reading the block does not take delivery (the budget measurement "
                  "reads it before the payload does)",
                  fb._question_path("sess-t").exists(),
                  "the block read consumed the sidecar")
            check("...it is handed to exactly ONE consumer: the consuming read empties the sidecar",
                  "Which of the two?" in fb.open_question("sess-t")
                  and not fb._question_path("sess-t").exists(),
                  fb.open_question("sess-t")[:140])
            # A question nobody came back to is stale, not eternal: past the TTL it is dropped
            # rather than surfaced, because by then the work has moved on.
            _qp = fb._question_path("sess-t")
            fb._ensure_sessions_dir()
            _qp.write_text(json.dumps({"question": "still relevant?", "at": time.time() - 25 * 3600}),
                           encoding="utf-8")
            check("a stale question is dropped, not surfaced",
                  fb.open_question("sess-t") == "" and not _qp.exists(), fb.open_question("sess-t")[:140])
            # the old shape stays available per box, deliberately, and only by config
            fb.CONFIG["agent"]["ask_timeout_continues"] = True
            out = fb.tool_ask_user({"question": "Which of the two?", "options": ["a", "b"]},
                                   {"session_key": "sess-t", "ask_door": door})
            check("ask_timeout_continues hands the decision back, as it used to",
                  "NO ANSWER" in out and "assumption" in out, out[:200])
            check("  and settles it, so the next run is not nagged about a question the model "
                  "was told to decide itself",
                  fb.open_question("sess-t") == "", fb.open_question("sess-t")[:140])
        finally:
            fb.clear_open_question("sess-t")
            fb.CONFIG["agent"]["ask_user"] = saved
            fb.CONFIG["agent"]["ask_user_wait_seconds"] = saved_wait
            fb.CONFIG["agent"]["ask_timeout_continues"] = True   # the suite's baseline


    def test_the_budget_measurement_does_not_eat_the_parked_question():
        """A-2026-10-08-152: the run loop's first step, `_compact`, always runs its budget
    measurement, and that measurement's volatile_context() read consumed the sidecar
    before `_payload` built the prompt - so the question the feature parks for the next
    run never reached any payload at all."""
        fb.set_open_question("sess-step", "Which of the two?", ["a", "b"])
        try:
            messages = [{"role": "user", "content": "carry on"}]
            fb.AGENT._compact(messages, "sess-step")          # the run loop's first step
            check("the measurement weighs the parked question without consuming it",
                  fb._question_path("sess-step").exists(),
                  "the measurement ate the sidecar")
            payload = fb.AGENT._payload(messages, session_key="sess-step")
            blob = "\n".join(str(m.get("content") or "") for m in payload)
            check("the payload the model receives carries the question",
                  "Which of the two?" in blob, blob[-300:])
            check("...and building the payload is what consumes it",
                  not fb._question_path("sess-step").exists())
        finally:
            fb.clear_open_question("sess-step")


    def test_new_does_not_inherit_a_parked_question():
        """A-2026-10-08-83: a question parked by a stopped run belongs to the conversation it
    was asked in; /new abandons that conversation, so the sidecar goes with it."""
        fb.set_open_question("sess-fresh", "Which of the two?", ["a", "b"])
        check("the question is parked", fb._question_path("sess-fresh").exists())
        fb.AGENT.reset("sess-fresh")
        check("reset drops the parked question",
              not fb._question_path("sess-fresh").exists(),
              "the sidecar survived /new")
        check("...so the fresh conversation's block does not carry it",
              "Which of the two?" not in fb.volatile_context(session_key="sess-fresh"))


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
        """ask_operator is called by the tool and the scheduler alike. A door
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
            d.active["chan-w"] = {"started": fb.now_mono() - 3600,
                                  "last": fb.now_mono() - 3000,
                                  "warned": False, "gen": 1}
            door = d.ask_door_factory("chan-w", "sess-w")
            in_thread(fb.tool_ask_user, {"question": "quick?"},
                      {"session_key": "sess-w", "ask_door": door})
            check("the question post counts as progress",
                  wait_for(lambda: d.active["chan-w"]["last"] > fb.now_mono() - 5, 3.0),
                  d.active["chan-w"])
            # and the watchdog, run at that instant, must not abandon the channel
            before = len(d.posted)
            d._stall_tick(now=fb.now_mono())
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
        d.active["chan-p"] = {"started": fb.now_mono(), "last": 0.0, "warned": False,
                              "gen": 1}
        check("it answers True", d.door_post("chan-p", "q", ["a"], 30) is True)
        check("and it counts as progress",
              d.active["chan-p"]["last"] > 0, d.active["chan-p"])


    _ns = dict(locals())
    TESTS = [v for k, v in sorted(_ns.items()) if k.startswith("test_")]

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


def _suite_test_cli_prompt():
    """A question at the console must reach the reader, not race it.

Operator, 2026-09-22: "the cli version ... asks the user for an answer ask_user and the
cmd window freezes and doesnt accept any input at all".

What happened: `_cli_reader` owns stdin for the whole interactive session, and the
question read the terminal itself (`input()`), so the reader took the typed line, filed
it as steering, and the run waited for ever - the window looked frozen and accepted
nothing. The door was not even wired for ask_user there (`drive_run` got no ask_door),
so the tool answered "nothing in this run can reach a human" while a chat lane could
still ask.

What this pins:
  * the answer reaches the question, and the typed line does NOT become steering
  * ask_user works at the console at all (it used to be refused, not parked)
  * a question is closed again afterwards (no box left open to eat the next request)
  * /stop while a question is open releases the run as a STOP, not a timeout
  * `--once` has no reader thread, so a question there reads stdin itself

    python tests/test_interaction_surface.py            (all checks)
    python tests/test_interaction_surface.py <substring>

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


    def test_5_options_are_numbered_at_the_prompt():
        """A-279 (2026-10-06): cli_ask_print printed the options as prose
        (`reply yes / no / session / always`) with no numbers, while every other
        lane numbered the same question and resolves a bare number."""
        out = io.StringIO()
        fb.cli_ask_print("Allow `rm`?", ["yes", "no", "session", "always"],
                         out=out, colour=False)
        text = out.getvalue()
        check("the options are numbered", "1. yes" in text and "4. always" in text,
              text)
        check("a number is offered as an answer", "number" in text, text)


    def test_a_launch_opens_a_fresh_conversation():
        """Typing `tinycmdr` means START: the console must not resume yesterday's transcript.

    `cli` conversation, so a new question was answered about the previous run's ledger and
    the order census counted a test question across all of them. Older conversations stay
    on disk, are listed by `/tinycmdr sessions`, and are resumed explicitly.
    """
        first = fb._cli_startup_session([])
        second = fb._cli_startup_session([])
        check("a fresh launch gets a fresh conversation key",
              first != second and first.startswith("cli-") and first != "cli", (first, second))
        check("--session names one exactly", fb._cli_startup_session(["--session", "work"]) == "work")
        check("--session=NAME works too", fb._cli_startup_session(["--session=work2"]) == "work2")
        os.environ["TINYCMDR_SESSION"] = "from-env"
        try:
            check("TINYCMDR_SESSION names one from the environment",
                  fb._cli_startup_session([]) == "from-env")
        finally:
            os.environ.pop("TINYCMDR_SESSION", None)
        # `--continue` resumes the newest saved conversation
        import json as _json
        _d = fb.SESSIONS_DIR
        _d.mkdir(parents=True, exist_ok=True)
        _old = _d / "cli-old.json"
        _new = _d / "cli-new.json"
        _old.write_text(_json.dumps([{"role": "user", "content": "old"}]), encoding="utf-8")
        _new.write_text(_json.dumps([{"role": "user", "content": "new"}]), encoding="utf-8")
        _now = time.time()
        os.utime(_old, (_now - 600, _now - 600))
        os.utime(_new, (_now, _now))
        try:
            check("--continue resumes the newest conversation",
                  fb._cli_startup_session(["--continue"]) == "cli-new",
                  fb._cli_startup_session(["--continue"]))
        finally:
            _old.unlink(missing_ok=True)
            _new.unlink(missing_ok=True)


    def main():
        tests = [v for k, v in sorted(_ns.items())
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
    _ns = dict(locals())
    return main()


def _suite_test_stop_now():
    """`/stop` has to stop everything, immediately.

Run:  python tests/test_interaction_surface.py            (all tests)
      python tests/test_interaction_surface.py <substring> (one test)

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
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-stop"
    STAGE.mkdir(parents=True, exist_ok=True)
    # The dispatcher now CARRIES the ids of the posts it has handled across a restart
    # (state.json, so a message posted during a downtime is not replayed as new work).
    # A suite is not a fresh box: clear the staged copy, or the next dispatcher in this
    # run inherits the previous test's ids and drops its messages as duplicates.
    (STAGE / "state.json").unlink(missing_ok=True)
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
        src = (BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")).read_text(
            encoding="utf-8", errors="replace")
        check("the run hands its cancel event to the tools",
              '"cancel_event": cancel_event,' in src
              and 'cancel=(ctx or {}).get("cancel_event")' in src)
        check("both shell and execute_code are wired",
              src.count('cancel=(ctx or {}).get("cancel_event")') == 2,
              f"found {src.count('cancel=(ctx or {}).get(chr(34)+chr(34))')}")


    # --- 6: the listener thread answers /stop and /restart force ------------------

    class FakeMessage:
        def __init__(self, text, sender="alice", channel="c1", mid=None):
            self.sender_name = sender
            self.channel_id = channel
            self.create_at = int(time.time() * 1000)
            self.id = mid or f"m{time.time()}"
            self.user_id = "u1"
            self.root_id = ""
            self.is_direct_message = True


    def make_dispatcher():
        # A FRESH box per dispatcher: the dispatcher carries the ids of the posts it has
        # handled in state.json (so a message posted during a downtime is not replayed as
        # new work), and this suite's staged copy is shared by every test in the run.
        (STAGE / "state.json").unlink(missing_ok=True)
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


    def with_allowed(sender="alice"):
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



    def test_a_stop_reaches_a_sub_agent():
        """A /stop must stop the SUB-AGENTS, not only the run parked on them.

    three sub-agents kept writing files. The parent was parked inside delegate_task waiting
    for them, and the sub-agents had been handed no cancel event at all - so the operator's
    stop had nothing to reach, and the parent could not act until every subtask had finished
    on its own.
    """
        ev = threading.Event()
        ev.set()                              # the operator's stop, already sent
        t0 = time.time()
        out = fb.tool_delegate_task(
            {"task": "Say the word ok. This is a cancellation test."},
            {"depth": 0, "cancel_event": ev, "session_key": "mm-test", "report": {}})
        dt = time.time() - t0
        check("delegate: a stopped sub-agent answers with the stop", "Stopped" in out,
              out[:160])
        # Without the event the sub-agent would attempt a MODEL CALL here to begin the work
        # (there is no endpoint in a suite, so it would spend its retry budget first). An
        # instant return is the proof the stop arrived before any work began.
        check("  and it never started work", dt < 5.0, f"took {dt:.1f}s")

    def main():
        tests = [v for k, v in sorted(_ns.items())
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
    _ns = dict(locals())
    return main()


def _suite_test_tinycmdr_prefix():
    """`/tinycmdr <verb>`: the one command trigger that always gets through.

Why this exists: a chat client never sends a message that starts with "/" unless it
matches a REGISTERED custom command, and the server refuses to register its own
trigger words - so `/help` and `/status` can never reach a bot, and a bare `/model`
only arrives because the relay has a row for it. `/tinycmdr` is one registered
trigger that carries anything, and the build accepts it in every lane it serves
(chat, Telegram DM, console) while the bare words keep working: the
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
  * the console lane

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
        d._handle("c1", "alice", "/cmdr status", "m9", "m9", True)
        text = " ".join(posted)
        check("chat answers a /cmdr line with the new prefix",
              "/tinycmdr" in text and "Unknown command" not in text, text[:200])
        check("...and does not send it to the model", not d.queues.get("c1"), d.queues)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fb._cli_command("/cmdr status")
        check("the console answers it the same way",
              "/tinycmdr" in out.getvalue(), out.getvalue()[:160])


    # --- the chat handler -------------------------------------------------------

    def make_dispatcher():
        # A FRESH box per dispatcher: the dispatcher carries the ids of the posts it has
        # handled in state.json (so a message posted during a downtime is not replayed as
        # new work), and this suite's staged copy is shared by every test in the run.
        (STAGE / "state.json").unlink(missing_ok=True)
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
        d._handle("c1", "alice", "/tinycmdr new", "m1", "m1", True)
        check("/tinycmdr new clears the session locally",
              any("Session cleared" in p for p in posted), posted)
        check("/tinycmdr new did not queue a task", not d.queues.get("c1"), d.queues)

        d2, posted2 = make_dispatcher()
        d2._handle("c1", "alice", "/tinycmdr help", "m2", "m2", True)
        text = " ".join(posted2)
        check("/tinycmdr help answers with the command list",
              "**Commands**" in text and "/tinycmdr help" in text, text[:200])
        check("/tinycmdr help queued nothing", not d2.queues.get("c1"), d2.queues)

        d3, posted3 = make_dispatcher()
        d3._handle("c1", "alice", "/tinycmdr wibble", "m3", "m3", True)
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
        fb.CONFIG["mattermost"]["allowed_users"] = ["alice"]
        try:
            class FakeMessage:
                def __init__(self, text, mid):
                    self.sender_name = "alice"
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

        # A management verb the session cannot run gets the door that has it, not "not a
        # command": the operator asked how to update, typed /update here, and the list it named
        # has no update verb in it (2026-09-30).
        # THE UPDATE RULE (2026-10-03): `/update` in a session RUNS the update - it does not
        # print a hint about a shell command ("... instead of dead-ending", 2026-09-30) and it
        # does not leave the old bytes running silently. The verb is STUBBED: this test grades
        # the door, not the network (unstubbed it fetched the release inside the suite).
        _saved_run_verb = fb.run_verb
        _called = []
        fb.run_verb = lambda argv: (_called.append(list(argv)) or 0)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                fb._cli_command("/tinycmdr update")
            said = out.getvalue()
        finally:
            fb.run_verb = _saved_run_verb
        check("/update in a session RUNS the update verb",
              _called and _called[0][:1] == ["update"], (_called, said[:160]))
        check("...and says how this session gets the new bytes",
              "relaunch" in said and "restart" in said, said[:200])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fb._cli_command("/tinycmdr doctor")
        said = out.getvalue()
        check("...and another shell-only verb names itself the same way",
              "not a command" not in said and "tinycmdr doctor" in said, said[:200])

        check("the console help text documents the prefix",
              "/tinycmdr help" in fb.HELP_TEXT, fb.HELP_TEXT[:80])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fb._cli_while_running("/tinycmdr wibble")
        check("a prefixed verb is understood by the mid-run reader",
              "not a command" not in out.getvalue(), out.getvalue()[:120])


    def main():
        tests = [v for k, v in sorted(_ns.items())
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
    _ns = dict(locals())
    return main()


def main():
    rc = 0
    for name, fn in (("test_ask_user", _suite_test_ask_user), ("test_cli_prompt", _suite_test_cli_prompt), ("test_stop_now", _suite_test_stop_now), ("test_tinycmdr_prefix", _suite_test_tinycmdr_prefix)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
