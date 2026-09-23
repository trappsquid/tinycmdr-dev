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

print()
print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
sys.exit(1 if FAILS else 0)
