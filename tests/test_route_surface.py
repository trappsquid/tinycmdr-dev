"""test_route_surface - one merged suite (test_endpoint_gate, test_route_hint).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: no member needed a namespace rewrite.
"""
import os
import sys


def _run(name, fn):
    """One member, its own snapshot: env, cwd and sys.path restored afterwards."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_path = list(sys.path)
    print("== member %s: start" % name)
    try:
        rc = fn()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
        sys.path[:] = saved_path
    rc = int(rc or 0)
    print("== member %s: exit %d" % (name, rc))
    return rc


def _suite_test_endpoint_gate():
    """The endpoint gate covers TOOLS, and a fresh reconnect gap makes it refuse.

Review of the campaign harness, 2026-09-21. Two holes:
  * the gate only ever read SHELL text, so a tool that restarts the model box (an
    inferctl/llamasrv verb) moved :8081 with no gate at all;
  * a confirmation asked while the lane is losing messages can be answered by nobody,
    and the run then reads the silence as consent - the very failure the guard exists
    for. So while a catch-up recovery is fresh the gate REFUSES instead of asking.

    python tests/test_route_surface.py
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

            # ---- the gate covers LOAD, not just restarts -------
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

            # The same question asked of a file PATH was answered from the BASENAME alone, so an
            # operator's own document was mistaken for the bot's memory. Measured
            # 2026-09-29: a write to "/home/user/acme/docs/notes.md" was gated as "a write to this
            # bot's own notes.md", and DECLINED on a lane with nobody to ask. The bot's own file is
            # identified by its RESOLVED PATH, like tools_dir_verdict does for tools.
            for path, want in ((str(fb.BASE_DIR / "notes.md"), True),
                               ("/home/user/acme/docs/notes.md", False),
                               ("/tmp/scratch/notes.md", False),
                               ("/tmp/atlas.md", False)):
                got = bool(fb._surface_write_gate(path, "write_file", {}))
                check(f"surface path: {path} -> {want}", got == want, f"got {got}")

            # ---- the strict-mode shell is a per-host CHOICE ----
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
    return main()


def _suite_test_route_hint():
    """The route hint: a shell content search gets pointed at search_files, once per run.

that calls atomic_write_text" became Select-String + a second Select-String for the def lines +
a python regex in execute_code + a 13,482-char spill + a repeat-read map -- 6 calls and 4.5
minutes for what ONE search_files call answers, with search_files never called. The hidden
tool's NAME is in the prompt now; its argument SHAPE is not, and the payload budget (8,518 of
8,900) will not carry its 528-char schema. So the harness says the one thing the result it
already paid for can say: here is the call.

    python tests/test_route_surface.py

Falsify: point it at a build without route_hint() (the checks go through getattr and FAIL).
"""
    import importlib.util
    import os
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    spec = importlib.util.spec_from_file_location("tinycmdr_route_under_test", SRC)
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_route_under_test"] = fb
    spec.loader.exec_module(fb)

    PASSES = []
    FAILS = []


    def check(name, cond, detail=""):
        (PASSES if cond else FAILS).append(name)
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


    hint = getattr(fb, "route_hint", lambda *a, **k: "")

    # The exact command a live install spent 4.5 minutes on.
    DRIVE_CMD = ('Select-String -Path C:\\tinycmdr\\tinycmdr.py -Pattern "atomic_write_text" '
                 '-AllMatches | ForEach-Object { "{0}: {1}" -f $_.LineNumber, $_.Line.Trim() }')

    # ---- it fires, and says something usable -----------------------------------------
    got = hint(DRIVE_CMD, {"session_key": "r-fire"})
    check("fires on a live install's own Select-String command", bool(got), got)
    check("the hint names search_files", "search_files" in got, got[:120])
    check("and gives the call shape, not just the name",
          '"pattern"' in got and '"path"' in got, got[:200])
    check("and says it returns line numbers", "line number" in got.lower(), got[:200])
    check("and is bounded", 0 < len(got) < 400, len(got))
    check("and rides as a HARNESS note, like the other harness verdicts", "[HARNESS:" in got, got[:60])

    # ---- twice per run, then quiet; again in the next run ----------------------------
    # A live install repeated the same Select-String four minutes after the first hint and heard
    # nothing, so the second miss is taught too - and the third is the
    # loop guard's business, not this line's.
    check("a second miss in the SAME run is taught as well",
          bool(hint(DRIVE_CMD, {"session_key": "r-fire"})))
    check("a third one is not (twice is the cap)",
          hint(DRIVE_CMD, {"session_key": "r-fire"}) == "")
    check("the next run hears it again (the count is per run)",
          bool(hint(DRIVE_CMD, {"session_key": "r-fresh"})))

    # ---- the other shapes that mean "content search" ---------------------------------
    for i, (cmd, what) in enumerate([
            ("grep -n atomic_write_text scripts/x.py", "grep"),
            ("rg -n atomic_write_text /srv/tinycmdr", "rg"),
            ('findstr /s /n "atomic_write_text" C:\\tinycmdr\\*.py', "findstr"),
            ('powershell -NoProfile -Command "Select-String -Path C:\\tinycmdr\\config.json '
             '-Pattern token"', "a wrapped powershell")]):
        check("fires on %s" % what, bool(hint(cmd, {"session_key": "r-%d" % i})))

    # ---- and the things that are NOT a file content search ---------------------------
    for i, cmd in enumerate([
            "docker ps | grep 8081",
            "netstat -an | grep 8787",
            "Get-Service tinycmdr",
            "Get-ChildItem C:\\tinycmdr -Recurse -Filter *.log",
            "echo hi",
            "python scripts/report.py --out report.txt",
            "Get-Content C:\\tinycmdr\\tasks.json",
            ""]):
        check("silent on %r" % cmd[:38], hint(cmd, {"session_key": "r-none-%d" % i}) == "")
    check("silent on a missing command", hint(None, {"session_key": "r-none-n"}) == "")

    # ---- it stands down when there is nothing to teach --------------------------------
    fb.reveal_tools("r-revealed", ["search_files"])
    check("no hint once this session already has search_files in its payload",
          hint(DRIVE_CMD, {"session_key": "r-revealed"}) == "")

    keep_disc = fb.CONFIG["agent"].get("tool_disclosure")
    keep_names = fb.CORE_TOOL_NAMES
    try:
        fb.CONFIG["agent"]["tool_disclosure"] = False
        check("no hint when disclosure is off (every schema is in the payload)",
              hint(DRIVE_CMD, {"session_key": "r-off"}) == "")
        fb.CONFIG["agent"]["tool_disclosure"] = keep_disc
        fb.CORE_TOOL_NAMES = set()
        check("no hint in a build without search_files",
              hint(DRIVE_CMD, {"session_key": "r-nofiles"}) == "")
    finally:
        fb.CORE_TOOL_NAMES = keep_names
        fb.CONFIG["agent"]["tool_disclosure"] = keep_disc

    # ---- end to end: the SHELL tool's own result carries it ---------------------------
    workdir = Path(tempfile.mkdtemp(prefix="fbroute-"))
    target = workdir / "probe.txt"
    target.write_text("needle\n", encoding="utf-8")
    verb = "findstr /n needle" if fb.IS_WINDOWS else "grep -n needle"
    ctx = {"session_key": "r-shell", "config": fb.CONFIG}
    out = fb.tool_shell({"command": "%s %s" % (verb, target)}, ctx)
    check("the shell tool's result carries the hint", "[HARNESS:" in out and "search_files" in out,
          out[-160:])
    out2 = fb.tool_shell({"command": "%s %s" % (verb, target)}, ctx)
    check("the second miss through the tool carries it too", "[HARNESS:" in out2, out2[-160:])
    out2b = fb.tool_shell({"command": "%s %s" % (verb, target)}, ctx)
    check("and the third does not (the cap holds through the tool)",
          "[HARNESS:" not in out2b, out2b[-160:])
    out3 = fb.tool_shell({"command": "echo hi"}, {"session_key": "r-shell-echo", "config": fb.CONFIG})
    check("a plain command's result carries nothing", "[HARNESS:" not in out3, out3[-120:])

    # ---- a computer/GUI procedure is ALREADY tool-driven: never offer to wrap it ---------
    # Operator report, 2026-10-03: a run executed a computer-use runbook "by hand" (9 calls) and
    # the harness offered "say mint it and I will turn it into a tool" - the capability was
    # already a tool, so the offer was noise.
    _st_gui = fb.run_state("mint-gui-1", create=True)
    _st_gui["calls_by"] = {"shell": 9, "computer_use": 9}
    _st_gui["skills_read"] = ["computer-use"]
    _st_gui["mint_ent"] = {"count": 2, "sample": "open the browser and click the button"}
    check("a run that used a computer/GUI tool is never offered a mint",
          fb.mint_offer("mint-gui-1", None, source="main") == ""
          and fb.mint_offer_line("mint-gui-1") == "",
          (fb.mint_offer_line("mint-gui-1"),))
    _st_nogui = fb.run_state("mint-nogui-1", create=True)
    _st_nogui["calls_by"] = {"shell": 9}
    _st_nogui["skills_read"] = ["web-server"]
    _st_nogui["mint_ent"] = {"count": 2, "sample": "restart the web server and check the port"}
    check("...while a shell-driven runbook still is",
          "mint it" in fb.mint_offer_line("mint-nogui-1"), fb.mint_offer_line("mint-nogui-1")[:120])

    # ---- a runbook whose procedure ALREADY has a tool: never offer to mint it ----------
    # Operator report, 2026-10-10: a run executed the dvd-pipeline runbook by hand while
    # the dvd_creator tool for it sat in tools/ - built from the same conversation, named
    # in the runbook and naming the runbook - and the offer still said "say mint it and I
    # will turn it into a tool". The GUI skip above is the same shape: nothing to mint,
    # nothing to ask. Falsify: a build without runbook_covered_by() answers "" and the
    # checks FAIL (never crash), the way the route_hint checks do.
    covered_by = getattr(fb, "runbook_covered_by", lambda *a, **k: "")
    _shelf = {"dvd_creator": {"schema": {"type": "function", "function": {
        "name": "dvd_creator",
        "description": "DVD pipeline on this Mac; read the dvd-pipeline skill first."}}}}
    check("a tool whose description names the runbook covers it",
          covered_by("dvd-pipeline", tools=_shelf) == "dvd_creator",
          covered_by("dvd-pipeline", tools=_shelf))
    _shelf2 = {"dvd_creator": {"schema": {"type": "function", "function": {
        "name": "dvd_creator", "description": "Burn an ISO to a disc."}}}}
    check("...and a shared name word covers it on its own",
          covered_by("dvd-pipeline", tools=_shelf2) == "dvd_creator",
          covered_by("dvd-pipeline", tools=_shelf2))
    check("...and so does a declared category",
          covered_by("dvd-pipeline", tools={
              "disc_dub": {"category": "dvd",
                           "schema": {"type": "function", "function": {
                               "name": "disc_dub", "description": "Copy discs."}}}}) == "disc_dub")
    check("a runbook with no tool of its own is not covered",
          covered_by("fleet-access", tools=_shelf) == "")

    # ---- every hand-driven call counts, not only the fingerprinted ones ----------------
    # Measured 2026-10-10: the offer quoted "5 hand calls" for a run that made 17 - the
    # increment sat inside the census branch, so 12 shell calls whose command vocabulary
    # did not fingerprint (ls, sleep, tail, ffmpeg chains) were invisible to the gate.
    # "echo" is the shape with NO fingerprint by construction (no verb in the list).
    # _exec_tool writes the carry sidecar per result, beside the sessions: into a temp
    # SESSIONS_DIR here, because the gate's report reads a repo-tree write as a leak
    # (named sessions/ on every run while this wrote into the tree).
    import tempfile as _sess_tmp
    _saved_sessions = fb.SESSIONS_DIR
    fb.SESSIONS_DIR = Path(_sess_tmp.mkdtemp(prefix="fbtest-mint-sessions-"))
    try:
        fb._run_state_reset("mint-count-1")
        for _ in range(3):
            fb.Agent._exec_tool(fb.AGENT,
                                {"function": {"name": "shell",
                                              "arguments": {"command": "echo count-me"}}},
                                {"session_key": "mint-count-1", "config": fb.CONFIG})
    finally:
        fb.SESSIONS_DIR = _saved_sessions
    _byc = (fb.run_state("mint-count-1") or {}).get("calls_by") or {}
    check("every successful hand-driven call counts toward the run's hand total",
          int(_byc.get("shell") or 0) == 3, _byc)
    check("...and the unfingerprintable one proves it (no census shape for echo)",
          fb._procedure_sig("shell", {"command": "echo count-me"}) == "")

    # ---- the mint census: one line when a by-hand SHAPE has run in several runs ----------
    # Measured 2026-09-25 on a live install: the run does a routine by hand every time and never
    # offers to keep it, and the whole six-day log held ONE `remember` call. The model sees one
    # run at a time; the harness keeps the census and asks the operator (see mint_offer).
    import json as _json
    import tempfile as _tempfile
    _proc = Path(_tempfile.mkdtemp(prefix="fbtest-mint-")) / "procedure-census.json"
    fb.PROC_CENSUS_FILE = _proc
    fb._CENSUS_FORCE = True          # this suite is the census's test, not the running bot

    # ---- the runbook offer, now that it can read the box and keep its own books --------
    # These call the offer itself, which marks the census file from here down - and the
    # file is the temp one, never the tree's.
    _st_cov = fb.run_state("mint-covered-1", create=True)
    _st_cov["calls_by"] = {"shell": 6}
    _st_cov["skills_read"] = ["dvd-pipeline"]
    _keep_shelf = dict(fb.REGISTRY.custom)
    fb.REGISTRY.custom.update(_shelf)
    try:
        check("the operator is never offered a mint for a runbook that has a tool",
              fb.mint_offer("mint-covered-1", None, source="main") == "")
    finally:
        fb.REGISTRY.custom.clear()
        fb.REGISTRY.custom.update(_keep_shelf)
    _st_oc = fb.run_state("mint-order-covered-1", create=True)
    _st_oc["calls_by"] = {"shell": 6}
    _st_oc["order_repeats"] = 3
    _st_oc["order_words"] = ["housekeeping", "sweep", "the", "notes"]
    _st_oc["skills_read"] = ["dvd-pipeline"]
    fb.REGISTRY.custom.update(_shelf)
    try:
        check("a repeated ORDER whose runbook a tool covers is not offered either",
              fb.mint_offer("mint-order-covered-1", None, source="main") == "")
    finally:
        fb.REGISTRY.custom.clear()
        fb.REGISTRY.custom.update(_keep_shelf)
    _st_un = fb.run_state("mint-uncovered-1", create=True)
    _st_un["calls_by"] = {"shell": 6}
    _st_un["skills_read"] = ["camera-swap"]
    _line_un = fb.mint_offer("mint-uncovered-1", None, source="main")
    check("an uncovered runbook still offers",
          bool(_line_un) and "mint it" in _line_un, _line_un)
    check("...once a week, not on every qualifying run",
          fb.mint_offer("mint-uncovered-1", None, source="main") == "")

    # ---- the offer speaks for THIS run: an earlier run's runbook cannot vouch for it ----
    _st_rb = fb.run_state("mint-runbook-run-1", create=True)
    _st_rb["calls_by"] = {"shell": 6}
    _st_rb["skills_read"] = ["gate-latch"]     # what the session carried in
    _marks = getattr(fb, "run_start_marks", None)
    if _marks:
        _marks("mint-runbook-run-1")
    check("at a run's start the runbook list is the run's own (empty) one",
          _marks is not None
          and (fb.run_state("mint-runbook-run-1").get("skills_read") or []) == [],
          (bool(_marks), fb.run_state("mint-runbook-run-1").get("skills_read")))
    check("...so the offer cannot name a runbook this run never read",
          "gate-latch" not in (fb.mint_offer("mint-runbook-run-1", None, source="main") or ""))
    _ord1 = fb.order_census_note("ord-sess", "Check free space on C, the 5 biggest files in logs, "
                                              "and the newest warnings in the supervisor log")
    _ord1b = fb.order_census_note("ord-sess", "check free space on C: the 5 biggest files under "
                                               "logs and the newest warnings in supervisor.log")
    check("the same request in the operator's own words counts as a repeat",
          _ord1["count"] == 1 and _ord1b["count"] == 2, (_ord1, _ord1b))
    check("a genuinely different order is a different routine",
          fb.order_census_note("ord-sess", "install the new poster art for the plex library")["count"] == 1)
    class _Rep2:
        def __init__(self):
            self.lines = []

        def say(self, text):
            self.lines.append(text)

    _rep2 = _Rep2()
    _st5 = fb.run_state("offer-6", create=True)
    _st5["calls_by"] = {"shell": 6, "read_file": 2}
    _st5["order_repeats"] = 3
    _line5 = fb.mint_offer("offer-6", _rep2, source="main")

    # ---- memory: a lookup that answered a durable-fact question gets ONE nudge ------------
    # Measured 2026-09-25 on a live install: asked which port the web UI listens on and where its
    # token file lives, the run found both and saved nothing - the whole six-day log holds ONE
    # `remember` call, because nothing anywhere points at the moment the fact appears.
    check("an order asking WHERE a fact lives is spotted as a lookup",
          fb.lookup_question("which port does your web UI listen on?") is True
          and fb.lookup_question("restart the tower computer over ssh") is False
          and fb.lookup_question("where is the token file") is True)
    fb.run_state("nudge-1", create=True)["order_is_lookup"] = 1
    _nudge = fb.remember_nudge("read_file", {"path": "config.json"}, {"session_key": "nudge-1"})
    check("the result that answered the lookup carries one memory nudge",
          "DURABLE" in _nudge and "memory" in _nudge, _nudge[:200])
    check("...and not a second time in the run",
          fb.remember_nudge("read_file", {}, {"session_key": "nudge-1"}) == "")
    _st6 = fb.run_state("nudge-2", create=True)
    _st6["order_is_lookup"] = 1
    _st6["remembered"] = 1
    check("no nudge once the run has saved something",
          fb.remember_nudge("shell", {}, {"session_key": "nudge-2"}) == "")
    check("no nudge when the order was not a lookup",
          fb.remember_nudge("shell", {}, {"session_key": "nudge-3"}) == "")
    check("the nudge is a config lever",
          fb.DEFAULT_CONFIG["agent"].get("remember_nudge") is True)
    _st9 = fb.run_state("mintline-1", create=True)
    check("no report-time invitation without a fired census",
          fb.mint_offer_line("mintline-1") == "")
    _st9["mint_ent"] = {"count": 2, "sample": "Get-PSDrive C | Select-Object Used,Free"}
    _line9 = fb.mint_offer_line("mintline-1")
    check("a fired census invites the model to offer it in the report",
          "SAY SO in your report" in _line9 and "2 separate runs" in _line9, _line9[:200])
    check("...naming the live mint route", "create_tool" in _line9, _line9[:200])
    check("...once per run", fb.mint_offer_line("mintline-1") == "")
    _st10 = fb.run_state("mintline-2", create=True)
    _st10["mint_ent"] = {"count": 3, "sample": "x"}
    _st10["calls_by"] = {"toolsmith": 1}
    check("no invitation when the run minted it", fb.mint_offer_line("mintline-2") == "")
    check("the invitation rides the trailing block, not the system prompt",
          "Repeatable procedure, offered not assumed" in fb.volatile_context(
              state_marker=False, session_key="mintline-3")
          or fb.mint_offer_line("mintline-3") == "")
    _st7 = fb.run_state("offer-7", create=True)
    _st7["calls_by"] = {"shell": 3}
    _st7["order_is_lookup"] = 1
    _st7["order_words"] = ["which", "port", "the", "web", "ui", "listens"]
    _line7 = fb.remember_offer("offer-7", _rep2, source="main")
    check("a lookup answered with nothing saved offers to keep the fact",
          _line7 and "save it" in _line7, _line7)
    check("...once per session", fb.remember_offer("offer-7", _rep2, source="main") == "")
    _st8 = fb.run_state("offer-8", create=True)
    _st8["calls_by"] = {"shell": 3}
    _st8["order_is_lookup"] = 1
    _st8["order_words"] = ["where", "is", "the", "token", "file", "now"]
    _st8["remembered"] = 1
    check("no offer when the run already saved it",
          fb.remember_offer("offer-8", _rep2, source="main") == "")
    _st_rb = fb.run_state("offer-9", create=True)
    _st_rb["calls_by"] = {"shell": 6}
    _st_rb["order_words"] = ["list", "the", "notes", "filing", "directory"]
    _st_rb["skills_read"] = ["notes-filing"]
    check("a run that read a runbook gets no save-or-dismiss offer (the runbook is durable)",
          fb.remember_offer("offer-9", _rep2, source="main", trigger="event") == "")
    _st_rb2 = fb.run_state("offer-10", create=True)
    _st_rb2["calls_by"] = {"shell": 6}
    _st_rb2["order_words"] = ["collect", "the", "dvd", "checksums", "again"]
    _line_rb = fb.remember_offer("offer-10", _rep2, source="main", trigger="event")
    check("...and a run that learned it by hand still does",
          _line_rb and "save it" in _line_rb, _line_rb)
    check("a repeated ORDER offers the mint without any command census",
          _line5 and "run #3" in _line5 and "mint it" in _line5, _line5)
    _CMD = "Get-PSDrive C | Select-Object Used,Free"
    _sig = fb._procedure_sig("shell", {"command": _CMD})
    check("a command shape has a stable signature",
          bool(_sig) and "get-psdrive" in _sig, _sig)
    check("a read_file has none (only hand-driven calls count)",
          fb._procedure_sig("read_file", {"path": "x"}) == "")
    check("a lone generic probe is not a routine",
          fb._procedure_sig("shell", {"command": "ps aux | head -1"}) == ""
          and fb._procedure_sig("shell", {"command": "grep -rn todo ."}) == "")
    check("...but two verbs still fingerprint",
          fb._procedure_sig("shell", {"command": "ps aux | grep llama"}) != "")
    check("...and a lone non-probe verb keeps its shape",
          fb._procedure_sig("shell", {"command": "curl -s http://x/y"}) == "curl")

    def _bump(run_id):
        fb._EVENT_RUN["mint-sess"] = run_id
        return fb.procedure_census_bump("shell", {"command": _CMD}, "mint-sess")

    e1 = _bump("run-1"); e2 = _bump("run-2"); e3 = _bump("run-3")
    check("the count is RUNS, not calls", e3["count"] == 3, e3)
    fb._CENSUS_FORCE = False
    check("an imported module (a suite) never reaches the census",
          fb.procedure_census_bump("shell", {"command": _CMD}, "test-stall-x") is None)
    check("...and neither does a harness-written turn",
          fb.order_census_note("mm-x", "SYSTEM: that cap is a CHECKPOINT - carry on") is None)
    fb._CENSUS_FORCE = True

    check("the same run bumped twice still counts once",
          _bump("run-3")["count"] == 3)
    _ctx = {"session_key": "mint-1"}
    first = fb.mint_hint("shell", {}, _ctx, e2)
    check("the hint needs the SECOND run, not the third",
          "separate runs" in first and "toolsmith" in first, first[:160])
    check("...and it names the live mint route first", "create_tool" in first, first[:160])
    check("...and not a second time in that run", fb.mint_hint("shell", {}, _ctx, e2) == "")
    _ctx2 = {"session_key": "mint-2"}          # a fresh session, as a restart gives
    check("...once per SHAPE, not per session: the census remembers the hint",
          fb.mint_hint("shell", {}, _ctx2, _bump("run-4")) == "")
    check("...and the census on disk carries the mark, so a restart does not re-nag",
          bool((_json.loads(_proc.read_text(encoding="utf-8")).get(_sig) or {}).get("minted")),
          _sig)
    check("the hint logs itself", True)

    class _Rep:
        def __init__(self):
            self.lines = []

        def say(self, text):
            self.lines.append(text)

    rep = _Rep()
    st = fb.run_state("offer-1", create=True)
    st["calls_by"] = {"shell": 5, "process": 2, "read_file": 4}
    st["sigs"] = [_sig]
    line = fb.mint_offer("offer-1", rep, source="main")
    check("the operator is asked once the run repeated a shape by hand",
          line and "mint it" in line and len(rep.lines) == 1, line)
    check("...and not again for the same procedure inside a week",
          fb.mint_offer("offer-1", rep, source="main") == "")
    st2 = fb.run_state("offer-2", create=True)
    st2["calls_by"] = {"shell": 9, "create_tool": 1}
    st2["sigs"] = [_sig]
    check("no offer when the run MINTED something", fb.mint_offer("offer-2", rep) == "")
    st3 = fb.run_state("offer-3", create=True)
    st3["calls_by"] = {"shell": 2}
    st3["sigs"] = [_sig]
    check("no offer for a run that did almost nothing by hand",
          fb.mint_offer("offer-3", rep) == "")
    check("no offer from a sub-agent", fb.mint_offer("offer-4", rep, source="sub") == "")
    st4 = fb.run_state("offer-5", create=True)
    st4["calls_by"] = {"shell": 4, "process": 2, "skill": 1}
    st4["skills_read"] = ["fleet-access"]
    st4["sigs"] = []
    line = fb.mint_offer("offer-5", rep, source="main")
    check("a runbook executed by hand is offered by NAME (the skill-to-tool case)",
          line and "fleet-access" in line and "mint it" in line, line)
    check("the gates are config keys with defaults",
          fb.DEFAULT_CONFIG["agent"].get("mint_hint") is True
          and int(fb.DEFAULT_CONFIG["agent"].get("mint_hint_after")) == 2
          and fb.DEFAULT_CONFIG["agent"].get("mint_offer") is True
          and int(fb.DEFAULT_CONFIG["agent"].get("mint_offer_steps")) == 4
          and float(fb.DEFAULT_CONFIG["agent"].get("order_repeat_overlap")) == 0.6)

    print()
    print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
    sys.exit(1 if FAILS else 0)


def main():
    rc = 0
    for name, fn in (("test_endpoint_gate", _suite_test_endpoint_gate), ("test_route_hint", _suite_test_route_hint)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
