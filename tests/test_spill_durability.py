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
    """Drop the in-memory index the way a fresh process starts, but keep the disk."""
    fb._SPILLS[:] = []
    fb._SPILLS_LOADED = False


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
        simulate_restart(fb)
        check(fb._spill_rows("sp1") == [],
              "and they do not come back on the next restart", fb._spill_rows("sp1"))

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
