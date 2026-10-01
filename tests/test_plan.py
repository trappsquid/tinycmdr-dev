"""Offline checks for the harness-held plan and the runway (Phase 2c).

Two kinds of check here. The unit ones poke plan_render/run_block/volatile_context
directly. The last two drive a real turn against a stubbed model, because "the harness
re-sends the plan with the position" and "the drift nudge fires" are claims about what
ends up in the REQUEST BODY, and only an end-to-end run can prove those.

    python tests/test_plan.py
"""
import contextlib
import io
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

        # ---- the ledger's staleness rule lives in ONE place (audit 2026-09-29) ----------
        # The prompt labelled items `(3d, stale)` while `tinycmdr tasks` - the verb the operator
        # is pointed at - showed no age at all: each view had its own copy of the rule, so they
        # could disagree about the same ledger. They share task_age() now, and this pins what it
        # decides, including the correction that came out of putting them side by side.
        # The age stamps are computed from NOW, never a literal date. They were "2026-09-26
        # 15:48" against a literal "(3d, stale)" expectation, which made this a check that
        # graded the calendar: it went red by itself at 15:48 on 2026-09-30 (four days) and
        # would have stayed red forever after - CI run 36778872020 was the first. 74h back is
        # tests/test_ledger.py's idiom ("30 * 3600" for "(1d"), and the extra two hours over
        # three days absorb the truncation to the minute and a DST step, so the rendered age is
        # "(3d" on any day in any timezone.
        old_stamp = time.strftime("%Y-%m-%d %H:%M",
                                  time.localtime(time.time() - 3 * 86400 - 2 * 3600))
        old_open = {"id": 1, "desc": "an old open item", "status": "open",
                    "updated": old_stamp, "created": old_stamp}
        old_done = {"id": 2, "desc": "an old finished item", "status": "done",
                    "updated": old_stamp, "created": old_stamp}
        fresh = {"id": 3, "desc": "just added", "status": "open",
                 "updated": time.strftime("%Y-%m-%d %H:%M")}
        check(fb.task_age(fresh)[1] is False, "a fresh item is not stale")
        check(fb.task_age(old_open)[1] is True, "an old OPEN item is stale")
        check(fb.task_age(old_done)[1] is False,
              "  and an old FINISHED item is not - stale means it needs attention")

        fb.TASKS_FILE.write_text(json.dumps({"items": [old_open, old_done, fresh]}),
                                 encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fb._verb_tasks([])
        shown = buf.getvalue()
        rows = [l for l in shown.splitlines() if l.strip().startswith("#")]
        open_row = next((l for l in rows if "an old open item" in l), "")
        done_row = next((l for l in rows if "an old finished item" in l), "")
        check("(3d, stale)" in open_row,
              f"the operator's view shows the age and the judgement ({open_row.strip()[:70]})")
        check("stale" not in done_row,
              f"  and only on the row that needs attention ({done_row.strip()[:70]})")

        # The same judgement, on the OTHER view. This block is re-sent as a trailing
        # message on every call, so a stale open item's full text rode in it for ever
        # (measured 2026-09-30: three 30-day-old items, three rows, every one
        # `('30d', True)`). The id and the AGE stay - the age is what makes "this is
        # not your plan" checkable without a tool call - and the text goes, exactly
        # as a done item's instruction text does. Nothing leaves the ledger.
        rendered = fb.render_task_prompt()
        check("an old open item" not in rendered and "#1 [open] (3d, stale)" in rendered,
              "the prompt keeps a stale open item's id and age, not its text")
        check("just added" in rendered and "ledger: 2 open" in rendered,
              "...keeps the fresh item's row and counts every open item")
        check("their age is shown, their text is not" in rendered
              and "`action=list` shows every item" in rendered,
              "...and says where the text went, without deleting the item")

        # ---- the harness asks the OPERATOR, instead of hoping the model does ----------
        # The prompt's standing instruction says an inherited open item "is not your
        # instruction: ask the operator before you resume one". Measured 2026-09-29 the model did
        # not ask, and the operator found out from a tool call that happened to mention it - so
        # the harness asks. It must ask ONCE per item version, or it becomes a nag, and never
        # from a sub-agent, which has no operator of its own.
        class _Rep:
            def __init__(self):
                self.said = []

            def say(self, text):
                self.said.append(text)

        stale_item = {"id": 11, "desc": "an item nobody came back to", "status": "open",
                      "updated": "2026-09-20 09:00", "created": "2026-09-20 09:00"}
        fb.TASKS_FILE.write_text(json.dumps({"items": [stale_item, old_done, fresh]}),
                                 encoding="utf-8")
        rep = _Rep()
        check(fb.notice_stale_tasks(rep) == 1, "a stale OPEN item is announced to the operator")
        said = (rep.said or [""])[0]
        check("#11" in said and "an item nobody came back to" in said, said[:120])
        check("NOT this run" in said, "  and it says the item is not this run's instruction")

        rep2 = _Rep()
        check(fb.notice_stale_tasks(rep2) == 0 and not rep2.said,
              "  it does not nag: one announcement per item VERSION")
        told = json.loads(fb.TASKS_FILE.read_text(encoding="utf-8"))["items"][0]
        check(told.get("told_updated") == "2026-09-20 09:00",
              f"  and the version it announced is recorded ({told.get('told_updated')})")

        told["updated"] = "2026-09-21 09:00"
        fb.TASKS_FILE.write_text(json.dumps({"items": [told]}), encoding="utf-8")
        check(fb.notice_stale_tasks(_Rep()) == 1, "  a later edit makes it eligible again")

        check(fb.notice_stale_tasks(_Rep(), depth=1) == 0,
              "a sub-agent never announces - it has no operator of its own")
        keep_notice = fb.CONFIG["agent"].get("ledger_notice")
        try:
            fb.CONFIG["agent"]["ledger_notice"] = False
            check(fb.notice_stale_tasks(_Rep()) == 0, "ledger_notice=false turns it off")
        finally:
            fb.CONFIG["agent"]["ledger_notice"] = keep_notice
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all plan/runway checks passed")


if __name__ == "__main__":
    main()
