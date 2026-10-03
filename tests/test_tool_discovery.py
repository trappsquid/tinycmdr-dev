"""Tool discovery: a capability question must not be answered with a wrong tool.

Measured on a fleet box 2026-09-23 (the Windows bed, 35B-A3B, its own log): three find_tools
calls, each answered "[HARNESS: now callable]" with a tool that does something else -

    "send Mattermost message to channel"            -> `schedule`  (the word "channel")
    "send Mattermost post message channel thread"   -> `blog`      (the word "post")
    "send mattermost message to agent channel via API" -> `delegate_task` ("agent")

- and then 35 minutes of rebuilding by hand a capability the box did not have. The word
overlap score is the defect: it fired on the words that appear in half the registry, so a
MISS now returns this box's whole remaining surface instead, and a query that names no
capability is told so instead of being guessed at.

    python tests/test_tool_discovery.py
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")


def shipped_tool_sources():
    """The tools/ files that are part of THIS repository, by absolute path - or None.

    tools/ is per-host by design: .gitignore carries everything but the starter files, and
    what an operator drops in is the operator's (the toolsmith grades those). The shelf rule
    below is a promise about the surface tinycmdr SHIPS, so it must not go red because a box
    added a tool - measured 2026-09-29 on the fleet Mac: two host tools with no category,
    130 passed / 1 failed, while the same tree archived to a clean checkout was 129/129.

    Asking git, rather than keeping a second list of what ships, is what keeps the answer
    from going stale. If git cannot answer, None exempts nothing and the rule stays as strict
    as it was.
    """
    try:
        out = subprocess.run(["git", "-C", str(BASE), "ls-files", "-z", "--", "tools"],
                             capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return {(BASE / rel).resolve() for rel in out.stdout.split("\0") if rel}


# A `python` reachable BY NAME for the checks that run one through the shell.
# The shell tool runs its command in the real shell, so `python -c "print(123)"` needs an
# interpreter called `python` on PATH. run_all.py invokes this suite as
# `<venv>/bin/python tests/...`, which does NOT put that bin on the child's PATH: measured
# 2026-09-26, the one-liner answered exit_code=127 and this suite went 125 passed / 2
# failed purely from how it was started. The interpreter running this file always exists,
# so make it reachable by that name.
os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
if shutil.which("python") is None:
    _shim_dir = Path(tempfile.mkdtemp(prefix="fbshell-python-"))
    _shim = _shim_dir / ("python.exe" if os.name == "nt" else "python")
    try:
        os.symlink(sys.executable, _shim)
    except (OSError, NotImplementedError):
        shutil.copy2(sys.executable, _shim)      # a copy is a poor alias, but it runs
    os.environ["PATH"] = str(_shim_dir) + os.pathsep + os.environ["PATH"]

spec = importlib.util.spec_from_file_location("tinycmdr_discovery_under_test", SRC)
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_discovery_under_test"] = fb
spec.loader.exec_module(fb)

PASSES = []
FAILS = []


def check(name, cond, detail=""):
    (PASSES if cond else FAILS).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


def asked(session, **args):
    return fb.tool_find_tools(args, {"session_key": session})


revealed = lambda out: "now callable" in out or "every remaining tool is now" in out
TAIL = "Everything else on this machine"

# ---- the three measured queries are the regression this suite exists for ---------
# What was measured (a fleet Windows bed, 2026-09-23): these three revealed a WRONG tool -
# `schedule` (the word "channel"), `blog` ("post"), `delegate_task` ("agent") - and the box then
# spent 35 minutes rebuilding a capability it already had. The REGRESSION is the wrong answer,
# not the existence of an answer: on a box that owns a mattermost tool (the fleet manager's own
# tree grew `tools/mattermost_ops.py` at 18:34 on 2026-09-25) revealing it for "send Mattermost
# message to channel" is correct, and a suite that called that a failure would be red on the very
# box the tool was written for.
WRONG = ("`schedule`", "`blog`", "`delegate_task`")
for i, q in enumerate(["send Mattermost message to channel",
                       "send Mattermost post message channel thread",
                       "send mattermost message to agent channel via API"]):
    out = asked("regress%d" % i, query=q)
    check("never the old wrong tool for %r" % q[:38],
          not any(w in out for w in WRONG), out[:160])
    if revealed(out):
        check("  ...a reveal names a tool that MATCHES the ask %r" % q[:30],
              "mattermost" in out.lower(), out[:200])
    else:
        check("  ...a miss says so for %r" % q[:38], "No tool matched" in out, out[:120])
        check("  ...and nothing was put in the payload",
              fb.hidden_tools("regress%d" % i) == fb.hidden_tools(None))
_m = fb._match_tools("send Mattermost message to channel", 4, None)
check("the score itself never returns one of the old wrong tools",
      not [t for t in _m if t in ("schedule", "blog", "delegate_task")], _m)

# ---- a query that names a capability still reveals the right tool ----------------
CAPS = [("keep noisy log digging out of my own context", "delegate_task"),
        ("subagent", "delegate_task"),
        ("schedule a job every morning", "schedule"),
        ("grep files by regex content", "search_files"),
        ("what did we do in a past session", "search_sessions"),
        ("fuzzy anchor edit a file", "patch"),
        ("restart the tinycmdr gateway", "tinycmdr_restart")]
hidden0 = set(fb.hidden_tools(None))
ran = 0
for q, want in CAPS:
    if want not in hidden0:
        continue                      # cut from this build, or already always-visible
    ran += 1
    out = asked("cap%d" % ran, query=q)
    check("%r finds %s" % (q[:40], want), want in out and revealed(out), out[:140])
check("the capability table ran against this build", ran >= 3, ran)

# ---- a query with no discriminating word is told so, not guessed at --------------
gen = "send a message to the channel"
check("generic words are not scored on",
      fb.discriminating_words(gen) == [], fb.discriminating_words(gen))
out = asked("s-gen", query=gen)
check("a capability-free query says so", "names no capability" in out, out[:140])
check("and it reveals nothing", not revealed(out))
check("and it still names what exists", TAIL in out, out[:140])

# ---- every discovery answer carries the remaining surface ------------------------
out = asked("s-miss", query="zzzznothing")
check("a miss keeps the contract the older tests pin",
      "No tool matched" in out and "all=true" in out, out[:120])
check("a miss names the surface, not a bare name list", TAIL in out, out[:160])
out = asked("s-hit", query="subagent")
check("a hit names the surface too", TAIL in out, out[:160])
out = asked("s-empty")
check("an empty query is the 'show me everything' call",
      "Tools this box has that your list does not" in out and "call" in out, out[:140])
check("an empty query reveals nothing", not revealed(out))
check("the surface names runbooks for procedures", "runbooks" in out, out[:160])
check("the surface is bounded", len(out) < 2500, len(out))
check("a miss points at the runbooks as well", "runbooks" in asked("s-miss2", query="zzzznothing"))
check("nothing is left hidden for a miss", fb.hidden_tools("s-miss") == fb.hidden_tools(None))

# ---- the surface line is a bounded summary, not the whole registry ---------------
tail = fb._surface_tail(None)
check("the tail names hidden tools with what they do", " (" in tail and ":" not in tail[:2],
      tail[:120])
check("the tail is bounded", len(tail) < 1200, len(tail))
many = fb._surface_tail(None, limit=2)
if len(hidden0) > 2:
    check("and it says how many it left out", "more, all=true" in many, many[-80:])
check("no hidden tools, no tail", fb._surface_tail("done", exclude=sorted(hidden0)) == "")

# ---- list_tools stays BOUNDED (pinned in test_stall too). Not one bare line any more:
# under disclosure the payload carries part of the core set, and the old wording told the
# model it already held all of them - measured 2026-09-24 on three boxes, where the run
# that read it never reached for create_tool and scaffolded the file through the shell.
# The answer now names the count it really has and the tools it does not; it is spent
# only when the model asks, so the budget is a few hundred characters, not 220.
out = fb.tool_list_tools({}, {})
check("list_tools stays bounded", len(out) < 2500, len(out))
check("and it forwards to discovery", "find_tools" in out, out)

# ---- an unknown tool name teaches the surface, absent stays absent --------------
_, _, out = fb.Agent._exec_tool(fb.AGENT, {"function": {"name": "delegate_tasksk",
                                                        "arguments": {}}},
                                {"session_key": "s-unk"})
check("an unknown name gets the closest matches", "unknown tool" in out and "find_tools" in out,
      out[:120])
check("and the whole remaining surface", TAIL in out, out[:160])
_, _, out = fb.Agent._exec_tool(fb.AGENT, {"function": {"name": "browser_navigate",
                                                       "arguments": {}}},
                                {"session_key": "s-absent"})
check("a tool this box never had still reads as absent",
      "exists on this box" in out and "find_tools" not in out, out[:140])

# ---- F2: the hidden tools are NAMED in the static prompt (first operator drive, ---------
# 2026-09-23). The model would not spend the discovery call: asked which tool edits by a
# fuzzy anchor it answered edit_file and named 3 of the 7 hidden ones, and across three
# runs it called find_tools ONCE. The names now ride the static prompt, generated from the
# build so a new hidden tool cannot fall out of it.
INV_MARK = "not in your tool list \u2014 call one by name and it stays for the session: "


def inv_names(prompt):
    """The names the inventory line carries, parsed the way a reader would."""
    rows = [l for l in prompt.splitlines() if INV_MARK in l]
    if len(rows) != 1:
        return None
    after = rows[0].split(INV_MARK, 1)[1].split(" Those are core tools")[0]
    return [w.strip() for w in after.replace(".", "").split(",") if w.strip()]


sp = fb.build_system_prompt()
# A pre-fix build has no such helper: the gate must FAIL, not crash with an AttributeError.
inv_line = getattr(fb, "hidden_inventory_line", lambda: "")()
named = inv_names(sp)
want_inv = [n for n in fb.hidden_tools(None) if n in fb.CORE_TOOL_NAMES]
check("the static prompt carries ONE hidden-tool inventory line", named is not None, sp[-600:])
check("it names every hidden core tool this build has", named == want_inv, (named, want_inv))
named = named or []
check("and invents none", bool(named) and all(n in fb.CORE_TOOL_NAMES for n in named), named)
check("it is generated, not typed: the set is what hidden_tools() reports",
      named == sorted(set(named)) and inv_line.count(", ".join(named)) == 1, inv_line[:160])
# The CONTRACT is "the inventory line is spliced directly after the preceding bullet, with
# no blank line between them" - a blank field of its own would render it as a separate
# paragraph. It used to be pinned as a byte sequence ending in the text of the bullet that
# happened to precede it ("named in its prompt).\n- Also on this box"), which went red the
# moment that bullet's trailing prose was trimmed (2026-09-27) - a change to the prompt,
# not to the layout. Pinned structurally instead: exactly one inventory line, and the line
# above it is the tail of a bullet.
_lines = sp.splitlines()
_inv_rows = [i for i, l in enumerate(_lines) if l.startswith("- Also on this box")]
check("the inventory line directly follows the tool bullet (no blank field left behind)",
      len(_inv_rows) == 1 and _inv_rows[0] > 0
      and _lines[_inv_rows[0] - 1].startswith("- ")
      and _lines[_inv_rows[0] - 1].strip() != "",
      _inv_rows)
check("the line is bounded", len(inv_line) < 320, len(inv_line))
check("the prompt is still byte-identical across two builds",
      fb.build_system_prompt() == sp)
check("the placeholder is a live field, not literal braces", "{inventory}" not in sp)
check("the line says these are CORE tools (the drive mislabeled them custom)",
      "Those are core tools" in fb.hidden_inventory_line(), fb.hidden_inventory_line()[:200])
check("and points at the custom block for the rest",
      "custom tools listed at the end" in fb.hidden_inventory_line())
# The skill block is generated from the runbooks INSTALLED on the box, and ./skills is
# gitignored (they are the operator's own procedures), so a clean clone has none and the
# sentence above was never in the prompt: measured 2026-09-26, this check was the suite's
# only real failure. Own the input instead of the host's disk, the way the parked-skill
# block below does: point SKILLS_DIR at a temp dir, once with a runbook in it and once with
# nothing, so both halves of the contract are graded.
skdir = Path(tempfile.mkdtemp(prefix="fbskills-"))
(skdir / "demo-runbook").mkdir()
(skdir / "demo-runbook" / "SKILL.md").write_text(
    "---\nname: demo-runbook\ndescription: a fixture runbook\n---\nsteps\n",
    encoding="utf-8", newline="\n")
(skdir / "empty").mkdir()
_keep_skills_dir = fb.SKILLS_DIR
try:
    fb.SKILLS_DIR = skdir / "demo-runbook"
    sp_skilled = fb.build_system_prompt()
    fb.SKILLS_DIR = skdir / "empty"
    sp_bare = fb.build_system_prompt()
finally:
    fb.SKILLS_DIR = _keep_skills_dir
    shutil.rmtree(skdir, ignore_errors=True)
check("prompt: the runbook block carries the rule this inventory depends on",
      "a skill is a runbook, not a tool" in sp_skilled)
check("...and names the runbook that is installed",
      "demo-runbook" in sp_skilled)
check("...and is absent when no runbook is installed (no dangling header)",
      "a skill is a runbook, not a tool" not in sp_bare
      and "Prose skills installed" not in sp_bare)

check("prompt: the routing bullet names the shell verbs it replaces",
      "Select-String" in sp and "findstr" in sp and "search_files {pattern, path}" in sp)
check("prompt: the routing bullet carries search_files' own call shape",
      "{pattern, path}" in sp)

# A tool made hidden by CONFIG must appear: that is the drift the hand-written list had.
_keep_core = fb.CONFIG["agent"].get("core_tools")
_keep_disc = fb.CONFIG["agent"].get("tool_disclosure")
try:
    fb.CONFIG["agent"]["core_tools"] = [n for n in fb.core_tool_names() if n != "shell"]
    narrow = getattr(fb, "hidden_inventory_line", lambda: "")()
    check("a tool hidden by config shows up in the inventory", "shell" in narrow, narrow)
    check("...and the line is regenerated, not padded",
          inv_names(fb.build_system_prompt()) ==
          [n for n in fb.hidden_tools(None) if n in fb.CORE_TOOL_NAMES])
    fb.CONFIG["agent"]["core_tools"] = sorted(fb.CORE_TOOL_NAMES)
    none_line = getattr(fb, "hidden_inventory_line", lambda: "x")()
    check("nothing hidden, no dangling line", none_line == "", none_line)
    fb.CONFIG["agent"]["tool_disclosure"] = False
    off_line = getattr(fb, "hidden_inventory_line", lambda: "x")()
    check("disclosure off means no line (every tool is in the payload)",
          off_line == "" and inv_names(fb.build_system_prompt()) is None)
finally:
    fb.CONFIG["agent"]["tool_disclosure"] = _keep_disc
    if _keep_core is None:
        fb.CONFIG["agent"].pop("core_tools", None)
    else:
        fb.CONFIG["agent"]["core_tools"] = _keep_core
check("the suite left the config as it found it",
      inv_names(fb.build_system_prompt()) == want_inv)

# ---- F3: a "verification" that does not test the claim -------------------------------
# Found in the same drive: it "proved" write_file wrote a file by reading that the file
# exists. The clause is on the check bullet, where the mistake is made.
check("prompt: the check must test the claim itself",
      "make the check test the claim itself" in sp)
check("prompt: the exists-is-not-evidence case is named",
      "a file existing proves nothing about what is in it or who wrote it" in sp)
check("prompt: the clause rides the check bullet",
      [l for l in sp.splitlines() if "make the check test the claim itself" in l][:1]
      and "Checking the work is the last step" in
      [l for l in sp.splitlines() if "make the check test the claim itself" in l][0])

# ---- a miss must name the RIGHT door (drive round 3, 2026-09-23) ---------------------
# The model went looking for a hidden tool's shape and used the skill tool for it, then typed
# the tool name into the shell. Both are misses the harness can answer precisely.
out = fb.tool_skill({"action": "read", "name": "search_files"}, {})
check("the skill tool says a TOOL name is a tool, not a skill",
      "is a TOOL on this box" in out and "not a skill" in out, out[:160])
check("and points at the call and at find_tools",
      "call it by name" in out and "find_tools" in out, out[:200])
check("and carries the tool's arguments, so the miss costs no second hop",
      "Its arguments:" in out and "pattern" in out, out[:300])
check("and stays bounded", 0 < len(out) < 900, len(out))
out = fb.tool_skill({"action": "read", "name": "no-such-runbook-xyz"}, {})
check("an ordinary skill miss stays an ordinary miss",
      "No skill named" in out and "is a TOOL" not in out, out[:120])
check("and its name list is bounded on a box with many skills",
      len(out) < 1200 or "more (skill action=list" in out, len(out))

# ---- parked skills are parked (efficiency review 2026-09-24) --------------------
# skills/.imported-unused kept 76 SKILL.md runbooks for reference and skill_index()
# rglob'd through them: the static prompt carried all 108 blurbs every call (5,366
# of its 20,942 chars, measured 2026-09-23). A dot dir must never be indexed.
_tmp = Path(tempfile.mkdtemp(prefix="tinycmdr-skills-"))
(_tmp / "live").mkdir()
(_tmp / "live" / "SKILL.md").write_text(
    "---\nname: liveskill\ndescription: a live one\n---\nbody\n",
    encoding="utf-8", newline="\n")
(_tmp / ".imported-unused").mkdir()
(_tmp / ".imported-unused" / "SKILL.md").write_text(
    "---\nname: parkedskill\ndescription: a parked one\n---\nbody\n",
    encoding="utf-8", newline="\n")
_keep_skills_dir = fb.SKILLS_DIR
try:
    fb.SKILLS_DIR = _tmp
    _names = [s["name"] for s in fb.skill_index()]
    check("a skill in a parked dot dir never reaches the index",
          "parkedskill" not in _names, _names)
    check("the live skill beside it still does",
          "liveskill" in _names, _names)
finally:
    fb.SKILLS_DIR = _keep_skills_dir

out = fb.tool_shell({"command": "list_tools"}, {"session_key": "s-bare"})
check("a bare tool name typed into the shell is answered as a tool",
      "is a TOOL on this box" in out and "Call list_tools directly" in out
      and "schema is now in your tool list" in out, out[:160])
out = fb.tool_shell({"command": "echo hi"}, {"session_key": "s-bare2"})
check("a real command still runs", "exit_code=0" in out, out[:80])

# ---- round 3: the same miss, piped; a gated write; a claim that is not a measurement ----
out = fb.tool_shell({"command": "list_tools 2>&1 | Select-String \"delegate\""},
                    {"session_key": "s-bare3"})
check("a tool name as the first token of a pipeline is answered as a tool too",
      "is a TOOL on this box" in out, out[:160])

# ---- round 6: the tool RUN AS A SCRIPT (`python toolsmith.py ...`) -------------------
# Found on the drive 2026-09-23: every tool file in ./tools/ is also a runnable script, so
# this miss SUCCEEDS and the model never self-corrects. Told to build a tool, the run ran
# `python toolsmith.py "action=new" ...` (which worked), and never made the `toolsmith` TOOL
# CALL its prompt names.
#
# The probe tool is REGISTERED HERE rather than naming a tool that happens to be in this
# checkout: an earlier version of these checks named toolsmith and passed only while another
# suite's leftovers sat in the shared staging dir (measured 2026-09-23). A check that depends
# on a sibling suite, or on the repo's tools/ folder, is not a check.
_keep_custom = dict(fb.REGISTRY.custom)
fb.REGISTRY.custom["probetool"] = {
    "fn": lambda args, ctx: "staged probe ran",
    "source": "<test_tool_discovery>",
    "mutates": False,
    "endpoint_touching": False,
    "schema": {"type": "function", "function": {
        "name": "probetool",
        "description": "staged by this suite to prove the shell answers a tool run as a "
                       "script",
        "parameters": {"type": "object",
                       "properties": {"action": {"type": "string"}},
                       "required": []}}},
}
try:
    out = fb.tool_shell({"command": 'cd C:\\x; python probetool.py "action=new"'},
                        {"session_key": "s-script1"})
    check("a tool run as a script is answered as a tool",
          "is a TOOL on this box" in out and "Call probetool directly" in out
          and "schema is now in your tool list" in out, out[:160])
    check("...and the answer carries that tool's arguments",
          "Its arguments:" in out and "action" in out, out[:300])
    for _cmd in ("python -m probetool action=list",
                 "python C:\\somewhere\\tools\\probetool.py action=list",
                 "python tools/probetool.py action=list"):
        out = fb.tool_shell({"command": _cmd}, {"session_key": "s-script-" + _cmd[:8]})
        check("the same miss is answered for %r" % _cmd[:34],
              "is a TOOL on this box" in out, out[:120])
finally:
    fb.REGISTRY.custom.clear()
    fb.REGISTRY.custom.update(_keep_custom)

# ---- round 7: the skill tool, with the RIGHT verb ------------------------------------
# Nine of eleven skill calls in one round went to `skill{action:list|search, name:<tool>}`,
# one answered with 8 KB of skill taxonomy. The door is the same whichever verb was guessed.
# The name is the one the drive actually used, and it is a CORE tool, so this half needs no
# staged registry entry.
for _act in ("list", "search", "read"):
    out = fb.tool_skill({"action": _act, "name": "search_sessions", "topic": "x"}, {})
    check("the skill tool answers a TOOL name for action=%s too" % _act,
          "is a TOOL on this box" in out and "Its arguments:" in out, out[:180])
out = fb.tool_skill({"action": "search", "topic": "poster"}, {})
check("a real skill search is untouched", "is a TOOL on this box" not in out, out[:140])

out = fb.tool_shell({"command": 'python -c "print(123)"'}, {"session_key": "s-script-c"})
check("a plain interpreter one-liner still runs", "123" in out, out[:120])
check("...and is not mistaken for a tool",
      "is a TOOL on this box" not in out, out[:120])

_keep_confirm = fb.CONFIG["agent"].get("confirm_patterns")
# The probe writes go to a temp dir, never into tests/sessions/: a suite grades the build,
# and a write into the checkout is state the next run (and `git status`) has to explain.
_wprobe = Path(tempfile.mkdtemp(prefix="fbdiscovery-writes-"))
try:
    fb.CONFIG["agent"]["confirm_patterns"] = ["\\bdel\\s+/[a-z]*[sq]"]
    said = []
    res = fb.tool_write_file({"path": str(_wprobe / "gated-probe.cmd"),
                              "content": "del /q /s C:\\nowhere\\x\n", "no_backup": True},
                             {"session_key": "s-gate", "confirm_cb": lambda s: said.append(s) or True})
    check("a gated write asks the operator first", bool(said), said)
    check("and the result says the operator approved it",
          "confirm_patterns" in res and "approved" in res, res[:200])
    res2 = fb.tool_write_file({"path": str(_wprobe / "ungated-probe.txt"),
                               "content": "just text\n", "no_backup": True},
                              {"session_key": "s-gate2", "confirm_cb": lambda s: said.append(s) or True})
    check("an ordinary write carries no such line", "approved" not in res2, res2[:160])
finally:
    if _keep_confirm is None:
        fb.CONFIG["agent"].pop("confirm_patterns", None)
    else:
        fb.CONFIG["agent"]["confirm_patterns"] = _keep_confirm
    shutil.rmtree(_wprobe, ignore_errors=True)

typed_probe = {"status": "ok", "summary": "everything was fine",
               "evidence": ["a diff"], "blockers": [], "followups": []}
rendered = fb.render_subagent_result(typed_probe, "", "the sub-agent's prose")
check("a typed sub-agent result is labeled a CLAIM, not a measurement",
      "CLAIM, not a tool result" in rendered, rendered[:200])
check("and it says to re-check a specific fact", "Re-check" in rendered)
check("the unparsed path keeps its own wording",
      "UNPARSED" in fb.render_subagent_result(None, "no block", "prose"))

sp2 = fb.build_system_prompt()
check("prompt: only a tool result proves a tool ran",
      "Only a TOOL RESULT proves a tool ran" in sp2)
check("prompt: a sub-agent report is a claim, not a measurement",
      "A sub-agent's report is a CLAIM, not a measurement" in sp2)
check("prompt: a result that is not in context means the call did not happen",
      "If a result is NOT in your context, that call did not happen in this run" in sp2)
check("prompt: and mining files for it is named as the slow way",
      "the slowest way to answer" in sp2)

# ---- search_files: the shape the prompt teaches must actually grep -------------------
# Measured 2026-09-25 driving the manager box: the route hint and the routing bullet both teach
# `search_files {"pattern": "<regex>", "path": "<file or directory>"}`, while the tool read
# `pattern` as a NAME glob and the grep as `content`. The run followed the taught shape and
# got a confident "No matches." for a string the file holds ten times - a silent wrong
# answer, and the whole reason the tool is named in the prompt.
srch = Path(tempfile.mkdtemp(prefix="fbtest-search-"))
(srch / "one.py").write_text("alpha = 1\nblocked_patterns here\nbeta = 2\n", encoding="utf-8")
(srch / "two.py").write_text("gamma\n", encoding="utf-8")
out = fb.tool_search_files({"pattern": "blocked_patterns",
                            "path": str(srch / "one.py")}, {})
check("search_files: a FILE path with a regex pattern greps it",
      "one.py:2:" in out and "blocked_patterns here" in out, out[:200])
out = fb.tool_search_files({"pattern": "blocked_patterns", "path": str(srch)}, {})
check("search_files: a DIRECTORY with a regex pattern greps its files",
      "one.py:2:" in out, out[:200])
# Every match is reported, not one per file: the directory pass used to `break` after the
# first hit, so a file holding a pattern four times answered with one line and no note
# (found 2026-10-02 - a silent wrong answer, the failure this tool exists to avoid).
(srch / "many.py").write_text("hit one\nnope\nhit two\nhit three\n", encoding="utf-8")
out = fb.tool_search_files({"pattern": "hit ", "path": str(srch)}, {})
check("search_files: every match in a file is reported, not just the first",
      "many.py:1:" in out and "many.py:3:" in out and "many.py:4:" in out, out[:300])
out = fb.tool_search_files({"pattern": "*.py", "path": str(srch)}, {})
check("search_files: a name glob still lists names",
      "two.py" in out and ":2:" not in out, out[:200])
out = fb.tool_search_files({"pattern": "nothing_here_at_all", "path": str(srch)}, {})
check("search_files: a real miss still answers No matches", out == "No matches.", out[:120])
out = fb.tool_search_files({"content": "gamma", "path": str(srch)}, {})
check("search_files: content= still greps (the schema-honest shape)",
      "two.py:1:" in out, out[:200])
out = fb.tool_search_files({"content": "gamma", "pattern": "*.py", "path": str(srch)}, {})
check("search_files: content= with a glob keeps the glob as the scope",
      "one.py" not in out and "two.py:1:" in out, out[:200])
out = fb.tool_search_files({"path": str(srch / "missing.txt")}, {})
check("search_files: a missing path still errors", out.startswith("ERROR"), out[:120])

# ---- a file too big to content-scan is SKIPPED; say so, or the miss is a lie -----------
# The directory scan skips files over 2 MB. Silently, that turned "find X under <dir>" into
# a confident wrong answer whenever X lived in the largest file - which was found instantly
# when the file was named directly (report H-5, 2026-10-02).
big = srch / "huge.log"
big.write_text("filler line\n" * 400000 + "NEEDLE-OVER-CAP\n", encoding="utf-8")
check("the fixture is over the 2 MB content-scan cap", big.stat().st_size > 2_000_000,
      big.stat().st_size)
out = fb.tool_search_files({"pattern": "NEEDLE-OVER-CAP", "path": str(srch)}, {})
check("search_files: a skipped large file is named, not silently omitted",
      out.startswith("No matches.") and "NOT searched" in out and "huge.log" in out,
      out[:240])
(srch / "small-needle.txt").write_text("NEEDLE-OVER-CAP\n", encoding="utf-8")
out = fb.tool_search_files({"pattern": "NEEDLE-OVER-CAP", "path": str(srch)}, {})
check("search_files: a small hit is returned AND the skip is still disclosed",
      "small-needle.txt" in out and "NOT searched" in out, out[:240])

# ---- the disclosure answer cannot be misread as "nothing is hidden" -------------------
# Measured 2026-09-25 driving the manager box (work order 5): asked which tools were NOT in its list,
# the run called list_tools and find_tools(all=true) in ONE batch, read "N of N", and
# answered "None are hidden" - the sibling call had already revealed them all.
fresh = "wp5-fresh"
out = fb.tool_list_tools({}, {"session_key": fresh})
_m = re.search(r"Core tools: (\d+) of (\d+) are in your list", out)
check("list_tools on a fresh session says part of the core set is missing",
      _m is not None and _m.group(1) != _m.group(2)
      and "answer when you call them by name" in out, out[:400])
check("list_tools: a fresh session names no reveal", "revealed earlier" not in out, out[-160:])
revealed_now = fb.tool_find_tools({"all": True}, {"session_key": fresh})
check("find_tools all=true says which tools were NOT in the list",
      "were NOT in your list a moment ago" in revealed_now, revealed_now[:200])
check("find_tools all=true still says every one of them is now in the list",
      "every remaining tool is now in your list" in revealed_now, revealed_now[:200])
after = fb.tool_list_tools({}, {"session_key": fresh})
check("list_tools after a reveal names the reveal, so N of N cannot read as 'none hidden'",
      "revealed earlier in THIS session" in after and "create_tool" in after, after[:300])
check("find_tools all=true on an already-visible set says so, not an empty diff",
      fb.tool_find_tools({"all": True}, {"session_key": fresh})
      == "All tools are already in your list for this session.")
# ---- a CAPABILITY phrase reveals the tool that serves it --------------------------
# Measured 2026-09-25 on the macOS box: the operator asked for a file to be ATTACHED and the
# run spent 22 calls and 194.8K prompt tokens echoing send_file's name in the shell, then
# failed - the name-driven reveal above never fires for "attach it, do not just paste".
_saved_core = fb.CONFIG["agent"].get("core_tools")
fb.CONFIG["agent"]["core_tools"] = ["shell"]
try:
    check("with send_file hidden, the capability phrase reveals it",
          fb.reveal_tools_named_in(
              "disc-capability",
              "save it as ~/mac-status.txt and attach it, do not just paste the text")
          == ["send_file"], sorted(fb.revealed_tools("disc-capability")))
    check("a neutral order still reveals nothing",
          fb.reveal_tools_named_in("disc-capability-none",
                                   "tell me how much disk is left on this box") == [])
    _missing = fb.pinned_core_tools_missing()
    # derived from _DEFAULT_CORE, not from a name spelled here: the default list is what
    # the pin is checked against, and it changed on 2026-09-27 (send_file and list_tools
    # moved behind disclosure), which made a hardcoded "send_file" assertion go stale.
    _expect = [n for n in fb._DEFAULT_CORE if n != "shell"
               and n in (set(fb.CORE_TOOLS) | set(fb.REGISTRY.custom))]
    check("a pinned core_tools list is checked against _DEFAULT_CORE",
          sorted(_missing) == sorted(_expect), (_missing, _expect))
    _line = fb.capability_line("cli")
    check("and the startup line warns about the stale pin",
          "WARNING" in _line and any(n in _line for n in _expect), _line[:400])
finally:
    if _saved_core is None:
        fb.CONFIG["agent"].pop("core_tools", None)
    else:
        fb.CONFIG["agent"]["core_tools"] = _saved_core
check("an unpinned host is not warned", fb.pinned_core_tools_missing() == [],
      fb.pinned_core_tools_missing())
check("and its capability line carries no warning",
      "WARNING" not in fb.capability_line("cli"))
# ---- the tool tree: find_tools {category: ...} is the leaf the prompt points at -----
# The static prompt carries the SKELETON (a shelf per line, the names on it) and the prose
# sits behind this call: a description line per custom tool cost 167.8 ch / 49.4 est-tok
# PER TOOL on every call (measured 2026-09-25, tests/tool_index_scale.py), so the index is
# capped and the leaf is on demand. Three things a shelf has to do: resolve from the label
# the prompt shows (and from a shorter word for it), name its tools WITH descriptions, and
# reveal NOTHING - a reveal is per-session schema rent that calling the tool pays anyway.
_shelves = {}
_other_shipped, _other_host = [], []
_shipped_sources = shipped_tool_sources()
for _n in sorted(set(fb.CORE_TOOLS) | set(fb.REGISTRY.custom)):
    _shelf = fb._tool_category(_n)
    _shelves.setdefault(_shelf, []).append(_n)
    if _shelf != fb._TOOL_CATEGORY_OTHER:
        continue
    # A tool that DOES file on a shelf stays graded below whether or not this box added it -
    # only an unclassifiable one is exempt, and only when the repository does not carry it.
    _src = (fb.REGISTRY.custom.get(_n) or {}).get("source")
    _host_added = bool(_shipped_sources is not None and _n not in fb.CORE_TOOLS
                       and _src is not None
                       and Path(_src).resolve() not in _shipped_sources)
    (_other_host if _host_added else _other_shipped).append(_n)
check("every SHIPPED tool files on a shelf (nothing strays into `other`)",
      not _other_shipped, _other_shipped)
if _other_host:
    # Said out loud rather than silently skipped: these were not graded, and a reader of this
    # run should know which rule applies to them (the toolsmith's, on the box that owns them)
    # instead of assuming the shelf rule covered them.
    print("     (not graded here - host tools this box added with no shelf: %s)"
          % ", ".join(_other_host))
for _shelf, _members in sorted(_shelves.items()):
    _out = fb.tool_find_tools({"category": _shelf}, {"session_key": "cat-probe"})
    check("%r names its %d tools with what they do" % (_shelf, len(_members)),
          all(n in _out for n in _members) and "- " in _out, _out[:120])
check("a shorter word for a shelf resolves too (the prompt's own labels are guessable)",
      "files & edit" in fb.tool_find_tools({"category": "files"},
                                           {"session_key": "cat-short"}))
check("a category answer reveals nothing (a reveal is schema rent)",
      fb.visible_tool_names("cat-short") == fb.visible_tool_names("cat-never-used"))
_out = fb.tool_find_tools({"category": "zzz-not-a-shelf"}, {"session_key": "cat-miss"})
check("an unknown category names the real ones instead of guessing",
      "no category named" in _out and "files & edit" in _out, _out[:160])
check("and the miss stays bounded", len(_out) < 600, len(_out))
_sp = fb.build_system_prompt()
check("prompt: the index header teaches the call that returns the prose",
      'find_tools {"category": "<cat>"}' in _sp)
check("prompt: the sentence is one field, not a doubled line",
      _sp.count('find_tools {"category": "<cat>"}') == 1)
check("prompt: the index block carries NAMES with no per-tool description line",
      all(not _sp.count("  %s: " % n) or _sp.count("  %s: " % n) == 1
          for n in fb.REGISTRY.custom) and
      all(fb.REGISTRY.custom[n]["schema"]["function"]["description"][:30] not in _sp
          for n in fb.REGISTRY.custom), [n for n in fb.REGISTRY.custom])

print()
print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
sys.exit(1 if FAILS else 0)
