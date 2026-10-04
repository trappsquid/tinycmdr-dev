"""The small-model thesis: what the harness does for a weak, small-window local model.

Five claims, each one a group here (review 2026-09-28, section 2):
  1. caps are chosen by the WINDOW the endpoint serves, not only by the model's NAME -
     llm.window_profiles bands, and the smallest band at least as large as the window wins;
  2. the wrap-up at the budget cap is a fixed skeleton, and that final call is clamped to
     the window the way every other call is (final_max_tokens 8,192 is larger than an 8k
     window);
  3. a tool call whose arguments arrived wrapped in a fence or prose is salvaged on the
     FRESH call, not only on replay - an avoided retry is minutes on a slow endpoint;
  4. a spilled result carries the cause-naming lines from the dropped middle inline, so
     recovering a log tail is not a second call;
  5. a FAILED call is shown the last call to the same tool that worked - the shape, not a
     note about a cause.

    python tests/test_small_model.py
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


def tool_call_reply(name, args, cid="c1"):
    return {"choices": [{"message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": cid, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)}}]},
        "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


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


def payload_blob(payload):
    return "\n".join(str(m.get("content") or "") for m in payload.get("messages", []))


BANDS = {
    "8192": {"history_exchanges": 4, "digest_lines": 12, "not_a_real_key": 1},
    "16384": {"history_exchanges": 8, "digest_lines": 24},
    "32768": {"history_exchanges": 12, "digest_lines": 40},
}


def at_window(fb, window):
    """Build the envelope as a box serving `window` tokens produces it."""
    fb.AGENT._window_cache = window
    fb.AGENT._envelope_cache = None
    fb._STATIC_CACHE.clear()
    return fb.AGENT._envelope("smallmodel")


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbsmall-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)

        # ---- 1. window bands -------------------------------------------------
        base_hist = fb.CONFIG["agent"]["history_exchanges"]
        check(at_window(fb, 16384)["window_profile"] is None,
              "no window_profiles configured means nothing is applied")

        fb.CONFIG["llm"]["window_profiles"] = BANDS
        fb._WINDOW_PROFILE_APPLIED.clear()
        env = at_window(fb, 12288)
        check((env["window_profile"] or {}).get("profile") == "16384",
              "a 12288-token window takes the smallest band at least as large (16384)")
        check(fb.CONFIG["agent"]["history_exchanges"] == 8, "and that band's caps apply")
        check(fb.CONFIG["agent"]["digest_lines"] == 24, "  the kept-lines cap too")
        check("not_a_real_key" not in fb.CONFIG["agent"],
              "  a key the harness does not read is dropped, as with llm.profiles")

        env = at_window(fb, 32768)
        check((env["window_profile"] or {}).get("profile") == "32768",
              "a bigger window moves to its own band")
        check(fb.CONFIG["agent"]["history_exchanges"] == 12, "  and its caps apply")

        env = at_window(fb, 16384)
        check(fb.CONFIG["agent"]["history_exchanges"] == 8,
              "coming back down restores the band, not a union of the two")

        fb.CONFIG["llm"]["window_profiles"] = {}
        at_window(fb, 16384)
        check(fb.CONFIG["agent"]["history_exchanges"] == base_hist,
              "  and the band's overrides are undone when the bands are removed")

        # ---- 1b. the window scalers, which are the floor under all of this ---
        fb.AGENT._envelope_cache = {"window": 8192}
        check(fb.mem_limit_num("digest_lines", 40, 400, 8) == 20,
              "digest_lines scales with the window (8192 // 400 = 20)")
        check(fb.mem_limit_chars("memory_concept_max_chars", 1200) == 1024,
              "the per-concept cap scales with the window (8192 // 8 = 1024)")
        fb.AGENT._envelope_cache = {"window": 0}
        check(fb.mem_limit_num("digest_lines", 40, 400, 8) == 40
              and fb.mem_limit_chars("memory_concept_max_chars", 1200) == 6000,
              "an undetected window leaves the configured value alone (6000)")

        # ---- 2. the landing skeleton, and the clamp on that final call --------
        fb.AGENT._envelope_cache = None
        fb.AGENT._window_cache = 16384
        fb.CONFIG["agent"]["max_steps"] = 2
        fb.CONFIG["agent"]["auto_continue_max"] = 0
        seen = []

        def script(i, payload):
            if i < 2:
                return tool_call_reply("shell", {"command": f"echo work-{i}"}, f"b{i}")
            return text_reply("ROOT CAUSE: x\nCHANGED: y\nSTATE: z\n"
                              "UNFINISHED: nothing\nVERIFIED: nothing")

        install_stub(fb, seen, script)
        fb.AGENT.run("s1", "a multi-step thing")
        final = payload_blob(seen[-1])
        check("budget is exhausted" in final, "the forced wrap-up still happens")
        for label in ("ROOT CAUSE:", "CHANGED:", "STATE:", "UNFINISHED:"):
            check(label in final, f"the wrap-up asks for the fixed skeleton ({label})")
        check("VERIFIED: " in final, "  and still ends on the VERIFIED line")
        check(seen[-1].get("max_tokens") == 4096,
              f"the final call is clamped to the window (16384 // 4), not 8,192 "
              f"(got {seen[-1].get('max_tokens')})")

        # ---- 2.5b: the cut-off retry escalates to the WINDOW, not past it ----------
        fb.AGENT._envelope_cache = None
        fb.AGENT._window_cache = 16384
        seen_esc = []

        def esc_script(i, payload):
            if i == 0:
                return {"choices": [{"message": {"role": "assistant", "content": ""},
                                     "finish_reason": "length"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 900}}
            return text_reply("ROOT CAUSE: x")

        install_stub(fb, seen_esc, esc_script)
        fb.AGENT.run("s2", "think about it")
        check(len(seen_esc) >= 2 and seen_esc[0].get("max_tokens") == 4096,
              f"the first call is the normal clamped reply "
              f"({seen_esc[0].get('max_tokens') if seen_esc else None})")
        check(len(seen_esc) >= 2 and seen_esc[1].get("max_tokens") == 16384,
              f"the cut-off retry escalates to the window, not to 65,536 "
              f"(got {seen_esc[1].get('max_tokens') if len(seen_esc) > 1 else None})")

        # ---- 3. a fresh call whose arguments arrived fenced -------------------
        ctx = {"session_key": "s1"}
        name, args, _out = fb.AGENT._exec_tool(
            {"function": {"name": "no_such_tool_here",
                          "arguments": '```json\n{"cmd": "echo hi"}\n```'}}, ctx)
        check(args == {"cmd": "echo hi"},
              f"a fenced fresh call is salvaged to its JSON object (got {args!r})")

        _name, _args, out = fb.AGENT._exec_tool(
            {"function": {"name": "shell", "arguments": "this is not json"}}, ctx)
        check(out.startswith("ERROR: invalid JSON arguments"),
              "a blob with no JSON object still takes the error path")

        # ---- 4. the spilled middle's cause lines ride inline ------------------
        body = ("start\n" + "routine line\n" * 200
                + "ERROR: disk quota exceeded on /var\n" + "more routine\n" * 200)
        lo = 0
        excerpt = fb._spill_signal(body, lo, len(body), 4000)
        check("disk quota exceeded" in excerpt, "the cause line is lifted out of the middle")
        check("routine line" not in excerpt, "  and the routine lines are not")
        check(fb._spill_signal("A" * 4000, 0, 4000, 1000) == "",
              "a middle with no cause line yields nothing at all")

        fb.AGENT._envelope_cache = {"window": 16384}
        spilled = fb.cap_output("shell", body, "command output")
        check(len(spilled) < len(body) and "disk quota exceeded" in spilled,
              "a real spill carries the cause inline, so no second call is needed")
        check("Nothing was dropped" in spilled, "  and still points at the full text")
        fb.AGENT._envelope_cache = None

        # ---- 5. the last call that worked, replayed on a failure --------------
        fb._LAST_GOOD_CALL.clear()
        fb.remember_good_call("shell", {"command": "echo hi"}, "hi\n")
        fb.remember_good_call("shell", {"command": "echo bad"}, "ERROR: nope")
        check(fb._LAST_GOOD_CALL.get("shell") == '{"command": "echo hi"}',
              "only a call that WORKED is remembered")
        ann = fb.annotate_failure("shell", {"command": "echo bye"}, "ERROR: boom")
        check("the last `shell` call on this box that worked" in ann
              and "echo hi" in ann,
              "a failure is shown the last call to that tool that worked")
        check(ann.startswith("ERROR: boom"), "  appended after the failure, never before")
        same = fb.annotate_failure("shell", {"command": "echo hi"}, "ERROR: boom")
        check("the last `shell` call" not in same,
              "re-sending the identical call is not shown back to the model")
        fb._LAST_GOOD_CALL.clear()

        print()
        if FAILS:
            print(f"{len(FAILS)} failed")
            for f in FAILS:
                print("  - " + f)
            return 1
        print("all small-model checks passed")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
