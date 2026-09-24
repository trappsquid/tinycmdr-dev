"""The experiment ledger: what this box has already TESTED (audit, 2026-09-21).

The campaign harness re-ran arms it had already measured, and one verdict ("MTP = wash")
was retracted silently because nothing recorded that an earlier line had been superseded.
These checks pin the replacement: an append-only file, a prompt index that is not the
file, and a gate that refuses to re-buy a settled question while citing its verdict.

    python tests/test_experiment.py
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


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILS.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbtest-exp-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        path = fb.EXPERIMENTS_FILE

        def lines():
            if not path.exists():
                return []
            return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

        # ---- an empty ledger is honest about being empty -------------------------
        out = fb.tool_experiment({"action": "index"}, {})
        check("an empty ledger says so and says how to open one",
              "empty" in out.lower() and "action=add" in out, out[:90])

        # ---- the fields are the the LAN model boxbot schema ----------------------------------
        check("the record schema is theirs, verbatim (29 names)",
              len(fb.EXPERIMENT_FIELDS) == 29
              and fb.EXPERIMENT_FIELDS[:6] == ("id", "date", "agent", "status",
                                               "question", "keys")
              and "binary+commit" in fb.EXPERIMENT_FIELDS
              and "fill_depth" in fb.EXPERIMENT_FIELDS
              and "superseded_by" in fb.EXPERIMENT_FIELDS,
              str(fb.EXPERIMENT_FIELDS))

        keys = ["mtp", "ctx38k"]
        cfg = "llama-server -np 2 --ctx-size 38912 -fa"
        out = fb.tool_experiment(
            {"action": "add", "question": "Does MTP pay off at 38k?",
             "keys": keys, "exact_config": cfg,
             "fields": {"preregistration": "MTP on should halve prefill",
                        "fill_depth": "8K fill",
                        "engine": "llama.cpp b6000",
                        "result": "1.02x prefill, 3 reps, spread 0.4%",
                        "verdict": "MTP is a wash at this fill depth",
                        "body": "two arms, 3 reps each, interleaved"}}, {})
        check("a first experiment is recorded", out.startswith("OK: experiment #1"),
              out[:120])
        check("as ONE appended line", len(lines()) == 1, str(len(lines())))
        rec = json.loads(lines()[0])
        check("the record carries the fields it was given",
              rec["fill_depth"] == "8K fill" and rec["verdict"].startswith("MTP is a wash")
              and rec["keys"] == keys, json.dumps(rec))
        check("id, date, agent and status are filled in by the harness",
              rec["id"] == 1 and rec["date"] and rec["agent"] and rec["status"] == "open")
        check("exact_config is stored literally, not summarised",
              rec["exact_config"] == cfg)

        # ---- the prompt gets the INDEX, never the file ---------------------------
        block = fb.render_experiment_prompt()
        check("the prompt block carries id, question, keys and verdict",
              "#1" in block and "mtp,ctx38k" in block
              and "verdict: MTP is a wash" in block, block[:200])
        check("... and not the whole record (preregistration stays on disk)",
              "preregistration" not in block)
        out = fb.tool_experiment({"action": "index"}, {})
        check("action=index lists it with a date", "#1 [" in out and rec["date"] in out,
              out[:120])

        # ---- the GATE: a settled question is not re-bought -----------------------
        out = fb.tool_experiment({"action": "add", "question": "MTP again, to be sure",
                                  "keys": keys, "exact_config": cfg}, {})
        check("a repeat arm is REFUSED", out.startswith("REFUSED"), out[:90])
        check("and the earlier verdict is cited in the refusal",
              "MTP is a wash" in out, out[:220])
        check("and the refusal names that line", "#1" in out)
        check("and nothing was written", len(lines()) == 1, str(len(lines())))

        out = fb.tool_experiment({"action": "add", "question": "MTP at a 4K fill",
                                  "keys": keys, "exact_config": cfg + " --fill 4k"}, {})
        check("same keys but a different exact_config is a DIFFERENT arm",
              out.startswith("OK: experiment #2"), out[:90])

        out = fb.tool_experiment({"action": "add", "question": "MTP re-run, 5 reps",
                                  "keys": keys, "exact_config": cfg, "supersedes": 1,
                                  "fields": {"preregistration": "the old run was 1 rep"}}, {})
        check("naming the line it supersedes allows the re-run",
              out.startswith("OK: experiment #3"), out[:90])
        check("and the result says what it superseded", "#1" in out and "MTP is a wash" in out,
              out[:220])
        recs = [json.loads(l) for l in lines()]
        one = [r for r in recs if str(r.get("id")) == "1"][-1]
        check("the superseded line is marked by APPENDING, never by rewriting",
              one.get("status") == "superseded" and one.get("superseded_by") == 3,
              json.dumps(one))

        # ---- an update is an append too ------------------------------------------
        before = lines()
        out = fb.tool_experiment({"action": "update", "id": 2,
                                  "fields": {"status": "done",
                                             "verdict": "MTP pays at a 4K fill"}}, {})
        check("an update lands", out.startswith("OK: experiment #2"), out[:90])
        check("the earlier lines are byte-identical afterwards",
              lines()[:len(before)] == before)
        check("and the newest line wins on read",
              "MTP pays at a 4K fill" in fb.tool_experiment({"action": "show", "id": 2}, {}))

        # ---- refusals that teach -------------------------------------------------
        out = fb.tool_experiment({"action": "update", "id": 2, "fields": {"nonsense": 1}}, {})
        check("an unknown field is refused BY NAME",
              out.startswith("ERROR") and "nonsense" in out, out[:140])
        out = fb.tool_experiment({"action": "add", "question": "no config at all",
                                  "keys": ["x"]}, {})
        check("add demands the exact config, and says so",
              out.startswith("ERROR") and "exact_config" in out, out[:160])
        out = fb.tool_experiment({"action": "show", "id": 99}, {})
        check("an unknown id names the way to list the ids",
              out.startswith("ERROR") and "action=index" in out, out[:120])

        # ---- Item D: on-demand by default to save ~350 tokens/turn ---------------
        check("the experiment tool is available on-demand",
              "experiment" in fb.hidden_tools(None))
        exp_s = [s for s in fb.REGISTRY.openai_schemas()
                 if s["function"]["name"] == "experiment"][0]
        check("and its schema is inside the per-tool cap",
              len(json.dumps(exp_s)) <= 1200, str(len(json.dumps(exp_s))))
        v = fb.volatile_context(session_key=None)
        check("the ledger index is in the prompt block", "experiment ledger" in v,
              v[-300:])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all experiment-ledger checks passed")


if __name__ == "__main__":
    main()
