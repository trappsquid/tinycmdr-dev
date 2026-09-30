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

    python tests/test_endpoint_window.py
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

    # ------------------------------------------------------------- the three routes
    check("vLLM's max_model_len is the window",
          window(Fake({"/v1/models": VLLM_MODELS})) == 32768)
    check("llama.cpp's meta.n_ctx on /v1/models is the window",
          window(Fake({"/v1/models": LLAMA_MODELS})) == 131072)
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
    # Audit 2026-09-29: the fallback was `models[0]`, so a gateway advertising a 0.5B and a 72B
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
    # F-16: one process-wide window meant a failover box was measured with the primary's
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
        fb.AGENT._window_cache[root] = (time.time() - (fb.WINDOW_TTL + 60), val)
        calls.clear()
        check("  and each endpoint's answer still expires on the TTL",
              fb.AGENT._endpoint_window(primary) == 32768
              and calls == [root], calls)

        fb.AGENT.__dict__["_window_cache"] = 12345       # stale scalar stub
        fb.AGENT.__dict__["_window_at"] = time.time() - (fb.WINDOW_TTL + 60)
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


if __name__ == "__main__":
    sys.exit(main())
