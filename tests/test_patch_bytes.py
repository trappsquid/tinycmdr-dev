"""A patch rewrites the line it was given - and nothing else, byte for byte.

Night audit run 11 (A-2026-10-05-131): `tools/patch.py` read with
`bytes.decode("utf-8", "replace")` and wrote UTF-8 back, so a Latin-1/CP1252 file lost
every non-ASCII byte to U+FFFD - silently, under a diff that showed only the intended
line (`caf\\xe9` became `caf\\xef\\xbf\\xbd`). A byte stream is not broken text: the file
is decoded losslessly (UTF-8 strict, else Latin-1, which maps every byte 1:1), and the
result is re-encoded in the SAME encoding, strictly - a character that encoding cannot
carry is an error naming the file, never replacement characters.

    python tests/test_patch_bytes.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PATCH = BASE / "tools" / "patch.py"

FAILS = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % detail))
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
