"""The ledger has an append-only history, and a shrink is never silent.

tasks.json is REPLACED atomically at every save, so a bad save, or a ledger rebuilt from
salvage, used to leave no trace: the campaign's ledger was rebuilt from scratch 43 times
and nobody could tell (audit, 2026-09-21).

Run:  python tests/test_ledger_journal.py
"""
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-journal"
FAILS = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        FAILS.append(what)


def stage():
    if STAGE.exists():
        shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    (STAGE / "config.json").write_text(
        json.dumps({"agent": {"tasks_file": str(STAGE / "tasks.json")}}), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("tinycmdr_journal", STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_journal"] = fb
    spec.loader.exec_module(fb)
    return fb


def lines(fb):
    p = Path(fb.TASKS_JOURNAL)
    if not p.exists():
        return []
    return [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    fb = stage()
    check(Path(fb.TASKS_JOURNAL).name.endswith(".jsonl"),
          "the journal is its own file beside the ledger, and it did not exist before")
    check(lines(fb) == [], "nothing is journalled until something is saved")

    t = {"items": [{"id": 1, "status": "doing", "desc": "a"}], "next_id": 2}
    fb.save_tasks(t)
    first = lines(fb)
    check(len(first) == 1, "one save, one journal line")
    row = json.loads(first[0])
    check(row["rev"] == 1 and row["items"] == 1 and row["next_id"] == 2,
          "the line carries the certain numbers: revision, item count, next_id")

    fb.save_tasks(t)
    two = lines(fb)
    check(len(two) == 2 and json.loads(two[1])["rev"] == 2,
          "the revision advances with each save")
    check(two[0] == first[0], "and an earlier line is never rewritten")

    # a ledger that lost items is reported, and the high-water mark never goes backwards
    fb._LEDGER_SEEN.update({"rev": 5, "items": 5})
    fb.save_tasks({"items": [], "next_id": 1})
    check(len(lines(fb)) == 3,
          "even an empty ledger is journalled (that is the shape of a fresh one)")
    check(fb._LEDGER_SEEN["rev"] == 5,
          "the high-water mark holds at what that box had already seen")
    fb.ledger_check({"revision": 2, "items": [{"id": 1}]})
    check(fb._LEDGER_SEEN["rev"] == 5,
          "a ledger that went BACKWARDS cannot pull the high-water mark down with it")

    # loading a damaged ledger still salvages, and the journal shows the drop
    (STAGE / "tasks.json").write_text('{"items": [{"id": 1, "status": "doing"}],'
                                      ' "next_id": 2}{"junk": 1}', encoding="utf-8")
    got = fb.load_tasks()
    check(len(got["items"]) == 1, "a damaged ledger is still salvaged, not thrown away")
    check(len(lines(fb)) == 3, "and salvaging does not rewrite history")

    # ---- D9: a journal line describes a save that LANDED, and the rename is durable ---
    # The line used to be appended BEFORE the write it describes, so a failed save left a
    # revision in the history that never happened (and the next save reused the number).
    # And the durability story was incomplete: fsync on the temp makes its CONTENTS
    # durable, but the rename is a change to the parent directory.
    (STAGE / "tasks.json").write_text('{"items": [], "next_id": 1}', encoding="utf-8")
    before = len(lines(fb))
    real_fsync = fb.os.fsync
    dir_fsynced = []

    def spy(fd):
        try:
            dir_fsynced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        except OSError:
            pass
        return real_fsync(fd)

    fb.os.fsync = spy
    raised = None
    try:
        fb.save_tasks({"items": [{"id": 1, "status": "open", "desc": "lands"}],
                       "next_id": 2})
        landed = len(lines(fb))
        fb.os.fsync = lambda _fd: (_ for _ in ()).throw(
            OSError(28, "No space left on device"))
        try:
            fb.save_tasks({"items": [], "next_id": 1})
        except OSError as e:
            raised = e
        fb.os.fsync = spy
    finally:
        fb.os.fsync = real_fsync
    check(landed == before + 1, "a save that lands adds exactly one journal line")
    check(len(lines(fb)) == landed,
          f"a save that FAILED writes no journal line at all ({lines(fb)[-3:]})")
    check(raised is not None, f"and the failure reaches the caller ({raised})")
    on_disk = json.loads((STAGE / "tasks.json").read_text(encoding="utf-8"))
    check(json.loads(lines(fb)[-1])["rev"] == on_disk["revision"],
          f"the last line's revision is the revision actually on disk ({on_disk})")
    check(any(dir_fsynced),
          f"the rename is made durable with a directory fsync ({dir_fsynced})")

    print()
    if FAILS:
        print("%d failed: %s" % (len(FAILS), FAILS))
        sys.exit(1)
    print("all ledger-journal checks passed")


main()
