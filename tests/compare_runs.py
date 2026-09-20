"""Compare scenario runs: medians per (scenario, label) and a before/after diff.

Reads the JSON lines that tests/run_scenario.py writes.

    python tests/run_scenario.py long --budget 12000 --label before > /tmp/before.jsonl
    # ... change something ...
    python tests/run_scenario.py long --budget 12000 --label after  > /tmp/after.jsonl
    python tests/compare_runs.py /tmp/before.jsonl /tmp/after.jsonl

Numbers are only ever compared within one provider (local and cloud are different
models), so keep the files separate per provider.
"""
import json
import statistics
import sys
from collections import defaultdict

KEYS = ("llm_calls", "prompt_tokens", "completion_tokens", "wall_s", "llm_secs",
        "tool_calls", "compactions", "blocks_dropped", "duplicates_blocked",
        "duplicates_labelled", "pairing_repairs", "narration", "answer_chars")
LOWER_BETTER = {"llm_calls", "prompt_tokens", "completion_tokens", "wall_s",
                "llm_secs", "tool_calls", "compactions", "blocks_dropped",
                "duplicates_blocked", "duplicates_labelled", "pairing_repairs"}


def load(paths):
    groups = defaultdict(list)
    for path in paths:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            groups[(row.get("scenario"), row.get("label"))].append(row)
    return groups


def med(rows, key):
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return round(statistics.median(vals), 1) if vals else None


def main(argv):
    groups = load(argv[1:] or ["/dev/stdin"])
    for (scenario, label), rows in sorted(groups.items()):
        print(f"=== {scenario} / {label or '(no label)'}  n={len(rows)}")
        for k in KEYS:
            v = med(rows, k)
            if v is not None:
                print(f"    {k:22s} {v}")
        answers = {r.get("answer_sha") for r in rows}
        print(f"    answer_shas           {len(answers)} distinct: {sorted(answers)}")
    if len(groups) > 1:
        print("\n=== deltas (second group vs first, per scenario) ===")
        names = list(groups)
        for scenario in sorted({s for s, _ in names}):
            pair = [g for g in names if g[0] == scenario]
            if len(pair) != 2:
                continue
            a, b = groups[pair[0]], groups[pair[1]]
            print(f"  {scenario}: {pair[0][1]} -> {pair[1][1]}")
            for k in KEYS:
                va, vb = med(a, k), med(b, k)
                if va is None or vb is None or va == vb:
                    continue
                arrow = "better" if ((vb < va) == (k in LOWER_BETTER)) else "worse"
                print(f"    {k:22s} {va} -> {vb}   ({arrow})")


if __name__ == "__main__":
    main(sys.argv)
