"""The elision marker carries run STATE, and the overflow path keeps a copy.

A list of dropped calls cannot say what the run has read and changed; the marker now
carries that state as a compact, deduped ledger with R/W/RW markers. Measured here
2026-09-29: a
run compacted mid-rewrite and the next calls re-derived the task from session files.

And `_force_shrink` - the path that drops the MOST context - was the one path that never
wrote the pre-compaction transcript.

    python tests/test_compaction_continuity.py
"""
import json
import os
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


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbcontinuity-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        A = fb.AGENT
        key = "continuity-key"

        # ---- the ledger: deduped by path, markers track read/write
        fb._TOUCHED.pop(key, None)
        f1 = workdir / "sub" / "a.py"
        f2 = workdir / "sub" / "b.conf"
        f1.parent.mkdir(parents=True, exist_ok=True)
        f1.write_text("x\n", encoding="utf-8")
        f2.write_text("y\n", encoding="utf-8")
        fb._touch_files(key, "read_file", {"path": str(f1)})
        fb._touch_files(key, "edit_file", {"path": str(f1)})
        fb._touch_files(key, "write_file", {"path": str(f2)})
        fb._touch_files(key, "shell", {"command": "echo hi"})
        ledger = fb._files_ledger(key)
        check("a.py (RW)" in ledger, "a read then a write of one path is RW", ledger)
        check("b.conf (W)" in ledger, "a written-only path is W", ledger)
        # os.sep: the ledger groups a directory with the separator the platform uses, and
        # asserting a "/" graded the POSIX spelling on a Windows path (the product appended a
        # hard-coded "/" until 2deb132, so this check passed there by accident).
        check(str(f1.parent) + os.sep in ledger and ledger.count("a.py") == 1,
              "paths are grouped by directory and deduped", ledger)

        # ---- the elision marker carries it, bounded and once
        marker = A._elision_note(fb.MARK_COMPACT, [], "", key)
        check("[files touched this run]" in marker and "a.py (RW)" in marker,
              "the marker carries the ledger", marker)
        marker2 = A._elision_note(fb.MARK_COMPACT, [], marker, key)
        check(marker2.count("[files touched this run]") == 1,
              "a second compaction does not stack a second ledger", marker2)

        # ---- the transcript, when it exists, is named in the marker
        sess = fb.SESSIONS_DIR
        sess.mkdir(parents=True, exist_ok=True)
        tp = sess / (key + ".transcript.jsonl")
        tp.write_text('{"role":"user","content":"earlier"}\n', encoding="utf-8")
        marker = A._elision_note(fb.MARK_COMPACT, [], "", key)
        check(str(tp) in marker and "instead of re-deriving" in marker,
              "an existing transcript is advertised in the marker", marker)

        # ---- a cut produces a marker carrying the ledger
        msgs = [{"role": "system", "content": "s"}]
        for i in range(6):
            msgs += [{"role": "user", "content": "ask %d %s" % (i, "x" * 200)},
                     {"role": "assistant", "content": "",
                      "tool_calls": [{"id": "c%d" % i, "type": "function",
                                      "function": {"name": "read_file",
                                                   "arguments": json.dumps(
                                                       {"path": str(f1)})}}]},
                     {"role": "tool", "tool_call_id": "c%d" % i,
                      "content": "line\n" * 50}]
        removed = A._drop_oldest_block(msgs, fb.MARK_COMPACT, key)
        check(isinstance(removed, int) and removed > 0, "the cut reports tokens removed",
              removed)
        check(str(msgs[1].get("content", "")).startswith(fb.MARK_COMPACT)
              and "[files touched this run]" in msgs[1]["content"],
              "the marker left behind carries the ledger", msgs[1]["content"][-160:])

        # ---- _force_shrink writes the transcript before it evicts
        fb._TOUCHED.pop(key, None)
        tp.unlink(missing_ok=True)
        big = [{"role": "system", "content": "s"}]
        for i in range(30):
            big += [{"role": "user", "content": "u%d %s" % (i, "y" * 2000)},
                    {"role": "assistant", "content": "a%d %s" % (i, "z" * 2000)}]
        A._force_shrink(big, key, fb.CONFIG["llm"]["base_url"])
        check(tp.exists() and tp.read_text(encoding="utf-8").count("\n") > 10,
              "_force_shrink writes the pre-compaction transcript", tp)
        check(any(str(m.get("content", "")).startswith(fb.MARK_SHRINK)
                  for m in big), "the shrink left its marker", [m.get("role") for m in big[:6]])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all compaction-continuity checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
