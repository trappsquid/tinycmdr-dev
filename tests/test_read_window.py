"""read_file's window: which lines you get, and what the header claims.

Three defects sat behind one operator report (2026-09-27, all reproduced before the fix):

  * `offset=-5, limit=3` answered `lines -5—-2 of 22096` - negative line references that
    say nothing to a reader - because a negative slice reads from the END while the header
    prints the raw index;
  * `from_end=bool(want_tail or offset_req)` meant ANY offset read the LAST bytes of the
    file and then sliced them by a line number meant for the whole file: `offset=10,
    limit=2` of a 28.6 MiB file returned lines 432238-432239 under a header claiming 10-12.
    A silently wrong answer, not a cosmetic one;
  * `_read_capped` appended its own warning INTO the text that is then split into lines,
    so `tail=2` of a file past the cap returned the warning's two lines instead of the
    file's last two.

The cap is lowered in the truncation checks on purpose: every path here is size-relative,
and a real 9 MiB fixture would cost the gate a second per run for the same coverage.

    python tests/test_read_window.py
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "tinycmdr.py"
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-window"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(BASE / "tests" / "fixture-config.json", STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_window", STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_window"] = fb
spec.loader.exec_module(fb)

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("ok   %s" % name)
    else:
        FAILS.append(name)
        print("FAIL %s: %s" % (name, detail))


def header_and_body(out):
    lines = str(out).splitlines()
    return lines[0], lines[1:]


SMALL = STAGE / "small.txt"
SMALL.write_text("".join("line %d\n" % i for i in range(1, 201)), encoding="utf-8")

# ------------------------------------------------------------------ the plain window
head, body = header_and_body(fb.tool_read_file({"path": str(SMALL), "offset": 10, "limit": 3}, {}))
check("offset is a 0-based start line", "(lines 10–13 of 200)" in head, head)
check("...and the body starts at that line", body[0] == "line 11", body[:2])

head, body = header_and_body(fb.tool_read_file({"path": str(SMALL), "offset": 0, "limit": 2}, {}))
check("offset 0 is the first line", "(lines 0–2 of 200)" in head and body[0] == "line 1", (head, body[:2]))

head, body = header_and_body(fb.tool_read_file({"path": str(SMALL), "tail": 3}, {}))
check("tail is its own door and says so", "(last 3 of 200 lines)" in head, head)
check("...with the file's real last lines", body[0] == "line 198" and "line 200" in body[-1], body)

out = str(fb.tool_read_file({"path": str(SMALL), "offset": -5, "limit": 3}, {}))
check("a negative offset is refused, not answered with negative line refs",
      out.startswith("ERROR: offset is a START line"), out[:90])
check("...and the refusal names the door for the last lines", "tail=N" in out, out[:160])

# ------------------------------------------------------ the window past the read cap
REAL_CAP = fb._MAX_CAPTURE_BYTES
fb._MAX_CAPTURE_BYTES = 4096
try:
    BIG = STAGE / "big.log"
    with open(BIG, "w", encoding="utf-8") as fh:
        for i in range(1, 4001):
            fh.write("line %05d %s\n" % (i, "pad" * 10))
    check("the fixture is over the lowered cap", BIG.stat().st_size > 4096,
          BIG.stat().st_size)

    head, body = header_and_body(fb.tool_read_file({"path": str(BIG), "offset": 10, "limit": 2}, {}))
    check("an offset reads from the START, not the tail chunk",
          body[0].startswith("line 00011 "), body[:2])
    check("...and the header says the file is bigger than the window",
          "shown, the file is bigger" in head, head)
    out = str(fb.tool_read_file({"path": str(BIG), "offset": 10, "limit": 2}, {}))
    check("...and the cap warning rides the result", "HARNESS: this produced" in out, out[-120:])

    head, body = header_and_body(fb.tool_read_file({"path": str(BIG), "tail": 2}, {}))
    check("tail past the cap returns the FILE's last lines, not the warning's",
          body[0].startswith("line 03999") and body[1].startswith("line 04000")
          and not any("HARNESS" in b for b in body[:2]), body[:3])
    check("...and still carries the cap warning", "HARNESS: this produced" in
          str(fb.tool_read_file({"path": str(BIG), "tail": 2}, {})), "no warning")

    out = str(fb.tool_read_file({"path": str(BIG), "offset": 3900, "limit": 2}, {}))
    check("an offset past the window says so instead of answering empty",
          out.startswith("ERROR: line 3900 is past") and "tail=N" in out, out[:150])

    text, cut, note = fb._read_capped(BIG)
    check("the cap warning is returned SEPARATELY from the text",
          cut is True and "HARNESS" not in text and "HARNESS" in note,
          (cut, "HARNESS" in text, len(note)))
finally:
    fb._MAX_CAPTURE_BYTES = REAL_CAP

# ------------------------------------------------- an unreadable file names the cause
# A file held open with a Windows deny-all share mode made read_file answer with a Python
# internal exception - "not enough values to unpack (expected 3, got 2)" - because
# _read_capped returned a 2-tuple on OSError while every caller unpacks 3 (report H-1,
# 2026-10-02). chmod 000 is the POSIX way to make open() raise where a Windows share lock
# does; both are an OSError whose args carry no filename.
if hasattr(os, "geteuid") and os.geteuid() != 0:
    LOCKED = STAGE / "locked.txt"
    LOCKED.write_text("secret\n", encoding="utf-8")
    os.chmod(LOCKED, 0)
    try:
        out = str(fb.tool_read_file({"path": str(LOCKED)}, {}))
        check("an unreadable file names the cause, not a Python unpack error",
              out.startswith("ERROR reading") and "unpack" not in out
              and ("Permission denied" in out or "Errno" in out), out[:160])
        raised = False
        try:
            fb._read_capped(LOCKED, strict=True)
        except OSError:
            raised = True
        check("_read_capped(strict=True) re-raises the real OSError", raised)
    finally:
        os.chmod(LOCKED, 0o644)

# ------------------------------------------------- the header never claims a wrong total
# When a read is cut, `len(lines)` is the window covered - the header printed it as if it
# were the file's line count, which is how the read cap read as the file's length
# (report H-4, 2026-10-02).
fb._MAX_CAPTURE_BYTES = 2048
try:
    CUT = STAGE / "cut.txt"
    CUT.write_text("".join("line %d\n" % i for i in range(1, 1001)), encoding="utf-8")
    check("the fixture is over the lowered cap", CUT.stat().st_size > 2048,
          CUT.stat().st_size)
    head, _body = header_and_body(
        fb.tool_read_file({"path": str(CUT), "offset": 0, "limit": 2}, {}))
    check("a cut read says the count is the window it covered",
          "this read covered" in head, head)
    head, _body = header_and_body(fb.tool_read_file({"path": str(CUT), "tail": 2}, {}))
    check("...and a tail of a cut read says the same",
          "this read covered" in head and "the file is bigger" in head, head)
    head, _body = header_and_body(fb.tool_read_file({"path": str(SMALL), "tail": 3}, {}))
    check("an uncut read keeps its plain 'last N of M lines'",
          "(last 3 of 200 lines)" in head, head)
finally:
    fb._MAX_CAPTURE_BYTES = REAL_CAP

# ------------------------------------------------- a Windows path past MAX_PATH (H-2)
# Creating a 339-character path on Windows throws WinError 206 with LongPathsEnabled=0,
# while the \\?\ extended form works (report H-2, measured on the fleet's Windows box, 2026-10-02). The
# transform is a pure function, so it is graded here - off Windows - by forcing the flag.
_real_win = fb.IS_WINDOWS
try:
    fb.IS_WINDOWS = True
    _short_win = r"C:\Users\a\file.txt"
    check("a short absolute Windows path is left alone",
          fb._win_long_path(_short_win) == _short_win)
    _long = r"C:\Users\a" + (r"\dddddddddd" * 26) + r"\file.txt"
    _got = fb._win_long_path(_long)
    check(r"a long drive path gets the \\?\ prefix", _got.startswith("\\\\?\\C:\\"), _got[:26])
    check("...and keeps the end of the path intact", _got.endswith(r"\file.txt"), _got[-14:])
    _unc = "\\\\server\\share" + (r"\dddddddddd" * 26) + r"\f.txt"
    _ugot = fb._win_long_path(_unc)
    check("a long UNC path uses the UNC extended form",
          _ugot.startswith("\\\\?\\UNC\\server"), _ugot[:22])
    _ext = r"\\?\C:\a\file.txt"
    check("an already-extended path is not double-prefixed",
          fb._win_long_path(_ext) == _ext)
    check("a relative path is left alone",
          fb._win_long_path("sub/dir/file.txt") == "sub/dir/file.txt")
finally:
    fb.IS_WINDOWS = _real_win
check("off Windows every path is untouched", fb._win_long_path("/tmp/a/b") == "/tmp/a/b")

print()
if FAILS:
    print("%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("read_file's window: offsets from the start, tail from the end, headers that tell the truth")
