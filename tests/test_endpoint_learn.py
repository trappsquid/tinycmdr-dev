"""The harness learns what an endpoint wants FROM the endpoint, never from a model name.

The 2026-10-06 review found the docs disagree in BOTH directions: DeepSeek's thinking
mode answers 400 when `reasoning_content` is MISSING ("must be passed back"), Groq and
1min.AI answer 400 when it is PRESENT (unsupported). So the decision is: what did THIS
endpoint do? Facts persist per endpoint (logs/state.json) because re-learning costs a 400
per process start. Same shape for the sticky-routing key: `prompt_cache_key` (OpenAI,
Moonshot, and OpenRouter's alias for it) goes out on remote calls, is remembered refused
if a host names it in a 400, and never goes to a local endpoint.

    python tests/test_endpoint_learn.py        [TINYCMDR_SRC=/path/to/old/tinycmdr.py]
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
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % detail))
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


if __name__ == "__main__":
    sys.exit(main())
