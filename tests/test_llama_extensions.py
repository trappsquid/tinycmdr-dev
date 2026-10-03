"""llama.cpp's own stream extensions, and the gate that keeps them off everything else.

`return_progress` and `sse_ping_interval` are llama.cpp SERVER extensions, not OpenAI
ones (verified live 2026-09-27 against a local llama.cpp server: `return_progress`
emits a normal chat chunk carrying `prompt_progress` at ~0.1s and then once per prompt
batch, and `sse_ping_interval` sets the interval of the bare `:` keep-alive comment
line). A long prompt is otherwise minutes of silence, so both are worth having - and
both are exactly the kind of field a cloud provider rejects with a 400, which is why
the operator's rule is that they are GATED.

The gate has three teeth, and every one of them is checked here:
  * the config switch (`llm.llama_extensions`);
  * `_is_local_url` - an off-LAN endpoint is never even probed, so no metadata request
    leaves the LAN;
  * the endpoint's own /props reply must fingerprint a llama.cpp build (vLLM answers
    /v1/models and /metrics, SGLang /get_server_info, hosted APIs nothing).

The negative direction is the one that matters: a request that reaches a non-llama.cpp
endpoint must not carry either field - not "the server probably ignores it".

    python tests/test_llama_extensions.py
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

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-llamaext"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
             STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_llamaext", STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_llamaext"] = fb
spec.loader.exec_module(fb)

import requests as _rq      # noqa: E402  (the module's own transport, for HTTPError)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}: {detail}")


# A llama.cpp /props reply, cut to the keys the fingerprint uses (the real one also
# carries the chat template, the slot count, the model path and the metrics flags).
LLAMA_PROPS = {
    "build_info": "b6900-abc1234",
    "total_slots": 2,
    "model_alias": "main",
    "default_generation_settings": {"n_ctx": 131072,
                                    "params": {"temperature": 1.0, "top_k": 20}},
    "endpoint_slots": True,
}
# What a vLLM-class server answers to a route it does not serve, or to /props with a
# generic 200 body: a dict, but nothing that says llama.cpp.
FOREIGN_PROPS = {"object": "list", "data": [{"id": "main", "max_model_len": 32768}]}


class FakeRequests:
    """The module's `requests`, with /props answered by the test.

    Everything else delegates to the real module, so the code under test still gets
    real `HTTPError`/`Timeout` classes and real `.post`.
    """

    def __init__(self, props=LLAMA_PROPS, status=200, real=None, by_host=None):
        self.props = props
        self.status = status
        self.real = real or _rq
        self.asked = []
        # Optional per-host answers: {host-or-prefix: props-or-None}. None means 404.
        self.by_host = by_host or {}

    def get(self, url, **kw):
        self.asked.append(url)
        props, status = self.props, self.status
        for host, answer in self.by_host.items():
            if host in url:
                props, status = answer, (200 if answer is not None else 404)
                break
        if status != 200:
            raise self.real.HTTPError(f"HTTP {status}", response=types.SimpleNamespace(
                status_code=status, text="not found"))
        return types.SimpleNamespace(json=lambda: props)

    def __getattr__(self, name):
        return getattr(self.real, name)


class FakeResp:
    """A streaming response: `script` is a list of (delay, line-or-Exception)."""

    def __init__(self, script):
        self.script = script
        self.raw = types.SimpleNamespace()
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        for delay, value in self.script:
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
    return [(0.0, "data: " + json.dumps(c)) for c in chunks]


SSE_END = (0.0, "data: [DONE]")


def progress(total, processed, cache=0):
    """A llama.cpp progress event: a normal chat chunk with no content."""
    return {"choices": [{"index": 0, "delta": {"role": "assistant", "content": None},
                         "finish_reason": None}],
            "prompt_progress": {"total": total, "cache": cache,
                                "processed": processed, "time_ms": 9172}}


def delta(content=None, finish=None):
    d = {}
    if content is not None:
        d["content"] = content
    return {"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}


def with_llm(**over):
    """Mutate the staged config for one block. Returns a restore callable."""
    saved = json.loads(json.dumps(fb.CONFIG["llm"]))
    fb.CONFIG["llm"].update(over)
    fb._LLAMA_PROPS_CACHE.clear()
    fb._LLAMA_EXT_ANNOUNCED.clear()

    def restore():
        fb.CONFIG["llm"] = saved
        fb._LLAMA_PROPS_CACHE.clear()
    return restore


def capture_payload(**over):
    """One real `Agent._chat` with the transport stubbed.

    Returns (payloads_the_endpoint_was_sent, urls_/props_was_asked_for). The stub's
    second call answers a well-formed stream, so the gate's effect on the FIRST
    (only) request is what gets asserted.
    """
    restore = with_llm(stream=True, **over)
    fb.AGENT._window_cache = 32768
    fb.AGENT._window_at = 0.0
    fb.AGENT._envelope_cache = None
    fb.AGENT.live_usage.pop("llamaext", None)
    seen = []
    real = fb._post_watchdog

    def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                  stream=False):
        seen.append(dict(payload))
        if len(seen) == 1:
            raise _rq.HTTPError("stub-500", response=types.SimpleNamespace(
                status_code=500, text="nope"))
        return FakeResp(sse(delta(content="fine"), delta(finish="stop")) + [SSE_END])

    fb._post_watchdog = fake_post
    try:
        fb.AGENT._chat([{"role": "system", "content": "s"},
                        {"role": "user", "content": "hi"}], session_key="llamaext")
    except Exception:                       # noqa: BLE001 - the stub is meant to fail
        pass
    finally:
        fb._post_watchdog = real
        restore()
    return seen


def main():
    # ---------------------------------------------------------- the URL shapes
    check("an OpenAI request URL reduces to the server root",
          fb._endpoint_root("http://box:8081/v1/chat/completions")
          == "http://box:8081", fb._endpoint_root("http://box:8081/v1/chat/completions"))
    check("a configured base_url (/v1) reduces to the server root",
          fb._endpoint_root("http://box:8081/v1") == "http://box:8081")
    check("a trailing slash is not a route",
          fb._endpoint_root("http://box:8081/") == "http://box:8081")

    # ---------------------------------------------------------- the fingerprint
    fake = FakeRequests(props=LLAMA_PROPS)
    fb.requests = fake
    try:
        got = fb._llama_props("http://127.0.0.1:1/v1")
        check("a llama.cpp /props reply is recognised", bool(got)
              and got.get("total_slots") == 2, got)
        check("the probe asks the SERVER ROOT, not the OpenAI path",
              fake.asked == ["http://127.0.0.1:1/props"], fake.asked)
        fb._llama_props("http://127.0.0.1:1/v1")
        check("a second call does not re-probe the box", len(fake.asked) == 1,
              fake.asked)

        fb._LLAMA_PROPS_CACHE.clear()
        foreign = FakeRequests(props=FOREIGN_PROPS)
        fb.requests = foreign
        check("a non-llama.cpp 200 to /props is not a fingerprint",
              fb._llama_props("http://127.0.0.1:2/v1") is None)

        fb._LLAMA_PROPS_CACHE.clear()
        missing = FakeRequests(status=404)
        fb.requests = missing
        check("a 404 from /props is not a fingerprint",
              fb._llama_props("http://127.0.0.1:3/v1") is None)
    finally:
        fb.requests = _rq

    # ---------------------------------------------------------- the gate
    fb.requests = FakeRequests(props=LLAMA_PROPS)
    try:
        check("on-LAN + llama.cpp /props -> the extensions are on",
              fb._llama_extensions("http://127.0.0.1:1/chat/completions", {}) is True)

        restore = with_llm(llama_extensions=False)
        try:
            check("the config switch alone turns them off",
                  fb._llama_extensions("http://127.0.0.1:1/chat/completions", {}) is False)
        finally:
            restore()

        fb.requests = FakeRequests(props=LLAMA_PROPS)
        check("an off-LAN endpoint is refused even when it would answer /props",
              fb._llama_extensions("https://api.openai.com/v1/chat/completions", {})
              is False)
        check("...and it was never probed (no metadata request leaves the LAN)",
              fb.requests.asked == [], fb.requests.asked)
    finally:
        fb.requests = _rq

    fb.requests = FakeRequests(props=FOREIGN_PROPS)
    try:
        check("on-LAN but not llama.cpp (vLLM-shape /props) -> off",
              fb._llama_extensions("http://127.0.0.1:1/chat/completions", {}) is False)
    finally:
        fb.requests = _rq

    # ---------------------------------------------------------- the ping knob
    for raw, want in ((0, 0), ("", 0), (None, 0), (5, 5), ("5", 5), (-1, -1),
                      ("nonsense", 0)):
        restore = with_llm(sse_ping_interval=raw)
        try:
            check(f"sse_ping_interval {raw!r} reads as {want}",
                  fb._llama_ping_interval() == want, fb._llama_ping_interval())
        finally:
            restore()

    # ---------------------------------------------------------- the parser
    script = ([(0.0, ": ping")] + sse(progress(1000, 0)) + sse(progress(1000, 512))
              + sse(progress(1000, 1000))
              + sse(delta(content="hel"), delta(content="lo"), delta(finish="stop"))
              + [SSE_END])
    data, stats = fb._stream_chat(FakeResp(script), idle_seconds=5,
                                  first_byte_seconds=5)
    check("the keep-alive comment and the progress events do not disturb the answer",
          data["choices"][0]["message"]["content"] == "hello",
          data["choices"][0]["message"])
    check("the last prompt_progress is exposed",
          (stats.get("prompt_progress") or {}).get("processed") == 1000,
          stats.get("prompt_progress"))
    check("every progress event is counted", stats.get("progress_events") == 3,
          stats.get("progress_events"))
    check("the keep-alive comment is counted, not mistaken for data",
          stats.get("pings") == 1, stats.get("pings"))
    check("progress is not the first token (ttft is real output's)",
          stats.get("generating") is True and stats.get("ttft") is not None, stats)

    # The prefill-rope pin. With progress on, the server sends chunks DURING the
    # prefill; those must not shorten a healthy prefill from the request timeout to
    # the idle gap. The gap below is longer than idle_seconds and shorter than
    # first_byte_seconds, exactly like a slow box reading a big prompt on one slot.
    script = (sse(progress(1000, 100)) + [(0.45, ": ping")]
              + sse(progress(1000, 500))
              + sse(delta(content="done"), delta(finish="stop")) + [SSE_END])
    try:
        data, stats = fb._stream_chat(FakeResp(script), idle_seconds=0.25,
                                      first_byte_seconds=5)
        check("progress events do not make a healthy prefill look wedged",
              data["choices"][0]["message"]["content"] == "done",
              data["choices"][0]["message"])
    except Exception as e:                              # noqa: BLE001
        check("progress events do not make a healthy prefill look wedged", False,
              "%s: %s" % (type(e).__name__, e))

    # ...and once real output has arrived, the idle gap still applies.
    script = (sse(progress(1000, 1000)) + sse(delta(content="start"))
              + [(0.6, ": idle")] + sse(delta(content="more"), delta(finish="stop"))
              + [SSE_END])
    try:
        fb._stream_chat(FakeResp(script), idle_seconds=0.25, first_byte_seconds=5)
        check("after real output, a quiet gap is still an idle failure", False,
              "returned")
    except fb.StreamFailed as e:
        check("after real output, a quiet gap is still an idle failure",
              "quiet" in str(e), e)
    except Exception as e:                              # noqa: BLE001
        check("after real output, a quiet gap is still an idle failure", False,
              "%s: %s" % (type(e).__name__, e))

    # ---------------------------------------------------------- the status line
    fb.requests = FakeRequests(props=LLAMA_PROPS)
    try:
        check("status says the extensions are on for a llama.cpp box",
              fb.llama_extensions_summary().startswith("on: prompt progress"),
              fb.llama_extensions_summary())
        restore = with_llm(sse_ping_interval=9)
        try:
            check("...and names a configured ping interval",
                  "ping every 9s" in fb.llama_extensions_summary(),
                  fb.llama_extensions_summary())
        finally:
            restore()
        restore = with_llm(llama_extensions=False)
        try:
            check("status says why they are off (the switch)",
                  fb.llama_extensions_summary() == "off (llm.llama_extensions=false)",
                  fb.llama_extensions_summary())
        finally:
            restore()
        # A remote base_url must be reported as remote, and never probed.
        restore = with_llm(base_url="https://api.openai.com/v1")
        try:
            fb.requests = FakeRequests(props=LLAMA_PROPS)
            check("status says why they are off (off-LAN endpoint)",
                  "not on this LAN" in fb.llama_extensions_summary(),
                  fb.llama_extensions_summary())
            check("...and the off-LAN endpoint was not probed",
                  fb.requests.asked == [], fb.requests.asked)
        finally:
            restore()
            fb.requests = _rq
        fb.requests = FakeRequests(props=FOREIGN_PROPS)
        check("status says why they are off (not a llama.cpp /props)",
              "does not fingerprint llama.cpp" in fb.llama_extensions_summary(),
              fb.llama_extensions_summary())
    finally:
        fb.requests = _rq

    check("no progress and no output still reads as before",
          fb.stream_heartbeat({}) == "waiting for the first token",
          fb.stream_heartbeat({}))
    check("a fresh prompt reads as 0%",
          fb.stream_heartbeat({"prompt_progress": {"total": 1000, "processed": 0,
                                                   "cache": 0}})
          == "reading prompt · 0% (0/1,000 tok)",
          fb.stream_heartbeat({"prompt_progress": {"total": 1000, "processed": 0,
                                                   "cache": 0}}))
    check("halfway reads as 50%",
          fb.stream_heartbeat({"prompt_progress": {"total": 1000, "processed": 500,
                                                   "cache": 0}})
          == "reading prompt · 50% (500/1,000 tok)",
          fb.stream_heartbeat({"prompt_progress": {"total": 1000, "processed": 500,
                                                   "cache": 0}}))
    check("a cached prefix is excluded from the percentage",
          fb.stream_heartbeat({"prompt_progress": {"total": 1000, "processed": 750,
                                                   "cache": 500}})
          == "reading prompt · 50% (750/1,000 tok)",
          fb.stream_heartbeat({"prompt_progress": {"total": 1000, "processed": 750,
                                                   "cache": 500}}))
    check("output beats progress once it starts",
          fb.stream_heartbeat({"chars": 12,
                               "prompt_progress": {"total": 1000, "processed": 10,
                                                   "cache": 0}})
          == "writing · 12 chars",
          fb.stream_heartbeat({"chars": 12,
                               "prompt_progress": {"total": 1000, "processed": 10,
                                                   "cache": 0}}))
    check("a progress object with no total cannot divide by zero",
          fb.stream_heartbeat({"prompt_progress": {"processed": 5}})
          == "waiting for the first token")

    # ------------------------------------------------- the gate on the wire
    fake = FakeRequests(props=LLAMA_PROPS)
    fb.requests = fake
    try:
        seen = capture_payload()
        check("a llama.cpp endpoint is asked for prompt progress",
              bool(seen) and seen[0].get("return_progress") is True,
              seen[:1])
        check("...and nothing else was added to the body",
              bool(seen) and "sse_ping_interval" not in seen[0], seen[:1])
        check("...and the /props probe is what decided it",
              fake.asked.count("http://127.0.0.1:1/props") == 1, fake.asked)

        fake.asked = []
        seen = capture_payload(sse_ping_interval=7)
        check("a configured ping interval rides along when supported",
              bool(seen) and seen[0].get("sse_ping_interval") == 7, seen[:1])
    finally:
        fb.requests = _rq

    fb.requests = FakeRequests(props=FOREIGN_PROPS)
    try:
        seen = capture_payload()
        check("a non-llama.cpp endpoint gets NEITHER field",
              bool(seen) and "return_progress" not in seen[0]
              and "sse_ping_interval" not in seen[0], seen[:1])
        check("...and the request is still made (the gate never blocks a call)",
              bool(seen), seen[:1])
    finally:
        fb.requests = _rq

    fake = FakeRequests(props=LLAMA_PROPS)
    fb.requests = fake
    try:
        seen = capture_payload(llama_extensions=False, sse_ping_interval=7)
        check("the config switch keeps both fields off the wire",
              bool(seen) and "return_progress" not in seen[0]
              and "sse_ping_interval" not in seen[0], seen[:1])
        check("...and nothing is probed when the switch is off",
              not any(u.endswith("/props") for u in fake.asked), fake.asked)
    finally:
        fb.requests = _rq

    # A 400 that NAMES the field: the provider is saying "unknown argument", so it is
    # dropped and the same endpoint is retried - the belt to the /props braces, for a
    # server whose /props lies or sits behind a proxy that adds fields.
    restore = with_llm(stream=True, base_url="http://127.0.0.1:1/v1", model="main")
    fb.AGENT._window_cache = 32768
    fb.AGENT._window_at = 0.0
    fb.AGENT._envelope_cache = None
    fb.requests = FakeRequests(props=LLAMA_PROPS)
    seen = []
    real = fb._post_watchdog

    def fake_400(url, headers, payload, timeout, grace, cancel_event=None,
                 stream=False):
        seen.append(dict(payload))
        if len(seen) == 1:
            raise _rq.HTTPError("400", response=types.SimpleNamespace(
                status_code=400,
                text='{"error": "unknown field: return_progress"}'))
        return FakeResp(sse(delta(content="fine"), delta(finish="stop")) + [SSE_END])

    fb._post_watchdog = fake_400
    try:
        reply = fb.AGENT._chat([{"role": "system", "content": "s"},
                                {"role": "user", "content": "hi"}], session_key="f400")
    except Exception as e:                              # noqa: BLE001
        reply = {"content": "RAISED %s: %s" % (type(e).__name__, e)}
    finally:
        fb._post_watchdog = real
        fb.requests = _rq
        restore()
    check("a 400 naming return_progress is retried, not fatal",
          (reply.get("content") or "") == "fine", reply)
    check("the first attempt SENT return_progress",
          bool(seen) and seen[0].get("return_progress") is True, seen[:1])
    check("the retry dropped it",
          len(seen) >= 2 and "return_progress" not in seen[1], seen[:2])

    # The gate is per ENDPOINT, not per request: one call can walk from a llama.cpp
    # primary to a fallback that is not one, and the fields must not ride along.
    restore = with_llm(stream=True, base_url="http://127.0.0.1:1/v1",
                       allow_cloud_fallback=True,
                       fallbacks=[{"base_url": "http://10.0.0.9:9000/v1",
                                   "model": "fb", "api_key": "none"}])
    # same-endpoint retries off: this grades the per-ENDPOINT extension gate, so the
    # fallback must be the second request this call makes (tests/test_transient_retry.py
    # pins the retry policy).
    _saved_retries = fb.CONFIG["llm"].get("same_endpoint_retries")
    fb.CONFIG["llm"]["same_endpoint_retries"] = 0
    fb.AGENT._window_cache = 32768
    fb.AGENT._window_at = 0.0
    fb.AGENT._envelope_cache = None
    fb.requests = FakeRequests(by_host={"127.0.0.1": LLAMA_PROPS, "10.0.0.9": None})
    seen = []
    real = fb._post_watchdog

    def fake_fail(url, headers, payload, timeout, grace, cancel_event=None,
                  stream=False):
        seen.append((url, dict(payload)))
        raise _rq.HTTPError("500", response=types.SimpleNamespace(
            status_code=500, text="nope"))

    fb._post_watchdog = fake_fail
    try:
        fb.AGENT._chat([{"role": "system", "content": "s"},
                        {"role": "user", "content": "hi"}], session_key="fbfb")
    except Exception:                       # noqa: BLE001 - both endpoints fail
        pass
    finally:
        fb._post_watchdog = real
        fb.requests = _rq
        restore()
        if _saved_retries is None:
            fb.CONFIG["llm"].pop("same_endpoint_retries", None)
        else:
            fb.CONFIG["llm"]["same_endpoint_retries"] = _saved_retries
    check("both endpoints were tried", len(seen) >= 2, seen)
    check("the llama.cpp primary was ASKED for prompt progress",
          seen and seen[0][1].get("return_progress") is True, seen[:1])
    check("the non-llama.cpp fallback was NOT",
          len(seen) > 1 and "return_progress" not in seen[1][1], seen[1:2])

    print()
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
