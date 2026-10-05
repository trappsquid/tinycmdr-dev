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

    # ---- what a local model actually emits --------------------------------
    cases = [
        ('```json\n{"action":"search","topic":"x"}\n```',
         {"action": "search", "topic": "x"}, "a fenced block keeps its object"),
        ('Sure - here you go: {"action":"search","topic":"x"} hope that helps',
         {"action": "search", "topic": "x"}, "prose around the object keeps the object"),
        ('{"cmd": "echo }"}', {"cmd": "echo }"}, "a brace inside a string is left alone"),
    ]
    for text, want, label in cases:
        got = args_of(fb._repair_tool_arguments([call(text)])[0])
        check(label, json.loads(got) == want, got)

    out = fb._repair_tool_arguments([call('[1, 2, 3]')])
    check("valid JSON that is not an object is passed through (the tool rejects it, not the server)",
          args_of(out[0]) == "[1, 2, 3]", args_of(out[0]))

    out = fb._repair_tool_arguments([call('here: [1, 2] done')])
    check("a WRAPPED non-object falls back to {}", args_of(out[0]) == "{}", args_of(out[0]))

    for empty in ("", "   ", None):
        out = fb._repair_tool_arguments([call(empty)])
        check(f"empty arguments ({empty!r}) become {{}}", args_of(out[0]) == "{}", args_of(out[0]))

    once = fb._repair_tool_arguments([call(MALFORMED)])
    twice = fb._repair_tool_arguments(once)
    check("the repair is idempotent", args_of(twice[0]) == "{}", args_of(twice[0]))

    # ---- doubled tool names, with and without arguments ------------------------
    def dbl(name, args=""):
        return [{"id": "c", "type": "function",
                 "function": {"name": name, "arguments": args}}]

    slots = dbl("shellshell", "{}")            # clean arguments: the old gate skipped it
    fb._repair_doubled_calls(slots)
    check("a doubled name with clean arguments is halved",
          slots[0]["function"]["name"] == "shell", slots[0])

    slots = dbl("memorymemory")                # no arguments at all: the old gate skipped it
    fb._repair_doubled_calls(slots)
    check("a doubled name with NO arguments is halved",
          slots[0]["function"]["name"] == "memory", slots[0])

    slots = dbl("shellshell", '{"a":1}{"a":1}')
    fb._repair_doubled_calls(slots)
    check("...and a doubled argument blob still collapses to the first object",
          slots[0]["function"]["name"] == "shell"
          and slots[0]["function"]["arguments"] == '{"a":1}', slots[0])

    slots = dbl("witwit")
    fb._repair_doubled_calls(slots)
    check("a doubled name that resolves to nothing is left alone",
          slots[0]["function"]["name"] == "witwit", slots[0])

    stored = [call(MALFORMED)]
    fb._repair_tool_arguments(stored)
    check("the repair does not rewrite the caller's stored history",
          args_of(stored[0]) == MALFORMED, args_of(stored[0]))

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
    check("the payload build left the stored blob untouched",
          args_of(history[1]) == MALFORMED, args_of(history[1]))

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
