"""Damaged state files are kept aside, not silently forgotten.

state.json, logs/state.json (the lane failure record), web-sessions.json and jobs.json
are the host's durable memory. Every reader used to turn a corrupt or truncated file into {}
in silence, and the next save overwrote the only evidence. This
suite stages the module, points the four paths into a temp dir, and grades the
quarantine: bytes kept in a `.damaged-*` copy, the document empty, and a healthy file
untouched.

    python tests/test_state_damage.py
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILS.append(name)
        print(f"FAIL {name}: {detail}")


def main():
    work = Path(tempfile.mkdtemp(prefix="tc-state-"))
    try:
        shutil.copy2(SRC, work / "tinycmdr.py")
        shutil.copy2(BASE / "tests" / "fixture-config.json", work / "config.json")
        spec = importlib.util.spec_from_file_location("tc_state_damage",
                                                      work / "tinycmdr.py")
        fb = importlib.util.module_from_spec(spec)
        sys.modules["tc_state_damage"] = fb
        spec.loader.exec_module(fb)

        # ---- state.json ----------------------------------------------------
        st_file = work / "state.json"
        fb.GLOBAL_STATE_FILE = st_file
        st_file.write_text("{not json", encoding="utf-8")
        got = fb._state()
        copies = sorted(work.glob("state.json.damaged-*"))
        check("a corrupt state.json reads as empty", got == {}, got)
        check("...and its bytes are kept in a .damaged copy", len(copies) == 1,
              [p.name for p in work.iterdir()])
        check("...with the original content",
              bool(copies) and copies[0].read_text(encoding="utf-8") == "{not json")
        st_file.write_text('{"model_overrides": {"x": "m"}}', encoding="utf-8")
        got = fb._state()
        check("a healthy state.json is read, and no second copy is made",
              got.get("model_overrides") == {"x": "m"}
              and len(sorted(work.glob("state.json.damaged-*"))) == 1, got)

        # ---- the lane failure record ---------------------------------------
        lane = work / "lane-state.json"
        fb.LANE_STATE_FILE = lane
        lane.write_text("[1,2,3]", encoding="utf-8")      # valid JSON, wrong shape
        check("a lane record of the wrong shape reads as empty (no copy)",
              fb._lane_state_read() == {}
              and not list(work.glob("lane-state.json.damaged-*")))
        lane.write_text("truncated {", encoding="utf-8")
        check("a corrupt lane record reads as empty", fb._lane_state_read() == {})
        check("...and is kept aside",
              len(sorted(work.glob("lane-state.json.damaged-*"))) == 1,
              [p.name for p in work.iterdir()])

        # ---- the conversation registry -------------------------------------
        web = work / "web-sessions.json"
        fb.WEB_STATE_FILE = web
        web.write_text("}{", encoding="utf-8")
        got = fb._web_state()
        check("a corrupt registry reads with empty collections",
              got.get("sessions") == [] and got.get("open") == {}, got)
        check("...and is kept aside",
              len(sorted(work.glob("web-sessions.json.damaged-*"))) == 1)

        # ---- the schedule ----------------------------------------------------
        # The one state file this suite did not cover, because the scheduler carried its
        # own silent reader: it answered {} for anything unreadable, with no log line and
        # no copy, and the next save took the only evidence of every job (run
        # 17, A-2026-10-07-08).
        jobs = work / "jobs.json"
        fb.JOBS_FILE = jobs
        jobs.write_text("}{ not json", encoding="utf-8")
        sched = fb.Scheduler(jobs)
        sched._stop.set()
        check("a corrupt jobs.json reads as an empty schedule", sched.jobs == {},
              sched.jobs)
        jcopies = sorted(work.glob("jobs.json.damaged-*"))
        check("...and its bytes are kept in a .damaged copy", len(jcopies) == 1,
              [p.name for p in work.iterdir()])
        check("...with the original content",
              bool(jcopies) and jcopies[0].read_text(encoding="utf-8") == "}{ not json")
        # ---- two corruptions inside one second do not share a copy -------------
        # The stamp has one-second resolution and copy2 overwrites, so two readers hitting
        # the same corrupt file in the same second (the service and a --once run both load
        # state.json at start) left ONE copy - whichever ran second, which is the least
        # interesting one when a restart loop keeps re-reading a wedged file.
        collide = work / "state-collide.json"
        fb.GLOBAL_STATE_FILE = collide
        collide.write_text("{first", encoding="utf-8")
        fb._state()
        collide.write_text("{second", encoding="utf-8")
        fb._state()
        kept = sorted(work.glob("state-collide.json.damaged-*"))
        held = {p.read_text(encoding="utf-8") for p in kept}
        check("a second corruption in the same second gets its own copy",
              len(kept) == 2, [p.name for p in kept])
        check("...and neither copy overwrote the other",
              "{first" in held and "{second" in held, held)

        # ---- retention is bounded, newest kept --------------------------------
        many = work / "state-many.json"
        fb.GLOBAL_STATE_FILE = many
        for i in range(fb._DAMAGED_KEEP + 3):
            many.write_text("{bad%d" % i, encoding="utf-8")
            fb._state()
        all_copies = sorted(work.glob("state-many.json.damaged-*"))
        check("quarantine retention is bounded (at most %d per state file)"
              % fb._DAMAGED_KEEP, len(all_copies) == fb._DAMAGED_KEEP,
              [p.name for p in all_copies])
        check("...and it is the newest copies that are kept",
              [p.name for p in all_copies] == [p.name for p in
                                               sorted(all_copies)[-fb._DAMAGED_KEEP:]],
              [p.name for p in all_copies])
        check("...so the oldest is the one dropped",
              not any("{bad0" in p.read_text(encoding="utf-8") for p in all_copies))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        return 1
    print("all state damage checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
