"""A revealed schema expires; the tool does not.

Reveal-on-demand was monotonic per session: once a tool's schema was revealed it rode
every later payload, so a long run that touched 30 tools paid 30 schemas for ever - the
exact cost the disclosure layer exists to avoid. `agent.reveal_ttl_secs` (default 1800,
0 = off) expires a schema after that long without a call; the NAME stays listed and the
call itself always executes, because disclosure is about schemas, never about existence.

    python tests/test_reveal_decay.py
"""
import importlib.util
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

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all reveal-decay checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
