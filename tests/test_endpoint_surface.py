"""test_endpoint_surface - one merged suite (test_endpoint_window, test_endpoint_learn, test_read_window).

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


def _suite_test_endpoint_window():
    """How the harness asks an endpoint how much context it serves.

`_detect_window` is the one place the context budget gets a number that did not come from
config, and it has four routes because there is no standard one: vLLM reports
max_model_len and llama.cpp carries n_ctx under meta on /v1/models, any llama.cpp build
answers /props at the server root, Ollama answers /api/ps with the context_length it is
actually serving, and SGLang answers /get_server_info with the model's own context_length.

What this suite guards, in order of what would hurt most:

  1. The window is NEVER over-reported. A budget an order of magnitude too large does not
     fail loudly - it overflows mid-run, and on a slow local endpoint that spends exactly
     the minutes this harness exists to save. Ollama's /api/show carries the model's
     MAXIMUM context while it serves num_ctx (4096 by default), so /api/show must stay
     unprobed; SGLang's max_total_num_tokens is the KV-cache budget shared across
     concurrent requests rather than a per-request window; and an endpoint that does not
     say must come back 0 rather than a guess.
  2. 0 means "did not say", and every route falls through to it rather than to a default.
  3. The probes ask the SERVER ROOT (.../props, .../api/ps), never the OpenAI path.
  4. An unrelated server's 200 is not read as an answer: each route checks the shape of
     the reply that fingerprints it.

    python tests/test_endpoint_surface.py
"""
    import importlib.util
    import shutil
    import sys
    import tempfile
    import time
    import types
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / "tinycmdr.py"

    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-window"
    if STAGE.exists():
        shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    shutil.copy2(BASE / "tests" / "fixture-config.json", STAGE / "config.json")
    spec = importlib.util.spec_from_file_location("tinycmdr_window", STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_window"] = fb
    spec.loader.exec_module(fb)

    import requests as _rq      # noqa: E402  (the module's own transport, for HTTPError)

    PASSES, FAILS = [], []


    def check(name, cond, detail=""):
        (PASSES if cond else FAILS).append(name)
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


    # The replies, cut to the keys each route's fingerprint reads.
    VLLM_MODELS = {"object": "list", "data": [{"id": "main", "max_model_len": 32768}]}
    LLAMA_MODELS = {"object": "list", "data": [{"id": "main", "meta": {"n_ctx": 131072}}]}
    LLAMA_PROPS = {"default_generation_settings": {"n_ctx": 65536}}
    OLLAMA_PS = {"models": [{"name": "main:latest", "context_length": 8192}]}
    # SGLang's /get_server_info, cut to the keys that matter. max_total_num_tokens is the
    # KV-cache budget across all concurrent requests - an order of magnitude above the window.
    SGLANG_INFO = {"model_path": "meta-llama/Llama-3-8B", "context_length": 32768,
                   "max_req_input_len": 30000, "max_total_num_tokens": 819200}
    SGLANG_INPUT_ONLY = {"model_path": "x", "max_req_input_len": 30000}
    SGLANG_CAPACITY_ONLY = {"model_path": "x", "max_total_num_tokens": 819200}
    # What Ollama answers when nothing is loaded - and the shape /api/show would give, which
    # carries the model MAXIMUM and must never be read as the served window.
    OLLAMA_PS_EMPTY = {"models": []}
    OLLAMA_SHOW = {"model_info": {"llama.context_length": 131072},
                   "parameters": "num_ctx 8192\nstop <|eot_id|>"}
    # A 200 from a server that is not answering the question: the right shape of nothing.
    FOREIGN_LIST = {"object": "list"}


    class Fake:
        """The module's `requests`, with each route answered by URL fragment.

    Everything else delegates to the real module, so the code under test still gets real
    `HTTPError`. A fragment mapped to None is a 404, which is what a box that is not that
    server does - the case the fallthrough depends on.
    """

        def __init__(self, by_path=None, real=None):
            self.by_path = by_path or {}
            self.real = real or _rq
            self.asked = []
            self.posted = []

        def _answer(self, url):
            self.asked.append(url)
            for frag, payload in self.by_path.items():
                if frag in url:
                    if payload is None:
                        break
                    return types.SimpleNamespace(json=lambda p=payload: p)
            raise self.real.HTTPError("HTTP 404", response=types.SimpleNamespace(
                status_code=404, text="not found"))

        def get(self, url, **kw):
            return self._answer(url)

        def post(self, url, **kw):
            self.posted.append(url)
            return types.SimpleNamespace(json=lambda: OLLAMA_SHOW)

        def __getattr__(self, name):
            return getattr(self.real, name)


    def window(fake, base="http://127.0.0.1:1/v1"):
        """One `_detect_window` against the stub, with the real transport restored."""
        real = fb.requests
        fb.requests = fake
        try:
            return fb._detect_window(base, {})
        finally:
            fb.requests = real


    def main():
        want = fb.CONFIG["llm"].get("model") or "main"

        # ----------------------------------------- an empty answer is not a window (v1.0.59)
        # Measured 2026-10-03 on a live install: one probe of a busy llama.cpp server timed out,
        # and the harness cached "0" as if it were a window for the whole WINDOW_TTL (5 min).
        # Every call in that window took the assumed branch - budget 8000 + static 4157 + reply
        # 2048 = a 14,205-token "window" that MOVED with the static prompt - while the server
        # serves 131,072. An empty answer now expires in WINDOW_MISS_TTL and is re-asked.
        real_detect_miss = fb._detect_window
        real_cache = getattr(fb.AGENT, "_window_cache", None)
        try:
            _base = "http://127.0.0.1:8081/v1"
            _root = fb._endpoint_root(_base)
            fb.AGENT._window_cache = {}
            _asked = []
            fb._detect_window = lambda url, headers=None: (_asked.append(url) or 0)
            check("a probe that came back empty is reported as 0",
                  fb.AGENT._endpoint_window(_base) == 0, _asked)
            fb._detect_window = lambda url, headers=None: (_asked.append(url) or 131072)
            check("...and it is not trusted for the full TTL (still inside the miss TTL)",
                  fb.AGENT._endpoint_window(_base) == 0 and len(_asked) == 1, _asked)
            _at, _val = fb.AGENT._window_cache[_root]
            fb.AGENT._window_cache[_root] = (_at - fb.WINDOW_MISS_TTL - 1, _val)
            check("...and after WINDOW_MISS_TTL the server's real answer is adopted",
                  fb.AGENT._endpoint_window(_base) == 131072 and len(_asked) == 2, _asked)
            check("...whereas a REAL answer is kept for the long TTL",
                  fb.AGENT._endpoint_window(_base) == 131072 and len(_asked) == 2, _asked)
        finally:
            fb._detect_window = real_detect_miss
            fb.AGENT._window_cache = real_cache if real_cache is not None else {}

        # ------------------------------------------------------ one retry when a route BLIPS
        # A route that raises is a busy or freshly-started server; asking once more in a moment
        # turns a blip into the right window instead of the assumed one. A server that ANSWERS
        # without a window is not retried - that is an answer, not a blip.
        class _Flaky:
            def __init__(self):
                self.models_calls = 0
                self.real = _rq

            def get(self, url, **kw):
                if url.endswith("/v1/models"):
                    self.models_calls += 1
                    if self.models_calls == 1:
                        raise _rq.ConnectionError("server busy")
                    return types.SimpleNamespace(json=lambda: LLAMA_MODELS)
                raise _rq.ConnectionError("down")

            def __getattr__(self, name):
                return getattr(self.real, name)

        _flaky = _Flaky()
        check("a blip on every route is retried once, and the retry's answer is used",
              window(_flaky) == 131072 and _flaky.models_calls == 2, _flaky.models_calls)

        # ------------------------------------------------- llm.context_window pins the window
        _saved_cw = fb.CONFIG["llm"].get("context_window")
        _saved_ceiling = fb.CONFIG["llm"].get("max_context_tokens")
        try:
            # The fixture sets max_context_tokens=110000: the ceiling would cap the budget and
            # mask the pin's own arithmetic, so clear it for this pair of checks.
            fb.CONFIG["llm"]["max_context_tokens"] = "auto"
            fb._detect_window = lambda url, headers=None: 131072
            fb.CONFIG["llm"]["context_window"] = 262144
            fb.AGENT._window_cache, fb.AGENT._envelope_cache = {}, None
            _env = fb.AGENT._envelope("pin-sess")
            check("llm.context_window pins the window over the endpoint's answer",
                  _env["window"] == 262144 and _env["source"] == "config-window", _env)
            check("...and the arithmetic follows the pin",
                  _env["budget"] == max(fb.ENVELOPE_MIN_BUDGET,
                                        _env["window"] - _env["static"] - _env["reply"]), _env)
            check("...and status names it, so a pin cannot read like a server fact",
                  "pinned by llm.context_window" in fb.envelope_line(_env),
                  fb.envelope_line(_env))
            fb.CONFIG["llm"]["context_window"] = "auto"
            fb._detect_window = lambda url, headers=None: 0
            fb.AGENT._window_cache, fb.AGENT._envelope_cache = {}, None
            _env2 = fb.AGENT._envelope("assume-sess")
            check("an endpoint that will not say is labelled ASSUMED, with the remedy",
                  _env2["source"] == "assumed" and "ASSUMED" in fb.envelope_line(_env2)
                  and "llm.context_window" in fb.envelope_line(_env2), fb.envelope_line(_env2))
            check("...and the assumed branch keeps its conservative 8000-token budget",
                  _env2["budget"] == 8000, _env2)
        finally:
            fb.CONFIG["llm"]["context_window"] = _saved_cw
            fb.CONFIG["llm"]["max_context_tokens"] = _saved_ceiling
            fb._detect_window = real_detect_miss       # the stub must not leak into the routes
            fb.AGENT._window_cache, fb.AGENT._envelope_cache = {}, None

        # ------------------------------- a host whose window is DOCUMENTED (presets)
        # Operator, 2026-10-04: a DeepSeek endpoint that reports nothing ran at the assumed
        # 8000 with replies clipped to 2048 ("you must write 1000000 explicitly"). A
        # reporting-nothing HOST now gets its documented window: the built-in table names a
        # few providers, llm.window_presets beats it, llm.max_context_tokens still caps it,
        # and the model NAME is never consulted (F-19's rule, kept).
        _saved_llm = {k: fb.CONFIG["llm"].get(k) for k in
                      ("base_url", "max_context_tokens", "window_presets")}
        try:
            fb.CONFIG["llm"]["max_context_tokens"] = "auto"
            fb.CONFIG["llm"].pop("window_presets", None)
            fb.CONFIG["llm"]["base_url"] = "https://api.deepseek.com/v1"
            fb._detect_window = lambda url, headers=None: 0
            fb.AGENT._window_cache, fb.AGENT._envelope_cache = {}, None
            _penv = fb.AGENT._envelope("preset-sess")
            check("a hosted endpoint that reports nothing gets its DOCUMENTED window",
                  _penv["window"] == 128000
                  and _penv["source"] == "preset:api.deepseek.com", _penv)
            check("...so the reply is no longer clamped to the assumed 2048",
                  _penv["reply"] > 2048, _penv["reply"])
            check("...and the line names the source, never the server",
                  "window from api.deepseek.com" in fb.envelope_line(_penv),
                  fb.envelope_line(_penv))
            fb.CONFIG["llm"]["window_presets"] = {"api.deepseek.com": 1000000}
            fb.AGENT._envelope_cache = None
            _penv2 = fb.AGENT._envelope("preset-sess")
            check("llm.window_presets beats the built-in table",
                  _penv2["window"] == 1000000
                  and _penv2["source"] == "window_presets:api.deepseek.com", _penv2)
            fb.CONFIG["llm"]["base_url"] = "https://api.example.com/v1"
            fb.AGENT._envelope_cache = None
            _penv3 = fb.AGENT._envelope("unknown-sess")
            check("an unknown host still assumes, and says so",
                  _penv3["source"] == "assumed" and _penv3["budget"] == 8000, _penv3)
            fb.CONFIG["llm"]["max_context_tokens"] = 20000
            fb.CONFIG["llm"]["base_url"] = "https://api.deepseek.com/v1"
            fb.AGENT._envelope_cache = None
            _penv4 = fb.AGENT._envelope("cap-sess")
            check("...and llm.max_context_tokens still caps a preset's budget",
                  _penv4["budget"] == max(fb.ENVELOPE_MIN_BUDGET, 20000), _penv4)
        finally:
            for _k, _v in _saved_llm.items():
                if _v is None:
                    fb.CONFIG["llm"].pop(_k, None)
                else:
                    fb.CONFIG["llm"][_k] = _v
            fb._detect_window = real_detect_miss
            fb.AGENT._window_cache, fb.AGENT._envelope_cache = {}, None

        # ------------------------------------------------------------- the three routes
        check("vLLM's max_model_len is the window",
              window(Fake({"/v1/models": VLLM_MODELS})) == 32768)
        check("llama.cpp's meta.n_ctx on /v1/models is the window",
              window(Fake({"/v1/models": LLAMA_MODELS})) == 131072)
        # A hosted provider puts the window ON the model entry (DeepSeek:
        # context_window 1048576 / max_output_tokens 393216).
        OPENAI_WINDOW = {"object": "list", "data": [
            {"id": "main", "context_window": 1048576, "max_output_tokens": 393216}]}
        check("an OpenAI-style context_window on the model entry is the window",
              window(Fake({"/v1/models": OPENAI_WINDOW})) == 1048576)
        HIDDEN = {"object": "list", "data": [
            {"id": "deepseek-flash", "context_window": 1048576},
            {"id": "deepseek-v4-pro", "context_window": 1048576}]}
        _keep_model = fb.CONFIG["llm"]["model"]
        fb.CONFIG["llm"]["model"] = "deepseek-v4-flash"      # not advertised
        check("an unadvertised configured id still gets the window when every entry agrees",
              window(Fake({"/v1/models": HIDDEN})) == 1048576)
        DISAGREE = {"object": "list", "data": [
            {"id": "a", "context_window": 8192}, {"id": "b", "context_window": 131072}]}
        check("...but entries that disagree stay unknown (no guess)",
              window(Fake({"/v1/models": DISAGREE})) == 0)
        fb.CONFIG["llm"]["model"] = _keep_model
        check("llama.cpp on /props is the fallback when /v1/models says nothing",
              window(Fake({"/v1/models": FOREIGN_LIST, "/props": LLAMA_PROPS})) == 65536)
        check("Ollama's /api/ps context_length is the window",
              window(Fake({"/api/ps": OLLAMA_PS}), "http://127.0.0.1:11434/v1") == 8192)
        check("SGLang's /get_server_info context_length is the window",
              window(Fake({"/get_server_info": SGLANG_INFO}),
                     "http://127.0.0.1:30000/v1") == 32768)
        check("...and max_req_input_len is the fallback when context_length is absent",
              window(Fake({"/get_server_info": SGLANG_INPUT_ONLY}),
                     "http://127.0.0.1:30000/v1") == 30000)
        check("SGLang's KV-cache capacity is NOT read as a window",
              window(Fake({"/get_server_info": SGLANG_CAPACITY_ONLY}),
                     "http://127.0.0.1:30000/v1") == 0)

        # The route the code must NOT take: Ollama's model maximum is 16x what it serves.
        f = Fake({"/api/ps": OLLAMA_PS_EMPTY, "/api/show": OLLAMA_SHOW})
        check("a model that is not loaded says nothing rather than its maximum",
              window(f, "http://127.0.0.1:11434/v1") == 0)
        check("...and /api/show is never probed at all", not f.posted
              and not any("/api/show" in u for u in f.asked), (f.posted, f.asked))

        # ------------------------------------------------------- the shape is the fingerprint
        check("a /api/ps 200 without a models list is not an answer",
              window(Fake({"/api/ps": FOREIGN_LIST}), "http://127.0.0.1:11434/v1") == 0)
        check("an /api/ps entry without context_length is not an answer",
              window(Fake({"/api/ps": {"models": [{"name": "main:latest"}]}}),
                     "http://127.0.0.1:11434/v1") == 0)
        check("a list 200 that carries no data is not an answer",
              window(Fake({"/v1/models": FOREIGN_LIST})) == 0)

        # ------------------------------------------------------------------ 0, and no guess
        check("a box that answers nothing at all is 0",
              window(Fake({"": None}), "http://127.0.0.1:9/v1") == 0)
        check("a box that only answers a route nobody asked about is 0",
              window(Fake({"/metrics": {"x": 1}}), "http://127.0.0.1:9/v1") == 0)

        # ------------------------------------------------------------------ where it asks
        # The probe order is /v1/models (where /models lives), then the metadata routes at the
        # server root. The Ollama one must be the ROOT form: /v1/api/ps is not a route on any
        # server, so a URL built from the OpenAI path would silently never match.
        f = Fake({"/api/ps": OLLAMA_PS})
        window(f, "http://127.0.0.1:11434/v1")
        check("the Ollama probe asks the SERVER ROOT, and /models still asks /v1",
              "http://127.0.0.1:11434/api/ps" in f.asked
              and "http://127.0.0.1:11434/v1/models" in f.asked
              and not any("/v1/api/" in u for u in f.asked), f.asked)
        f = Fake({"/v1/models": FOREIGN_LIST, "/props": LLAMA_PROPS})
        window(f, "http://127.0.0.1:8081/v1")
        check("the /props probe asks the SERVER ROOT, not the OpenAI path",
              "http://127.0.0.1:8081/props" in f.asked, f.asked)

        # ------------------------------------------------------------------ precedence
        f = Fake({"/v1/models": {"object": "list", "data": [
            {"id": "somebody-elses", "max_model_len": 4096},
            {"id": want, "max_model_len": 32768}]}})
        check("the configured model's entry wins over the first in the list",
              window(f) == 32768)

        # ------------------------------------------- identified, never guessed
        # Review 2026-09-29: the fallback was `models[0]`, so a gateway advertising a 0.5B and a 72B
        # while the config named an alias sized the ENTIRE envelope from the 0.5B - or, worse, from
        # whatever happened to be listed first. Over-reporting a window is the direction this
        # harness treats as dangerous; under-reporting clips every large result for the whole run.
        GW = [{"id": "Qwen/Qwen2.5-0.5B", "max_model_len": 32768},
              {"id": "Qwen/Qwen2.5-72B", "max_model_len": 131072}]
        check("an exact id picks that model",
              fb._pick_model(GW, "Qwen/Qwen2.5-72B")["max_model_len"] == 131072)
        check("  case alone does not defeat it",
              fb._pick_model(GW, "qwen/qwen2.5-72B")["max_model_len"] == 131072)
        check("  a gateway's owner prefix does not either",
              fb._pick_model(GW, "Qwen2.5-72B")["max_model_len"] == 131072)
        check("several advertised and none of them this one -> UNKNOWN, not the first",
              fb._pick_model(GW, "some-other-alias") is None,
              fb._pick_model(GW, "some-other-alias"))
        check("  but ONE advertised model is the model, whatever the config calls it",
              fb._pick_model([GW[0]], "some-other-alias")["id"] == "Qwen/Qwen2.5-0.5B")
        check("  and nothing advertised is unknown, not a crash",
              fb._pick_model([], "x") is None and fb._pick_model(None, "x") is None)

        two = Fake({"/v1/models": {"object": "list", "data": GW}})
        check("the window is UNKNOWN when the model cannot be identified",
              window(two) == 0, window(two))
        one = Fake({"/v1/models": {"object": "list", "data": [
            {"id": "renamed-by-the-box", "max_model_len": 8192}]}})
        check("  and a single-model endpoint still reports its window",
              window(one) == 8192, window(one))

        # ------------------------------------------- slots: how many requests at once
        # The /props reply that fingerprints llama.cpp also carries total_slots, and until
        # 2026-09-29 the harness threw it away: the live box reported 2 slots while a run fanned
        # out 4 delegated subtasks, so two requests queued and EVERY one fell from ~50 to ~8-10
        # tok/s. A batch of delegate_task calls is a batch of MODEL requests; a batch of shells
        # is local work and must stay parallel. Both stubs are restored so nothing here reaches
        # the network (a real hostname would be a DNS lookup).
        real_props, real_local = fb._llama_props, fb._is_local_url
        try:
            fb._llama_props = lambda url: {"default_generation_settings": {"n_ctx": 131072},
                                           "total_slots": 2}
            check("llama.cpp's total_slots is the concurrency the box serves",
                  fb.endpoint_slots("http://127.0.0.1:8081/v1/chat/completions") == 2)
            four_delegated = [{"function": {"name": "delegate_task"}}] * 4
            four_shells = [{"function": {"name": "shell"}}] * 4
            check("four delegated subtasks on a 2-slot box fan out 2 at a time",
                  fb.batch_workers(four_delegated, "http://127.0.0.1:8081/v1") == 2)
            check("  four SHELLS are local work and still run in parallel",
                  fb.batch_workers(four_shells, "http://127.0.0.1:8081/v1") == 4)
            fb._llama_props = lambda url: {"build_info": "b1", "default_generation_settings": {}}
            check("an endpoint that does not report slots keeps the old fan-out",
                  fb.batch_workers(four_delegated, "http://127.0.0.1:8081/v1") == 4)
            check("  0 means 'did not say', never a guess",
                  fb.endpoint_slots("http://127.0.0.1:8081/v1") == 0)
            fb._is_local_url = lambda url: False
            check("an off-LAN endpoint is never asked for its slots",
                  fb.endpoint_slots("https://api.example.com/v1") == 0)
        finally:
            fb._llama_props, fb._is_local_url = real_props, real_local

        # ------------------------------------- the cache is per ENDPOINT, with the same TTL
        # One process-wide window meant a failover box was measured with the primary's
        # number (a 32k primary "proved" an 8k fallback was big, and the fallback's 400 became
        # a context overflow with no endpoint left to try). Each answer is cached under its own
        # server root instead - and the scalar `_window_cache` a stub sets is still read as the
        # PRIMARY's window, so those callers keep working and it still expires.
        real_detect = fb._detect_window
        calls = []
        try:
            fb._detect_window = lambda url, headers=None: (
                calls.append(fb._endpoint_root(url))
                or (8192 if "10.0.0.9" in str(url) else 32768))
            for _n in ("_window_cache", "_window_at"):
                fb.AGENT.__dict__.pop(_n, None)
            check("_endpoint_window answers for the endpoint it was asked about",
                  fb.AGENT._endpoint_window("http://127.0.0.1:8081/v1") == 32768
                  and fb.AGENT._endpoint_window("http://10.0.0.9:9000/v1") == 8192, calls)
            check("  and a second ask is the cache, not a second probe",
                  calls == ["http://127.0.0.1:8081", "http://10.0.0.9:9000"], calls)

            primary = fb.CONFIG["llm"]["base_url"]
            before = list(calls)
            fb.AGENT.__dict__["_window_cache"] = 65536       # the scalar stub surface
            fb.AGENT.__dict__.pop("_window_at", None)
            check("a scalar stub is the PRIMARY endpoint's window",
                  fb.AGENT._endpoint_window(primary) == 65536 and calls == before, calls)

            root = fb._endpoint_root(primary)
            at, val = fb.AGENT._window_cache[root]
            # Back-date on the MONOTONIC clock: the TTL is measured with now_mono() since the
            # clock fix (a suspend must not expire a live cache), so a time.time() value here
            # would read as the far future and never expire.
            fb.AGENT._window_cache[root] = (fb.now_mono() - (fb.WINDOW_TTL + 60), val)
            calls.clear()
            check("  and each endpoint's answer still expires on the TTL",
                  fb.AGENT._endpoint_window(primary) == 32768
                  and calls == [root], calls)

            fb.AGENT.__dict__["_window_cache"] = 12345       # stale scalar stub
            fb.AGENT.__dict__["_window_at"] = fb.now_mono() - (fb.WINDOW_TTL + 60)
            calls.clear()
            check("  a stale scalar stub is re-asked too, not trusted for ever",
                  fb.AGENT._endpoint_window(primary) == 32768
                  and calls == [root], calls)
        finally:
            fb._detect_window = real_detect
            for _n in ("_window_cache", "_window_at"):
                fb.AGENT.__dict__.pop(_n, None)

        print()
        print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
        return 1 if FAILS else 0
    return main()


def _suite_test_endpoint_learn():
    """The harness learns what an endpoint wants FROM the endpoint, never from a model name.

The 2026-10-06 review found the docs disagree in BOTH directions: DeepSeek's thinking
mode answers 400 when `reasoning_content` is MISSING ("must be passed back"), Groq and
1min.AI answer 400 when it is PRESENT (unsupported). So the decision is: what did THIS
endpoint do? Facts persist per endpoint (logs/state.json) because re-learning costs a 400
per process start. Same shape for the sticky-routing key: `prompt_cache_key` (OpenAI,
Moonshot, and OpenRouter's alias for it) goes out on remote calls, is remembered refused
if a host names it in a 400, and never goes to a local endpoint.

    python tests/test_endpoint_surface.py        [TINYCMDR_SRC=/path/to/old/tinycmdr.py]
"""
    import importlib.util
    import json
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


    def main():
        work = Path(tempfile.mkdtemp(prefix="fbendp-"))
        try:
            for name in ("theme.default.toml", "soul.example.md"):
                shutil.copy2(BASE / name, work / name)
            shutil.copy2(SRC, work / "tinycmdr.py")
            shutil.copy2(BASE / "tests" / "fixture-config.json", work / "config.json")
            spec = importlib.util.spec_from_file_location("tinycmdr_endpoint_learn",
                                                          work / "tinycmdr.py")
            fb = importlib.util.module_from_spec(spec)
            sys.modules["tinycmdr_endpoint_learn"] = fb
            spec.loader.exec_module(fb)

            # --- the wire shapes the harness must read -----------------------------
            check("reasoning_details[] is read as reasoning text",
                  fb._delta_reasoning({"reasoning_details": [
                      {"type": "reasoning.text", "text": "TH"},
                      {"type": "reasoning.encrypted", "data": "xx"}]}) == "TH")
            check("cached tokens: OpenAI/vLLM/OpenRouter nested shape",
                  fb._cached_tokens_from_usage(
                      {"prompt_tokens_details": {"cached_tokens": 512}}) == 512)
            check("cached tokens: DeepSeek/Moonshot flat shape",
                  fb._cached_tokens_from_usage({"prompt_cache_hit_tokens": 7}) == 7)
            check("cached tokens: neither shape is 0, never a guess",
                  fb._cached_tokens_from_usage({}) == 0)

            # --- endpoint identity --------------------------------------------------
            check("facts key on the endpoint, not the path",
                  fb._endpoint_origin("https://api.example.com/v1/chat/completions")
                  == "https://api.example.com",
                  fb._endpoint_origin("https://api.example.com/v1/chat/completions"))

            # --- the echo decision, learned off the wire ---------------------------
            host = "https://api.example.com/v1"
            check("a remote endpoint starts silent (no echo, nobody claimed anything)",
                  fb._replay_reasoning_ok(host) is False)
            check("...and a local endpoint always replays (template rebuilds <think>)",
                  fb._replay_reasoning_ok("http://127.0.0.1:8081/v1") is True)

            fb._endpoint_note(host, reasoning="on")
            check("an endpoint that emitted reasoning gets it replayed",
                  fb._replay_reasoning_ok(host) is True)
            state = json.loads((work / "logs" / "state.json").read_text(encoding="utf-8"))
            check("...and the fact is PERSISTED, so a restart does not re-learn it",
                  state.get("endpoints", {}).get("https://api.example.com", {})
                  .get("reasoning") == "on", state.get("endpoints"))

            # --- the 400 verdicts (opposite meanings, same field name) -------------
            check("a 'must be passed back' 400 means the echo is REQUIRED",
                  fb._reasoning_400_verdict(
                      "The reasoning_content in the thinking mode must be passed back "
                      "to the API") == "on")
            check("an 'unsupported' 400 means the echo is REJECTED",
                  fb._reasoning_400_verdict(
                      "('messages.1': property 'reasoning_content' is unsupported)") == "off")
            check("a 400 that says neither is not guessed at",
                  fb._reasoning_400_verdict("context length exceeded") is None)
            check("a tools+reasoning_effort 400 means the field is OFF for that host",
                  fb._reasoning_400_verdict(
                      "Function tools with reasoning_effort are not supported for "
                      "gpt-5.6-terra in /v1/chat/completions.") == "none")

            # --- the none verdict reaches apply_reasoning and the paired flags -----
            blocked = "https://tools-only.example.com/v1"
            fb.CONFIG["llm"]["reasoning"] = "medium"
            try:
                probe = {}
                fb.apply_reasoning(probe, blocked, "whatever")
                check("a level rides a healthy endpoint", "reasoning_effort" in probe, probe)
                fb._endpoint_note(blocked, reasoning="none")
                probe = {}
                fb.apply_reasoning(probe, blocked, "whatever")
                check("...and is dropped for an endpoint that cannot combine it with tools",
                      "reasoning_effort" not in probe and "reasoning" not in probe, probe)

                fb._endpoint_note(host, reasoning="on")
                fb.CONFIG["llm"]["reasoning_flags"] = {"clear_thinking": False}
                probe = {}
                fb._apply_reasoning_flags(probe, host)
                check("the paired preservation flags ride where the echo is wanted",
                      probe.get("clear_thinking") is False, probe)
                probe = {}
                fb._apply_reasoning_flags(probe, blocked)
                check("...and never elsewhere", not probe, probe)
            finally:
                fb.CONFIG["llm"].pop("reasoning_flags", None)
                fb.CONFIG["llm"]["reasoning"] = "auto"

            # --- the strip happens at SEND time, never at capture ------------------
            strict = "https://strict.example.com/v1"
            fb._endpoint_note(strict, reasoning="off")
            msgs = [{"role": "assistant", "content": "c", "reasoning_content": "R"}]
            out = fb._messages_for_wire(msgs, strict)
            check("a rejecting endpoint's copy drops the field",
                  "reasoning_content" not in out[0])
            check("...while the HISTORY keeps the text (a later 'required' can be served)",
                  msgs[0].get("reasoning_content") == "R")
            check("an accepting endpoint gets the history list itself (byte-identical)",
                  fb._messages_for_wire(msgs, host) is msgs)

            # --- the sticky-routing key -------------------------------------------
            key = fb._session_cache_key("cli")
            check("a session key derives a stable cache key",
                  isinstance(key, str) and key.startswith("tinycmdr-")
                  and key == fb._session_cache_key("cli") and len(key) <= 256, key)
            check("...sent to a remote endpoint", fb._cache_key_for(host, "cli") == key)
            check("...never to a local one",
                  fb._cache_key_for("http://127.0.0.1:8081/v1", "cli") is None)
            fb._endpoint_note("https://refuser.example.com/v1", drop=["prompt_cache_key"])
            check("...and never twice to a host that named it in a 400",
                  fb._cache_key_for("https://refuser.example.com/v1", "cli") is None)
            saved = fb.CONFIG["llm"].get("prompt_cache_key")
            fb.CONFIG["llm"]["prompt_cache_key"] = "off"
            try:
                check("the config lever turns it off everywhere",
                      fb._cache_key_for(host, "cli") is None)
            finally:
                if saved is None:
                    fb.CONFIG["llm"].pop("prompt_cache_key", None)
                else:
                    fb.CONFIG["llm"]["prompt_cache_key"] = saved

            if FAILS:
                print("\n%d FAILED: %s" % (len(FAILS), "; ".join(FAILS)))
                return 1
            print("\nall endpoint-learning checks passed")
            return 0
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return main()


def _suite_test_read_window():
    """read_file's window: which lines you get, and what the header claims.

Three defects (2026-09-27, all reproduced before the fix):

  * `offset=-5, limit=3` answered `lines -5—-2 of 22096` - negative line references that
    say nothing to a reader - because a negative slice reads from the END while the header
    prints the raw index;
  * `from_end=bool(want_tail or offset_req)` meant ANY offset read the LAST bytes of the
    file and then sliced them by a line number meant for the whole file: `offset=10,
    limit=2` of a 28.6 MiB file returned lines 432238-432239 under a header claiming 10-12.
    A silently wrong answer, not a cosmetic one;
  * `_read_capped` appended its own warning INTO the text that is then split into lines,
    so `tail=2` of a file past the cap returned the warning's two lines instead of the
    file's last two.

The cap is lowered in the truncation checks on purpose: every path here is size-relative,
and a real 9 MiB fixture would cost the gate a second per run for the same coverage.

    python tests/test_endpoint_surface.py
"""
    import importlib.util
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-window"
    if STAGE.exists():
        shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    shutil.copy2(BASE / "tests" / "fixture-config.json", STAGE / "config.json")
    spec = importlib.util.spec_from_file_location("tinycmdr_window", STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_window"] = fb
    spec.loader.exec_module(fb)

    FAILS = []


    def check(name, cond, detail=""):
        if cond:
            print("ok   %s" % name)
        else:
            FAILS.append(name)
            print("FAIL %s: %s" % (name, detail))


    def header_and_body(out):
        lines = str(out).splitlines()
        return lines[0], lines[1:]


    SMALL = STAGE / "small.txt"
    SMALL.write_text("".join("line %d\n" % i for i in range(1, 201)), encoding="utf-8")

    # ------------------------------------------------------------------ the plain window
    head, body = header_and_body(fb.tool_read_file({"path": str(SMALL), "offset": 10, "limit": 3}, {}))
    check("offset is a 0-based start line", "(lines 10–13 of 200)" in head, head)
    check("...and the body starts at that line", body[0] == "line 11", body[:2])

    head, body = header_and_body(fb.tool_read_file({"path": str(SMALL), "offset": 0, "limit": 2}, {}))
    check("offset 0 is the first line", "(lines 0–2 of 200)" in head and body[0] == "line 1", (head, body[:2]))

    head, body = header_and_body(fb.tool_read_file({"path": str(SMALL), "tail": 3}, {}))
    check("tail is its own door and says so", "(last 3 of 200 lines)" in head, head)
    check("...with the file's real last lines", body[0] == "line 198" and "line 200" in body[-1], body)

    out = str(fb.tool_read_file({"path": str(SMALL), "offset": -5, "limit": 3}, {}))
    check("a negative offset is refused, not answered with negative line refs",
          out.startswith("ERROR: offset is a START line"), out[:90])
    check("...and the refusal names the door for the last lines", "tail=N" in out, out[:160])
    # A-2026-10-05-70: an offset past the end answered `(lines 99999–99999 of 200)` with no
    # body - a range that reads as if the file had those lines.
    out = str(fb.tool_read_file({"path": str(SMALL), "offset": 99999}, {}))
    check("a read past the end names the file's real length",
          out.startswith("ERROR: line 99999 is past the end") and "(200 lines)" in out,
          out[:120])
    head, body = header_and_body(fb.tool_read_file({"path": str(SMALL), "offset": 199}, {}))
    check("...while the file's last line is still a read", body and body[0] == "line 200",
          (head, body[:2]))

    # ------------------------------------------------------ the window past the read cap
    REAL_CAP = fb._MAX_CAPTURE_BYTES
    fb._MAX_CAPTURE_BYTES = 4096
    try:
        BIG = STAGE / "big.log"
        with open(BIG, "w", encoding="utf-8") as fh:
            for i in range(1, 4001):
                fh.write("line %05d %s\n" % (i, "pad" * 10))
        check("the fixture is over the lowered cap", BIG.stat().st_size > 4096,
              BIG.stat().st_size)

        head, body = header_and_body(fb.tool_read_file({"path": str(BIG), "offset": 10, "limit": 2}, {}))
        check("an offset reads from the START, not the tail chunk",
              body[0].startswith("line 00011 "), body[:2])
        check("...and the header says the file is bigger than the window",
              "shown, the file is bigger" in head, head)
        out = str(fb.tool_read_file({"path": str(BIG), "offset": 10, "limit": 2}, {}))
        check("...and the cap warning rides the result", "HARNESS: this produced" in out, out[-120:])

        head, body = header_and_body(fb.tool_read_file({"path": str(BIG), "tail": 2}, {}))
        check("tail past the cap returns the FILE's last lines, not the warning's",
              body[0].startswith("line 03999") and body[1].startswith("line 04000")
              and not any("HARNESS" in b for b in body[:2]), body[:3])
        check("...and still carries the cap warning", "HARNESS: this produced" in
              str(fb.tool_read_file({"path": str(BIG), "tail": 2}, {})), "no warning")

        out = str(fb.tool_read_file({"path": str(BIG), "offset": 3900, "limit": 2}, {}))
        check("an offset past the window says so instead of answering empty",
              out.startswith("ERROR: line 3900 is past") and "tail=N" in out, out[:150])

        text, cut, note = fb._read_capped(BIG)
        check("the cap warning is returned SEPARATELY from the text",
              cut is True and "HARNESS" not in text and "HARNESS" in note,
              (cut, "HARNESS" in text, len(note)))
    finally:
        fb._MAX_CAPTURE_BYTES = REAL_CAP

    # ------------------------------------------------- an unreadable file names the cause
    # A file held open with a Windows deny-all share mode made read_file answer with a Python
    # internal exception - "not enough values to unpack (expected 3, got 2)" - because
    # _read_capped returned a 2-tuple on OSError while every caller unpacks 3 (
    # 2026-10-02). chmod 000 is the POSIX way to make open() raise where a Windows share lock
    # does; both are an OSError whose args carry no filename.
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        LOCKED = STAGE / "locked.txt"
        LOCKED.write_text("secret\n", encoding="utf-8")
        os.chmod(LOCKED, 0)
        try:
            out = str(fb.tool_read_file({"path": str(LOCKED)}, {}))
            check("an unreadable file names the cause, not a Python unpack error",
                  out.startswith("ERROR reading") and "unpack" not in out
                  and ("Permission denied" in out or "Errno" in out), out[:160])
            raised = False
            try:
                fb._read_capped(LOCKED, strict=True)
            except OSError:
                raised = True
            check("_read_capped(strict=True) re-raises the real OSError", raised)
        finally:
            os.chmod(LOCKED, 0o644)

    # ------------------------------------------------- the header never claims a wrong total
    # When a read is cut, `len(lines)` is the window covered - the header printed it as if it
    # were the file's line count, which is how the read cap read as the file's length
    # (2026-10-02).
    fb._MAX_CAPTURE_BYTES = 2048
    try:
        CUT = STAGE / "cut.txt"
        CUT.write_text("".join("line %d\n" % i for i in range(1, 1001)), encoding="utf-8")
        check("the fixture is over the lowered cap", CUT.stat().st_size > 2048,
              CUT.stat().st_size)
        head, _body = header_and_body(
            fb.tool_read_file({"path": str(CUT), "offset": 0, "limit": 2}, {}))
        check("a cut read says the count is the window it covered",
              "this read covered" in head, head)
        head, _body = header_and_body(fb.tool_read_file({"path": str(CUT), "tail": 2}, {}))
        check("...and a tail of a cut read says the same",
              "this read covered" in head and "the file is bigger" in head, head)
        head, _body = header_and_body(fb.tool_read_file({"path": str(SMALL), "tail": 3}, {}))
        check("an uncut read keeps its plain 'last N of M lines'",
              "(last 3 of 200 lines)" in head, head)
    finally:
        fb._MAX_CAPTURE_BYTES = REAL_CAP

    # ------------------------------------------------- a Windows path past MAX_PATH
    # Creating a 339-character path on Windows throws WinError 206 with LongPathsEnabled=0,
    # while the \\?\ extended form works (measured on Windows, 2026-10-02). The
    # transform is a pure function, so it is graded here - off Windows - by forcing the flag.
    _real_win = fb.IS_WINDOWS
    try:
        fb.IS_WINDOWS = True
        _short_win = r"C:\Users\a\file.txt"
        check("a short absolute Windows path is left alone",
              fb._win_long_path(_short_win) == _short_win)
        _long = r"C:\Users\a" + (r"\dddddddddd" * 26) + r"\file.txt"
        _got = fb._win_long_path(_long)
        check(r"a long drive path gets the \\?\ prefix", _got.startswith("\\\\?\\C:\\"), _got[:26])
        check("...and keeps the end of the path intact", _got.endswith(r"\file.txt"), _got[-14:])
        _unc = "\\\\server\\share" + (r"\dddddddddd" * 26) + r"\f.txt"
        _ugot = fb._win_long_path(_unc)
        check("a long UNC path uses the UNC extended form",
              _ugot.startswith("\\\\?\\UNC\\server"), _ugot[:22])
        _ext = r"\\?\C:\a\file.txt"
        check("an already-extended path is not double-prefixed",
              fb._win_long_path(_ext) == _ext)
        check("a relative path is left alone",
              fb._win_long_path("sub/dir/file.txt") == "sub/dir/file.txt")
    finally:
        fb.IS_WINDOWS = _real_win
    check("off Windows every path is untouched", fb._win_long_path("/tmp/a/b") == "/tmp/a/b")

    print()
    if FAILS:
        print("%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
        sys.exit(1)
    print("read_file's window: offsets from the start, tail from the end, headers that tell the truth")


def main():
    rc = 0
    for name, fn in (("test_endpoint_window", _suite_test_endpoint_window), ("test_endpoint_learn", _suite_test_endpoint_learn), ("test_read_window", _suite_test_read_window)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
