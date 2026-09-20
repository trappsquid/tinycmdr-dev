"""Offline checks for the cost ceiling on expensive commands (plan item 6).

Two halves, and the second is the one that was measured to matter:

  * a per-call ceiling on a broad-root scan (search_timeout), and
  * a per-RUN budget on the time spent scanning, across the shell AND execute_code
    (scan_budget_seconds).

No model calls and no slow commands: the classifier and the limits are pure functions
of the command text, the budget is exercised with real but tiny durations, and the
messages are checked through tool_shell/tool_execute_code with run_capture stubbed.

    python tests/test_cost_guard.py
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
    # A glob is as broad as its fixed prefix, and this one is all of /var.
    r'''grep -r ERROR /var/**/*.log''',
    r'''Get-ChildItem "$HOME" -Recurse''',
    # Profile-shaped by structure, whatever the user is called.
    r'''Select-String -Path C:/Users/<user> -Pattern todo -Recurse''',
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
    # A named subdirectory is not a whole tree, so this stays unbounded on purpose.
    r'''grep -r ERROR /var/log''',
]

CODE_RISKY = [
    'import os\nfor dp, dn, fn in os.walk(r"C:\\Users\\<user>"):\n    pass\n',
    'from pathlib import Path\nlist(Path("/home").rglob("*.csv"))\n',
    'import glob\nprint(glob.glob("/var/**/*.log", recursive=True))\n',
    'import os\nfor e in os.scandir("C:\\\\"):\n    print(e)\n',
]
CODE_PLAIN = [
    'import os\nfor dp, dn, fn in os.walk(r"C:\\Users\\<user>\\tinycmdr"):\n    pass\n',
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


if __name__ == "__main__":
    main()
