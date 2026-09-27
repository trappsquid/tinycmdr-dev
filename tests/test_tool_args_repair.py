"""A replayed tool call must carry VALID JSON in `arguments`.

Measured on the live install 2026-09-26: a local model emitted
`{"action":"search","name":"web,"topic":"the failure"}` - one quote where a comma belongs. The
harness executed a salvaged version of it, and the malformed blob was then replayed as the
assistant's own `tool_calls` on the next request. llama.cpp parses those arguments and answers
HTTP 500 for the WHOLE request ("Failed to parse tool call arguments as JSON ... parse error at
line 1, column 34"), so every later turn in that session died as "no LLM endpoint answered".

Reproduced against the live endpoint before the fix: the malformed blob 500'd at column 34, the
same call with valid JSON returned 200, and the repaired `{}` returned 200.

The repair runs at the same choke point as the tool-pairing repair, so a history written by an
older build heals on its next send instead of needing the session dropped.

    python tests/test_tool_args_repair.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
FAILS = []

# The blob from the live session, and the same call one character away from valid.
MALFORMED = '{"action":"search","name":"web,"topic":"the failure"}'
VALID = '{"action":"search","name":"web","topic":"the failure"}'


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILS.append(name)
        print(f"FAIL {name}: {detail}")


def load_app():
    """Import the app from a staged copy - the suites never import the checkout in place."""
    stage = Path(tempfile.mkdtemp(prefix="fbargs-"))
    shutil.copy2(SRC, stage / "tinycmdr.py")
    spec = importlib.util.spec_from_file_location("fbargs", stage / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fbargs"] = mod
    spec.loader.exec_module(mod)
    return mod


def call(arguments, name="skill"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": name, "arguments": arguments}}]}


def args_of(message):
    return message["tool_calls"][0]["function"]["arguments"]


def parses(text):
    try:
        json.loads(text)
        return True
    except Exception:
        return False


def main():
    fb = load_app()

    # ---- the helper ------------------------------------------------------------
    out = fb._repair_tool_arguments([call(MALFORMED)])
    check("a malformed blob is replaced with valid JSON",
          args_of(out[0]) == "{}", args_of(out[0]))

    out = fb._repair_tool_arguments([call(VALID)])
    check("valid arguments are left byte-identical",
          args_of(out[0]) == VALID, args_of(out[0]))

    out = fb._repair_tool_arguments([call({"action": "search"})])
    normalised = args_of(out[0])
    check("a dict-shaped arguments field becomes a JSON string",
          isinstance(normalised, str) and json.loads(normalised) == {"action": "search"},
          normalised)

    for empty in ("", "   ", None):
        out = fb._repair_tool_arguments([call(empty)])
        check(f"empty arguments ({empty!r}) become {{}}", args_of(out[0]) == "{}", args_of(out[0]))

    once = fb._repair_tool_arguments([call(MALFORMED)])
    twice = fb._repair_tool_arguments(once)
    check("the repair is idempotent", args_of(twice[0]) == "{}", args_of(twice[0]))

    # ---- the choke point: what the request actually carries --------------------
    agent = fb.Agent()
    history = [
        {"role": "user", "content": "first task"},
        call(MALFORMED),
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        {"role": "assistant", "content": "done"},
    ]
    payload = agent._payload(history, state=False)
    sent = [tc["function"]["arguments"]
            for m in payload if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])]
    check("the payload builder sends at least one tool call", bool(sent), payload)
    check("every replayed arguments field parses as JSON",
          all(parses(a) for a in sent), sent)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed:")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("all tool-argument checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
