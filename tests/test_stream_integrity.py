"""A clean close is not a completion, and a no-op edit is not an edit.

Two silent-wrong-answer shapes ported from omp (2026-10-03):

  * a stream that ends with content but NEITHER a `finish_reason` NOR `[DONE]` is a
    truncation (a server killed mid-answer), and it used to be returned as the model's
    final word - finish_reason '' meant the length/window checks never saw it either
    (omp: packages/ai/src/providers/openai-completions.ts:1686-1702);
  * an edit whose new_string reproduces the bytes already on disk wrote the file back
    and returned OK over an empty diff, teaching a weak model to re-anchor and re-send
    variants of the same payload (omp: crates/pi-edit/src/modes/hashline/patcher.rs:53-77,
    NOOP_HARD_LIMIT=3 in crates/pi-edit/src/store.rs:21).

It also pins the reasoning-aliases + leading think-fence rules: a server with no
reasoning parser leaks `<think>...</think>` into `content`, and `reasoning`/
`reasoning_text` fields used to be dropped (omp: openai-completions.ts:1402-1412).

    python tests/test_stream_integrity.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-stream-integrity"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
             STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_stream_integrity",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_stream_integrity"] = fb
spec.loader.exec_module(fb)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}: {detail}")


class FakeResp:
    def __init__(self, script):
        self.script = script
        self.raw = types.SimpleNamespace()
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        for delay, value in self.script:
            if delay:
                time.sleep(delay)
            yield value.encode() if isinstance(value, str) else value

    def close(self):
        self.closed = True

    def raise_for_status(self):
        return None


def sse(*chunks):
    return [(0.0, "data: " + json.dumps(c)) for c in chunks]


SSE_END = (0.0, "data: [DONE]")


def delta(content=None, reasoning=None, finish=None):
    d = {}
    if content is not None:
        d["content"] = content
    if reasoning is not None:
        d.update(reasoning)
    ch = {"index": 0, "delta": d, "finish_reason": finish}
    return {"choices": [ch]}


def run_stream(script, **kw):
    return fb._stream_chat(FakeResp(script), **kw)


# ---------------------------------------------------------- incomplete streams
def test_clean_close_without_terminator_is_a_truncation():
    script = sse(delta(content="The answer so far"), delta(content=" and more"))
    try:
        run_stream(script, idle_seconds=5, first_byte_seconds=5)
        check("content with no finish_reason and no [DONE] raises StreamFailed",
              False, "returned a truncated answer")
    except fb.StreamFailed as e:
        check("content with no finish_reason and no [DONE] raises StreamFailed",
              "no finish_reason" in str(e), e)
    except Exception as e:                                  # noqa: BLE001
        check("content with no finish_reason and no [DONE] raises StreamFailed",
              False, "%s: %s" % (type(e).__name__, e))


def test_done_alone_is_a_completion():
    script = sse(delta(content="hello"), delta(content=" world")) + [SSE_END]
    data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
    check("a stream ended by [DONE] alone is accepted",
          data["choices"][0]["message"]["content"] == "hello world",
          data["choices"][0]["message"])


def test_finish_reason_alone_is_a_completion():
    script = sse(delta(content="hello"), delta(finish="stop"))
    data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
    check("a stream ended by finish_reason alone is accepted",
          data["choices"][0]["message"]["content"] == "hello"
          and data["choices"][0]["finish_reason"] == "stop",
          data["choices"][0])


# ---------------------------------------------------------- reasoning fields
def test_reasoning_alias_fields_are_read():
    script = sse(delta(reasoning={"reasoning": "I think "}),
                 delta(reasoning={"reasoning_text": "therefore"}),
                 delta(content="Answer"), delta(finish="stop")) + [SSE_END]
    data, stats = run_stream(script, idle_seconds=5, first_byte_seconds=5)
    m = data["choices"][0]["message"]
    check("the `reasoning` field is captured", m.get("reasoning_content") == "I think therefore",
          m)
    check("...and is not in the answer", m.get("content") == "Answer", m)
    check("...and counts as reasoning, not answer chars",
          stats["reasoning_chars"] == len("I think therefore")
          and stats["chars"] == len("Answer"), stats)


def test_aliases_are_not_summed():
    d = {"reasoning": "same text", "reasoning_content": "same text"}
    script = sse(delta(reasoning=d), delta(content="x"), delta(finish="stop")) + [SSE_END]
    data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
    check("two aliases carrying one text are not double-counted",
          data["choices"][0]["message"].get("reasoning_content") == "same text",
          data["choices"][0]["message"])


# ---------------------------------------------------------- leading think fence
def test_leading_fence_split_across_deltas():
    script = sse(delta(content="<thi"), delta(content="nk>"),
                 delta(content="secret plan"), delta(content="</thi"), delta(content="nk>"),
                 delta(content="The answer"), delta(finish="stop")) + [SSE_END]
    data, stats = run_stream(script, idle_seconds=5, first_byte_seconds=5)
    m = data["choices"][0]["message"]
    check("a leading <think> fence lands in reasoning, not content",
          m.get("content") == "The answer", m)
    check("...with the thinking intact", m.get("reasoning_content") == "secret plan", m)
    check("...and the stats split answers from thinking",
          stats["chars"] == len("The answer")
          and stats["reasoning_chars"] == len("secret plan"), stats)


def test_non_leading_fence_stays_content():
    script = sse(delta(content="The answer. "), delta(content="<think>x</think>"),
                 delta(finish="stop")) + [SSE_END]
    data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
    m = data["choices"][0]["message"]
    check("a <think> inside an answer is left as the model wrote it",
          m.get("content") == "The answer. <think>x</think>"
          and not m.get("reasoning_content"), m)


def test_unterminated_fence_is_thinking():
    script = sse(delta(content="<think>started thinking"), delta(finish="stop")) + [SSE_END]
    data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
    m = data["choices"][0]["message"]
    check("an unterminated fence is thinking, never an answer",
          m.get("content") == "" and m.get("reasoning_content") == "started thinking", m)


def test_plain_angle_bracket_text_is_not_held():
    script = sse(delta(content="<b>hi</b> and < not a fence"), delta(finish="stop")) + [SSE_END]
    data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
    check("content that merely starts with < is answered as-is",
          data["choices"][0]["message"].get("content") == "<b>hi</b> and < not a fence",
          data["choices"][0]["message"])


def test_fence_off_keeps_raw_text():
    was = fb.CONFIG["llm"].get("think_fence", True)
    fb.CONFIG["llm"]["think_fence"] = False
    try:
        script = sse(delta(content="<think>x</think>answer"), delta(finish="stop")) + [SSE_END]
        data, _ = run_stream(script, idle_seconds=5, first_byte_seconds=5)
        check("llm.think_fence=false sends the raw text through",
              data["choices"][0]["message"].get("content") == "<think>x</think>answer",
              data["choices"][0]["message"])
    finally:
        fb.CONFIG["llm"]["think_fence"] = was


# ---------------------------------------------------------- no-op edit guard
def test_noop_edit_is_refused_then_escalated_then_cleared():
    work = Path(tempfile.mkdtemp(prefix="fbtest-noop-edit-"))
    try:
        target = work / "cfg.txt"
        target.write_text("hello\n", encoding="utf-8")
        ctx = {"session_key": "noop-s1"}
        same = {"path": str(target), "old_string": "hello", "new_string": "hello"}
        before = target.read_bytes()

        r1 = fb.tool_edit_file(dict(same), ctx)
        check("a byte-identical edit is refused, not reported OK",
              "changed nothing" in r1 and not r1.startswith("OK"), r1)
        check("...and the file is left alone",
              target.read_bytes() == before and not (work / "cfg.txt.bak").exists(),
              target.read_bytes())

        fb.tool_edit_file(dict(same), ctx)
        r3 = fb.tool_edit_file(dict(same), ctx)
        check("the third identical no-op escalates to STOP",
              r3.startswith("STOP."), r3)

        real = fb.tool_edit_file({"path": str(target), "old_string": "hello",
                                  "new_string": "world"}, ctx)
        check("a real edit still lands", real.startswith("OK") and
              target.read_text(encoding="utf-8") == "world\n", real)

        r4 = fb.tool_edit_file({"path": str(target), "old_string": "world",
                                "new_string": "world"}, ctx)
        check("a landed edit clears the no-op streak (no instant STOP)",
              "changed nothing" in r4 and not r4.startswith("STOP."), r4)

        # A write_file to the same path clears it too.
        fb.tool_edit_file({"path": str(target), "old_string": "world",
                           "new_string": "world"}, ctx)
        fb.tool_write_file({"path": str(target), "content": "fresh\n"}, ctx)
        r5 = fb.tool_edit_file({"path": str(target), "old_string": "fresh",
                                "new_string": "fresh"}, ctx)
        check("a write_file clears the no-op streak",
              "changed nothing" in r5 and not r5.startswith("STOP."), r5)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    test_clean_close_without_terminator_is_a_truncation()
    test_done_alone_is_a_completion()
    test_finish_reason_alone_is_a_completion()
    test_reasoning_alias_fields_are_read()
    test_aliases_are_not_summed()
    test_leading_fence_split_across_deltas()
    test_non_leading_fence_stays_content()
    test_unterminated_fence_is_thinking()
    test_plain_angle_bracket_text_is_not_held()
    test_fence_off_keeps_raw_text()
    test_noop_edit_is_refused_then_escalated_then_cleared()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        return 1
    print("all stream-integrity checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
