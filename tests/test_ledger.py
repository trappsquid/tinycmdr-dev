"""Tests for the v1.9.0 ledger + transport work.

Run:  python tests/test_ledger.py        (the whole gate: python tests/run_all.py)
They import the live tinycmdr.py as a module (no Mattermost connection, no
scheduled jobs, no lock) and redirect every file it writes at a temp dir.
"""
import copy
import importlib.util
import json
import os
import re
import socket
import sys
import tempfile
import threading
import atexit
import shutil
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# This suite imports the bot build.
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

# --- hermetic staging -------------------------------------------------------
# config.json is written by the installer, so it is NOT in the shipped package,
# and the module refuses to start without one. These suites must run against a
# fresh unpack (CI, a friend's box, a stranger's download), so import a
# byte-identical copy from a temp dir that HAS a config.json beside it.
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-ledger"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
FIXTURE_CFG = STAGE / "config.json"
# The fixture is a shipped file, not a dict buried in this suite: it is the
# sanitized worked example of a complete config.json, it is what the suites
# actually run against, and having one copy of it stops the two suites drifting.
FIXTURE_SRC = Path(__file__).resolve().parent / "fixture-config.json"
if not FIXTURE_SRC.exists():
    sys.exit(f"missing test fixture: {FIXTURE_SRC} (it ships in tests/)")
shutil.copy2(FIXTURE_SRC, FIXTURE_CFG)

spec = importlib.util.spec_from_file_location("tinycmdr_under_test",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_under_test"] = fb
spec.loader.exec_module(fb)

TMP = Path(tempfile.mkdtemp(prefix="fbtest-"))
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
    # A damaged-ledger test archives the file it salvaged; TMP is shared across
    # this whole suite, so a leftover would make the next test's "nothing was
    # kept" assertion read as a failure.
    for f in TMP.glob("tasks.json.damaged-*"):
        f.unlink()
    for f in TMP.glob("*.tmp-*"):
        f.unlink()


def fresh_notes(text=""):
    fb.NOTES_FILE.write_text(text, encoding="utf-8")
    if fb.NOTES_ARCHIVE_FILE.exists():
        fb.NOTES_ARCHIVE_FILE.unlink()


# --------------------------------------------------------------------------
# notes: bounded at write time, evict to archive, mark every elision
# --------------------------------------------------------------------------

def test_remember_refuses_rather_than_clipping():
    """Finding 2 (audit, 2026-09-21) replaced "clip at the cap" with "refuse and say
    where the long version belongs", so this check is the NEW contract, not the old
    one: a mutilated fact rides in every future prompt and the model re-derives the
    rest, which is the redo pattern the finding came from."""
    redirect_files()
    fb.CONFIG["agent"]["notes_max_note_chars"] = 200
    fb.CONFIG["agent"]["notes_max_chars"] = 100000   # isolate this test
    out = fb.tool_remember({"note": "x" * 5000}, {})
    check("an extreme note is REFUSED, not clipped", out.startswith("ERROR"),
          out[:120])
    check("and the refusal names the per-note limit", "200" in out, out[:200])
    check("and says where long content belongs", "file" in out, out[:240])
    check("and no half-true fact was stored",
          fb.NOTES_FILE.read_text(encoding="utf-8").strip() == "",
          fb.NOTES_FILE.read_text(encoding="utf-8")[:80])
    sane = " ".join(f"fact-{i}" for i in range(30))     # inside the limit
    out = fb.tool_remember({"note": sane}, {})
    body = fb.NOTES_FILE.read_text(encoding="utf-8")
    check("a note inside the limit is stored whole, and nothing is clipped",
          out.startswith("OK") and sane in body and "clipped" not in out,
          out[:120])


def test_remember_rejects_empty():
    redirect_files()
    out = fb.tool_remember({"note": "   "}, {})
    check("empty note rejected", out.startswith("ERROR"), out)


def test_preamble_is_preserved():
    redirect_files()
    fb.NOTES_FILE.write_text(
        "# my hand-written header\nsome operator prose\n"
        "- [2026-09-01 10:00] first fact\n", encoding="utf-8")
    fb.CONFIG["agent"]["notes_keep_entries"] = 1
    fb.CONFIG["agent"]["notes_archive_days"] = 1
    fb.CONFIG["agent"]["notes_max_chars"] = 4000
    fb.curate_notes("test")
    body = fb.NOTES_FILE.read_text(encoding="utf-8")
    check("preamble survives curation", "# my hand-written header" in body
          and "some operator prose" in body, body)
    check("aged entry archived", "first fact" in
          fb.NOTES_ARCHIVE_FILE.read_text(encoding="utf-8"))


def test_curate_dedupes_and_marks():
    redirect_files()
    fb.CONFIG["agent"]["notes_keep_entries"] = 100
    fb.CONFIG["agent"]["notes_archive_days"] = 9999
    for _ in range(6):
        fb.NOTES_FILE.write_text(
            fb.NOTES_FILE.read_text(encoding="utf-8")
            + "- [2026-09-10 10:00] identical fact\n", encoding="utf-8")
    out = fb.curate_notes("test")
    body = fb.NOTES_FILE.read_text(encoding="utf-8")
    check("duplicates collapsed to one", body.count("identical fact") == 1, body)
    check("curation reports the merge", "duplicate" in out, out)


def test_curate_respects_char_cap_and_archives():
    redirect_files()
    cfg = fb.CONFIG["agent"]
    cfg["notes_max_chars"] = 600
    cfg["notes_keep_entries"] = 500
    cfg["notes_archive_days"] = 9999
    lines = [f"- [2026-09-10 10:{i:02d}] fact number {i} " + "y" * 80
             for i in range(20)]
    fb.NOTES_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = [l for l in lines]
    fb.curate_notes("test")
    body = fb.NOTES_FILE.read_text(encoding="utf-8")
    archived = fb.NOTES_ARCHIVE_FILE.read_text(encoding="utf-8")
    check("notes under cap after curation", len(body) <= 600, f"{len(body)}")
    check("elision is explicit", "notes elided" in body, body[:200])
    check("archived, not lost", "fact number 0" in archived, archived[:200])
    check("newest entries kept", "fact number 19" in body, body[-300:])
    check("oldest entry left the prompt", "fact number 0" not in body, body)


def test_remember_auto_curates():
    redirect_files()
    fb.CONFIG["agent"]["notes_max_note_chars"] = 100000
    fb.CONFIG["agent"]["notes_max_chars"] = 400
    fb.CONFIG["agent"]["notes_keep_entries"] = 500
    fb.CONFIG["agent"]["notes_archive_days"] = 9999
    out = ""
    for i in range(12):
        # DISTINCT facts on purpose: twelve notes that say the same thing in the same words
        # are now superseded rather than stacked (notes_supersede_share), which is the point
        # of that rule - this test is about the budget, so each note has to be its own fact.
        out = fb.tool_remember(
            {"note": f"fact {i}: the widget{i} dial reads value{i} " + "z" * 60}, {})
    check("curation triggered by remember", "curated" in out, out[-200:])
    check("notes bounded at write time",
          len(fb.NOTES_FILE.read_text(encoding="utf-8")) <= 400,
          str(len(fb.NOTES_FILE.read_text(encoding="utf-8"))))


def test_notes_tool_actions():
    redirect_files()
    fb.CONFIG["agent"]["notes_max_chars"] = 4000
    fb.tool_remember({"note": "a durable fact"}, {})
    view = fb.tool_notes({"action": "view"}, {})
    check("notes view shows the budget", "prompt cap" in view, view[:120])
    check("notes view shows content", "a durable fact" in view, view[:200])
    check("notes curate is a no-op when fine",
          "inside budget" in fb.tool_notes({"action": "curate"}, {}), "")


def test_prompt_carries_elision_pointer():
    redirect_files()
    fb.NOTES_FILE.write_text("- [2026-09-10 10:00] hi\n", encoding="utf-8")
    prompt = fb.build_system_prompt()
    # v1.9.1: the notes block (and with it the archive pointer) moved out of the
    # static system prompt into the trailing state block, so a `remember` write
    # can't invalidate the server's prefix cache. The pointer must still reach
    # the model: in the trailing block, and in the remember tool description.
    check("the archive is named where the notes actually are",
          "notes-archive.md" in fb.volatile_context())
    check("the remember tool schema still names the archive",
          "notes-archive.md" in json.dumps(fb.REGISTRY.openai_schemas()))
    check("the static prompt carries no archive pointer (cache-stable)",
          "notes-archive.md" not in prompt)
    check("prompt lists the task tool", "task ledger" in prompt.lower(), "")


# --------------------------------------------------------------------------
# task ledger
# --------------------------------------------------------------------------

def test_task_ledger_lifecycle():
    redirect_files()
    fb.CONFIG["agent"]["tasks_max_open"] = 3
    fb.CONFIG["agent"]["tasks_done_keep"] = 2
    out = fb.tool_task({"action": "add", "task": "roll tinycmdr 1.9.0 out"},
                       {})
    check("task add", "task #1 added" in out, out)
    tid = json.loads(fb.TASKS_FILE.read_text(encoding="utf-8"))["items"][0]["id"]
    check("tasks.json is the source of truth",
          (TMP / "tasks.md").exists() and "#1" in
          (TMP / "tasks.md").read_text(encoding="utf-8"), "")
    out = fb.tool_task({"action": "doing", "id": tid}, {})
    check("task doing", "-> doing" in out, out)
    out = fb.tool_task({"action": "done", "id": tid}, {})
    check("done without evidence refused", out.startswith("ERROR"), out)
    out = fb.tool_task({"action": "done", "id": tid,
                        "note": "unit tests green, service restarted"}, {})
    check("done with evidence", "-> done" in out, out)
    for n in range(2, 5):
        fb.tool_task({"action": "add", "task": f"job {n}"}, {})
    out = fb.tool_task({"action": "add", "task": "one too many"}, {})
    check("open-task cap enforced", out.startswith("ERROR") and "cap" in out,
          out)
    out = fb.tool_task({"action": "clear"}, {})
    check("clear prunes finished", "cleared 1" in out, out)
    out = fb.tool_task({"action": "status", "id": 999, "status": "done"}, {})
    check("unknown id refused", out.startswith("ERROR"), out)
    out = fb.tool_task({"action": "status", "id": 2, "status": "nonsense"}, {})
    check("bad status refused", out.startswith("ERROR"), out)


def test_done_with_two_open_items_names_both():
    """Measured 2026-09-25 driving the manager box (work order 2): two items were open, the model called
    done with no id, got a one-liner listing the IDS only, and stopped using the ledger for
    the rest of the run. The refusal now names each open item so the choice is obvious."""
    fb.tool_task({"action": "clear"}, {})
    fb.tool_task({"action": "add", "task": "restart the tower computer"}, {})
    fb.tool_task({"action": "add", "task": "check the disk space"}, {})
    out = fb.tool_task({"action": "done", "note": "ran it"}, {})
    check("an ambiguous done is refused", out.startswith("ERROR"), out[:120])
    check("...and it names every open item with its id and text",
          "#1" in out and "restart the tower computer" in out
          and "#2" in out and "check the disk space" in out, out[:300])
    check("...and it says to pass id=<n>, one call per task",
          "id=<n>" in out and "one call per task" in out, out[:300])
    out = fb.tool_task({"action": "done", "id": 2, "note": "df says 64 GB free"}, {})
    check("naming the id still works", out.startswith("OK"), out[:160])


def test_the_list_carries_its_own_tally():
    """The rows AND the count, because the model miscounted the rows it was given.

    Measured on the drive 2026-09-23: asked how many items the ledger held, the run read
    this list and answered "13 items (8 done, 1 dropped, 5 open)" - its own breakdown summed
    to 14 and the real split was 7 done. Counting rows is arithmetic the harness can do once,
    in one place, instead of asking the model to do it from the rendering.
    """
    redirect_files()
    fb.tool_task({"action": "add", "task": "still open one"}, {})
    fb.tool_task({"action": "add", "task": "still open two"}, {})
    tid = json.loads(fb.TASKS_FILE.read_text(encoding="utf-8"))["items"][0]["id"]
    fb.tool_task({"action": "done", "id": tid, "note": "re-ran the check"}, {})
    out = fb.tool_task({"action": "list"}, {})
    check("the list still lists every row", out.count("\n") >= 2, out)
    check("the list ends with the tally", "(2 item(s):" in out, out)
    check("...counting each status", "1 done" in out and "1 open" in out, out)
    _tally = re.search(r"\((\d+) item\(s\): ([^)]+)\)$", out)
    _rows = [l for l in out.splitlines() if l.startswith("#")]
    check("...and the tally's total is the row count",
          bool(_tally) and int(_tally.group(1)) == len(_rows),
          f"{_tally.group(0) if _tally else 'no tally'} vs {len(_rows)} rows")
    check("...and its parts sum to that total",
          bool(_tally) and sum(int(n) for n in re.findall(r"(\d+) \w+", _tally.group(2)))
          == int(_tally.group(1)), _tally.group(0) if _tally else "no tally")


def test_task_prompt_render():
    redirect_files()
    fb.tool_task({"action": "add", "task": "fix the poster pipeline"}, {})
    rendered = fb.render_task_prompt()
    check("render shows the open task", "fix the poster pipeline" in rendered,
          rendered)
    # "to-do list" used to be the marker here, and that phrase is what made a fresh session
    # adopt an ended one's thread (2026-09-27): the requirement is that the block says how to
    # keep the ledger AND that inherited items need the operator's yes, not that it calls them
    # a plan.
    check("render is a plan not a log",
          "mark, don't append" in rendered
          and "ask the operator before resuming" in rendered,
          rendered)
    check("a done row is marked as history, not an order",
          "[done, no action]" in rendered or "0 done" in rendered, rendered)
    # v1.9.1: the ledger is injected as a trailing block, not into the static
    # system prompt — see test_system_prompt_is_static_state_is_trailing.
    check("the ledger reaches the model (trailing block)",
          "fix the poster pipeline" in fb.volatile_context())
    check("the static prompt does not carry the ledger",
          "fix the poster pipeline" not in fb.build_system_prompt())


def test_skill_read_names_the_tool_surface():
    """A dropped-in runbook has to arrive beside the list of tools that exist here.

    A prose runbook transfers as text; the tools it names do not. An install whose tools/
    folder is empty otherwise gets instructions for a program it does not have, and the
    mismatch only surfaces several steps later as an unknown-tool error. Measured on a
    real install: a desktop-automation runbook dropped in, tools/ empty, the agent
    looking for a tool that no build of it has.
    """
    reset_config()
    sdir = Path(fb.SKILLS_DIR) / "hermes-runbook"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "SKILL.md").write_text(
        "---\nname: hermes-runbook\ndescription: written for another harness\n---\n\n"
        "## Steps\n\nCall `computer_use` to look at the desktop.\n", encoding="utf-8")
    try:
        body = fb.tool_skill({"action": "read", "name": "hermes-runbook"}, {})
        check("the runbook body comes back", "Call `computer_use`" in body, body[:120])
        check("the tool surface rides with it",
              "[HARNESS: tools this box has:" in body, body[-300:])
        surface = body.split("[HARNESS:", 1)[-1]
        check("the surface lists the tools this build does have",
              "list_tools" in surface and "shell" in surface, surface[:160])
        check("a step's missing tool is named as such, not left to be discovered",
              "written for another build" in surface, surface[:200])
        cont = fb.tool_skill({"action": "read", "name": "hermes-runbook", "offset": 10}, {})
        check("a continuation page does not repeat it", "[HARNESS:" not in cont, cont[-160:])
    finally:
        shutil.rmtree(sdir, ignore_errors=True)


# --------------------------------------------------------------------------
# evidence check
# --------------------------------------------------------------------------

def test_force_shrink_terminates_under_a_tight_budget():
    """Regression: _force_shrink cut at messages[1:first_user_message], which
    pointed at its OWN elision marker once one was in place — it deleted the
    marker, re-inserted it, and looped for ever, so recovering from a
    server-side context overflow hung the run instead of recovering.
    test_force_shrink above did not catch it: its budget was loose enough that
    the loop never iterated (the tool-output clipping did all the shrinking)."""
    redirect_files()
    try:
        fb.CONFIG["llm"]["max_context_tokens"] = 4000        # target = 2000
        _budget_clear()
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(30):
            msgs.append({"role": "user", "content": f"u{i} " + "x" * 2000})
            msgs.append({"role": "assistant", "content": ""})
            msgs.append({"role": "tool", "tool_call_id": str(i),
                         "content": "y" * 3000})
        before = fb.AGENT._messages_token_est(msgs)
        fb.AGENT._force_shrink(msgs)                        # used to hang here
        after = fb.AGENT._messages_token_est(msgs)
        check("terminates", True)
        check("keeps the system prompt", msgs[0]["content"] == "sys")
        check("leaves no orphan tool message",
              all(msgs[i].get("role") != "tool" or
                  msgs[i - 1].get("role") in ("tool", "assistant")
                  for i in range(1, len(msgs))))
        check("shrinks hard", after < before / 2, f"{before} -> {after}")
        marks = [m["content"] for m in msgs
                 if str(m.get("content") or "").startswith(fb.ELISION_MARKERS)]
        check("reuses one elision marker instead of stacking them",
              len(marks) == 1, marks)
    finally:
        redirect_files()
        _budget_clear()


def test_elision_note_names_what_was_dropped():
    """A bare elision marker told the model that something had vanished, not WHAT.

    Measured on the live box 2026-09-29: a rewrite compacted mid-task, and the next several
    calls went into re-deriving the task out of the harness's own session files and carry
    file instead of continuing the work. The marker now carries the shape of what it
    replaced, and it grows across repeated compactions - a long run compacts more than once,
    and the earlier notes must not be replaced by the newest.
    """
    def call(name, args, cid):
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}]}

    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "rewrite the Book of Enoch"},
            call("shell", {"command": "ls -la /work"}, "c1"),
            {"role": "tool", "tool_call_id": "c1", "content": "y" * 200},
            {"role": "assistant", "content": "found it"},
            {"role": "user", "content": "carry on"}]
    check("the cut happened", fb.AGENT._drop_oldest_block(msgs, fb.MARK_COMPACT))
    note = str(msgs[1].get("content") or "")
    check("the marker still LEADS, so prefix matching recognises it",
          note.startswith(fb.MARK_COMPACT), note[:70])
    check("  and it names the call that was dropped",
          "shell" in note and "ls -la /work" in note, note)
    check("  and it carries the operator's words that were in that range",
          "operator: rewrite the Book of Enoch" in note, note)

    msgs += [call("read_file", {"path": "/work/p01.txt"}, "c2"),
             {"role": "tool", "tool_call_id": "c2", "content": "y" * 200},
             {"role": "user", "content": "keep going"}]
    check("a second cut happened", fb.AGENT._drop_oldest_block(msgs, fb.MARK_COMPACT))
    note2 = str(msgs[1].get("content") or "")
    check("a later compaction ADDS to the note instead of replacing it",
          "shell" in note2 and "read_file" in note2, note2)
    check("  and the note stays bounded",
          len(note2) <= len(fb.MARK_COMPACT) + fb.ELISION_NOTES_CHARS + 4,
          f"{len(note2)} chars")
    check("_short_args reads the one useful key and shrugs at the rest",
          fb._short_args('{"command": "echo hi"}') == "echo hi"
          and fb._short_args({"path": "/x"}) == "/x"
          and fb._short_args("not json at all") == "")


def test_evidence_rules():
    a = fb._annotate_evidence("I fixed the widget.", [], 0)
    check("claim with no tool call is flagged", "evidence check" in a, a)
    b = fb._annotate_evidence("Nothing to do here.", [], 0)
    check("silent when nothing is claimed", "evidence check" not in b, b)
    e = fb._annotate_evidence("FILES: 5 4\nREADBACK: alphabeta", [], 0)
    check("a filled-in report with no tool call is flagged", "evidence check" in e, e)
    f = fb._annotate_evidence("Nothing to do here.", [], 0)
    check("prose with no measurement is not flagged", "evidence check" not in f, f)
    c = fb._annotate_evidence("Fixed it.", [("write_file", "x.py", 3)], 3)
    check("last write with no read-back flagged", "no read-back" in c, c)
    d = fb._annotate_evidence("Fixed it.", [("write_file", "x.py", 3)], 4)
    check("silent once something follows the write",
          "evidence check" not in d, d)
    check("mutation detection: file writers",
          fb._is_mutation("write_file", {}, "OK") and
          fb._is_mutation("edit_file", {}, "OK") and
          fb._is_mutation("create_tool", {}, "OK"))
    check("mutation detection: shell is not guessed at",
          not fb._is_mutation("shell", {"command": "rm x"}, "OK"))
    check("mutation detection: failed writes do not count",
          not fb._is_mutation("write_file", {}, "ERROR: nope"))
    fb.REGISTRY.custom["faux"] = {"mutates": True}
    check("mutation detection: MUTATES honoured",
          fb._is_mutation("faux", {}, "OK"))
    fb.REGISTRY.custom.pop("faux", None)



# --------------------------------------------------------------------------
# the run after a wreck: ONE payload line, derived from the transcript
# --------------------------------------------------------------------------

def test_a_finished_conversation_gets_no_warning_line():
    """The line is conditional: on a healthy session the block is byte-identical to what it
    was before this feature. That is the whole point of deriving the condition from the
    transcript instead of carrying a flag - no rent on a session that is fine."""
    fb.AGENT.histories["warn-clean"] = [
        {"role": "user", "content": "what model are you on"},
        {"role": "assistant", "content": "Model for this conversation: `main` (local)."},
    ]
    check("clean: no reason is derived",
          fb.AGENT._prior_run_unfinished("warn-clean") == "",
          fb.AGENT._prior_run_unfinished("warn-clean"))
    block = fb.volatile_context(session_key="warn-clean", prior_unfinished="")
    check("clean: no warning line in the block", "did not finish" not in block, block[-200:])


def test_each_abnormal_end_is_named():
    """The measured shapes of an unfinished exchange, and the reason each one yields."""
    cases = [
        ("\U0001f501 Stopped a loop: `task` repeated 6 times",
         "the harness stopped the previous run mid-task"),
        ("\u26a0\ufe0f **No answer** from the model twice in a row.",
         "the previous run ended without an answer"),
        ("I'll gather the logs, then write it up.",
         "ended on a promise"),
        ("\u26a0\ufe0f Hit the turn limit without finishing. Send 'continue' and "
         "I'll pick up where I left off.",
         "hit its turn limit"),
        ("config.json is 19448 bytes.", ""),
    ]
    for i, (line, expect) in enumerate(cases):
        key = "warn-%d" % i
        fb.AGENT.histories[key] = [{"role": "assistant", "content": line}]
        got = fb.AGENT._prior_run_unfinished(key)
        check("reason for %r" % line[:28],
              (expect in got) if expect else got == "", (got, expect))
    block = fb.volatile_context(session_key="warn-0",
                                prior_unfinished="the harness stopped the previous run mid-task")
    check("warning: the line says the message below is the current request",
          "CURRENT request" in block, block[-260:])
    check("warning: it resumes the unfinished work when the operator asks",
          "If it asks to continue, resume that unfinished work" in block, block[-320:])
    check("warning: and leaves the old task alone otherwise",
          "leave the old task alone" in block, block[-320:])


def test_an_unanswered_order_is_named_as_an_interruption():
    """A killed run leaves the operator's message as the last turn and nothing after it.

    Measured 2026-09-24 on the fleet's macOS bed: an order went in, the box was pushed
    mid-run, and the run that followed ("Continue with the task") opened with an EMPTY
    history and could only ask what the task was. Every path that ENDS a run appends an
    assistant turn, so a dangling operator message is an interruption by construction.
    """
    key = "warn-interrupted"
    fb.AGENT.histories[key] = [
        {"role": "assistant", "content": "Model for this conversation: `main`."},
        {"role": "user", "content": "Download the video in this link and send it here"},
    ]
    got = fb.AGENT._prior_run_unfinished(key)
    check("interrupted: the reason names the unanswered order",
          "never answered" in got and "interrupted" in got, got)
    block = fb.volatile_context(session_key=key, prior_unfinished=got)
    check("interrupted: the line says which request is current",
          "CURRENT request" in block, block[-320:])
    check("interrupted: and that 'continue' means resume it",
          "resume that unfinished work" in block, block[-320:])


def test_the_order_is_on_disk_before_the_first_model_call():
    """The transcript was written only in the run's `finally`, so a process killed mid-run
    took the operator's own message with it - the carry sidecar survived (it is written per
    entry) and the conversation did not, which is how a bot can describe the work it did and
    not the order it was doing it for.

    Graded from DISK while the first model call is in flight, which is exactly what a
    restart or a push sees.
    """
    redirect_files()
    fb.AGENT.histories.clear()
    path = fb.AGENT._session_path("order-session")
    path.unlink(missing_ok=True)
    seen = {}
    saved_chat = fb.AGENT._chat

    def peek(messages, *args, **kw):
        # The harness calls _chat(payload, model) POSITIONALLY: a stub that only
        # accepts **kw raises before it can observe anything, and the test then
        # reads the failure as "the file was not written".
        seen["exists"] = path.exists()
        seen["text"] = path.read_text(encoding="utf-8") if path.exists() else ""
        return {"role": "assistant", "content": "Done: nothing further."}

    fb.AGENT._chat = peek
    try:
        fb.AGENT.run("order-session", "download the video and send it here")
    finally:
        fb.AGENT._chat = saved_chat
    check("durable order: the transcript is on disk during the run",
          seen.get("exists"), seen.get("text", "")[:120])
    check("durable order: it already holds the operator's message",
          "download the video and send it here" in (seen.get("text") or ""),
          seen.get("text", "")[:200])


def test_the_warning_clears_once_a_run_answers_normally():
    """Self-clearing: the reason comes from the transcript, so a normal answer ends it."""
    key = "warn-clear"
    fb.AGENT.histories[key] = [{"role": "assistant", "content": "I'll check the ledger."}]
    check("warning: present before a normal answer",
          fb.AGENT._prior_run_unfinished(key) != "")
    fb.AGENT.histories[key].append({"role": "assistant",
                                    "content": "Ledger holds 3 open items."})
    check("warning: gone after one", fb.AGENT._prior_run_unfinished(key) == "",
          fb.AGENT._prior_run_unfinished(key))

def scripted_run(seq, **cfg):
    """Run Agent.run against a scripted _chat, with files redirected."""
    redirect_files()
    fb.AGENT.histories.clear()
    fb.AGENT.model_overrides.clear()
    fb.AGENT.last_usage.clear()
    saved_chat = fb.AGENT._chat
    saved_cfg = copy.deepcopy(fb.CONFIG["agent"]) if cfg else None
    fb.CONFIG["agent"].update(cfg)
    seq = list(seq)

    def fake_chat(messages, model=None, use_tools=True, usage=None,
                  max_tokens=None, cancel_event=None,
                  on_delta=None, session_key=None):
        reply = seq.pop(0)
        if usage is not None:
            usage["calls"] += 1
            usage["llm_secs"] += 0.01
            usage.setdefault("finish_reason", "stop")
        return reply

    fb.AGENT._chat = fake_chat
    try:
        out = fb.AGENT.run("test-session", "do the thing")
    finally:
        fb.AGENT._chat = saved_chat
        if saved_cfg is not None:
            fb.CONFIG["agent"].clear()
            fb.CONFIG["agent"].update(saved_cfg)
    return out, fb.AGENT.last_usage.get("test-session") or {}


def test_run_flags_unverified_write():
    target = TMP / "written.txt"
    out, usage = scripted_run([
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "1", "function": {
             "name": "write_file",
             "arguments": json.dumps({"path": str(target),
                                      "content": "hello"})}}]},
        {"role": "assistant", "content": "Wrote the config file."},
    ])
    check("unverified write flagged in the answer", "evidence check" in out, out)
    check("run counted the mutation", usage.get("mutations") == 1, usage)
    check("run classified ok", usage.get("status") == "ok", usage)


def test_run_silent_when_verified():
    target = TMP / "written2.txt"
    out, usage = scripted_run([
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "1", "function": {
             "name": "write_file",
             "arguments": json.dumps({"path": str(target),
                                      "content": "hello"})}}]},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "2", "function": {
             "name": "read_file",
             "arguments": json.dumps({"path": str(target)})}}]},
        {"role": "assistant", "content": "Wrote and read back the config file."},
    ])
    check("verified write is not flagged", "evidence check" not in out, out)
    check("read-back recognised", usage.get("mutations") == 1, usage)


def test_run_flags_hallucinated_change():
    out, usage = scripted_run([
        {"role": "assistant", "content": "I updated the service and restarted "
                                        "it, all good."},
    ])
    check("change claimed with no tool call flagged",
          "evidence check" in out, out)
    check("no mutations recorded", usage.get("mutations") == 0, usage)


def test_run_budget_wrapup_asks_for_verification():
    target = TMP / "written3.txt"
    out, usage = scripted_run([
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "1", "function": {
             "name": "write_file",
             "arguments": json.dumps({"path": str(target),
                                      "content": "hello"})}}]},
        {"role": "assistant", "content": "Report body."},
        # auto_continue_max=0 on purpose: a run that still has continuation budget left
        # opens another segment instead of wrapping up (tests/test_stall.py pins that
        # behaviour). This test is about the wrap-up itself, so it takes the one setting
        # that reaches it.
    ], max_steps=1, auto_continue_max=0)
    check("budget wrap-up reported", "Budget reached" in out, out[:200])
    check("budget status recorded", usage.get("status") == "budget", usage)


def test_run_infra_failure_is_not_a_wrong_answer():
    redirect_files()
    fb.AGENT.histories.clear()
    saved_chat = fb.AGENT._chat

    def boom(*a, **kw):
        raise fb.InfraError("connection refused by all endpoints")

    fb.AGENT._chat = boom
    try:
        out = fb.AGENT.run("test-infra", "do the thing")
    finally:
        fb.AGENT._chat = saved_chat
    check("infra failure named as infra", "infrastructure failure" in out, out)
    check("infra status recorded",
          fb.AGENT.last_usage["test-infra"].get("status") == "infra",
          fb.AGENT.last_usage["test-infra"])


# --------------------------------------------------------------------------
# transport hardening
# --------------------------------------------------------------------------

class FakeResp:
    def __init__(self, status=200, body=None, text="", headers=None):
        self.status_code = status
        self._body = body
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            err = fb.requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def _sse(*objs, done=True):
    """Build an SSE body: dicts become data: lines, bytes are appended verbatim."""
    body = b""
    for o in objs:
        body += o if isinstance(o, bytes) else b"data: " + json.dumps(o).encode() + b"\n\n"
    return body + (b"data: [DONE]\n\n" if done else b"")


def _chunk(delta, finish=None, **extra):
    c = {"choices": [{"index": 0, "delta": delta}], "object": "chat.completion.chunk"}
    if finish:
        c["choices"][0]["finish_reason"] = finish
    c.update(extra)
    return c


class _RawStub:
    """Stands in for urllib3's HTTPResponse: _stream_chat's hang-up path reaches for
    raw._connection.sock (absent here, which is fine) and then raw.close()."""

    def __init__(self, outer):
        self.outer = outer

    def close(self):
        self.outer.closed = True


class StreamResp:
    """A streamed response. `stall=True` keeps the connection open and sends nothing,
    which is the wedged-stream shape the idle gap exists for."""

    def __init__(self, body=b"", status=200, stall=False):
        self.status_code = status
        self._body = body
        self._stall = stall
        self.closed = False
        self.headers = {}
        self.raw = _RawStub(self)

    def raise_for_status(self):
        if self.status_code >= 400:
            err = fb.requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def iter_lines(self, decode_unicode=False):
        for line in self._body.splitlines():
            yield line.decode("utf-8", "replace") if decode_unicode else line
        if self._stall:
            time.sleep(600)

    def close(self):
        self.closed = True


class DribbleServer(threading.Thread):
    """Accepts the POST, sends headers, then dribbles a byte every `interval`
    seconds for `duration`. Every individual read succeeds inside the requests
    timeout, so nothing but a wall-clock bound can end this call — which is
    exactly the endpoint behaviour the watchdog exists for."""

    saw_close = False

    def __init__(self, interval=0.15, duration=6.0):
        super().__init__(daemon=True)
        self.interval, self.duration = interval, duration
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]

    def run(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            try:
                conn.recv(65536)
                conn.sendall(b"HTTP/1.1 200 OK\r\n"
                             b"Content-Type: application/json\r\n\r\n")
                deadline = time.time() + self.duration
                while time.time() < deadline:
                    try:
                        conn.sendall(b" ")
                    except OSError:
                        # a write into a closed peer IS the hang-up: this
                        # is how the test proves the client cancelled the
                        # call instead of walking away from a live thread
                        self.saw_close = True
                        return
                    time.sleep(self.interval)
            except Exception:
                pass

    def stop(self):
        try:
            self.sock.close()
        except Exception:
            pass


class SilentServer(threading.Thread):
    """Accepts and never sends anything — the plain hang that the ordinary
    read timeout is supposed to bound."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]

    def run(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            try:
                conn.recv(65536)
                time.sleep(8)
            except Exception:
                pass

    def stop(self):
        try:
            self.sock.close()
        except Exception:
            pass


def test_watchdog_abandons_a_trickling_endpoint():
    srv = DribbleServer()
    srv.start()
    url = f"http://127.0.0.1:{srv.port}/v1/chat/completions"
    t0 = time.time()
    try:
        fb._post_watchdog(url, {}, {}, timeout=0.4, grace=0.2)
        check("trickling endpoint abandoned", False, "no exception raised")
    except fb.InfraError as e:
        elapsed = time.time() - t0
        check("trickling endpoint abandoned", True)
        check("abandoned at the hard bound, not a read timeout",
              elapsed < 2.5, f"{elapsed:.1f}s")
        check("abandon message explains itself", "abandoned" in str(e), str(e))
        # KNOWN GAP, pinned so it is not mistaken for handled: the caller walks
        # away, the socket stays open, and the server below keeps working (for a
        # real trickling llama.cpp that means the GPU keeps generating for an
        # answer nobody will collect). Closing the requests.Session does NOT fix
        # it: urllib3 closes only IDLE pooled connections, and this one is checked
        # out. A real cancel means owning the socket (http.client), which the
        # operator has explicitly parked as not worth the restructuring.
        # For STREAMING calls that gap is now closed (v1.9.28 closes the response,
        # which drops the connection, and the local box stops generating). A
        # non-streaming call still walks away from a live socket: that half is
        # unchanged, and test_a_streaming_cancel_hangs_up_on_the_server proves the
        # half that changed.
        if srv.saw_close:
            # Fails only if the non-streaming path started closing the socket too,
            # which would mean the two paths no longer differ.
            check("non-streaming abandonment still does not close the socket",
                  False, "saw_close became True on the non-streaming path")
    except Exception as e:
        check("trickling endpoint abandoned", False, repr(e))
    finally:
        srv.stop()


def test_read_timeout_still_bounds_a_silent_endpoint():
    srv = SilentServer()
    srv.start()
    url = f"http://127.0.0.1:{srv.port}/v1/chat/completions"
    t0 = time.time()
    try:
        fb._post_watchdog(url, {}, {}, timeout=0.4, grace=2.0)
        check("silent endpoint raises", False, "no exception raised")
    except fb.InfraError as e:
        check("silent endpoint bounded", False, f"InfraError: {e}")
    except Exception as e:
        check("silent endpoint bounded by the read timeout", True)
        check("bounded quickly", time.time() - t0 < 2.0,
              f"{time.time() - t0:.1f}s")
    finally:
        srv.stop()


def with_fake_post(responses, fn, catalog=None):
    """Run fn with requests.post scripted. Returns (result, call list)."""
    calls = []
    saved = fb.requests.post

    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        # headers are recorded too: which key an endpoint is called with is the
        # difference between the local box ("none") and a cloud provider
        calls.append({"url": url, "payload": json, "stream": stream, "headers": headers})
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    fb.requests.post = fake_post
    fb._MODEL_CACHE.update(at=time.time(), entries=catalog if catalog is not None else [
        {"name": "main", "url": fb.CONFIG["llm"]["base_url"], "local": True,
         "alias": False, "send_as": "main", "key": "none"}])
    try:
        return fn(), calls
    finally:
        fb.requests.post = saved


def ok_resp(text="answer"):
    return FakeResp(200, body={
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 3}})


def isolated_config():
    """Single-endpoint config, so failover cannot muddy a transport test."""
    cfg = copy.deepcopy(fb.CONFIG)
    cfg["llm"]["fallbacks"] = []
    cfg["llm"]["allow_cloud_fallback"] = False
    fb.CONFIG = cfg
    return cfg


def test_429_is_waited_out_on_the_same_endpoint():
    saved_cfg = fb.CONFIG
    try:
        cfg = isolated_config()
        cfg["llm"]["retry_after_max"] = 0
        usage = {}
        resp, calls = with_fake_post(
            [FakeResp(429, text="slow down", headers={"Retry-After": "0"}),
             ok_resp("recovered")],
            lambda: fb.AGENT._chat([{"role": "user", "content": "hi"}],
                                   usage=usage))
        check("429 retried, not failed over",
              len(calls) == 2 and len({c["url"] for c in calls}) == 1, calls)
        check("429 recovered on retry", resp["content"] == "recovered", resp)
        check("429 recorded as a retry", usage.get("retries") == 1, usage)
        check("attempt log notes the wait",
              usage["attempts"][0]["outcome"] == "retry", usage)
        check("usage line admits the retry",
              "retried/abandoned" in fb.fmt_usage(usage), fb.fmt_usage(usage))
    finally:
        fb.CONFIG = saved_cfg


def test_a_cancel_is_noticed_while_waiting_out_a_429():
    """A 429 wait is the one place the call slept in a single un-interruptible block, so
    a /stop sent during it was not seen until the whole Retry-After (a config cap of 60s,
    by default) had elapsed. The wait is sliced now, and the OperatorStop is raised from
    inside it, exactly as _post_watchdog does."""
    saved_cfg = fb.CONFIG
    try:
        cfg = isolated_config()
        cfg["llm"]["retry_after_max"] = 5
        ev = threading.Event()
        box = {}

        def call():
            try:
                fb.AGENT._chat([{"role": "user", "content": "hi"}], usage={},
                               cancel_event=ev)
                box["r"] = "returned"
            except fb.OperatorStop:
                box["r"] = "stopped"
            except BaseException as e:        # noqa: BLE001 - reported below
                box["r"] = f"{type(e).__name__}: {e}"

        def go():
            th = threading.Thread(target=call, daemon=True)
            th.start()
            time.sleep(0.3)
            ev.set()
            th.join(3)
            return box

        t0 = time.time()
        box, _calls = with_fake_post(
            [FakeResp(429, text="slow down", headers={"Retry-After": "5"}),
             ok_resp("never reached")], go)
        elapsed = time.time() - t0
        check("429 wait: the cancel stops the call", box.get("r") == "stopped", box)
        check("429 wait: it stops long before Retry-After elapses",
              elapsed < 2.0, f"{elapsed:.2f}s")
    finally:
        fb.CONFIG = saved_cfg


def test_context_overflow_is_recoverable():
    saved_cfg = fb.CONFIG
    try:
        isolated_config()
        usage = {}
        err = None
        def go():
            fb.AGENT._chat([{"role": "user", "content": "hi"}], usage=usage)
        _r, calls = with_fake_post(
            [FakeResp(400, text="This model's maximum context length is 4096 "
                                "tokens, however you requested 8000 tokens")],
            go)
        check("context overflow did not fail over", len(calls) == 1, calls)
        check("overflow recorded", usage.get("retries") == 1, usage)
    except fb.ContextOverflow as e:
        check("context overflow raised for the agent loop",
              "context window" in str(e), str(e))
    except Exception as e:
        check("context overflow raised for the agent loop", False, repr(e))
    finally:
        fb.CONFIG = saved_cfg


def test_a_400_naming_max_tokens_is_retried_as_max_completion_tokens():
    """OpenAI's newer models (and Azure) answer 400 to the legacy `max_tokens` and NAME
    it. That used to be classified FATAL, so the run died telling the operator to check
    the key/model/base_url over a renamed field - and the optional-field scan could not
    help, because dropping the field would remove the envelope's output clamp. The same
    value is re-sent under `max_completion_tokens` to the SAME endpoint."""
    saved_cfg = fb.CONFIG
    try:
        isolated_config()
        # The cap the envelope actually sends, learned from a clean call: the payloads
        # recorded by with_fake_post are the LIVE dict, so the first (rejected) record
        # has already been mutated by the rename below and cannot be compared against.
        probe = with_fake_post([ok_resp("probe")],
                               lambda: fb.AGENT._chat(
                                   [{"role": "user", "content": "hi"}]))[1]
        cap = probe[0]["payload"]["max_tokens"]
        check("400 max_tokens: the envelope sends a cap to begin with", cap >= 1, cap)
        usage = {}
        resp, calls = with_fake_post(
            [FakeResp(400, text="Unsupported parameter: 'max_tokens' is not supported "
                                "with this model. Use 'max_completion_tokens' instead."),
             ok_resp("recovered")],
            lambda: fb.AGENT._chat([{"role": "user", "content": "hi"}], usage=usage))
        check("400 max_tokens: the same endpoint is retried",
              len(calls) == 2 and calls[0]["url"] == calls[1]["url"], calls)
        sent = calls[1]["payload"]
        check("400 max_tokens: the value is re-sent under the new name",
              sent.get("max_completion_tokens") == cap,
              (cap, sent.get("max_completion_tokens")))
        check("400 max_tokens: the rejected name is gone",
              "max_tokens" not in sent, sorted(sent))
        check("400 max_tokens: the answer arrives",
              resp.get("content") == "recovered", resp)
        check("400 max_tokens: recorded as a retry", usage.get("retries") == 1, usage)
    finally:
        fb.CONFIG = saved_cfg


def test_fatal_status_is_not_reported_as_model_failure():
    saved_cfg = fb.CONFIG
    try:
        isolated_config()
        try:
            with_fake_post([FakeResp(401, text="invalid api key")],
                           lambda: fb.AGENT._chat(
                               [{"role": "user", "content": "hi"}], usage={}))
            check("401 raises InfraError", False, "no exception")
        except fb.InfraError as e:
            check("401 raises InfraError", "rejected the request" in str(e),
                  str(e))
            check("401 names the cause", "401" in str(e), str(e))
    finally:
        fb.CONFIG = saved_cfg


def test_dead_endpoint_reports_infra_not_answer():
    saved_cfg = fb.CONFIG
    try:
        isolated_config()
        try:
            with_fake_post([fb.requests.ConnectionError("refused")],
                           lambda: fb.AGENT._chat(
                               [{"role": "user", "content": "hi"}], usage={}))
            check("dead endpoint raises InfraError", False, "no exception")
        except fb.InfraError as e:
            check("dead endpoint raises InfraError", "no LLM endpoint" in str(e),
                  str(e))
    finally:
        fb.CONFIG = saved_cfg


def test_clamp_is_detected_against_the_sent_cap():
    saved_cfg = fb.CONFIG
    try:
        cfg = isolated_config()
        cfg["llm"]["max_tokens"] = 8000
        usage = {}
        clamped = FakeResp(200, body={
            "choices": [{"message": {"content": "partial..."},
                         "finish_reason": "length"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 512}})
        with_fake_post([clamped], lambda: fb.AGENT._chat(
            [{"role": "user", "content": "hi"}], usage=usage))
        outcomes = [a["outcome"] for a in usage.get("attempts", [])]
        check("server clamp detected", "clamped" in outcomes, usage)
    finally:
        fb.CONFIG = saved_cfg


def test_force_shrink():
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(30):
        msgs.append({"role": "user", "content": f"u{i} " + "x" * 2000})
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": str(i), "function": {
                         "name": "shell", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": str(i),
                     "content": "y" * 3000})
    before = fb.AGENT._messages_token_est(msgs)
    fb.AGENT._force_shrink(msgs)
    after = fb.AGENT._messages_token_est(msgs)
    check("force_shrink shrinks hard", after < before / 2, f"{before} -> {after}")
    check("force_shrink keeps the system prompt", msgs[0]["content"] == "sys")
    check("force_shrink leaves no orphan tool message",
          all(msgs[i].get("role") != "tool" or
              msgs[i - 1].get("role") in ("tool", "assistant")
              for i in range(1, len(msgs))))


# --------------------------------------------------------------------------
# v1.9.1: cache-stable prompt (trailing state block), deep compaction,
# block-trimmed history. The point of all three is the same: never rewrite
# tokens early in the payload, because that invalidates the server's prefix
# cache and re-prefills the WHOLE conversation (measured 24.5 s at 7.7k
# tokens, ~93 s for a notes write at 6.3k, ~400 s at 120k).
# --------------------------------------------------------------------------

def _budget_clear():
    """Drop everything the build memoises for the window and the budget.

    Re-pointed 2026-09-26 (the batch that deleted REPLY_HEADROOM): _envelope() is the one
    accessor and it caches per session, so a suite that changes llm.max_context_tokens or
    stubs the served window has to drop _window_cache/_window_at, _envelope_cache and the
    static-prompt cache - clearing only the window left a stale envelope in place, and eight
    checks read a 19,254-token budget out of it instead of the 4,000 they had configured.
    `_budget_cache` is kept in the list for an OLD build under TINYCMDR_SRC.
    """
    for name in ("_window_cache", "_window_at", "_envelope_cache", "_budget_cache"):
        fb.AGENT.__dict__.pop(name, None)
    if hasattr(fb, "_STATIC_CACHE"):
        fb._STATIC_CACHE.clear()


def test_system_prompt_is_static_state_is_trailing():
    redirect_files()
    fb.NOTES_FILE.write_text("- [10:00] a durable fact\n", encoding="utf-8")
    sp = fb.build_system_prompt()
    vc = fb.volatile_context()
    check("system prompt carries no notes", "a durable fact" not in sp,
          sp[-200:])
    check("system prompt carries no task ledger", "Task ledger" not in sp)
    check("system prompt still carries the instructions",
          "How you work:" in sp)
    check("volatile block carries the notes", "a durable fact" in vc)
    check("volatile block is marked as state, not a request",
          vc.startswith("[context only") and "NOT a new request" in vc,
          vc[:80])


def test_notes_write_does_not_move_the_cached_prefix():
    redirect_files()
    fb.NOTES_FILE.write_text("- [10:00] one\n", encoding="utf-8")
    before = fb.build_system_prompt()
    fb.tool_remember({"note": "two"}, {})
    after = fb.build_system_prompt()
    check("system prompt is byte-identical after a remember", before == after)
    check("the volatile block did change", "two" in fb.volatile_context())


def test_volatile_block_always_carries_the_clock():
    """Nothing to say is now "the clock and nothing else". v2.0.0 put a timestamp in
    the trailing block, never in the system prompt (which must stay byte-identical for
    prefix caching). Live 2026-09-13 on a Windows host: asked for the date, time, zone,
    weekday and yesterday with tools forbidden, the bot answered all five from this line
    and made no tool call, where it previously had to shell out to `date` first."""
    redirect_files()
    vc = fb.volatile_context()
    check("with no notes and no ledger the block still exists", vc != "")
    check("it is still marked as state, not a request",
          vc.startswith("[context only") and "NOT a new request" in vc, vc[:80])
    check("its first line is the machine clock with the offset and the zone",
          re.search(r"Current date and time on this machine: \d{4}-\d{2}-\d{2} "
                    r"\d{2}:\d{2}:\d{2} [+-]\d{4} \(\w+", vc) is not None, vc[:160])
    check("and it carries nothing else",
          "Notes from previous sessions" not in vc and "Task ledger" not in vc)


def test_payload_inserts_state_without_accumulating():
    """The block goes BEFORE the operator's request, not after it. Appended last
    it became the thing the model answered (live 2026-09-10: asked to list
    containers, it replied "Nothing new to chase — the refreshed state just
    confirms everything I've reported still holds")."""
    redirect_files()
    fb.NOTES_FILE.write_text("- [10:00] a fact\n", encoding="utf-8")
    base = [{"role": "system", "content": "s"},
            {"role": "user", "content": "u"}]
    p1 = fb.AGENT._payload(base)
    p2 = fb.AGENT._payload(base)
    check("state block is inserted", len(p1) == 3 and
          "a fact" in p1[1]["content"], len(p1))
    check("the operator's message stays last",
          p1[-1]["content"] == "u" and len(p1[-1]) == 2, p1[-1])
    check("payload is a copy, not the caller's list",
          len(base) == 2 and p1 is not base and p2 is not base, len(base))
    check("state does not accumulate over calls",
          len(p2) == 3, len(p2))
    check("wrap-up call can omit the state block",
          fb.AGENT._payload(base, state=False) is base)


def test_run_injects_state_at_every_tool_call_but_not_the_wrapup():
    """Structural: this is the fix, and losing it is invisible at runtime.

    Parsed with ast rather than matched as text: an earlier version counted a literal
    string, so any reformatting of the call (necessary when a new keyword arrived) failed
    a check whose invariant was still intact. The invariant is the shape of the call, not
    the spelling of the line.
    """
    import ast
    src = (SRC).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    calls = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_chat"):
            continue
        arg0 = node.args[0] if node.args else None
        via_payload = (isinstance(arg0, ast.Call)
                       and isinstance(arg0.func, ast.Attribute)
                       and arg0.func.attr == "_payload")
        payload_session = False
        if via_payload:
            payload_session = any(kw.arg == "session_key" for kw in arg0.keywords)
        state_false = False
        if via_payload:
            # state=False is a keyword of the INNER _payload call, not of _chat.
            state_false = any(kw.arg == "state" and isinstance(kw.value, ast.Constant)
                              and kw.value.value is False for kw in arg0.keywords)
        raw_list = isinstance(arg0, ast.Name) and arg0.id == "messages"
        calls.append({"via_payload": via_payload, "state_false": state_false,
                      "raw_list": raw_list, "session": payload_session})

    check("there is more than one _chat call site to check", len(calls) >= 3, len(calls))
    check("every _chat call goes through _payload",
          all(c["via_payload"] for c in calls),
          [c for c in calls if not c["via_payload"]])
    check("no _chat call receives the raw list",
          not any(c["raw_list"] for c in calls), calls)
    check("the forced wrap-up is the only caller that omits the state block",
          sum(1 for c in calls if c["state_false"]) == 1,
          sum(1 for c in calls if c["state_false"]))
    check("the tool-calling calls pass the session through, so the plan and runway "
          "reach the payload",
          sum(1 for c in calls if c["session"] and not c["state_false"]) >= 2,
          sum(1 for c in calls if c["session"]))


def test_history_trims_in_blocks_not_every_turn():
    redirect_files()
    fb.CONFIG["agent"]["history_exchanges"] = 10          # keep = 20 messages
    hist = fb.AGENT._history("trim-test")
    hist.clear()
    for i in range(20):
        hist.append({"role": "user" if i % 2 == 0 else "assistant",
                     "content": f"m{i}"})
    fb.AGENT._trim_history("trim-test")
    check("at the limit nothing is dropped (no cache churn)", len(hist) == 20,
          len(hist))
    hist.append({"role": "user", "content": "one more"})
    fb.AGENT._trim_history("trim-test")
    check("over the limit it cuts deep, not by one exchange",
          len(hist) == 10, len(hist))
    check("the newest message survives", hist[-1]["content"] == "one more")
    for i in range(5):
        hist.append({"role": "assistant", "content": f"a{i}"})
    fb.AGENT._trim_history("trim-test")
    check("hysteresis: no second trim for several more turns",
          len(hist) == 15, len(hist))


def test_compact_goes_deep_then_stays_put():
    redirect_files()
    try:
        fb.CONFIG["llm"]["max_context_tokens"] = 4000   # the ceiling IS the budget here
        _window_stub(0)                                # no server reporting a window
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(12):
            msgs.append({"role": "user", "content": f"u{i} " + "x" * 3000})
            msgs.append({"role": "assistant", "content": ""})
            msgs.append({"role": "tool", "tool_call_id": str(i),
                         "content": "y" * 3000})
        fb.AGENT._compact(msgs)
        # The unit is the CONVERSATION (messages[0] is counted by `static`, not here), and
        # _compact lands it on its own low-water mark: low = max(2000, int(budget * 0.6))
        # where budget is the messages budget left after the trailing state block.
        est = fb.AGENT._conversation_token_est(msgs)
        budget = (fb.AGENT._context_budget()
                  - fb.est_tokens(fb.volatile_context()))
        low = max(2000, int(budget * 0.6))
        check("lands under the low-water mark, not merely the budget",
              est <= low, (est, low))
        check("system prompt survives compaction", msgs[0]["content"] == "sys")
        check("no orphan tool message",
              all(msgs[i].get("role") != "tool" or
                  msgs[i - 1].get("role") in ("tool", "assistant")
                  for i in range(1, len(msgs))))
        again = copy.deepcopy(msgs)
        fb.AGENT._compact(again)
        check("a second compaction is a no-op (no per-turn cache churn)",
              [m["content"] for m in again] == [m["content"] for m in msgs])
    finally:
        redirect_files()
        _budget_clear()


def test_compaction_budget_counts_the_trailing_state_block():
    """A fat notes file must make compaction cut DEEPER, because the trailing
    block is part of the payload even though it is not in the message list."""
    def run(note_chars):
        redirect_files()
        fb.CONFIG["agent"]["notes_max_chars"] = 9000
        fb.CONFIG["llm"]["max_context_tokens"] = 6000
        # No server: the configured ceiling IS the budget (deterministic, no probe). The
        # trailing block is then bounded by mem_limit_chars, which scales with the window
        # (audit D5), so the absolute size of the block is not a fixed number any more -
        # what this test grades is that a bigger block is CHARGED to compaction.
        _window_stub(0)
        if note_chars:
            fb.NOTES_FILE.write_text("- [10:00] " + "n" * note_chars,
                                     encoding="utf-8")
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(60):
            msgs.append({"role": "user", "content": f"u{i} " + "x" * 200})
            msgs.append({"role": "assistant", "content": ""})
            msgs.append({"role": "tool", "tool_call_id": str(i),
                         "content": "y" * 200})
        fb.AGENT._compact(msgs)
        raw = fb.volatile_context()
        return (fb.AGENT._conversation_token_est(msgs), fb.est_tokens(raw), raw)

    est_small, vol_small, raw_small = run(0)
    est_big, vol_big, raw_big = run(7000)
    # v2.0.0: the block is never empty, it is the clock and nothing else, so the
    # baseline is small rather than absent (~25 tokens)
    check("no notes and no ledger -> the trailing block is only the clock",
          raw_small.startswith(fb._STATE_MARKER) and "Current date and time" in raw_small
          and "Notes from previous sessions" not in raw_small and vol_small < 60,
          (vol_small, raw_small[:90]))
    check("the state block is genuinely large",
          vol_big > 200 and vol_big > 5 * vol_small, (vol_big, vol_small))
    check("a large state block makes compaction cut deeper",
          est_big < est_small, f"{est_small} vs {est_big}")
    # The budget is the CONVERSATION's, and the trailing block is part of the payload even
    # though it is not in the message list: what compaction leaves must fit under the budget
    # once the block is counted too.
    budget = fb.AGENT._context_budget()
    check("payload + state block fits the budget",
          est_big + vol_big <= budget, f"{est_big} + {vol_big} vs {budget}")
    redirect_files()
    _budget_clear()


# What the model box accepts in ONE request. Re-measured 2026-09-16 from the
# server itself (`GET http://the LAN model box:8081/props`): total_slots 3,
# default_generation_settings.n_ctx 262144, i.e. -c 786432 split three ways.
# It was 131072 when the box ran two slots, and this constant was left at the
# old number, so the check failed on a config that in fact fits — a stale
# constant just makes a suite red and hides the failures that matter.
SERVER_WINDOW = 262144


def test_tuning_defaults_are_the_agreed_ones():
    reset_config()
    # The old assertion here pinned ">= 150000" — which is how the budget ended
    # up 65k over what the server can accept (n_ctx 262144 / total_slots 2 =
    # 131072 per request). A budget that does not fit makes the server reject the
    # payload, which triggers _force_shrink and leaves "[earlier context
    # dropped...]" in the transcript — so assert the INVARIANT, not a number.
    llm = fb.CONFIG["llm"]
    worst_case = (llm["max_context_tokens"] + llm["max_tokens"]
                  + 4000)          # ~3.3k of tool schemas + slack
    check("budget + generation + schemas fits the server window",
          worst_case <= SERVER_WINDOW, f"{worst_case} > {SERVER_WINDOW}")
    check("budget is not shrunk to nothing", llm["max_context_tokens"] >= 60000,
          llm["max_context_tokens"])
    check("tool output cap trimmed from 16k",
          fb.CONFIG["agent"]["tool_output_max_chars"] <= 12000,
          fb.CONFIG["agent"]["tool_output_max_chars"])
    check("sampling is pinned explicitly, not half-inherited",
          isinstance(llm.get("sampling"), dict) or llm.get("temperature") is None,
          llm.get("sampling"))
    # When this suite runs beside a real install, assert THAT config too: the
    # staged fixture proves the invariant holds for a sane config, the host file
    # proves the fleet's own numbers still fit the server it talks to.
    host_cfg = BASE / "config.json"
    if host_cfg.exists():
        h = json.loads(host_cfg.read_text(encoding="utf-8-sig"))["llm"]
        hval = h["max_context_tokens"]
        if ((isinstance(hval, str) and hval.strip().lower() == "auto")
                or hval in (None, "", 0)):
            # "auto" is the shipped default now: this host asks the endpoint what it serves
            # instead of asserting a number that cannot stay true across a restart (the
            # 2026-09-21 incident). There is nothing to add up - the budget IS the served
            # window less the reply and schema room - so the fit is true by construction.
            check("host config: the budget follows the endpoint (auto), not a number a "
                  "restart can invalidate", True)
        else:
            hworst = hval + h["max_tokens"] + 4000
            check("host config: budget + generation + schemas fits the window",
                  hworst <= SERVER_WINDOW, f"{hworst} > {SERVER_WINDOW}")
            check("host config: budget is not shrunk to nothing",
                  hval >= 60000, hval)

# --- tool pairing -----------------------------------------------------------
# Live failure 2026-09-11 on api.deepseek.com: the loop guard nudged the model
# after the FIRST of two batched tool calls, so a user message landed between
# the two tool results. DeepSeek rejected the entire request (400 "an assistant
# message with 'tool_calls' must be followed by tool messages responding to each
# 'tool_call_id'") and the task never ran. The local llama.cpp server had
# accepted that shape for weeks, because it does not validate. Three guards now:
# the nudge is queued, an unanswered call gets an explicit result, and the one
# choke point every payload passes repairs and logs.

def _batched_turn(nudge_inside=True, both_results=True,
                  nudge="SYSTEM: stop repeating yourself"):
    c1 = {"id": "call_a", "type": "function",
          "function": {"name": "shell", "arguments": "{\"command\": \"ls\"}"}}
    c2 = {"id": "call_b", "type": "function",
          "function": {"name": "read_file",
                       "arguments": "{\"path\": \"/etc/hosts\"}"}}
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "do the thing"},
            {"role": "assistant", "content": "", "tool_calls": [c1, c2]},
            {"role": "tool", "tool_call_id": "call_a", "content": "a"},
            {"role": "tool", "tool_call_id": "call_b", "content": "b"}]
    if not both_results:
        msgs = msgs[:4]
    if nudge_inside:
        msgs.insert(4, {"role": "user", "content": nudge})
    return msgs


def test_a_batched_tool_turn_with_adjacent_results_is_valid():
    msgs = _batched_turn(nudge_inside=False)
    probs = fb._tool_pairing_problems(msgs)
    check("a batched turn with contiguous tool results has no pairing problem",
          probs == [], probs)


def test_a_user_message_between_batched_tool_results_is_flagged():
    probs = fb._tool_pairing_problems(_batched_turn(nudge_inside=True))
    check("a nudge inside a tool batch is reported as a violation",
          bool(probs), probs)
    check("and the report names the unanswered tool_call_id",
          any("call_b" in p for p in probs), probs)


def test_payload_repairs_a_nudge_that_landed_inside_a_batch():
    msgs = _batched_turn(nudge_inside=True)
    out = fb.AGENT._payload([dict(m) for m in msgs], state=False)
    probs = fb._tool_pairing_problems(out)
    check("the repaired payload is valid for a strict provider",
          probs == [], probs)
    ids = [m.get("tool_call_id") for m in out if m.get("role") == "tool"]
    check("both tool results survive, in call order",
          ids == ["call_a", "call_b"], ids)
    users = [m.get("content") for m in out if m.get("role") == "user"]
    check("the nudge is kept, just moved after the block",
          users == ["do the thing", "SYSTEM: stop repeating yourself"], users)
    check("nothing was dropped by the repair",
          len(out) == len(msgs), f"{len(out)} vs {len(msgs)}")


def test_payload_answers_a_tool_call_that_produced_no_result():
    msgs = _batched_turn(nudge_inside=False, both_results=False)
    check("a missing tool result is flagged as a violation",
          bool(fb._tool_pairing_problems(msgs)))
    out = fb.AGENT._payload([dict(m) for m in msgs], state=False)
    check("the repaired payload is valid",
          fb._tool_pairing_problems(out) == [],
          fb._tool_pairing_problems(out))
    filler = [m for m in out if m.get("role") == "tool"
              and m.get("tool_call_id") == "call_b"]
    check("the unanswered call gets an explicit tool result",
          len(filler) == 1, len(filler))
    check("and that result says the call did not complete",
          "did not complete" in (filler[0]["content"] if filler else ""),
          filler[0]["content"] if filler else None)


def test_the_loop_guard_nudge_waits_for_the_whole_tool_batch():
    src = (SRC).read_text(encoding="utf-8")
    i, j = src.find("nudges.append("), src.find("you have now made this exact")
    check("the loop-guard nudge is queued, not appended mid-batch",
          0 <= i < j and (j - i) < 120, f"{i} {j}")
    check("and it is flushed after the per-call loop",
          "for _nudge in nudges:" in src)
    check("_payload repairs pairing at the one choke point",
          "messages = _repair_tool_arguments(_repair_tool_pairing(messages))" in src
          or "messages = _repair_tool_pairing(messages)" in src)
    check("...and repairs malformed tool-call arguments on the way out",
          "_repair_tool_arguments(_repair_tool_pairing(" in src)
    check("a tool call that produced no result is answered, never skipped",
          "this call did not\n" in src or "this call did not " in src)

# --- restart ownership ------------------------------------------------------
# /restart used to always spawn a detached copy of itself: a second process on
# disk while the first is still alive, with the instance lock deciding which
# survives. On a supervised Windows host the supervisor's child lost that race
# ("bot exited 3 (lock held elsewhere)") and the supervisor backed off 300 s;
# under systemd it produced two instances fighting over the same port. The owner
# now decides.

# restart_owner() reads exactly these three. A suite asserting about the owner must OWN
# all three: measured 2026-09-26, a macOS shell carries XPC_SERVICE_NAME=0 (launchd sets it
# in every process it starts, and "0" is not our label), so leaving it inherited made the
# "self" case assert about the host's own session instead of about the branch.
_RESTART_MARKERS = ("INVOCATION_ID", "TINYCMDR_SUPERVISED", "XPC_SERVICE_NAME")
_UNOWNED = {k: None for k in _RESTART_MARKERS}


def _with_restart_env(env, fn):
    saved = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        return fn()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_restart_owner_reads_the_environment():
    def run():
        out = []
        for k in _RESTART_MARKERS:
            os.environ.pop(k, None)
        out.append(("self", fb.restart_owner()))
        os.environ["TINYCMDR_SUPERVISED"] = "1"
        out.append(("supervisor", fb.restart_owner()))
        os.environ["INVOCATION_ID"] = "test-unit-start"
        out.append(("systemd", fb.restart_owner()))
        os.environ.pop("INVOCATION_ID")
        os.environ.pop("TINYCMDR_SUPERVISED")
        os.environ["XPC_SERVICE_NAME"] = "com.tinycmdr.agent"
        out.append(("launchd", fb.restart_owner()))
        # The marker a plain GUI app carries must NOT read as our supervisor: launchd sets
        # XPC_SERVICE_NAME in every process it starts, so only OUR label means launchd owns
        # this bot. (A darwin catch-all made a hand-started mac bot take the hand-over path
        # and stay dead - measured 2026-09-26, reported as a code site.)
        os.environ["XPC_SERVICE_NAME"] = "com.apple.Terminal"
        out.append(("self", fb.restart_owner()))
        return out
    got = _with_restart_env(dict(_UNOWNED), run)
    for want, actual in got:
        check(f"restart owner with that environment is '{want}'",
              actual == want, actual)


def _restart_with(env):
    """Call perform_restart without touching the real process, report what it did."""
    calls = {"spawn": 0, "code": None}
    real_exit, real_sleep, real_spawn = os._exit, time.sleep, fb._spawn_replacement

    class _Exited(Exception):
        pass

    def fake_exit(code):
        raise _Exited(code)

    def fake_spawn():
        calls["spawn"] += 1

    os._exit = fake_exit
    time.sleep = lambda _s: None
    fb._spawn_replacement = fake_spawn

    def run():
        try:
            fb.perform_restart(by="test")
        except _Exited as e:
            calls["code"] = e.args[0]
        except Exception as e:            # noqa: BLE001 - report, do not mask
            calls["error"] = repr(e)
        return calls

    try:
        return _with_restart_env(env, run)
    finally:
        os._exit = real_exit
        time.sleep = real_sleep
        fb._spawn_replacement = real_spawn


def test_restart_hands_over_instead_of_spawning_when_supervised():
    c = _restart_with({"TINYCMDR_SUPERVISED": "1", "INVOCATION_ID": None,
                       "XPC_SERVICE_NAME": None})
    check("a supervised restart does not spawn a second process",
          c["spawn"] == 0, c)
    check("it exits with the hand-over code", c["code"] == fb.RESTART_EXIT_CODE, c)
    check("which is not 0, so a supervisor can tell it from a clean stop",
          c["code"] != 0, c)


def test_restart_hands_over_under_systemd():
    c = _restart_with({"INVOCATION_ID": "test-unit-start", "TINYCMDR_SUPERVISED": None,
                       "XPC_SERVICE_NAME": None})
    check("under systemd it does not spawn either", c["spawn"] == 0, c)
    check("and it exits with the hand-over code", c["code"] == fb.RESTART_EXIT_CODE, c)


def test_restart_hands_over_under_launchd():
    """The macOS lane, graded on every host: launchd owns the job, so exiting 75 IS the
    restart (KeepAlive.SuccessfulExit=false). This is the only place the lane is
    measurable - the plist's own interpreter here is python, and a launchd job's process
    sees XPC_SERVICE_NAME=<label> (measured 2026-09-26, throwaway label)."""
    c = _restart_with({"XPC_SERVICE_NAME": "com.tinycmdr.agent",
                       "TINYCMDR_SUPERVISED": None, "INVOCATION_ID": None})
    check("under launchd it does not spawn either", c["spawn"] == 0, c)
    check("and it exits with the code KeepAlive.SuccessfulExit=false relaunches on",
          c["code"] == fb.RESTART_EXIT_CODE, c)


def test_restart_spawns_a_replacement_when_nobody_owns_it():
    c = _restart_with(_UNOWNED)
    check("with no owner it spawns exactly one replacement", c["spawn"] == 1, c)
    check("and exits 0, because the replacement is already up", c["code"] == 0, c)

def test_a_spawned_replacement_re_reads_dot_env():
    """A changed .env must take effect on /restart.

    The child inherits our environment and _load_env_file never overwrites an
    inherited key, so without stripping them an edited token would be masked by
    the stale copy (live: skyteck kept running as the old bot account after its
    token was replaced, and /restart looked like it had ignored the edit).
    """
    envf = fb.ENV_FILE
    saved = envf.read_text(encoding="utf-8") if envf.exists() else None
    stale = os.environ.get("TINYCMDR_MM_TOKEN")
    captured = {}
    real_popen = fb.subprocess.Popen

    class _Fake:
        def __init__(self, *a, **kw):
            captured.update(kw)

    fb.subprocess.Popen = _Fake
    try:
        envf.write_text("TINYCMDR_MM_TOKEN=file-value\nOTHER=kept\n", encoding="utf-8")
        os.environ["TINYCMDR_MM_TOKEN"] = "stale-inherited-value"
        check("the file's keys are found", "TINYCMDR_MM_TOKEN" in fb._env_file_keys(),
              fb._env_file_keys())
        fb._spawn_replacement()
    finally:
        fb.subprocess.Popen = real_popen
        if saved is None:
            envf.unlink(missing_ok=True)
        else:
            envf.write_text(saved, encoding="utf-8")
        if stale is None:
            os.environ.pop("TINYCMDR_MM_TOKEN", None)
        else:
            os.environ["TINYCMDR_MM_TOKEN"] = stale
    env = captured.get("env") or {}
    check("the child does not inherit the stale .env value",
          "TINYCMDR_MM_TOKEN" not in env, env.get("TINYCMDR_MM_TOKEN"))
    check("keys that .env does not own are still inherited",
          "PATH" in env or "PYTHONPATH" in env, list(env)[:4])



# --------------------------------------------------- stage 3: SSE streaming

def test_stream_chat_assembles_content_reasoning_and_usage():
    body = _sse(
        _chunk({"reasoning_content": "let me think"}),
        _chunk({"content": "Hel"}),
        _chunk({"content": "lo"}),
        _chunk({}, finish="stop"),
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2},
         "timings": {"predicted_per_second": 24.5}})
    data, stats = fb._stream_chat(StreamResp(body))
    msg = data["choices"][0]["message"]
    check("stream: content assembled", msg["content"] == "Hello", msg)
    check("stream: reasoning assembled", msg["reasoning_content"] == "let me think", msg)
    check("stream: finish reason kept", data["choices"][0]["finish_reason"] == "stop", data)
    check("stream: usage taken from the final chunk",
          data["usage"]["completion_tokens"] == 2, data)
    check("stream: server-reported decode rate recorded",
          abs(stats.get("server_tps", 0) - 24.5) < 0.01, stats)
    check("stream: first-delta latency measured", stats.get("ttft") is not None, stats)
    check("stream: chunks counted", stats.get("deltas") == 4, stats)


def test_stream_chat_merges_fragmented_tool_calls():
    body = _sse(
        _chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                "function": {"name": "shell", "arguments": ""}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"comm'}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'and": "hostname"}'}}]}),
        _chunk({"tool_calls": [{"index": 1, "id": "call_2", "type": "function",
                                "function": {"name": "list_tools", "arguments": "{}"}}]}),
        _chunk({}, finish="tool_calls"))
    data, _ = fb._stream_chat(StreamResp(body))
    tcs = data["choices"][0]["message"].get("tool_calls") or []
    check("stream: both tool calls reassembled", len(tcs) == 2, tcs)
    check("stream: argument fragments merged across chunks",
          tcs and tcs[0]["function"]["arguments"] == '{"command": "hostname"}', tcs)
    check("stream: ids and names survive the split",
          tcs and tcs[0]["id"] == "call_1" and tcs[0]["function"]["name"] == "shell"
          and tcs[1]["id"] == "call_2", tcs)
    check("stream: finish=tool_calls preserved",
          data["choices"][0]["finish_reason"] == "tool_calls", data)


def test_stream_chat_tolerates_comments_keepalives_and_torn_lines():
    body = (b": keep-alive\n\n" + b"\n" + b"data: {not json}\n\n"
            + b"data: " + json.dumps(_chunk({"content": "ok"})).encode() + b"\n\n"
            + b"data: [DONE]\n\n")
    data, _ = fb._stream_chat(StreamResp(body))
    check("stream: SSE noise and a torn line do not break the call",
          data["choices"][0]["message"]["content"] == "ok", data)


def test_stream_idle_gap_ends_the_call_and_closes_it():
    resp = StreamResp(_sse(_chunk({"content": "partial"}), done=False), stall=True)
    t0 = time.time()
    try:
        fb._stream_chat(resp, idle_seconds=1)
        check("stream: an idle stream is ended", False, "no exception raised")
    except fb.StreamFailed as e:
        elapsed = time.time() - t0
        check("stream: an idle stream is ended", True)
        check("stream: it ends on the idle gap, not the request timeout",
              elapsed < 5, f"{elapsed:.1f}s")
        check("stream: the wedged stream is closed", resp.closed, "close() not called")
        check("stream: the reason names the quiet gap", "quiet" in str(e), str(e))
    except Exception as e:
        check("stream: an idle stream is ended", False, repr(e))


def test_a_streaming_cancel_hangs_up_on_the_server():
    """The acceptance test for stage 3, and the other half of /stop: a cancel while
    streaming must CLOSE the socket, because that is what makes a local llama.cpp stop
    generating. Proven against a socket that reports the peer hang-up (the
    non-streaming path used to pin saw_close False here)."""
    srv = DribbleServer(interval=0.05, duration=10.0)
    srv.start()
    url = f"http://127.0.0.1:{srv.port}/v1/chat/completions"
    ev = threading.Event()
    box = {}

    def call():
        try:
            resp = fb._post_watchdog(url, {}, {}, timeout=5, grace=1,
                                     cancel_event=ev, stream=True)
            fb._stream_chat(resp, cancel_event=ev, idle_seconds=60)
            box["r"] = "returned"
        except fb.OperatorStop:
            box["r"] = "stopped"
        except BaseException as e:            # noqa: BLE001 - reported below
            box["r"] = f"{type(e).__name__}: {e}"

    th = threading.Thread(target=call, daemon=True)
    th.start()
    time.sleep(0.8)
    ev.set()
    th.join(8)
    check("stream cancel: the call stops with OperatorStop",
          box.get("r") == "stopped", box)
    deadline = time.time() + 5
    while time.time() < deadline and not srv.saw_close:
        time.sleep(0.1)
    check("stream cancel: the SERVER sees the hang-up (this is what ends the GPU work)",
          srv.saw_close, "the socket stayed open")
    srv.stop()


def test_chat_retries_the_same_endpoint_without_streaming_when_the_stream_fails():
    """A server that ignores "stream": true answers with plain JSON. Reporting that as
    an empty answer would be a silent wrong answer, so the call is retried on the SAME
    endpoint without streaming - not demoted to another provider."""
    saved_cfg = copy.deepcopy(fb.CONFIG)
    fb.CONFIG["llm"]["stream"] = True
    fb._STREAM_UNSUPPORTED.clear()
    try:
        usage = {}
        not_sse = StreamResp(b'{"choices":[{"message":{"content":"ignored"}}]}')
        good = ok_resp("recovered without streaming")
        resp, calls = with_fake_post(
            [not_sse, good],
            lambda: fb.AGENT._chat([{"role": "user", "content": "hi"}], usage=usage))
        check("stream fallback: the same endpoint is retried",
              len(calls) == 2 and calls[0]["url"] == calls[1]["url"], calls)
        check("stream fallback: the first attempt asked for a stream",
              calls[0].get("stream") is True, calls[0].get("stream"))
        check("stream fallback: the retry does not",
              calls[1].get("stream") is not True, calls[1].get("stream"))
        check("stream fallback: no key named stream is left in the payload",
              "stream" not in (calls[1]["payload"] or {}), calls[1]["payload"])
        check("stream fallback: the answer arrives",
              resp.get("content") == "recovered without streaming", resp)
        check("stream fallback: the endpoint is remembered as stream-less",
              any(u.endswith("/chat/completions") for u in fb._STREAM_UNSUPPORTED),
              fb._STREAM_UNSUPPORTED)
        check("stream fallback: it is recorded as a failed attempt",
              any(a["outcome"] == "error" and "stream" in a["detail"]
                  for a in usage.get("attempts", [])), usage.get("attempts"))
    finally:
        fb.CONFIG.clear()
        fb.CONFIG.update(saved_cfg)



def test_a_mid_stream_break_does_not_blacklist_streaming():
    """A break after the first deltas is a transient failure, not proof the endpoint
    cannot stream. It used to add the URL to _STREAM_UNSUPPORTED (a module global) and
    set stream_on=False - so one hiccup ended streaming for the whole process, and for
    the remaining failover endpoints of that call. Only the "server ignored stream:true
    and answered with plain JSON" cause is remembered now."""
    saved_cfg = copy.deepcopy(fb.CONFIG)
    fb.CONFIG["llm"]["stream"] = True
    fb._STREAM_UNSUPPORTED.clear()

    class BreakAfterDeltas(StreamResp):
        """Delivers one delta and then the reader dies: the mid-stream break (the 2129
        path), which is NOT a server that cannot stream."""

        def iter_lines(self, decode_unicode=False):
            for line in _sse(_chunk({"content": "partial"}), done=False).splitlines():
                yield line
            raise fb.requests.ConnectionError("connection reset by peer")

    try:
        resp, calls = with_fake_post(
            [BreakAfterDeltas(), ok_resp("recovered without streaming")],
            lambda: fb.AGENT._chat([{"role": "user", "content": "hi"}]))
        check("mid-stream break: the same endpoint is retried",
              len(calls) == 2 and calls[0]["url"] == calls[1]["url"], calls)
        check("mid-stream break: the first attempt asked for a stream",
              calls[0].get("stream") is True, calls[0].get("stream"))
        check("mid-stream break: the answer arrives",
              resp.get("content") == "recovered without streaming", resp)
        check("mid-stream break: the endpoint is NOT remembered as stream-less",
              not any(u.endswith("/chat/completions") for u in fb._STREAM_UNSUPPORTED),
              fb._STREAM_UNSUPPORTED)
        # The process-level promise: a later call must stream again.
        _r, calls2 = with_fake_post(
            [StreamResp(_sse(_chunk({"content": "streamed"}), _chunk({}, finish="stop")))],
            lambda: fb.AGENT._chat([{"role": "user", "content": "again"}]))
        check("mid-stream break: a later call still asks to stream",
              calls2[0].get("stream") is True, calls2[0].get("stream"))
    finally:
        fb.CONFIG.clear()
        fb.CONFIG.update(saved_cfg)


def test_a_cancel_is_noticed_while_chunks_are_still_flowing():
    """Caught LIVE on the manager box 2026-09-12: /stop arrived mid-generation and the run kept
    going, because the cancel was only checked when the queue went EMPTY - and a busy
    stream never goes empty. The check now runs on every iteration."""

    class BusyStream(StreamResp):
        def iter_lines(self, decode_unicode=False):
            for _ in range(400):
                yield b"data: " + json.dumps(_chunk({"content": "x"})).encode()
                time.sleep(0.02)

    resp = BusyStream()
    ev = threading.Event()
    box = {}

    def run():
        try:
            fb._stream_chat(resp, cancel_event=ev, idle_seconds=30)
            box["r"] = "returned"
        except fb.OperatorStop:
            box["r"] = "stopped"
        except BaseException as e:            # noqa: BLE001 - reported below
            box["r"] = f"{type(e).__name__}: {e}"

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(0.4)
    ev.set()
    th.join(6)
    check("busy stream: a cancel while chunks flow still stops the call",
          box.get("r") == "stopped", box)
    check("busy stream: the connection is dropped, not left running",
          resp.closed, "not closed")



# --------------------------------------------- both sides: LAN endpoint vs cloud

CLOUD_URL = "https://api.deepseek.com/v1"
CLOUD_MODEL = "deepseek-v4-flash"


def _both_sides(allow_cloud):
    """A config with a LAN primary AND a cloud fallback, as every fleet host has."""
    fb.CONFIG["llm"]["fallbacks"] = [{"base_url": CLOUD_URL, "model": CLOUD_MODEL,
                                      "alias": "cloud", "api_key": "k-cloud"}]
    fb.CONFIG["llm"]["allow_cloud_fallback"] = allow_cloud


def _catalog_both_sides():
    return [{"name": "main", "url": fb.CONFIG["llm"]["base_url"], "local": True,
             "alias": False, "send_as": "main", "key": "none"},
            {"name": CLOUD_MODEL, "url": CLOUD_URL, "local": False, "alias": False,
             "send_as": CLOUD_MODEL, "key": "k-cloud"},
            {"name": "cloud", "url": CLOUD_URL, "local": False, "alias": True,
             "send_as": CLOUD_MODEL, "key": "k-cloud"}]


def test_local_and_cloud_are_classified_correctly():
    """Everything else rests on this split: the failover gate, the streaming option set,
    and the privacy promise that a local failure does not reach the internet."""
    for url in ("http://10.20.30.40:8081/v1", "http://127.0.0.1:8081/v1",
                "http://[redacted]:8080/v1", "http://172.16.5.4:8081/v1",
                "http://172.31.255.1/v1", "http://nas-bot.local:8081/v1"):
        check(f"local: {url}", fb._is_local_url(url) is True, url)
    for url in ("https://api.deepseek.com/v1", "https://api.moonshot.ai/v1",
                "https://chat.example.com/api", "http://8.8.8.8:8081/v1",
                "http://172.32.0.1/v1", "http://192.169.0.1/v1"):
        check(f"cloud: {url}", fb._is_local_url(url) is False, url)


def test_an_explicit_cloud_model_reaches_the_cloud_endpoint_first():
    """The live lesson from a bot account: a name that matches a fallback routes THERE, even when
    the primary is a LAN box that would happily accept the request and ignore the model
    field. Getting this wrong is silent - the answer looks fine and comes from the wrong
    place - so it is pinned for both the model id and the alias."""
    saved = copy.deepcopy(fb.CONFIG)
    try:
        _both_sides(allow_cloud=True)
        for want in (CLOUD_MODEL, "cloud"):
            resp, calls = with_fake_post([ok_resp("from the cloud")],
                                         lambda: fb.AGENT._chat(
                                             [{"role": "user", "content": "hi"}],
                                             model=want),
                                         catalog=_catalog_both_sides())
            check(f"cloud {want}: the first call goes to the cloud endpoint",
                  calls and calls[0]["url"].startswith(CLOUD_URL), [c["url"] for c in calls])
            check(f"cloud {want}: with the cloud endpoint's own key",
                  calls and calls[0]["headers"].get("Authorization") == "Bearer k-cloud",
                  calls[0].get("headers"))
            check(f"cloud {want}: sent as the model that endpoint serves",
                  calls and calls[0]["payload"]["model"] == CLOUD_MODEL, calls[0]["payload"].get("model"))
            check(f"cloud {want}: the answer comes back",
                  resp.get("content") == "from the cloud", resp)
    finally:
        fb.CONFIG.clear()
        fb.CONFIG.update(saved)


def test_a_local_model_stays_local_and_is_not_given_a_cloud_key():
    saved = copy.deepcopy(fb.CONFIG)
    try:
        _both_sides(allow_cloud=True)
        resp, calls = with_fake_post([ok_resp("from the box")],
                                     lambda: fb.AGENT._chat(
                                         [{"role": "user", "content": "hi"}], model="main"),
                                     catalog=_catalog_both_sides())
        lan_chat = fb.CONFIG["llm"]["base_url"].rstrip("/") + "/chat/completions"
        check("local model: the call stays on the LAN endpoint",
              calls and calls[0]["url"] == lan_chat, [c["url"] for c in calls])
        check("local model: no cloud key is attached",
              calls and calls[0]["headers"].get("Authorization") == "Bearer none",
              calls[0].get("headers"))
        check("local model: the answer comes back",
              resp.get("content") == "from the box", resp)
    finally:
        fb.CONFIG.clear()
        fb.CONFIG.update(saved)


def test_cloud_failover_is_off_by_default_and_that_is_the_privacy_guarantee():
    """allow_cloud_fallback=false: a LAN failure must FAIL, not quietly ship the
    conversation to a provider. Verified by proving the cloud endpoint is never called."""
    saved = copy.deepcopy(fb.CONFIG)
    try:
        _both_sides(allow_cloud=False)
        resp, calls = with_fake_post(
            [FakeResp(500, text="box is unhappy"), ok_resp("cloud would have answered")],
            lambda: _try_chat(),
            catalog=_catalog_both_sides())
        check("privacy: the cloud endpoint was never called",
              all(not c["url"].startswith(CLOUD_URL) for c in calls), [c["url"] for c in calls])
        check("privacy: the run reports infrastructure failure, not an answer",
              isinstance(resp, fb.InfraError), resp)
    finally:
        fb.CONFIG.clear()
        fb.CONFIG.update(saved)


def test_cloud_failover_works_when_the_operator_allows_it():
    saved = copy.deepcopy(fb.CONFIG)
    try:
        _both_sides(allow_cloud=True)
        usage = {}
        resp, calls = with_fake_post(
            [FakeResp(500, text="box is unhappy"), ok_resp("from the cloud")],
            lambda: _try_chat(usage=usage),
            catalog=_catalog_both_sides())
        check("failover: the cloud endpoint took over",
              any(c["url"].startswith(CLOUD_URL) for c in calls), [c["url"] for c in calls])
        check("failover: the answer came from the cloud",
              resp == "from the cloud", resp)
        # usage['failovers'] lists the endpoints that FAILED (the local box here), not
        # where the call ended up - the answer above already proves the cloud took it
        check("failover: the local failure is on the record",
              any(u.startswith("http://127.0.0.1") for u in usage.get("failovers", [])),
              usage.get("failovers"))
        check("failover: the local attempt is on the record",
              any(a["outcome"] in ("error", "fatal") for a in usage.get("attempts", [])),
              usage.get("attempts"))
    finally:
        fb.CONFIG.clear()
        fb.CONFIG.update(saved)


def _try_chat(usage=None):
    """Run one chat call and return either the content or the InfraError raised."""
    try:
        r = fb.AGENT._chat([{"role": "user", "content": "hi"}], usage=usage)
        return (r.get("content") or "")
    except fb.InfraError as e:
        return e


def test_streaming_asks_for_usage_on_the_lan_but_not_from_a_provider():
    """stream_options is a llama.cpp nicety: a provider that does not know it can answer
    400, so it goes only to a LAN endpoint. Both directions are pinned - the option's
    presence is what makes tok/s exact locally, its absence is what keeps cloud calls
    working at all."""
    saved = copy.deepcopy(fb.CONFIG)
    try:
        _both_sides(allow_cloud=True)
        fb.CONFIG["llm"]["stream"] = True
        fb._STREAM_UNSUPPORTED.clear()
        body = _sse(_chunk({"content": "local"}), _chunk({}, finish="stop"))
        _, calls = with_fake_post([StreamResp(body)],
                                  lambda: fb.AGENT._chat(
                                      [{"role": "user", "content": "hi"}], model="main"),
                                  catalog=_catalog_both_sides())
        check("streaming/local: the request asks to stream", calls[0].get("stream") is True,
              calls[0].get("stream"))
        check("streaming/local: usage is requested (llama.cpp answers with it)",
              calls[0]["payload"].get("stream_options") == {"include_usage": True},
              calls[0]["payload"].get("stream_options"))

        fb._STREAM_UNSUPPORTED.clear()
        body2 = _sse(_chunk({"content": "cloud"}), _chunk({}, finish="stop"))
        _, calls2 = with_fake_post([StreamResp(body2)],
                                   lambda: fb.AGENT._chat(
                                       [{"role": "user", "content": "hi"}], model=CLOUD_MODEL),
                                   catalog=_catalog_both_sides())
        check("streaming/cloud: the provider is asked to stream",
              calls2[0].get("stream") is True, calls2[0].get("stream"))
        check("streaming/cloud: NO stream_options (a provider may reject it)",
              "stream_options" not in (calls2[0]["payload"] or {}),
              sorted((calls2[0]["payload"] or {}).keys()))
    finally:
        fb.CONFIG.clear()
        fb.CONFIG.update(saved)


def test_a_cloud_stream_without_usage_still_answers_and_marks_the_estimate():
    """Cloud reality: usage may never arrive. The call must still produce the answer, and
    the token figures must be marked as estimates rather than silently invented."""
    saved = copy.deepcopy(fb.CONFIG)
    try:
        _both_sides(allow_cloud=True)
        fb.CONFIG["llm"]["stream"] = True
        fb._STREAM_UNSUPPORTED.clear()
        usage = {}
        body = _sse(_chunk({"content": "no usage here"}), _chunk({}, finish="stop"))
        resp, _ = with_fake_post([StreamResp(body)],
                                 lambda: fb.AGENT._chat(
                                     [{"role": "user", "content": "hi"}], model=CLOUD_MODEL,
                                     usage=usage),
                                 catalog=_catalog_both_sides())
        check("cloud stream: the answer arrives without a usage chunk",
              resp.get("content") == "no usage here", resp)
        check("cloud stream: the figures are flagged as estimates",
              usage.get("estimated") is True, usage)
        check("cloud stream: the call still counts",
              usage.get("calls") == 1 and usage.get("streamed") == 1, usage)
    finally:
        fb.CONFIG.clear()
        fb.CONFIG.update(saved)


def test_a_rejected_cloud_request_names_the_config_not_the_model():
    """A 401/403/404 from a provider is a key/model/base_url problem. The bot must say so
    instead of reporting a model failure - that message is what sent the operator looking
    in the wrong place the first time a cloud key went stale."""
    saved = copy.deepcopy(fb.CONFIG)
    try:
        _both_sides(allow_cloud=True)
        resp, calls = with_fake_post(
            [FakeResp(401, text="invalid api key")],
            lambda: _try_chat(),
            catalog=[{"name": CLOUD_MODEL, "url": CLOUD_URL, "local": False,
                      "alias": False, "send_as": CLOUD_MODEL, "key": "stale"},
                     {"name": "cloud", "url": CLOUD_URL, "local": False, "alias": True,
                      "send_as": CLOUD_MODEL, "key": "stale"}])
        check("rejected cloud call: reported as infrastructure, not an answer",
              isinstance(resp, fb.InfraError), resp)
        check("rejected cloud call: the message points at the config",
              "key" in str(resp) and "base_url" in str(resp), str(resp))
    finally:
        fb.CONFIG.clear()
        fb.CONFIG.update(saved)


# --------------------------------------------------------------------------
# ledger integrity: survive a damaged file, and never splice a write
# --------------------------------------------------------------------------
# Found on the fleet manager 2026-09-16: tasks.json held a complete JSON document
# followed by a duplicated fragment ("Extra data: line 165 column 2"), the loader
# read that as "unreadable", and 20 items disappeared into a fresh ledger. Two
# fixes are pinned here: the file is replaced in one step, and items are only
# dropped when there is nothing parseable at all.

def test_a_damaged_ledger_is_salvaged_not_dropped():
    redirect_files()
    good = json.dumps({"items": [{"id": 1, "desc": "keep me", "status": "open",
                                  "note": ""}], "next_id": 2}, indent=2)
    fb.TASKS_FILE.write_text(good + '\n"desc": "a duplicated tail',
                             encoding="utf-8")
    t = fb.load_tasks()
    check("a tail-damaged ledger keeps its items",
          [i["desc"] for i in t["items"]] == ["keep me"], t)
    check("the damaged file is kept for inspection",
          sorted(p.name for p in TMP.glob("tasks.json.damaged-*")),
          sorted(p.name for p in TMP.iterdir()))
    check("the salvaged ledger needs no repair to render",
          "keep me" in fb.render_task_prompt())


def test_a_ledger_with_nothing_parseable_starts_fresh():
    redirect_files()
    fb.TASKS_FILE.write_text("not json at all", encoding="utf-8")
    t = fb.load_tasks()
    check("nothing salvageable -> an empty ledger, not an exception",
          t["items"] == [] and t["next_id"] == 1, t)
    check("no damaged copy is kept when there was nothing to keep",
          not list(TMP.glob("tasks.json.damaged-*")),
          sorted(p.name for p in TMP.iterdir()))


def test_state_files_are_replaced_atomically():
    redirect_files()
    calls = []
    real_replace = fb.os.replace
    try:
        fb.os.replace = lambda a, b: (calls.append((str(a), str(b))),
                                      real_replace(a, b))[1]
        fb.save_tasks({"items": [], "next_id": 1})
    finally:
        fb.os.replace = real_replace
    check("the ledger and its mirror are renamed into place",
          sorted(os.path.basename(c[1]) for c in calls) ==
          ["tasks.json", "tasks.md"], calls)
    check("each temp file is a sibling of its target (same filesystem)",
          all(os.path.dirname(c[0]) == os.path.dirname(c[1]) for c in calls),
          calls)
    check("no temp file is left behind",
          not list(TMP.glob("*.tmp-*")), sorted(p.name for p in TMP.iterdir()))


def test_a_failed_atomic_write_keeps_the_old_ledger():
    """BUGREPORT §D1 changed this contract: a save that cannot land must not touch the
    destination AT ALL, and it must say so. The old shape (fall back to writing the
    destination in place) is what spliced a half-written ledger on a full disk."""
    redirect_files()
    fb.save_tasks({"items": [{"id": 1, "desc": "first", "status": "open",
                              "note": ""}], "next_id": 2})
    before = fb.TASKS_FILE.read_bytes()
    records = []
    log = fb.logging.getLogger()
    handler = fb.logging.Handler()
    handler.emit = lambda rec: records.append(rec.getMessage())
    log.addHandler(handler)
    real_fsync = fb.os.fsync
    raised = None
    try:
        fb.os.fsync = lambda _fd: (_ for _ in ()).throw(OSError("disk gone"))
        try:
            fb.save_tasks({"items": [{"id": 2, "desc": "second", "status": "open",
                                      "note": ""}], "next_id": 3})
        except Exception as e:               # noqa: BLE001 - the point of the test
            raised = e
    finally:
        fb.os.fsync = real_fsync
        log.removeHandler(handler)
    check("a save that cannot land raises instead of writing the destination",
          raised is not None, raised)
    check("...and the ledger on disk is byte-identical to what was there",
          fb.TASKS_FILE.read_bytes() == before, fb.TASKS_FILE.read_bytes()[:200])
    loaded = json.loads(fb.TASKS_FILE.read_text(encoding="utf-8"))
    check("...so it still parses, with the old items and no splice",
          len(loaded["items"]) == 1 and loaded["items"][0]["desc"] == "first", loaded)
    check("...and the failure is named in the log",
          any("atomic write of tasks.json failed" in r for r in records), records[-3:])
    check("the failed attempt cleans up after itself",
          not list(TMP.glob("*.tmp-*")), sorted(p.name for p in TMP.iterdir()))



# ------------------------- 2026-09-21: the endpoint's window is the truth


def _window_stub(value):
    """Pretend the endpoint reported `value` tokens per request (0 = it said
    nothing). _context_budget/_endpoint_window memoise on the instance, so both
    caches have to go."""
    _budget_clear()
    fb.AGENT.__dict__["_window_cache"] = value


def test_a_restarted_endpoint_is_noticed_after_the_ttl():
    """A window cached for the life of the process cannot notice a server restarted
    into a smaller slot count, which is exactly the 2026-09-21 failure: the harness
    trusted a number the box no longer served and lost the turn. The cache has a TTL
    now, so a long run re-asks (audit, 2026-09-22)."""
    saved_cfg = fb.CONFIG
    saved_detect = fb._detect_window
    calls = {"n": 0}

    def detect(url, headers):
        calls["n"] += 1
        return 262144 if calls["n"] == 1 else 131072     # the box was restarted

    try:
        cfg = isolated_config()
        cfg["llm"]["max_context_tokens"] = "auto"
        cfg["llm"]["max_tokens"] = 16384
        fb._detect_window = detect
        _budget_clear()
        fb.AGENT.__dict__.pop("_window_cache", None)
        fb.AGENT.__dict__.pop("_window_at", None)
        first_env = fb.AGENT._envelope()
        check("ttl: the first budget is what the server reported",
              first_env["source"] == "server" and first_env["window"] == 262144
              and first_env["budget"] == max(fb.ENVELOPE_MIN_BUDGET, 262144 - first_env["static"]
                                             - first_env["reply"]), first_env)
        check("ttl: and it is not re-asked on every payload",
              fb.AGENT._context_budget() == first_env["budget"] and calls["n"] == 1, calls)
        # Back-date the entry itself, on the MONOTONIC clock the TTL is measured with, and
        # drop only what the envelope memoises: _budget_clear() would also drop the window
        # cache, so the TTL would not be what re-asks and this check would grade the clear.
        # (A scalar `_window_at` is not read here - once the map exists, only its entries are.)
        root = fb._endpoint_root(cfg["llm"]["base_url"])
        at, val = fb.AGENT._window_cache[root]
        fb.AGENT._window_cache[root] = (fb.now_mono() - (fb.WINDOW_TTL + 60), val)
        fb.AGENT.__dict__.pop("_envelope_cache", None)
        fb.AGENT.__dict__.pop("_budget_cache", None)
        second_env = fb.AGENT._envelope()
        check("ttl: past the TTL the endpoint is asked again, so a restarted box "
              "is noticed",
              second_env["window"] == 131072 and calls["n"] == 2
              and second_env["budget"] < first_env["budget"], (second_env, calls))
    finally:
        fb._detect_window = saved_detect
        fb.AGENT.__dict__.pop("_window_cache", None)
        fb.AGENT.__dict__.pop("_window_at", None)
        _budget_clear()
        fb.CONFIG = saved_cfg


def test_the_budget_is_clamped_by_what_the_endpoint_serves():
    """A host that says 200000 while the box actually serves 131072 sent a 126,261
    token prompt with 4,808 tokens of room to answer in and lost the turn
    (2026-09-21). The server's number is the truth: a configured budget that
    exceeds it is clamped, never trusted, and the clamp is not silent."""
    saved_cfg = fb.CONFIG
    try:
        cfg = isolated_config()
        cfg["llm"]["max_context_tokens"] = 200000
        cfg["llm"]["max_tokens"] = 16384
        _window_stub(131072)
        env = fb.AGENT._envelope()
        check("the served window clamps a bigger budget",
              env["window"] == 131072
              and env["budget"] == max(fb.ENVELOPE_MIN_BUDGET, 131072 - env["static"] - env["reply"]),
              env)
        _window_stub(262144)
        roomy = fb.AGENT._envelope()
        check("a roomier server does not raise the configured budget",
              roomy["window"] == 262144 and roomy["budget"] == 200000,
              (roomy["budget"], roomy["window"]))
        # The ceiling is the ceiling: min(llm.max_context_tokens, window - static - reply),
        # never under ENVELOPE_MIN_BUDGET.
        cfg["llm"]["max_context_tokens"] = 12000
        _window_stub(32768)
        capped = fb.AGENT._envelope()
        check("a configured ceiling below the room is the budget",
              capped["budget"] == 12000, capped)
        # The window that makes the floor bind is COMPUTED, not fixed at 8,192: with the
        # prompt at 5,237 that window left under the floor, and with a smaller prompt it
        # leaves room - the check silently stopped exercising the floor (2026-09-27, after
        # the prompt was trimmed). reply shrinks with the window too, so the only window
        # that leaves NOTHING is one no larger than the static prompt itself.
        _now = fb.AGENT._envelope()
        _window_stub(_now["static"])
        floored = fb.AGENT._envelope()
        check("...and the floor wins when the window cannot hold it",
              floored["budget"] == fb.ENVELOPE_MIN_BUDGET, floored)
        cfg["llm"]["max_context_tokens"] = 200000
        _window_stub(0)
        check("a server that does not say keeps the configured budget",
              fb.AGENT._context_budget() == 200000, fb.AGENT._context_budget())
        cfg["llm"]["max_context_tokens"] = "auto"
        _window_stub(131072)
        auto_env = fb.AGENT._envelope()
        check("auto still means what the server says",
              auto_env["budget"] == max(fb.ENVELOPE_MIN_BUDGET, 131072 - auto_env["static"]
                                        - auto_env["reply"]), auto_env)
        # "auto" is the shipped default, so a hand-edit around it must not be able to kill
        # a run: the value arrives from a text file. Casing and spacing are not syntax, 0 is
        # documented as auto, and anything else that is not a number asks the endpoint
        # rather than raising ValueError out of int() (audit, 2026-09-22).
        for weird in ("AUTO", 0, "12k"):
            cfg["llm"]["max_context_tokens"] = weird
            _window_stub(131072)
            got = fb.AGENT._envelope()
            check(f"{weird!r} means auto, not an empty budget",
                  got["budget"] == max(fb.ENVELOPE_MIN_BUDGET, 131072 - got["static"] - got["reply"]), got)
    finally:
        fb.AGENT.__dict__.pop("_window_cache", None)
        _budget_clear()
        fb.CONFIG = saved_cfg


def test_a_length_cut_that_fills_the_window_is_an_overflow_not_a_cap():
    """finish_reason=length with no answer, where prompt + generated reached the
    endpoint's own n_ctx, is the WINDOW closing - not a small max_tokens. Raising
    the cap buys the identical wall (measured: 4,808 tokens, then 4,759 at a 65,536
    ceiling). It has to come back as ContextOverflow, which the agent loop answers
    by shrinking the prompt and re-asking this same turn."""
    saved_cfg = fb.CONFIG
    try:
        cfg = isolated_config()
        cfg["llm"]["max_tokens"] = 16384
        _window_stub(131072)
        usage = {}
        cut = FakeResp(200, body={
            "choices": [{"message": {"content": "",
                                     "reasoning_content": "think " * 200},
                         "finish_reason": "length"}],
            "usage": {"prompt_tokens": 126261, "completion_tokens": 4808}})
        raised = None
        try:
            _r, calls = with_fake_post([cut, ok_resp("should never be reached")],
                                       lambda: fb.AGENT._chat(
                                           [{"role": "user", "content": "hi"}],
                                           usage=usage))
            check("a window-full cut never retries at a bigger cap",
                  len(calls) == 1, calls)
        except fb.ContextOverflow as e:
            raised = str(e)
        check("a window-full cut raises ContextOverflow",
              raised is not None and "context window" in raised, raised)
        check("the window attempt is recorded",
              "window" in [a["outcome"] for a in usage.get("attempts", [])],
              usage)
    finally:
        fb.AGENT.__dict__.pop("_window_cache", None)
        _budget_clear()
        fb.CONFIG = saved_cfg
def test_inherited_open_items_are_not_a_plan():
    """The ledger is durable, so a fresh session inherits the last one's thread.

    Measured on a live install 2026-09-27: a day-old "boot Linux on the iPhone" item plus two
    hours-old entries drove a 26-step run nobody asked for, because the block called open items
    "the to-do list" and never showed how old any of them was. An inherited item needs a yes
    from the operator, and its age is what makes that checkable.
    """
    redirect_files()
    fb.tool_task({"action": "add", "task": "an item the operator asked for a minute ago"}, None)
    fb.tool_task({"action": "add", "task": "an item a session that ended a day ago left open"}, None)
    t = fb.load_tasks()
    stamp = fb.time.strftime("%Y-%m-%d %H:%M", fb.time.localtime(fb.time.time() - 30 * 3600))
    t["items"][-1]["created"] = t["items"][-1]["updated"] = stamp
    fb.save_tasks(t)

    block = fb.render_task_prompt()
    check("an inherited item shows its age", "(1d" in block, block)
    check("and an item past ledger_stale_hours is marked stale", "stale" in block, block)
    check("a fresh item is not marked stale",
          "(0m" in block or "(1m" in block, block)
    check("the block no longer calls open items \"the to-do list\"",
          "to-do list" not in block, block)
    check("it says an inherited item is not a plan for this conversation",
          "NOT a plan for the current conversation" in block, block)
    check("and says to ask before resuming one",
          "ask the operator before resuming" in block, block)


def test_lan_permission_hint():
    """The macOS Local Network prompt is named where it is the likely cause - and only there.

    macOS asks for Local Network access the first time a process connects to a private
    address, and it asks the process that dials: for this bot that is a background service at
    boot, where nobody can answer. An unanswered permission and a dead server both fail to
    connect, so "the endpoint did not answer" used to send the reader to the wrong machine (a
    live install, 2026-09-27, while the model box was serving another process the whole time).
    The hint must stay off for loopback and public hosts, where it would be noise that teaches
    the reader to ignore it.
    """
    saved = fb.sys.platform
    try:
        # The prompt this names is macOS-only, so pin the platform instead of inheriting the
        # runner's: on Linux the whole function returns "" and the three checks below that
        # assert the hint is PRESENT went red (CI, ubuntu, 2026-09-27 - 285 passed, 3 failed).
        fb.sys.platform = "darwin"
        check("a LAN endpoint gets the hint",
              "Local Network" in fb.lan_permission_hint("http://[redacted]:8081/v1"))
        check("a .local name gets the hint",
              "Local Network" in fb.lan_permission_hint("http://box.local:8081/v1"))
        check("credentials and the port are stripped, not read as the host",
              "Local Network" in fb.lan_permission_hint("http://user:pw@[redacted]:8081"))
        check("loopback gets nothing",
              fb.lan_permission_hint("http://127.0.0.1:8080/v1") == "")
        check("a public host gets nothing",
              fb.lan_permission_hint("https://api.example.com/v1") == "")
        check("no url, no hint", fb.lan_permission_hint("") == "")
        fb.sys.platform = "linux"
        check("another OS gets nothing (it is a macOS prompt)",
              fb.lan_permission_hint("http://[redacted]:8081/v1") == "")
    finally:
        fb.sys.platform = saved



def main():
    # The chat-only tests are skipped when this build has no chat layer at all.
    CHATLESS = not hasattr(fb, "MattermostDispatcher")
    CHAT_ONLY = ("test_restart_", "test_an_explicit_cloud_model", "test_cloud_failover",
                 "test_a_cloud_stream_without_usage", "test_a_local_model_stays_local", "test_a_spawned_replacement", "test_streaming_asks_for_usage")
    chatless_skips = []
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for t in tests:
        if only and only not in t.__name__:
            continue
        if CHATLESS and t.__name__.startswith(CHAT_ONLY):
            chatless_skips.append(t.__name__)
            continue
        try:
            t()
        except Exception as e:
            import traceback
            FAILURES.append(f"{t.__name__} raised: {e}")
            traceback.print_exc()
    _tail = ("" if not chatless_skips
             else f", {len(chatless_skips)} skipped (chat build only)")
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed{_tail}")
    for f in FAILURES:
        print("  FAIL:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
