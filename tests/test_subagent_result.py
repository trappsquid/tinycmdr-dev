"""A sub-agent's yield is TYPED, and an unparseable one is still the work.

delegate_task used to hand the parent raw prose, so "did the check pass" had to be read
out of a paragraph by the same model that asked for it. Now the sub-agent ends with one
fenced ```result block, the harness validates it, and the parent gets fields - which is
what makes "have an agent that did not write the work check it" a usable instruction.

Fail-soft is the point of half these checks: a sub-agent that ignores the contract, or
returns JSON with the wrong shape, still comes back with its answer attached and a
marker saying the shape is missing. Losing the work to a parse error would be worse than
losing the shape.

    python tests/test_subagent_result.py
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_subagent_under_test", SRC)
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_subagent_under_test"] = fb
spec.loader.exec_module(fb)

PASSES = []
FAILS = []


def check(name, cond, detail=""):
    (PASSES if cond else FAILS).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


def block(**kw):
    return "```result\n" + json.dumps(kw) + "\n```"


OK = 'Checked the three claims.\n' + block(status="ok", summary="all three hold",
                                           evidence=["pytest: 12 passed"],
                                           blockers=[], followups=["ship it"])
typed, why = fb.parse_subagent_result(OK)
check("a well-formed yield parses", typed is not None, why)
check("status survives", (typed or {}).get("status") == "ok")
check("summary survives", (typed or {}).get("summary") == "all three hold")
check("evidence is a list", (typed or {}).get("evidence") == ["pytest: 12 passed"])
check("empty fields are empty, not missing", (typed or {}).get("blockers") == [])

# ---- the parent's view -----------------------------------------------------------
out = fb.render_subagent_result(typed, why, OK)
check("the parent is told the yield is typed", "typed sub-agent result" in out, out[:80])
check("with the status in the header", "status ok" in out, out[:80])
check("and each field on its own line", "summary:" in out and "evidence:" in out
      and "blockers: none" in out and "followups: ship it" in out, out)
check("the raw words still ride along", "Checked the three claims." in out, out)
check("the machine block is not repeated as prose", "```result" not in out, out)

# ---- the fail-soft paths ---------------------------------------------------------
for name, text, why_bit in (("no block at all", "I looked around, everything seems fine.",
                             "no ```result block"),
                            ("not JSON", "notes\n```result\n{not json}\n```",
                             "is not JSON"),
                            ("a JSON array", "```result\n[1, 2]\n```", "not a JSON object"),
                            ("an invented status", block(status="probably", summary="x"),
                             "not one of ok|blocked|failed"),
                            ("no summary", block(status="ok", summary="   "),
                             "no summary")):
    t, w = fb.parse_subagent_result(text)
    check("%s is UNPARSED" % name, t is None and why_bit in w, w)
    r = fb.render_subagent_result(t, w, text)
    check("  ...and the answer is still handed over", "UNPARSED" in r
          and "read them as prose" in r, r[:120])

check("an empty answer does not vanish", "(empty answer)" in
      fb.render_subagent_result(None, "no block", ""))

# ---- statuses and shape edges ----------------------------------------------------
t, w = fb.parse_subagent_result(block(status="blocked", summary="no key on the box",
                                      blockers=["needs DEEPSEEK_API_KEY"]))
check("blocked carries its blocker", t["status"] == "blocked" and t["blockers"],
      (t, w))
t, _ = fb.parse_subagent_result(block(status="failed", summary="suite is red",
                                      evidence="pytest: 3 failed"))
check("a bare string field is accepted as one line",
      t["evidence"] == ["pytest: 3 failed"], t)
t, _ = fb.parse_subagent_result(block(status="ok", summary="s",
                                      evidence=["e%d" % i for i in range(20)]))
check("fields are capped, not unbounded", len(t["evidence"]) == 8, len(t["evidence"]))

quoted = ("here is the shape I will use:\n" + block(status="ok", summary="example")
          + "\nthe real one:\n" + block(status="blocked", summary="real answer"))
t, w = fb.parse_subagent_result(quoted)
check("the LAST block wins", t["status"] == "blocked" and t["summary"] == "real answer", (t, w))
check("a quoted example does not decide the yield", "example" not in t["summary"], t)

long = "x" * 5000 + block(status="ok", summary="s")
r = fb.render_subagent_result(None, "why", long)
check("a runaway answer is trimmed with a pointer",
      "trimmed" in r and len(r) < 2600, len(r))

# ---- the wiring: the contract rides the TASK ------------------------------------
seen = {}


def fake_run(key, text, **kw):
    seen["key"] = key
    seen["text"] = text
    seen["source"] = kw.get("source")
    return OK


real_run, real_reset, real_relay = fb.AGENT.run, fb.AGENT.reset, fb._relay_callbacks
try:
    fb.AGENT.run = fake_run
    fb.AGENT.reset = lambda key: None
    fb._relay_callbacks = lambda ctx, src: {}
    out = fb.tool_delegate_task({"task": "verify the last change"}, {"session_key": "s1"})
finally:
    fb.AGENT.run, fb.AGENT.reset, fb._relay_callbacks = real_run, real_reset, real_relay

check("the sub-agent is told how to end", fb._SUBAGENT_RESULT_CONTRACT in seen["text"],
      seen["text"][-120:])
check("and the task is still at the front, unchanged",
      seen["text"].startswith("verify the last change"), seen["text"][:60])
check("the parent gets the typed rendering back", "typed sub-agent result" in out, out[:100])
check("the sub-agent runs in its own session", str(seen["key"]).startswith("sub-"),
      seen["key"])
check("its lines are tagged as a subtask", str(seen["source"]).startswith("sub:"),
      seen["source"])
check("the tool schema advertises the typed yield",
      "TYPED" in fb.CORE_TOOLS["delegate_task"]["schema"]["function"]["description"])
check("a sub-agent cannot spawn a sub-agent",
      "cannot spawn further" in fb.tool_delegate_task({"task": "x"}, {"depth": 1}))

print()
print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
sys.exit(1 if FAILS else 0)
