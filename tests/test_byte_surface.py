"""test_byte_surface - one merged suite (test_patch_bytes, test_newlines).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: no member needed a namespace rewrite.
"""
import os
import sys


def _run(name, fn):
    """One member, its own snapshot: env, cwd and sys.path restored afterwards."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_path = list(sys.path)
    print("== member %s: start" % name)
    try:
        rc = fn()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
        sys.path[:] = saved_path
    rc = int(rc or 0)
    print("== member %s: exit %d" % (name, rc))
    return rc


def _suite_test_patch_bytes():
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

    python tests/test_byte_surface.py                     [TINYCMDR_SRC=<patch.py>]
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
    return main()


def _suite_test_newlines():
    """F2: the write path must not translate newlines.

Windows text mode turns every "\\n" into os.linesep on write, so a CRLF file
handed back as "\\r\\n" landed as "\\r\\r\\n": one extra blank line per line, and
the .bak written through the same path was not restorable as-was. The mirror case
is an LF-only payload (a bash script, a .gitattributes) being rewritten CRLF and
then refused by bash with "$'\\r': command not found" - which reads as the model's
fault, not the writer's.

Both halves are pinned here against the file on disk, not against the return
string: what a reader (or bash) sees is the bytes.

    python tests/test_byte_surface.py                      (the app build)

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
            # MEASURED 2026-09-25 on Windows: the old flat warning ("must use
            # CRLF ... it will not run") was FALSE - an LF-only .ps1, .cmd and .bat all RAN,
            # including a cmd with an if/else block and a goto/label - and it cost 2-4 calls per
            # script as the model rewrote bytes that were already runnable. What stays is the
            # narrow, true note about cmd.exe's own parsing.
            check("NOTE" in out and "cmd.exe" in out,
                  "an LF-only .cmd is noted (narrowly) in the result the model reads")
            ps1 = workdir / "probe.ps1"
            out = fb.tool_write_file(
                {"path": str(ps1), "content": "Write-Output 'hi'\n"}, {})
            check("CRLF" not in out and "WARNING" not in out,
                  "an LF-only .ps1 is NOT warned about (it runs; measured on a live install)")
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
    return main()


def main():
    rc = 0
    for name, fn in (("test_patch_bytes", _suite_test_patch_bytes), ("test_newlines", _suite_test_newlines)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
