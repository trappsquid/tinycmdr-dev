"""atomic_write_text: a failed write must never destroy the file that was there.

Pins BUGREPORT §D1, §D4 and §D11. The measured incidents: a denied rename fell back
to a plain non-atomic write, so a locked target (Windows holds the handle) turned a
20-item ledger into a zero-byte or half-written file - the loss the function exists
to prevent, committed by its own handler; and the temp file was created with the
process umask, so writing .env briefly published a secret as world-readable.

The four contracts here: a failed rename and a failed write both leave the previous
file byte-identical and TELL THE CALLER; the destination's mode is preserved (0600
stays 0600, a new file is private); no temp survives a failure.

    python tests/test_atomic_write.py
"""
import atexit
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

# --- hermetic staging (same idiom as the other suites) ----------------------
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-atomic"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
             STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_atomic",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_atomic"] = fb
spec.loader.exec_module(fb)

TMP = Path(tempfile.mkdtemp(prefix="tc-atomic-"))
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))
FAILURES = []

OLD = "old contents that must survive\n" * 4
NEW = "brand new contents\n" * 4


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}: {detail}")


def deny_rename(*a, **k):
    raise PermissionError(5, "Access is denied")


def disk_full(*a, **k):
    raise OSError(28, "No space left on device")


def test_denied_rename_keeps_old_file():
    p = TMP / "denied.json"
    p.write_text(OLD, encoding="utf-8")
    os.chmod(p, 0o600)
    real = fb.os.replace
    fb.os.replace = deny_rename
    raised = None
    try:
        fb.atomic_write_text(p, NEW)
    except Exception as e:
        raised = e
    finally:
        fb.os.replace = real
    check("denied rename: the caller is told", raised is not None, "no exception")
    check("denied rename: old file byte-identical",
          p.read_text(encoding="utf-8") == OLD, p.read_text()[:40])
    check("denied rename: no temp left behind",
          not list(TMP.glob("denied.json.tmp-*")), list(TMP.glob("denied.json.tmp-*")))


def test_failed_write_keeps_old_file():
    p = TMP / "full.json"
    p.write_text(OLD, encoding="utf-8")
    real = fb.os.fsync
    fb.os.fsync = disk_full
    raised = None
    try:
        fb.atomic_write_text(p, NEW)
    except Exception as e:
        raised = e
    finally:
        fb.os.fsync = real
    check("failed write (ENOSPC): the caller is told", raised is not None, "no exception")
    check("failed write (ENOSPC): old file byte-identical",
          p.read_text(encoding="utf-8") == OLD, p.read_text()[:40])
    check("failed write (ENOSPC): no temp left behind",
          not list(TMP.glob("full.json.tmp-*")), list(TMP.glob("full.json.tmp-*")))


def test_success_writes_and_cleans_up():
    p = TMP / "ok.json"
    fb.atomic_write_text(p, NEW)
    check("success: new contents landed", p.read_text(encoding="utf-8") == NEW)
    check("success: no temp left behind",
          not list(TMP.glob("ok.json.tmp-*")), list(TMP.glob("ok.json.tmp-*")))


def test_modes_preserved_and_never_widened():
    secret = TMP / "secret.env"
    secret.write_text(OLD, encoding="utf-8")
    os.chmod(secret, 0o600)
    fb.atomic_write_text(secret, NEW)
    check("0600 stays 0600", (secret.stat().st_mode & 0o7777) == 0o600,
          oct(secret.stat().st_mode & 0o7777))

    shared = TMP / "shared.json"
    shared.write_text(OLD, encoding="utf-8")
    os.chmod(shared, 0o644)
    fb.atomic_write_text(shared, NEW)
    check("0644 stays 0644", (shared.stat().st_mode & 0o7777) == 0o644,
          oct(shared.stat().st_mode & 0o7777))

    fresh = TMP / "fresh.json"
    fb.atomic_write_text(fresh, NEW)
    check("a new state file is private (0600)",
          (fresh.stat().st_mode & 0o7777) == 0o600,
          oct(fresh.stat().st_mode & 0o7777))

    forced = TMP / "config.json"
    fb.atomic_write_text(forced, NEW, mode=0o644)
    check("an explicit mode wins (installer's 0644 config.json)",
          (forced.stat().st_mode & 0o7777) == 0o644,
          oct(forced.stat().st_mode & 0o7777))


def test_locked_target_does_not_deadlock():
    """The lock the writer takes is reentrant and thread-scoped: two writers of one
    path serialize, and a same-thread nested write (the ledger tool holds it while
    save_tasks writes) must not freeze the run."""
    p = TMP / "nested.json"
    with fb._path_lock(str(p)):
        fb.atomic_write_text(p, NEW)      # would deadlock on a plain Lock
    check("a nested write under the path lock returns", p.exists())


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed")
        sys.exit(1)
    print("all atomic-write checks passed")


if __name__ == "__main__":
    main()
