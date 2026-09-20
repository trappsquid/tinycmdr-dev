"""Offline checks for the eval scoreboard: the grader must not pass or fail by accident.

No model calls. Two directions per task:

  - a fresh staged install with an empty answer must FAIL (no free passes), and
  - hand-built "correct" end states for a sample of tasks must PASS, which is what
    proves the check specs are writable at all (a task whose spec can never be
    satisfied would silently cap the scoreboard below 100%).

    python tests/test_eval_grading.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import eval_tasks  # noqa: E402
import run_eval  # noqa: E402

FAILS = []


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def staged(task, budget=24000):
    workdir = Path(tempfile.mkdtemp(prefix=f"fbgrade-{task['id']}-"))
    run_eval.stage_task(workdir, budget, task)
    return workdir


def metrics_for(workdir, answer, **kw):
    m = {"task": kw.get("tid", "?"), "category": kw.get("cat", "?"),
         "difficulty": "x", "label": "grading", "tool_calls": 0,
         "tool_errors": [], "_workdir": str(workdir)}
    m.update(kw)
    m["answer"] = answer
    return m


def main():
    # 1. every task must fail on a fresh stage with no answer
    for task in eval_tasks.TASKS:
        wd = staged(task)
        try:
            passed, why = run_eval.grade(task, metrics_for(wd, "", tid=task["id"]))
            check(not passed, f"{task['id']} fails on an empty run")
        finally:
            shutil.rmtree(wd, ignore_errors=True)

    # 2. hand-built correct states must pass
    positive = []

    # T01: the file exists with 50 lines
    t = eval_tasks.BY_ID["T01_write_count"]
    wd = staged(t)
    (wd / "probe.txt").write_text("\n".join(str(i) for i in range(1, 51)) + "\n",
                                  encoding="utf-8")
    positive.append((t, metrics_for(wd, "The file has 50 lines.")))

    # T02: valid JSON with the right port
    t = eval_tasks.BY_ID["T02_fix_config"]
    wd2 = staged(t)
    (wd2 / "app-settings.json").write_text(
        json.dumps({"service": {"name": "backupd", "port": 8082, "retries": 3}}),
        encoding="utf-8")
    positive.append((t, metrics_for(wd2, "Set service.port to 8082.")))

    # T04: right answer, zero tool calls
    positive.append((eval_tasks.BY_ID["T04_no_tools_needed"],
                     metrics_for(BASE, "391")))

    # T05: no-op admitted, file untouched
    t = eval_tasks.BY_ID["T05_already_correct"]
    wd3 = staged(t)
    positive.append((t, metrics_for(wd3, "log_level was already DEBUG, so no change "
                                         "was needed.")))

    # T07: merged file exact, answer reports 5 lines and epsilon
    t = eval_tasks.BY_ID["T07_six_steps"]
    wd4 = staged(t)
    (wd4 / "build" / "eval").mkdir(parents=True)
    (wd4 / "build" / "eval" / "merged.txt").write_text(
        "alpha\nbeta\ngamma\ndelta\nepsilon\n", encoding="utf-8")
    positive.append((t, metrics_for(wd4, "merged.txt has 5 lines, last line epsilon")))

    # T08 / T11 / T10: answer-only checks
    positive.append((eval_tasks.BY_ID["T08_fixture_triage"],
                     metrics_for(BASE, "cache-svc is unhealthy, publishing port 6379")))
    positive.append((eval_tasks.BY_ID["T11_precision"],
                     metrics_for(BASE, "8443")))
    positive.append((eval_tasks.BY_ID["T10_unknown_tool"],
                     metrics_for(BASE, "There is no such tool as docker_manager in "
                                       "this install.")))
    # T12: the run must land on the budget status and still report
    positive.append((eval_tasks.BY_ID["T12_budget_landing"],
                     metrics_for(BASE, "Partial work. VERIFIED: nothing",
                                 status="budget")))
    # T13 / T14: the scored artefacts of the two Phase 1 features
    positive.append((eval_tasks.BY_ID["T13_buried_error"],
                     metrics_for(BASE, "vaultsync failed: checksum mismatch",
                                 digests_fired=1)))
    positive.append((eval_tasks.BY_ID["T14_field_note"],
                     metrics_for(BASE, "The command was not recognized: no such "
                                       "program on this box.",
                                 field_notes_fired=1)))
    # T15 / T16: the scored artefacts of post-write verification
    t = eval_tasks.BY_ID["T15_verify_ok"]
    wd7 = staged(t)
    (wd7 / "config.json").write_text(
        json.dumps({"service": {"name": "backupd", "port": 8082}}), encoding="utf-8")
    positive.append((t, metrics_for(wd7, "The port is 8082.", verifies_fired=1)))
    positive.append((eval_tasks.BY_ID["T16_verify_failure"],
                     metrics_for(BASE, "The write failed: the file is invalid JSON.",
                                 verify_failures=1)))
    # T09: both defaults, which are the values in tinycmdr.py
    positive.append((eval_tasks.BY_ID["T09_code_grounding"],
                     metrics_for(BASE, "notes_max_chars is 4000 and tasks_max_open "
                                       "is 15.")))
    # T03 / T06
    positive.append((eval_tasks.BY_ID["T03_log_cause"],
                     metrics_for(BASE, "backupd is failing: disk quota exceeded")))
    positive.append((eval_tasks.BY_ID["T06_wrong_path"],
                     metrics_for(BASE, "The third column sums to 137.")))

    for task, m in positive:
        passed, why = run_eval.grade(task, m)
        check(passed, f"{task['id']} passes on a correct state "
                      f"({[w for w in why if w.startswith('FAIL')] or 'all rules ok'})")

    # 3. wrong answers must still fail, not just empty ones
    t = eval_tasks.BY_ID["T03_log_cause"]
    passed, _ = run_eval.grade(t, metrics_for(BASE, "indexer is failing"))
    check(not passed, "T03 fails on the wrong service")
    t = eval_tasks.BY_ID["T05_already_correct"]
    passed, _ = run_eval.grade(t, metrics_for(BASE, "I changed the log level to DEBUG"))
    check(not passed, "T05 fails when the model claims a change it did not make")
    t = eval_tasks.BY_ID["T11_precision"]
    passed, _ = run_eval.grade(t, metrics_for(BASE, "The port is 8081"))
    check(not passed, "T11 fails on the wrong port")
    # T10 must not be satisfied by building the tool instead of checking
    t = eval_tasks.BY_ID["T10_unknown_tool"]
    passed, _ = run_eval.grade(t, metrics_for(
        BASE, "docker_manager did not exist, so I created it", create_tool_calls=1))
    check(not passed, "T10 fails when the model builds the missing tool")
    # T14 must not pass when the note never fired, even if the answer is right
    t = eval_tasks.BY_ID["T14_field_note"]
    passed, _ = run_eval.grade(t, metrics_for(
        BASE, "zztool was not recognized", field_notes_fired=0))
    check(not passed, "T14 fails when the field note did not fire")
    # T16 must not pass when the harness never reported the broken write
    t = eval_tasks.BY_ID["T16_verify_failure"]
    passed, _ = run_eval.grade(t, metrics_for(
        BASE, "Wrote broken.json, all good.", verify_failures=0))
    check(not passed, "T16 fails when no verify failure was reported")

    # 4. per-task config overrides land in the staged config
    t = eval_tasks.BY_ID["T12_budget_landing"]
    wd5 = staged(t)
    try:
        cfg = json.loads((wd5 / "config.json").read_text(encoding="utf-8"))
        check(cfg["agent"]["max_steps"] == 8,
              f"T12 max_steps override is staged (got {cfg['agent']['max_steps']})")
        check(cfg["agent"]["max_minutes"] == 10,
              "T12 max_minutes override is staged")
    finally:
        shutil.rmtree(wd5, ignore_errors=True)
    t = eval_tasks.BY_ID["T01_write_count"]
    wd6 = staged(t)
    try:
        cfg = json.loads((wd6 / "config.json").read_text(encoding="utf-8"))
        check(cfg["agent"]["max_steps"] == 40,
              "defaults apply when a task has no override")
        check((wd6 / "tinycmdr.py").exists(), "the staged install has tinycmdr.py")
    finally:
        shutil.rmtree(wd6, ignore_errors=True)

    # 5. task set sanity
    ids = [t["id"] for t in eval_tasks.TASKS]
    check(len(ids) == len(set(ids)), "task ids are unique")
    check(len(ids) >= 12, f"task set has {len(ids)} tasks")
    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print(f"all grading checks passed ({len(ids)} tasks)")


if __name__ == "__main__":
    main()
