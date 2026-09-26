"""The route hint: a shell content search gets pointed at search_files, once per run.

Measured on the operator drive's first work order (the Windows bed, 2026-09-23): "find every line
that calls atomic_write_text" became Select-String + a second Select-String for the def lines +
a python regex in execute_code + a 13,482-char spill + a repeat-read map -- 6 calls and 4.5
minutes for what ONE search_files call answers, with search_files never called. The hidden
tool's NAME is in the prompt now; its argument SHAPE is not, and the payload budget (8,518 of
8,900) will not carry its 528-char schema. So the harness says the one thing the result it
already paid for can say: here is the call.

    python tests/test_route_hint.py

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

# ---- twice per run, then quiet; again in the next run ----------------------------
# The drive repeated the same Select-String four minutes after the first hint and heard
# nothing (measured 2026-09-23), so the second miss is taught too - and the third is the
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

# ---- the mint census: one line when a by-hand SHAPE has run in several runs ----------
# Measured 2026-09-25 driving the manager box: the run does a routine by hand every time and never
# offers to keep it, and the whole six-day log held ONE `remember` call. The model sees one
# run at a time; the harness keeps the census and asks the operator (see mint_offer).
import json as _json
import tempfile as _tempfile
_proc = Path(_tempfile.mkdtemp(prefix="fbtest-mint-")) / "procedure-census.json"
fb.PROC_CENSUS_FILE = _proc
fb._CENSUS_FORCE = True          # this suite is the census's test, not the running bot
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
# Measured 2026-09-25 driving the manager box: asked which port the web UI listens on and where its
# token file lives, the run found both and saved nothing - the whole six-day log holds ONE
# `remember` call, because nothing anywhere points at the moment the fact appears.
check("an order asking WHERE a fact lives is spotted as a lookup",
      fb.lookup_question("which port does your web UI listen on?") is True
      and fb.lookup_question("restart the tower computer over ssh") is False
      and fb.lookup_question("where is the token file") is True)
fb.run_state("nudge-1", create=True)["order_is_lookup"] = 1
_nudge = fb.remember_nudge("read_file", {"path": "config.json"}, {"session_key": "nudge-1"})
check("the result that answered the lookup carries one memory nudge",
      "DURABLE" in _nudge and "remember" in _nudge, _nudge[:200])
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
_st7["calls_by"] = {"shell": 3, "read_file": 1}
_st7["order_is_lookup"] = 1
_line7 = fb.mint_offer("offer-7", _rep2, source="main")
check("a lookup answered with nothing saved offers to keep the fact",
      _line7 and "save it" in _line7, _line7)
check("...once per session", fb.mint_offer("offer-7", _rep2) == "")
_st8 = fb.run_state("offer-8", create=True)
_st8["calls_by"] = {"shell": 3}
_st8["order_is_lookup"] = 1
_st8["remembered"] = 1
check("no offer when the run already saved it", fb.mint_offer("offer-8", _rep2) == "")
check("a repeated ORDER offers the mint without any command census",
      _line5 and "run #3" in _line5 and "mint it" in _line5, _line5)
_CMD = "Get-PSDrive C | Select-Object Used,Free"
_sig = fb._procedure_sig("shell", {"command": _CMD})
check("a command shape has a stable signature",
      bool(_sig) and "get-psdrive" in _sig, _sig)
check("a read_file has none (only hand-driven calls count)",
      fb._procedure_sig("read_file", {"path": "x"}) == "")

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
check("the hint needs the SECOND run, not the third",
      fb.mint_hint("shell", {}, _ctx, e2) != "")
_ctx2 = {"session_key": "mint-2"}
first = fb.mint_hint("shell", {}, _ctx2, e3)
check("the hint fires once the shape has run in 3 runs",
      "separate runs" in first and "toolsmith" in first, first[:160])
check("...and only once per run", fb.mint_hint("shell", {}, _ctx, e3) == "")
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
