"""The route hint: a shell content search gets pointed at search_files, once per run.

Measured on the operator drive's first work order (the Windows test box, 2026-09-23): "find every line
that calls atomic_write_text" became Select-String + a second Select-String for the def lines +
a python regex in execute_code + a 13,482-char spill + a repeat-read map -- 6 calls and 4.5
minutes for what ONE search_files call answers, with search_files never called. The hidden
tool's NAME is in the prompt now; its argument SHAPE is not, and the payload budget (8,518 of
8,900) will not carry its 528-char schema. So the harness says the one thing the result it
already paid for can say: here is the call.

    python tests/test_route_hint.py
    TINYCMDR_SRC=tinycmdr-cli.py python tests/test_route_hint.py

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

# The exact command the drive spent 4.5 minutes on.
DRIVE_CMD = ('Select-String -Path C:\\tinycmdr\\tinycmdr.py -Pattern "atomic_write_text" '
             '-AllMatches | ForEach-Object { "{0}: {1}" -f $_.LineNumber, $_.Line.Trim() }')

# ---- it fires, and says something usable -----------------------------------------
got = hint(DRIVE_CMD, {"session_key": "r-fire"})
check("fires on the drive's own Select-String command", bool(got), got)
check("the hint names search_files", "search_files" in got, got[:120])
check("and gives the call shape, not just the name",
      '"pattern"' in got and '"path"' in got, got[:200])
check("and says it returns line numbers", "line number" in got.lower(), got[:200])
check("and is bounded", 0 < len(got) < 400, len(got))
check("and rides as a HARNESS note, like the other harness verdicts", "[HARNESS:" in got, got[:60])

# ---- once per run, again in the next one -----------------------------------------
check("a second call in the SAME run is not lectured again",
      hint(DRIVE_CMD, {"session_key": "r-fire"}) == "")
check("the next run hears it again (the flag is per run)",
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
check("and the tool does not repeat it in the same run", "[HARNESS:" not in out2, out2[-160:])
out3 = fb.tool_shell({"command": "echo hi"}, {"session_key": "r-shell-echo", "config": fb.CONFIG})
check("a plain command's result carries nothing", "[HARNESS:" not in out3, out3[-120:])

print()
print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
sys.exit(1 if FAILS else 0)
