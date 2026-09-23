"""Concurrent state writes: one batch, one file, no lost entry.

Run:  python tests/test_ledger_race.py            (all tests)
      python tests/test_ledger_race.py <substring>  (one test)

The measured failure this suite pins (operator drive on the Windows test box, 2026-09-23):
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

# Which build to import: the Mattermost bot by default, the chatless CLI build
# with TINYCMDR_SRC=tinycmdr-cli.py.
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

# --- hermetic staging (same idiom as the other suites) ----------------------
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage"
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
        check("no atomic write fell back to the plain path",
              not cap.atomic_failures(), cap.atomic_failures())
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
        check("no atomic write fell back to the plain path",
              not cap.atomic_failures(), cap.atomic_failures())
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
        check("no atomic write fell back to the plain path",
              not cap.atomic_failures(), cap.atomic_failures())
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
