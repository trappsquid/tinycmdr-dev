"""Tests for the v1.9.3 check-ins: the model's own narration (interim_cb) and the
harness-side per-tool lines (progress_done_cb).

Run:  python tests/test_checkin.py            (plain python, no pytest needed)
      python -m pytest -q tests/test_checkin.py
Same shape as tests/test_ledger.py: imports the live tinycmdr.py as a module,
redirects every file it writes into a temp dir, no Mattermost connection.
"""
import copy
import importlib.util
import json
import os
import sys
import tempfile
import atexit
import shutil
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# Which build to import: the Mattermost bot by default, the chatless CLI build
# with tinycmdr_SRC=tinycmdr-cli.py (that build has no chat layer to fake).
SRC = BASE / os.environ.get("tinycmdr_SRC", "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_checkin_under_test",
                                              SRC)
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_checkin_under_test"] = fb
spec.loader.exec_module(fb)

TMP = Path(tempfile.mkdtemp(prefix="fbcheckin-"))
# Clean the scratch dir on exit: running the suites on a fresh host
# should not leave a directory behind for every run.
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))
PRISTINE = copy.deepcopy(fb.CONFIG)
FAILURES = []
PASSES = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def reset_config():
    fb.CONFIG.clear()
    fb.CONFIG.update(copy.deepcopy(PRISTINE))


def redirect_files():
    reset_config()
    fb.NOTES_FILE = TMP / "notes.md"
    fb.NOTES_ARCHIVE_FILE = TMP / "notes-archive.md"
    fb.TASKS_FILE = TMP / "tasks.json"
    fb.TASKS_DOC = TMP / "tasks.md"
    fb.SESSIONS_DIR = TMP / "sessions"
    fb.SESSIONS_DIR.mkdir(exist_ok=True)
    fb.NOTES_FILE.write_text("", encoding="utf-8")
    for f in (fb.NOTES_ARCHIVE_FILE, fb.TASKS_FILE, fb.TASKS_DOC):
        if f.exists():
            f.unlink()


class FakeDispatcher:
    """Records _post/_edit instead of talking to Mattermost."""

    def __init__(self):
        self.posts = []
        self.colors = []
        self.ids = []
        self.edits = []
        self.edit_colors = []
        self.deletes = []
        self._n = 0

    def _post(self, channel_id, root_id, text, color=None):
        self._n += 1
        pid = f"post{self._n}"
        self.posts.append((channel_id, root_id, text))
        self.colors.append(color)
        self.ids.append(pid)
        return pid

    def _edit(self, post_id, channel_id, text, color=None):
        self.edits.append((post_id, channel_id, text))
        self.edit_colors.append(color)
        return True

    def _delete(self, post_id, channel_id):
        self.deletes.append((post_id, channel_id))
        return True


def reporter(**cfg):
    """A ProgressReporter on a fake dispatcher; the '🔧 Working…' line is dropped
    so assertions see only what the check-ins add."""
    redirect_files()
    fb.CONFIG["agent"].update(cfg)
    d = FakeDispatcher()
    rep = fb.ProgressReporter(d, "chan", "root", "sess")
    d.posts.clear()          # drop the '🔧 Working…' status line
    d.ids.clear()
    return d, rep


# --------------------------------------------------------------------------
# the previews and exit codes the harness reads off a tool call
# --------------------------------------------------------------------------

def test_tool_preview_takes_first_line():
    prev = fb._tool_preview("shell", {"command": "Get-Item C:/x\nRemove-Item y"})
    check("preview is the first line only", prev == "Get-Item C:/x", prev)
    long_cmd = "x" * 500
    prev = fb._tool_preview("shell", {"command": long_cmd}, limit=20)
    check("preview is truncated", len(prev) <= 21 and prev.endswith("…"), prev)
    prev = fb._tool_preview("read_file", {"path": "C:/a/b.txt"})
    check("path tools preview the path", prev == "C:/a/b.txt", prev)
    prev = fb._tool_preview("weird_tool", {"alpha": 1, "beta": 2})
    check("other tools preview their args", prev == "alpha=1, beta=2", prev)


def test_tool_preview_scrubs_secrets():
    fb._SECRETS.add("HUNTER2SECRET")
    try:
        prev = fb._tool_preview("shell", "{\"command\": \"curl -H 'k: HUNTER2SECRET'\"}")
    finally:
        fb._SECRETS.discard("HUNTER2SECRET")
    check("preview scrubs secrets", "HUNTER2SECRET" not in prev, prev)


def test_exit_code_parsing():
    check("exit_code=1 parsed", fb._exit_code("exit_code=1\nerr") == 1)
    check("exit_code=0 parsed", fb._exit_code("exit_code=0\nok") == 0)
    check("no exit code -> None", fb._exit_code("just output") is None)
    check("None output is safe", fb._exit_code(None) is None)


# --------------------------------------------------------------------------
# the model's own narration
# --------------------------------------------------------------------------

def test_note_posts_its_own_message():
    d, rep = reporter(checkin_note_min_seconds=0)
    rep.note("Checking what holds the file lock:")
    check("narration is one new message", len(d.posts) == 1, d.posts)
    check("narration is marked and verbatim",
          d.posts[0][2] == "💬 Checking what holds the file lock:", d.posts)
    check("narration is threaded on the root", d.posts[0][1] == "root", d.posts)


def test_note_throttle_and_cap():
    d, rep = reporter(checkin_note_min_seconds=60)
    rep.note("first")
    rep.note("second — inside the throttle window")
    check("throttled narration is dropped", len(d.posts) == 1, d.posts)
    d, rep = reporter(checkin_note_chars=20, checkin_note_min_seconds=0)
    rep.note("y" * 200)
    check("narration is capped", len(d.posts[0][2]) <= 25, d.posts[0][2])


# --------------------------------------------------------------------------
# the harness-side tool lines
# --------------------------------------------------------------------------

def test_launch_warning_only_where_a_cap_exists():
    """A model server started as a child joins THIS unit's cgroup, so its load kills
    the agent (a bot account 17:09: 33.4 GiB inside tinycmdr.service)."""
    cmd = "llama-server -m /mnt/models/x.gguf --host 0.0.0.0"
    if not fb._self_mem_cap_mb():
        check("no cgroup ceiling on this host -> no warning",
              fb._launch_warning(cmd) == "", fb._launch_warning(cmd))
    warned = fb._launch_warning(cmd, cap_mb=32768)
    check("with a 32 GiB cap the launch is called out",
          "CHILD of me" in warned and "systemd-run" in warned, warned)
    plain = fb._launch_warning("mkdir -p /tmp/x && ls -la", cap_mb=32768)
    check("an ordinary command is not warned about", plain == "", plain)


def test_cgroup_memory_and_probe():
    cg = fb._cgroup_mem_mb()
    if fb.IS_WINDOWS or sys.platform == "darwin":
        # Neither has cgroup accounting, and "unknown ceiling" is the honest answer.
        check("no cgroup accounting on this platform", cg is None, cg)
    else:
        # These readers follow /proc/self/cgroup (before 2.5.19 they read the cgroup ROOT,
        # which does not exist on a systemd host, so both were blind there). What matters is
        # that the path resolved is THIS process's own scope, and that the number is never
        # the whole machine's: in a plain shell the scope may expose accounting or not.
        own = fb._own_cgroup_dir()
        check("the memory readers follow this process's own cgroup, not the root",
              bool(own) and own.startswith("/sys/fs/cgroup/") and own.count("/") >= 4, own)
        check("and the figure is a scope's, never the whole box",
              cg is None or cg < 8192, cg)
    check("the memory probe stays off while the process is small",
          fb._mem_probe() is None, fb._mem_probe())


def test_checkin_shows_memory():
    """The climb that ends in an OOM kill has to be visible in chat, not only in
    journalctl after four kills (a bot account, 2026-09-19)."""
    d, rep = reporter()
    txt = rep.checkin_text(3, 61.0, "shell", {"command": "x"})
    check("the periodic line carries RAM", "RAM" in txt, txt)
    rss = fb._self_rss_mb()
    check("this process's RSS is readable on this host",
          bool(rss) and rss > 0, rss)
    check("the line renders the ceiling when the OS exposes one",
          fb.mem_line().startswith("RAM"), fb.mem_line())


def test_tool_line_single_call():
    d, rep = reporter()
    rep.tool_done("shell", {"command": "Get-PSDrive C"}, "exit_code=0\nfree", 0.4)
    check("one tool call -> one new message", len(d.posts) == 1 and not d.edits,
          (d.posts, d.edits))
    body = d.posts[0][2]
    check("line names the tool and the command", "`shell`" in body
          and "Get-PSDrive C" in body, body)
    check("line shows the duration", "0.4s" in body, body)
    check("exit 0 is not shouted about", "exit" not in body, body)


def test_tool_line_flags_nonzero_exit():
    d, rep = reporter()
    rep.tool_done("shell", {"command": "rmdir release"}, "exit_code=1\nbusy", 2.6)
    body = d.posts[0][2]
    check("failed command shows its exit code", "[exit 1]" in body, body)


def test_tool_line_shows_why_a_call_failed():
    """A red line carrying only an exit code sends the operator to the host log to
    find out what happened, which is the whole reason this exists."""
    d, rep = reporter()
    rep.tool_done("shell", {"command": "cat /etc/shadow"},
                  "exit_code=1\ncat: /etc/shadow: Permission denied", 1.2)
    body = d.posts[0][2]
    check("a failure shows its reason", "Permission denied" in body, body)
    check("the exit code stays", "[exit 1]" in body, body)


def test_tool_line_stays_quiet_on_success():
    d, rep = reporter()
    rep.tool_done("shell", {"command": "ls"}, "exit_code=0\nlots of good output", 0.3)
    body = d.posts[0][2]
    check("a successful call shows no output", "good output" not in body, body)
    check("a successful call gets no reason", " — " not in body, body)


def test_the_failure_reason_is_one_clean_line():
    snip = fb._failure_snippet("exit_code=2\nfirst real line\nsecond line")
    check("the reason is the first real line", snip == "first real line", snip)
    long_line = "x" * 400
    snip = fb._failure_snippet("exit_code=1\n" + long_line)
    check("a long reason is capped", len(snip) == 161 and snip.endswith("…"), len(snip))
    snip = fb._failure_snippet("exit_code=1\nweird `tick` line\nmore")
    check("backticks are stripped (the batch text is not a fence)",
          "`" not in snip, snip)
    fb._SECRETS.add("HUNTER3SECRET")
    try:
        snip = fb._failure_snippet("exit_code=1\nleaked HUNTER3SECRET here")
    finally:
        fb._SECRETS.discard("HUNTER3SECRET")
    check("the reason is scrubbed", "HUNTER3SECRET" not in snip, snip)
    check("clean output produces no reason at all",
          fb._failed_call("just output")[1] == "", fb._failed_call("just output"))
    check("empty output is safe", fb._failed_call(None)[1] == "", "raised or returned")


def test_marker_failures_show_their_reason_too():
    """Tools with no exit code still fail; the harness's own verdict words are the
    signal, and they are exactly the cases where the reason matters."""
    d, rep = reporter()
    rep.tool_done("edit_file", {"path": "C:/x"}, "ERROR: path does not exist", 0.1)
    check("a harness ERROR shows its reason",
          "path does not exist" in d.posts[0][2], d.posts[0][2])
    d, rep = reporter()
    rep.tool_done("shell", {"command": "sleep 900"}, "TIMEOUT after 300s — killed", 300.2)
    check("a TIMEOUT shows its reason",
          "TIMEOUT after 300s" in d.posts[0][2], d.posts[0][2])


def test_tool_lines_merge_into_one_message():
    d, rep = reporter(checkin_tool_merge_seconds=60)
    for i in range(3):
        rep.tool_done("shell", {"command": f"step-{i}"}, "exit_code=0", 0.2)
    check("a batch is ONE message", len(d.posts) == 1, d.posts)
    check("the batch grows by edit", len(d.edits) == 2, d.edits)
    final = d.edits[-1][2]
    check("the batch counts its calls", "3 tool calls" in final, final)
    check("every call is listed", all(f"step-{i}" in final for i in range(3)), final)
    check("edits carry the same message id",
          {e[0] for e in d.edits} == {d.ids[0]}, d.edits)


def test_tool_lines_cap_starts_new_message():
    d, rep = reporter(checkin_tool_merge_seconds=60, checkin_tool_max_lines=2)
    for i in range(3):
        rep.tool_done("shell", {"command": f"step-{i}"}, "exit_code=0", 0.2)
    check("cap forces a second message", len(d.posts) == 2, d.posts)
    check("second message starts a fresh batch", "step-2" in d.posts[1][2], d.posts[1][2])


def test_switches_silence_everything():
    d, rep = reporter(checkin_per_tool=False, checkin_notes=False)
    rep.tool_done("shell", {"command": "x"}, "exit_code=0", 1.0)
    rep.note("narrating anyway")
    check("both channels off -> silence", not d.posts and not d.edits, (d.posts, d.edits))
    d, rep = reporter(progress_updates=False)
    rep.tool_done("shell", {"command": "x"}, "exit_code=0", 1.0)
    rep.note("narrating anyway")
    check("progress_updates off -> silence too",
          not d.posts and not d.edits, (d.posts, d.edits))


# --------------------------------------------------------------------------
# wiring: run() hands both callbacks what they need
# --------------------------------------------------------------------------

def scripted_run(seq, **cfg):
    """Drive Agent.run against a scripted _chat, recording both callbacks."""
    redirect_files()
    fb.AGENT.histories.clear()
    fb.AGENT.model_overrides.clear()
    fb.AGENT.last_usage.clear()
    saved_chat = fb.AGENT._chat
    saved_cfg = copy.deepcopy(fb.CONFIG["agent"])
    fb.CONFIG["agent"].update(cfg)
    seq = list(seq)
    events = []

    def fake_chat(messages, model=None, use_tools=True, usage=None, max_tokens=None,
                  cancel_event=None, on_delta=None, session_key=None):
        return seq.pop(0)

    fb.AGENT._chat = fake_chat
    try:
        out = fb.AGENT.run(
            "checkin-session", "check the box",
            interim_cb=lambda t: events.append(("note", t)),
            progress_done_cb=lambda n, a, o, e: events.append(
                ("tool", n, round(e, 3), str(o)[:60])))
    finally:
        fb.AGENT._chat = saved_chat
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved_cfg)
    return out, events


def test_run_announces_narration_before_tools():
    out, events = scripted_run([
        {"role": "assistant", "content": "Checking what holds the lock:",
         "tool_calls": [{"id": "1", "function": {
             "name": "shell",
             "arguments": json.dumps({"command": "echo checkin-probe"})}}]},
        {"role": "assistant", "content": "Nothing holds it."},
    ])
    check("narration arrives", ("note", "Checking what holds the lock:") in events, events)
    check("narration precedes the tool it introduces",
          events and events[0][0] == "note", events)
    tool_events = [e for e in events if e[0] == "tool"]
    check("each finished tool is reported once", len(tool_events) == 1, events)
    check("the tool event carries name + duration + output",
          tool_events[0][1] == "shell" and tool_events[0][2] >= 0
          and "checkin-probe" in tool_events[0][3], tool_events)
    check("the run still returns its answer", out == "Nothing holds it.", out)


def test_run_silent_without_narration():
    out, events = scripted_run([
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "1", "function": {
             "name": "shell",
             "arguments": json.dumps({"command": "echo quiet"})}}]},
        {"role": "assistant", "content": "Done."},
    ])
    check("no prose -> no note event", not [e for e in events if e[0] == "note"], events)
    check("the tool line still fires (model-independent)",
          [e for e in events if e[0] == "tool"], events)
    check("answer intact", out == "Done.", out)


def test_run_works_without_the_new_callbacks():
    """Default kwargs must stay optional — the web/SSE path passes neither."""
    redirect_files()
    fb.AGENT.histories.clear()
    saved_chat = fb.AGENT._chat
    seq = [{"role": "assistant", "content": "All good."}]
    fb.AGENT._chat = lambda messages, model=None, use_tools=True, usage=None, \
        max_tokens=None, cancel_event=None, on_delta=None, session_key=None: seq.pop(0)
    try:
        out = fb.AGENT.run("callbacks-off", "say hi")
    finally:
        fb.AGENT._chat = saved_chat
    check("run() still works with no callbacks", out == "All good.", out)


# -------------------------------- v2.0.1: an empty turn is asked once more

def scripted_run_with_usage(seq, session="empty-turn"):
    """Drive Agent.run against a scripted _chat that fills `usage` the way the
    real one does (finish_reason + last_reasoning_chars). Returns (out, seen),
    where `seen` is the messages list of every call, shallow-copied."""
    redirect_files()
    fb.AGENT.histories.clear()
    fb.AGENT.model_overrides.clear()
    fb.AGENT.last_usage.clear()
    saved_chat = fb.AGENT._chat
    replies = list(seq)
    seen = []

    def fake_chat(messages, model=None, use_tools=True, usage=None, max_tokens=None,
                  cancel_event=None, on_delta=None, session_key=None):
        seen.append([dict(m) for m in messages])
        reply = replies.pop(0)
        if isinstance(usage, dict):
            rc = reply.get("reasoning_content") or ""
            usage["calls"] = usage.get("calls", 0) + 1
            usage["finish_reason"] = reply.get("finish_reason") or "stop"
            usage["last_reasoning_chars"] = len(rc)
        return {k: v for k, v in reply.items() if k != "finish_reason"}

    fb.AGENT._chat = fake_chat
    try:
        out = fb.AGENT.run(session, "carry on with the job")
    finally:
        fb.AGENT._chat = saved_chat
    return out, seen


def test_empty_turn_is_retried_and_the_run_continues():
    """A reasoning model that ends its own turn with no answer (finish=stop) used
    to stop the run dead: the operator got a warning and had to prompt again.
    Asking the same turn one more time is one call, and keeps the work moving."""
    out, seen = scripted_run_with_usage([
        {"role": "assistant", "content": "", "reasoning_content": "Hmm, let me"},
        {"role": "assistant", "content": "Next I read the second log."},
    ])
    check("empty turn: the run does not end on the empty answer",
          out == "Next I read the second log.", out)
    check("empty turn: the model was asked again (2 calls)", len(seen) == 2, len(seen))
    if len(seen) == 2:
        check("empty turn: the empty assistant turn is dropped before the retry",
              all(not (m.get("role") == "assistant" and not m.get("content")
                       and not m.get("tool_calls")) for m in seen[1]), seen[1])
        check("empty turn: the retry ends with a plain user nudge",
              seen[1][-1].get("role") == "user"
              and "EMPTY" in str(seen[1][-1].get("content")), seen[1][-1])


def test_two_empty_turns_end_the_run_with_honest_text():
    """One retry, then tell the operator. A machine that answers nothing twice is
    not going to answer on the third try, and retrying forever burns the clock."""
    out, seen = scripted_run_with_usage([
        {"role": "assistant", "content": "", "reasoning_content": "Hmm, let me"},
        {"role": "assistant", "content": "", "reasoning_content": "Hmm again"},
    ])
    check("two empty turns: exactly one retry", len(seen) == 2, len(seen))
    check("two empty turns: the warning comes back", "no answer" in out, out)
    check("two empty turns: it says it asked twice", "twice" in out, out)
    check("two empty turns: it does not send the operator to llm.no_think",
          "set llm.no_think: true" not in out, out)


# ------------------------------------------- v1.9.29: streamed narration

def test_streamed_narration_posts_once_and_grows():
    """The whole point: ONE post that grows while the model writes, instead of one
    lump per call after it returns."""
    d = FakeDispatcher()
    rep = fb.ProgressReporter(d, "chan", "root", "sess")
    saved = copy.deepcopy(fb.CONFIG["agent"])
    fb.CONFIG["agent"].update(progress_updates=True, checkin_notes=True,
                              checkin_stream_notes=True, checkin_stream_seconds=0.0)
    try:
        rep.narration("I will check the lock")
        rep.narration("I will check the lock, then restart the service")
        rep.narration("I will check the lock, then restart the service, then verify it")
        # the reporter posts its status line in __init__, so filter for the 💬 post
        notes = [p for p in d.posts if p[2].startswith("💬 ")]
        check("streamed narration: one post, not one per delta", len(notes) == 1, d.posts)
        check("streamed narration: the post is the 💬 line", bool(notes), d.posts)
        check("streamed narration: later text EDITS that post",
              len(d.edits) == 2 and len({e[0] for e in d.edits}) == 1
              and all(e[2].startswith("💬 ") for e in d.edits), d.edits)
        check("streamed narration: the post shows the latest text",
              "verify it" in d.edits[-1][2], d.edits[-1][2])
    finally:
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)


def test_streamed_narration_respects_its_gap_but_final_always_lands():
    d = FakeDispatcher()
    rep = fb.ProgressReporter(d, "chan", "root", "sess")
    saved = copy.deepcopy(fb.CONFIG["agent"])
    fb.CONFIG["agent"].update(progress_updates=True, checkin_notes=True,
                              checkin_stream_notes=True, checkin_stream_seconds=30.0)
    try:
        rep.narration("first")
        rep.narration("first and second")
        check("streamed narration: edits inside the gap are skipped",
              not d.edits, d.edits)
        rep.narration("first and second and third", final=True)
        check("streamed narration: the final state is always written",
              len(d.edits) == 1 and "third" in d.edits[-1][2], d.edits)
    finally:
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)


def test_streamed_narration_is_dropped_when_it_was_the_answer():
    d = FakeDispatcher()
    rep = fb.ProgressReporter(d, "chan", "root", "sess")
    saved = copy.deepcopy(fb.CONFIG["agent"])
    fb.CONFIG["agent"].update(progress_updates=True, checkin_notes=True,
                              checkin_stream_notes=True, checkin_stream_seconds=0.0)
    try:
        rep.narration("Here is the answer, being written out")
        check("a streamed draft exists", rep.narration_live() is True, rep.narration_live())
        rep.narration_drop()
        narration_id = d.ids[[i for i, p in enumerate(d.posts)
                             if p[2].startswith("💬 ")][0]]
        check("the draft is DELETED (the answer is posted by the caller)",
              d.deletes == [(narration_id, "chan")], d.deletes)
        check("the reporter forgets it", rep.narration_live() is False, rep.narration_live())
        rep.narration_drop()          # idempotent: nothing left to delete
        check("dropping twice deletes nothing more", len(d.deletes) == 1, d.deletes)
    finally:
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)


def test_streamed_narration_can_be_switched_off():
    d = FakeDispatcher()
    rep = fb.ProgressReporter(d, "chan", "root", "sess")
    saved = copy.deepcopy(fb.CONFIG["agent"])
    fb.CONFIG["agent"].update(progress_updates=True, checkin_notes=True,
                              checkin_stream_notes=False)
    try:
        rep.narration("should not appear anywhere")
        check("checkin_stream_notes=false posts nothing",
              not [p for p in d.posts if p[2].startswith("💬 ")] and not d.edits,
              (d.posts, d.edits))
    finally:
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)


def test_run_does_not_double_post_a_streamed_line():
    """The streamed narration IS the 💬 line: run() must not post it again via
    interim_cb. And when the streamed text turns out to be the ANSWER (no tool
    calls), the draft must be dropped instead of duplicating the answer."""
    d = FakeDispatcher()
    rep = fb.ProgressReporter(d, "chan", "root", "sess")
    saved_chat = fb.AGENT._chat
    saved_cfg = copy.deepcopy(fb.CONFIG["agent"])
    fb.CONFIG["agent"].update(progress_updates=True, checkin_notes=True,
                              checkin_stream_notes=True, checkin_stream_seconds=0.0)
    notes, dropped = [], []

    def make_chat(reply_content, tool_calls=None):
        def fake_chat(messages, model=None, use_tools=True, usage=None,
                      max_tokens=None, cancel_event=None, on_delta=None,
                      session_key=None):
            if on_delta:
                # what the SSE path does while the model writes
                on_delta({"deltas": 1, "chars": len(reply_content), "content": reply_content,
                          "ttft": 0.1, "tps": 10.0})
                on_delta({"deltas": 2, "chars": len(reply_content) + 8,
                          "content": reply_content + " and one more thing",
                          "ttft": 0.1, "tps": 10.0, "final": True})
            r = {"role": "assistant", "content": reply_content}
            if tool_calls:
                r["tool_calls"] = tool_calls
            return r
        return fake_chat

    plan = [{"id": "c1", "function": {"name": "list_tools", "arguments": "{}"}}]
    try:
        fb.AGENT._chat = make_chat("I will list the tools first:", plan)
        fb.AGENT.run("stream-notes", "do it", interim_cb=lambda t: notes.append(t),
                     narration_cb=rep.narration, narration_drop_cb=rep.narration_drop,
                     progress_cb=None)
        notes_posts = [p for p in d.posts if p[2].startswith("💬 ")]
        check("streamed plan: the narration was streamed",
              len(notes_posts) == 1
              and notes_posts[0][2].startswith("💬 I will list the tools"), d.posts)
        check("streamed plan: interim_cb did NOT post it a second time",
              not notes, notes)
        check("streamed plan: the draft is kept (it was the plan)",
              rep.narration_live() is True, rep.narration_live())
        check("streamed plan: no delete happened",
              not d.deletes, d.deletes)

        d.posts.clear(); d.edits.clear(); d.deletes.clear(); notes.clear()
        fb.AGENT._chat = make_chat("The answer, written out live.")
        fb.AGENT.run("stream-answer", "do it", interim_cb=lambda t: notes.append(t),
                     narration_cb=rep.narration, narration_drop_cb=rep.narration_drop,
                     progress_cb=None)
        check("streamed answer: the draft was deleted, not left as a duplicate",
              len(d.deletes) == 1, d.deletes)
        check("streamed answer: nothing was left live",
              rep.narration_live() is False, rep.narration_live())
        check("streamed answer: interim_cb was not used either", not notes, notes)
    finally:
        fb.AGENT._chat = saved_chat
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved_cfg)



def test_each_turn_opens_its_own_narration_post():
    """A multi-step run must read as a SEQUENCE. The first live test reused one post, so
    every step replaced the previous step's text - visible in the chat as one line whose
    content kept changing, and useless for reading the plan."""
    d = FakeDispatcher()
    rep = fb.ProgressReporter(d, "chan", "root", "sess")
    saved = copy.deepcopy(fb.CONFIG["agent"])
    fb.CONFIG["agent"].update(progress_updates=True, checkin_notes=True,
                              checkin_stream_notes=True, checkin_stream_seconds=0.0)
    try:
        rep.narration("Step one: check the date", new_turn=True)
        rep.narration("Step one: check the date, then the free space", final=True)
        rep.narration("Step two: list the tinycmdr folder", new_turn=True)
        rep.narration("Step two: list the tinycmdr folder", final=True)
        notes = [p for p in d.posts if p[2].startswith("💬 ")]
        check("a new turn opens a new post", len(notes) == 2, d.posts)
        first_id = d.ids[d.posts.index(notes[0])]
        first_edits = [e[2] for e in d.edits if e[0] == first_id]
        check("the previous step's line is left complete",
              first_edits and "free space" in first_edits[-1], first_edits)
        check("the new post carries the new step", "Step two" in notes[1][2], notes[1][2])
        check("edits stayed within their own post",
              len({e[0] for e in d.edits}) == 1, d.edits)
    finally:
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)




# --------------------------------------------- colour: telling the lines apart (v1.9.30)

def test_every_line_says_what_it_is_by_colour():
    """The operator's ask: the noise is now colour-differentiated rather than larger.
    Green = the model narrating what it is about to do, red = a tool call that ran,
    white = harness status / Done summary. Each role's bar is pinned by exact colour."""
    d = FakeDispatcher()
    saved = copy.deepcopy(fb.CONFIG["agent"])
    fb.CONFIG["agent"].update(progress_updates=True, checkin_notes=True,
                              checkin_stream_notes=True, checkin_stream_seconds=0.0,
                              checkin_per_tool=True, color_coded=True)
    try:
        rep = fb.ProgressReporter(d, "chan", "root", "sess")
        check("the working line is barred red",
              d.colors[0] == fb.COLOR_TOOL, d.colors)
        rep.narration("checking what holds the lock:", new_turn=True)
        check("narration is barred green",
              d.colors[-1] == fb.COLOR_NARRATION, d.colors)
        rep.narration("checking what holds the lock, and the port:", final=True)
        check("a growing narration keeps its green on the edit",
              d.edit_colors[-1] == fb.COLOR_NARRATION, d.edit_colors)
        rep.tool_done("shell", {"command": "ls"}, "ok\n", 0.4)
        check("a tool call is barred red", d.colors[-1] == fb.COLOR_TOOL, d.colors)
        rep.finish()
        check("Done turns the bar white",
              d.edit_colors[-1] == fb.COLOR_STATUS, d.edit_colors)
        check("nothing was posted without a colour that should have one",
              None not in d.colors, d.colors)
    finally:
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)


def test_color_off_posts_plain_text_again():
    """The kill switch has to be complete: one config flag and the channel looks
    exactly like pre-1.9.30, with no half-barred lines left behind."""
    d = FakeDispatcher()
    saved = copy.deepcopy(fb.CONFIG["agent"])
    fb.CONFIG["agent"].update(progress_updates=True, checkin_notes=True,
                              checkin_stream_notes=True, checkin_stream_seconds=0.0,
                              checkin_per_tool=True, color_coded=False)
    try:
        rep = fb.ProgressReporter(d, "chan", "root", "sess")
        rep.narration("about to look", new_turn=True)
        rep.tool_done("shell", {"command": "ls"}, "ok\n", 0.4)
        rep.finish()
        check("no post carries a colour", set(d.colors) == {None}, d.colors)
        check("no edit carries a colour", set(d.edit_colors) == {None}, d.edit_colors)
    finally:
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)


def test_a_reply_that_is_not_progress_stays_unbarred():
    """Command replies and the final answer must stay plain: after a run of coloured
    lines, an unbarred post is what says 'this is the payload, not more noise'."""
    d = FakeDispatcher()
    d._post("chan", None, "/version reply")
    check("a plain post has no colour", d.colors == [None], d.colors)
    check("and want_color(None) stays None", fb.want_color(None) is None, None)


def test_the_bar_shape_is_exactly_what_mattermost_renders():
    """The contract with the server: a post carries Slack-style attachments and the
    colour lives on each attachment. Verified against the real server before this was
    built, and pinned here so a refactor cannot quietly change the shape."""
    props = fb.bar_props("💬 about to check the lock", fb.COLOR_NARRATION)
    check("props shape", props == {"attachments": [
        {"color": "#2ecc71", "text": "💬 about to check the lock"}]}, props)
    check("the palette is the one the operator approved",
          (fb.COLOR_NARRATION, fb.COLOR_TOOL, fb.COLOR_STATUS)
          == ("#2ecc71", "#f1c40f", "#ffffff"),
          (fb.COLOR_NARRATION, fb.COLOR_TOOL, fb.COLOR_STATUS))
    check("red is reserved for failures",
          fb.COLOR_FAIL == "#e74c3c", fb.COLOR_FAIL)




def test_red_is_only_for_failures():
    """Amber means a tool call ran; red is kept for the two cases where something is
    actually wrong - a call that exited non-zero, and a run that ended badly - so a red
    bar is never a false alarm."""
    d = FakeDispatcher()
    saved = copy.deepcopy(fb.CONFIG["agent"])
    fb.CONFIG["agent"].update(progress_updates=True, checkin_notes=True,
                              checkin_stream_notes=True, checkin_stream_seconds=0.0,
                              checkin_per_tool=True, checkin_tool_merge_seconds=0.0,
                              # cap 1: every call is its own post. Two calls in the same
                              # clock tick would otherwise merge into one batch (Windows
                              # time.time() granularity), which is real behaviour but not
                              # what this test is pinning.
                              checkin_tool_max_lines=1, color_coded=True)
    try:
        rep = fb.ProgressReporter(d, "chan", "root", "sess")
        d.colors.clear()
        rep.tool_done("shell", {"command": "ls"}, "fine\nexit_code=0", 0.3)
        check("a tool call that ran is amber", d.colors[-1] == fb.COLOR_TOOL, d.colors)
        rep.tool_done("shell", {"command": "false"}, "boom\nexit_code=1", 0.3)
        check("a call that exited non-zero is red", d.colors[-1] == fb.COLOR_FAIL,
              d.colors)

        d.edit_colors.clear()
        fb.ProgressReporter(d, "chan", "root", "sess").finish(ok=False)
        check("a run that ended badly is red", d.edit_colors[-1] == fb.COLOR_FAIL,
              d.edit_colors)
        fb.ProgressReporter(d, "chan", "root", "sess").finish(ok=True)
        check("a clean run's Done is white", d.edit_colors[-1] == fb.COLOR_STATUS,
              d.edit_colors)
    finally:
        fb.CONFIG["agent"].clear()
        fb.CONFIG["agent"].update(saved)


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
            FAILURES.append(f"{t.__name__} raised: {e}")
            traceback.print_exc()
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print("  FAIL:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
