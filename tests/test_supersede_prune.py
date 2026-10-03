"""A re-read supersedes the older copy - but only when the prefix cache can take it.

Ported from omp's compaction pruning (packages/agent/src/compaction/pruning.ts:1-263,
264-420,498-515): a read->edit->read loop keeps every version of a file in context until
the whole conversation is compacted, and tinycmdr measured 46% of reads of its own source
as re-acquisitions. Blanking an older result mutates the prompt PREFIX, which forces the
provider to re-write everything after it - so the pass is gated: now only when the suffix
is small, otherwise at the idle flush when the cache is cold.

    python tests/test_supersede_prune.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import run_scenario  # noqa: E402

FAILS = []


def check(cond, what, detail=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}: {detail}")
    else:
        print(f"ok   {what}")


LONG = "read me " * 700          # ~5600 est tokens, well past the prune floor


def read_call(cid, path, **kw):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": cid, "type": "function",
                            "function": {"name": "read_file",
                                         "arguments": json.dumps(dict(path=path, **kw))}}]}


def result(cid, text):
    return {"role": "tool", "tool_call_id": cid, "content": text}


def transcript(path, newest_form=None):
    return [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        read_call("a1", str(path)),                          # 2
        result("a1", LONG),                                  # 3 <- older read
        {"role": "user", "content": "u2"},                   # 4
        read_call("a2", str(path), **(newest_form or {})),   # 5 <- newer read
        result("a2", LONG),                                  # 6
        {"role": "assistant", "content": "ok"},              # 7
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "x"},
        {"role": "user", "content": "u4"},
        {"role": "assistant", "content": "y"},
    ]


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbprune-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        A = fb.AGENT
        target = workdir / "target.txt"
        target.write_text("hello\n", encoding="utf-8")

        # ---- a newer full read supersedes the older copy
        fb.CONFIG["agent"]["prune_superseded"] = True
        fb.CONFIG["agent"]["prune_suffix_tokens"] = 100000
        msgs = transcript(target)
        saved = A._supersede_prune(msgs, "prune-key")
        check(msgs[3]["content"] == fb.SUPERSEDED_NOTICE,
              "the older read is blanked", msgs[3]["content"][:80])
        check(msgs[6]["content"] == LONG, "the newest read survives",
              msgs[6]["content"][:60])
        check(saved > 0 and len(msgs) == 12,
              "the reclaim is counted, and the list keeps its length",
              (saved, len(msgs)))

        # ---- the suffix gate: a big suffix defers the blanking
        fb.CONFIG["agent"]["prune_suffix_tokens"] = 1
        fb.CONFIG["agent"]["prune_idle_secs"] = 10 ** 9
        msgs = transcript(target)
        A._session_path = lambda key: workdir / "missing-session.json"
        saved = A._supersede_prune(msgs, "prune-key")
        check(msgs[3]["content"] == LONG and saved == 0,
              "a warm cache prefix defers the blanking",
              (saved, msgs[3]["content"][:40], msgs[6]["content"][:20]))

        # ---- ...but the idle flush blanks it anyway
        fb.CONFIG["agent"]["prune_idle_secs"] = 10
        sess = workdir / "sessions"
        sess.mkdir(exist_ok=True)
        f = sess / "idle-session.json"
        f.write_text("{}", encoding="utf-8")
        old = time.time() - 3600
        os.utime(f, (old, old))
        A._session_path = lambda key: f
        msgs = transcript(target)
        saved = A._supersede_prune(msgs, "idle-session")
        check(msgs[3]["content"] == fb.SUPERSEDED_NOTICE and saved > 0,
              "the idle flush blanks it with a cold cache",
              (saved, msgs[3]["content"][:40]))

        # ---- a PARTIAL newer read does not supersede a full older one
        fb.CONFIG["agent"]["prune_suffix_tokens"] = 100000
        msgs = transcript(target, newest_form={"offset": 0, "limit": 5})
        A._supersede_prune(msgs, "prune-key")
        check(msgs[3]["content"] == LONG,
              "a partial re-read does not blank a full read", msgs[3]["content"][:40])

        # ---- ...but it does supersede the same partial form
        msgs = transcript(target)
        msgs[2] = read_call("a1", str(target), offset=0, limit=5)
        msgs[3] = result("a1", LONG)
        A._supersede_prune(msgs, "prune-key")
        check(msgs[3]["content"] == fb.SUPERSEDED_NOTICE,
              "the same partial form IS superseded", msgs[3]["content"][:40])

        # ---- a failed read is never blanked, and the newest read is never blanked
        msgs = transcript(target)
        msgs[3] = result("a1", "ERROR: no such file")
        A._supersede_prune(msgs, "prune-key")
        check(msgs[3]["content"].startswith("ERROR"),
              "an ERROR result is left alone", msgs[3]["content"][:40])
        msgs = transcript(target)
        A._supersede_prune(msgs, "prune-key")
        check(msgs[6]["content"] == LONG, "the newest read is never blanked")

        # ---- _compact runs the pass before the budget check
        msgs = transcript(target)
        A._compact(msgs, "prune-key")
        check(msgs[3]["content"] == fb.SUPERSEDED_NOTICE,
              "_compact reclaims before deciding to cut", msgs[3]["content"][:40])

        # ---- _drop_oldest_block reports the NET tokens it removed
        small = [{"role": "system", "content": "s"},
                 {"role": "user", "content": "u1"},
                 {"role": "assistant", "content": "a1"},
                 {"role": "tool", "tool_call_id": "x", "content": "t1"},
                 {"role": "user", "content": "u2"},
                 {"role": "assistant", "content": "a2"}]
        removed = A._drop_oldest_block(small, fb.MARK_COMPACT)
        check(isinstance(removed, int) and removed > 0 and len(small) == 4,
              "_drop_oldest_block returns the net tokens removed",
              (removed, len(small)))
        check(A._drop_oldest_block(small, fb.MARK_COMPACT) is None,
              "...and None when only the newest exchange is left")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all superseded-prune checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
