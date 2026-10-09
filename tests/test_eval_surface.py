"""test_eval_surface - one merged suite (test_small_model, test_eval_grading).

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


def _suite_test_small_model():
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

    python tests/test_eval_surface.py
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
    return main()


def _suite_test_eval_grading():
    """Offline checks for the eval scoreboard: the grader must not pass or fail by accident.

No model calls. Two directions per task:

  - a fresh staged install with an empty answer must FAIL (no free passes), and
  - hand-built "correct" end states for a sample of tasks must PASS, which is what
    proves the check specs are writable at all (a task whose spec can never be
    satisfied would silently cap the scoreboard below 100%).

    python tests/test_eval_surface.py
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

    import eval_tasks  # noqa: E402
    import run_eval  # noqa: E402

    FAILS = []


    def check(cond, what):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}")
        else:
            print(f"ok   {what}")


    def staged(task, budget=24000):
        workdir = Path(tempfile.mkdtemp(prefix=f"fbgrade-{task['id']}-"))
        run_eval.stage_task(workdir, budget, task)
        return workdir


    def metrics_for(workdir, answer, **kw):
        m = {"task": kw.get("tid", "?"), "category": kw.get("cat", "?"),
             "difficulty": "x", "label": "grading", "tool_calls": 0,
             "tool_errors": [], "_workdir": str(workdir)}
        m.update(kw)
        m["answer"] = answer
        return m


    def main():
        # 1. every task must fail on a fresh stage with no answer
        for task in eval_tasks.TASKS:
            wd = staged(task)
            try:
                passed, why = run_eval.grade(task, metrics_for(wd, "", tid=task["id"]))
                check(not passed, f"{task['id']} fails on an empty run")
            finally:
                shutil.rmtree(wd, ignore_errors=True)

        # 2. hand-built correct states must pass
        positive = []

        # T01: the file exists with 50 lines
        t = eval_tasks.BY_ID["T01_write_count"]
        wd = staged(t)
        (wd / "probe.txt").write_text("\n".join(str(i) for i in range(1, 51)) + "\n",
                                      encoding="utf-8")
        positive.append((t, metrics_for(wd, "The file has 50 lines.")))

        # T02: valid JSON with the right port
        t = eval_tasks.BY_ID["T02_fix_config"]
        wd2 = staged(t)
        (wd2 / "app-settings.json").write_text(
            json.dumps({"service": {"name": "backupd", "port": 8082, "retries": 3}}),
            encoding="utf-8")
        positive.append((t, metrics_for(wd2, "Set service.port to 8082.")))

        # T04: right answer, zero tool calls
        positive.append((eval_tasks.BY_ID["T04_no_tools_needed"],
                         metrics_for(BASE, "391")))

        # T05: no-op admitted, file untouched
        t = eval_tasks.BY_ID["T05_already_correct"]
        wd3 = staged(t)
        positive.append((t, metrics_for(wd3, "log_level was already DEBUG, so no change "
                                             "was needed.")))

        # T07: merged file exact, answer reports 5 lines and epsilon
        t = eval_tasks.BY_ID["T07_six_steps"]
        wd4 = staged(t)
        (wd4 / "build" / "eval").mkdir(parents=True)
        (wd4 / "build" / "eval" / "merged.txt").write_text(
            "alpha\nbeta\ngamma\ndelta\nepsilon\n", encoding="utf-8")
        positive.append((t, metrics_for(wd4, "merged.txt has 5 lines, last line epsilon")))

        # T08 / T11 / T10: answer-only checks
        positive.append((eval_tasks.BY_ID["T08_fixture_triage"],
                         metrics_for(BASE, "cache-svc is unhealthy, publishing port 6379")))
        positive.append((eval_tasks.BY_ID["T11_precision"],
                         metrics_for(BASE, "8443")))
        positive.append((eval_tasks.BY_ID["T10_unknown_tool"],
                         metrics_for(BASE, "There is no such tool as docker_manager in "
                                           "this install.")))
        # T12: the run must land on the budget status and still report
        positive.append((eval_tasks.BY_ID["T12_budget_landing"],
                         metrics_for(BASE, "Partial work. VERIFIED: nothing",
                                     status="budget")))
        # T13 / T14: the scored artefacts of tool-result digestion and field notes
        positive.append((eval_tasks.BY_ID["T13_buried_error"],
                         metrics_for(BASE, "vaultsync failed: checksum mismatch",
                                     digests_fired=1)))
        positive.append((eval_tasks.BY_ID["T14_field_note"],
                         metrics_for(BASE, "The command was not recognized: no such "
                                           "program on this box.",
                                     field_notes_fired=1)))
        # T15 / T16: the scored artefacts of post-write verification
        t = eval_tasks.BY_ID["T15_verify_ok"]
        wd7 = staged(t)
        (wd7 / "config.json").write_text(
            json.dumps({"service": {"name": "backupd", "port": 8082}}), encoding="utf-8")
        positive.append((t, metrics_for(wd7, "The port is 8082.", verifies_fired=1)))
        positive.append((eval_tasks.BY_ID["T16_verify_failure"],
                         metrics_for(BASE, "The write failed: the file is invalid JSON.",
                                     verify_failures=1)))
        # T09: both defaults, which are the values in tinycmdr.py
        positive.append((eval_tasks.BY_ID["T09_code_grounding"],
                         metrics_for(BASE, "memory_index_max_chars is 3000 and "
                                           "memory_concept_max_chars is 6000.")))
        # T03 / T06
        positive.append((eval_tasks.BY_ID["T03_log_cause"],
                         metrics_for(BASE, "backupd is failing: disk quota exceeded")))
        positive.append((eval_tasks.BY_ID["T06_wrong_path"],
                         metrics_for(BASE, "The third column sums to 137.")))

        for task, m in positive:
            passed, why = run_eval.grade(task, m)
            check(passed, f"{task['id']} passes on a correct state "
                          f"({[w for w in why if w.startswith('FAIL')] or 'all rules ok'})")

        # 3. wrong answers must still fail, not just empty ones
        t = eval_tasks.BY_ID["T03_log_cause"]
        passed, _ = run_eval.grade(t, metrics_for(BASE, "indexer is failing"))
        check(not passed, "T03 fails on the wrong service")
        t = eval_tasks.BY_ID["T05_already_correct"]
        passed, _ = run_eval.grade(t, metrics_for(BASE, "I changed the log level to DEBUG"))
        check(not passed, "T05 fails when the model claims a change it did not make")
        t = eval_tasks.BY_ID["T11_precision"]
        passed, _ = run_eval.grade(t, metrics_for(BASE, "The port is 8081"))
        check(not passed, "T11 fails on the wrong port")
        # T10 must not be satisfied by building the tool instead of checking
        t = eval_tasks.BY_ID["T10_unknown_tool"]
        passed, _ = run_eval.grade(t, metrics_for(
            BASE, "docker_manager did not exist, so I created it", create_tool_calls=1))
        check(not passed, "T10 fails when the model builds the missing tool")
        # T14 must not pass when the note never fired, even if the answer is right
        t = eval_tasks.BY_ID["T14_field_note"]
        passed, _ = run_eval.grade(t, metrics_for(
            BASE, "zztool was not recognized", field_notes_fired=0))
        check(not passed, "T14 fails when the field note did not fire")
        # T16 must not pass when the harness never reported the broken write
        t = eval_tasks.BY_ID["T16_verify_failure"]
        passed, _ = run_eval.grade(t, metrics_for(
            BASE, "Wrote broken.json, all good.", verify_failures=0))
        check(not passed, "T16 fails when no verify failure was reported")

        # 4. per-task config overrides land in the staged config
        t = eval_tasks.BY_ID["T12_budget_landing"]
        wd5 = staged(t)
        try:
            cfg = json.loads((wd5 / "config.json").read_text(encoding="utf-8"))
            check(cfg["agent"]["max_steps"] == 8,
                  f"T12 max_steps override is staged (got {cfg['agent']['max_steps']})")
            check(cfg["agent"]["max_minutes"] == 10,
                  "T12 max_minutes override is staged")
        finally:
            shutil.rmtree(wd5, ignore_errors=True)
        t = eval_tasks.BY_ID["T01_write_count"]
        wd6 = staged(t)
        try:
            cfg = json.loads((wd6 / "config.json").read_text(encoding="utf-8"))
            check(cfg["agent"]["max_steps"] == 40,
                  "defaults apply when a task has no override")
            check((wd6 / "tinycmdr.py").exists(), "the staged install has tinycmdr.py")
        finally:
            shutil.rmtree(wd6, ignore_errors=True)

        # 5. task set sanity
        ids = [t["id"] for t in eval_tasks.TASKS]
        check(len(ids) == len(set(ids)), "task ids are unique")
        check(len(ids) >= 12, f"task set has {len(ids)} tasks")
        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) failed")
            sys.exit(1)
        print(f"all grading checks passed ({len(ids)} tasks)")
    return main()


def main():
    rc = 0
    for name, fn in (("test_small_model", _suite_test_small_model), ("test_eval_grading", _suite_test_eval_grading)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
