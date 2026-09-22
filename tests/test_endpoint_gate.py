"""The endpoint gate covers TOOLS, and a fresh reconnect gap makes it refuse.

Audit of the campaign harness, 2026-09-21. Two holes:
  * the gate only ever read SHELL text, so a tool that restarts the model box (an
    inferctl/llamasrv verb) moved :8081 with no gate at all;
  * a confirmation asked while the lane is losing messages can be answered by nobody,
    and the run then reads the silence as consent - the very failure the guard exists
    for. So while a catch-up recovery is fresh the gate REFUSES instead of asking.

    python tests/test_endpoint_gate.py
"""
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import run_scenario  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILS.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


TOOL_SRC = '''\
NAME = "inferctl"
DESCRIPTION = "Move the llama server on the LAN box: restart, stop, or load a model."
SCHEMA = {"type": "object", "properties": {}}
MUTATES = True


def run(args, ctx):
    return "RESTARTED-THE-ENDPOINT"
'''

PLAIN_TOOL_SRC = '''\
NAME = "hello"
DESCRIPTION = "Say hello."
SCHEMA = {"type": "object", "properties": {}}


def run(args, ctx):
    return "HELLO"
'''


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbtest-endgate-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        fb._ENDPOINT_GAP["at"] = 0.0
        fb._ENDPOINT_GAP["note"] = ""

        base = str(fb.CONFIG["llm"]["base_url"])
        host = base.split("//", 1)[-1].split("/")[0]
        check(f"the staged config names an endpoint ({host})", bool(host))
        cmd = f"Stop-Service -Name 'llama-{host}' -WhatIf"

        calls = []

        def yes(subject):
            calls.append(subject)
            return True

        def no(subject):
            calls.append(subject)
            return False

        # ---- the shell half still works, through the same gate -------------------
        check("the command really is endpoint self-harm",
              bool(fb._endpoint_self_harm(cmd)), cmd)
        out = fb.tool_shell({"command": cmd}, {})
        check("no confirm door -> DECLINED, nothing ran",
              out.startswith("DECLINED") and "needs operator confirmation" in out,
              out[:160])
        out = fb.tool_shell({"command": cmd}, {"confirm_cb": no})
        check("a 'no' is a no", out.startswith("DECLINED"), out[:120])

        # ---- a TOOL that moves the endpoint is gated too -------------------------
        (fb.TOOLS_DIR / "inferctl.py").write_text(TOOL_SRC, encoding="utf-8")
        (fb.TOOLS_DIR / "hello.py").write_text(PLAIN_TOOL_SRC, encoding="utf-8")
        ok, err = fb.REGISTRY.reload_tool("inferctl")
        check("the custom tool loads", ok, str(err))
        fb.REGISTRY.reload_tool("hello")
        entry = fb.REGISTRY.custom.get("inferctl") or {}
        check("it is marked endpoint_touching from its name/description",
              entry.get("endpoint_touching") == "inferctl",
              str(entry.get("endpoint_touching")))

        call = {"function": {"name": "inferctl", "arguments": {}}}
        name, args, out = fb.Agent._exec_tool(fb.AGENT, call, {})
        check("a call on it with no door to ask through is DECLINED",
              out.startswith("DECLINED"), out[:160])
        check("and the tool did NOT run", "RESTARTED" not in out)

        calls.clear()
        name, args, out = fb.Agent._exec_tool(fb.AGENT, call, {"confirm_cb": no})
        check("the operator's 'no' stops it", out.startswith("DECLINED"))
        check("the confirmation named the tool", calls and "inferctl" in calls[0],
              str(calls))

        calls.clear()
        name, args, out = fb.Agent._exec_tool(fb.AGENT, call, {"confirm_cb": yes})
        # the result carries the disclosure note (the tool was hidden until now)
        check("a yes lets it through", out.startswith("RESTARTED-THE-ENDPOINT"), out[:80])
        check("and the gate asked first", bool(calls))

        pcall = {"function": {"name": "hello", "arguments": {}}}
        calls.clear()
        name, args, out = fb.Agent._exec_tool(fb.AGENT, pcall, {})
        check("an ordinary tool is not gated at all",
              out.startswith("HELLO") and not calls, out[:80])

        # ---- no box is hard-coded: the marker list is config ---------------------
        fb.CONFIG["agent"]["endpoint_tools"] = ["zzz-someone-elses-launcher"]
        fb.REGISTRY.reload_tool("inferctl")
        entry = fb.REGISTRY.custom.get("inferctl") or {}
        check("with the list changed, the same tool is no longer endpoint_touching",
              not entry.get("endpoint_touching"), str(entry.get("endpoint_touching")))
        fb.CONFIG["agent"]["endpoint_tools"] = ["inferctl", "llamasrv", "serve_", "llama",
                                               "vllm"]
        fb.REGISTRY.reload_tool("inferctl")

        # ---- a fresh steering gap makes the gate REFUSE, not ask -----------------
        fb.note_steering_gap("recovered a message from 12:00:00 (post-abc)")
        note = fb.steering_gap_note()
        check("a recovery is recorded as a gap", "post-abc" in note, note)

        calls.clear()
        out = fb.tool_shell({"command": cmd}, {"confirm_cb": yes})
        check("with a gap fresh, a shell command touching the endpoint is REFUSED",
              out.startswith("REFUSED"), out[:160])
        check("and the operator was NOT asked", not calls, str(calls))
        check("the refusal says the lane lost messages", "LOST messages" in out, out[:240])

        calls.clear()
        name, args, out = fb.Agent._exec_tool(fb.AGENT, call, {"confirm_cb": yes})
        check("and an endpoint TOOL is refused the same way",
              out.startswith("REFUSED") and "RESTARTED" not in out, out[:160])
        check("with no question asked into the gap", not calls, str(calls))

        # a bounded suspicion, not a permanent state
        fb._ENDPOINT_GAP["at"] = time.time() - fb._ENDPOINT_GAP_FRESH - 30
        check("a gap goes stale", fb.steering_gap_note() == "")
        calls.clear()
        name, args, out = fb.Agent._exec_tool(fb.AGENT, call, {"confirm_cb": yes})
        check("after it goes stale the gate asks again (and a yes runs)",
              out.startswith("RESTARTED-THE-ENDPOINT") and bool(calls), out[:90])

        # ---- the SWEEP is what records a gap ------------------------------------
        fb._ENDPOINT_GAP["at"] = 0.0
        fb._ENDPOINT_GAP["note"] = ""
        fb.CONFIG["mattermost"]["allowed_users"] = ["u1"]
        now = time.time()
        post = {"id": "post-gap", "user_id": "u1", "message": "hello?",
                "create_at": int((now - 20) * 1000), "channel_id": "chan-gap"}

        class _Posts:
            def get_posts_for_channel(self, cid, params=None):
                return {"order": ["post-gap"], "posts": {"post-gap": post}}

        d = object.__new__(fb.MattermostDispatcher)
        d.last_seen = {"chan-gap": now - 120}
        d.seen = []
        d.bot_user_id = "bot-1"
        d.channel_dm = {}
        d.driver = types.SimpleNamespace(posts=_Posts(), channels=types.SimpleNamespace(
            get_channel=lambda cid: {"type": "D"}))
        d.queued = []
        d.enqueue = lambda *a: d.queued.append(a[-1])
        n = d._catch_up_once(now)
        check("the sweep recovers the missed post", n == 1, str(n))
        check("and the recovery is what sets the gap flag",
              fb.steering_gap_note() != "" and "post-gap" in fb.steering_gap_note(),
              fb.steering_gap_note())
        check("and the recovered post really was queued", d.queued == ["hello?"],
              str(d.queued))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all endpoint-gate checks passed")


if __name__ == "__main__":
    main()
