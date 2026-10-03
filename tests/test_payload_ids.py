"""Duplicate tool_call_ids: repaired deterministically, reported honestly.

A local server (or a proxy) that re-emits a call id used to collapse two calls onto one
id in every pairing structure here, so the payload shipped two tool_calls with one id and
one result. llama.cpp ignores it; a strict endpoint answers 400 - one failover away by
design; ids are split in order and the results re-pointed.

    python tests/test_payload_ids.py
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-payload-ids"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
             STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_payload_ids",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_payload_ids"] = fb
spec.loader.exec_module(fb)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}: {detail}")


def _call(tid, name):
    return {"id": tid, "type": "function",
            "function": {"name": name, "arguments": "{}"}}


def main():
    dup = [
        {"role": "assistant", "content": "",
         "tool_calls": [_call("x", "shell"), _call("x", "shell")]},
        {"role": "tool", "tool_call_id": "x", "content": "first result"},
        {"role": "tool", "tool_call_id": "x", "content": "second result"},
    ]

    check("duplicates are reported before repair",
          any("duplicate tool_call_id" in p for p in fb._tool_pairing_problems(dup)),
          fb._tool_pairing_problems(dup))

    out = fb._uniquify_tool_call_ids(dup)
    ids = [tc["id"] for tc in out[0]["tool_calls"]]
    check("the duplicate call gets its own id", ids == ["x", "x_dup1"], ids)
    check("the results are re-pointed in order",
          [m["tool_call_id"] for m in out[1:]] == ["x", "x_dup1"],
          [m["tool_call_id"] for m in out[1:]])
    check("...and the caller's messages are not mutated in place",
          dup[0]["tool_calls"][1]["id"] == "x"
          and dup[2]["tool_call_id"] == "x", dup)

    repaired = fb._repair_tool_pairing(dup)
    check("the repaired payload has no pairing problems",
          fb._tool_pairing_problems(repaired) == [],
          fb._tool_pairing_problems(repaired))
    check("the repaired payload keeps every call and result",
          len(repaired) == 3
          and [tc["id"] for tc in repaired[0]["tool_calls"]] == ["x", "x_dup1"],
          repaired)

    # A healthy payload is returned object-identical: the pass must cost nothing.
    clean = [
        {"role": "assistant", "content": "",
         "tool_calls": [_call("a", "shell"), _call("b", "shell")]},
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": "rb"},
    ]
    check("a clean payload is untouched", fb._uniquify_tool_call_ids(clean) is clean)

    # The same id reused in a LATER assistant message is also split.
    later = [
        {"role": "assistant", "content": "", "tool_calls": [_call("k", "shell")]},
        {"role": "tool", "tool_call_id": "k", "content": "r1"},
        {"role": "assistant", "content": "again", "tool_calls": [_call("k", "shell")]},
        {"role": "tool", "tool_call_id": "k", "content": "r2"},
    ]
    out = fb._uniquify_tool_call_ids(later)
    check("an id reused across turns is split too",
          out[2]["tool_calls"][0]["id"] == "k_dup1"
          and out[3]["tool_call_id"] == "k_dup1", out)

    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        return 1
    print("all payload-id checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
