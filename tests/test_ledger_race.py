"""Concurrent state writes: one batch, one file, no lost entry.

Run:  python tests/test_ledger_race.py            (all tests)
      python tests/test_ledger_race.py <substring>  (one test)

The measured failure this suite pins (operator drive on the Windows bed, 2026-09-23):
ONE assistant turn issued `task done(#7)` plus three `task add` calls, the
harness runs a turn's tool calls in a ThreadPoolExecutor of up to 4 workers, and
every one of those calls is a read-modify-write pass over one JSON file.

    log    atomic write of tasks.json failed (WinError 32 ... 'tasks.json.tmp-65588')
           atomic write of tasks.md   failed (WinError 32 ...)
    journal revision 15 written TWICE (two writers, one snapshot each)
    ledger  #7 still [open] (the `done` was lost) and the first `add` never existed

Two defects, and they need two fixes:
  * atomic_write_text named its temp file `<name>.tmp-<pid>`, so two writers in
    one process shared it: the second rename raised, and BOTH writes fell back
    to the plain non-atomic write the function exists to avoid. Fixed by a
    per-writer temp name plus the per-path lock.
  * even with perfect atomic saves, two load-mutate-save passes lose one update.
    Fixed by serializing the ledger tool on the ledger file (`serialized_on`).

Falsify it: `git show HEAD:<tinycmdr.py> > /tmp/probe-pre.py` on the parent of the
fix commit and run this suite with TINYCMDR_SRC pointed at that file - the
parallel tests must FAIL there, not crash.

It imports the live build as a module (no Mattermost connection, no scheduled
jobs, no lock) and redirects every file it writes at a temp dir.
"""
import atexit
import copy
import importlib.util
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# This suite imports the bot build.
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

# --- hermetic staging (same idiom as the other suites) ----------------------
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-fbrace"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
FIXTURE_CFG = STAGE / "config.json"
FIXTURE_SRC = Path(__file__).resolve().parent / "fixture-config.json"
if not FIXTURE_SRC.exists():
    sys.exit(f"missing test fixture: {FIXTURE_SRC} (it ships in tests/)")
shutil.copy2(FIXTURE_SRC, FIXTURE_CFG)

spec = importlib.util.spec_from_file_location("tinycmdr_under_test",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_under_test"] = fb
spec.loader.exec_module(fb)

TMP = Path(tempfile.mkdtemp(prefix="fbrace-"))
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))
PRISTINE = copy.deepcopy(fb.CONFIG)
FAILURES = []
PASSES = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def redirect():
    """Every state file this suite touches lives in TMP, not in the tree."""
    fb.TASKS_FILE = TMP / "tasks.json"
    fb.TASKS_DOC = TMP / "tasks.md"
    fb.TASKS_JOURNAL = TMP / "tasks.journal.jsonl"
    fb._LEDGER_SEEN.update({"rev": 0, "items": 0})
    for f in (fb.TASKS_FILE, fb.TASKS_DOC, fb.TASKS_JOURNAL):
        if Path(f).exists():
            Path(f).unlink()
    fb.CONFIG = copy.deepcopy(PRISTINE)


class Capture(logging.Handler):
    """The warnings the bot would have logged, so the suite can grade them.

    The lock is named `records_lock` and NOT `lock`: `logging.Handler` already HAS a
    `lock`, taken by `handle()` around every `emit()`, so assigning to that name with a
    plain (non-reentrant) Lock deadlocks the first record - the same self-deadlock the
    notes guard documents (2026-09-18), reproduced here by a test trying to be tidy.
    """

    def __init__(self):
        super().__init__()
        self.lines = []
        self.records_lock = threading.Lock()

    def emit(self, record):
        with self.records_lock:
            self.lines.append(record.getMessage())

    def atomic_failures(self):
        with self.records_lock:
            return [l for l in self.lines if "atomic write of" in l]


def fire(fn, *calls):
    """Run the calls the way a batch does: concurrently, up to 4 at a time."""
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(fn, args) for args in calls]
        return [f.result() for f in futures]


def add(args):
    return fb.tool_task(args, {})


# --- the ledger -------------------------------------------------------------

def test_parallel_adds_all_land():
    """Six adds in one batch: six items, six saves, nothing lost."""
    redirect()
    cap = Capture()
    fb.log.addHandler(cap)
    try:
        asks = [{"action": "add", "task": f"parallel add {i}"} for i in range(6)]
        outs = fire(add, *asks)
        t = json.loads(Path(fb.TASKS_FILE).read_text(encoding="utf-8"))
        descs = [i.get("desc") for i in t["items"]]
        check("six parallel adds are six items", len(t["items"]) == 6, descs)
        check("every add answered OK", all(str(o).startswith("OK") for o in outs), outs)
        check("all six descriptions are present",
              all(f"parallel add {i}" in descs for i in range(6)), descs)
        check("the README mirror names every item",
              (TMP / "tasks.md").read_text(encoding="utf-8").count("- [") == 6,
              (TMP / "tasks.md").read_text(encoding="utf-8"))
    finally:
        fb.log.removeHandler(cap)


def test_a_done_and_three_adds_do_not_clobber_each_other():
    """The measured shape: one `done` + three `add`s in a single batch."""
    redirect()
    cap = Capture()
    fb.log.addHandler(cap)
    try:
        add({"action": "add", "task": "the task to finish"})
        tid = json.loads(Path(fb.TASKS_FILE).read_text(encoding="utf-8"))["items"][0]["id"]
        calls = ([{"action": "done", "id": tid, "note": "checked by re-running it"}]
                 + [{"action": "add", "task": f"batch add {i}"} for i in range(3)])
        fire(add, *calls)
        t = json.loads(Path(fb.TASKS_FILE).read_text(encoding="utf-8"))
        by_id = {i["id"]: i for i in t["items"]}
        check("the done survived the batch",
              by_id[tid]["status"] == "done", by_id[tid])
        check("all three adds survived the same batch",
              sum(1 for i in t["items"] if str(i.get("desc", "")).startswith("batch add")) == 3,
              [i.get("desc") for i in t["items"]])
    finally:
        fb.log.removeHandler(cap)


def test_the_journal_gives_every_save_its_own_revision():
    """Two writers on one snapshot used to write the same revision twice."""
    redirect()
    asks = [{"action": "add", "task": f"journal add {i}"} for i in range(5)]
    fire(add, *asks)
    rows = [json.loads(l) for l in (TMP / "tasks.journal.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    revs = [r.get("rev") for r in rows]
    check("one journal line per save", len(rows) == 5, revs)
    check("revisions strictly increase", revs == sorted(set(revs)), revs)
    check("the ledger's own revision matches the last line",
          json.loads(Path(fb.TASKS_FILE).read_text(encoding="utf-8"))["revision"] == revs[-1],
          revs)


def test_an_idless_done_lands_on_the_one_open_task():
    """Measured: seven id-less `done` calls, seven dead ends, one lost turn."""
    redirect()
    add({"action": "add", "task": "the only open task"})
    out = fb.tool_task({"action": "done", "note": "re-read the change and re-ran the check"}, {})
    t = json.loads(Path(fb.TASKS_FILE).read_text(encoding="utf-8"))
    check("an id-less done with ONE open task is accepted",
          str(out).startswith("OK"), out)
    check("...and it finished that task",
          t["items"][0]["status"] == "done", t["items"][0])


def test_an_idless_done_with_several_open_names_the_ids():
    """When it is ambiguous the refusal has to carry the ids and the shape."""
    redirect()
    add({"action": "add", "task": "first open task"})
    add({"action": "add", "task": "second open task"})
    out = str(fb.tool_task({"action": "done", "note": "guessing which one"}, {}))
    check("the refusal says the call is an ERROR", out.startswith("ERROR"), out)
    check("...names the shape (id=<n>)", "id=<n>" in out, out)
    check("...and names both open ids", "#1" in out and "#2" in out, out)


# --- atomic_write_text itself ----------------------------------------------

def test_a_torn_write_is_never_visible():
    """Eight writers, one path: the reader gets one payload, never a mixture."""
    redirect()
    cap = Capture()
    fb.log.addHandler(cap)
    target = TMP / "state-under-race.json"
    payloads = [json.dumps({"writer": i, "pad": "x" * 200000}) for i in range(8)]
    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda p: fb.atomic_write_text(target, p), payloads))
        got = target.read_text(encoding="utf-8")
        check("the file holds exactly one writer's payload", got in payloads,
              f"{len(got)} chars, first 40: {got[:40]!r}")
        check("it is valid JSON (no splice)", json.loads(got)["writer"] in range(8),
              got[:60])
        leftovers = [p.name for p in TMP.glob("state-under-race.json.tmp-*")]
        check("no temp file is left behind", not leftovers, leftovers)
    finally:
        fb.log.removeHandler(cap)


def test_each_writer_gets_its_own_temp_name():
    """The defect itself: one process-wide temp name for concurrent writers."""
    redirect()
    seen = []
    lock = threading.Lock()
    real_replace = os.replace

    def spy(src, dst, *a, **kw):
        with lock:
            seen.append(Path(src).name)
        return real_replace(src, dst, *a, **kw)

    target = TMP / "temp-name-race.txt"
    fb.os.replace = spy
    try:
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(lambda i: fb.atomic_write_text(target, f"payload {i}"),
                        range(6)))
    finally:
        fb.os.replace = real_replace
    check("six writers, six distinct temp names", len(set(seen)) == 6, seen)
    check("...and none of them is the old pid-only name",
          all(n.startswith("temp-name-race.txt.tmp-") for n in seen), seen)


# --- notes.md: the same race on the file `remember` writes ------------------

def redirect_notes():
    """notes.md state into TMP. NOTE the two locks below: `tool_remember` wears
    `serialized_on(NOTES_FILE)`, which captured the ORIGINAL path object at def
    time (same as tool_task + TASKS_FILE), while `curate_notes` looks its path up
    at call time. Tests must hold the lock the code under test actually takes."""
    fb.NOTES_FILE = TMP / "notes.md"
    fb.NOTES_ARCHIVE_FILE = TMP / "notes-archive.md"
    fb.NOTES_AUTHORED_FILE = TMP / "notes-authored.json"
    fb._NOTES_AUTHORED["loaded"] = False
    fb._NOTES_AUTHORED["hashes"] = []
    for f in (fb.NOTES_FILE, fb.NOTES_ARCHIVE_FILE, fb.NOTES_AUTHORED_FILE):
        if Path(f).exists():
            Path(f).unlink()


def remember(args):
    return fb.tool_remember(args, {})


def test_parallel_remembers_all_land():
    """WO8's measured shape: `remember` calls inside one 4-worker batch.

    The drive's four facts landed in write order 1,3,4,2 - concurrent writers on
    notes.md - and the append was only safe by luck: the curator that runs after
    every remember is a whole-file rewrite with no lock over its read."""
    redirect()
    redirect_notes()
    cap = Capture()
    fb.log.addHandler(cap)
    try:
        asks = [{"note": "w8-fact-%d: fact %d" % (i, i)} for i in range(8)]
        outs = fire(remember, *asks)
        text = Path(fb.NOTES_FILE).read_text(encoding="utf-8")
        check("eight parallel remembers are eight notes",
              text.count("w8-fact-") == 8, text)
        check("every remember answered OK",
              all(str(o).startswith("OK") for o in outs), outs)
        # ...and the SUPERSEDE rule must not swallow them: these eight notes share their only
        # 3+ char word ("fact"), so a bare containment share reads 1.00 and collapses them
        # into one entry. That is exactly what happened on the 1.0.14 build (measured
        # 2026-09-25: this suite 35 passed, 1 failed). A short note is not a near-duplicate
        # of another short note, so superseding needs a minimum shared vocabulary.
        redirect_notes()
        o1 = remember({"note": "probe-a: alpha"})
        o2 = remember({"note": "probe-b: bravo"})
        txt = Path(fb.NOTES_FILE).read_text(encoding="utf-8")
        check("two SHORT notes that share their only word are both kept",
              "probe-a" in txt and "probe-b" in txt,
              f"{o1[:60]!r} / {o2[:60]!r} / {txt[:120]!r}")
        check("and the second one did not supersede the first",
              "superseded" not in o2, o2[:120])
    finally:
        fb.log.removeHandler(cap)


def test_a_note_never_glues_onto_the_previous_line():
    """Measured 2026-09-25 on a fleet macOS box: notes.md's last line carried no
    terminator, so the next remember landed INSIDE that line and two entries read as one
    fact in every later prompt. The append has to look at the last byte.
    """
    redirect()
    redirect_notes()
    Path(fb.NOTES_FILE).write_text(
        "- [2026-09-22 20:49] first fact, written without a trailing newline",
        encoding="utf-8")
    out = remember({"note": "second fact"})
    text = Path(fb.NOTES_FILE).read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if l.strip()]
    check("the new entry is its own line", len(lines) == 2, repr(text))
    check("and the fact before it was not swallowed",
          lines and "first fact" in lines[0] and lines[0].endswith("a trailing newline"),
          lines)
    check("remember still answered OK", str(out).startswith("OK"), out)


def test_tool_remember_runs_under_the_notes_lock():
    """The lock has to cover the READ side too (the ledger's lesson): a
    `remember` must wait while another writer holds the notes.md lock, or its
    append lands inside a curator's read-modify-write and is erased."""
    redirect()
    redirect_notes()
    import inspect
    captured = inspect.getclosurevars(fb.tool_remember).nonlocals.get("path")
    lock = fb._path_lock(str(captured if captured is not None else fb.NOTES_FILE))
    done = threading.Event()
    entered = threading.Event()

    def go():
        entered.set()
        remember({"note": "lock probe fact"})
        done.set()

    with lock:
        t = threading.Thread(target=go, daemon=True)
        t.start()
        # The handshake matters: without it a thread that had not STARTED yet
        # passes "blocks" for the wrong reason (measured flake under sweep load).
        started = entered.wait(2.0)
        blocked = started and not done.wait(0.5)
    check("tool_remember blocks while the notes.md lock is held", blocked,
          "thread started=%s" % started)
    check("...and completes once it is released", done.wait(10.0))


def test_curate_notes_runs_under_the_notes_lock():
    """Same claim for the curator: its whole-file rewrite must wait its turn.
    It wrote with a plain open("w") and no lock until 2026-09-23 - the exact
    shape that lost the ledger's `done` and first `add`."""
    redirect()
    redirect_notes()
    fb.tool_remember({"note": "a fact so the file is non-empty"}, {})
    lock = fb._path_lock(str(fb.NOTES_FILE))
    done = threading.Event()
    entered = threading.Event()

    def go():
        entered.set()
        fb.curate_notes("probe")
        done.set()

    with lock:
        t = threading.Thread(target=go, daemon=True)
        t.start()
        started = entered.wait(2.0)
        blocked = started and not done.wait(0.5)
    check("curate_notes blocks while the notes.md lock is held", blocked,
          "thread started=%s" % started)
    check("...and completes once it is released", done.wait(10.0))



# --------------------------------------------------------------------------
# one file, two spellings: the key was unnormalised, so the lock was not shared
# --------------------------------------------------------------------------

def test_two_spellings_of_one_path_share_one_lock():
    """The same lost-update as above, one level down: the per-path lock keyed on the
    STRING the model passed, so `C:\\x\\big.txt` and `C:/x/big.txt` were two different
    locks and two edits of one file ran in parallel. `lockprobe` measured both edits
    reporting "OK: replaced 1 occurrence(s)" with one of them gone."""
    import os
    tmp = TMP / "lockkey"
    tmp.mkdir(parents=True, exist_ok=True)
    target = tmp / "big.txt"
    target.write_text("x\n")
    pairs = [(str(target), str(target).replace(os.sep, "/")),
             (str(target), str(target).upper() if os.name == "nt" else str(target)),
             (str(target), str(target) + os.sep + ".")]
    try:
        # A "./"-relative spelling exists only within ONE drive. When the tree lives on a
        # share and the fixture's temp dir sits on the local disk, relpath raises "path is on
        # mount ..." and takes the whole suite down with it (measured 2026-09-26, the first
        # sweep run from a share-mounted checkout). The spellings above cover the same
        # normalisation, so the pair is simply skipped when it cannot be formed.
        pairs.insert(1, (str(target), "./" + os.path.relpath(target, os.getcwd())))
    except ValueError:
        pass
    for a, b in pairs:
        key_a, key_b = fb._lock_key(a), fb._lock_key(b)
        same_file = os.path.samefile(a, b) if os.path.exists(a) and os.path.exists(b) else True
        if same_file:
            check("lock key: %r and %r agree" % (a[-24:], b[-24:]), key_a == key_b,
                  "%r != %r" % (key_a, key_b))
    check("lock key: the same key is the same lock object",
          fb._path_lock(str(target)) is fb._path_lock(str(target).replace(os.sep, "/")))
    check("lock key: a path-less caller keeps the empty key",
          fb._lock_key("") == "" and fb._lock_key(None) == "")


def test_a_batch_of_two_spellings_keeps_both_edits():
    """Behaviour, not just the key: two edits of one file, in two threads, under the two
    spellings, with the write slowed so the two read-modify-write passes really overlap.
    Fails on the pre-fix build (one edit is overwritten)."""
    import time
    tmp = TMP / "lockrace"
    tmp.mkdir(parents=True, exist_ok=True)
    target = tmp / "race.txt"
    target.write_bytes(b"MARKER-AAA\n" + b"filler\n" * 2000 + b"MARKER-BBB\n")
    real = fb.atomic_write_text

    def slow(path, text, encoding="utf-8"):
        time.sleep(0.4)
        return real(path, text, encoding=encoding)
    saved = fb.atomic_write_text
    fb.atomic_write_text = slow
    spellings = [str(target), str(target).replace(os.sep, "/")]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(fb.tool_edit_file, {"path": spellings[0],
                                                 "old_string": "MARKER-AAA",
                                                 "new_string": "MARKER-A1"}, {})
            f2 = pool.submit(fb.tool_edit_file, {"path": spellings[1],
                                                 "old_string": "MARKER-BBB",
                                                 "new_string": "MARKER-B1"}, {})
            f1.result(); f2.result()
    finally:
        fb.atomic_write_text = saved
    body = target.read_bytes()
    check("two spellings: the first edit survives", b"MARKER-A1" in body, body[:60])
    check("two spellings: the second edit survives", b"MARKER-B1" in body, body[-60:])

def test_denied_rename_still_writes():
    """Force os.replace to be denied: the write must land anyway, via the plain path.

    Windows denies a rename when another handle holds the target, so the fallback is real
    behaviour, not an edge case. Waiting for the OS to deny it made this suite flaky; denying
    it ourselves tests the fallback every run.
    """
    redirect()
    cap = Capture()
    fb.log.addHandler(cap)
    real_replace = fb.os.replace
    def deny(*a, **k):
        raise PermissionError(5, "Access is denied")
    try:
        fb.os.replace = deny
        out = fb.tool_task({"action": "add", "task": "forced fallback"}, {})
        check("the add answers OK even with the rename denied", str(out).startswith("OK"), out)
        check("the fallback is logged", bool(cap.atomic_failures()), cap.atomic_failures())
        items = json.loads(Path(fb.TASKS_FILE).read_text(encoding="utf-8"))["items"]
        check("the item landed through the plain write",
              any("forced fallback" in (i.get("desc") or "") for i in items), items)
    finally:
        fb.os.replace = real_replace
        fb.log.removeHandler(cap)


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for t in tests:
        if only and only not in t.__name__:
            continue
        try:
            t()
        except Exception as e:
            import traceback
            FAILURES.append(f"{t.__name__} raised: {e}")
            traceback.print_exc()
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print("  FAIL:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
