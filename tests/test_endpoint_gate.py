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

        # ---- C1: the three tiers -------------------------------------------------
        # A reversible recursive delete asks; it is not an absolute block any more.
        for shape in ["rd /s /q C:\\build\\out",
                      "RD /S C:\\build",
                      "rmdir /s C:\\build",
                      "del /s /q C:\\build\\*.obj",
                      "Remove-Item -Recurse -Force C:\\build\\out",
                      "remove-item C:\\build -recurse"]:
            check(f"{shape!r} is no longer an absolute block",
                  fb.is_blocked(shape) is None, str(fb.is_blocked(shape)))
            check(f"{shape!r} asks instead", bool(fb._confirm_hit(shape)), shape)

        check("the shipped confirm list is NON-empty by default",
              bool(fb.DEFAULT_CONFIG["agent"]["confirm_patterns"]),
              str(fb.DEFAULT_CONFIG["agent"]["confirm_patterns"]))
        check("an unrecoverable shape is STILL an unappealable block",
              fb.is_blocked("mkfs.ext4 /dev/nvme0n1")
              and fb._confirm_hit("mkfs.ext4 /dev/nvme0n1") is None)

        # a lane with nobody to ask declines, and the config is what decides
        check("the shipped confirm_without_door default is decline",
              fb.DEFAULT_CONFIG["agent"].get("confirm_without_door") == "decline",
              str(fb.DEFAULT_CONFIG["agent"].get("confirm_without_door")))
        check("a job or sub-agent lane DECLINES a confirm pattern",
              fb.RunReporter(fb.NowhereDestination(), "gate-sess")
              .confirm("rd /s /q C:\\build") is False)
        fb.CONFIG["agent"]["confirm_without_door"] = "allow"
        check("confirm_without_door=allow is what flips it (so the default matters)",
              fb.RunReporter(fb.NowhereDestination(), "gate-sess")
              .confirm("rd /s /q C:\\build") is True)
        fb.CONFIG["agent"]["confirm_without_door"] = "decline"

        # the shell tool asks, quoting the exact command
        harmless = "Remove-Item -Recurse -Force " + str(workdir / "not-there")
        out = fb.tool_shell({"command": harmless}, {})
        check("a recursive delete with no door is DECLINED, not run",
              out.startswith("DECLINED"), out[:140])
        calls.clear()
        out = fb.tool_shell({"command": harmless}, {"confirm_cb": yes})
        check("and a yes runs it", not out.startswith("DECLINED"), out[:140])
        check("the question quoted the exact command",
              bool(calls) and harmless in calls[0], str(calls)[:200])

        # execute_code's SOURCE text walks the same gate
        code_body = 'import os\nos.system("rd /s /q %s")\n' % (workdir / "not-there")
        calls.clear()
        out = fb.tool_execute_code({"code": code_body}, {})
        check("execute_code source is asked about, not only shell",
              out.startswith("DECLINED"), out[:140])
        calls.clear()
        out = fb.tool_execute_code({"code": code_body}, {"confirm_cb": yes})
        check("a yes runs the code", not out.startswith("DECLINED"), out[:120])
        check("and the question quoted the code it was about",
              bool(calls) and "rd /s /q" in calls[0] and "execute_code" in calls[0],
              str(calls)[:200])

        # the block tier is unchanged in reach, and honest about the way out
        out = fb.tool_shell({"command": "mkfs.ext4 /dev/nvme0n1"}, {"confirm_cb": yes})
        check("a blocked shape is refused even WITH a yes", out.startswith("BLOCKED"),
              out[:140])
        check("the refusal names the out-of-band path",
              "by hand" in out and "config.json" in out and "mkfs" in out, out[:320])
        check("and it no longer teaches guard-dodging",
              "safer, more targeted" not in out, out[:320])

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

        # ---- the CONTENT tier: prose in a file is not a command (2026-09-25) ----
        # Three writes in ONE run were gated over the word in a script's own section header
        # ("# ---------- REBOOT / UPDATE STATE ----------"): a 300s stall, a declined write
        # and a rewrite - while the same run's real tree move, `robocopy /MOVE` of 194 items,
        # matched nothing in either tier.
        def gate(text):
            return fb.confirm_gate(text, "write_file x.ps1", {"confirm_cb": yes})

        check("a section header mentioning REBOOT is NOT a command",
              gate("# ---------- REBOOT / UPDATE STATE ----------") is None)
        check("a quoted string mentioning REBOOT is NOT a command",
              gate('[void]$out.Append("RECENT REBOOT/CRITICAL EVENTS:")') is None)
        check("a real reboot line in a file IS a command",
              gate("echo done\nreboot\n") is not None)
        check("a real recursive delete in a file IS a command",
              gate("Remove-Item C:\\tmp -Recurse -Force") is not None)
        check("a tree MOVE in a file IS a command",
              gate("robocopy C:\\a C:\\b /E /MOVE") is not None)
        check("a shell command still takes the command tier",
              fb._confirm_hit("shutdown /r /t 0") is not None)

        # A fresh reconnect gap REFUSES instead of asking (the whole point of the guard),
        # so clear it here: this block is about what the load gate does on a healthy lane.
        fb._ENDPOINT_GAP["at"] = 0.0
        fb._ENDPOINT_GAP["note"] = ""

        # ---- the gate covers LOAD, not just restarts (measured 2026-09-25) -------
        # An operator order about a slow machine made the run send real completion requests
        # to the production model box (~900 generated tokens, 2+ slots of load) while it was
        # itself using that box to think. Reads stay free: gating /props, /metrics or
        # /v1/models would teach the model to avoid its own box's telemetry.
        for shape in (f'curl -X POST http://{host}/v1/chat/completions -d \'{{"max_tokens": 400}}\'',
                      f'python -c "import urllib.request; '
                      f'urllib.request.urlopen(\'http://{host}/v1/completions\')"'):
            check(f"a generation request to the endpoint is gated ({shape[:44]!r})",
                  bool(fb._endpoint_load_request(shape)), shape)
        for shape in (f"curl -s http://{host}/v1/models",
                      f"curl -s http://{host}/props",
                      f"curl -s http://{host}/metrics"):
            check(f"a READ of the endpoint is not gated ({shape[:36]!r})",
                  fb._endpoint_load_request(shape) is None,
                  str(fb._endpoint_load_request(shape)))
        check("a generation request to a DIFFERENT host is not ours to gate",
              fb._endpoint_load_request("curl http://192.0.2.9:11434/v1/completions") is None)

        _probe_code = (f"import urllib.request\n"
                       f"urllib.request.urlopen('http://{host}/v1/chat/completions')\n")
        out = fb.tool_execute_code({"code": _probe_code}, {})
        check("the execute_code door gates it too (no door -> DECLINED)",
              out.startswith("DECLINED") or out.startswith("REFUSED"), out[:200])
        calls.clear()
        out = fb.tool_execute_code({"code": _probe_code}, {"confirm_cb": yes})
        check("and the operator is asked, with the code line quoted back",
              bool(calls) and "chat/completions" in calls[0], str(calls))

        # ---- a shell WRITE to the bot's own memory asks first -------------------
        # The injected note's LAST step was `printf 'notes cleared by cleanup' > notes.md`,
        # and the run did it: the bot's whole memory replaced by a line from a file it had
        # been told to read. Reads stay free.
        _note = str(fb.BASE_DIR / "notes.md")
        for shape, want in (
                (f"printf 'x\\n' > {_note}", True),
                (f"echo hi >> {_note}", True),
                (f"Set-Content -Path {_note} -Value 'x'", True),
                (f"cat {_note}", False),
                (f"grep -n canary {_note}", False),
                ("printf 'x\\n' > /tmp/scratch.md", False)):
            got = bool(fb._prompt_surface_write(shape))
            check(f"surface write: {shape[:50]!r} -> {want}", got == want, f"got {got}")

        # ---- the strict-mode shell is a per-host CHOICE (measured before it was offered) ----
        if fb.IS_WINDOWS:
            fb.CONFIG["agent"]["shell_strict_mode"] = False
            off = fb.tool_shell({"command": "$s = Get-CimInstance Win32_OperatingSystem; "
                                            "'free=' + [int]$s.NoSuchPropHere"}, {})
            fb.CONFIG["agent"]["shell_strict_mode"] = True
            on = fb.tool_shell({"command": "$s = Get-CimInstance Win32_OperatingSystem; "
                                           "'free=' + [int]$s.NoSuchPropHere"}, {})
            fb.CONFIG["agent"]["shell_strict_mode"] = False
            check("strict OFF: a missing property reads as 0 and says nothing",
                  "free=0" in off and "cannot be found" not in off, off[:120])
            check("strict ON: the same command FAILS loudly",
                  "cannot be found" in on, on[:160])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all endpoint-gate checks passed")


if __name__ == "__main__":
    main()
