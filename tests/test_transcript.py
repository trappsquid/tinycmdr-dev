"""Compaction keeps what it destroys (2026-09-18).

The harness's own analysis ranked this second: `_compact` shrinks and deletes, and the session
file holds the compacted version only, so the evicted middle was unrecoverable. These checks pin
the transcript sink and the tally that makes it visible: before anything is cut, the full text
lands in sessions/<key>.transcript.jsonl, and a compaction shows up in the usage line.
"""
import json
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
    workdir = Path(tempfile.mkdtemp(prefix="fbtest-transcript-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        key = "transcript-session"
        fb.SESSIONS_DIR = workdir / "sessions"
        fb.SESSIONS_DIR.mkdir(exist_ok=True)

        # a conversation far over the budget, with a needle that only the transcript will hold
        needle = "NEEDLE-8KQ31 the only copy of this sentence"
        messages = [{"role": "system", "content": "sys"}]
        for i in range(400):
            messages.append({"role": "user", "content": f"question {i} " + "q" * 400})
            messages.append({"role": "assistant", "content": needle if i == 12 else f"answer {i} " + "a" * 400})
        before = len(messages)
        out = fb.AGENT._compact(messages, key)

        check(len(out) < before, f"compaction shrank the payload ({before} -> {len(out)} messages)")
        tp = fb.SESSIONS_DIR / f"{key}.transcript.jsonl"
        check(tp.exists(), "the transcript file exists")
        body = tp.read_text(encoding="utf-8") if tp.exists() else ""
        check(needle in body, "the evicted middle is in the transcript, byte for byte")
        rows = [json.loads(l) for l in body.splitlines() if l.strip()]
        check(len(rows) == before - 1, f"one line per non-system message ({len(rows)})")
        check(all(r.get("why") == "compact" for r in rows), "each line says why it was written")
        check(json.loads((fb.SESSIONS_DIR / f"{key}.transcript.jsonl").read_text(encoding="utf-8")
                         .splitlines()[0]).get("role") == "user",
              "and the first line is the oldest message, in order")

        # the tally the operator sees
        st = fb.run_state(key, create=True)
        check(int(st.get("compactions") or 0) == 1, f"the run state counts it ({st.get('compactions')})")
        line = fb.fmt_usage({"calls": 2, "prompt": 100, "completion": 10, "llm_secs": 1,
                             "compactions": 1})
        check("compaction(s)" in line, f"and the usage line shows it: {line}")

        # nothing over budget: nothing written
        tp.unlink()
        small = [{"role": "system", "content": "sys"},
                 {"role": "user", "content": "hi"},
                 {"role": "assistant", "content": "hello"}]
        fb.AGENT._compact(small, key)
        check(not tp.exists(), "a payload inside the budget writes no transcript")

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) FAILED")
            return 1
        print("all transcript checks passed")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
