"""atomic_write_text / atomic_write_bytes: never truncate, always tell the caller.

Run:  python tests/test_atomic_write.py, or
      python tests/run_all.py --filter atomic_write
Not pytest, deliberately: `check()` records a failure and the suite's exit code is the
verdict (0 pass / 1 fail), so pytest would report this file green whatever the checks said.

The audit's D1: every failure inside `atomic_write_text` - the temp write, the fsync or the
rename - fell back to `p.open("w", ...)` on the DESTINATION, so a failed save truncated the
file the function exists to protect (measured: a 20-item ledger -> 0 bytes, next load died
on JSONDecodeError, no .damaged-* copy). These cases pin the replacement's promises: the
old file survives a failed rename and a failed write, the error reaches the caller, no temp
is left behind, and the temp carries the destination's own mode (audit D4/B3).
"""
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import atexit
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / (os.environ.get("TINYCMDR_TEST_APP")
              or os.environ.get("TINYCMDR_SRC") or "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_atomic_under_test", SRC)
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_atomic_under_test"] = fb
spec.loader.exec_module(fb)

TMP = Path(tempfile.mkdtemp(prefix="fbatomic-"))
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))
FAILURES = []
PASSES = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def _temps(target):
    return sorted(p.name for p in target.parent.glob(target.name + ".tmp-*"))


def test_a_failed_rename_keeps_the_old_file_and_raises():
    target = TMP / "rename-denied.json"
    original = '{"items": [' + ", ".join('"i%d"' % i for i in range(20)) + "]}"
    target.write_text(original, encoding="utf-8")
    real_replace = fb.os.replace

    def deny(src, dst, *a, **kw):
        raise PermissionError(5, "Access is denied")

    fb.os.replace = deny
    raised = None
    try:
        fb.atomic_write_text(target, '{"items": []}')
    except Exception as e:                                       # noqa: BLE001
        raised = e
    finally:
        fb.os.replace = real_replace
    check("a denied rename raises instead of pretending to save",
          raised is not None, raised)
    check("...the old file is byte-identical (20 items survive)",
          target.read_text(encoding="utf-8") == original,
          repr(target.read_text(encoding="utf-8")[:80]))
    check("...and no temp file is left behind", not _temps(target), _temps(target))


def test_a_failed_write_keeps_the_old_file_and_raises():
    target = TMP / "write-denied.json"
    original = '{"items": ["kept"]}'
    target.write_text(original, encoding="utf-8")
    real_fsync = fb.os.fsync
    fb.os.fsync = lambda _fd: (_ for _ in ()).throw(
        OSError(28, "No space left on device"))
    raised = None
    try:
        fb.atomic_write_text(target, '{"items": []}')
    except Exception as e:                                       # noqa: BLE001
        raised = e
    finally:
        fb.os.fsync = real_fsync
    check("a failed flush raises instead of truncating the destination",
          raised is not None, raised)
    check("...and the old file is byte-identical",
          target.read_text(encoding="utf-8") == original,
          repr(target.read_text(encoding="utf-8")[:80]))
    check("...and the temp is cleaned up", not _temps(target), _temps(target))
    # The same failure through the production caller: the ledger keeps the item it had.
    ledger = TMP / "ledger"
    ledger.mkdir()
    fb.TASKS_FILE = ledger / "tasks.json"
    fb.TASKS_DOC = ledger / "tasks.md"
    fb.TASKS_JOURNAL = ledger / "tasks.journal.jsonl"
    fb.save_tasks({"items": [{"id": 1, "desc": "keep me", "status": "open",
                              "note": ""}], "next_id": 2})
    fb.os.fsync = lambda _fd: (_ for _ in ()).throw(OSError(28, "disk gone"))
    try:
        fb.save_tasks({"items": [], "next_id": 1})
        check("save_tasks reports the failure", False, "it returned as if it saved")
    except OSError:
        check("save_tasks reports the failure", True)
    finally:
        fb.os.fsync = real_fsync
    loaded = json.loads(fb.TASKS_FILE.read_text(encoding="utf-8"))
    check("...and the ledger still holds the item it had",
          len(loaded["items"]) == 1 and loaded["items"][0]["desc"] == "keep me",
          loaded)


def test_the_mode_of_the_destination_is_preserved():
    if os.name == "nt":
        print("  (mode case skipped on Windows: no POSIX mode bits)")
    else:
        for mode in (0o600, 0o644, 0o755):
            p = TMP / ("mode-%04o.txt" % mode)
            p.write_text("old", encoding="utf-8")
            os.chmod(p, mode)
            fb.atomic_write_text(p, "new")
            got = stat.S_IMODE(p.stat().st_mode)
            check("an existing %04o file is still %04o after a write" % (mode, mode),
                  got == mode, oct(got))
            check("...and holds the new text",
                  p.read_text(encoding="utf-8") == "new", p.read_text(encoding="utf-8"))
    fresh = TMP / "fresh-state.json"
    fb.atomic_write_text(fresh, "{}")
    if os.name != "nt":
        got = stat.S_IMODE(fresh.stat().st_mode)
        check("a NEW state file is 0600, not the umask's 0644",
              got == 0o600, oct(got))


def test_bytes_go_in_verbatim():
    p = TMP / "bytes.bin"
    data = bytes(range(256))
    fb.atomic_write_bytes(p, data)
    check("atomic_write_bytes writes the bytes exactly",
          p.read_bytes() == data, repr(p.read_bytes()[:20]))
    crlf = TMP / "crlf.txt"
    fb.atomic_write_text(crlf, "a\r\nb\r\n")
    check("a caller's newline convention is not translated",
          crlf.read_bytes() == b"a\r\nb\r\n", repr(crlf.read_bytes()))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"--- {t.__name__}")
        try:
            t()
        except Exception as e:
            FAILURES.append(f"{t.__name__} raised: {type(e).__name__}: {e}")
            print(f"FAIL {t.__name__} raised: {type(e).__name__}: {e}")
    print(f"\n{len(PASSES)} checks passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
