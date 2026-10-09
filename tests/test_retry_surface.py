"""test_retry_surface - one merged suite (test_transient_retry, test_empty_stop_retry, test_reasoning_replay).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: no member needed a namespace rewrite.
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


def _suite_test_transient_retry():
    """A transient 5xx/transport failure re-asks the SAME endpoint before any failover.

Local llama.cpp/vLLM boxes answer 503 while a model loads or is swapped; demoting that to
the next endpoint silently changes the model the conversation runs on, and back-to-back
hops maximize the chance of re-tripping the same 503. Capped exponential backoff +
jitter, same model, same key, bounded, and never on 4xx.

    python tests/test_retry_surface.py
"""
    import json
    import shutil
    import sys
    import tempfile
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


    def reply(content):
        return {"choices": [{"message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3}}


    class FakeResp:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._data


    def make_stub(fb, seen, script):
        """script(i) -> FakeResp | Exception, keyed by call index (0-based)."""
        state = {"i": 0}

        def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                      stream=False):
            seen.append(url)
            i = state["i"]
            state["i"] += 1
            out = script(i)
            if isinstance(out, BaseException):
                raise out
            return out

        return fake_post


    def http_error(status, text="nope"):
        import requests
        return requests.HTTPError("stub-%s" % status,
                                  response=type("R", (), {"status_code": status,
                                                          "text": text})())


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbretry-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            fb.CONFIG["llm"]["stream"] = False
            fb.CONFIG["llm"]["retry_base_ms"] = 1
            fb.CONFIG["llm"]["retry_max_ms"] = 2
            fb.CONFIG["llm"]["retry_jitter_pct"] = 0
            msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]

            # ---- 503 then success: same endpoint, once
            seen, usage = [], {}
            real = fb._post_watchdog
            fb._post_watchdog = make_stub(fb, seen, lambda i: http_error(503) if i == 0
                                          else FakeResp(reply("ok")))
            try:
                msg = fb.AGENT._chat(list(msgs), session_key="r1", usage=usage)
            finally:
                fb._post_watchdog = real
            check(len(seen) == 2 and seen[0] == seen[1],
                  "a 503 is retried on the SAME endpoint", seen)
            check((msg.get("content") or "") == "ok", "and the retry's answer is used", msg)
            check(int(usage.get("retries") or 0) >= 1,
                  "the retry is recorded in usage", usage.get("retries"))

            # ---- bounded, then failover (0 = old immediate-failover behaviour)
            fb.CONFIG["llm"]["same_endpoint_retries"] = 0
            seen = []
            fb._post_watchdog = make_stub(fb, seen, lambda i: http_error(503))
            try:
                try:
                    fb.AGENT._chat(list(msgs), session_key="r2", usage={})
                    raised = ""
                except fb.InfraError as e:
                    raised = str(e)
            finally:
                fb._post_watchdog = real
            check(len(seen) == 1 and raised, "0 disables the retry (immediate failover)",
                  (seen, raised[:60]))

            fb.CONFIG["llm"]["same_endpoint_retries"] = 2
            seen = []
            fb._post_watchdog = make_stub(fb, seen, lambda i: http_error(500))
            try:
                try:
                    fb.AGENT._chat(list(msgs), session_key="r3", usage={})
                except fb.InfraError:
                    pass
            finally:
                fb._post_watchdog = real
            check(len(seen) == 3, "the retry budget is bounded (1 + 2 retries)", len(seen))

            # ---- a 4xx is terminal, never retried
            seen = []
            fb._post_watchdog = make_stub(fb, seen, lambda i: http_error(400, "bad request"))
            try:
                try:
                    fb.AGENT._chat(list(msgs), session_key="r4", usage={})
                except fb.InfraError:
                    pass
            finally:
                fb._post_watchdog = real
            check(len(seen) == 1, "a 400 is not retried", len(seen))

            # ---- a transport failure is retried too
            import requests
            seen = []
            fb._post_watchdog = make_stub(
                fb, seen,
                lambda i: requests.ConnectionError("Connection refused")
                if i == 0 else FakeResp(reply("recovered")))
            try:
                msg = fb.AGENT._chat(list(msgs), session_key="r5", usage={})
            finally:
                fb._post_watchdog = real
            check(len(seen) == 2 and (msg.get("content") or "") == "recovered",
                  "a refused connection is retried on the same endpoint", seen)

            # ---- the backoff formula: capped, and jitterless when asked
            check(fb._retry_backoff_ms(1, 500, 8000, 0) == 500, "attempt 1 is the base delay")
            check(fb._retry_backoff_ms(10, 500, 8000, 0) == 8000, "the delay is capped")
            jittered = fb._retry_backoff_ms(1, 500, 8000, 25)
            check(375 <= jittered <= 500, "jitter only ever shaves the delay", jittered)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print("all transient-retry checks passed")
        return 0
    return main()


def _suite_test_empty_stop_retry():
    """A clean `stop` with no content is a no-op, not an answer: re-send, don't re-turn.

The agent-level empty-answer path costs a whole extra turn - a nudge message plus a full
prompt re-read on a prefill-bound local box. The provider layer re-sends it instead:
finish_reason `stop`, no visible content, <= 1 output token, one bounded re-send of the
IDENTICAL payload.

    python tests/test_retry_surface.py
"""
    import json
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    TESTS = BASE / "tests"
    sys.path.insert(0, str(TESTS))

    import run_scenario  # noqa: E402

    FAILS = []


    def check(cond, what):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}")
        else:
            print(f"ok   {what}")


    class FakeResp:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._data


    def reply(content, finish="stop", completion_tokens=5):
        return {"choices": [{"message": {"role": "assistant", "content": content},
                             "finish_reason": finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": completion_tokens}}


    def install_stub(fb, seen, replies):
        state = {"i": 0}

        def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                      stream=False):
            seen.append(json.loads(json.dumps(payload)))
            i = state["i"]
            state["i"] += 1
            return FakeResp(replies[min(i, len(replies) - 1)])

        fb._post_watchdog = fake_post


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbempty-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            fb.CONFIG["llm"]["stream"] = False

            msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]

            # ---- an empty stop is re-sent once, unchanged, and the second answer wins
            seen = []
            install_stub(fb, seen, [reply("", completion_tokens=0),
                                    reply("the real answer")])
            msg = fb.AGENT._chat(list(msgs), session_key="empty-1", usage={})
            check(len(seen) == 2, "an empty stop completion is retried once")
            check(seen[0] == seen[1], "...with the IDENTICAL payload")
            check((msg.get("content") or "") == "the real answer",
                  "...and the answer is the second response")

            # ---- a real answer is not re-sent, and neither is a turn that spent tokens
            seen = []
            install_stub(fb, seen, [reply("done")])
            msg = fb.AGENT._chat(list(msgs), session_key="empty-2", usage={})
            check(len(seen) == 1 and msg.get("content") == "done",
                  "a normal answer costs one call")

            seen = []
            install_stub(fb, seen, [reply("", completion_tokens=40),
                                    reply("should not be asked")])
            fb.AGENT._chat(list(msgs), session_key="empty-3", usage={})
            check(len(seen) == 1, "an empty turn that spent tokens is NOT re-sent")

            # ---- bounded: the re-send itself coming back empty ends the call (no loop)
            seen = []
            install_stub(fb, seen, [reply("", completion_tokens=0),
                                    reply("", completion_tokens=0)])
            msg = fb.AGENT._chat(list(msgs), session_key="empty-4", usage={})
            check(len(seen) == 2 and not (msg.get("content") or ""),
                  "the retry is one-shot, never a loop")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print("all empty-stop retry checks passed")
        return 0
    return main()


def _suite_test_reasoning_replay():
    """Reasoning is replayed to LOCAL endpoints, and to a remote one only once the wire has
earned it - by emitting reasoning, or by a 400 that says the field must be passed back.

A llama.cpp/vLLM chat template rebuilds its <think> block from `reasoning_content`; if
the replayed turn has no trace of it, the template renders a different token sequence for
that turn and the prefix KV-cache diverges from there - on every turn, for exactly the
models that emit the most tokens. Remote providers disagree in BOTH directions (DeepSeek
400s when the field is missing, Groq when it is present), so for them the endpoint's own
behaviour decides and the verdict is remembered per endpoint - never a model-name table.
See tests/test_endpoint_surface.py for the learning rules themselves.

    python tests/test_retry_surface.py
"""
    import json
    import shutil
    import sys
    import tempfile
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


    class FakeResp:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._data


    def text_reply(text):
        return {"choices": [{"message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


    def install_stub(fb, seen, script):
        state = {"i": 0}

        def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                      stream=False):
            seen.append(json.loads(json.dumps(payload)))
            i = state["i"]
            state["i"] += 1
            return FakeResp(script(i, payload))

        fb._post_watchdog = fake_post


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbreplay-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            fb.CONFIG["llm"]["stream"] = False
            THINK = "I should list the tools first. " * 20

            # ---- the gate: local only, and switchable
            check(fb._replay_reasoning_ok(), "a loopback endpoint gets replay by default")
            check(not fb._replay_reasoning_ok("https://api.example.com/v1"),
                  "a remote endpoint does not")
            fb.CONFIG["llm"]["replay_reasoning"] = False
            check(not fb._replay_reasoning_ok(), "the switch turns it off")
            fb.CONFIG["llm"]["replay_reasoning"] = True

            fb.CONFIG["llm"]["replay_reasoning_max_chars"] = 100
            capped = fb._reasoning_for_replay({"reasoning_content": "x" * 500})
            check(len(capped) == 100, "the replay text is capped", len(capped))

            # ---- end to end: the tool-call turn carries the reasoning into the next payload
            def script(i, payload):
                if i == 0:
                    return {"choices": [{"message": {
                        "role": "assistant", "content": "",
                        "reasoning_content": THINK,
                        "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "list_tools",
                                                     "arguments": "{}"}}]},
                        "finish_reason": "tool_calls"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
                return text_reply("done")

            seen = []
            install_stub(fb, seen, script)
            fb.AGENT.run("replay-local", "list the tools")
            turns = [m for m in seen[-1]["messages"]
                     if m.get("role") == "assistant" and m.get("tool_calls")]
            check(bool(turns) and turns[0].get("reasoning_content") == THINK[:100],
                  "the replayed tool-call turn carries the capped reasoning",
                  turns[:1])
            check(len(seen[-1]["messages"]) >= 3,
                  "the payload kept the full turn sequence", len(seen[-1]["messages"]))

            # ---- switch off: the field is gone
            fb.CONFIG["llm"]["replay_reasoning"] = False
            seen = []
            install_stub(fb, seen, script)
            fb.AGENT.run("replay-off", "list the tools")
            bad = [m for m in seen[-1]["messages"]
                   if m.get("role") == "assistant" and m.get("reasoning_content")]
            check(not bad, "with replay off no assistant turn carries reasoning_content", bad)
            fb.CONFIG["llm"]["replay_reasoning"] = True

            # ---- a remote endpoint that has never emitted reasoning gets no field
            _saved_urls = (fb.CONFIG["llm"]["base_url"], fb.AGENT.llm_url)
            fb.CONFIG["llm"]["base_url"] = "https://silent.example.com/v1"
            fb.AGENT.llm_url = "https://silent.example.com/v1/chat/completions"
            seen = []

            def quiet(i, payload):
                if i == 0:
                    return {"choices": [{"message": {
                        "role": "assistant", "content": "",
                        "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "list_tools",
                                                     "arguments": "{}"}}]},
                        "finish_reason": "tool_calls"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
                return text_reply("done")

            install_stub(fb, seen, quiet)
            fb.AGENT.run("replay-silent-remote", "list the tools")
            bad = [m for m in seen[-1]["messages"]
                   if m.get("role") == "assistant" and m.get("reasoning_content")]
            check(not bad, "an off-LAN endpoint that never emitted reasoning gets no field",
                  bad)

            # ...but one that DID emit it has told the harness what it takes, and gets it
            # back on the next request (no model was named anywhere in that decision).
            fb.CONFIG["llm"]["base_url"] = "https://api.example.com/v1"
            fb.AGENT.llm_url = "https://api.example.com/v1/chat/completions"
            seen = []
            install_stub(fb, seen, script)
            fb.AGENT.run("replay-remote-learned", "list the tools")
            turns = [m for m in seen[-1]["messages"]
                     if m.get("role") == "assistant" and m.get("reasoning_content")]
            check(bool(turns) and turns[0].get("reasoning_content") == THINK[:100],
                  "...but one that emitted reasoning gets it back, learned per endpoint",
                  turns[:1])
            check(fb._replay_reasoning_ok("https://api.example.com/v1") is True,
                  "...and the fact is remembered for the next request")
            fb.CONFIG["llm"]["base_url"], fb.AGENT.llm_url = _saved_urls
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print("all reasoning-replay checks passed")
        return 0
    return main()


def main():
    rc = 0
    for name, fn in (("test_transient_retry", _suite_test_transient_retry), ("test_empty_stop_retry", _suite_test_empty_stop_retry), ("test_reasoning_replay", _suite_test_reasoning_replay)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
