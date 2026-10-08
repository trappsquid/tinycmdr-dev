"""The spill promise survives a restart, a retry, and a runaway command.

"The FULL text is on disk - nothing was dropped" was process-scoped: the index lived in
memory only (a restart orphaned every pointer), the filename carried a timestamp (the same
output spilled twice wrote two files), and the write had no ceiling (a multi-GB log was
faithfully written byte for byte). Content addressing plus a byte cap close both.

    python tests/test_spill_durability.py
"""
import json
import re
import shutil
import sys
import tempfile
import time
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


def simulate_restart(fb):
    """Drop the in-memory state the way a fresh process starts, but keep the disk.

    The id COUNTER is process state too: a restart begins at 0 and derives the next id
    from the index. Leaving it standing made this simulation unable to see the id-reuse
    bug the whole check group exists for (run 11, A-107).
    """
    fb._SPILLS[:] = []
    fb._SPILLS_LOADED = False
    fb._SPILL_SEQ["n"] = 0


def index_records(fb):
    """The index's lines as dicts - rows AND `removed` records (see _spill_index_save)."""
    out = []
    path = fb._spill_index_path()
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def index_rows(fb):
    return [e for e in index_records(fb) if isinstance(e, dict) and e.get("path")]


def index_removals(fb):
    return [e for e in index_records(fb) if isinstance(e, dict) and e.get("removed")]


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbspill-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        spill = fb.BASE_DIR / "spill"
        marker = "MARKER-9K2"
        body = "A" * 12000 + marker + "B" * 12000

        # ---- the same output spills once: one file, one row, stable id
        out1 = fb.cap_output("shell", body, "command output", session="sp1")
        out2 = fb.cap_output("shell", body, "command output", session="sp1")
        name = re.search(r"spill/([0-9a-f]{16}\.txt)", out1)
        check(bool(name), "the spill file is content-addressed", out1[:120])
        check((spill / name.group(1)).read_text(encoding="utf-8") == body,
              "and holds the whole text", name.group(1))
        rows = fb._spill_rows("sp1")
        check(len(rows) == 1, "the same output does not add a second index row", rows)
        id_before = rows[0]["id"]
        fb.cap_output("shell", body, "command output", session="sp1")
        rows = fb._spill_rows("sp1")
        check(len(rows) == 1 and rows[0]["id"] == id_before,
              "a retry refreshes the row in place (same id)", rows)
        check(re.search(r"spill/([0-9a-f]{16}\.txt)", out2).group(1) == name.group(1),
              "and the pointer is the same file", out2[:120])

        # ---- the index survives a restart
        simulate_restart(fb)
        rows = fb._spill_rows("sp1")
        check(len(rows) == 1 and rows[0]["id"] == id_before,
              "the row is reloaded from disk after a restart", rows)
        resolved = fb._spill_path("spill#%d" % id_before, "sp1")
        check(resolved and Path(resolved).exists(),
              "and spill#<id> still resolves", resolved)

        # ---- /new's removal is persistent too
        fb.AGENT.reset("sp1")
        check(fb._spill_rows("sp1") == [], "reset drops this session's rows")
        check([r for r in index_removals(fb) if r["removed"].startswith("spill/")],
              "the removal itself is written into the index (not just process memory)",
              index_removals(fb))
        simulate_restart(fb)
        check(fb._spill_rows("sp1") == [],
              "and they do not come back on the next restart", fb._spill_rows("sp1"))

        # ---- a STALE memory row in the other process cannot resurrect a removal
        # The merge's `rows` half reads THIS process's memory, so the process that never
        # saw the /new (the concurrent --once run) is the one that used to write the row
        # straight back. Only the DISK record can stop it - an in-memory tombstone cannot,
        # and it is spent by the very save that made it.
        stale = [r for r in index_removals(fb)]
        gone = stale[0]["removed"]
        holder = fb.BASE_DIR / gone
        check(holder.exists(), "the removed row's file stays on disk (nothing was dropped)",
              gone)
        fb._SPILLS.append({"id": 7777, "tool": "shell", "path": gone, "first": "x",
                           "chars": 1, "at": time.time() - 120, "session": "sp1"})
        fb._spill_index_save()
        back = [r.get("path") for r in index_rows(fb)]
        check(gone not in back,
              "a second process holding the stale row cannot put it back", back)
        fb._SPILLS[:] = [r for r in fb._SPILLS if r.get("path") != gone]

        # ---- a FAILED save neither loses the removal nor spends its protection
        # The order used to be: drop the tombstones, then write. A write that raised
        # therefore lost the removal and the thing guarding it in one go, and the next save
        # re-persisted what /new had just deleted - /new quietly stopped holding.
        fb.cap_output("shell", "tombstone-me-" + body, "command output", session="sp9")
        check(bool(fb._spill_rows("sp9")), "a row for /new to remove", fb._spill_rows("sp9"))
        rows_now = index_rows(fb)
        removed_now = {r["removed"] for r in index_removals(fb)}
        real_write = fb.atomic_write_text

        def boom(*_a, **_k):
            raise OSError("disk full (simulated)")

        fb.atomic_write_text = boom
        try:
            fb.AGENT.reset("sp9")                  # its own save is the one that fails
            check(fb._spill_rows("sp9") == [],
                  "the removal still applies in this process")
            check(bool(fb._SPILL_TOMBSTONES),
                  "and a failed save KEEPS the tombstones for the retry",
                  fb._SPILL_TOMBSTONES)
        finally:
            fb.atomic_write_text = real_write
        check(index_rows(fb) == rows_now and
              {r["removed"] for r in index_removals(fb)} == removed_now,
              "the index on disk still holds the previous state, unmangled")
        fb._spill_index_save()                     # the retry
        check(not fb._SPILL_TOMBSTONES, "the retry spends what the failure kept")
        check(all(r.get("path") not in {x["removed"] for x in index_removals(fb)}
                  for r in index_rows(fb)),
              "...and the removal is on disk, so the merge can never put the row back",
              index_removals(fb))

        # ---- a runaway command is capped, honestly
        fb.CONFIG["agent"]["spill_max_bytes"] = 4000
        huge = "".join("line %d %s\n" % (i, "x" * 80) for i in range(4000))
        out3 = fb.cap_output("shell", huge, "command output", session="sp2", limit=500)
        m3 = re.search(r"spill/([0-9a-f]{16}\.txt)", out3)
        blob = (spill / m3.group(1)).read_bytes() if m3 else b""
        check(len(blob) <= 4200, "the spill file respects the byte cap (%d)" % len(blob),
              len(blob))
        check("bytes omitted" in blob.decode("utf-8", "replace"),
              "the file says what it dropped")
        check("capped at 4000 bytes" in out3,
              "the pointer says the file is capped", out3[-300:])
        check(blob.startswith(huge[:200].encode()),
              "the file still starts with the real output")
        fb.CONFIG["agent"]["spill_max_bytes"] = 8 * 1024 * 1024

        # ---- the index MERGES with what another process wrote
        # A second process on this install never sees this one's rows, and its save
        # used to replace the whole index; the save re-reads and unions by `path`.
        fb.cap_output("shell", "first-" + body, "command output", session="sp5")
        idx = fb._spill_index_path()
        foreign = spill / "ffffffffffffffff.txt"
        foreign.write_text("another process's output", encoding="utf-8")
        with idx.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": 4242, "path": "spill/ffffffffffffffff.txt",
                                 "at": time.time() + 60, "session": "elsewhere",
                                 "tool": "shell", "kind": "command output"}) + "\n")
        fb.cap_output("shell", "second-" + body, "command output", session="sp5")
        lines = [json.loads(l) for l in idx.read_text(encoding="utf-8").splitlines()
                 if l.strip()]
        paths = {str(e.get("path")) for e in lines}
        check("a save merges a foreign row instead of clobbering the index",
              "spill/ffffffffffffffff.txt" in paths, sorted(paths))
        check("...and this process's own rows are all there",
              len([p for p in paths if p != "spill/ffffffffffffffff.txt"]) >= 2,
              sorted(paths))
        # ---- ids are never reused, even after every spill file is deleted (A-107)
        # Measured in run 11: with all spill files gone the counter restarted at 1, so a new
        # row took id 1 while the index still carried the old id-1 row, and `spill#1`
        # resolved to a different tool's output than the row the prompt named.
        rows = fb._spill_rows()
        max_id = max([int(e["id"]) for e in rows] or [0])
        for f in spill.glob("*.txt"):
            f.unlink()
        simulate_restart(fb)
        fb._spill_rows()                       # the load advances the sequence
        fb.cap_output("shell", "after-the-wipe-" + body, "command output", session="sp5")
        rows = index_rows(fb)
        ids = [int(e["id"]) for e in rows]
        check(len(ids) == len(set(ids)) and min(ids) > max_id,
              "an id is never reused after its file is deleted (A-107)", (max_id, ids))
        check(Path(fb._spill_path("spill#%d" % max(ids), "sp5") or "").exists(),
              "...and the new row resolves to the new file",
              fb._spill_path("spill#%d" % max(ids), "sp5"))

        # ---- a dead row is not re-persisted by the merge (A-108)
        idx = fb._spill_index_path()
        dead = "spill/00000000000000ff.txt"
        with idx.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": 9999, "path": dead, "at": time.time() + 60,
                                 "session": "sp6", "tool": "shell"}) + "\n")
        fb.cap_output("shell", "saves-again-" + body, "command output", session="sp6")
        paths = {str(e.get("path")) for e in index_rows(fb)}
        check(dead not in paths, "a row whose file is gone is not re-persisted (A-108)",
              sorted(paths))

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all spill-durability checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
