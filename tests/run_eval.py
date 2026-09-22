"""Run the graded task set in tests/eval_tasks.py and print a scoreboard.

Phase 0 of the scaffolding plan. This is the measuring stick: the same tasks, the
same model, the same config, before and after a harness change. Nothing in here
touches a live bot — it stages a temp install (a byte copy of tinycmdr.py plus a
generated config.json) and drives AGENT.run() in-process, the way the suites do.

    python tests/run_eval.py --list
    python tests/run_eval.py --all --label baseline
    python tests/run_eval.py T03_log_cause T10_unknown_tool --label probe

Endpoints and model come from the environment, so this file stays publishable:

    TINYCMDR_TEST_BASE_URL   default http://127.0.0.1:8081/v1
    TINYCMDR_TEST_MODEL      default main
    TINYCMDR_TEST_BUDGET     llm.max_context_tokens for the staged config, default 24000

Per-task overrides live on the task (`config`); the defaults below match what the
shipped build does on a real box, so the baseline is production-shaped rather than
a special eval mode.

Every run writes one JSON line per task to tests/eval-runs/<stamp>-<label>.jsonl,
so the same file can be fed to compare_runs.py for a before/after diff.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import eval_tasks  # noqa: E402
import run_scenario  # noqa: E402

RUNS_DIR = TESTS / "eval-runs"

# Production-shaped defaults for every task unless the task overrides them.
DEFAULT_CONFIG = {"max_steps": 40, "max_minutes": 12}


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------

def _get_path(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def check_files(workdir, spec):
    """File assertions, each returning (ok, reason). Reasons are what make a
    failure readable instead of a bare False."""
    out = []
    for rel, rules in (spec or {}).items():
        path = workdir / rel
        if rules.get("exists") and not path.exists():
            out.append((False, f"{rel}: missing"))
            continue
        if not path.exists():
            out.append((True, f"{rel}: absent as expected"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if text.startswith("\ufeff"):
            # Windows shells write UTF-8 with a BOM by default (Set-Content, Out-File),
            # which is one invisible character in an otherwise correct file. Caught by
            # T07: "content differs (got 32 chars, want 31)".
            text = text.lstrip("\ufeff")
        if "equals" in rules:
            want = rules["equals"]
            # Line endings are not what these tasks are about: on Windows the shell's
            # own writers produce CRLF, so an exactly-correct file was failing on a
            # byte count (measured: T07 got 32 chars, wanted 31, content identical).
            got = text.replace("\r\n", "\n").replace("\r", "\n")
            if not got.endswith("\n"):
                got += "\n"
            if got != want:
                out.append((False, f"{rel}: content differs "
                                   f"(got {len(got)} chars, want {len(want)})"))
            else:
                out.append((True, f"{rel}: exact match (line endings normalized)"))
        if "lines" in rules:
            n = len(text.splitlines())
            out.append((n == rules["lines"],
                        f"{rel}: {n} lines (want {rules['lines']})"))
        if "contains" in rules and rules["contains"] not in text:
            out.append((False, f"{rel}: does not contain {rules['contains']!r}"))
        if "not_contains" in rules and rules["not_contains"] in text:
            out.append((False, f"{rel}: still contains {rules['not_contains']!r}"))
        if rules.get("json") or "json_paths" in rules:
            try:
                doc = json.loads(text)
            except Exception as e:
                out.append((False, f"{rel}: not valid JSON ({e})"))
                continue
            out.append((True, f"{rel}: valid JSON"))
            for dotted, want in (rules.get("json_paths") or {}).items():
                try:
                    got = _get_path(doc, dotted)
                except Exception:
                    out.append((False, f"{rel}: {dotted} missing"))
                    continue
                out.append((got == want, f"{rel}: {dotted}={got!r} "
                                         f"(want {want!r})"))
    return out


def grade(task, metrics):
    """Returns (passed, [reasons]). Every rule contributes a reason, so a failing
    task says which rule broke, not just 'failed'."""
    spec = task.get("check") or {}
    raw_answer = metrics.get("answer") or ""
    # Markdown emphasis is noise for phrase matching: a correct "does **not** exist"
    # failed an earlier version of the T10 spec because of the asterisks, which is a
    # grader bug, not a model failure. Regexes still run against the raw text.
    answer = re.sub(r"\s+", " ", re.sub(r"[*_`]+", "", raw_answer)).lower()
    reasons = []

    reasons.extend(check_files(Path(metrics["_workdir"]), spec.get("files")))

    if spec.get("answer_contains"):
        missing = [s for s in spec["answer_contains"] if s.lower() not in answer]
        reasons.append((not missing,
                        "answer contains all required terms"
                        if not missing else f"answer missing {missing}"))
    if spec.get("answer_contains_any"):
        hit = [s for s in spec["answer_contains_any"] if s.lower() in answer]
        reasons.append((bool(hit), f"answer matched {hit}" if hit
                                   else f"answer matched none of "
                                        f"{spec['answer_contains_any']}"))
    if spec.get("answer_not_contains"):
        bad = [s for s in spec["answer_not_contains"] if s.lower() in answer]
        reasons.append((not bad, "no forbidden terms" if not bad
                                 else f"answer contains forbidden {bad}"))
    if spec.get("answer_regex"):
        import re as _re
        hit = _re.search(spec["answer_regex"], raw_answer)
        reasons.append((bool(hit), "answer matched regex" if hit
                                   else f"answer did not match "
                                        f"{spec['answer_regex']!r}"))
    if spec.get("field_notes_min") is not None:
        n = metrics.get("field_notes_fired", 0)
        reasons.append((n >= spec["field_notes_min"],
                        f"field notes fired {n} (min {spec['field_notes_min']})"))
    if spec.get("digests_min") is not None:
        n = metrics.get("digests_fired", 0)
        reasons.append((n >= spec["digests_min"],
                        f"digests fired {n} (min {spec['digests_min']})"))
    if spec.get("verifies_min") is not None:
        n = metrics.get("verifies_fired", 0)
        reasons.append((n >= spec["verifies_min"],
                        f"write verifications fired {n} (min {spec['verifies_min']})"))
    if spec.get("verify_failures_min") is not None:
        n = metrics.get("verify_failures", 0)
        reasons.append((n >= spec["verify_failures_min"],
                        f"verify failures reported {n} "
                        f"(min {spec['verify_failures_min']})"))
    if spec.get("no_create_tool"):
        n = metrics.get("create_tool_calls", 0)
        reasons.append((n == 0, f"create_tool calls {n} (want 0)"))
    if "tool_calls_max" in spec:
        n = metrics.get("tool_calls", 0)
        reasons.append((n <= spec["tool_calls_max"],
                        f"tool calls {n} (max {spec['tool_calls_max']})"))
    if "tool_calls_min" in spec:
        n = metrics.get("tool_calls", 0)
        reasons.append((n >= spec["tool_calls_min"],
                        f"tool calls {n} (min {spec['tool_calls_min']})"))
    if spec.get("status_in"):
        st = metrics.get("status", "")
        reasons.append((st in spec["status_in"],
                        f"status {st!r} (want {spec['status_in']})"))
    if not reasons:
        reasons.append((False, "task has no machine check"))
    return all(ok for ok, _ in reasons), [f"{'ok ' if ok else 'FAIL'} {why}"
                                          for ok, why in reasons]


# --------------------------------------------------------------------------
# Instrumentation beyond run_scenario's
# --------------------------------------------------------------------------

def add_error_metrics(fb, metrics):
    """Classify what came back from tools. run_scenario counts calls and
    duplicates; the categories that matter for scaffolding are the failures:
    a tool that does not exist, args that do not parse, a command that exits
    non-zero, and a blocked/declined call."""
    inner = fb.Agent._exec_tool
    metrics.setdefault("tool_errors", [])
    metrics.setdefault("tool_seconds", {})

    def exec_tool(self, tool_call, ctx):
        t0 = time.time()
        name, args, out = inner(self, tool_call, ctx)
        text = out if isinstance(out, str) else str(out)
        if "unknown tool '" in text:
            metrics["unknown_tool"] += 1
        if "invalid JSON arguments" in text:
            metrics["arg_parse_fail"] += 1
        if text.startswith(("ERROR", "BLOCKED", "DECLINED", "TIMEOUT")) or \
                "--- stderr ---" in text:
            metrics["tool_errors"].append({"tool": name, "out": text[:200]})
        if text.startswith("exit_code=") and not text.startswith("exit_code=0"):
            metrics["cmd_nonzero"] += 1
        if name in ("write_file", "edit_file", "create_tool"):
            metrics["mutating_calls"] += 1
        if name == "create_tool":
            metrics["create_tool_calls"] += 1
        if "[HARNESS: digested" in text:
            metrics["digests_fired"] += 1
            m = re.search(r"digested `([^`]+)`", text)
            if m:
                metrics.setdefault("digest_shapes", []).append(m.group(1))
        if "[HARNESS field note" in text:
            metrics["field_notes_fired"] += 1
        if "[HARNESS verify" in text:
            metrics["verifies_fired"] += 1
            if "verify FAILED" in text:
                metrics["verify_failures"] += 1
        if name == "plan":
            metrics["plan_calls"] += 1
        per = metrics["tool_seconds"].setdefault(name, [0, 0.0])
        per[0] += 1
        per[1] += time.time() - t0
        return name, args, out

    fb.Agent._exec_tool = exec_tool
    for key in ("unknown_tool", "arg_parse_fail", "cmd_nonzero", "mutating_calls",
                "create_tool_calls", "digests_fired", "field_notes_fired",
                "verifies_fired", "verify_failures", "plan_calls"):
        metrics.setdefault(key, 0)


def stage_task(workdir, budget, task, overrides=None):
    """stage_install plus this task's fixtures and config overrides."""
    cfg = run_scenario.stage_install(workdir, budget)
    # Data assets the build ships next to the code. Without these the staged install
    # is not the deployed install, and a feature that reads them looks broken: the
    # first T14 run reported "field notes fired 0" purely because field-notes.md was
    # never copied into the sandbox.
    for asset in ("field-notes.md",):
        src = BASE / asset
        if src.exists():
            shutil.copy2(src, workdir / asset)
    for rel, content in (task.get("setup") or {}).items():
        path = workdir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    cfg["agent"].update(DEFAULT_CONFIG)
    cfg["agent"].update(task.get("config") or {})
    cfg["agent"].update(overrides or {})
    cfg["llm"]["max_context_tokens"] = int(budget)
    (workdir / "config.json").write_text(json.dumps(cfg, indent=2),
                                         encoding="utf-8")
    return cfg


def run_task(task, budget, label, artifacts_dir=None, overrides=None):
    workdir = Path(tempfile.mkdtemp(prefix=f"fbeval-{task['id']}-"))
    metrics = {"task": task["id"], "category": task["category"],
               "difficulty": task["difficulty"], "label": label, "budget": int(budget),
               "tool_calls": 0, "tool_names": [], "compactions": 0,
               "blocks_dropped": 0, "duplicates_blocked": 0, "pairing_repairs": 0,
               "pairing_problems_seen": 0, "narration": 0, "progress_lines": 0,
               "_workdir": str(workdir)}
    cwd = os.getcwd()
    try:
        stage_task(workdir, budget, task, overrides)
        fb = run_scenario.load(workdir)
        run_scenario.instrument(fb, metrics)
        add_error_metrics(fb, metrics)

        narration = []
        session = f"eval-{task['id']}-{int(time.time())}"
        t0 = time.time()
        os.chdir(workdir)
        try:
            answer = fb.AGENT.run(
                session, task["prompt"],
                interim_cb=lambda text: (metrics.__setitem__(
                    "narration", metrics["narration"] + 1), narration.append(text)),
                progress_done_cb=lambda *a: metrics.__setitem__(
                    "progress_lines", metrics["progress_lines"] + 1),
            )
        finally:
            os.chdir(cwd)
        metrics["wall_s"] = round(time.time() - t0, 1)

        usage = (fb.AGENT.last_usage.get(session)
                 or fb.AGENT.live_usage.get(session) or {})
        # What the harness held for this run: plan size and whether it had to nudge.
        _st = fb.run_state(session)
        metrics["plan_steps"] = len(_st["plan"]) if _st else 0
        metrics["plan_done"] = sum(1 for s in (_st["plan"] if _st else [])
                                   if s["status"] == "done")
        metrics["plan_nudges"] = _st["nudges"] if _st else 0
        # Was the atlas actually in play for this task? The staged install generates its own
        # draft on the first run, so a non-zero size here is the proof the ON leg was live.
        _atlas = Path(workdir) / "atlas.md"
        try:
            metrics["atlas_chars"] = _atlas.stat().st_size if _atlas.exists() else 0
        except OSError:
            metrics["atlas_chars"] = 0
        metrics.update({
            "status": usage.get("status", ""),
            "llm_calls": usage.get("calls", 0),
            "prompt_tokens": usage.get("prompt", 0),
            "completion_tokens": usage.get("completion", 0),
            "llm_secs": round(usage.get("llm_secs", 0.0), 1),
            "mutations": usage.get("mutations", 0),
            "duplicates_labelled": usage.get("duplicates_labelled", 0),
            "context_retries": usage.get("context_retries", 0),
            "steps_seen": sum(metrics["tool_seconds"][k][0]
                              for k in metrics["tool_seconds"]),
            "distinct_tools": sorted(metrics["tool_seconds"]),
            "answer": answer or "",
            "answer_chars": len(answer or ""),
            "answer_sha": hashlib.sha256((answer or "").encode()).hexdigest()[:12],
            "answer_head": (answer or "").strip().splitlines()[0][:110] if answer else "",
            "narration_head": narration[0][:90] if narration else "",
        })
        passed, why = grade(task, metrics)
        metrics["pass"] = passed
        metrics["why"] = why
        # Keep the sandbox's own record: the staged build writes its log inside the
        # temp dir, which is deleted on the way out. Without this copy, a claim in the
        # final answer about what the model did ("the tool did not exist on first
        # call") cannot be checked, and process honesty is exactly what several
        # categories are meant to measure.
        if artifacts_dir:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            for name in ("tinycmdr.log", "notes.md", "tasks.json", "state.json"):
                src = workdir / name
                if src.exists():
                    shutil.copy2(src, artifacts_dir / f"{task['id']}.{name}")
            made = workdir / "tools"
            if made.is_dir():
                listing = sorted(p.name for p in made.iterdir())
                (artifacts_dir / f"{task['id']}.tools.txt").write_text(
                    "\n".join(listing) + "\n", encoding="utf-8")
        return metrics
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def summarize(all_metrics):
    by_cat = {}
    for m in all_metrics:
        c = by_cat.setdefault(m["category"], {"pass": 0, "n": 0, "steps": 0,
                                              "wall": 0.0, "tools": 0,
                                              "errors": 0})
        c["n"] += 1
        c["pass"] += 1 if m["pass"] else 0
        c["steps"] += m.get("steps_seen", 0)
        c["wall"] += m.get("wall_s", 0)
        c["tools"] += m.get("tool_calls", 0)
        c["errors"] += len(m.get("tool_errors") or [])
    lines = []
    done = sum(1 for m in all_metrics if m["pass"])
    lines.append(f"SCORE {done}/{len(all_metrics)} passed")
    for cat in sorted(by_cat):
        c = by_cat[cat]
        lines.append(f"  {cat:<18} {c['pass']}/{c['n']} pass   "
                     f"steps {c['steps']}  tools {c['tools']}  "
                     f"tool_errors {c['errors']}  wall {round(c['wall'], 1)}s")
    totals = {
        "tasks": len(all_metrics),
        "passed": done,
        "llm_calls": sum(m.get("llm_calls", 0) for m in all_metrics),
        "prompt_tokens": sum(m.get("prompt_tokens", 0) for m in all_metrics),
        "completion_tokens": sum(m.get("completion_tokens", 0) for m in all_metrics),
        "tool_calls": sum(m.get("tool_calls", 0) for m in all_metrics),
        "tool_errors": sum(len(m.get("tool_errors") or []) for m in all_metrics),
        "cmd_nonzero": sum(m.get("cmd_nonzero", 0) for m in all_metrics),
        "unknown_tool": sum(m.get("unknown_tool", 0) for m in all_metrics),
        "arg_parse_fail": sum(m.get("arg_parse_fail", 0) for m in all_metrics),
        "duplicates_blocked": sum(m.get("duplicates_blocked", 0) for m in all_metrics),
        "digests_fired": sum(m.get("digests_fired", 0) for m in all_metrics),
        "field_notes_fired": sum(m.get("field_notes_fired", 0) for m in all_metrics),
        "verifies_fired": sum(m.get("verifies_fired", 0) for m in all_metrics),
        "verify_failures": sum(m.get("verify_failures", 0) for m in all_metrics),
        "wall_s": round(sum(m.get("wall_s", 0) for m in all_metrics), 1),
    }
    lines.append("TOTALS " + json.dumps(totals))
    return lines, totals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="*", help="task ids (default: --all)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--budget", default=os.environ.get("TINYCMDR_TEST_BUDGET", "24000"))
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--out", default="")
    # Feature flags for measurement: --config digest_enabled=false runs the SAME
    # build with the feature switched off, which isolates the feature instead of
    # comparing two different files.
    ap.add_argument("--config", action="append", default=[],
                    metavar="KEY=VALUE")
    args = ap.parse_args()

    overrides = {}
    for item in args.config:
        if "=" not in item:
            sys.exit(f"--config needs KEY=VALUE, got {item!r}")
        key, _, raw = item.partition("=")
        try:
            overrides[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            overrides[key.strip()] = raw

    if args.list:
        for t in eval_tasks.TASKS:
            print(f"{t['id']:<22} {t['category']:<18} {t['difficulty']}")
        return

    ids = [t["id"] for t in eval_tasks.TASKS] if (args.all or not args.tasks) \
        else args.tasks
    unknown = [i for i in ids if i not in eval_tasks.BY_ID]
    if unknown:
        sys.exit(f"unknown task id(s): {unknown}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else \
        RUNS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.label}.jsonl"
    artifacts = RUNS_DIR / f"{out.stem}-artifacts"

    results = []
    with out.open("w", encoding="utf-8") as fh:
        for tid in ids:
            task = eval_tasks.BY_ID[tid]
            print(f"--- {tid} ({task['category']}) ...", flush=True)
            m = run_task(task, args.budget, args.label, artifacts, overrides)
            results.append(m)
            fh.write(json.dumps(m) + "\n")
            fh.flush()
            verdict = "PASS" if m["pass"] else "FAIL"
            print(f"    {verdict}  steps={m.get('steps_seen')} "
                  f"tools={m.get('tool_calls')} wall={m.get('wall_s')}s "
                  f"status={m.get('status')} digests={m.get('digests_fired')} "
                  f"notes={m.get('field_notes_fired')}", flush=True)
            for why in m["why"]:
                print(f"      {why}", flush=True)
            if not m["pass"]:
                print(f"      answer head: {m.get('answer_head')!r}", flush=True)

    lines, totals = summarize(results)
    print()
    for line in lines:
        print(line)
    print(f"\njsonl: {out}")


if __name__ == "__main__":
    main()
