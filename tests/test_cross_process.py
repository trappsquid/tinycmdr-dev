"""Two lanes, one state file: the read-modify-write has to serialize ACROSS processes.

Run:  python tests/test_cross_process.py, or
      python tests/run_all.py --filter cross_process

Every lane is its own process - --cli, --once and each verb all skip the
single-instance lock - and the path locks were a dict in ONE interpreter, so two lanes
that each loaded, mutated and saved lost one of the updates. Measured: three
processes each ran `task add` with a barrier between the read and the save; all three
answered "OK: task #1 added", all three got id 1, and the file held ONE entry. The same
shape on the model overrides (one lane's save dropped the other's choice), and
the single-instance lock handed a second bot the same token after `rm`.

These checks use REAL subprocesses on purpose: an in-process test cannot see any of it. The
instance-lock checks are POSIX-complete; on Windows the lock is still a file, but a file another
process holds open cannot be DELETED there (sharing violation), so the deletion foot-gun this
fixes is POSIX-only and that branch is *Unverified on Windows*.
"""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("TINYCMDR_TEST_APP")
           or os.environ.get("TINYCMDR_SRC") or (BASE / "tinycmdr.py"))
if not SRC.is_absolute():
    SRC = BASE / SRC
FAILS = []
PASSES = []

STAGE = Path(tempfile.mkdtemp(prefix="fbtest-crossproc-"))


def check(cond, what, detail=""):
    print(("ok   " if cond else "FAIL ") + what + ("" if cond else " :: %r" % (detail,)))
    if not cond:
        FAILS.append(what)
    else:
        PASSES.append(what)


def stage():
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    (STAGE / "config.json").write_text(json.dumps({"llm": {}}), encoding="utf-8")


def reset_state():
    for name in ("notes.md", "state.json"):
        p = STAGE / name
        if p.exists():
            p.unlink()
    shutil.rmtree(STAGE / "memory", ignore_errors=True)


def load():
    """The staged module, in THIS process (for the single-process halves)."""
    spec = importlib.util.spec_from_file_location("fb_crossproc", STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["fb_crossproc"] = fb
    spec.loader.exec_module(fb)
    return fb


# One worker. `hold` widens the window INSIDE the memory write (concept + index under one
# lock), which is the interleaving that would lose an index entry - and inside the fix it
# is eaten by the memory lock, so the answer is the same.
WORKER_NOTE = r'''
import importlib.util, sys, time
from pathlib import Path
stage = Path(sys.argv[1]); tag = sys.argv[2]; hold = float(sys.argv[3])
spec = importlib.util.spec_from_file_location("mp_worker", stage / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["mp_worker"] = fb
spec.loader.exec_module(fb)
real = fb.memory_index_render
def slow(*a, **k):
    out = real(*a, **k)
    time.sleep(hold)
    return out
fb.memory_index_render = slow
print(fb.tool_memory({"action": "add", "title": "worker-" + tag,
                      "body": "worker-" + tag}, {}))
'''

# A lane that sets ONE conversation's model choice and saves it, optionally waiting for
# another lane to save first. This is the shape: the waiter loaded the file BEFORE the
# other lane wrote, so its snapshot is stale by the time it saves.
WORKER_OVERRIDE = r'''
import importlib.util, sys, time
from pathlib import Path
stage = Path(sys.argv[1]); key = sys.argv[2]; model = sys.argv[3]
wait_for = sys.argv[4]; signal = sys.argv[5]
spec = importlib.util.spec_from_file_location("mp_worker2", stage / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["mp_worker2"] = fb
spec.loader.exec_module(fb)
fb.AGENT.model_overrides[key] = model
if wait_for != "-":
    deadline = time.time() + 60
    while not Path(wait_for).exists():
        if time.time() > deadline:
            sys.exit("waited too long for " + wait_for)
        time.sleep(0.02)
fb._save_overrides()
Path(signal).write_text("done")
'''


def spawn(code, args, timeout=180):
    p = subprocess.Popen([sys.executable, "-c", code] + [str(a) for a in args],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(timeout=timeout)
    return p.returncode, out.strip(), err.strip()


def test_three_processes_writing_one_memory():
    """The memory bundle's shape: three lanes add at once, each asleep inside
    the write window, and neither a concept nor the shared index loses an entry."""
    reset_state()
    hold = 0.4
    procs = [subprocess.Popen(
        [sys.executable, "-c", WORKER_NOTE, str(STAGE), str(i), str(hold)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(3)]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=180)
        outs.append((p.returncode, out.strip(), err.strip()))
    check(all(rc == 0 for rc, _o, _e in outs), "all three lanes exited 0", outs)
    check(sum(1 for _rc, o, _e in outs if o.startswith("OK:")) == 3,
          "and all three answered OK", outs)
    concepts = sorted(p.stem for p in (STAGE / "memory").glob("*.md")
                      if p.stem not in ("index", "log"))
    check(concepts == ["worker-0", "worker-1", "worker-2"],
          "all three concepts are on disk", concepts)
    index = (STAGE / "memory" / "index.md").read_text(encoding="utf-8")
    check(all(("worker-%d" % i) in index for i in range(3)),
          "and the shared index lists all three", index)


# A lane that bumps one counter in state.json with a deliberate pause between the read
# and the write: without an inter-process lock all three read 0 and write 1.
WORKER_STATE = r'''
import importlib.util, sys, time
from pathlib import Path
stage = Path(sys.argv[1]); hold = float(sys.argv[2])
spec = importlib.util.spec_from_file_location("mp_state", stage / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["mp_state"] = fb
spec.loader.exec_module(fb)
def bump(st):
    time.sleep(hold)              # the read-modify-write window, inside the lock
    st["count"] = int(st.get("count") or 0) + 1
fb._state(bump)
print("bumped")
'''


def test_state_bumps_from_three_lanes_are_not_lost():
    """The lock itself, not just the merge: three lanes increment one counter."""
    reset_state()
    hold = 0.4
    procs = [subprocess.Popen(
        [sys.executable, "-c", WORKER_STATE, str(STAGE), str(hold)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
    outs = [p.communicate(timeout=180)[0].strip() for p in procs]
    check(outs == ["bumped"] * 3, "all three lanes bumped state.json", outs)
    st = json.loads((STAGE / "state.json").read_text(encoding="utf-8"))
    check(st.get("count") == 3,
          "state.json counted all three (the read-modify-write is serialized)", st)


def test_a_lane_keeps_the_other_lanes_overrides():
    """The stale-snapshot save used to drop the other lane's key."""
    reset_state()
    bot_done = STAGE / "bot.done"
    cli_done = STAGE / "cli.done"
    for p in (bot_done, cli_done):
        if p.exists():
            p.unlink()
    # The cli lane starts FIRST and loads state.json before the bot has written anything,
    # then sets its own key and waits. The bot lane then sets its key and saves. When the
    # cli lane finally saves, its snapshot is stale - and the bot's key must survive.
    cli = subprocess.Popen(
        [sys.executable, "-c", WORKER_OVERRIDE, str(STAGE), "cli:tab-2",
         "model-from-cli", str(bot_done), str(cli_done)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(0.6)                      # let it load state.json and park
    rc_bot, out_bot, err_bot = spawn(
        WORKER_OVERRIDE, [STAGE, "mattermost:chan-1", "model-from-bot", "-",
                          str(bot_done)])
    out_cli, err_cli = cli.communicate(timeout=180)
    check(rc_bot == 0, "the bot lane saved its choice", (rc_bot, out_bot, err_bot))
    check(cli.returncode == 0, "the cli lane saved too", (cli.returncode, out_cli, err_cli))
    st = json.loads((STAGE / "state.json").read_text(encoding="utf-8"))
    got = st.get("model_overrides") or {}
    check(got.get("mattermost:chan-1") == "model-from-bot",
          "the bot lane's /model choice survived the cli lane's stale save", got)
    check(got.get("cli:tab-2") == "model-from-cli",
          "and the cli lane's own choice was written", got)


def test_the_global_switch_still_clears_the_map():
    """`_save_overrides(replace=True)` is the deliberate 'everyone inherits this' call."""
    fb = load()
    fb.AGENT.model_overrides["cli:tab-9"] = "mine"
    fb._save_overrides()
    fb.AGENT.model_overrides["cli:tab-9"] = "mine-again"
    fb._save_overrides()
    st = json.loads((STAGE / "state.json").read_text(encoding="utf-8"))
    check((st.get("model_overrides") or {}).get("cli:tab-9") == "mine-again",
          "a normal save writes this process's own key", st)
    fb.AGENT.model_overrides.clear()
    fb._save_overrides(replace=True)
    st = json.loads((STAGE / "state.json").read_text(encoding="utf-8"))
    check(st.get("model_overrides") == {},
          "a global switch still replaces the whole map", st)


# A second lane that just takes the instance lock and holds it until told to stop.
WORKER_LOCK = r'''
import importlib.util, sys, time
from pathlib import Path
stage = Path(sys.argv[1]); mode = sys.argv[2]
held, stop = Path(sys.argv[3]), Path(sys.argv[4])
spec = importlib.util.spec_from_file_location("mp_lock", stage / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["mp_lock"] = fb
spec.loader.exec_module(fb)
ok = fb.acquire_single_instance_lock()
print("ACQUIRED" if ok else "REFUSED")
if ok and mode == "hold":
    held.write_text("held")
    deadline = time.time() + 60
    while not stop.exists() and time.time() < deadline:
        time.sleep(0.05)
    fb._release_lock()
'''


def _try_lock():
    rc, out, err = spawn(WORKER_LOCK, [STAGE, "try", STAGE / "x.held", STAGE / "x.stop"])
    return rc, out


def test_the_instance_lock_is_not_a_deletable_file():
    """Flock lives on the inode, so `rm tinycmdr.lock` handed a second bot the
    same token. POSIX locks the install FOLDER - a directory with contents cannot be
    unlinked - and the abort text no longer tells anyone to delete anything.

    Real processes: the holder is a child, so this is the shape that actually happens
    (two bots, one folder), not a same-process re-entry.
    """
    fb = load()
    reset_state()
    held, stop = STAGE / "held.txt", STAGE / "stop.txt"
    for p in (held, stop):
        p.unlink(missing_ok=True)
    holder = subprocess.Popen(
        [sys.executable, "-c", WORKER_LOCK, str(STAGE), "hold", str(held), str(stop)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 30
        while not held.exists() and time.time() < deadline:
            time.sleep(0.05)
        check(held.exists(), "the holder process took the instance lock", holder.poll())
        check(fb._verb_running() is True,
              "another process sees the instance as running")
        _rc, out = _try_lock()
        check(out == "REFUSED", "a second process is refused", out)
        if os.name == "nt":
            # No delete-the-lock check here, because the defect cannot exist on Windows:
            # DeleteFile on a file another process holds open fails with a sharing
            # violation (the CRT's open() does not share delete - the same reason a
            # rename is denied while a handle holds the target), so a deleter must stop
            # the bot first. The installer's own comments record that: an in-place
            # install fails while the bot "holds tinycmdr.log and tinycmdr.lock".
            # *Unverified on Windows*: this branch is reasoning, not a run.
            print("  (Windows: the lock is a file, but it cannot be deleted while held - "
                  "not exercised on this host)")
        else:
            check(not (STAGE / "tinycmdr.lock").exists(),
                  "on POSIX the lock is the FOLDER itself: there is no file to delete")
            (STAGE / "tinycmdr.lock").unlink(missing_ok=True)   # the old foot-gun
            _rc, out = _try_lock()
            check(out == "REFUSED",
                  "deleting tinycmdr.lock does not free the instance lock any more", out)
    finally:
        stop.write_text("stop")
        try:
            holder.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            holder.kill()
    check(fb._verb_running() is False, "with the holder gone the folder is free")
    # A read-only probe must not CREATE tinycmdr.lock.
    if os.name == "nt":
        (STAGE / "tinycmdr.lock").unlink(missing_ok=True)
        # On Windows the lock is a FILE and the probe opens it r+b, so a probe with no lock
        # present raises the documented OSError - every production caller catches it
        # (_verb_running turns it into "unknown" rather than a false "free"). This check is
        # about CREATION, so it catches it too: unguarded, it was a FileNotFoundError that
        # killed the suite on windows-latest (measured 2026-10-08).
        try:
            fb._instance_lock_free()
        except OSError:
            pass
        check(not (STAGE / "tinycmdr.lock").exists(),
              "the lock probe does not create tinycmdr.lock")
    else:
        print(" (POSIX: the probe locks the folder, so there is no file to create)")
    # The inter-process lock namespace is keyed on the INSTALL, not the
    # caller's uid (root's cron and the User= service used to take different files), and
    # the shared dir/files carry the modes that let a second uid use them at all.
    if os.name != "nt":
        old_name = "tinycmdr-locks-%s" % getattr(os, "getuid", lambda: "w")()
        check(fb.LOCK_DIR.name != old_name,
              "the lock namespace is not keyed on the caller's uid", fb.LOCK_DIR.name)
        probe_key = str(fb.BASE_DIR / "audit19.state")
        fb._ip_take(probe_key)
        try:
            lock_file = fb._ip_lock_file(probe_key)
            check(stat.S_IMODE(fb.LOCK_DIR.stat().st_mode) == 0o1777,
                  "the shared lock dir is 1777 (sticky: create/enter, delete own)",
                  oct(stat.S_IMODE(fb.LOCK_DIR.stat().st_mode)))
            check(stat.S_IMODE(lock_file.stat().st_mode) == 0o666,
                  "and a lock file is 0666, so the second uid can open it",
                  oct(stat.S_IMODE(lock_file.stat().st_mode)))
        finally:
            fb._ip_drop(probe_key)
    _rc, out = _try_lock()
    check(out == "ACQUIRED", "and the next instance takes the lock", out)
    note = fb.instance_busy_note()
    check("status" in note and "delete" not in note.lower(),
          "the busy note points at `tinycmdr status`, never at deleting the lock", note)


def test_one_path_has_one_lock_whatever_the_case():
    """A path has ONE lock however it is spelled, and the key folds case exactly where the
    filesystem folds it.

    normcase folds case on Windows only; macOS's default APFS is case-insensitive too
    (measured 2026-10-07 on this box: `touch NOTES.md` then `test -e notes.md` answers
    yes, so one file answers to both names). Two spellings of one file therefore took two
    locks, two parallel tool calls both reported success, and one write was silently lost
    - the lost update the per-path lock was added to prevent. The volume is ASKED (no probe
    file is written), so a case-sensitive volume keeps two distinct keys, correctly.
    """
    fb = load()
    lower = str(STAGE / "notes.md")
    upper = str(STAGE / "NOTES.md")
    folding = fb._volume_folds_case(str(STAGE))
    check((fb._lock_key(lower) == fb._lock_key(upper)) == bool(folding),
          "the lock key folds case exactly when the volume does",
          (folding, fb._lock_key(lower)[-20:], fb._lock_key(upper)[-20:]))
    check(fb._lock_key(lower) == fb._lock_key(str(STAGE / "." / "notes.md")),
          "one path spelled two ways keys the same lock",
          (fb._lock_key(lower)[-20:], fb._lock_key(str(STAGE / "." / "notes.md"))[-20:]))
    check(fb._lock_key("") == "" and fb._lock_key(None) == "",
          "a path-less caller keeps the empty key")
    if folding:
        check(fb._path_lock(lower) is fb._path_lock(upper),
              "and two spellings of one file share ONE lock object")
    else:
        print("  (this volume is case-SENSITIVE: the fold is graded by the check above)")


def main():
    stage()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"--- {t.__name__}")
        try:
            t()
        except Exception as e:
            FAILS.append(f"{t.__name__} raised: {type(e).__name__}: {e}")
            print(f"FAIL {t.__name__} raised: {type(e).__name__}: {e}")
    shutil.rmtree(STAGE, ignore_errors=True)
    print()
    print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
    if FAILS:
        print("%d failed: %s" % (len(FAILS), FAILS))
        return 1
    print("all cross-process checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
