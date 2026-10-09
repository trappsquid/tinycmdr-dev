"""A file log that cannot write a record says so - and keeps the record when a rollover is
blocked.

on a Windows install: a refused secret verb's line
appeared on the console and never in the file, three calls in a row, with nothing saying
why. Two Windows facts make that shape: a rollover RENAMES the log, and an open handle
(the running bot holds this very file) makes the rename fail - and the exception died in
the QueueListener's thread, which reports nothing. The handler answers both: a blocked
rollover falls back to a plain append (losing the rotation is fine, losing the record is
not), and one stderr line names the file and the error. A WRITE failure reports through
the same hook - StreamHandler.emit swallows its own exceptions and calls handleError(),
so an except around super().emit() would be dead code (A-2026-10-08-141).

    python tests/test_file_log.py        [TINYCMDR_SRC=/path/to/old/tinycmdr.py]
"""
import importlib.util
import io
import logging
import logging.handlers
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-filelog"

FAILS = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
    if not ok:
        FAILS.append(what)


def main():
    if STAGE.exists():
        shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    shutil.copy2(BASE / "tests" / "fixture-config.json", STAGE / "config.json")
    spec = importlib.util.spec_from_file_location("tinycmdr_filelog", STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_filelog"] = fb
    try:
        spec.loader.exec_module(fb)
    except Exception as e:                                    # noqa: BLE001
        print("FAIL the staged module loads: %s" % e)
        return 1

    work = Path(tempfile.mkdtemp(prefix="fbflog-"))
    try:
        log = work / "tinycmdr.log"
        handler = fb._LoudRotatingFileHandler(str(log), maxBytes=300, backupCount=1,
                                              encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, "first line", (), None)
        handler.handle(rec)
        check("a normal record lands", "first line" in log.read_text(encoding="utf-8"))

        # A rollover is what Windows refuses while the running bot holds the file.
        saved = logging.handlers.RotatingFileHandler.doRollover

        def blocked(self):
            raise PermissionError("the file is open elsewhere")

        logging.handlers.RotatingFileHandler.doRollover = blocked
        captured = io.StringIO()
        saved_stderr, sys.stderr = sys.stderr, captured
        try:
            big = logging.LogRecord("t", logging.INFO, __file__, 1, "R" * 400, (), None)
            handler.handle(big)
        finally:
            sys.stderr = saved_stderr
            logging.handlers.RotatingFileHandler.doRollover = saved
        text = log.read_text(encoding="utf-8")
        check("a blocked rollover still keeps the record (append fallback)",
              ("R" * 400) in text, text[-80:])
        check("...and says why, once, on stderr",
              "could not roll over" in captured.getvalue()
              and "the file is open elsewhere" in captured.getvalue(),
              captured.getvalue()[:200])
        handler.handle(logging.LogRecord("t", logging.INFO, __file__, 1, "second", (), None))
        check("...and only once (a per-record warning would be noise)",
              captured.getvalue().count("WARNING") == 1, captured.getvalue())

        # A WRITE failure - the handle died under the running bot - must reach the same
        # fallback. StreamHandler.emit catches its own write errors and calls handleError,
        # so an except around super().emit() never ran: pre-fix this record is LOST and
        # stderr gets the stdlib's per-record traceback instead of the one line
        # (A-2026-10-08-141). A fresh handler, because the warn-once flag is per instance.
        log2 = work / "tinycmdr2.log"
        handler2 = fb._LoudRotatingFileHandler(str(log2), maxBytes=300, backupCount=1,
                                               encoding="utf-8")
        handler2.setFormatter(logging.Formatter("%(message)s"))
        handler2.handle(logging.LogRecord("t", logging.INFO, __file__, 1, "alive", (), None))
        handler2.stream.close()                       # the handle dies mid-flight
        captured2 = io.StringIO()
        saved_stderr, sys.stderr = sys.stderr, captured2
        try:
            handler2.handle(logging.LogRecord("t", logging.INFO, __file__, 1,
                                              "after the handle died", (), None))
        finally:
            sys.stderr = saved_stderr
        check("a write failure keeps the record (fresh handle, append fallback)",
              "after the handle died" in log2.read_text(encoding="utf-8"),
              log2.read_text(encoding="utf-8")[-80:])
        check("...and says why once, as the one line - not the stdlib traceback",
              "could not be written" in captured2.getvalue()
              and "--- Logging error ---" not in captured2.getvalue(),
              captured2.getvalue()[:200])

        if FAILS:
            print("\n%d FAILED: %s" % (len(FAILS), "; ".join(FAILS)))
            return 1
        print("\nall file-log checks passed")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
