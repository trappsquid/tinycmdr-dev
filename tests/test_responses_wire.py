"""The /responses wire is a real BODY shape, not a renamed reasoning field.

Chat and Responses disagree about far more than the field name: `messages` -> `input`,
system -> `instructions`, text parts carry an explicit type, tools are flat, the output
cap is `max_output_tokens`, and the answer arrives as `output` items with a `status`
instead of `choices` with a `finish_reason`. This exercises the pure helpers that own that
boundary, plus the URL derivation the tools+reasoning_effort 400 escalation retries on.

On the pre-fix build these helpers do not exist, so every check must come back red.

    python tests/test_responses_wire.py        [TINYCMDR_SRC=/path/to/old/tinycmdr.py]
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

FAILS = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
    if not ok:
        FAILS.append(what)


def got(what, fn, pred=None):
    """Run fn(); a raised exception (a pre-fix build has no helper) is a FAIL too."""
    try:
        v = fn()
    except Exception as e:                      # noqa: BLE001 - report, do not crash
        check(what, False, "%s: %s" % (type(e).__name__, e))
        return None
    check(what, pred(v) if pred else bool(v), repr(v))
    return v


MSGS = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "calling",
     "tool_calls": [{"id": "call_1", "type": "function",
                     "function": {"name": "echo", "arguments": '{"x":1}'}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "out"},
]
TOOLS = [{"type": "function",
          "function": {"name": "echo", "description": "d",
                       "parameters": {"type": "object", "properties": {}}}}]


def main():
    work = Path(tempfile.mkdtemp(prefix="fbresp-"))
    try:
        for name in ("theme.default.toml", "soul.example.md"):
            shutil.copy2(BASE / name, work / name)
        shutil.copy2(SRC, work / "tinycmdr.py")
        shutil.copy2(BASE / "tests" / "fixture-config.json", work / "config.json")
        spec = importlib.util.spec_from_file_location("tinycmdr_responses_wire",
                                                      work / "tinycmdr.py")
        fb = importlib.util.module_from_spec(spec)
        sys.modules["tinycmdr_responses_wire"] = fb
        spec.loader.exec_module(fb)

        # --- the payload mapping ----------------------------------------------
        p = got("responses_payload maps the history into a Responses body",
                lambda: fb.responses_payload("m1", MSGS, tools=TOOLS,
                                             max_output_tokens=1234),
                lambda v: isinstance(v, dict))
        got("a system message becomes `instructions`",
            lambda: p["instructions"], lambda v: v == "SYS")
        got("the request is not stored server-side",
            lambda: p["store"], lambda v: v is False)
        got("the model is carried through",
            lambda: p["model"], lambda v: v == "m1")
        got("the output cap rides as max_output_tokens",
            lambda: p["max_output_tokens"], lambda v: v == 1234)
        got("no chat-shaped `messages` key survives",
            lambda: "messages" not in p, lambda v: v)
        got("a user message becomes an input_text part",
            lambda: p["input"][0]["content"][0],
            lambda v: v == {"type": "input_text", "text": "hi"})
        got("an assistant tool_call becomes a function_call item",
            lambda: p["input"][1],
            lambda v: v == {"type": "function_call", "call_id": "call_1",
                            "name": "echo", "arguments": '{"x":1}'})
        got("...and the call item precedes the assistant's own text",
            lambda: (p["input"][1]["type"], p["input"][2]["content"][0]),
            lambda v: v == ("function_call",
                            {"type": "output_text", "text": "calling"}))
        got("a tool result becomes a function_call_output item",
            lambda: p["input"][3],
            lambda v: v == {"type": "function_call_output", "call_id": "call_1",
                            "output": "out"})
        got("chat tools are flattened into the Responses shape",
            lambda: p["tools"][0],
            lambda v: v == {"type": "function", "name": "echo", "description": "d",
                            "parameters": {"type": "object", "properties": {}}})
        got("stream is omitted unless asked for",
            lambda: "stream" not in p, lambda v: v)
        got("stream: true is carried when asked for",
            lambda: fb.responses_payload("m1", MSGS, stream=True).get("stream"),
            lambda v: v is True)
        got("arguments arriving as a JSON object are stringified",
            lambda: fb.responses_payload("m", [
                {"role": "assistant", "tool_calls": [
                    {"id": "c", "function": {"name": "e",
                                             "arguments": {"x": 1}}}]}])["input"][0]
            ["arguments"], lambda v: v == '{"x": 1}')

        # --- reading a Responses body back ------------------------------------
        msg = got("parse_responses joins message text",
                  lambda: fb.parse_responses({"status": "completed", "output": [
                      {"type": "message", "content": [
                          {"type": "output_text", "text": "Hi "},
                          {"type": "output_text", "text": "there"}]}]}),
                  lambda v: v[0]["content"] == "Hi there" and v[1] == "stop")
        got("...and leaves tool_calls off a plain answer",
            lambda: "tool_calls" not in msg[0], lambda v: v)
        msg = got("parse_responses maps a function_call to a tool_call",
                  lambda: fb.parse_responses({"status": "completed", "output": [
                      {"type": "function_call", "call_id": "call_9", "name": "echo",
                       "arguments": '{"a":1}'},
                      {"type": "message", "content": [
                          {"type": "output_text", "text": "ok"}]}]}),
                  lambda v: v[1] == "tool_calls")
        got("...with the call_id preserved as the call id",
            lambda: msg[0]["tool_calls"][0],
            lambda v: v == {"id": "call_9", "type": "function",
                            "function": {"name": "echo", "arguments": '{"a":1}'}})
        got("...and the accompanying text kept",
            lambda: msg[0]["content"], lambda v: v == "ok")
        msg = got("a reasoning summary becomes reasoning_content",
                  lambda: fb.parse_responses({"status": "incomplete", "output": [
                      {"type": "reasoning", "summary": [
                          {"type": "summary_text", "text": "TH"}]},
                      {"type": "message", "content": [
                          {"type": "output_text", "text": "a"}]}]}),
                  lambda v: v[0].get("reasoning_content") == "TH")
        got("...and an incomplete status maps to a length finish",
            lambda: msg[1], lambda v: v == "length")

        # --- the SSE event mapping --------------------------------------------
        got("response.output_text.delta is text",
            lambda: fb.responses_stream_event(
                {"type": "response.output_text.delta", "delta": "x"}),
            lambda v: v == ("text", "x"))
        got("response.reasoning_summary_text.delta is reasoning",
            lambda: fb.responses_stream_event(
                {"type": "response.reasoning_summary_text.delta", "delta": "r"}),
            lambda v: v == ("reasoning", "r"))
        got("response.reasoning_text.delta is reasoning",
            lambda: fb.responses_stream_event(
                {"type": "response.reasoning_text.delta", "delta": "r2"}),
            lambda v: v == ("reasoning", "r2"))
        got("response.function_call_arguments.delta is tool_args",
            lambda: fb.responses_stream_event(
                {"type": "response.function_call_arguments.delta", "delta": '{"a"',
                 "item_id": "i1", "output_index": 2})[0],
            lambda v: v == "tool_args")
        got("...carrying the fragment and its slot",
            lambda: fb.responses_stream_event(
                {"type": "response.function_call_arguments.delta", "delta": '{"a"',
                 "item_id": "i1", "output_index": 2})[1],
            lambda v: isinstance(v, dict) and v.get("delta") == '{"a"'
            and v.get("output_index") == 2)
        got("an added function_call item names the call",
            lambda: fb.responses_stream_event({"type": "response.output_item.added",
                                               "output_index": 0, "item": {
                                                   "type": "function_call",
                                                   "name": "echo",
                                                   "call_id": "call_3"}})[1],
            lambda v: isinstance(v, dict) and v.get("name") == "echo"
            and v.get("call_id") == "call_3")
        got("response.completed is done, with usage",
            lambda: fb.responses_stream_event({"type": "response.completed", "response": {
                "status": "completed", "usage": {"input_tokens": 5}}})[1]["usage"],
            lambda v: v.get("input_tokens") == 5)
        got("an unknown event is ignored, never guessed at",
            lambda: fb.responses_stream_event({"type": "response.weird.thing"}),
            lambda v: v == (None, None))

        # --- the usage vocabulary boundary ------------------------------------
        got("_responses_as_chat renames usage to the chat fields",
            lambda: fb._responses_as_chat({"status": "completed", "output": [],
                                           "usage": {"input_tokens": 11,
                                                     "output_tokens": 7}}),
            lambda v: v["usage"]["prompt_tokens"] == 11
            and v["usage"]["completion_tokens"] == 7)

        # --- the 400 escalation's URL derivation and remembered wire -----------
        chat_url = "https://api.example.com/v1/chat/completions"
        resp_url = "https://api.example.com/v1/responses"
        got("the /responses sibling is derived from a chat-completions URL",
            lambda: fb._responses_sibling_url(chat_url), lambda v: v == resp_url)
        got("...and it shares the endpoint origin, so the learned fact covers both",
            lambda: fb._endpoint_origin(fb._responses_sibling_url(chat_url))
            == fb._endpoint_origin(chat_url), lambda v: v)
        got("a URL that is not a chat-completions path has no derived sibling",
            lambda: fb._responses_sibling_url("https://api.example.com/v1"),
            lambda v: v is None)
        got("a /responses URL has no further sibling",
            lambda: fb._responses_sibling_url(resp_url), lambda v: v is None)
        got("a chat URL starts on the chat wire",
            lambda: fb._wire_for(chat_url), lambda v: v == "chat")
        got("a tools+reasoning_effort 400 derives the /responses retry URL",
            lambda: fb._responses_escalation_url(chat_url, _TOOLS_400),
            lambda v: v == resp_url)
        got("...and a 400 that names neither field is not escalated",
            lambda: fb._responses_escalation_url(chat_url, "context length exceeded"),
            lambda v: v is None)
        got("...nor is an endpoint already on the responses wire",
            lambda: fb._responses_escalation_url(resp_url, _TOOLS_400),
            lambda v: v is None)
        fb._endpoint_note(chat_url, wire="responses")
        got("the escalation's remembered wire drives the endpoint's next call",
            lambda: (fb._endpoint_facts(chat_url).get("wire"),
                     fb._wire_for(chat_url)),
            lambda v: v == ("responses", "responses"))
        fb._ENDPOINT_FACTS["loaded"] = False        # simulate a fresh process
        got("...and the fact is PERSISTED across a restart",
            lambda: fb._wire_for(chat_url), lambda v: v == "responses")

        if FAILS:
            print("\n%d FAILED: %s" % (len(FAILS), "; ".join(FAILS)))
            return 1
        print("\nall responses-wire checks passed")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


_TOOLS_400 = ("Function tools with reasoning_effort are not supported for "
              "gpt-5.6-terra in /v1/chat/completions.")


if __name__ == "__main__":
    sys.exit(main())
