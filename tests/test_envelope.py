"""The envelope: window, measured static, clamped reply, leftover messages budget.

Pins the arithmetic that replaces the old guess (audit FEATURE D1/D2/D5, BUGREPORT
§M9). Measured at HEAD before this change: an 8,192-token endpoint was sent a
9,275-token payload and a 16,384-token completion request, because the budget was
max(4000, window - 7000 - max_tokens) with the 5,322-token static half subtracted
from nothing. The checks here are the five numbers and the relations between them
at the windows the audit swept, the refusal below 8,192, and the two things that
must scale with the window rather than with the model name: the memory caps and
the schemas the payload actually carries.

Hermetic: no network, no model. `_endpoint_window` is stubbed to each served
window, which is exactly what the detection probe returns on a real box.

    python tests/test_envelope.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
# TINYCMDR_SRC lets a reverted copy be graded (falsification: revert one fix, watch this go red).
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
FAILS = []


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def stage(workdir, llm=None):
    shutil.copy2(SRC, workdir / "tinycmdr.py")
    fixture = json.loads((BASE / "tests" / "fixture-config.json")
                         .read_text(encoding="utf-8-sig"))
    cfg = {k: v for k, v in fixture.items() if not k.startswith("_")}
    cfg["llm"].update(llm or {})
    (workdir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def load(workdir, name="envelope_build"):
    spec = importlib.util.spec_from_file_location(name, workdir / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules[name] = fb
    spec.loader.exec_module(fb)
    return fb


def at_window(fb, window):
    """The envelope as a box that serves `window` produces it, cache cleared."""
    fb.AGENT._window_cache = window
    fb.AGENT._window_at = 0.0
    fb.AGENT._envelope_cache = None
    fb._STATIC_CACHE.clear()
    return fb.AGENT._envelope("envtest")


class FakeResp:
    def __init__(self, status=500, text="nope"):
        self.status_code = status
        self.text = text

    def json(self):
        return {}


def capture_request(fb, session_key="envtest"):
    """Send one _chat with the transport stubbed; return the payload it tried to send.

    The stub raises a plain 500, so _chat walks off the end and raises InfraError -
    what matters is the payload it had already assembled.
    """
    import requests
    seen = []
    # These measure payload SHAPE and the failover CHAIN, not the retry policy: with
    # same-endpoint retries on (the default), a 500 would be re-sent before failover.
    # The retry policy has its own suite (tests/test_transient_retry.py).
    _saved_retries = fb.CONFIG["llm"].get("same_endpoint_retries")
    fb.CONFIG["llm"]["same_endpoint_retries"] = 0

    def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                  stream=False):
        seen.append(payload)
        raise requests.HTTPError("stub", response=FakeResp())

    real = fb._post_watchdog
    fb._post_watchdog = fake_post
    try:
        fb.AGENT._chat([{"role": "system", "content": "s"},
                        {"role": "user", "content": "hi"}], session_key=session_key)
        sent = "no error raised"
    except fb.InfraError as e:
        sent = str(e)
    finally:
        fb._post_watchdog = real
        if _saved_retries is None:
            fb.CONFIG["llm"].pop("same_endpoint_retries", None)
        else:
            fb.CONFIG["llm"]["same_endpoint_retries"] = _saved_retries
    return seen, sent


def capture_chain(fb, messages, session_key="envtest"):
    """Walk one _chat down the failover chain; return [(url, payload), ...].

    The stub fails EVERY endpoint with a plain 500 (which is not a context overflow, so
    the loop moves to the next endpoint instead of raising). Each payload is deep-copied
    at the moment it was about to be sent, because _chat keeps mutating the same dict
    across endpoints/retries.
    """
    import requests
    seen = []
    # Chain shape, not retry policy (see capture_request).
    _saved_retries = fb.CONFIG["llm"].get("same_endpoint_retries")
    fb.CONFIG["llm"]["same_endpoint_retries"] = 0

    def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                  stream=False):
        seen.append((url, json.loads(json.dumps(payload))))
        raise requests.HTTPError("stub", response=FakeResp())

    real = fb._post_watchdog
    fb._post_watchdog = fake_post
    try:
        fb.AGENT._chat(messages, session_key=session_key)
    except Exception:                          # noqa: BLE001 - no endpoint answered
        pass
    finally:
        fb._post_watchdog = real
        if _saved_retries is None:
            fb.CONFIG["llm"].pop("same_endpoint_retries", None)
        else:
            fb.CONFIG["llm"]["same_endpoint_retries"] = _saved_retries
    return seen


def main():
    workdir = Path(tempfile.mkdtemp(prefix="tc-envelope-"))
    try:
        stage(workdir)
        fb = load(workdir)
        reply_cfg = int(fb.CONFIG["llm"]["max_tokens"])
        soft = fb.ENVELOPE_MIN_WINDOW
        floor = fb.ENVELOPE_MIN_BUDGET
        # The fixture config carries llm.max_context_tokens, which the harness treats as a
        # CEILING on the messages budget (pinned elsewhere). The expectation has to
        # model that: while the prompt was 5,237 tokens the ceiling never bound at 131,072
        # and this arithmetic passed by luck (2026-09-27, after the prompt was trimmed).
        ceiling = fb.CONFIG["llm"].get("max_context_tokens")
        ceiling = ceiling if isinstance(ceiling, int) and ceiling > 0 else None

        def expected_budget(window, static, reply):
            room = max(floor, window - static - reply)
            return max(floor, min(room, ceiling)) if ceiling else room

        # --- the five numbers, and the relations between them -----------------
        env0 = at_window(fb, 32768)
        static = env0["static"]
        check(static > 1000, f"static is MEASURED from the prompt (got {static} tokens)")
        # G5's ratchet: the audit measured 5,322 tokens of static overhead on the installed
        # box and set the ceiling at 5,400 (that + margin). This asserts against the
        # staged fixture, so it fails the moment a change makes the static prompt or the
        # disclosed schema set grow past the ceiling.
        check(static <= 5400,
              f"static overhead is within the 5,400-token ceiling (got {static})")
        check(static < soft,
              f"static {static} is below the {soft}-token minimum window, so a legal "
              f"request exists at the floor")

        for window in (8192, 16384, 32768, 131072):
            env = at_window(fb, window)
            reply = min(reply_cfg, window // 4)
            budget = expected_budget(window, static, reply)
            line = fb.envelope_line(env)
            check(env["window"] == window and env["static"] == static,
                  f"w={window}: window and static named in the envelope")
            check(env["reply"] == reply,
                  f"w={window}: reply clamped to min(max_tokens, window//4) = {reply}")
            check(env["budget"] == budget,
                  f"w={window}: budget = window - static - reply = {budget}")
            check(static + env["budget"] <= window,
                  f"w={window}: static + messages fits in the window "
                  f"({static} + {env['budget']} <= {window})")
            raw_line = fb.envelope_line(env, raw=True)
            check(all(str(n) in raw_line for n in (window, static, reply, budget)),
                  f"w={window}: envelope_line names all five numbers: {line}")

        # 32,768 is the audit's worked example: the old 4,000 floor became 19,254.
        env32 = at_window(fb, 32768)
        check(32768 - static - min(reply_cfg, 32768 // 4) > 19000,
              f"w=32768: the budget is a measurement, not the old 4,000 floor "
              f"({env32['budget']})")

        # --- 4,096 refuses, with the arithmetic, before any request -----------
        env4 = at_window(fb, 4096)
        check(env4["refused"], "w=4096: the envelope refuses")
        for token in ("window=4096", f"static={static}", "reply=", "budget="):
            check(token in env4["refusal"], f"w=4096: refusal names {token}")
        seen, sent = capture_request(fb, "envtest")
        check(not seen, "w=4096: _chat sends NOTHING (refused before the POST)")
        check("serves 4096 tokens per request" in sent,
              f"w=4096: _chat raises InfraError with the refusal ({sent[:60]}...)")

        # --- 8,192 runs, and the wire carries the clamped reply ---------------
        env8 = at_window(fb, 8192)
        check(not env8["refused"], "w=8192: the envelope runs (with a warning)")
        seen, sent = capture_request(fb, "envtest")
        check(len(seen) == 1, "w=8192: exactly one request was assembled")
        if seen:
            payload = seen[0]
            conv = fb.AGENT._conversation_token_est(payload["messages"])
            check(payload["max_tokens"] == env8["reply"] == min(reply_cfg, 2048),
                  f"w=8192: max_tokens sent is the clamped reply "
                  f"({payload['max_tokens']}, was {reply_cfg})")
            check(conv <= env8["budget"],
                  f"w=8192: the conversation fits the budget ({conv} <= "
                  f"{env8['budget']}) - the system prompt is counted in static, "
                  f"not again here")
            check(env8["static"] + conv <= 8192,
                  f"w=8192: the real payload (system + schemas + conversation) fits "
                  f"the window ({env8['static']} + {conv} <= 8192)")

        # --- an explicit llm.max_context_tokens is still a CEILING -------------
        saved_ceiling = fb.CONFIG["llm"].get("max_context_tokens")
        try:
            fb.CONFIG["llm"]["max_context_tokens"] = 12000
            env_c = at_window(fb, 32768)
            check(env_c["budget"] == 12000,
                  f"an explicit ceiling wins over a roomier window "
                  f"({env_c['budget']} == 12000)")
            env_c2 = at_window(fb, 8192)
            # computed, not hardcoded: the old 1,024 was the floor winning, which only
            # happened while the static prompt was big enough to eat the whole window.
            expect_c2 = max(floor, min(8192 - static - min(reply_cfg, 8192 // 4), 12000))
            check(env_c2["budget"] == expect_c2,
                  f"a tighter window still wins over a looser ceiling "
                  f"({env_c2['budget']} == {expect_c2})")
        finally:
            fb.CONFIG["llm"]["max_context_tokens"] = saved_ceiling

        # --- 16,384: the warn line, and the payload still fits -----------------
        env16 = at_window(fb, 16384)
        seen16, _ = capture_request(fb, "envtest")
        if seen16:
            conv16 = fb.AGENT._conversation_token_est(seen16[0]["messages"])
            check(env16["static"] + conv16 <= 16384,
                  f"w=16384: the real payload fits the window "
                  f"({env16['static']} + {conv16} <= 16384)")

        # --- memory limits scale with the window, not the model name ----------
        at_window(fb, 16384)
        notes = fb.mem_limit_chars("notes_max_chars", 8000)
        tools = fb.mem_limit_chars("tool_output_max_chars", 10000)
        fetch = fb.mem_limit_chars("fetch_max_chars", 12000)
        hist = fb.mem_limit_exchanges("history_exchanges", 20)
        check(notes < 8000,
              f"w=16384: an 8,000-char notes block is impossible ({notes})")
        check(tools == fetch == notes == 16384 // 8,
              f"w=16384: tool output, fetch and notes all read {notes} "
              f"(window // 8)")
        check(hist < 20, f"w=16384: history_exchanges scales down to {hist}")
        at_window(fb, 131072)
        check(fb.mem_limit_chars("notes_max_chars", 8000) == 8000
              and fb.mem_limit_exchanges("history_exchanges", 20) == 20,
              "w=131072: a big window keeps the configured caps")

        # ... while a ONE-SHOT tool result may follow the window UP to its ceiling. Measured
        # 2026-09-29 on this 131k box: three reads of ~10.6k chars against the 10,000 default
        # were spilled inside six minutes and cost four further calls to read back, when the
        # turn's budget was 110,186 tokens. Every one of those calls re-sends the whole
        # conversation, so refusing 2,600 tokens of a 110,000-token budget was the expensive
        # choice. The every-turn blocks above deliberately do NOT move: notes ride EVERY
        # request, a tool result rides exactly one.
        at_window(fb, 131072)
        tool_cap = fb.mem_limit_chars("tool_output_max_chars", 10000,
                                      ceiling=fb.TOOL_RESULT_CAP_CEILING)
        check(tool_cap == min(fb.TOOL_RESULT_CAP_CEILING, 131072 // 8) and tool_cap > 10000,
              f"w=131072: a one-shot tool cap follows the window up to its ceiling "
              f"({tool_cap}), so a 10.6k document is no longer cut and re-read")
        fb.CONFIG["agent"]["tool_output_max_chars"] = 40000
        try:
            check(fb.mem_limit_chars("tool_output_max_chars", 10000) == 131072 // 8,
                  "  and a number the OPERATOR chose is still capped by window // 8")
        finally:
            fb.CONFIG["agent"]["tool_output_max_chars"] = 10000
        at_window(fb, 16384)
        check(fb.mem_limit_chars("tool_output_max_chars", 10000,
                                 ceiling=fb.TOOL_RESULT_CAP_CEILING) == 16384 // 8,
              "  on a small window the ceiling changes nothing (window // 8 still wins)")

        # --- a SLOW box gets a shorter generation, sized to its measured rate ----------
        # Measured 2026-09-29 on the fleet's Mac: the same endpoint served 8-57 tok/s depending
        # on how many requests shared its two slots, so one 16,384-token cap meant a six-minute
        # generation at its best and half an hour at its worst.
        _rate_key = fb._endpoint_root(fb.CONFIG["llm"]["base_url"])
        _saved_rate = dict(fb._DECODE_TPS)
        try:
            fb._DECODE_TPS[_rate_key] = 8.0
            slow = at_window(fb, 131072)
            check(slow["reply"] == int(8 * fb.CONFIG["llm"]["max_call_seconds"]),
                  f"w=131072 at 8 tok/s: one call is capped to max_call_seconds of "
                  f"generation ({slow['reply']})")
            fb._DECODE_TPS[_rate_key] = 400.0
            fast = at_window(fb, 131072)
            check(fast["reply"] == min(reply_cfg, 131072 // 4),
                  f"  and a fast box keeps the ordinary cap ({fast['reply']})")
        finally:
            fb._DECODE_TPS.clear()
            fb._DECODE_TPS.update(_saved_rate)

        # --- F-16: each endpoint's request is sized to its OWN window ----------
        # A failover box is a different box. The window/envelope used to be keyed to the
        # SESSION, so a big primary handed a small fallback a payload sized for the
        # primary; the fallback's 400 matched the overflow regex and the run stopped with
        # no endpoint left to try. Here the chain is 65,536 -> 16,384 and the primary
        # fails (500), so the payload the fallback receives must be the fallback's.
        saved_fallbacks = fb.CONFIG["llm"].get("fallbacks")
        saved_allow = fb.CONFIG["llm"].get("allow_cloud_fallback")
        real_win = fb.AGENT._endpoint_window
        primary_chat = fb.AGENT.llm_url
        fb_chat = "http://127.0.0.1:2/v1/chat/completions"
        fb.CONFIG["llm"]["fallbacks"] = [{"base_url": "http://127.0.0.1:2/v1",
                                          "model": "fb-model", "api_key": "x"}]
        fb.CONFIG["llm"]["allow_cloud_fallback"] = True
        fb.AGENT._endpoint_window = (
            lambda url=None: 16384 if url and "127.0.0.1:2" in url else 65536)
        try:
            fb.AGENT._envelope_cache = None
            prim = fb.AGENT._envelope("fochain")
            fb.AGENT._envelope_cache = None
            fbk = fb.AGENT._envelope("fochain", fb_chat)
            check(prim["window"] == 65536 and fbk["window"] == 16384
                  and prim["endpoint"] != fbk["endpoint"],
                  f"F-16: the envelope follows the endpoint ({prim['window']} then "
                  f"{fbk['window']})")
            check(fbk["reply"] == min(reply_cfg, 16384 // 4)
                  and prim["reply"] == min(reply_cfg, 65536 // 4),
                  f"F-16:   and so does the clamped reply "
                  f"({prim['reply']} vs {fbk['reply']})")

            # A conversation sized to fit the primary, and several times the fallback.
            # Each exchange is a quarter of the fallback's budget, so the newest exchange
            # (which no shrink may drop) still fits under a half-budget target.
            per = max(200, fbk["budget"] // 4)
            tpc = fb.est_tokens("z" * 4000) / 4000.0     # tokens/char, measured
            chunk = "z" * max(64, int(per / tpc))
            msgs = [{"role": "system", "content": "sys"}]
            while fb.AGENT._conversation_token_est(msgs) < fbk["budget"] * 3:
                msgs.append({"role": "user", "content": chunk})
                msgs.append({"role": "assistant", "content": "ok"})
            chain = capture_chain(fb, msgs, "fochain")
            urls = [u for u, _ in chain]
            by_url = dict(chain)
            check(len(urls) == 2 and "127.0.0.1:2" in urls[1],
                  f"F-16: the chain fell over to the fallback {urls}")
            conv_p = fb.AGENT._conversation_token_est(
                by_url[primary_chat]["messages"])
            conv_f = fb.AGENT._conversation_token_est(by_url[fb_chat]["messages"])
            check(conv_p > fbk["budget"],
                  f"F-16: the primary's request was NOT resized for the fallback "
                  f"({conv_p} > {fbk['budget']})")
            check(conv_f <= fbk["budget"],
                  f"F-16: the fallback's request fits the fallback's budget "
                  f"({conv_f} <= {fbk['budget']})")
            check(fbk["static"] + conv_f <= fbk["window"],
                  f"F-16:   and the real payload fits the fallback's window "
                  f"({fbk['static']} + {conv_f} <= {fbk['window']})")
            check(by_url[fb_chat]["max_tokens"] == fbk["reply"]
                  and by_url[primary_chat]["max_tokens"] == prim["reply"],
                  f"F-16: each request carried its own endpoint's reply cap "
                  f"({by_url[primary_chat]['max_tokens']} vs "
                  f"{by_url[fb_chat]['max_tokens']})")

            # The shrink target follows the endpoint that REJECTED the prompt.
            plain = [dict(m) for m in msgs]
            routed = [dict(m) for m in msgs]
            fb.AGENT._force_shrink(plain, "fochain")
            fb.AGENT._force_shrink(routed, "fochain", endpoint=fb_chat)
            est_p = fb.AGENT._conversation_token_est(plain)
            est_r = fb.AGENT._conversation_token_est(routed)
            target = max(2000, fb.AGENT._context_budget("fochain", fb_chat) // 2)
            check(est_r <= target and est_r < est_p,
                  f"F-16: shrinking for the FAILING endpoint cuts to its own window "
                  f"({est_r} <= {target}), not the primary's ({est_p})")
        finally:
            fb.CONFIG["llm"]["fallbacks"] = saved_fallbacks
            fb.CONFIG["llm"]["allow_cloud_fallback"] = saved_allow
            fb.AGENT._endpoint_window = real_win
            fb.AGENT._envelope_cache = None

        # --- a bigger tool surface is COUNTED, not ignored --------------------
        at_window(fb, 32768)
        fb.CONFIG["agent"]["tool_disclosure"] = False   # send the whole registry
        fb.AGENT._envelope_cache = None
        fb._STATIC_CACHE.clear()
        before = fb.AGENT._envelope("envtest")
        for i in range(100):
            fb.REGISTRY.custom["probe_tool_%03d" % i] = {"schema": {
                "type": "function", "function": {
                    "name": "probe_tool_%03d" % i,
                    "description": "a probe tool with a sentence of description" * 2,
                    "parameters": {"type": "object", "properties": {
                        "path": {"type": "string", "description": "a path"}},
                        "required": ["path"]}}}}
        fb.AGENT._envelope_cache = None
        fb._STATIC_CACHE.clear()
        after = fb.AGENT._envelope("envtest")
        delta = after["static"] - before["static"]
        check(delta > 100 * 20,
              f"+100 sent schemas raise static by {delta} tokens (counted, not "
              f"ignored)")
        check(after["budget"] == before["budget"] - delta,
              f"the budget falls by exactly the schema cost ({before['budget']} - "
              f"{delta} = {after['budget']}) - the outcome is named, not silent")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all envelope checks passed")


if __name__ == "__main__":
    main()
