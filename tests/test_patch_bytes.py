"""A patch rewrites the line it was given - and nothing else, byte for byte.

`tools/patch.py` read with
`bytes.decode("utf-8", "replace")` and wrote UTF-8 back, so a Latin-1/CP1252 file lost
every non-ASCII byte to U+FFFD - silently, under a diff that showed only the intended
line (`caf\\xe9` became `caf\\xef\\xbf\\xbd`). A byte stream is not broken text: the file
is decoded losslessly (UTF-8 strict, else Latin-1, which maps every byte 1:1), and the
result is re-encoded in the SAME encoding, strictly - a character that encoding cannot
carry is an error naming the file, never replacement characters.

second half:

  A-132  a file that mixes line endings came back uniformly the dominant convention
         (`line1\\r\\nline2\\n` -> all CRLF), so an edit to one line rewrote every other
         line's ending. Each existing line keeps its OWN ending; inserted lines take the
         dominant one.
  A-133  a whitespace-only old_string matched under the whitespace-insensitive strategy
         and edited a blank line; it is refused with "nothing to anchor on".
  A-134  an anchor of only blank lines matched (and collapsed) any adjacent blank run;
         refused by the same rule.

    python tests/test_patch_bytes.py                     [TINYCMDR_SRC=<patch.py>]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
# The tool under test. TINYCMDR_SRC points the suite at a snapshot (the pre-fix
# tools/patch.py) so every check below can be watched going red.
PATCH = BASE / os.environ.get("TINYCMDR_SRC", "tools/patch.py")

FAILS = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
    if not ok:
        FAILS.append(what)


def run_patch(path, old_string, new_string):
    proc = subprocess.run([sys.executable, str(PATCH),
                           "path=" + str(path),
                           "old_string=" + old_string,
                           "new_string=" + new_string],
                          capture_output=True, text=True, timeout=60)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main():
    work = Path(tempfile.mkdtemp(prefix="fbpatchbytes-"))
    try:
        latin = work / "latin.txt"
        latin.write_bytes(b"caf\xe9 tail line\nsecond\n")
        rc, out = run_patch(latin, "tail line", "TAIL")
        data = latin.read_bytes()
        check("a latin-1 file keeps its own bytes",
              b"caf\xe9 TAIL\nsecond\n" == data, data)
        check("...and no replacement character is written",
              b"\xef\xbf\xbd" not in data, data)
        check("...the tool reported the edit", "OK" in out, out[:120])

        utf8 = work / "utf8.txt"
        utf8.write_bytes("caf\u00e9 tail line\nsecond\n".encode("utf-8"))
        rc, out = run_patch(utf8, "tail line", "TAIL")
        check("a utf-8 file still round-trips",
              utf8.read_bytes() == "caf\u00e9 TAIL\nsecond\n".encode("utf-8"),
              utf8.read_bytes())

        plain = work / "plain.txt"
        plain.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        rc, out = run_patch(plain, "beta", "BETA")
        check("an ascii file still works",
              plain.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n", out[:120])

        # A character the file's encoding cannot carry is refused by name, not mangled.
        rc, out = run_patch(latin, "TAIL", "TAIL\u2192")
        check("a character latin-1 cannot carry is refused",
              "cannot carry" in out and "latin-1" in out, out[:160])
        check("...and the file is untouched",
              latin.read_bytes() == b"caf\xe9 TAIL\nsecond\n", latin.read_bytes())

        # ---- A-132: a file that mixes endings keeps each line's OWN ending. The file is
        #      mostly CRLF with one LF line; the edit replaces a CRLF line and inserts a
        #      line, so the LF line is what proves the endings were not flattened.
        mixed = work / "mixed.txt"
        mixed.write_bytes(b"a\r\nb\r\nc\nd\r\n")
        rc, out = run_patch(mixed, "b", "B")
        check("a mixed-ending file keeps each line's own ending",
              mixed.read_bytes() == b"a\r\nB\r\nc\nd\r\n", mixed.read_bytes())

        mixed2 = work / "mixed2.txt"
        mixed2.write_bytes(b"a\r\nb\r\nc\nd\r\n")
        rc, out = run_patch(mixed2, "b", "B1\nB2")
        check("...while an inserted line takes the file's dominant ending",
              mixed2.read_bytes() == b"a\r\nB1\r\nB2\r\nc\nd\r\n", mixed2.read_bytes())

        # ---- A-133/A-134: an anchor with no non-blank content has nothing to anchor on
        blank = work / "blank.txt"
        # No trailing newline on purpose: with one, `split("\n")` ends in an empty line
        # and the pre-fix tool refused for the wrong reason (two candidates) instead of
        # matching the one blank line this anchor used to edit.
        blank.write_bytes(b"one\n \t\ntwo")
        rc, out = run_patch(blank, "  ", "X")
        check("a whitespace-only anchor is refused",
              "ERROR" in out and "anchor" in out, out[:160])
        check("...and the file is untouched", blank.read_bytes() == b"one\n \t\ntwo",
              blank.read_bytes())

        blanks = work / "blanks.txt"
        blanks.write_bytes(b"one\n\n\ttwo\n")
        rc, out = run_patch(blanks, "\n\n", "X")
        check("a blank-line-only anchor is refused",
              "ERROR" in out and "anchor" in out, out[:160])
        check("...and the file with the blank run is untouched",
              blanks.read_bytes() == b"one\n\n\ttwo\n", blanks.read_bytes())

        if FAILS:
            print("\n%d FAILED: %s" % (len(FAILS), "; ".join(FAILS)))
            return 1
        print("\nall patch-bytes checks passed")
        return 0
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
