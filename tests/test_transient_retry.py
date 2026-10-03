"""A transient 5xx/transport failure re-asks the SAME endpoint before any failover.

Local llama.cpp/vLLM boxes answer 503 while a model loads or is swapped; demoting that to
the next endpoint silently changes the model the conversation runs on, and back-to-back
hops maximize the chance of re-tripping the same 503. Ported from omp
(docs/non-compaction-retry-policy.md:55-140, packages/ai/src/error/retryable.ts:20-58):
capped exponential backoff + jitter, same model, same key, bounded, and never on 4xx.

    python tests/test_transient_retry.py
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


if __name__ == "__main__":
    sys.exit(main())
