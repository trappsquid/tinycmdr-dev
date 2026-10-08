"""Delegation: a child prompt, a shared context block, a per-child budget and its cost.

A child inherited the FULL main prompt - the Mattermost framing, "narrate as you go",
ask_user, and an instruction to delegate that its own depth check refuses - and had no
wall-clock of its own (one hung child held a thread for the parent's whole 75 minutes). A
batch had no way to share one Goal/Contract block, so N children each restated the same
interfaces: context is shared, each task is self-contained, the child's prompt is
purpose-built, and a settled child always returns the same typed shape plus what it cost.

    python tests/test_delegation.py
"""
import json
import shutil
import sys
import tempfile
import time
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


CONTRACT_ANSWER = (
    "did the thing\n\n```result\n"
    '{"status": "ok", "summary": "did it", "evidence": ["e"], '
    '"blockers": [], "followups": []}\n```\n')


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbdelegate-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        A = fb.AGENT
        real_run = A.run

        # ---- the child prompt: same rules, minus what a child cannot do
        main_prompt = fb.build_system_prompt()
        sub_prompt = fb.build_system_prompt(subagent=True)
        check(len(sub_prompt) < len(main_prompt), "the child prompt is shorter",
              (len(sub_prompt), len(main_prompt)))
        check("SUB-AGENT" in sub_prompt and "no operator" in sub_prompt.lower()
              or "no operator" in sub_prompt,
              "it says there is no operator", sub_prompt[:200])
        check("Narrate as you go" not in sub_prompt,
              "it drops the narration instruction")
        check("use `ask_user` and wait" not in sub_prompt,
              "it drops ask_user")
        check("Completion: you are executing ONE assignment" in sub_prompt,
              "it states the completion rule")
        check("A sub-agent's report is a CLAIM, not a measurement" in sub_prompt
              and "No, do not use ask_user" not in sub_prompt,
              "it keeps the no-fabrication rules", sub_prompt[-400:])

        # ---- the child prompt carries no pointer to a block it does not carry -----------
        # `custom_block` is gated off for a child, and the hidden-tool inventory pointed at it
        # anyway ("the custom tools listed at the end of this prompt"), on every box with a
        # drop-in tool - a dead pointer in the one line that tells the model what it can call
        # (run 22, A-2026-10-07-57).
        fb.REGISTRY.custom["probe_tool"] = {"schema": {"type": "function", "function": {
            "name": "probe_tool", "description": "a drop-in probe",
            "parameters": {"type": "object", "properties": {}}}}}
        try:
            main_p = fb.build_system_prompt()
            sub_p = fb.build_system_prompt(subagent=True)
        finally:
            fb.REGISTRY.custom.pop("probe_tool", None)
        check("More tools on this box, by category" in main_p
              and "custom tools listed at the end of this prompt" in main_p,
              "the parent prompt carries the custom shelf and says so",
              main_p[-600:])
        check("More tools on this box, by category" not in sub_p,
              "the child prompt carries no custom shelf",
              "the child got a custom shelf")
        check("custom tools listed at the end" not in sub_p,
              "...and does not point at one",
              "the child prompt names a block it does not carry")
        check("Also on this box, not in your tool list" in sub_p,
              "the child still gets the hidden-name inventory",
              "the inventory line is gone")

        # ---- one child: context rides the prompt, budget and prompt go to run()
        seen = {}

        def fake_run(session_key, user_text, **kw):
            seen["text"] = user_text
            seen["kw"] = kw
            A.last_usage[session_key] = {"calls": 2, "prompt": 100, "completion": 20}
            return CONTRACT_ANSWER

        A.run = fake_run
        try:
            out = fb._run_delegate_one("check /var", "GOAL: be safe; CONTRACT: nobody else",
                                       None, 30, {"depth": 0, "model": "m0"})
        finally:
            A.run = real_run
        check("GOAL" in seen["text"] and "check /var" in seen["text"],
              "the shared context is prepended to the task", seen["text"][:120])
        check(seen["kw"].get("max_seconds") == 30.0,
              "the per-child wall clock is passed", seen["kw"].get("max_seconds"))
        check(seen["kw"].get("system_prompt") == sub_prompt,
              "the child gets the subagent prompt")
        check(seen["kw"].get("depth") == 1, "depth advances", seen["kw"].get("depth"))
        check("sub-agent cost" in out and "2 call(s)" in out,
              "the cost line is attached", out[-200:])
        check("status ok" in out, "the typed result is rendered", out[:160])
        check(A.model_overrides == {} and "last_usage" in dir(A),
              "the child's model override is cleaned up", A.model_overrides)

        # ---- a crash still yields the typed shape
        def boom(session_key, user_text, **kw):
            raise RuntimeError("child exploded")

        A.run = boom
        try:
            out = fb._run_delegate_one("x", "", None, 5, {"depth": 0})
        finally:
            A.run = real_run
        check("RuntimeError" in out and "failed" in out.lower(),
              "a crashing child is reported, not raised", out[:200])

        # ---- a batch: ordered output, one worker per endpoint slot at most
        order = []

        def slow_run(session_key, user_text, **kw):
            order.append(user_text.split()[0])
            time.sleep(0.05)
            A.last_usage.pop(session_key, None)
            return CONTRACT_ANSWER

        A.run = slow_run
        try:
            out = fb._run_delegate_batch(
                [{"task": "alpha"}, {"task": "beta", "name": "B"},
                 {"task": "gamma"}], "shared context here", {"depth": 0})
        finally:
            A.run = real_run
        a, b, g = out.index("--- sub-agent 1/3"), out.index("--- sub-agent 2/3"), \
            out.index("--- sub-agent 3/3")
        check(a < b < g, "batch results come back in input order", (a, b, g))
        check("(B)" in out, "a named entry keeps its label", out[:200])
        check("shared context here" not in out,
              "the shared context is not echoed, only used", out[:200])

        # ---- bad batch entries are named, never half-run
        out = fb._run_delegate_batch([{"task": "ok"}, {"name": "no task"}], "", {"depth": 0})
        check(out.startswith("ERROR") and "tasks[1]" in out,
              "a malformed entry refuses the whole batch", out[:120])

        # ---- the tool door
        check(fb.tool_delegate_task({}, {"depth": 0}).startswith("ERROR"),
              "no task and no tasks is an ERROR")
        check(fb.tool_delegate_task({"task": "x"}, {"depth": 1}).startswith("ERROR"),
              "a grandchild is refused")

        # ---- tool_done is relayed to the parent's reporter
        seen_relay = []

        def tool_done(name, args, output, elapsed, src=None):
            seen_relay.append((name, src))

        relay = fb._relay_callbacks({"report": {"tool_done": tool_done}}, "sub:t")
        relay["progress_done_cb"]("shell", {}, "out", 1.0)
        check(seen_relay and seen_relay[0][1] == "sub:t",
              "the child's completed tool calls reach the parent lane", seen_relay)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all delegation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
