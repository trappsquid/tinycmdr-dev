"""Reasoning is replayed to LOCAL endpoints, capped, and never to a remote one.

A llama.cpp/vLLM chat template rebuilds its <think> block from `reasoning_content`; if
the replayed turn has no trace of it, the template renders a different token sequence for
that turn and the prefix KV-cache diverges from there - on every turn, for exactly the
models that emit the most tokens. A strict remote provider may reject the message-level
field, so replay is local-only. omp: docs/provider-compat-reference.md:68-69.

    python tests/test_reasoning_replay.py
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import run_scenario  # noqa: E402

FAILS = []


def check(cond, what, detail=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}: {detail}")
    else:
        print(f"ok   {what}")


class FakeResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def text_reply(text):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def install_stub(fb, seen, script):
    state = {"i": 0}

    def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                  stream=False):
        seen.append(json.loads(json.dumps(payload)))
        i = state["i"]
        state["i"] += 1
        return FakeResp(script(i, payload))

    fb._post_watchdog = fake_post


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbreplay-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        fb.CONFIG["llm"]["stream"] = False
        THINK = "I should list the tools first. " * 20

        # ---- the gate: local only, and switchable
        check(fb._replay_reasoning_ok(), "a loopback endpoint gets replay by default")
        check(not fb._replay_reasoning_ok("https://api.example.com/v1"),
              "a remote endpoint does not")
        fb.CONFIG["llm"]["replay_reasoning"] = False
        check(not fb._replay_reasoning_ok(), "the switch turns it off")
        fb.CONFIG["llm"]["replay_reasoning"] = True

        fb.CONFIG["llm"]["replay_reasoning_max_chars"] = 100
        capped = fb._reasoning_for_replay({"reasoning_content": "x" * 500})
        check(len(capped) == 100, "the replay text is capped", len(capped))

        # ---- end to end: the tool-call turn carries the reasoning into the next payload
        def script(i, payload):
            if i == 0:
                return {"choices": [{"message": {
                    "role": "assistant", "content": "",
                    "reasoning_content": THINK,
                    "tool_calls": [{"id": "c1", "type": "function",
                                    "function": {"name": "list_tools",
                                                 "arguments": "{}"}}]},
                    "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
            return text_reply("done")

        seen = []
        install_stub(fb, seen, script)
        fb.AGENT.run("replay-local", "list the tools")
        turns = [m for m in seen[-1]["messages"]
                 if m.get("role") == "assistant" and m.get("tool_calls")]
        check(bool(turns) and turns[0].get("reasoning_content") == THINK[:100],
              "the replayed tool-call turn carries the capped reasoning",
              turns[:1])
        check(len(seen[-1]["messages"]) >= 3,
              "the payload kept the full turn sequence", len(seen[-1]["messages"]))

        # ---- switch off: the field is gone
        fb.CONFIG["llm"]["replay_reasoning"] = False
        seen = []
        install_stub(fb, seen, script)
        fb.AGENT.run("replay-off", "list the tools")
        bad = [m for m in seen[-1]["messages"]
               if m.get("role") == "assistant" and m.get("reasoning_content")]
        check(not bad, "with replay off no assistant turn carries reasoning_content", bad)
        fb.CONFIG["llm"]["replay_reasoning"] = True

        # ---- remote gate end to end: an off-LAN base_url gets no field
        fb.CONFIG["llm"]["base_url"] = "https://api.example.com/v1"
        seen = []
        install_stub(fb, seen, script)
        fb.AGENT.run("replay-remote", "list the tools")
        bad = [m for m in seen[-1]["messages"]
               if m.get("role") == "assistant" and m.get("reasoning_content")]
        check(not bad, "an off-LAN endpoint gets no reasoning field", bad)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all reasoning-replay checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
