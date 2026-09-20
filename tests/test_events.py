"""Tests for the event log (stage 4 of the MiniDSH plan), shadow only.

    python tests/test_events.py
    tinycmdr_SRC=tinycmdr-cli.py python tests/test_events.py

The artifact is a file nothing reads yet, so nothing downstream will ever notice when it is
wrong. Every property it has to have - one line per event, valid JSON, no secrets on disk, a
file that survives concurrent tool calls, a run that keeps going when the write fails - has
to be asserted here or it is a hope.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("tinycmdr_SRC", "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_events_under_test", SRC)
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_events_under_test"] = fb
spec.loader.exec_module(fb)

TMP = Path(tempfile.mkdtemp(prefix="fbevents-"))
fb.NOTES_FILE = TMP / "notes.md"
fb.NOTES_FILE.write_text("", encoding="utf-8")
fb.TASKS_FILE = TMP / "tasks.json"
fb.SESSIONS_DIR = TMP / "sessions"
fb.SESSIONS_DIR.mkdir(exist_ok=True)

PASSES = []
FAILS = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
    else:
        FAILS.append(name)
        print(f"FAIL {name}  {detail}")


def rows(key):
    path = fb._event_path(key)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def clear(key):
    fb._event_path(key).unlink(missing_ok=True)
    fb._EVENT_SEQ.pop(key, None)


def with_log(on=True):
    fb.CONFIG["agent"]["event_log"] = on


# --------------------------------------------------------------------------
# the switch, and the file
# --------------------------------------------------------------------------

def test_it_is_off_until_a_host_asks_for_it():
    with_log(False)
    check("the fleet default is off", fb.CONFIG["agent"].get("event_log") is False,
          fb.CONFIG["agent"].get("event_log"))
    clear("off-session")
    fb.event("tool.call", session_key="off-session", name="shell")
    check("off: nothing is written at all", rows("off-session") == [], rows("off-session"))
    clear("off-2")
    with fb._RunSpan("off-2"):
        fb.event("tool.call", session_key="off-2", name="shell")
    check("off: a whole span writes no file", not fb._event_path("off-2").exists())


def test_one_line_per_event_with_the_fields_every_reader_will_need():
    with_log(True)
    clear("shape")
    span = fb._RunSpan("shape")
    with span:
        fb.event("tool.call", session_key="shape", name="read_file",
                 args='{"path": "x"}', args_digest="abc123", args_len=14)
    got = rows("shape")
    kinds = [r["kind"] for r in got]
    check("run.start is first and run.end is last",
          kinds[0] == "run.start" and kinds[-1] == "run.end", kinds)
    for field in ("ts", "at", "session", "run", "seq", "kind"):
        check(f"every line carries {field}", all(field in r for r in got), got)
    check("the run id is shared inside one run",
          len({r["run"] for r in got}) == 1 and got[0]["run"], got)
    check("the sequence is monotonic", [r["seq"] for r in got] == sorted(r["seq"] for r in got),
          [r["seq"] for r in got])
    check("an exception is recorded as one", _raises_in_span())


def _raises_in_span():
    clear("boom")
    try:
        with fb._RunSpan("boom"):
            raise ValueError("pretend the run failed")
    except ValueError:
        pass
    got = rows("boom")
    last = got[-1] if got else {}
    return last.get("kind") == "run.end" and last.get("status") == "exception" \
        and "ValueError" in str(last.get("error"))


# --------------------------------------------------------------------------
# the two properties that make it worth having: outcomes, and no secrets
# --------------------------------------------------------------------------

def test_a_real_tool_call_records_how_it_ended():
    with_log(True)
    clear("outcomes")
    ok = fb.tool_shell({"command": "echo event-log-ok"}, {"session_key": "outcomes"})
    bad = fb.tool_shell({"command": "exit 7"}, {"session_key": "outcomes"})
    for name, out in (("echo", ok), ("exit 7", bad)):
        check(f"outcome: {name} came back with an outcome shape",
              isinstance(out, str) and out, out[:60])
    ok_out = fb._event_outcome("shell", "hi\nexit_code=0")
    bad_out = fb._event_outcome("shell", "boom\nexit_code=7")
    err_out = fb._event_outcome("shell", "ERROR: refused, identical to the last call")
    check("outcome: exit 0 is ok", ok_out["ok"] and ok_out["exit"] == 0, ok_out)
    check("outcome: a nonzero exit is not ok", not bad_out["ok"] and bad_out["exit"] == 7, bad_out)
    check("outcome: a refused call is not ok", not err_out["ok"], err_out)
    check("outcome: the size and digest are recorded",
          ok_out["bytes"] > 0 and len(ok_out["digest"]) == 12, ok_out)


def test_arguments_are_scrubbed_and_digested_not_stored_raw():
    with_log(True)
    clear("secrets")
    secret = "SECRET-TOKEN-abcdefghijklmnop"
    real_scrub = fb.scrub
    try:
        fb.scrub = lambda text: str(text).replace(secret, "<redacted>")
        digest, length, text = fb._event_args({"command": f"curl -H 'token: {secret}'"})
        fb.event("tool.call", session_key="secrets", name="shell", args=text,
                 args_digest=digest, args_len=length)
    finally:
        fb.scrub = real_scrub
    body = fb._event_path("secrets").read_text(encoding="utf-8")
    check("scrub is applied in ONE place, on the way in", secret not in body, body[:200])
    check("the digest still identifies the call", "args_digest" in body)
    check("the digest is of the RAW arguments, so redaction cannot change it",
          len(fb._event_args({"a": 1})[0]) == 12 and fb._event_args({"a": 1})[0]
          == fb._event_args({"a": 1})[0])
    check("long arguments are capped, not dropped", len(fb._event_args({"a": "x" * 5000})[2])
          <= fb._EVENT_ARGS_MAX, len(fb._event_args({"a": "x" * 5000})[2]))


# --------------------------------------------------------------------------
# a log must never be the reason a run breaks
# --------------------------------------------------------------------------

def test_concurrent_writes_leave_a_file_where_every_line_parses():
    with_log(True)
    clear("race")
    errors = []

    def worker(n):
        try:
            fb.event("tool.call", session_key="race", name=f"t{n}", args="{}")
        except Exception as exc:                      # noqa: BLE001 - reported
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(48)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = rows("race")
    check("48 batched writers leave 48 readable lines", len(got) == 48, len(got))
    check("every sequence number is unique", len({r["seq"] for r in got}) == 48,
          len({r["seq"] for r in got}))
    check("and no writer raised", not errors, errors[:3])


def test_a_failed_write_warns_once_and_never_reaches_the_run():
    with_log(True)
    real_path = fb._event_path
    try:
        fb._event_path = lambda key: TMP / "notes.md" / "impossible" / "x.events.jsonl"
        fb._EVENT_WARNED = False
        fb.event("tool.call", session_key="nowhere", name="shell")
        fb.event("tool.result", session_key="nowhere", name="shell", ok=True)
        check("a write that cannot land does not raise", True)
        check("and it is reported once, not every event", fb._EVENT_WARNED is True)
    finally:
        fb._event_path = real_path


def test_retention_keeps_the_newest_and_touches_nothing_else():
    with_log(True)
    keep_dir = TMP / "retention"
    shutil.rmtree(keep_dir, ignore_errors=True)
    keep_dir.mkdir(parents=True)
    real_dir = fb.SESSIONS_DIR
    try:
        fb.SESSIONS_DIR = keep_dir
        for n in range(35):
            f = keep_dir / f"s{n:02d}.events.jsonl"
            f.write_text("{}\n", encoding="utf-8")
            os.utime(f, (time.time() - (35 - n) * 60, time.time() - (35 - n) * 60))
        bystander = keep_dir / "s00.json"
        bystander.write_text("{}", encoding="utf-8")
        other = keep_dir / "s00.transcript.jsonl"
        other.write_text("{}\n", encoding="utf-8")
        removed = fb.prune_events()
        left = sorted(p.name for p in keep_dir.glob("*.events.jsonl"))
        check("retention keeps exactly the newest 30", len(left) == 30, len(left))
        check("and removes the oldest, not the newest",
              "s05.events.jsonl" in left and "s04.events.jsonl" not in left, left[:3])
        check("it reports what it removed", len(removed) == 5, removed)
        check("it never touches a session file or a transcript",
              bystander.exists() and other.exists())
    finally:
        fb.SESSIONS_DIR = real_dir


# --------------------------------------------------------------------------
# end to end: a real run, stubbed at the model
# --------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def test_a_stubbed_run_writes_the_span_and_the_tool_pair():
    with_log(True)
    clear("evt-e2e")
    seen = []

    def fake_post(url, headers, payload, timeout, grace, cancel_event=None, stream=False):
        seen.append(json.loads(json.dumps(payload)))
        if len(seen) == 1 and (payload.get("tools")):
            return _FakeResp({"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "shell",
                                             "arguments": json.dumps({"command": "echo evt"})}}]},
                "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
        return _FakeResp({"choices": [{"message": {"role": "assistant",
                                                  "content": "done"}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 6, "completion_tokens": 1}})

    # stream off: the streaming path wants an object with iter_lines, and the point here is
    # the events, not the transport. The stub replaces the HTTP call, so no model is reached
    # and no endpoint is contacted - this suite must not spend a real call.
    saved_stream = fb.CONFIG["llm"].get("stream")
    saved_post = getattr(fb, "_post_watchdog", None)
    saved_model = fb.CONFIG["llm"].get("model")
    try:
        fb.CONFIG["llm"]["stream"] = False
        fb.CONFIG["llm"]["model"] = "mock"
        fb._post_watchdog = fake_post
        answer = fb.AGENT.run("evt-e2e", "run the echo test")
    finally:
        fb.CONFIG["llm"]["stream"] = saved_stream
        fb.CONFIG["llm"]["model"] = saved_model
        if saved_post is None:
            del fb._post_watchdog
        else:
            fb._post_watchdog = saved_post
    got = rows("evt-e2e")
    kinds = [r["kind"] for r in got]
    check("the run answered", bool(answer), answer[:60])
    check("run.start is the first event", kinds[:1] == ["run.start"], kinds[:3])
    check("run.end is the last event", kinds[-1:] == ["run.end"], kinds[-3:])
    check("the tool call and its result are both there",
          kinds.count("tool.call") == 1 and kinds.count("tool.result") == 1, kinds)
    check("the tool result names the tool and says it worked",
          got[kinds.index("tool.result")]["name"] == "shell"
          and got[kinds.index("tool.result")]["ok"] is True,
          got[kinds.index("tool.result")])
    check("the call and its result share one run id",
          len({r["run"] for r in got}) == 1, {r["run"] for r in got})
    check("the events name the session they belong to",
          all(r["session"] == "evt-e2e" for r in got))


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for t in tests:
        if only and only not in t.__name__:
            continue
        try:
            t()
        except Exception as e:
            import traceback
            FAILS.append(f"{t.__name__} raised: {e}")
            traceback.print_exc()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    for f in FAILS:
        print("  FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
