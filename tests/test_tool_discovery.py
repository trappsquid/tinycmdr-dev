"""Tool discovery: a capability question must not be answered with a wrong tool.

Measured on a fleet box 2026-09-23 (the Windows test box, 35B-A3B, its own log): three find_tools
calls, each answered "[HARNESS: now callable]" with a tool that does something else -

    "send Mattermost message to channel"            -> `schedule`  (the word "channel")
    "send Mattermost post message channel thread"   -> `blog`      (the word "post")
    "send mattermost message to agent channel via API" -> `delegate_task` ("agent")

- and then 35 minutes of rebuilding by hand a capability the box did not have. The word
overlap score is the defect: it fired on the words that appear in half the registry, so a
MISS now returns this box's whole remaining surface instead, and a query that names no
capability is told so instead of being guessed at.

    python tests/test_tool_discovery.py
    TINYCMDR_SRC=tinycmdr-cli.py python tests/test_tool_discovery.py
"""
import importlib.util
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
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
for i, q in enumerate(["send Mattermost message to channel",
                       "send Mattermost post message channel thread",
                       "send mattermost message to agent channel via API"]):
    out = asked("regress%d" % i, query=q)
    check("no reveal for %r" % q[:44], not revealed(out), out[:120])
    check("  ...and it says nothing matched %r" % q[:38],
          "No tool matched" in out, out[:120])
    check("  ...and nothing was put in the payload",
          fb.hidden_tools("regress%d" % i) == fb.hidden_tools(None))
check("the score itself returns nothing for the worst of them",
      fb._match_tools("send Mattermost message to channel", 4, None) == [],
      fb._match_tools("send Mattermost message to channel", 4, None))

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

# ---- list_tools stays one line (pinned in test_stall too: it was measured) -------
out = fb.tool_list_tools({}, {})
check("list_tools answers in one line", len(out) < 220, len(out))
check("and it forwards to discovery", "find_tools" in out, out)

# ---- an unknown tool name teaches the surface, absent stays absent --------------
_, _, out = fb.Agent._exec_tool(fb.AGENT, {"function": {"name": "delegate_tasksk",
                                                        "arguments": {}}},
                                {"session_key": "s-unk"})
check("an unknown name gets the closest matches", "unknown tool" in out and "find_tools" in out,
      out[:120])
check("and the whole remaining surface", TAIL in out, out[:160])
_, _, out = fb.Agent._exec_tool(fb.AGENT, {"function": {"name": "computer_use",
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
check("the bullet follows the find_tools one directly (no blank field left behind)",
      "filesystem up.\n- Also on this box" in sp)
check("the line is bounded", len(inv_line) < 320, len(inv_line))
check("the prompt is still byte-identical across two builds",
      fb.build_system_prompt() == sp)
check("the placeholder is a live field, not literal braces", "{inventory}" not in sp)
check("the line says these are CORE tools (the drive mislabeled them custom)",
      "Those are core tools" in fb.hidden_inventory_line(), fb.hidden_inventory_line()[:200])
check("and points at the custom block for the rest",
      "custom tools listed at the end" in fb.hidden_inventory_line())
check("prompt: a skill is a runbook, not a tool, so a tools inventory names tools",
      "a skill is a runbook, not a tool" in sp)
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
check("prompt: the clause rides the ledger/check bullet",
      [l for l in sp.splitlines() if "make the check test the claim itself" in l][:1]
      and "Checking the work is the last ledger item" in
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

out = fb.tool_shell({"command": "list_tools"}, {"session_key": "s-bare"})
check("a bare tool name typed into the shell is answered as a tool",
      "is a TOOL on this box" in out and "shell cannot" in out, out[:160])
out = fb.tool_shell({"command": "echo hi"}, {"session_key": "s-bare2"})
check("a real command still runs", "exit_code=0" in out, out[:80])

# ---- round 3: the same miss, piped; a gated write; a claim that is not a measurement ----
out = fb.tool_shell({"command": "list_tools 2>&1 | Select-String \"delegate\""},
                    {"session_key": "s-bare3"})
check("a tool name as the first token of a pipeline is answered as a tool too",
      "is a TOOL on this box" in out, out[:160])

# ---- round 6: the tool RUN AS A SCRIPT (`python toolsmith.py ...`) -------------------
# Found on the drive 2026-09-23: every tool file in ./tools/ is also a runnable script, so
# this miss SUCCEEDS and the model never self-corrects. Told to build a tool, the run read
# toolsmith.py off disk, spilled it twice, tried `python -m toolsmith` (failed), ran
# `python toolsmith.py "action=new" ...` (worked), then listed tools and called find_tools -
# 12 calls in and it had never made the `toolsmith` TOOL CALL its prompt names.
out = fb.tool_shell(
    {"command": 'cd C:\\tinycmdr\\tools; python toolsmith.py "action=new" "name=x"'},
    {"session_key": "s-script1"})
check("a tool run as a script is answered as a tool",
      "is a TOOL on this box" in out and "shell cannot" in out, out[:160])
check("...and the answer carries that tool's arguments",
      "Its arguments:" in out and "action" in out, out[:300])
for _cmd in ("python -m toolsmith action=list",
             "python C:\\tinycmdr\\tools\\toolsmith.py action=list",
             "python tools/toolsmith.py action=list"):
    out = fb.tool_shell({"command": _cmd}, {"session_key": "s-script-" + _cmd[:8]})
    check("the same miss is answered for %r" % _cmd[:34],
          "is a TOOL on this box" in out, out[:120])
out = fb.tool_shell({"command": 'python -c "print(123)"'}, {"session_key": "s-script-c"})
check("a plain interpreter one-liner still runs", "123" in out, out[:120])
check("...and is not mistaken for a tool",
      "is a TOOL on this box" not in out, out[:120])

_keep_confirm = fb.CONFIG["agent"].get("confirm_patterns")
try:
    fb.CONFIG["agent"]["confirm_patterns"] = ["\\bdel\\s+/[a-z]*[sq]"]
    said = []
    res = fb.tool_write_file({"path": str(BASE / "tests" / "sessions" / "gated-probe.cmd"),
                              "content": "del /q /s C:\\nowhere\\x\n", "no_backup": True},
                             {"session_key": "s-gate", "confirm_cb": lambda s: said.append(s) or True})
    check("a gated write asks the operator first", bool(said), said)
    check("and the result says the operator approved it",
          "confirm_patterns" in res and "approved" in res, res[:200])
    res2 = fb.tool_write_file({"path": str(BASE / "tests" / "sessions" / "ungated-probe.txt"),
                               "content": "just text\n", "no_backup": True},
                              {"session_key": "s-gate2", "confirm_cb": lambda s: said.append(s) or True})
    check("an ordinary write carries no such line", "approved" not in res2, res2[:160])
finally:
    if _keep_confirm is None:
        fb.CONFIG["agent"].pop("confirm_patterns", None)
    else:
        fb.CONFIG["agent"]["confirm_patterns"] = _keep_confirm

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

print()
print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
sys.exit(1 if FAILS else 0)
