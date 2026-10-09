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

    python tests/test_stream_calls.py
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
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed")
        for f in FAILURES:
            print("  FAIL:", f)
        sys.exit(1)
    print("all turn-engine checks passed")


if __name__ == "__main__":
    main()
