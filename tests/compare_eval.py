"""Compare two graded-set runs (tests/eval-runs/*.jsonl) task by task.

    python tests/compare_eval.py <before.jsonl> <after.jsonl>

The harness has compare_runs.py for the run_scenario JSONL; this is the same idea for the
GRADED set, where the number that matters per task is `pass`, and the interesting deltas are
steps, calls and prompt tokens. Prints per-task rows that differ, then the totals.
"""
import json
import sys
from pathlib import Path


def load(path):
    rows = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r.get("task")] = r
    return rows


def main(before_path, after_path):
    b, a = load(before_path), load(after_path)
    keys = sorted(set(b) | set(a))
    print("%-24s %-10s %-10s %7s %7s %8s %8s"
          % ("task", "before", "after", "steps", "steps", "ptok", "ptok"))
    print("%-24s %-10s %-10s %7s %7s %8s %8s"
          % ("", "(pass)", "(pass)", "b", "a", "b", "a"))
    print("-" * 78)
    tot = {"b": {"pass": 0, "steps": 0, "ptok": 0, "calls": 0},
           "a": {"pass": 0, "steps": 0, "ptok": 0, "calls": 0}}
    for k in keys:
        rb, ra = b.get(k), a.get(k)
        def val(r, field):
            return (r or {}).get(field) or 0
        for side, r in (("b", rb), ("a", ra)):
            tot[side]["pass"] += 1 if r and r.get("pass") else 0
            tot[side]["steps"] += val(r, "steps_seen")
            tot[side]["ptok"] += val(r, "prompt_tokens")
            tot[side]["calls"] += val(r, "llm_calls")
        flag = ""
        if bool(rb and rb.get("pass")) != bool(ra and ra.get("pass")):
            flag = "  <-- SCORE CHANGED"
        print("%-24s %-10s %-10s %7d %7d %8d %8d%s"
              % (k, "PASS" if rb and rb.get("pass") else "fail",
                 "PASS" if ra and ra.get("pass") else "fail",
                 val(rb, "steps_seen"), val(ra, "steps_seen"),
                 val(rb, "prompt_tokens"), val(ra, "prompt_tokens"), flag))
    print("-" * 78)
    print("TOTAL pass   %d/%d -> %d/%d" % (tot["b"]["pass"], len(keys),
                                           tot["a"]["pass"], len(keys)))
    for field, label in (("steps", "tool steps"), ("calls", "llm calls"), ("ptok", "prompt tokens")):
        lo, hi = tot["b"][field], tot["a"][field]
        pct = ("%+.0f%%" % (100 * (hi - lo) / lo)) if lo else "n/a"
        print("TOTAL %-12s %8d -> %8d  (%s)" % (label, lo, hi, pct))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
