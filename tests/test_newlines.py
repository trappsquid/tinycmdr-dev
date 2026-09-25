"""F2: the write path must not translate newlines (measured 2026-09-22).

Windows text mode turns every "\\n" into os.linesep on write, so a CRLF file
handed back as "\\r\\n" landed as "\\r\\r\\n": one extra blank line per line, and
the .bak written through the same path was not restorable as-was. The mirror case
is an LF-only payload (a bash script, a .gitattributes) being rewritten CRLF and
then refused by bash with "$'\\r': command not found" - which reads as the model's
fault, not the writer's.

Both halves are pinned here against the file on disk, not against the return
string: what a reader (or bash) sees is the bytes.

    python tests/test_newlines.py                      (the app build)

Falsify it before trusting it: point TINYCMDR_TEST_APP at a pre-fix build
(tinycmdr.py.bak-f2newline-*) and the CRLF checks must FAIL.
"""
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


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbnewline-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)

        # ---- write_file: what the model wrote is what lands -------------------
        sh = workdir / "probe.sh"
        out = fb.tool_write_file(
            {"path": str(sh), "content": "#!/bin/bash\necho hi\n"}, {})
        check("OK: wrote" in out, "write_file still reports success")
        check(sh.read_bytes() == b"#!/bin/bash\necho hi\n",
              "an LF-only script lands byte-for-byte LF (bash can run it)")
        check(b"\r" not in sh.read_bytes(), "no CR was added by the platform")

        # ---- write_file: a Windows script that cannot run is called out -------
        cmd = workdir / "probe.cmd"
        out = fb.tool_write_file(
            {"path": str(cmd), "content": "@echo off\necho hi\n"}, {})
        check("WARNING" in out and "CRLF" in out,
              "an LF-only .cmd is warned about in the result the model reads")
        cmd_ok = workdir / "probe_ok.cmd"
        out_ok = fb.tool_write_file(
            {"path": str(cmd_ok), "content": "@echo off\r\necho hi\r\n"}, {})
        check("WARNING" not in out_ok, "a CRLF .cmd is not warned about")
        check(cmd_ok.read_bytes() == b"@echo off\r\necho hi\r\n",
              "CRLF content is not doubled on the way in")

        # ---- write_file: append, and the backup ------------------------------
        ap = workdir / "appended.txt"
        ap.write_bytes(b"a\r\n")
        fb.tool_write_file({"path": str(ap), "content": "b\r\n", "append": True}, {})
        check(ap.read_bytes() == b"a\r\nb\r\n", "append writes the bytes as given")

        old = workdir / "overwrite.txt"
        old.write_bytes(b"first\r\nsecond\r\n")
        fb.tool_write_file({"path": str(old), "content": "replaced\n"}, {})
        check(old.read_bytes() == b"replaced\n", "an overwrite writes what was given")
        check(old.with_suffix(".txt.bak").read_bytes() == b"first\r\nsecond\r\n",
              "the overwrite backup is byte-identical to the file it replaced")

        # ---- edit_file: CRLF in, CRLF out, untouched lines untouched ---------
        target = workdir / "crlf.txt"
        before = b"alpha\r\nbeta\r\ngamma\r\ndelta\r\n"
        target.write_bytes(before)
        out = fb.tool_edit_file({"path": str(target), "old_string": "beta",
                                 "new_string": "BETA"}, {})
        check("OK: replaced" in out, "edit_file still reports success")
        after = target.read_bytes()
        check(after == b"alpha\r\nBETA\r\ngamma\r\ndelta\r\n",
              "a CRLF file keeps CRLF and every untouched line is byte-identical")
        check(b"\r\r" not in after, "no doubled CR anywhere in the file")
        check(before.replace(b"beta", b"BETA") == after,
              "the edit is exactly the one string asked for")
        check(target.with_suffix(".txt.bak").read_bytes() == before,
              "the edit backup is restorable as-was (byte-identical)")

        # ---- edit_file: an LF file stays LF -----------------------------------
        lf = workdir / "lf.txt"
        lf.write_bytes(b"alpha\nbeta\n")
        fb.tool_edit_file({"path": str(lf), "old_string": "beta",
                           "new_string": "BETA"}, {})
        check(lf.read_bytes() == b"alpha\nBETA\n",
              "an LF file stays LF (a Linux host's file is not flipped to CRLF)")

        # ---- atomic_write_text: bytes as given, both ways ---------------------
        state = workdir / "state.json"
        fb.atomic_write_text(state, "{\n  \"a\": 1\n}\n")
        check(state.read_bytes() == b"{\n  \"a\": 1\n}\n",
              "atomic_write_text writes LF as LF (state files stay LF on Windows)")
        fb.atomic_write_text(state, "x\r\ny\r\n")
        check(state.read_bytes() == b"x\r\ny\r\n", "and CRLF as CRLF")
        check(not list(workdir.glob("state.json.tmp-*")),
              "the temp file is still replaced, not left behind")

        # ---- the failure this exists for, in one assertion -------------------
        rl = workdir / "roundtrip.txt"
        rl.write_bytes(b"one\r\ntwo\r\n")
        fb.tool_edit_file({"path": str(rl), "old_string": "one",
                           "new_string": "ONE"}, {})
        check(rl.read_text(encoding="utf-8").count("\n") == 2,
              "reading it back gives 2 lines, not 4 (the doubling is gone)")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all newline checks passed")


if __name__ == "__main__":
    main()
