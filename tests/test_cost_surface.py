"""test_cost_surface - one merged suite (test_tokens, test_cost_guard).

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


def _suite_test_tokens():
    """Token estimation: the shape of it, pinned offline, with an opt-in live check.

est_tokens() sizes every compaction and force-shrink decision, so an estimator that
is 2-3x low moves the failure to the moment the context is fullest (review,
2026-09-22). There is no tokenizer in this repo and no network call in a suite, so
the checks below are properties a real tokenizer agrees with - prose near 4
chars/token, source/JSON denser, CJK much denser - measured on the two samples that
actually recur in an ops run: this build's own source (the model re-reads it more
than any other file) and a JSON tool payload.

A live comparison against a real tokenizer runs ONLY when TINYCMDR_TEST_TOKENIZE_URL
is set (a llama.cpp box answers POST /tokenize). Do not point it at a box that is
busy: it is a model server. Left unset, it prints a skip line and the offline
checks are the gate.

    python tests/test_cost_surface.py
    TINYCMDR_TEST_TOKENIZE_URL=http://<lan-box>:8081/tokenize python tests/test_cost_surface.py
"""
    import json
    import os
    import shutil
    import sys
    import tempfile
    import urllib.request
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    TESTS = BASE / "tests"
    sys.path.insert(0, str(TESTS))

    import run_scenario  # noqa: E402

    FAILS = []

    PROSE = ("The supervisor waits for the lock to be held and for the chat door to "
             "answer before it counts a start as ready, because a child that cannot be "
             "reached is not a child that is running. ") * 8

    CODE = ("def _endpoint_window(self):\n"
            "    now = time.time()\n"
            "    if not hasattr(self, '_window_cache'):\n"
            "        self._window_cache = _detect_window(CONFIG['llm']['base_url'])\n"
            "    return self._window_cache\n") * 8

    TOOL_JSON = json.dumps(
        [{"role": "tool", "tool_call_id": "call_abc123", "content": "ERROR: no such file"},
         {"role": "user", "content": "check the disk"},
         {"index": 0, "ok": True, "elapsed_ms": 41}] * 12)

    CJK = ("この設定ファイルは、エージェントが読むすべての値を定義します。"
           "既定値は控えめに設定されており、必要に応じて変更できます。") * 8


    def check(cond, what):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}")
        else:
            print(f"ok   {what}")


    def live_count(url, text):
        req = urllib.request.Request(
            url, data=json.dumps({"content": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        toks = data.get("tokens")
        return len(toks) if isinstance(toks, list) else int(toks)


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbtokens-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            est = fb.est_tokens

            check(est("") == 1, "an empty string never estimates zero")
            check(est("x") >= 1, "a single character never estimates zero")
            check(est(PROSE * 2) >= est(PROSE), "the estimate grows with the text")

            prose_ratio = len(PROSE) / est(PROSE)
            check(3.5 <= prose_ratio <= 4.6,
                  f"prose stays near 4 chars/token ({prose_ratio:.2f})")
            check(est(CODE) > len(CODE) // 4 * 1.2,
                  "code estimates DENSER than len//4 (the bug this exists for)")
            check(est(TOOL_JSON) > len(TOOL_JSON) // 4 * 1.2,
                  "a JSON tool payload estimates denser than len//4")
            check(est(CJK) > est(PROSE[:len(CJK)]) * 2,
                  "CJK costs at least twice the tokens of the same length of prose")

            # The real recurring sample: this build's own source. 54% of the model's
            # read_file calls were re-reads of tinycmdr.py (2026-09-17 measurement), so
            # this is the text whose estimate the budget is most often computed from.
            src = BASE / "tinycmdr.py"
            if src.exists():
                text = src.read_text(encoding="utf-8", errors="replace")[:200000]
                ratio = len(text) / est(text)
                check(ratio <= 3.5,
                      f"a 200 KB source sample estimates at {ratio:.2f} chars/token "
                      f"(was 4.00 flat)")
            else:
                print("skip the source sample (tinycmdr.py is not beside the suite)")

            # both guards that size the cut must read the same number
            msgs = [{"role": "user", "content": CODE},
                    {"role": "assistant", "content": None,
                     "tool_calls": [{"id": "a", "type": "function",
                                     "function": {"name": "shell",
                                                  "arguments": '{"command": "ls"}'}}]}]
            total = fb.AGENT._messages_token_est(msgs)
            check(total >= est(CODE) + est('{"command": "ls"}'),
                  "the payload estimate counts tool-call arguments too")

            # ---- live, opt-in -------------------------------------------------
            url = (os.environ.get("TINYCMDR_TEST_TOKENIZE_URL") or "").strip()
            if not url:
                print("skip live tokenizer comparison (set TINYCMDR_TEST_TOKENIZE_URL "
                      "to a llama.cpp /tokenize to run it)")
            else:
                for label, text in (("prose", PROSE), ("code", CODE),
                                    ("json", TOOL_JSON), ("cjk", CJK)):
                    real = live_count(url, text)
                    mine = est(text)
                    check(real * 0.75 <= mine <= real * 1.25,
                          f"{label}: estimate {mine} within +/-25% of the tokenizer's "
                          f"{real} ({mine / real:.2f}x)")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) failed")
            sys.exit(1)
        print("all token-estimate checks passed")
    return main()


def _suite_test_cost_guard():
    """Offline checks for the cost ceiling on expensive commands (plan item 6).

Two halves, and the second is the one that was measured to matter:

  * a per-call ceiling on a broad-root scan (search_timeout), and
  * a per-RUN budget on the time spent scanning, across the shell AND execute_code
    (scan_budget_seconds).

No model calls and no slow commands: the classifier and the limits are pure functions
of the command text, the budget is exercised with real but tiny durations, and the
messages are checked through tool_shell/tool_execute_code with run_capture stubbed.

    python tests/test_cost_surface.py
"""
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


    # The command that cost 608 of a 1193-second graded leg on 2026-09-17, and friends.
    RISKY = [
        r'''Get-ChildItem -Path "C:/Users/<user>" -Recurse -Include *.csv -Force''',
        r'''Get-ChildItem -Path C:\ -Recurse -Filter *.log''',
        r'''dir C:\ /s /b''',
        r'''find / -name '*.csv' ''',
        r'''find /home -type f -name '*.log' ''',
        r'''grep -R foo /usr''',
        # A BUNDLED short flag is how the command is actually written, and it was invisible to the
        # shape regex (A-2026-10-07-79).
        r'''grep -rn TODO /''',
        r'''grep -nr TODO /''',
        r'''grep -Rn TODO /''',
        "findstr /s TODO C:\\",
        # A glob is as broad as its fixed prefix, and this one is all of /var.
        r'''grep -r ERROR /var/**/*.log''',
        r'''Get-ChildItem "$HOME" -Recurse''',
        # Profile-shaped by structure, whatever the user is called.
        r'''Select-String -Path C:/Users/<user> -Pattern todo -Recurse''',
        # Recursive by default - no flag to gate on.
        r'''tree C:\Users\<user>''',
        r'''du -sh /var''',
        r'''rg TODO /''',
        r'''ls -R /''',
    ]

    # Ordinary work: narrow roots, non-recursive reads, long jobs that are not walks.
    PLAIN = [
        r'''Get-ChildItem -Path C:\tinycmdr\logs -Recurse''',
        r'''Get-ChildItem -Path "C:/Users/<user>\tinycmdr" -Recurse -Include *.log''',
        r'''find ~/tinycmdr -name '*.py' ''',
        r'''grep -r ERROR /var/log/mattermost''',
        r'''Get-Content C:\tinycmdr\tinycmdr.log -Tail 50''',
        r'''apt-get install -y docker-ce''',
        r'''docker compose -f F:\Docker\x\docker-compose.yml build''',
        r'''git clone https://github.com/x/y.git D:/tmp/y''',
        r'''ls -la C:\tinycmdr''',
        r'''Get-ChildItem -Recurse''',
        # One level under the user tree is a PROJECT, whichever platform spells the profile
        # (/Users/<name>/<project> was judged the whole profile while /home/<name>/<project> was
        # not: A-2026-10-07-80).
        r'''grep -r ERROR /Users/x/proj''',
        # A named subdirectory is not a whole tree, so this stays unbounded on purpose.
        r'''grep -r ERROR /var/log''',
        # Recursive by default at a NARROW root: no flag, no budget.
        r'''rg TODO src''',
        r'''du -sh ./logs''',
        r'''ls -R C:\tinycmdr\logs''',
    ]

    CODE_RISKY = [
        'import os\nfor dp, dn, fn in os.walk(r"C:/Users/<user>"):\n    pass\n',
        'from pathlib import Path\nlist(Path("/home").rglob("*.csv"))\n',
        'import glob\nprint(glob.glob("/var/**/*.log", recursive=True))\n',
        'import os\nfor e in os.scandir("C:\\\\"):\n    print(e)\n',
    ]
    CODE_PLAIN = [
        'import os\nfor dp, dn, fn in os.walk(r"C:/Users/<user>\\tinycmdr"):\n    pass\n',
        'print(open(r"C:\\tinycmdr\\tinycmdr.log").read()[:100])\n',
        'import json\nprint(json.load(open("config.json")))\n',
        'from pathlib import Path\nprint(Path("/home/dave/project").rglob("*.py"))\n',
    ]


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbcost-"))
        real_capture = None
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            real_capture = fb.run_capture
            fb.CONFIG["agent"]["command_cost_guard"] = True
            fb.CONFIG["agent"]["search_timeout"] = 60
            fb.CONFIG["agent"]["scan_budget_seconds"] = 120
            fb.reset_scan_spend("s")

            # ---- the classifier ---------------------------------------------------
            for cmd in RISKY:
                risk = fb.command_cost_risk(cmd)
                check(bool(risk), f"an unbounded walk is recognised -> {cmd[:58]}")
                if risk:
                    check(risk.get("shape") and risk.get("root"),
                          f"  it names the shape and the root ({risk['shape']}, {risk['root']!r})")
            for cmd in PLAIN:
                check(fb.command_cost_risk(cmd) is None,
                      f"ordinary work is left alone -> {cmd[:58]}")
            for code in CODE_RISKY:
                check(bool(fb.code_cost_risk(code)),
                      f"a walk in Python is recognised -> {code.splitlines()[0][:46]}")
            for code in CODE_PLAIN:
                check(fb.code_cost_risk(code) is None,
                      f"ordinary code is left alone -> {code.splitlines()[0][:46]}")

            # ---- a MENTION is not a walk (review 2026-09-29) -----------------------
            # The shape regexes read the whole command, so a quoted argument DESCRIBING a walk was
            # billed against the run's scan budget - and once the budget was spent, the harmless
            # command was refused outright. A quote is not a command, and a comment cannot walk
            # anything, so neither should be able to spend this budget.
            for cmd in ('echo "find / -name x"',
                        "printf '%s\\n' 'grep -r error /var'",
                        'grep -n "os.walk" notes.md'):
                check(fb.command_cost_risk(cmd) is None,
                      f"a quoted mention is not a walk -> {cmd[:52]}")
            for cmd in ('find / -name x', 'bash -c "find / -name x"', 'eval "find / -name x"',
                        "sh -c 'grep -r error /var'"):
                check(bool(fb.command_cost_risk(cmd)),
                      f"  but a walk that RUNS still counts -> {cmd[:52]}")
            for code in ('# os.walk("/") is exactly what we must not do',
                         '# rglob("/") was the slow path\nprint(1)'):
                check(fb.code_cost_risk(code) is None,
                      f"a comment cannot walk -> {code.splitlines()[0][:46]}")
            check(bool(fb.code_cost_risk('os.walk("/")')),
                  "  while real code that walks is still a walk")
            check(bool(fb.code_cost_risk('exec("os.walk(\'/\')")')),
                  "  and a walk assembled in a STRING is still seen")

            # ---- a quoted path with a SPACE keeps its whole root ------
            # The root scan used to read the quote-STRIPPED command, and the bare-path
            # alternative stops at whitespace - so every quoted walk under a profile like
            # "C:\Users\Example User" was judged as rooted at C:\Users\David and billed,
            # and eventually refused, as a whole-tree sweep.
            narrow_spaced = r'''Get-ChildItem -Recurse "C:\Users\Example User\tinycmdr"'''
            check(fb.command_cost_risk(narrow_spaced) is None,
                  "a quoted path three levels into a spaced profile is narrow")
            profile = r'''Get-ChildItem -Recurse "C:\Users\Example User"'''
            risk = fb.command_cost_risk(profile)
            check(bool(risk) and risk["root"] == r"C:\Users\Example User",
                  f"a quoted PROFILE root is still broad, and named in full ({risk})")
            check(fb.code_cost_risk('os.walk("C:/Users/Example User/tinycmdr")') is None,
                  "a quoted spaced path in Python keeps its root too")

            # ---- the per-call ceiling --------------------------------------------
            ctx = {"session_key": "s"}
            risk = fb.command_cost_risk(RISKY[0])
            t, allowed, msg = fb.scan_limits(ctx, 380, risk)
            check(t == 60 and allowed, f"a broad walk asked for 380s is capped to {t}s")
            t, allowed, _ = fb.scan_limits(ctx, 30, risk)
            check(t == 30 and allowed, f"a requested timeout below the cap is kept ({t}s)")
            install = next(c for c in PLAIN if c.startswith("apt-get"))
            t, allowed, _ = fb.scan_limits(ctx, 380, fb.command_cost_risk(install))
            check(t == 380 and allowed, f"an install keeps its timeout ({t}s)")
            narrow = next(c for c in PLAIN if "tinycmdr\\logs" in c)
            t, allowed, _ = fb.scan_limits(ctx, 380, fb.command_cost_risk(narrow))
            check(t == 380 and allowed, f"a narrow recursive walk keeps its timeout ({t}s)")

            # ---- the run budget --------------------------------------------------
            check(fb.scan_spend(ctx) == 0.0, "a fresh run has spent nothing")
            fb.charge_scan(ctx, 30.0)
            t, allowed, _ = fb.scan_limits(ctx, 380, risk)
            check(t == 60 and allowed, f"30s spent leaves the cap at 60s, not 90 ({t}s)")
            fb.charge_scan(ctx, 60.0)
            t, allowed, msg = fb.scan_limits(ctx, 380, risk)
            check(t == 30 and allowed,
                  f"90s of a 120s budget leaves 30s for the next walk ({t}s)")
            fb.charge_scan(ctx, 40.0)
            t, allowed, msg = fb.scan_limits(ctx, 380, risk)
            check(not allowed and "REFUSED" in msg, "an exhausted budget refuses the walk")
            check("scan_budget_seconds" in msg and "search_files" in msg and "atlas.md" in msg,
                  f"the refusal names the budget and the way out -> {msg[:70]}")
            t, allowed, _ = fb.scan_limits(ctx, 380, fb.command_cost_risk(install))
            check(t == 380 and allowed, "the budget never blocks a non-scan command")
            fb.reset_scan_spend("s")
            check(fb.scan_spend(ctx) == 0.0, "a new run starts with the budget whole")

            # ---- the budget really counts wall clock -----------------------------
            fb.CONFIG["agent"]["scan_budget_seconds"] = 0.5
            try:
                fb.run_capture = lambda argv, timeout, cancel=None: (time.sleep(0.3), 0, "x", "", False)[1:]
                out1 = fb.tool_shell({"command": RISKY[0]}, ctx)
                out2 = fb.tool_shell({"command": RISKY[0]}, ctx)
                out3 = fb.tool_shell({"command": RISKY[0]}, ctx)
            finally:
                fb.run_capture = real_capture
            check(not out1.startswith("REFUSED"), "the first scan runs")
            check(not out2.startswith("REFUSED"), "a scan inside the budget still runs")
            check(out3.startswith("REFUSED"),
                  f"once the budget is spent the next scan is refused -> {out3[:30]}")
            check(fb.scan_spend(ctx) >= 0.6, f"the spend is real ({fb.scan_spend(ctx):.2f}s)")
            out3 = fb.tool_shell({"command": "Get-Content C:\\tinycmdr\\tinycmdr.log -Tail 5"}, ctx)
            check(not out3.startswith("REFUSED"),
                  "a targeted read still runs once the budget is spent")
            fb.reset_scan_spend("s")
            fb.CONFIG["agent"]["scan_budget_seconds"] = 0.5
            try:
                fb.run_capture = lambda argv, timeout, cancel=None: (time.sleep(0.3), 0, "x", "", False)[1:]
                out4 = fb.tool_execute_code({"code": CODE_RISKY[0]}, ctx)
                out5 = fb.tool_execute_code({"code": CODE_RISKY[0]}, ctx)
                out6 = fb.tool_execute_code({"code": CODE_RISKY[0]}, ctx)
            finally:
                fb.run_capture = real_capture
            check(out4.startswith("exit_code="), f"a first os.walk is allowed -> {out4[:22]}")
            check(not out5.startswith("REFUSED"), "a second os.walk inside the budget runs")
            check(out6.startswith("REFUSED"),
                  f"the shell and the code share one budget -> {out6[:34]}")
            fb.reset_scan_spend("s")
            fb.CONFIG["agent"]["scan_budget_seconds"] = 120

            # ---- a hand-off to the background table still pays its walk ----------
            # The auto-background return skipped the charge, so the SLOWEST
            # walks - the ones that ran past the window - cost the run's budget nothing.
            fb.reset_scan_spend("s")
            saved_autobg = fb._shell_autobg
            saved_autobg_s = fb.CONFIG["agent"].get("auto_background_seconds")
            try:
                fb._shell_autobg = lambda command, ctx, threshold, timeout: (
                    time.sleep(0.3) or "[HARNESS: handed to the background table]")
                fb.CONFIG["agent"]["auto_background_seconds"] = 1
                out = fb.tool_shell({"command": RISKY[0]}, ctx)
            finally:
                fb._shell_autobg = saved_autobg
                fb.CONFIG["agent"]["auto_background_seconds"] = saved_autobg_s
            check("background table" in out,
                  f"a backgrounded walk returns the hand-off notice -> {out[:40]}")
            check(fb.scan_spend(ctx) >= 0.25,
                  f"a backgrounded walk still pays for what it walked "
                  f"({fb.scan_spend(ctx):.2f}s)")
            fb.reset_scan_spend("s")

            # ---- what the model is told when the per-call ceiling fires ----------
            try:
                fb.run_capture = lambda argv, timeout, cancel=None: (0, "partial listing", "", True)
                out = fb.tool_shell({"command": RISKY[0]}, ctx)
            finally:
                fb.run_capture = real_capture
            check(out.startswith("TIMEOUT after 60s"), f"the capped call says so -> {out[:40]}")
            for hint in ("TIMEOUT after", "search_timeout", "Cheaper", "search_files", "Depth 2"):
                check(hint in out, f"  the verdict tells the model about {hint!r}")
            check("recursive directory walk" in out, "  it names the shape")
            check("partial listing" in out, "  the partial output is still handed over")

            # A command that is NOT this shape keeps the original timeout message.
            try:
                fb.run_capture = lambda argv, timeout, cancel=None: (0, "x", "", True)
                out2 = fb.tool_shell({"command": "C:\\Python312\\python.exe -m pip list",
                                      "timeout": 15}, ctx)
            finally:
                fb.run_capture = real_capture
            check(out2.startswith("TIMEOUT after 15s"),
                  f"an ordinary timeout is unchanged -> {out2[:34]}")
            check("search_timeout" not in out2, "  and it does not mention the ceiling")

            # ---- off switches ----------------------------------------------------
            fb.CONFIG["agent"]["command_cost_guard"] = False
            t, allowed, _ = fb.scan_limits(ctx, 380, fb.command_cost_risk(RISKY[0]))
            check(t == 380 and allowed, "the guard's off switch leaves the request alone")
            fb.CONFIG["agent"]["command_cost_guard"] = True
            fb.CONFIG["agent"]["search_timeout"] = 0
            t, allowed, _ = fb.scan_limits(ctx, 380, fb.command_cost_risk(RISKY[0]))
            fb.CONFIG["agent"]["scan_budget_seconds"] = 0
            t, allowed, _ = fb.scan_limits(ctx, 380, fb.command_cost_risk(RISKY[0]))
            check(t == 380 and allowed, "both ceilings off leaves the request alone")
            fb.CONFIG["agent"]["search_timeout"] = 60
            t, allowed, _ = fb.scan_limits(ctx, 380, fb.command_cost_risk(RISKY[0]))
            check(t == 60 and allowed, "scan_budget_seconds=0 leaves only the per-call ceiling")
            fb.CONFIG["agent"]["search_timeout"] = 0
            fb.CONFIG["agent"]["scan_budget_seconds"] = 120
            fb.reset_scan_spend("s")      # earlier checks spent real sub-second time here
            t, allowed, _ = fb.scan_limits(ctx, 380, fb.command_cost_risk(RISKY[0]))
            # The budget is the ceiling, and it is measured in real seconds: a sub-second residue
            # from an earlier check (or a different platform's timing) must not fail the suite, so
            # assert the ceiling holds with a one-second tolerance rather than an exact integer.
            # Caught by the clean-unpack run on Linux before this version was published.
            check(119 <= t <= 120 and allowed,
                  "search_timeout=0 leaves the run budget as the only ceiling ({0}s)".format(t))
            fb.CONFIG["agent"]["search_timeout"] = 60
            fb.CONFIG["agent"]["scan_budget_seconds"] = 120
        finally:
            if real_capture is not None:
                fb.run_capture = real_capture
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) failed")
            sys.exit(1)
        print("all cost-ceiling checks passed")
    return main()


def main():
    rc = 0
    for name, fn in (("test_tokens", _suite_test_tokens), ("test_cost_guard", _suite_test_cost_guard)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
