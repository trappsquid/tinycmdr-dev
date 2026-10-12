"""The event log's files are OWNER-ONLY, and they stay that way through a roll.

A session's `sessions/<key>.events.jsonl` names every tool call and its outcome, and
the one predecessor `<key>.events.1.jsonl` holds the same text once the file rolls.
Both were created with `open("a")`, which is blind to the umask - 0644 on a default
box, so any account on a shared host could read a session's history - and an existing
file kept whatever mode an older build had left it. The fix opens the fd with
O_CREAT and 0600 (fchmod'ing past an older, looser mode) and tightens the roll target
on the rename path; `sessions/` itself is set 0700 at creation and when found wide
(A-2026-10-10-15).

POSIX only: Windows has no mode bits here and its ACLs are deliberately untouched, so
this suite exits 77 (SKIP, never a green 0) on Windows, per the gate's convention.

    python tests/test_fix_perms.py
    TINYCMDR_SRC=../pre-fix/tinycmdr.py python tests/test_fix_perms.py   # must go red
"""
import importlib.util
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
# TINYCMDR_SRC points this at a reverted copy, so a fix can be watched going red
# (tests/run_all.py clears it for a normal run).
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

if os.name != "posix":
    print("SKIP the event-file permission checks: Windows has no POSIX mode bits, and "
          "this fix deliberately leaves its ACLs alone")
    sys.exit(77)

# A fresh home AND a fresh BASE_DIR: the staged build's BASE_DIR is its own file's
# parent, so the temp dir below becomes the sessions/ parent and nothing lands in the
# operator's checkout or home.
STAGE = Path(tempfile.mkdtemp(prefix="fbfixperms-"))
HOME = STAGE / "home"
HOME.mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(HOME)
os.environ["USERPROFILE"] = str(HOME)
# The umask makes the umask-blind creation this fix is about reproducible: a default
# shell umask is already 022, but the check must not depend on the caller's.
_OLD_UMASK = os.umask(0o022)

shutil.copy2(SRC, STAGE / "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_fix_perms", STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_fix_perms"] = fb
spec.loader.exec_module(fb)

# The event writer is shadow-only; a host opts in.
fb.CONFIG["agent"]["event_log"] = True

PASSES = []
FAILS = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print("ok   %s" % name)
    else:
        FAILS.append(name)
        print("FAIL %s: %s" % (name, detail))


def mode_of(path):
    """The POSIX permission bits of a path, or None if it is not there."""
    try:
        return stat.S_IMODE(os.stat(str(path)).st_mode)
    except OSError:
        return None


def main():
    # ---- the acceptance: a fresh log and its first roll both land 0600 ----------
    # One byte is the roll threshold here: the first append is already over it, so the
    # second call takes the roll path. The size behaviour itself is graded elsewhere.
    fb._EVENT_MAX_BYTES = 1
    key = "perms-roll"
    fb.event("tool.call", session_key=key, name="shell")
    fb.event("tool.call", session_key=key, name="shell")

    live = fb._event_path(key)
    rolled = fb._event_rolled_path(key)
    check("the second event rolled the first into .1", rolled.exists(), str(rolled))
    check("a fresh .events.jsonl is 0600",
          mode_of(live) == 0o600, oct(mode_of(live) or 0))
    check("the rolled predecessor is 0600 too",
          mode_of(rolled) == 0o600, oct(mode_of(rolled) or 0))
    check("sessions/ is 0700",
          mode_of(fb.SESSIONS_DIR) == 0o700, oct(mode_of(fb.SESSIONS_DIR) or 0))

    # ---- an older build's file is TIGHTENED, not adopted ------------------------
    # Nothing recreates this file: the writer must bring an existing looser mode down
    # on its next append, or an upgraded host keeps reading its history out loud.
    fb._EVENT_MAX_BYTES = 8_000_000
    legacy = fb._event_path("perms-legacy")
    legacy.write_text("", encoding="utf-8")
    os.chmod(str(legacy), 0o644)
    fb.event("tool.call", session_key="perms-legacy", name="shell")
    check("an existing 0644 log is tightened on append",
          mode_of(legacy) == 0o600, oct(mode_of(legacy) or 0))

    # ---- and a roll that inherited a looser mode is tightened on the way ---------
    # The roll is a rename: the new .1 keeps the inode (and mode) of the file it
    # replaced, so the tightening has to sit on the roll path, not only at creation.
    fb._EVENT_MAX_BYTES = 1
    old = fb._event_path("perms-legacyroll")
    old.write_text("x" * 100 + "\n", encoding="utf-8")
    os.chmod(str(old), 0o644)
    fb.event("tool.call", session_key="perms-legacyroll", name="shell")
    rolled2 = fb._event_rolled_path("perms-legacyroll")
    check("the oversized legacy log rolled", rolled2.exists(), str(rolled2))
    check("a roll of a 0644 log is tightened to 0600",
          mode_of(rolled2) == 0o600,
          oct(mode_of(rolled2) or 0))

    # ---- the transcript sidecar is the same shape, same rule --------------------
    # `_save_transcript` appended through open("a") too: sessions/<key>.transcript.jsonl
    # held the dropped conversation at the umask default (found while fixing the event
    # log; one helper now covers both).
    fb._EVENT_MAX_BYTES = 8_000_000
    agent = fb.Agent.__new__(fb.Agent)
    agent._TRANSCRIPT_MAX_BYTES = 1
    tkey = "perms-transcript"
    dropped = [{"role": "user", "content": "first"},
               {"role": "assistant", "content": "second"}]
    agent._save_transcript(tkey, dropped, "the perms check")
    tpath = fb.SESSIONS_DIR / (fb._session_stem(tkey) + ".transcript.jsonl")
    check("the transcript line landed", tpath.exists(), str(tpath))
    check("a fresh .transcript.jsonl is 0600",
          mode_of(tpath) == 0o600, oct(mode_of(tpath) or 0))
    agent._save_transcript(tkey, dropped, "the perms check")
    troll = fb.SESSIONS_DIR / (fb._session_stem(tkey) + ".transcript.1.jsonl")
    check("the oversized transcript rolled", troll.exists(), str(troll))
    check("the transcript roll is 0600 too",
          mode_of(troll) == 0o600, oct(mode_of(troll) or 0))

    print()
    print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
    if FAILS:
        print("%d failed: %s" % (len(FAILS), FAILS))
        return 1
    print("every event file and its roll are owner-only")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        os.umask(_OLD_UMASK)
        shutil.rmtree(STAGE, ignore_errors=True)
    sys.exit(rc)
