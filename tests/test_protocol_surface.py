"""test_protocol_surface - one merged suite (test_llama_extensions, test_mcp).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: test_mcp: globals()-> _ns.
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


def _suite_test_llama_extensions():
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

    python tests/test_protocol_surface.py
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
        # fallback must be the second request this call makes (tests/test_retry_surface.py
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

        # ---- reasoning effort: three wire shapes (omp's catalog is the evidence) ---------
        # Auto sends nothing; an OpenAI-compatible endpoint takes reasoning_effort; /responses
        # takes reasoning.effort; an Anthropic-style endpoint takes a thinking BUDGET, with a
        # per-level table, a per-level override and a per-level wire remap.
        _saved_r = {k: fb.CONFIG["llm"].get(k) for k in
                    ("reasoning", "reasoning_mode", "reasoning_wire", "thinking_budgets")}
        try:
            fb.CONFIG["llm"]["reasoning"] = "auto"
            fb.CONFIG["llm"]["reasoning_mode"] = "auto"
            fb.CONFIG["llm"]["reasoning_wire"] = {}
            fb.CONFIG["llm"]["thinking_budgets"] = {}
            _p = {}
            fb.apply_reasoning(_p, "http://127.0.0.1:8081/v1", "main")
            check("reasoning: auto sends no field at all", _p == {}, _p)
            fb.CONFIG["llm"]["reasoning"] = "high"
            _p = {}
            fb.apply_reasoning(_p, "http://127.0.0.1:8081/v1", "main")
            check("an OpenAI-compatible endpoint gets reasoning_effort",
                  _p == {"reasoning_effort": "high"}, _p)
            _p = {}
            fb.apply_reasoning(_p, "https://api.openai.com/v1/responses", "main")
            check("a /responses endpoint gets reasoning.effort",
                  _p == {"reasoning": {"effort": "high"}}, _p)
            _p = {}
            fb.apply_reasoning(_p, "https://api.anthropic.com/v1", "main")
            check("an Anthropic-style endpoint gets a thinking budget",
                  _p == {"thinking": {"type": "enabled", "budget_tokens": 10000}}, _p)
            fb.CONFIG["llm"]["reasoning"] = "max"
            _p = {}
            fb.apply_reasoning(_p, "https://api.anthropic.com/v1", "main")
            check("...and the budget follows the level",
                  _p.get("thinking", {}).get("budget_tokens") == 64000, _p)
            fb.CONFIG["llm"]["thinking_budgets"] = {"max": 1234}
            _p = {}
            fb.apply_reasoning(_p, "https://api.anthropic.com/v1", "main")
            check("...and a per-level override wins over the table",
                  _p.get("thinking", {}).get("budget_tokens") == 1234, _p)
            fb.CONFIG["llm"]["thinking_budgets"] = {}
            fb.CONFIG["llm"]["reasoning_wire"] = {"max": "xhigh"}
            _p = {}
            fb.apply_reasoning(_p, "http://127.0.0.1:8081/v1", "main")
            check("a provider's own spelling is honoured (omp's reasoningEffortMap)",
                  _p == {"reasoning_effort": "xhigh"}, _p)
            fb.CONFIG["llm"]["reasoning"] = "off"
            _p = {}
            fb.apply_reasoning(_p, "http://127.0.0.1:8081/v1", "main")
            check("off sends nothing to an effort endpoint", _p == {}, _p)
            _p = {}
            fb.apply_reasoning(_p, "https://api.anthropic.com/v1", "main")
            check("...and disables thinking where that shape supports it",
                  _p == {"thinking": {"type": "disabled"}}, _p)
            fb.CONFIG["llm"]["reasoning"] = "junk"
            _p = {}
            fb.apply_reasoning(_p, "http://127.0.0.1:8081/v1", "main")
            check("a level nobody knows sends nothing", _p == {}, _p)
            fb.CONFIG["llm"]["reasoning"] = "auto"
            fb.AGENT.reasoning_overrides = {"reasoning-sess": "low"}
            check("a /reasoning session override wins over the config",
                  fb.reasoning_level("reasoning-sess") == "low",
                  fb.reasoning_level("reasoning-sess"))
            _p = {}
            fb.apply_reasoning(_p, "http://127.0.0.1:8081/v1", "reasoning-sess")
            check("...and shapes that conversation's payload",
                  _p == {"reasoning_effort": "low"}, _p)
            fb.CONFIG["llm"]["reasoning_mode"] = "budget"
            _p = {}
            fb.apply_reasoning(_p, "http://127.0.0.1:8081/v1", "reasoning-sess")
            check("llm.reasoning_mode forces a shape regardless of the URL",
                  _p == {"thinking": {"type": "enabled", "budget_tokens": 1000}}, _p)
        finally:
            fb.AGENT.reasoning_overrides = {}
            for k, v in _saved_r.items():
                if v is None:
                    fb.CONFIG["llm"].pop(k, None)
                else:
                    fb.CONFIG["llm"][k] = v

        print()
        if FAILURES:
            print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
            return 1
        print("all checks passed")
        return 0
    return main()


def _suite_test_mcp():
    """MCP: the stdio client, its two protocol revisions, and the rent rule.

The stub servers here are real processes speaking newline-delimited JSON-RPC: one that
demands the stateless 2026-07-28 shape (every request must carry `_meta`), one that
demands the older `initialize` handshake first, and one that never answers. What is
graded: discovery, a call, the one-time fallback, process reuse, the honest errors
(unknown server/tool, no answer, dead server), and the rule that a box with no servers
configures registers no tool at all.

    python tests/test_protocol_surface.py
"""
    import importlib.util
    import json
    import logging
    import os
    import shutil
    import sys
    import tempfile
    import time
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / (os.environ.get("TINYCMDR_TEST_APP")
                  or os.environ.get("TINYCMDR_SRC") or "tinycmdr.py")
    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-mcp"
    STAGE.mkdir(parents=True, exist_ok=True)
    STUB = STAGE / "mcp_stub.py"
    STUB.write_text(r'''
import json, sys
legacy = "--legacy" in sys.argv
silent = "--silent" in sys.argv
srvreq = "--srvreq" in sys.argv
die_on_call = "--die-on-call" in sys.argv
stray = 12 if "--strays" in sys.argv else 0
initialized = False

def send(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()

def reply(o):
    # --srvreq: a server-initiated REQUEST whose id collides with the client's request,
    # sent just before the real reply. --strays: replies for ids the client never asked
    # for, sent ahead of the real one.
    if srvreq:
        send({"jsonrpc": "2.0", "id": o.get("id"), "method": "ping", "params": {}})
    for k in range(stray):
        send({"jsonrpc": "2.0", "id": 900000 + k, "result": {"content": [], "isError": False}})
    send(o)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if "id" not in msg:
        continue
    rid, method = msg.get("id"), msg.get("method")
    params = msg.get("params") or {}
    if silent:
        continue
    if not legacy and "_meta" not in params:
        reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32022,
             "message": "missing _meta: this server wants the stateless revision"}})
        continue
    if legacy and not initialized and method != "initialize":
        reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602,
             "message": "initialize first"}})
        continue
    if method == "initialize":
        initialized = True
        reply({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-06-18",
              "capabilities": {"tools": {}}, "serverInfo": {"name": "stub", "version": "1"}}})
        continue
    if method == "tools/list":
        res = {"tools": [{"name": "echo", "description": "echo the text back",
                          "inputSchema": {"type": "object",
                                          "properties": {"text": {"type": "string"}},
                                          "required": ["text"]}},
                         {"name": "boom", "description": "always fails",
                          "inputSchema": {"type": "object", "properties": {}}}]}
        if not legacy:
            res["resultType"] = "complete"
        reply({"jsonrpc": "2.0", "id": rid, "result": res})
        continue
    if method == "tools/call":
        if die_on_call:
            sys.stderr.write("boom: the tool call killed this server\n")
            sys.stderr.flush()
            sys.exit(4)
        if params.get("name") == "boom":
            res = {"content": [{"type": "text", "text": "boom failed"}], "isError": True}
            if not legacy:
                res["resultType"] = "complete"
            reply({"jsonrpc": "2.0", "id": rid, "result": res})
            continue
        if params.get("name") != "echo":
            reply({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "no such tool"}], "isError": True,
                **({} if legacy else {"resultType": "complete"})}})
            continue
        text = str((params.get("arguments") or {}).get("text", ""))
        res = {"content": [{"type": "text", "text": "echo: " + text}], "isError": False}
        if not legacy:
            res["resultType"] = "complete"
        reply({"jsonrpc": "2.0", "id": rid, "result": res})
        continue
    reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601,
         "message": "method not found"}})
''', encoding="utf-8")


    def stage(name, servers=None):
        work = STAGE / name
        work.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC, work / "tinycmdr.py")
        (work / "config.json").write_text(
            json.dumps({"llm": {"model": "stub"}, "agent": {"mcp_servers": servers or {}}}),
            encoding="utf-8")
        spec = importlib.util.spec_from_file_location("tinycmdr_mcp_" + name,
                                                      work / "tinycmdr.py")
        fb = importlib.util.module_from_spec(spec)
        sys.modules["tinycmdr_mcp_" + name] = fb
        spec.loader.exec_module(fb)
        return fb


    def stub_spec(*flags):
        return {"command": sys.executable, "args": [str(STUB)] + list(flags)}


    ON = stage("on", servers={"stub": stub_spec()})
    OFF = stage("off")
    FAILURES, PASSES = [], []


    def check(name, cond, detail=""):
        if cond:
            PASSES.append(name)
            print(f"ok   {name}")
        else:
            FAILURES.append(name)
            print(f"FAIL {name}: {detail}")


    def test_the_modern_revision_discovers_and_calls():
        out = ON.tool_mcp({"action": "tools", "server": "stub"}, {})
        check("tools lists the server's tools", "echo" in out and "exposes 2 tool" in out, out)
        out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "echo",
                           "arguments": {"text": "hi"}}, {})
        check("call returns the tool's text", out.endswith("echo: hi"), out)
        check("...with the server and tool named", "stub/echo" in out, out)
        ent = ON._MCP_PROCS["stub"]
        check("the stateless revision was detected", ent["modern"] is True, ent["modern"])
        pid = ent["proc"].pid
        ON.tool_mcp({"action": "call", "server": "stub", "tool": "echo",
                     "arguments": {"text": "again"}}, {})
        check("the server process is reused between calls",
              ON._MCP_PROCS["stub"]["proc"].pid == pid, ON._MCP_PROCS["stub"]["proc"].pid)


    def test_a_legacy_server_gets_the_handshake():
        ON.CONFIG["agent"]["mcp_servers"]["old"] = stub_spec("--legacy")
        try:
            out = ON.tool_mcp({"action": "tools", "server": "old"}, {})
            check("a pre-2026 server is discovered after the fallback",
                  "echo" in out, out)
            ent = ON._MCP_PROCS["old"]
            check("...and is remembered as legacy", ent["modern"] is False, ent["modern"])
            out = ON.tool_mcp({"action": "call", "server": "old", "tool": "echo",
                               "arguments": {"text": "legacy"}}, {})
            check("a call on the legacy path answers", out.endswith("echo: legacy"), out)
        finally:
            ON.CONFIG["agent"]["mcp_servers"].pop("old", None)


    def test_a_configured_command_is_screened_before_it_is_spawned():
        """The absolute tier, over agent.mcp_servers.

    The manifest loader for a dropped-in tool refuses a command that matches an
    absolute-tier pattern at load time, because that file arrives from outside the bot.
    config.json is the same kind of file - the agent's own write tools edit it - and its
    command reached subprocess.Popen with no tier at all (measured 2026-10-07). The
    refusal names the server and the pattern, and nothing is spawned.
    """
        # The command is blocked by the never tier AND the binary does not exist on any test
        # host, so the falsification run (pre-fix, where nothing screens it) cannot do damage:
        # Popen raises and the refusal is only about the pattern.
        ON.CONFIG["agent"]["mcp_servers"]["blocked"] = {
            "command": "mkfs.ext4", "args": ["/dev/sda1"]}
        try:
            out = ON.tool_mcp({"action": "call", "server": "blocked", "tool": "echo"}, {})
            check("an MCP command that matches the absolute tier is refused",
                  out.startswith("ERROR") and "blocked" in out, out)
            check("...by the same rule the drop-in loader uses, and it says so",
                  "safety pattern" in out and "in-band" in out, out)
            check("...and nothing was spawned for it", "blocked" not in ON._MCP_PROCS,
                  sorted(ON._MCP_PROCS))
            out = ON.tool_mcp({"action": "list"}, {})
            check("the server list marks it REFUSED rather than offering it",
                  "REFUSED" in out and "blocked" in out, out)
        finally:
            ON.CONFIG["agent"]["mcp_servers"].pop("blocked", None)


    def test_the_errors_are_honest():
        out = ON.tool_mcp({"action": "tools", "server": "ghost"}, {})
        check("an unknown server is refused by name",
              out.startswith("ERROR") and "stub" in out, out)
        out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "nope"}, {})
        check("an unknown tool names the real ones",
              out.startswith("ERROR") and "echo" in out, out)
        out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "boom"}, {})
        check("a tool result with isError carries the ERROR prefix",
              out.startswith("ERROR: stub/boom") and "boom failed" in out, out)
        ON.CONFIG["agent"]["mcp_servers"]["quiet"] = stub_spec("--silent")
        ON.CONFIG["agent"]["mcp_timeout"] = 0.6
        try:
            out = ON.tool_mcp({"action": "tools", "server": "quiet"}, {})
            check("a server that never answers times out honestly",
                  "no answer within" in out, out)
        finally:
            ON.CONFIG["agent"]["mcp_servers"].pop("quiet", None)
            ON.CONFIG["agent"]["mcp_timeout"] = 60
        ON.CONFIG["agent"]["mcp_servers"]["dead"] = {
            "command": sys.executable, "args": ["-c", "pass"]}
        try:
            out = ON.tool_mcp({"action": "tools", "server": "dead"}, {})
            check("a server that exits is reported, not retried forever",
                  out.startswith("ERROR") and "exited" in out, out)
        finally:
            ON.CONFIG["agent"]["mcp_servers"].pop("dead", None)
        ON.CONFIG["agent"]["mcp_servers"]["nope"] = {"command": "definitely-not-a-binary-xyz"}
        try:
            out = ON.tool_mcp({"action": "tools", "server": "nope"}, {})
            check("a command that cannot start says so",
                  out.startswith("ERROR") and "could not start" in out, out)
        finally:
            ON.CONFIG["agent"]["mcp_servers"].pop("nope", None)


    def test_no_servers_means_no_tool():
        check("off: a box with no servers has no mcp tool", "mcp" not in OFF.CORE_TOOLS)
        out = OFF.tool_mcp({"action": "list"}, {})
        check("...and the tool itself says what to configure",
              out.startswith("ERROR") and "mcp_servers" in out, out)
        check("on: the same build with a server registers it", "mcp" in ON.CORE_TOOLS)
        check("...hidden from the default core", "mcp" not in ON._DEFAULT_CORE)
        check("...under the per-tool rent cap",
              len(json.dumps(ON.CORE_TOOLS["mcp"]["schema"])) <= 1200,
              len(json.dumps(ON.CORE_TOOLS["mcp"]["schema"])))


    def _capture_log(module):
        """(seen, handler): the records a module logs while a block runs."""
        seen = []
        handler = logging.Handler()
        handler.emit = lambda r: seen.append(r.getMessage())
        module.log.addHandler(handler)
        return seen, handler


    def _call(fn, *a, **k):
        """(result, error): run one door and turn a pre-fix raise into a graded FAIL."""
        try:
            return fn(*a, **k), ""
        except Exception as exc:                                     # noqa: BLE001
            return "", "%s: %s" % (type(exc).__name__, exc)


    def test_a_wrong_servers_shape_answers_never_raises():
        saved = ON.CONFIG["agent"]["mcp_servers"]
        try:
            for bad in (["a", "b"], "stub", 3):
                ON.CONFIG["agent"]["mcp_servers"] = bad
                raised = ""
                try:
                    out = ON.tool_mcp({"action": "list"}, {})
                except Exception as exc:                             # noqa: BLE001
                    out, raised = "", "%s: %s" % (type(exc).__name__, exc)
                check("a %s mcp_servers answers an ERROR naming the map shape"
                      % type(bad).__name__,
                      not raised and out.startswith("ERROR") and "map" in out
                      and "command" in out, raised or out)
            ON.CONFIG["agent"]["mcp_servers"] = ["a", "b"]
            _name, _args, door = ON.AGENT._exec_tool(
                {"function": {"name": "mcp", "arguments": '{"action": "list"}'}},
                {"session_key": "mcp-shape"})
            check("...and the same answer comes out of the tool door",
                  door.startswith("ERROR") and "map" in door, door)
            ON.CONFIG["agent"]["mcp_servers"] = {"bad": "oops"}
            out, raised = _call(ON.tool_mcp, {"action": "list"}, {})
            check("the list action survives a non-map entry",
                  not raised and "bad" in out and "not a {command, args?} map" in out,
                  raised or out)
            out = ON.tool_mcp({"action": "tools", "server": "bad"}, {})
            check("...and a named entry that is not a map says so",
                  out.startswith("ERROR") and "not a map of" in out, out)
        finally:
            ON.CONFIG["agent"]["mcp_servers"] = saved


    def test_a_bad_timeout_value_falls_back_with_one_warning():
        seen, handler = _capture_log(ON)
        ON.CONFIG["agent"]["mcp_timeout"] = "60s"
        try:
            out, raised = _call(ON.tool_mcp, {"action": "tools", "server": "stub"}, {})
        finally:
            ON.CONFIG["agent"]["mcp_timeout"] = 60
            ON.log.removeHandler(handler)
        warned = [m for m in seen if "mcp_timeout" in m]
        check("a non-numeric mcp_timeout falls back to the working default",
              not raised and "echo" in out, raised or out)
        check("...with one WARNING naming the key and the value",
              len(warned) == 1 and "60s" in warned[0], seen)


    def test_a_zero_timeout_means_zero_not_sixty():
        ON.CONFIG["agent"]["mcp_servers"]["zero"] = stub_spec()
        ON.CONFIG["agent"]["mcp_timeout"] = 0
        try:
            t0 = time.monotonic()
            out = ON.tool_mcp({"action": "tools", "server": "zero"}, {})
            elapsed = time.monotonic() - t0
        finally:
            ON.CONFIG["agent"]["mcp_timeout"] = 60
            ON.CONFIG["agent"]["mcp_servers"].pop("zero", None)
        check("mcp_timeout 0 fails at once instead of hiding a 60",
              out.startswith("ERROR") and "within 0s" in out, out)
        check("...and it really is immediate", elapsed < 1.0, "%.3fs" % elapsed)


    def test_a_failed_handshake_is_remembered():
        ON.CONFIG["agent"]["mcp_servers"]["quiet2"] = stub_spec("--silent")
        ON.CONFIG["agent"]["mcp_timeout"] = 0.3
        try:
            t0 = time.monotonic()
            first = ON.tool_mcp({"action": "tools", "server": "quiet2"}, {})
            pay = time.monotonic() - t0
            t1 = time.monotonic()
            second = ON.tool_mcp({"action": "tools", "server": "quiet2"}, {})
            again = time.monotonic() - t1
            ent = ON._MCP_PROCS["quiet2"]
            window = ent.get("fail_until", 0) - time.time()
            ent["fail_until"] = 0.0                     # pretend the window elapsed
            t2 = time.monotonic()
            ON.tool_mcp({"action": "tools", "server": "quiet2"}, {})
            retried = time.monotonic() - t2
        finally:
            ON.CONFIG["agent"]["mcp_timeout"] = 60
            ON.CONFIG["agent"]["mcp_servers"].pop("quiet2", None)
        check("a failed handshake is paid once, not on every call", again < 0.15,
              "%.3fs then %.3fs" % (pay, again))
        check("...and the cached answer is the same honest reason",
              second == first and "no answer within" in second, second)
        check("...the memory is a bounded window", 0 < window <= 60.5, window)
        check("...and past the window the call pays again", retried >= 0.25,
              "%.3fs" % retried)


    def test_a_server_without_a_command_says_so():
        ON.CONFIG["agent"]["mcp_servers"]["nocmd"] = {"args": []}
        try:
            out = ON.tool_mcp({"action": "tools", "server": "nocmd"}, {})
        finally:
            ON.CONFIG["agent"]["mcp_servers"].pop("nocmd", None)
        check("a server configured without a command says exactly that",
              out.startswith("ERROR") and "WITHOUT a command" in out
              and "no such server" not in out, out)


    def test_a_death_is_noticed_and_says_why():
        ON.CONFIG["agent"]["mcp_servers"]["crash"] = {
            "command": sys.executable,
            "args": ["-c", "import sys; sys.stderr.write('boom: bad module\\n'); "
                           "sys.exit(3)"]}
        ON.CONFIG["agent"]["mcp_timeout"] = 5
        try:
            t0 = time.monotonic()
            out = ON.tool_mcp({"action": "tools", "server": "crash"}, {})
            elapsed = time.monotonic() - t0
        finally:
            ON.CONFIG["agent"]["mcp_timeout"] = 60
            ON.CONFIG["agent"]["mcp_servers"].pop("crash", None)
        check("a server that dies is noticed inside the timeout, not after it",
              out.startswith("ERROR") and "exited" in out and elapsed < 2.5,
              "%s (%.2fs)" % (out, elapsed))
        check("...and its stderr is the reason it gives",
              "boom: bad module" in out, out)


    def test_a_call_failure_carries_the_server_stderr():
        ON.CONFIG["agent"]["mcp_servers"]["dier"] = stub_spec("--die-on-call")
        ON.CONFIG["agent"]["mcp_timeout"] = 2
        try:
            listed = ON.tool_mcp({"action": "tools", "server": "dier"}, {})
            out = ON.tool_mcp({"action": "call", "server": "dier", "tool": "echo",
                               "arguments": {"text": "x"}}, {})
        finally:
            ON.CONFIG["agent"]["mcp_timeout"] = 60
            ON.CONFIG["agent"]["mcp_servers"].pop("dier", None)
        check("the call path works up to the crash", "echo" in listed, listed)
        check("a call that dies carries the server's stderr tail",
              out.startswith("ERROR") and "exited" in out
              and "boom: the tool call killed" in out, out)


    def test_arguments_arriving_as_a_json_string():
        ON.CONFIG["agent"]["mcp_timeout"] = 0.8
        try:
            out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "echo",
                               "arguments": '{"text": "hi"}'}, {})
            bad = ON.tool_mcp({"action": "call", "server": "stub", "tool": "echo",
                               "arguments": '{"text": '}, {})
            notobj = ON.tool_mcp({"action": "call", "server": "stub", "tool": "echo",
                                  "arguments": "[1, 2]"}, {})
        finally:
            ON.CONFIG["agent"]["mcp_timeout"] = 60
        check("a JSON-string arguments is parsed, not forwarded verbatim",
              out.endswith("echo: hi"), out)
        check("...and text that is not JSON answers a clear ERROR",
              bad.startswith("ERROR") and "does not parse" in bad, bad)
        check("...and a JSON value that is not an object says so",
              notobj.startswith("ERROR") and "must be a JSON object" in notobj, notobj)


    def test_the_tool_name_is_matched_case_insensitively():
        out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "Echo",
                           "arguments": {"text": "Hi"}}, {})
        check("a differently-cased tool name is accepted", out.endswith("echo: Hi"), out)
        check("...and the call goes out under the server's spelling",
              "stub/echo ->" in out, out)
        out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "ECHO",
                           "arguments": {"text": "Yo"}}, {})
        check("...for any casing", out.endswith("echo: Yo"), out)
        out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "ghost_tool"}, {})
        check("...and a genuinely absent tool still lists the real ones",
              out.startswith("ERROR") and "echo" in out, out)


    def test_a_server_request_is_never_taken_for_a_reply():
        ON.CONFIG["agent"]["mcp_servers"]["srvreq"] = stub_spec("--srvreq")
        try:
            out = ON.tool_mcp({"action": "tools", "server": "srvreq"}, {})
            parked = ON._MCP_PROCS["srvreq"].get("parked") or {}
        finally:
            ON.CONFIG["agent"]["mcp_servers"].pop("srvreq", None)
        check("a server-initiated request with the in-flight id is not consumed as the reply",
              "echo" in out and "exposes 2 tool" in out, out)
        check("...and it is not parked as a reply either", parked == {}, parked)


    def test_a_late_reply_is_parked_not_dropped():
        ON.CONFIG["agent"]["mcp_servers"]["strays"] = stub_spec("--strays")
        try:
            out = ON.tool_mcp({"action": "tools", "server": "strays"}, {})
            parked = ON._MCP_PROCS["strays"].get("parked") or {}
        finally:
            ON.CONFIG["agent"]["mcp_servers"].pop("strays", None)
        check("mismatched replies do not break the real answer", "echo" in out, out)
        check("...they are parked by id instead of dropped",
              parked.get(900011) is not None, sorted(parked))
        check("...bounded to the last few, oldest evicted",
              len(parked) == 8 and 900000 not in parked, sorted(parked))


    def main():
        try:
            tests = [v for k, v in sorted(_ns.items()) if k.startswith("test_")]
            for fn in tests:
                fn()
        finally:
            for ent in list(ON._MCP_PROCS.values()):
                try:
                    ent["proc"].kill()
                except Exception:
                    pass
        print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
        return 1 if FAILURES else 0
    _ns = dict(locals())
    return main()


def main():
    rc = 0
    for name, fn in (("test_llama_extensions", _suite_test_llama_extensions), ("test_mcp", _suite_test_mcp)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
