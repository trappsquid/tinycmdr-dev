"""A clean `stop` with no content is a no-op, not an answer: re-send, don't re-turn.

The agent-level empty-answer path costs a whole extra turn - a nudge message plus a full
prompt re-read on a prefill-bound local box. The provider layer re-sends it instead:
finish_reason `stop`, no visible content, <= 1 output token, one bounded re-send of the
IDENTICAL payload.

    python tests/test_empty_stop_retry.py
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


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
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


def reply(content, finish="stop", completion_tokens=5):
    return {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": completion_tokens}}


def install_stub(fb, seen, replies):
    state = {"i": 0}

    def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                  stream=False):
        seen.append(json.loads(json.dumps(payload)))
        i = state["i"]
        state["i"] += 1
        return FakeResp(replies[min(i, len(replies) - 1)])

    fb._post_watchdog = fake_post


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbempty-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        fb.CONFIG["llm"]["stream"] = False

        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]

        # ---- an empty stop is re-sent once, unchanged, and the second answer wins
        seen = []
        install_stub(fb, seen, [reply("", completion_tokens=0),
                                reply("the real answer")])
        msg = fb.AGENT._chat(list(msgs), session_key="empty-1", usage={})
        check(len(seen) == 2, "an empty stop completion is retried once")
        check(seen[0] == seen[1], "...with the IDENTICAL payload")
        check((msg.get("content") or "") == "the real answer",
              "...and the answer is the second response")

        # ---- a real answer is not re-sent, and neither is a turn that spent tokens
        seen = []
        install_stub(fb, seen, [reply("done")])
        msg = fb.AGENT._chat(list(msgs), session_key="empty-2", usage={})
        check(len(seen) == 1 and msg.get("content") == "done",
              "a normal answer costs one call")

        seen = []
        install_stub(fb, seen, [reply("", completion_tokens=40),
                                reply("should not be asked")])
        fb.AGENT._chat(list(msgs), session_key="empty-3", usage={})
        check(len(seen) == 1, "an empty turn that spent tokens is NOT re-sent")

        # ---- bounded: the re-send itself coming back empty ends the call (no loop)
        seen = []
        install_stub(fb, seen, [reply("", completion_tokens=0),
                                reply("", completion_tokens=0)])
        msg = fb.AGENT._chat(list(msgs), session_key="empty-4", usage={})
        check(len(seen) == 2 and not (msg.get("content") or ""),
              "the retry is one-shot, never a loop")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all empty-stop retry checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
