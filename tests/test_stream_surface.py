"""test_stream_surface - one merged suite (test_stream_calls, test_stream_integrity, test_responses_wire).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: test_stream_calls: globals()-> _ns.
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


def _suite_test_stream_calls():
    """The turn engine on a slow, flaky box: prefill vs idle, torn streams, tool-call merging.

Every check here failed at HEAD:

  * the idle timer covered the PREFILL, so a healthy long prompt was declared wedged
    (the first byte waits on the model, not on the stream);
  * a stream that died after the first delta was handed back as the model's complete
    answer, because the error check only fired when nothing had arrived at all;
  * index-less tool-call fragments were all keyed on 0, MERGING two distinct calls
    (a run that asked for `echo A` and `echo B` executed `echo AB`), and a byte-identical
    repeated fragment was appended twice;
  * `_call_sig` truncated canonical args at 400 chars, so two calls differing only
    after that collapsed onto one signature;
  * a 400 naming `stream_options` was classified FATAL, so a cosmetic provider
    difference killed the run;
  * the privacy pin missed the primary, and `127.1` / `[::1]` / a LAN hostname
    were classified as off-LAN;
  * an OperatorStop raised during TOOL execution escaped `run()`.

No network: the SSE bodies are fake responses, and the transport is stubbed the way the
other suites stub it.

    python tests/test_stream_surface.py
"""
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    import threading
    import time
    import types
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-stream"
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
                 STAGE / "config.json")
    spec = importlib.util.spec_from_file_location("tinycmdr_stream", STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_stream"] = fb
    spec.loader.exec_module(fb)

    FAILURES = []


    def check(name, cond, detail=""):
        if cond:
            print(f"ok   {name}")
        else:
            FAILURES.append(name)
            print(f"FAIL {name}: {detail}")


    class FakeResp:
        """A streaming response: `script` is a list of (delay, line-or-Exception)."""

        def __init__(self, script):
            self.script = script
            self.raw = types.SimpleNamespace()
            self.closed = False

        def iter_lines(self, decode_unicode=False):
            for item in self.script:
                delay, value = item
                if delay:
                    time.sleep(delay)
                if isinstance(value, BaseException):
                    raise value
                yield value.encode() if isinstance(value, str) else value

        def close(self):
            self.closed = True

        def raise_for_status(self):
            return None


    def sse(*chunks):
        """(delay, line) pairs, the order FakeResp.iter_lines consumes. No terminator:
    append SSE_END where the stream is supposed to finish cleanly."""
        return [(0.0, "data: " + json.dumps(c)) for c in chunks]


    SSE_END = (0.0, "data: [DONE]")


    def delta(content=None, tool_calls=None, finish=None):
        d = {}
        if content is not None:
            d["content"] = content
        if tool_calls is not None:
            d["tool_calls"] = tool_calls
        ch = {"index": 0, "delta": d, "finish_reason": finish}
        return {"choices": [ch]}


    def run_stream(script, **kw):
        return fb._stream_chat(FakeResp(script), **kw)


    # ---------------------------------------------------------------- a slow prefill is not an idle gap
    def test_prefill_is_not_an_idle_gap():
        """A first byte that takes longer than the idle gap is a slow PREFILL, not a wedge."""
        script = [(0.6, ": prefill wait")] + sse(delta(content="hello"), delta(finish="stop")) + [SSE_END]
        try:
            data, stats = run_stream(script, idle_seconds=0.3, first_byte_seconds=5)
        except Exception as e:                                  # noqa: BLE001
            check("a slow prefill is not declared wedged", False,
                  "%s: %s" % (type(e).__name__, e))
            return
        check("a slow prefill is not declared wedged",
              data["choices"][0]["message"]["content"] == "hello",
              data["choices"][0]["message"])
        check("...and the delta was counted", stats["deltas"] >= 1, stats["deltas"])


    def test_no_first_byte_is_reported_as_a_prefill_failure():
        try:
            run_stream([(3.0, "data: [DONE]")], idle_seconds=0.3, first_byte_seconds=0.5)
            check("a first byte that never comes raises StreamFailed", False, "returned")
        except fb.StreamFailed as e:
            check("a first byte that never comes raises StreamFailed",
                  "prefill" in str(e), e)
        except Exception as e:                                  # noqa: BLE001
            check("a first byte that never comes raises StreamFailed", False,
                  "%s: %s" % (type(e).__name__, e))


    def test_a_quiet_stream_after_the_first_delta_is_wedged():
        script = sse(delta(content="start")) + [(0.7, ": idle gap")] + \
                 sse(delta(content="more"), delta(finish="stop")) + [SSE_END]
        try:
            run_stream(script, idle_seconds=0.3, first_byte_seconds=5)
            check("a quiet gap BETWEEN chunks is still an idle failure", False, "returned")
        except fb.StreamFailed as e:
            check("a quiet gap BETWEEN chunks is still an idle failure",
                  "quiet" in str(e), e)
        except Exception as e:                                  # noqa: BLE001
            check("a quiet gap BETWEEN chunks is still an idle failure", False,
                  "%s: %s" % (type(e).__name__, e))


    # ---------------------------------------------------------------- the server said it failed
    def test_an_sse_error_event_is_a_failure_not_an_answer():
        """llama.cpp sends `error: {...}` then [DONE]; vLLM sends data: {"error": ...}
        then [DONE]. The [DONE] sets terminal and saw_done, so without reading the
        error the partial text passes every terminal check and comes back as the
        model's answer."""
        for label, line, text in (
                ("llama.cpp", 'error: {"message": "failed to decode, OOM"}', "OOM"),
                ("vLLM", 'data: {"error": {"message": "engine died"}}', "engine died")):
            script = sse(delta(content="partial answer")) + [(0.0, line), SSE_END]
            try:
                data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
                check("an SSE error is a failure, not the answer (%s)" % label,
                      False, data["choices"][0]["message"])
            except fb.StreamFailed as e:
                check("an SSE error is a failure, not the answer (%s)" % label,
                      "reported an error mid-stream" in str(e) and text in str(e)
                      and not e.not_a_stream, e)
            except Exception as e:                              # noqa: BLE001
                check("an SSE error is a failure, not the answer (%s)" % label, False,
                      "%s: %s" % (type(e).__name__, e))

    def test_an_error_only_stream_is_not_mistaken_for_not_a_stream():
        script = [(0.0, 'error: {"message": "no slot available"}'), SSE_END]
        try:
            run_stream(script, idle_seconds=5, first_byte_seconds=5)
            check("an error-only stream is a stream error, not 'not a stream'",
                  False, "returned")
        except fb.StreamFailed as e:
            check("an error-only stream is a stream error, not 'not a stream'",
                  "no slot available" in str(e) and not e.not_a_stream, e)
        except Exception as e:                                  # noqa: BLE001
            check("an error-only stream is a stream error, not 'not a stream'",
                  False, "%s: %s" % (type(e).__name__, e))

    # ---------------------------------------------------------------- keep-alive pings are not output
    def test_keepalive_pings_do_not_extend_the_idle_bound():
        """A wedged generation that still pings must trip the idle bound: the pings
        prove the socket is alive, not that the model is writing. They arrive faster
        than the queue timeout, so the old check (only reached when the queue went
        empty for 0.25s) never ran."""
        script = sse(delta(content="start")) + [(0.1, ": ping") for _ in range(12)]
        try:
            run_stream(script, idle_seconds=0.3, first_byte_seconds=5)
            check("a pinging wedge is still an idle failure", False, "returned")
        except fb.StreamFailed as e:
            check("a pinging wedge is still an idle failure", "quiet" in str(e), e)
        except Exception as e:                                  # noqa: BLE001
            check("a pinging wedge is still an idle failure", False,
                  "%s: %s" % (type(e).__name__, e))


    # ---------------------------------------------------------------- a torn stream is not the answer
    def test_a_torn_stream_is_not_the_answer():
        script = sse(delta(content="partial answer")) + \
            [(0.0, ConnectionError("connection reset by peer"))]
        try:
            data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
            check("a stream that breaks mid-answer is not handed back as the answer",
                  False, data["choices"][0]["message"])
        except fb.StreamFailed as e:
            check("a stream that breaks mid-answer is not handed back as the answer",
                  "broke before it finished" in str(e), e)
        except Exception as e:                                  # noqa: BLE001
            check("a stream that breaks mid-answer is not handed back as the answer",
                  False, "%s: %s" % (type(e).__name__, e))


    def test_a_stream_that_finished_before_the_break_is_kept():
        script = sse(delta(content="done"), delta(finish="stop")) + \
            [(0.0, ConnectionError("reset after the terminal chunk"))]
        try:
            data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
            check("a break AFTER a terminal chunk keeps the answer",
                  data["choices"][0]["message"]["content"] == "done",
                  data["choices"][0]["message"])
        except Exception as e:                                  # noqa: BLE001
            check("a break AFTER a terminal chunk keeps the answer", False,
                  "%s: %s" % (type(e).__name__, e))


    # ---------------------------------------------------------------- two index-less calls stay two calls
    def test_two_indexless_calls_stay_two_calls():
        script = sse(
            delta(tool_calls=[{"id": "call_a", "type": "function",
                               "function": {"name": "shell", "arguments": ""}}]),
            delta(tool_calls=[{"function": {"arguments": '{"command": "echo A"}'}}]),
            delta(tool_calls=[{"id": "call_b", "type": "function",
                               "function": {"name": "shell", "arguments": ""}}]),
            delta(tool_calls=[{"function": {"arguments": '{"command": "echo B"}'}}]),
            delta(tool_calls=[{"function": {"arguments": '{"command": "echo B"}'}}]),
            delta(finish="tool_calls")) + [SSE_END]
        data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        calls = data["choices"][0]["message"].get("tool_calls") or []
        check("two index-less calls stay TWO calls", len(calls) == 2,
              [c.get("function", {}).get("arguments") for c in calls])
        if len(calls) == 2:
            args = [c["function"]["arguments"] for c in calls]
            check("the first call keeps its own arguments",
                  'echo A' in args[0] and 'echo B' not in args[0], args)
            check("the second call keeps its own arguments",
                  'echo B' in args[1], args)
            check("a byte-identical repeated fragment is not appended twice",
                  args[1].count("echo B") == 1, args[1])


    def test_repeated_fragments_inside_one_call_are_kept():
        """The resend guard used to drop the SECOND and later copy of any fragment ever seen
    in a call, which deleted real punctuation before JSON was parsed: `grep -nE` ->
    `grepnE`, `head -5` -> `head5`, `/tmp/x, /tmp/y` -> `/tmp/x,/y`, `a, b, c` -> `a, b c`.
    while the raw SSE carried them correctly. Every fragment below is one llama.cpp
    actually sends for this command."""
        cmd = 'ls -la /tmp/alpha, /tmp/beta | grep -nE "x-y" | head -5'
        frags = ['{', '"command":"', 'ls', ' -', 'la', ' /', 'tmp', '/alpha', ',', ' /',
                 'tmp', '/beta', ' |', ' grep', ' -', 'n', 'E', ' \\"', 'x', '-', 'y',
                 '\\"', ' |', ' head', ' -', '5', '"', '}']
        chunks = [delta(tool_calls=[{"index": 0, "id": "c1", "type": "function",
                                     "function": {"name": "shell", "arguments": ""}}])]
        chunks += [delta(tool_calls=[{"index": 0, "function": {"arguments": f}}])
                   for f in frags]
        chunks.append(delta(finish="tool_calls"))
        data, _ = run_stream(sse(*chunks) + [SSE_END], idle_seconds=5, first_byte_seconds=5)
        calls = data["choices"][0]["message"].get("tool_calls") or []
        check("repeated fragments stay in the arguments", len(calls) == 1, calls)
        if calls:
            got = calls[0]["function"]["arguments"]
            check("...and the command arrives byte-for-byte",
                  json.loads(got)["command"] == cmd, got)


    def test_a_re_emitted_call_replaces_instead_of_doubling():
        """The resend the guard exists for: the endpoint finishes a tool call and emits the
    whole call again from the top. Appending that twice produced "echo hiecho hi"; the
    first copy must be replaced, name and all, not concatenated."""
        def emission():
            return [delta(tool_calls=[{"index": 0, "id": "c1", "type": "function",
                                       "function": {"name": "shell", "arguments": ""}}]),
                    delta(tool_calls=[{"index": 0,
                                       "function": {"arguments": '{"command": "echo hi"}'}}])]
        script = sse(*(emission() + emission() + [delta(finish="tool_calls")])) + [SSE_END]
        data, stats = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        calls = data["choices"][0]["message"].get("tool_calls") or []
        check("a re-emitted call is still ONE call", len(calls) == 1, calls)
        if calls:
            check("...its name is not doubled",
                  calls[0]["function"]["name"] == "shell", calls[0]["function"]["name"])
            check("...its arguments are not doubled",
                  calls[0]["function"]["arguments"].count("echo hi") == 1,
                  calls[0]["function"]["arguments"])


    def test_repeated_characters_inside_one_argument_survive():
        """A repeated character is not a resend. `2000` arrives as 2-0-0-0, and a guard that
    dropped "the same fragment as the one before it" turned the live command
    `seq 1 2000` into `seq 1 20` (measured 2026-09-27 against the endpoint: raw concat
    correct, harness output `seq 1 20`)."""
        cmd = "seq 1 2000 | awk '{print $1, $1*$1}'"
        frags = ['{', '"command":"', 'seq', ' ', '1', ' ', '2', '0', '0', '0', ' |', ' awk',
                 " '{", 'print', ' $', '1', ',', ' $', '1', '*$', '1', "}'", '"', '}']
        chunks = [delta(tool_calls=[{"index": 0, "id": "c1", "type": "function",
                                     "function": {"name": "shell", "arguments": ""}}])]
        chunks += [delta(tool_calls=[{"index": 0, "function": {"arguments": f}}])
                   for f in frags]
        chunks.append(delta(finish="tool_calls"))
        data, _ = run_stream(sse(*chunks) + [SSE_END], idle_seconds=5, first_byte_seconds=5)
        calls = data["choices"][0]["message"].get("tool_calls") or []
        check("repeated characters in one argument survive", len(calls) == 1, calls)
        if calls:
            got = json.loads(calls[0]["function"]["arguments"])["command"]
            check("...2000 is not clipped to 20", got == cmd, got)


    def test_other_openai_compatible_server_shapes():
        """llama.cpp streams one string fragment per token. Other OpenAI-compatible servers -
    vLLM/SGLang-class, and the proxies in front of them - also send the arguments as an
    OBJECT, the legacy `function_call` delta, and `content` as a list of parts. Measured
    arguments, the second lost the call entirely, and the third lost the whole answer.
    None of the three raised anything."""
        # (a) arguments as a JSON object, then a trailing empty object
        script = sse(
            delta(tool_calls=[{"index": 0, "id": "c1", "type": "function",
                               "function": {"name": "shell",
                                            "arguments": {"command": "echo OBJECT-ARGS"}}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": {}}}]),
            delta(finish="tool_calls")) + [SSE_END]
        data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        calls = data["choices"][0]["message"].get("tool_calls") or []
        check("object-form arguments keep the call", len(calls) == 1, calls)
        if calls:
            got = calls[0]["function"]["arguments"]
            try:
                parsed = json.loads(got)
            except Exception as e:                      # an empty/again-empty argument string
                parsed = {"__unparseable__": str(e)}
            check("...and are not emptied, nor beaten by a trailing {}",
                  parsed.get("command") == "echo OBJECT-ARGS", got)

        # (b) the legacy single-call shape
        legacy = {"choices": [{"index": 0, "finish_reason": None,
                               "delta": {"function_call": {"name": "shell",
                                                           "arguments": '{"command": "echo LEGACY"}'}}}]}
        data, _ = run_stream(sse(legacy, delta(finish="function_call")) + [SSE_END],
                             idle_seconds=5, first_byte_seconds=5)
        calls = data["choices"][0]["message"].get("tool_calls") or []
        check("a legacy function_call delta is not dropped", len(calls) == 1, calls)
        if calls:
            try:
                parsed = json.loads(calls[0]["function"]["arguments"])
            except Exception as e:
                parsed = {"__unparseable__": str(e)}
            check("...and carries its arguments", parsed.get("command") == "echo LEGACY",
                  calls[0]["function"]["arguments"])

        # (c) content as a list of parts
        data, _ = run_stream(sse(delta(content=[{"type": "text", "text": "Hello "},
                                                {"type": "text", "text": "world"}]),
                                 delta(content=[{"type": "text", "text": "!"}]),
                                 delta(finish="stop")) + [SSE_END],
                             idle_seconds=5, first_byte_seconds=5)
        check("list-form content is not thrown away",
              data["choices"][0]["message"]["content"] == "Hello world!",
              data["choices"][0]["message"]["content"])


    def test_non_streaming_message_shapes_are_normalized():
        """A non-streamed response arrives whole and can carry the same two shapes a delta can:
    `content` as parts, and the legacy `function_call`. It is normalized at the door, so the
    rest of the harness - and the replayed history - only ever sees one shape."""
        m = fb._normalize_assistant_message(
            {"role": "assistant",
             "content": [{"type": "text", "text": "Hi "}, {"type": "text", "text": "there"}]})
        check("list-form content is joined", m["content"] == "Hi there", m["content"])

        m = fb._normalize_assistant_message(
            {"role": "assistant", "content": "",
             "function_call": {"name": "shell", "arguments": {"command": "echo NS"}}})
        calls = m.get("tool_calls") or []
        check("a legacy function_call becomes a tool call", len(calls) == 1, m)
        if calls:
            check("...with string arguments for the replay",
                  json.loads(calls[0]["function"]["arguments"])["command"] == "echo NS",
                  calls[0]["function"]["arguments"])

        m = fb._normalize_assistant_message(
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "a",
                             "function": {"name": "shell",
                                          "arguments": {"command": "echo OBJ"}}}]})
        check("object arguments are stringified",
              isinstance(m["tool_calls"][0]["function"]["arguments"], str),
              m["tool_calls"][0]["function"]["arguments"])


    def test_indexed_calls_keep_their_index():
        script = sse(
            delta(tool_calls=[{"index": 0, "id": "a", "type": "function",
                               "function": {"name": "shell", "arguments": ""}},
                              {"index": 1, "id": "b", "type": "function",
                               "function": {"name": "shell", "arguments": ""}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '{"x": 1}'}},
                              {"index": 1, "function": {"arguments": '{"x": 2}'}}]),
            delta(finish="tool_calls")) + [SSE_END]
        data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        calls = data["choices"][0]["message"].get("tool_calls") or []
        check("indexed fragments land in their own slots", len(calls) == 2, calls)
        if len(calls) == 2:
            check("index 0 keeps its arguments", calls[0]["function"]["arguments"] == '{"x": 1}',
                  calls[0])
            check("index 1 keeps its arguments", calls[1]["function"]["arguments"] == '{"x": 2}',
                  calls[1])


    # ---------------------------------------------------------------- `_call_sig` does not collapse long arguments
    def test_call_sig_does_not_collapse_long_arguments():
        a = {"path": "/tmp/x", "blob": "A" * 500 + "TAIL-ONE"}
        b = {"path": "/tmp/x", "blob": "A" * 500 + "TAIL-TWO"}
        check("two calls differing after 400 chars are different signatures",
              fb._call_sig("write_file", a) != fb._call_sig("write_file", b),
              fb._call_sig("write_file", a))
        check("the same call as a dict and as JSON has ONE signature",
              fb._call_sig("write_file", a) == fb._call_sig("write_file", json.dumps(a)))


    # ---------------------------------------------------------------- locality and the privacy pin
    def test_locality_classification():
        for url, want in (("http://127.1:8081/v1", True),
                          ("http://127.0.0.1:8081/v1", True),
                          ("http://[::1]:8080/v1", True),
                          ("http://localhost:8080/v1", True),
                          ("http://0.0.0.0:8080/v1", True),
                          ("http://gpu-box.local:8080/v1", True),
                          ("http://10.1.2.3:8080/v1", True),
                          ("http://10.9.9.9:8080/v1", True),
                          ("http://172.16.0.4:8080/v1", True),
                          ("http://172.32.0.4:8080/v1", False),
                          ("https://api.deepseek.com/v1", False),
                          ("https://no-such-host.invalid/v1", False),
                          # The authority ends where the CLIENT ends it ('/', '?', '#'):
                          # userinfo after that point is not the host the request reaches.
                          ("http://no-such.invalid?@10.0.0.1/v1", False),
                          ("http://no-such.invalid#@10.0.0.1/v1", False),
                          ("http://127.0.0.1#@no-such.invalid/v1", True),
                          # A leading-zero octet is octal to inet_aton (010 -> 8) and decimal
                          # to other parsers: ambiguous, so never classified as LAN.
                          ("http://010.0.0.1:8080/v1", False)):
            got = fb._is_local_url(url)
            check(f"locality: {url} -> {want}", got is want, got)


    def test_a_name_is_local_only_when_every_address_is():
        """A name with a private AND a public answer: requests may connect to the public one,
    so the NAME is off-LAN. The old rule returned True on the first private answer."""
        real = fb.socket.getaddrinfo

        def fake(host, *a, **kw):
            return [(2, 1, 6, "", ("10.0.0.5", 0)), (2, 1, 6, "", ("93.184.216.34", 0))]

        try:
            fb.socket.getaddrinfo = fake
            fb._LOCAL_URL_CACHE.clear()
            check("a dual-homed name is NOT local",
                  fb._is_local_url("http://dual-homed.invalid:1/") is False,
                  fb._is_local_url("http://dual-homed.invalid:1/"))

            def fake_private(host, *a, **kw):
                return [(2, 1, 6, "", ("10.0.0.5", 0)), (2, 1, 6, "", ("172.16.0.9", 0))]

            fb.socket.getaddrinfo = fake_private
            fb._LOCAL_URL_CACHE.clear()
            check("...while an all-private name still is",
                  fb._is_local_url("http://all-private.invalid:1/") is True,
                  fb._is_local_url("http://all-private.invalid:1/"))
        finally:
            fb.socket.getaddrinfo = real
            fb._LOCAL_URL_CACHE.clear()


    def test_privacy_pin_covers_the_failover_tail():
        """With the flag false, a local choice must not fail over to a hosted primary."""
        saved = json.loads(json.dumps(fb.CONFIG["llm"]))
        try:
            fb.CONFIG["llm"]["base_url"] = "http://127.0.0.1:1/v1"
            fb.CONFIG["llm"]["model"] = "main"
            fb.CONFIG["llm"]["allow_cloud_fallback"] = False
            fb.CONFIG["llm"]["fallbacks"] = [
                {"base_url": "http://127.0.0.1:2/v1", "model": "main", "api_key": "x"},
                {"base_url": "https://api.example.com/v1", "model": "other",
                 "alias": "cloud", "api_key": "x"}]
            fb.CONFIG["llm"]["stream"] = False
            fb.AGENT._window_cache = 32768
            fb.AGENT._window_at = 0.0
            fb.AGENT._envelope_cache = None
            import requests as _rq
            tried = []
            real = fb._post_watchdog

            def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                          stream=False):
                tried.append(url)
                raise _rq.HTTPError("500", response=types.SimpleNamespace(
                    status_code=500, text="nope"))

            fb._post_watchdog = fake_post
            try:
                fb.AGENT._chat([{"role": "system", "content": "s"},
                                {"role": "user", "content": "hi"}], session_key="f5")
            except Exception:                                   # noqa: BLE001
                pass
            finally:
                fb._post_watchdog = real
            check("the local fallback was tried", any("127.0.0.1:2" in u for u in tried), tried)
            check("the cloud fallback was NOT tried", not any("api.example.com" in u for u in tried),
                  tried)
        finally:
            fb.CONFIG["llm"] = saved


    # ---------------------------------------------------------------- a 400 naming stream_options is retried
    def test_a_400_naming_stream_options_is_retried_without_it():
        saved = json.loads(json.dumps(fb.CONFIG["llm"]))
        try:
            import requests as _rq
            fb.CONFIG["llm"]["base_url"] = "http://127.0.0.1:1/v1"
            fb.CONFIG["llm"]["model"] = "main"
            fb.CONFIG["llm"]["fallbacks"] = []
            fb.CONFIG["llm"]["stream"] = True
            fb.AGENT._window_cache = 32768
            fb.AGENT._window_at = 0.0
            fb.AGENT._envelope_cache = None
            seen = []
            real = fb._post_watchdog

            def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                          stream=False):
                seen.append(dict(payload))
                if len(seen) == 1:
                    raise _rq.HTTPError("400", response=types.SimpleNamespace(
                        status_code=400,
                        text='{"error": "unknown field: stream_options"}'))
                return FakeResp(sse(delta(content="fine"), delta(finish="stop")) + [SSE_END])

            fb._post_watchdog = fake_post
            try:
                reply = fb.AGENT._chat([{"role": "system", "content": "s"},
                                        {"role": "user", "content": "hi"}],
                                       session_key="f4")
            except Exception as e:                              # noqa: BLE001
                reply = {"content": "RAISED %s: %s" % (type(e).__name__, e)}
            finally:
                fb._post_watchdog = real
            check("a 400 naming stream_options is retried, not fatal",
                  (reply.get("content") or "") == "fine", reply)
            check("the first attempt SENT stream_options",
                  bool(seen) and "stream_options" in seen[0], seen[:1])
            check("the retry dropped it",
                  len(seen) >= 2 and "stream_options" not in seen[1],
                  seen[1] if len(seen) > 1 else seen)
        finally:
            fb.CONFIG["llm"] = saved


    # ---------------------------------------------------------------- the fallback log tells the truth
    def test_a_transient_stream_failure_is_logged_as_per_call():
        """Every StreamFailed used to be logged as "keeping this endpoint off
        streaming for the rest of THIS process" - a process-wide downgrade that a
        transient failure (a prefill timeout, an idle gap, a mid-stream break) does
        not make. Only a server that ignored stream:true is blacklisted."""
        import logging
        saved = json.loads(json.dumps(fb.CONFIG["llm"]))
        url = "http://127.0.0.1:1/v1"

        def held():
            return [u for u in fb._STREAM_UNSUPPORTED if u.startswith(url)]

        def drive(exc):
            recs = []
            h = logging.Handler()
            h.emit = lambda rec: recs.append(rec.getMessage())
            fb.log.addHandler(h)
            real_post, real_stream = fb._post_watchdog, fb._stream_chat
            fb._STREAM_UNSUPPORTED.difference_update(held())

            def fake_post(u, headers, payload, timeout, grace, cancel_event=None,
                          stream=False):
                if payload.get("stream"):
                    return FakeResp([(0.0, "data: [DONE]")])
                raise RuntimeError("the non-streamed retry is not the subject")

            def fake_stream(resp, **kw):
                raise exc

            fb._post_watchdog = fake_post
            fb._stream_chat = fake_stream
            try:
                fb.CONFIG["llm"]["base_url"] = url
                fb.CONFIG["llm"]["model"] = "main"
                fb.CONFIG["llm"]["fallbacks"] = []
                fb.CONFIG["llm"]["stream"] = True
                fb.AGENT._window_cache = 32768
                fb.AGENT._window_at = 0.0
                fb.AGENT._envelope_cache = None
                try:
                    fb.AGENT._chat([{"role": "system", "content": "s"},
                                    {"role": "user", "content": "hi"}],
                                   session_key="f7")
                except Exception:                               # noqa: BLE001
                    pass
            finally:
                fb._post_watchdog, fb._stream_chat = real_post, real_stream
                fb.log.removeHandler(h)
            return "\n".join(recs)

        try:
            msg = drive(fb.StreamFailed("stream went quiet for 3s (idle limit 2s)"))
            check("a transient stream failure is logged per-call",
                  "retrying this call without streaming" in msg
                  and "rest of THIS process" not in msg, msg[-300:])
            check("...and a transient failure does not blacklist the endpoint",
                  not held(), fb._STREAM_UNSUPPORTED)
            ff = fb.StreamFailed("the response carried no SSE data (not a stream?)",
                                 not_a_stream=True)
            msg2 = drive(ff)
            check("a server that answered plain JSON IS logged as blacklisted",
                  "rest of THIS process" in msg2, msg2[-300:])
            check("...and is remembered for the process",
                  held(), fb._STREAM_UNSUPPORTED)
        finally:
            fb.CONFIG["llm"] = saved
            fb._STREAM_UNSUPPORTED.difference_update(held())


    # ---------------------------------------------------------------- a tool's OperatorStop is an answer
    def test_operator_stop_from_a_tool_is_an_answer():
        """A stop raised while a TOOL runs must come back as run()'s answer, not escape."""
        calls = {"chat": 0}
        real_chat = fb.AGENT._chat
        real_exec = fb.AGENT._exec_tool

        def fake_chat(messages, *a, **kw):
            calls["chat"] += 1
            if calls["chat"] == 1:
                return {"role": "assistant", "content": "",
                        "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "shell",
                                                     "arguments": '{"command": "echo hi"}'}}]}
            return {"role": "assistant", "content": "final", "tool_calls": []}

        def fake_exec(tool_call, ctx):
            raise fb.OperatorStop("nobody answered the question in time")

        fb.AGENT._chat = fake_chat
        fb.AGENT._exec_tool = fake_exec
        try:
            try:
                answer = fb.AGENT.run("f3-stop", "do a thing")
            except fb.OperatorStop as e:
                check("an OperatorStop from tool execution does not escape run()", False, e)
                return
        finally:
            fb.AGENT._chat = real_chat
            fb.AGENT._exec_tool = real_exec
        check("an OperatorStop from tool execution does not escape run()", True)
        check("...and the run answers with the stop notice",
              "🛑" in str(answer) or "Stopped" in str(answer), answer)


    def main():
        for name, fn in sorted(_ns.items()):
            if name.startswith("test_") and callable(fn):
                fn()
        print()
        if FAILURES:
            print(f"{len(FAILURES)} check(s) failed")
            for f in FAILURES:
                print("  FAIL:", f)
            sys.exit(1)
        print("all turn-engine checks passed")
    _ns = dict(locals())
    return main()


def _suite_test_stream_integrity():
    """A clean close is not a completion, and a no-op edit is not an edit.

Two silent-wrong-answer shapes:

  * a stream that ends with content but NEITHER a `finish_reason` NOR `[DONE]` is a
    truncation (a server killed mid-answer), and it used to be returned as the model's
    final word - finish_reason '' meant the length/window checks never saw it either;
  * an edit whose new_string reproduces the bytes already on disk wrote the file back
    and returned OK over an empty diff, teaching a weak model to re-anchor and re-send
    variants of the same payload (escalating on the third identical one).

It also pins the reasoning-aliases + leading think-fence rules: a server with no
reasoning parser leaks `<think>...</think>` into `content`, and `reasoning`/
`reasoning_text` fields used to be dropped.

    python tests/test_stream_surface.py
"""
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    import time
    import types
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-stream-integrity"
    if STAGE.exists():
        shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
                 STAGE / "config.json")
    spec = importlib.util.spec_from_file_location("tinycmdr_stream_integrity",
                                                  STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_stream_integrity"] = fb
    spec.loader.exec_module(fb)

    FAILURES = []


    def check(name, cond, detail=""):
        if cond:
            print(f"ok   {name}")
        else:
            FAILURES.append(name)
            print(f"FAIL {name}: {detail}")


    class FakeResp:
        def __init__(self, script):
            self.script = script
            self.raw = types.SimpleNamespace()
            self.closed = False

        def iter_lines(self, decode_unicode=False):
            for delay, value in self.script:
                if delay:
                    time.sleep(delay)
                yield value.encode() if isinstance(value, str) else value

        def close(self):
            self.closed = True

        def raise_for_status(self):
            return None


    def sse(*chunks):
        return [(0.0, "data: " + json.dumps(c)) for c in chunks]


    SSE_END = (0.0, "data: [DONE]")


    def delta(content=None, reasoning=None, finish=None):
        d = {}
        if content is not None:
            d["content"] = content
        if reasoning is not None:
            d.update(reasoning)
        ch = {"index": 0, "delta": d, "finish_reason": finish}
        return {"choices": [ch]}


    def run_stream(script, **kw):
        return fb._stream_chat(FakeResp(script), **kw)


    # ---------------------------------------------------------- incomplete streams
    def test_clean_close_without_terminator_is_a_truncation():
        script = sse(delta(content="The answer so far"), delta(content=" and more"))
        try:
            run_stream(script, idle_seconds=5, first_byte_seconds=5)
            check("content with no finish_reason and no [DONE] raises StreamFailed",
                  False, "returned a truncated answer")
        except fb.StreamFailed as e:
            check("content with no finish_reason and no [DONE] raises StreamFailed",
                  "no finish_reason" in str(e), e)
        except Exception as e:                                  # noqa: BLE001
            check("content with no finish_reason and no [DONE] raises StreamFailed",
                  False, "%s: %s" % (type(e).__name__, e))


    def test_done_alone_is_a_completion():
        script = sse(delta(content="hello"), delta(content=" world")) + [SSE_END]
        data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        check("a stream ended by [DONE] alone is accepted",
              data["choices"][0]["message"]["content"] == "hello world",
              data["choices"][0]["message"])


    def test_finish_reason_alone_is_a_completion():
        script = sse(delta(content="hello"), delta(finish="stop"))
        data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        check("a stream ended by finish_reason alone is accepted",
              data["choices"][0]["message"]["content"] == "hello"
              and data["choices"][0]["finish_reason"] == "stop",
              data["choices"][0])


    # ---------------------------------------------------------- reasoning fields
    def test_reasoning_alias_fields_are_read():
        script = sse(delta(reasoning={"reasoning": "I think "}),
                     delta(reasoning={"reasoning_text": "therefore"}),
                     delta(content="Answer"), delta(finish="stop")) + [SSE_END]
        data, stats = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        m = data["choices"][0]["message"]
        check("the `reasoning` field is captured", m.get("reasoning_content") == "I think therefore",
              m)
        check("...and is not in the answer", m.get("content") == "Answer", m)
        check("...and counts as reasoning, not answer chars",
              stats["reasoning_chars"] == len("I think therefore")
              and stats["chars"] == len("Answer"), stats)


    def test_aliases_are_not_summed():
        d = {"reasoning": "same text", "reasoning_content": "same text"}
        script = sse(delta(reasoning=d), delta(content="x"), delta(finish="stop")) + [SSE_END]
        data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        check("two aliases carrying one text are not double-counted",
              data["choices"][0]["message"].get("reasoning_content") == "same text",
              data["choices"][0]["message"])


    # ---------------------------------------------------------- leading think fence
    def test_leading_fence_split_across_deltas():
        script = sse(delta(content="<thi"), delta(content="nk>"),
                     delta(content="secret plan"), delta(content="</thi"), delta(content="nk>"),
                     delta(content="The answer"), delta(finish="stop")) + [SSE_END]
        data, stats = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        m = data["choices"][0]["message"]
        check("a leading <think> fence lands in reasoning, not content",
              m.get("content") == "The answer", m)
        check("...with the thinking intact", m.get("reasoning_content") == "secret plan", m)
        check("...and the stats split answers from thinking",
              stats["chars"] == len("The answer")
              and stats["reasoning_chars"] == len("secret plan"), stats)


    def test_non_leading_fence_stays_content():
        script = sse(delta(content="The answer. "), delta(content="<think>x</think>"),
                     delta(finish="stop")) + [SSE_END]
        data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        m = data["choices"][0]["message"]
        check("a <think> inside an answer is left as the model wrote it",
              m.get("content") == "The answer. <think>x</think>"
              and not m.get("reasoning_content"), m)


    def test_unterminated_fence_is_thinking():
        script = sse(delta(content="<think>started thinking"), delta(finish="stop")) + [SSE_END]
        data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        m = data["choices"][0]["message"]
        check("an unterminated fence is thinking, never an answer",
              m.get("content") == "" and m.get("reasoning_content") == "started thinking", m)


    def test_plain_angle_bracket_text_is_not_held():
        script = sse(delta(content="<b>hi</b> and < not a fence"), delta(finish="stop")) + [SSE_END]
        data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        check("content that merely starts with < is answered as-is",
              data["choices"][0]["message"].get("content") == "<b>hi</b> and < not a fence",
              data["choices"][0]["message"])


    def test_fence_off_keeps_raw_text():
        was = fb.CONFIG["llm"].get("think_fence", True)
        fb.CONFIG["llm"]["think_fence"] = False
        try:
            script = sse(delta(content="<think>x</think>answer"), delta(finish="stop")) + [SSE_END]
            data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
            check("llm.think_fence=false sends the raw text through",
                  data["choices"][0]["message"].get("content") == "<think>x</think>answer",
                  data["choices"][0]["message"])
        finally:
            fb.CONFIG["llm"]["think_fence"] = was


    # ---------------------------------------------------------- no-op edit guard
    def test_noop_edit_is_refused_then_escalated_then_cleared():
        work = Path(tempfile.mkdtemp(prefix="fbtest-noop-edit-"))
        try:
            target = work / "cfg.txt"
            target.write_text("hello\n", encoding="utf-8")
            ctx = {"session_key": "noop-s1"}
            same = {"path": str(target), "old_string": "hello", "new_string": "hello"}
            before = target.read_bytes()

            r1 = fb.tool_edit_file(dict(same), ctx)
            check("a byte-identical edit is refused, not reported OK",
                  "changed nothing" in r1 and not r1.startswith("OK"), r1)
            check("...and the file is left alone",
                  target.read_bytes() == before and not (work / "cfg.txt.bak").exists(),
                  target.read_bytes())

            fb.tool_edit_file(dict(same), ctx)
            r3 = fb.tool_edit_file(dict(same), ctx)
            check("the third identical no-op escalates to STOP",
                  r3.startswith("STOP."), r3)

            real = fb.tool_edit_file({"path": str(target), "old_string": "hello",
                                      "new_string": "world"}, ctx)
            check("a real edit still lands", real.startswith("OK") and
                  target.read_text(encoding="utf-8") == "world\n", real)

            r4 = fb.tool_edit_file({"path": str(target), "old_string": "world",
                                    "new_string": "world"}, ctx)
            check("a landed edit clears the no-op streak (no instant STOP)",
                  "changed nothing" in r4 and not r4.startswith("STOP."), r4)

            # A write_file to the same path clears it too.
            fb.tool_edit_file({"path": str(target), "old_string": "world",
                               "new_string": "world"}, ctx)
            fb.tool_write_file({"path": str(target), "content": "fresh\n"}, ctx)
            r5 = fb.tool_edit_file({"path": str(target), "old_string": "fresh",
                                    "new_string": "fresh"}, ctx)
            check("a write_file clears the no-op streak",
                  "changed nothing" in r5 and not r5.startswith("STOP."), r5)
        finally:
            shutil.rmtree(work, ignore_errors=True)


    def main():
        test_clean_close_without_terminator_is_a_truncation()
        test_done_alone_is_a_completion()
        test_finish_reason_alone_is_a_completion()
        test_reasoning_alias_fields_are_read()
        test_aliases_are_not_summed()
        test_leading_fence_split_across_deltas()
        test_non_leading_fence_stays_content()
        test_unterminated_fence_is_thinking()
        test_plain_angle_bracket_text_is_not_held()
        test_fence_off_keeps_raw_text()
        test_noop_edit_is_refused_then_escalated_then_cleared()
        print()
        if FAILURES:
            print("%d check(s) failed" % len(FAILURES))
            return 1
        print("all stream-integrity checks passed")
        return 0
    return main()


def _suite_test_responses_wire():
    """The /responses wire is a real BODY shape, not a renamed reasoning field.

Chat and Responses disagree about far more than the field name: `messages` -> `input`,
system -> `instructions`, text parts carry an explicit type, tools are flat, the output
cap is `max_output_tokens`, and the answer arrives as `output` items with a `status`
instead of `choices` with a `finish_reason`. This exercises the pure helpers that own that
boundary, plus the URL derivation the tools+reasoning_effort 400 escalation retries on.

On the pre-fix build these helpers do not exist, so every check must come back red.

    python tests/test_stream_surface.py        [TINYCMDR_SRC=/path/to/old/tinycmdr.py]
"""
    import importlib.util
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

    FAILS = []


    def check(what, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
        if not ok:
            FAILS.append(what)


    def got(what, fn, pred=None):
        """Run fn(); a raised exception (a pre-fix build has no helper) is a FAIL too."""
        try:
            v = fn()
        except Exception as e:                      # noqa: BLE001 - report, do not crash
            check(what, False, "%s: %s" % (type(e).__name__, e))
            return None
        check(what, pred(v) if pred else bool(v), repr(v))
        return v


    MSGS = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "calling",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "echo", "arguments": '{"x":1}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "out"},
    ]
    TOOLS = [{"type": "function",
              "function": {"name": "echo", "description": "d",
                           "parameters": {"type": "object", "properties": {}}}}]


    def main():
        work = Path(tempfile.mkdtemp(prefix="fbresp-"))
        try:
            for name in ("theme.default.toml", "soul.example.md"):
                shutil.copy2(BASE / name, work / name)
            shutil.copy2(SRC, work / "tinycmdr.py")
            shutil.copy2(BASE / "tests" / "fixture-config.json", work / "config.json")
            spec = importlib.util.spec_from_file_location("tinycmdr_responses_wire",
                                                          work / "tinycmdr.py")
            fb = importlib.util.module_from_spec(spec)
            sys.modules["tinycmdr_responses_wire"] = fb
            spec.loader.exec_module(fb)

            # --- the payload mapping ----------------------------------------------
            p = got("responses_payload maps the history into a Responses body",
                    lambda: fb.responses_payload("m1", MSGS, tools=TOOLS,
                                                 max_output_tokens=1234),
                    lambda v: isinstance(v, dict))
            got("a system message becomes `instructions`",
                lambda: p["instructions"], lambda v: v == "SYS")
            got("the request is not stored server-side",
                lambda: p["store"], lambda v: v is False)
            got("the model is carried through",
                lambda: p["model"], lambda v: v == "m1")
            got("the output cap rides as max_output_tokens",
                lambda: p["max_output_tokens"], lambda v: v == 1234)
            got("no chat-shaped `messages` key survives",
                lambda: "messages" not in p, lambda v: v)
            got("a user message becomes an input_text part",
                lambda: p["input"][0]["content"][0],
                lambda v: v == {"type": "input_text", "text": "hi"})
            got("an assistant tool_call becomes a function_call item",
                lambda: p["input"][1],
                lambda v: v == {"type": "function_call", "call_id": "call_1",
                                "name": "echo", "arguments": '{"x":1}'})
            got("...and the call item precedes the assistant's own text",
                lambda: (p["input"][1]["type"], p["input"][2]["content"][0]),
                lambda v: v == ("function_call",
                                {"type": "output_text", "text": "calling"}))
            got("a tool result becomes a function_call_output item",
                lambda: p["input"][3],
                lambda v: v == {"type": "function_call_output", "call_id": "call_1",
                                "output": "out"})
            got("chat tools are flattened into the Responses shape",
                lambda: p["tools"][0],
                lambda v: v == {"type": "function", "name": "echo", "description": "d",
                                "parameters": {"type": "object", "properties": {}}})
            got("stream is omitted unless asked for",
                lambda: "stream" not in p, lambda v: v)
            got("stream: true is carried when asked for",
                lambda: fb.responses_payload("m1", MSGS, stream=True).get("stream"),
                lambda v: v is True)
            got("arguments arriving as a JSON object are stringified",
                lambda: fb.responses_payload("m", [
                    {"role": "assistant", "tool_calls": [
                        {"id": "c", "function": {"name": "e",
                                                 "arguments": {"x": 1}}}]}])["input"][0]
                ["arguments"], lambda v: v == '{"x": 1}')

            # --- reading a Responses body back ------------------------------------
            msg = got("parse_responses joins message text",
                      lambda: fb.parse_responses({"status": "completed", "output": [
                          {"type": "message", "content": [
                              {"type": "output_text", "text": "Hi "},
                              {"type": "output_text", "text": "there"}]}]}),
                      lambda v: v[0]["content"] == "Hi there" and v[1] == "stop")
            got("...and leaves tool_calls off a plain answer",
                lambda: "tool_calls" not in msg[0], lambda v: v)
            msg = got("parse_responses maps a function_call to a tool_call",
                      lambda: fb.parse_responses({"status": "completed", "output": [
                          {"type": "function_call", "call_id": "call_9", "name": "echo",
                           "arguments": '{"a":1}'},
                          {"type": "message", "content": [
                              {"type": "output_text", "text": "ok"}]}]}),
                      lambda v: v[1] == "tool_calls")
            got("...with the call_id preserved as the call id",
                lambda: msg[0]["tool_calls"][0],
                lambda v: v == {"id": "call_9", "type": "function",
                                "function": {"name": "echo", "arguments": '{"a":1}'}})
            got("...and the accompanying text kept",
                lambda: msg[0]["content"], lambda v: v == "ok")
            msg = got("a reasoning summary becomes reasoning_content",
                      lambda: fb.parse_responses({"status": "incomplete", "output": [
                          {"type": "reasoning", "summary": [
                              {"type": "summary_text", "text": "TH"}]},
                          {"type": "message", "content": [
                              {"type": "output_text", "text": "a"}]}]}),
                      lambda v: v[0].get("reasoning_content") == "TH")
            got("...and an incomplete status maps to a length finish",
                lambda: msg[1], lambda v: v == "length")

            # --- the SSE event mapping --------------------------------------------
            got("response.output_text.delta is text",
                lambda: fb.responses_stream_event(
                    {"type": "response.output_text.delta", "delta": "x"}),
                lambda v: v == ("text", "x"))
            got("response.reasoning_summary_text.delta is reasoning",
                lambda: fb.responses_stream_event(
                    {"type": "response.reasoning_summary_text.delta", "delta": "r"}),
                lambda v: v == ("reasoning", "r"))
            got("response.reasoning_text.delta is reasoning",
                lambda: fb.responses_stream_event(
                    {"type": "response.reasoning_text.delta", "delta": "r2"}),
                lambda v: v == ("reasoning", "r2"))
            got("response.function_call_arguments.delta is tool_args",
                lambda: fb.responses_stream_event(
                    {"type": "response.function_call_arguments.delta", "delta": '{"a"',
                     "item_id": "i1", "output_index": 2})[0],
                lambda v: v == "tool_args")
            got("...carrying the fragment and its slot",
                lambda: fb.responses_stream_event(
                    {"type": "response.function_call_arguments.delta", "delta": '{"a"',
                     "item_id": "i1", "output_index": 2})[1],
                lambda v: isinstance(v, dict) and v.get("delta") == '{"a"'
                and v.get("output_index") == 2)
            got("an added function_call item names the call",
                lambda: fb.responses_stream_event({"type": "response.output_item.added",
                                                   "output_index": 0, "item": {
                                                       "type": "function_call",
                                                       "name": "echo",
                                                       "call_id": "call_3"}})[1],
                lambda v: isinstance(v, dict) and v.get("name") == "echo"
                and v.get("call_id") == "call_3")
            got("response.completed is done, with usage",
                lambda: fb.responses_stream_event({"type": "response.completed", "response": {
                    "status": "completed", "usage": {"input_tokens": 5}}})[1]["usage"],
                lambda v: v.get("input_tokens") == 5)
            got("an unknown event is ignored, never guessed at",
                lambda: fb.responses_stream_event({"type": "response.weird.thing"}),
                lambda v: v == (None, None))

            # --- the usage vocabulary boundary ------------------------------------
            got("_responses_as_chat renames usage to the chat fields",
                lambda: fb._responses_as_chat({"status": "completed", "output": [],
                                               "usage": {"input_tokens": 11,
                                                         "output_tokens": 7}}),
                lambda v: v["usage"]["prompt_tokens"] == 11
                and v["usage"]["completion_tokens"] == 7)

            # --- the 400 escalation's URL derivation and remembered wire -----------
            chat_url = "https://api.example.com/v1/chat/completions"
            resp_url = "https://api.example.com/v1/responses"
            got("the /responses sibling is derived from a chat-completions URL",
                lambda: fb._responses_sibling_url(chat_url), lambda v: v == resp_url)
            got("...and it shares the endpoint origin, so the learned fact covers both",
                lambda: fb._endpoint_origin(fb._responses_sibling_url(chat_url))
                == fb._endpoint_origin(chat_url), lambda v: v)
            got("a URL that is not a chat-completions path has no derived sibling",
                lambda: fb._responses_sibling_url("https://api.example.com/v1"),
                lambda v: v is None)
            got("a /responses URL has no further sibling",
                lambda: fb._responses_sibling_url(resp_url), lambda v: v is None)
            got("a chat URL starts on the chat wire",
                lambda: fb._wire_for(chat_url), lambda v: v == "chat")
            got("a tools+reasoning_effort 400 derives the /responses retry URL",
                lambda: fb._responses_escalation_url(chat_url, _TOOLS_400),
                lambda v: v == resp_url)
            got("...and a 400 that names neither field is not escalated",
                lambda: fb._responses_escalation_url(chat_url, "context length exceeded"),
                lambda v: v is None)
            got("...nor is an endpoint already on the responses wire",
                lambda: fb._responses_escalation_url(resp_url, _TOOLS_400),
                lambda v: v is None)
            fb._endpoint_note(chat_url, wire="responses")
            got("the escalation's remembered wire drives the endpoint's next call",
                lambda: (fb._endpoint_facts(chat_url).get("wire"),
                         fb._wire_for(chat_url)),
                lambda v: v == ("responses", "responses"))
            fb._ENDPOINT_FACTS["loaded"] = False        # simulate a fresh process
            got("...and the fact is PERSISTED across a restart",
                lambda: fb._wire_for(chat_url), lambda v: v == "responses")

            if FAILS:
                print("\n%d FAILED: %s" % (len(FAILS), "; ".join(FAILS)))
                return 1
            print("\nall responses-wire checks passed")
            return 0
        finally:
            shutil.rmtree(work, ignore_errors=True)


    _TOOLS_400 = ("Function tools with reasoning_effort are not supported for "
                  "gpt-5.6-terra in /v1/chat/completions.")
    return main()


def main():
    rc = 0
    for name, fn in (("test_stream_calls", _suite_test_stream_calls), ("test_stream_integrity", _suite_test_stream_integrity), ("test_responses_wire", _suite_test_responses_wire)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
