"""Offline checks for tool disclosure (Phase 2a).

The claim being tested is narrow and important: the payload carries fewer schemas, and
NOTHING becomes unreachable. So the checks come in pairs — a hidden tool must be absent
from the schema list, and calling it anyway must work and stick.

The last section is end-to-end with no server: it stubs the POST, runs a real turn, and
looks at what the request body actually carried.

    python tests/test_disclosure.py
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


def names(schemas):
    return sorted(s["function"]["name"] for s in schemas)


class FakeResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def install_stub_post(fb, seen):
    """Answer every model call with a fixed reply and record the request bodies."""

    def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                  stream=False):
        seen.append(json.loads(json.dumps(payload)))
        return FakeResp({"choices": [{"message": {"role": "assistant",
                                                  "content": "understood"},
                                      "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 11, "completion_tokens": 2}})

    fb._post_watchdog = fake_post


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbdisclose-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)

        # The example tool has to exist in the build under test, so every check below
        # uses EX rather than assuming the scheduler is there.
        EX = "schedule" if fb.REGISTRY.get("schedule") else "delegate_task"
        EX_QUERY = {"schedule": "schedule a job every morning",
                    "delegate_task": "delegate a task"}[EX]
        EX_NEAR = EX + "s"          # one letter off: the near-miss suggestion path

        everything = names(fb.REGISTRY.openai_schemas())
        check(fb.disclosure_on() is True, "disclosure is on by default")
        visible = names(fb.select_tool_schemas("s1"))
        check("find_tools" in visible, "the discovery tool is always visible")
        check(len(visible) < len(everything),
              f"the payload carries fewer tools ({len(visible)} of {len(everything)})")
        hidden = fb.hidden_tools("s1")
        check(EX in hidden and len(hidden) >= 3,
              f"rarely used tools are hidden: {hidden}")
        check("shell" in visible and "read_file" in visible and "write_file" in visible,
              "the five primitives stay visible")
        check(not set(visible) & set(hidden), "visible and hidden do not overlap")

        vis_tokens = fb.est_tokens(json.dumps(fb.select_tool_schemas("s1")))
        all_tokens = fb.est_tokens(json.dumps(fb.REGISTRY.openai_schemas()))
        check(vis_tokens < all_tokens,
              f"the visible schemas cost less ({vis_tokens} < {all_tokens} tokens)")

        # ---- the always-on payload has a budget, and the budget is gated ----
        # A convention that is not gated does not hold. These schemas ride on EVERY
        # call, so they are paid for before the first tool call of every run, and the
        # prose had crept to 13,898 chars across the registry with nobody watching
        # (measured 2026-09-19). The numbers below were measured the same day against
        # this harness: 7,133 chars over 13 always-visible tools, fattest single
        # schema ask_user at 1,078. It is a ceiling, not a target: when it fires, cut
        # prose or drop a tool - raising the number is a decision, not a fix.
        #
        # RAISED 2026-09-21, on the record rather than quietly: the experiment ledger
        # tool is always-on BY DESIGN (a run has to know what this box already tested
        # BEFORE it runs an arm), and adding it moved the block from 7,133 over 13 tools
        # to 8,392 over 14 - experiment itself 1,170 chars, ask_user 1,165 after its
        # description was corrected to say an unanswered question STOPS the run. The new
        # ceiling is that measurement plus ~6% headroom, not room to grow.
        SCHEMA_BUDGET = 8900          # chars, measured 8,392 + ~6% headroom
        TOOL_SCHEMA_CAP = 1200        # chars for one tool, fattest measured 1,170
        always_on = fb.select_tool_schemas(None)
        block = json.dumps(always_on)
        check(len(block) <= SCHEMA_BUDGET,
              f"the always-on schema block is inside its budget "
              f"({len(block)} of {SCHEMA_BUDGET} chars, {fb.est_tokens(block)} tokens)")
        sizes = sorted(((len(json.dumps(s)), s["function"]["name"]) for s in always_on),
                       reverse=True)
        check(sizes[0][0] <= TOOL_SCHEMA_CAP,
              f"no single tool schema exceeds {TOOL_SCHEMA_CAP} chars "
              f"(fattest {sizes[0][1]} {sizes[0][0]})")

        # ---- asking for a tool reveals it ----------------------------------
        out = fb.tool_find_tools({"query": EX_QUERY}, {"session_key": "s1"})
        check(EX in out and "args:" in out,
              "find_tools returns the matched tool WITH its arguments")
        check("now callable" in out, "find_tools says the tool is now callable")
        check(EX in names(fb.select_tool_schemas("s1")),
              "the revealed tool is in this session's payload")

        # a different session is unaffected
        check(EX not in names(fb.select_tool_schemas("s2")),
              "another session does not inherit the reveal")

        # ---- no query lists what exists, without revealing ------------------
        out = fb.tool_find_tools({}, {"session_key": "s3"})
        check(EX in out, "an empty query lists the hidden tools")
        check(EX not in names(fb.select_tool_schemas("s3")),
              "listing them does not reveal them")

        # ---- all=true reveals everything ------------------------------------
        out = fb.tool_find_tools({"all": True}, {"session_key": "s4"})
        check(fb.hidden_tools("s4") == [], "all=true leaves nothing hidden")
        check(len(fb.select_tool_schemas("s4")) == len(everything),
              "an all=true session carries the full registry again")

        # ---- a bad query is honest ------------------------------------------
        out = fb.tool_find_tools({"query": "zzzznothing"}, {"session_key": "s5"})
        check("No tool matched" in out and "all=true" in out,
              "an unmatched query says so and points at all=true")

        # ---- calling a hidden tool works, and sticks ------------------------
        ctx = {"session_key": "s6"}
        name, args, out = fb.Agent._exec_tool(
            fb.AGENT, {"function": {"name": "notes", "arguments": {}}}, ctx)
        check(not out.startswith("ERROR"), f"a hidden tool still runs ({out[:60]!r})")
        check("was not in your tool list" in out,
              "the result says the tool was revealed")
        check("notes" in names(fb.select_tool_schemas("s6")),
              "and it is in the payload from then on")

        # a near-miss name now suggests the real one
        _, _, out = fb.Agent._exec_tool(
            fb.AGENT, {"function": {"name": EX_NEAR, "arguments": {}}}, ctx)
        check("unknown tool" in out and "find_tools" in out,
              f"an unknown tool points at find_tools ({out[:80]!r})")
        check(EX in out, f"a near-miss name suggests the real tool ({out[:110]!r})")

        # An ABSENT tool must not read like a hidden one. A dropped-in runbook written
        # for another harness names tools no build here has, and the old hint answered
        # every unknown with "find_tools can reveal them" - which is what sent the model
        # looking for a tool that was never on the box.
        _, _, out = fb.Agent._exec_tool(
            fb.AGENT, {"function": {"name": "computer_use", "arguments": {}}}, ctx)
        check("an absent tool says it is absent",
              "unknown tool" in out and "exists on this box" in out)
        check("and does not send the model hunting for it",
              "find_tools" not in out)
        check("and names the way to have that capability here",
              "create_tool" in out)
        out = fb.tool_find_tools({"query": "send it to a subagent"}, {"session_key": "s9"})
        check("delegate" in out, "plain language finds the delegation tool")

        # ---- the off switch is a true rollback ------------------------------
        fb.CONFIG["agent"]["tool_disclosure"] = False
        check(names(fb.select_tool_schemas("s7")) == everything,
              "tool_disclosure=false sends the whole registry")
        check(fb.hidden_tools("s7") == [], "and hides nothing")
        out = fb.tool_find_tools({}, {"session_key": "s7"})
        check("already in your list" in out,
              "find_tools says everything is already visible")
        fb.CONFIG["agent"]["tool_disclosure"] = True

        # ---- core_tools overrides the visible set ---------------------------
        fb.CONFIG["agent"]["core_tools"] = ["shell", "find_tools"]
        vis = names(fb.select_tool_schemas("s8"))
        check(vis == ["find_tools", "shell"], f"core_tools is honoured exactly ({vis})")
        fb.CONFIG["agent"]["core_tools"] = []

        # ---- end to end: what the request body really carried ---------------
        seen = []
        install_stub_post(fb, seen)
        answer = fb.AGENT.run("disc-e2e", "say something short")
        check(bool(answer), f"a stubbed turn answers ({answer[:40]!r})")
        check(bool(seen), "the model was actually called")
        sent = names(seen[0].get("tools") or [])
        check(sent == names(fb.select_tool_schemas("disc-e2e")),
              f"the request carried exactly the visible set ({len(sent)} tools)")
        check(EX not in sent, "a hidden tool is absent from the request body")

        # reveal one, run again in the same session, and it is there
        fb.reveal_tools("disc-e2e", [EX])
        seen.clear()
        fb.AGENT.run("disc-e2e", "and again")
        sent2 = names(seen[0].get("tools") or [])
        check(EX in sent2, "the revealed tool rides in the next request")

        # ---- the banner reports what the REQUEST carries --------------------
        # (audit, 2026-09-22: it counted REGISTRY.openai_schemas(), so a real
        # install read "34 tool schemas" while its requests carried 14 - the
        # number a reader checks the ~4k-token claim against was the wrong one.)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fb.cli_banner()
        lines = [l for l in buf.getvalue().splitlines() if "prompt overhead" in l]
        check(bool(lines), "the banner prints an overhead line")
        if lines:
            text = lines[0]
            visible = fb.select_tool_schemas(None)
            registry_n = len(fb.REGISTRY.openai_schemas())
            hidden_n = registry_n - len(visible)
            static = fb.est_tokens(fb.build_system_prompt() + json.dumps(visible))
            check("%d tool schemas" % len(visible) in text,
                  f"the banner counts the VISIBLE schemas ({len(visible)}), not the "
                  f"registry's {registry_n}")
            check(fb.fmt_tokens(static) in text,
                  f"the static number is what the wire carries ({fb.fmt_tokens(static)})")
            check(not hidden_n or ("%d hidden" % hidden_n) in text,
                  "hidden tools are named separately instead of being added in")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all tool-disclosure checks passed")


if __name__ == "__main__":
    main()
