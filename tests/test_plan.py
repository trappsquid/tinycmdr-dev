"""Offline checks for the harness-held plan and the runway (Phase 2c).

Two kinds of check here. The unit ones poke plan_render/run_block/volatile_context
directly. The last two drive a real turn against a stubbed model, because "the harness
re-sends the plan with the position" and "the drift nudge fires" are claims about what
ends up in the REQUEST BODY, and only an end-to-end run can prove those.

    python tests/test_plan.py
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
    """script(i, payload) -> response dict for the i-th model call."""
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


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbplan-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)

        check(fb.CONFIG["agent"].get("plan_drift_after") == 8,
              "plan_drift_after is configured")
        check("plan" not in [s["function"]["name"] for s in
                             fb.select_tool_schemas("s1")],
              "the plan tool is NOT in the always-visible set (measured: never called)")
        check(fb.REGISTRY.get("plan") is not None,
              "but it stays in the registry, so it is one call away")
        check(fb.hidden_tools("s1") and "plan" in fb.hidden_tools("s1"),
              "and find_tools can reveal it")

        # ---- set / render --------------------------------------------------
        ctx = {"session_key": "s1"}
        out = fb.tool_plan({"action": "set",
                            "steps": "check disk\n1. restart the service\n- verify the port"},
                           ctx)
        st = fb.run_state("s1")
        check(len(st["plan"]) == 3, f"three steps recorded ({len(st['plan'])})")
        check([s["text"] for s in st["plan"]] ==
              ["check disk", "restart the service", "verify the port"],
              "list markers and numbering are stripped from the step text")
        check("1. [open] check disk" in out, f"the plan renders with status ({out[:80]!r})")
        check("Plan for this run (0/3 done)" in out, "the header counts progress")

        # ---- moving it -----------------------------------------------------
        out = fb.tool_plan({"action": "doing", "id": 2}, ctx)
        check("[NOW ] restart the service" in out, "doing marks the current step")
        out = fb.tool_plan({"action": "done", "id": 2, "note": "unit active"}, ctx)
        check("[done] restart the service" in out and "unit active" in out,
              "done records the one-line note")
        check("Plan for this run (1/3 done)" in out, "the header follows progress")
        out = fb.tool_plan({"action": "blocked", "id": 3, "note": "port not answering"}, ctx)
        check("[BLOCKED] verify the port" in out, "blocked is visible in the render")
        out = fb.tool_plan({"action": "doing", "id": 1}, ctx)
        st = fb.run_state("s1")
        check(sum(1 for s in st["plan"] if s["status"] == "doing") == 1,
              "only one step can be current at a time")
        check(fb.plan_open("s1") and len(fb.plan_open("s1")) == 2,
              f"plan_open lists what is left ({fb.plan_open('s1')})")

        # ---- bad input is answered, not crashed ----------------------------
        out = fb.tool_plan({"action": "done", "id": 99}, ctx)
        check("No plan step with id 99" in out, "an unknown id says so")
        out = fb.tool_plan({"action": "set", "steps": ""}, ctx)
        check("nothing recorded" in out, "an empty set does not wipe a plan silently")
        out = fb.tool_plan({"action": "show"}, {"session_key": "s2"})
        check("No plan for this run yet" in out, "another session has its own (empty) plan")

        # ---- the render is bounded -----------------------------------------
        # The cap is read from the config and the test offers MORE steps than it, so the
        # check stays honest when the budget moves (it was raised to 24 on 2026-09-17 and a
        # hard-coded 20 steps then no longer proved anything).
        plan_cap = int(fb.CONFIG["agent"]["plan_max_steps"])
        fb.tool_plan({"action": "set",
                      "steps": "\n".join(f"step {i}" for i in range(1, plan_cap + 6))}, ctx)
        st = fb.run_state("s1")
        check(len(st["plan"]) == plan_cap,
              f"a plan is capped at plan_max_steps ({len(st['plan'])} of {plan_cap})")

        # ---- the runway ----------------------------------------------------
        fb.tool_plan({"action": "clear"}, ctx)
        fb.run_state("s1")["calls"] = 0
        check(fb.run_block("s1") == "", "no runway line before the first call")
        fb.run_state("s1")["calls"] = 12
        blk = fb.run_block("s1")
        check("12 of" in blk and "tool calls used" in blk,
              f"the runway line shows position ({blk!r})")
        check("plan_drift_after" not in blk, "no config keys leak into the prompt")

        vc = fb.volatile_context(session_key="s1")
        check("Run so far:" in vc, "the volatile block carries the runway")
        check(fb.volatile_context(session_key="never-run") .find("Run so far:") == -1,
              "a session with no run gets no runway line")

        # ---- end to end: the plan and the nudge reach the request body ------
        # First, the derived case: a request that lists its own steps needs no cooperation.
        _d = fb.derive_plan_from_text("Do these in order:\n1. check the disk space now\n"
                                      "2. restart the service cleanly\n"
                                      "3. verify the port answers")
        check(len(_d) == 3, f"steps are parsed out of a numbered request ({_d})")
        check(fb.derive_plan_from_text("no list here, just a sentence") == [],
              "a request without a list yields no plan")
        check(fb.derive_plan_from_text("1. only one item") == [],
              "one item is not a plan")
        fb.run_state("s3", create=True)
        seen3 = []
        install_stub(fb, seen3, lambda i, p: text_reply("ok"))
        fb.AGENT.run("s3", "Please do these in order:\n"
                           "1. check the free space on the system drive\n"
                           "2. report the tinycmdr version on this box\n"
                           "3. count the files under the tests directory")
        st3 = fb.run_state("s3")
        check(len(st3["plan"]) == 3 and st3.get("derived"),
              f"a run derives its plan from the request ({len(st3['plan'])} steps)")
        blob3 = payload_blob(seen3[0])
        check("Plan for this run" in blob3 and "check the free space" in blob3,
              "the derived plan is in the very first request")
        check("parsed those steps from the request" in blob3,
              "and the model is told the steps were parsed, so it can revise them")
        fb.tool_plan({"action": "set", "steps": "my own step one\nmy own step two"}, ctx)
        check(fb.run_state("s1").get("derived") is False,
              "the model's own plan replaces a derived one")

        # Then the tool-driven case: a plan the model writes itself.
        fb.tool_plan({"action": "set", "steps": "write the file\nverify it parses"}, ctx)

        def script(i, payload):
            if i == 0:
                return tool_call_reply("shell", {"command": "echo step-one"}, f"a{i}")
            if i <= 10:      # ten calls with no plan movement: drift must fire
                return tool_call_reply("shell", {"command": f"echo filler-{i}"}, f"a{i}")
            return text_reply("done")

        seen = []
        install_stub(fb, seen, script)
        fb.AGENT.run("s1", "do the two-step thing")
        blobs = [payload_blob(p) for p in seen]
        check(any("Plan for this run" in b for b in blobs),
              "the plan is re-sent in the request body")
        check(any("Run so far:" in b and "tool calls used" in b for b in blobs),
              "the runway is re-sent too")
        check(any("tool calls have run since any plan step moved" in b for b in blobs),
              "the drift nudge reaches the model when nothing moves")
        st = fb.run_state("s1")
        check(st["nudges"] >= 1, f"the drift nudge was counted ({st['nudges']})")

        # ---- end to end: the cap reports what is still open ----------------
        fb.tool_plan({"action": "set", "steps": "step one\nstep two\nstep three"}, ctx)
        fb.CONFIG["agent"]["max_steps"] = 3
        # auto_continue_max=0 on purpose: with continuation budget left, a cap and an open
        # plan open a NEW segment instead of wrapping up (tests/test_stall.py pins that).
        # This scenario is the wrap-up itself, so it takes the setting that reaches it.
        fb.CONFIG["agent"]["auto_continue_max"] = 0

        def cap_script(i, payload):
            if i < 6:
                return tool_call_reply("shell", {"command": f"echo work-{i}"}, f"b{i}")
            return text_reply("wrapped up")

        seen2 = []
        install_stub(fb, seen2, cap_script)
        fb.AGENT.run("s1", "another multi-step thing")
        final = payload_blob(seen2[-1])
        check("budget is exhausted" in final, "the forced wrap-up still happens")
        check("Open plan steps at the cap" in final,
              "the wrap-up names the plan steps that are still open")
        check("step one" in final, "and it names them by text, not just by count")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all plan/runway checks passed")


if __name__ == "__main__":
    main()
