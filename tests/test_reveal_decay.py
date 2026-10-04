"""A revealed schema expires; the tool does not.

Reveal-on-demand was monotonic per session: once a tool's schema was revealed it rode
every later payload, so a long run that touched 30 tools paid 30 schemas for ever - the
exact cost the disclosure layer exists to avoid. `agent.reveal_ttl_secs` (default 1800,
0 = off) expires a schema after that long without a call; the NAME stays listed and the
call itself always executes, because disclosure is about schemas, never about existence.

    python tests/test_reveal_decay.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-reveal-decay"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
             STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_reveal_decay", STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_reveal_decay"] = fb
spec.loader.exec_module(fb)

FAILS = []


def check(cond, what, detail=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}: {detail}")
    else:
        print(f"ok   {what}")


def main():
    key = "decay-s1"
    hidden = fb.hidden_tools(key)
    check(bool(hidden), "the session starts with hidden tools", hidden[:5])
    victim = sorted(hidden)[0]

    fb.reveal_tools(key, [victim])
    check(victim in fb.revealed_tools(key), "a revealed tool is visible", victim)
    check(victim in fb.visible_tool_names(key), "...and its schema is sent", victim)

    # ---- expiry
    fb.CONFIG["agent"]["reveal_ttl_secs"] = 0.05
    time.sleep(0.08)
    check(victim not in fb.revealed_tools(key),
          "the schema expires after the TTL without a call", victim)
    check(victim not in fb.visible_tool_names(key),
          "...so the payload stops paying for it", victim)
    check(victim in fb.hidden_tools(key),
          "...and the tool is discoverable again", victim)

    # ---- a call (or a reveal) refreshes it
    fb.reveal_tools(key, [victim])
    check(victim in fb.revealed_tools(key), "a new call re-reveals it", victim)

    # ---- ttl 0 is the old behaviour: never decay
    fb.CONFIG["agent"]["reveal_ttl_secs"] = 0
    time.sleep(0.02)
    check(victim in fb.revealed_tools(key), "ttl 0 keeps the schema for the session",
          victim)

    # ---- reveal_tools still returns the session's known names
    names = fb.reveal_tools(key, ["another_tool"])
    check(victim in names and "another_tool" in names,
          "reveal_tools returns the known names", names)

    # ---- a call is a use: the TTL clock is idle time, not time since the reveal ----
    # Spec (2026-10-03): reveal -> call at t+1700 -> still visible at t+1900, because the
    # call moved the stamp; reveal -> never called -> gone at t+1900, and the call still
    # executes and re-reveals; ttl=0 -> the old never-decay behaviour. The clock is aged
    # directly, so the checks are deterministic.
    bkey = "decay-use"
    tool = "notes"                    # hidden by default and safe to run with no args
    fb.CONFIG["agent"]["reveal_ttl_secs"] = 1800
    fb.reveal_tools(bkey, [tool])
    fb._revealed[bkey][tool] = time.time() - 1700            # a reveal 1700s old
    _, _, out = fb.Agent._exec_tool(
        fb.AGENT, {"function": {"name": tool, "arguments": {}}},
        {"session_key": bkey})
    check("was not in your tool list" not in out,
          "a call inside the ttl is not treated as hidden")
    fresh = time.time() - fb._revealed[bkey][tool]
    check(fresh < 5, "the call moved the stamp (%ds old)" % int(fresh))
    fb._revealed[bkey][tool] -= 200                          # t+1900 since the reveal
    check(tool in fb.revealed_tools(bkey),
          "...so the used schema is still visible at t+1900")

    ikey = "decay-idle"
    fb.reveal_tools(ikey, [tool])
    fb._revealed[ikey][tool] = time.time() - 1900
    check(tool not in fb.revealed_tools(ikey),
          "an UNUSED reveal is gone at t+1900")
    _, _, out = fb.Agent._exec_tool(
        fb.AGENT, {"function": {"name": tool, "arguments": {}}},
        {"session_key": ikey})
    check(not out.startswith("ERROR"), "the call still executes")
    check("was not in your tool list" in out, "...and re-reveals the tool")
    check("expires after 1800s unused" in out,
          "the banner says how long the schema stays")
    check(tool in fb.revealed_tools(ikey), "the payload carries it again")

    fb.CONFIG["agent"]["reveal_ttl_secs"] = 0
    fb.reveal_tools("decay-off", [tool])
    fb._revealed["decay-off"][tool] = time.time() - 10 ** 6
    check(tool in fb.revealed_tools("decay-off"),
          "ttl 0: an aged reveal never decays (the old behaviour)")
    _, _, out = fb.Agent._exec_tool(
        fb.AGENT, {"function": {"name": tool, "arguments": {}}},
        {"session_key": "decay-off2"})
    check("was not in your tool list" in out and "expires after" not in out,
          "ttl 0 reveals without advertising an expiry")

    # ---- reveal/eject transitions land in the event ledger -------------------
    lkey = "decay-ledger"
    fb.CONFIG["agent"]["event_log"] = True
    fb.CONFIG["agent"]["reveal_ttl_secs"] = 1800
    fb.note_schema_transition(lkey, fb.select_tool_schemas(lkey))     # baseline
    fb.reveal_tools(lkey, [tool])
    fb.note_schema_transition(lkey, fb.select_tool_schemas(lkey))
    fb._revealed[lkey][tool] = time.time() - 4000
    fb.note_schema_transition(lkey, fb.select_tool_schemas(lkey))
    lpath = STAGE / "sessions" / (lkey + ".events.jsonl")
    rows = ([json.loads(x) for x in lpath.read_text(encoding="utf-8").splitlines()
             if x.strip()] if lpath.exists() else [])
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r.get("kind"), []).append(r.get("names") or [])
    check([tool] in by_kind.get("reveal", []),
          "a reveal is logged with the tool it revealed")
    check([tool] in by_kind.get("eject", []),
          "an eject is logged with the tool it dropped")

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all reveal-decay checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
