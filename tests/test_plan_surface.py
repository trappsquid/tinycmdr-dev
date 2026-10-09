"""test_plan_surface - one merged suite (test_plan, test_plan_and_context).

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


def _suite_test_plan():
    """Offline checks for the harness-held plan and the runway.

Two kinds of check here. The unit ones poke plan_render/run_block/volatile_context
directly. The last two drive a real turn against a stubbed model, because "the harness
re-sends the plan with the position" and "the drift nudge fires" are claims about what
ends up in the REQUEST BODY, and only an end-to-end run can prove those.

    python tests/test_plan_surface.py
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
            # The loop stops at max_turns as well as max_steps, so the runway must name both:
            # llm.max_turns=100 next to agent.max_steps=250 meant "about N left" promised a step
            # budget the turn cap could cut off two thirds early.
            fb.run_state("s1")["turn"] = 3
            blk = fb.run_block("s1")
            check("turn 3 of" in blk and "whichever cap is reached first" in blk,
                  f"the runway names the turn cap too, not only the step cap ({blk!r})")
            check("left before the harness forces" not in blk,
                  "and stops promising the whole step budget when the turn cap may bind first")

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

            # A-2026-10-08-155: the parse used to be anchored (.{10,200}$), so a listed step
            # longer than 200 chars vanished from the plan and from the wrap-up check.
            _dl = fb.derive_plan_from_text("Do these in order:\n1. short step here\n"
                                           "2. %s\n3. another short step" % ("x" * 250))
            check(len(_dl) == 3,
                  f"a listed step longer than 200 chars stays in the plan ({len(_dl)})")
            # A-2026-10-08-157: the derived path caps at 200 and the model's set did not,
            # while the plan (and the current-step header) rides every later payload.
            fb.tool_plan({"action": "set", "steps": "short one\n" + ("y" * 260)}, ctx)
            _pl = fb.run_state("s1")["plan"]
            check(all(len(s["text"]) <= 200 for s in _pl),
                  "model-set steps are capped at 200 chars (the plan rides every payload)")

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
    return main()


def _suite_test_plan_and_context():
    """Plan mode is a hard read-only gate; AGENTS.md travels with the checkout.

Plan mode is enforced in code, not prose: every
write/exec tool refuses at the dispatch point until the operator approves a plan, and a
drop-in is covered because unknown tools are tier exec. `/plan apply` (or answering the
approval question) is the approval.

Context files: AGENTS.md/CLAUDE.md from the run's cwd up to the project root are read at
session start into the cached static prefix - one per depth, nearest most prominent, a
farther file contained in a nearer one dropped, the block bounded.

    python tests/test_plan_surface.py
"""
    import json
    import os
    import shutil
    import sys
    import tempfile
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


    def named(block, path):
        """True when `block` names `path` the way context_files_block actually renders it.

    The block writes `<file path=%r>`, and `repr()` DOUBLES every backslash in the string - so
    `str(p) in block` is false for any Windows path while it passes on POSIX, where a path
    carries no backslash. That is how this suite's first check failed on windows-latest through
    every release while passing here, and the cut's own record said one check failed (measured
    2026-10-08 on the v1.0.88 release's product CI: 1 failing check before that release, 2
    after, because a new check I added compared `str(p)` too). Compare the rendering, not the
    path.
    """
        return repr(str(Path(path).resolve())) in block


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbplan-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            A = fb.AGENT
            ctx = {"session_key": "plan-test"}

            # ---------------------------------------------------------- plan mode
            check(fb.plan_mode("p0") == "execute", "the default mode is execute")
            fb.CONFIG["agent"]["plan_requires_approval"] = True
            check(fb.plan_mode("p1") == "plan",
                  "plan_requires_approval starts a session in plan mode")
            fb.CONFIG["agent"]["plan_requires_approval"] = False

            fb.plan_mode_set("plan-test", "plan")
            check(fb.plan_mode("plan-test") == "plan", "the mode can be set")
            probe = workdir / "SHOULD-NOT-EXIST.txt"
            name, args, out = A._exec_tool(
                {"function": {"name": "shell",
                              "arguments": json.dumps({"command": "touch %s" % probe})}},
                dict(ctx))
            check(out.startswith("REFUSED") and "plan mode" in out and not probe.exists(),
                  "a shell call is refused and never runs", out[:90])
            name, args, out = A._exec_tool(
                {"function": {"name": "write_file",
                              "arguments": json.dumps({"path": str(probe), "content": "x"})}},
                dict(ctx))
            check(out.startswith("REFUSED") and not probe.exists(),
                  "a write is refused", out[:90])
            name, args, out = A._exec_tool(
                {"function": {"name": "read_file",
                              "arguments": json.dumps({"path": str(workdir / "config.json")})}},
                dict(ctx))
            check(not out.startswith("REFUSED"), "a read still runs", out[:60])
            name, args, out = A._exec_tool(
                {"function": {"name": "plan",
                              "arguments": json.dumps({"action": "set",
                                                       "steps": ["look", "then write"]})}},
                dict(ctx))
            check("plan mode is ON" in out and "/plan apply" in out,
                  "recording a plan says how it is approved", out[-160:])

            # the plan tool is the ONLY mutating door, and it is the gate's own door
            name, args, out = A._exec_tool(
                {"function": {"name": "shell", "arguments": json.dumps({"command": "echo hi"})}},
                dict(ctx))
            check(out.startswith("REFUSED"), "...and the gate is still shut", out[:60])

            # an approval question in-band flips it
            fb.plan_mode_set("plan-door", "plan")
            name, args, out = A._exec_tool(
                {"function": {"name": "plan",
                              "arguments": json.dumps({"action": "set", "steps": ["a"]})}},
                {"session_key": "plan-door", "confirm_cb": lambda s: True})
            check(fb.plan_mode("plan-door") == "execute" and "APPROVED" in out,
                  "a yes at the door leaves plan mode", out[-120:])

            # /plan apply is the operator's door
            fb.plan_mode_set("plan-test", "execute")
            check(fb.plan_mode("plan-test") == "execute", "/plan apply resumes execution")

            # ---------------------------------------------------------- context files
            tree = Path(tempfile.mkdtemp(prefix="fbctx-"))
            (tree / ".git").mkdir()
            (tree / "AGENTS.md").write_text("root rule: run the gate\n", encoding="utf-8")
            (tree / "CLAUDE.md").write_text("root CLAUDE (shadowed)\n", encoding="utf-8")
            sub = tree / "pkg" / "api"
            sub.mkdir(parents=True)
            (sub / "AGENTS.md").write_text("package rule: name the file\n", encoding="utf-8")
            deep = sub / "deep"
            deep.mkdir()
            (deep / "AGENTS.md").write_text("package rule: name the file\n", encoding="utf-8")
            rows = fb._context_files(deep)
            paths = [str(p) for p, _ in rows]
            # pkg/api/AGENTS.md is byte-identical to deep/AGENTS.md: the FARTHER one is
            # contained in the nearer, so it is dropped and only root + deep survive.
            check(paths == [str((tree / "AGENTS.md").resolve()),
                            str((deep / "AGENTS.md").resolve())],
                  "one file per depth, AGENTS.md wins there, a contained farther file "
                  "dropped", paths)
            check("root CLAUDE" not in "\n".join(t for _, t in rows),
                  "a shadowed CLAUDE.md is not read", rows)

            block = fb.context_files_block(deep)
            check(named(block, deep / "AGENTS.md") and "package rule" in block
                  and "LOCAL conventions" in block,
                  "the block names the paths and labels them", block[-300:])
            # The class the check above is an instance of, graded on a path whose rendering differs
            # from `str()`: a backslash in a name is legal on POSIX and reproduces exactly the split
            # Windows creates for every path, so this fails on ANY platform if the comparison slips
            # back to `str(p)`.
            if os.name == "posix":
                # A backslash is legal in a POSIX name and reproduces the split on this host. On
                # Windows it is a SEPARATOR, so the fixture cannot exist there - and it does not
                # need to: every Windows path has the split already, which is what the check in
                # the else-branch asserts (this suite runs in the dev Windows tier now, so both
                # halves have to be true wherever they run).
                odd_tree = Path(tempfile.mkdtemp(prefix="fbctx-odd-"))
                (odd_tree / ".git").mkdir()
                (odd_tree / "a\\b").mkdir()
                (odd_tree / "a\\b" / "AGENTS.md").write_text("odd rule: name the file\n",
                                                            encoding="utf-8")
                odd = (odd_tree / "a\\b" / "AGENTS.md").resolve()
                odd_block = fb.context_files_block(odd_tree / "a\\b")
                check(str(odd) != repr(str(odd)),
                      "the fixture reproduces the rendering split (a backslash in the path)",
                      (str(odd), repr(str(odd))))
                check(named(odd_block, odd) and "odd rule" in odd_block,
                      "...and the block names that path too", odd_block[-200:])
                shutil.rmtree(odd_tree, ignore_errors=True)
            else:
                _win = Path("C:/somewhere/AGENTS.md")
                check(str(_win) != repr(str(_win)),
                      "on Windows EVERY path has the str/repr split the named() comparison "
                      "exists for", (str(_win), repr(str(_win))))

            fb.CONFIG["agent"]["context_files_max_chars"] = 20
            block = fb.context_files_block(deep)
            check(len(block) < 900 and "package rule" not in block and "CUT at 0 of" in block,
                  "the block honours the char budget and SAYS it cut", block[-300:])
            fb.CONFIG["agent"]["context_files_max_chars"] = 4000

            # ---- a cut says so, and the cut lands on a line boundary (run 21, A-61) ---------
            # The block used to truncate in silence at the byte: the model read half a rulebook,
            # would not go and look for the rest, and nothing in the log said a rule was missing.
            cut_tree = Path(tempfile.mkdtemp(prefix="fbctx-cut-"))
            (cut_tree / ".git").mkdir()
            (cut_tree / "AGENTS.md").write_text("line one is a rule\n" * 40
                                               + "the very last rule\n", encoding="utf-8")
            fb.CONFIG["agent"]["context_files_max_chars"] = 120
            cut_block = fb.context_files_block(cut_tree)
            check("CUT at" in cut_block and "AGENTS.md" in cut_block
                  and "of %d chars" % len((cut_tree / "AGENTS.md")
                                          .read_text(encoding="utf-8").strip()) in cut_block,
                  "a cut file is marked in the prompt, by name and counts",
                  cut_block[-260:])
            check(cut_block.count("line one is a rule") == 6
                  and "the very last rule" not in cut_block
                  and "line one is a rul\n" not in cut_block,
                  "the visible half ends on a line boundary, not mid-rule",
                  cut_block[:400])
            # A budget the first file spends to the character leaves the next one unreached: it
            # must be named, not dropped (the old code `break`ed in silence).
            fb.CONFIG["agent"]["context_files_max_chars"] = len(
                (cut_tree / "AGENTS.md").read_text(encoding="utf-8").strip())
            (cut_tree / "pkg").mkdir()
            (cut_tree / "pkg" / "AGENTS.md").write_text("near rule: keep this\n", encoding="utf-8")
            starved = fb.context_files_block(cut_tree / "pkg")
            check("NOT READ" in starved and named(starved, cut_tree / "pkg" / "AGENTS.md"),
                  "a file the budget never reached is named as NOT READ", starved[-320:])
            fb.CONFIG["agent"]["context_files_max_chars"] = 4000
            shutil.rmtree(cut_tree, ignore_errors=True)

            fb.CONFIG["agent"]["context_files"] = False
            check(fb.context_files_block(deep) == "", "context_files=false disables it")
            fb.CONFIG["agent"]["context_files"] = True

            # the prompt carries it (static prefix), and disabling strips it
            was_cwd = Path.cwd()
            import os as _os
            _os.chdir(tree)
            try:
                prompt = fb.build_system_prompt()
                check("root rule: run the gate" in prompt,
                      "the static prompt carries the repo's rules")
                fb.CONFIG["agent"]["context_files"] = False
                check("root rule: run the gate" not in fb.build_system_prompt(),
                      "...and dropping the switch removes them")
            finally:
                fb.CONFIG["agent"]["context_files"] = True
                _os.chdir(was_cwd)
            shutil.rmtree(tree, ignore_errors=True)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print("all plan/context checks passed")
        return 0
    return main()


def main():
    rc = 0
    for name, fn in (("test_plan", _suite_test_plan), ("test_plan_and_context", _suite_test_plan_and_context)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
