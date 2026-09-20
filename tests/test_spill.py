"""Over-cap tool results are SPILLED, not shredded (2026-09-18).

Proven on the Windows test box by the harness's own analysis: a 30,045-char tool result lost ~20,100
middle characters to truncate_middle, and re-issuing the call with `raw=true` lost the same
middle (raw bypasses digestion, not the cap). These checks pin the replacement: the whole text
lands on disk, the prompt gets both ends plus a pointer that works, and a spill that cannot be
written degrades to the old truncation instead of breaking the run.
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


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbtest-spill-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        cap = int(fb.CONFIG["agent"]["tool_output_max_chars"])
        marker = "MARKER-7QZ42"
        body = "A" * 12000 + marker + "B" * 12000 + "END-OF-OUTPUT"

        # ---- a big result keeps its middle, on disk -------------------------------------
        out = fb.cap_output("shell", body, "command output")
        check(len(out) < len(body), f"the prompt copy is smaller ({len(out)} < {len(body)})")
        check(marker not in out, "the middle is not in the prompt - that is what the cap is")
        m = re.search(r"spill/([A-Za-z0-9_.-]+\.txt)", out)
        check(bool(m), "the result names a spill file")
        spilled = ((fb.BASE_DIR / "spill" / m.group(1)).read_text(encoding="utf-8")
                   if m else "")
        check(marker in spilled, "the text on disk still carries the middle marker")
        check(spilled == body, "and it is byte-for-byte what the tool produced")
        check("Nothing was dropped" in out and "Do NOT" in out,
              "the pointer says how to read it and not to re-run the command")

        # ---- a small result is untouched -----------------------------------------------
        check(fb.cap_output("shell", "one line", "command output") == "one line",
              "a result under the cap passes through unchanged")

        # ---- switching it off restores the old behaviour -------------------------------
        fb.CONFIG["agent"]["spill_output"] = False
        try:
            out2 = fb.cap_output("shell", body, "command output")
        finally:
            fb.CONFIG["agent"]["spill_output"] = True
        check("truncated" in out2 and marker not in out2,
              "spill off falls back to truncation")

        # ---- a spill that cannot be written must never break the run -------------------
        real_dir = fb._spill_dir
        fb._spill_dir = lambda: (_ for _ in ()).throw(OSError("disk full"))
        try:
            out3 = fb.cap_output("shell", body, "command output")
        finally:
            fb._spill_dir = real_dir
        check("truncated" in out3, "a failed spill degrades to truncation, no exception")

        # ---- rotation keeps the folder bounded ----------------------------------------
        fb.CONFIG["agent"]["spill_keep"] = 3
        for i in range(6):
            fb.cap_output("shell", f"{i}" + "x" * (cap + 200), "command output")
            time.sleep(0.02)
        left = list((fb.BASE_DIR / "spill").glob("*.txt"))
        check(len(left) <= 3, f"rotation keeps the folder bounded ({len(left)} files)")
        fb.CONFIG["agent"]["spill_keep"] = 50

        # ---- a REAL call site spills, on the exact route the analysis lost data on -----
        big = workdir / "big_output.txt"
        lines = [f"line {i} token-{i * 7919}" for i in range(4000)]
        big.write_text("\n".join(lines), encoding="utf-8")

        # Plain read: the DIGEST is what shrinks a file read (it matches a "log file" shape and
        # keeps the newest 40 lines). The digest has its own escape hatch - raw=true - and this
        # is the route that did NOT have one, because raw bypasses digestion but not the cap.
        plain = fb.tool_read_file({"path": str(big), "limit": 4000},
                                  {"session_key": "spill-session"})
        check(len(plain) < cap, "a plain read of a big file is digested under the cap")
        check("raw=true" in plain, "  and the digest states its own escape hatch")

        got = fb.tool_read_file({"path": str(big), "limit": 4000, "raw": True},
                                {"session_key": "spill-session"})
        check("spill/" in got,
              "read_file raw=true over the cap hands back a spill pointer (the route that lost data)")
        m2 = re.search(r"spill/([A-Za-z0-9_.-]+\.txt)", got)
        spilled2 = ((fb.BASE_DIR / "spill" / m2.group(1)).read_text(encoding="utf-8")
                    if m2 else "")
        check(bool(m2) and spilled2.count("token-") == 4000,
              f"and that file holds all 4000 lines, readable by the model "
              f"({spilled2.count('token-')} found)")

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) FAILED")
            return 1
        print("all spill checks passed")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
