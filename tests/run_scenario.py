"""Scenario harness for tinycmdr: run the agent loop in CLI mode and print metrics.

Stage 0 of the changes plan: this is the test bed's measuring stick, not a feature.
It stages a temp install (a copy of tinycmdr.py plus a generated config.json), imports
it exactly like the suites do, runs a fixed task through AGENT.run() with no
Mattermost connection, and prints one JSON line per run.

Why CLI mode: the live bots are the thing we are trying to protect. Anything risky
gets measured here first, and only then goes near the manager box tinycmdr.

Endpoints come from the environment so this file stays publishable (no LAN addresses):
    TINYCMDR_TEST_BASE_URL   default http://127.0.0.1:8081/v1
    TINYCMDR_TEST_MODEL      default main
    TINYCMDR_TEST_BUDGET     llm.max_context_tokens for the staged config, default 24000

Usage:
    python tests/run_scenario.py short
    python tests/run_scenario.py long --budget 12000 --label baseline
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

SCENARIOS = {
    # A small task: a write, a read-back, a report. Cheap, exercises the normal loop.
    "short": ("Do all of this, then report:\n"
              "1. write a file named probe.txt in the working directory containing the "
              "numbers 1 to 50, one per line\n"
              "2. read it back and count the lines\n"
              "3. report the count and the first line, nothing else"),
    # A long task: three chunked reads of a big file, enough to cross a deliberately
    # small budget. Measured 2026-09-11: a single-request task does NOT block-compact at
    # all (`_drop_oldest_block` cuts on user-message boundaries and one request has none),
    # so this scenario exercises the tool-output shrink path and the force-shrink guard,
    # while "session" below is the one that reaches block compaction.
    "long": ("Read this file in chunks and then report:\n"
             "1. read_file on tinycmdr.py with limit 300, three times, at offsets 1, 1200 "
             "and 2400\n"
             "2. write a file named lines.txt containing just the total number of lines "
             "you read\n"
             "3. read lines.txt back\n"
             "4. report the number of lines and the name of the function defined closest "
             "to offset 1200"),
    # Multi-message session: three requests in ONE session, each reading a BIG chunk.
    # Both ingredients are needed to reach block compaction: `_drop_oldest_block` cuts on
    # user-message boundaries (measured: a single-request task with 15 tool calls produced
    # zero block drops), and the payload must actually exceed the budget, which needs
    # large tool results. It also exposes the design fact that matters for item 2:
    # history persists user/assistant text ONLY, so earlier turns' tool results are not in
    # the payload at all (measured: hist_roles {'user': 3, 'assistant': 3},
    # hist_has_tool_messages False).
    "session": ("turn1: read tinycmdr.py with limit 300 at offset 1, then report the name "
                "of the first top-level constant you see\n"
                "turn2: read tinycmdr.py with limit 300 at offset 1200, then report the "
                "name of the first function you see\n"
                "turn3: read tinycmdr.py with limit 300 at offset 2400, then report the "
                "name of the first class you see"),
    # Cross-turn RECALL: a regression check for compaction. Turn 1 puts a code word into the
    # transcript, turn 2 forces compaction with a big read, then the file is DELETED so
    # re-reading cannot substitute for memory, and turn 3 reads a big chunk first (a drop is
    # per-payload, so the budget must be crossed in the answering turn for the elision marker
    # to be in the prompt that answers) before asking for the code word.
    # Measured behaviour: at a small budget the old build answers NOT FOUND exactly as it
    # should, because the dropped exchange is genuinely gone from that payload.
    "recall": ("turn1: read recall.txt and report the code word it contains, then the file "
               "is deleted\n"
               "turn2: read tinycmdr.py with limit 300 at offset 1 and report the first "
               "top-level constant\n"
               "turn3: read tinycmdr.py with limit 300 at offset 1200, then without any "
               "further tool calls say what the code word was"),
}

RECALL_CODEWORD = "FALCON-7"
RECALL_TURNS = [
    "read the file recall.txt and report the code word it contains, and nothing else",
    "read tinycmdr.py with limit 300 at offset 1, then report the name of the first "
    "top-level constant you see",
    "read tinycmdr.py with limit 300 at offset 1200, then do not call any more tools: from "
    "the earlier part of this conversation, what was the code word in recall.txt? If you "
    "cannot find it in this conversation, say NOT FOUND. Do not guess.",
]


SESSION_TURNS = [
    "read tinycmdr.py with limit 300 at offset 1, then report the name of the first "
    "top-level constant you see",
    "read tinycmdr.py with limit 300 at offset 1200, then report the name of the first "
    "function you see",
    "read tinycmdr.py with limit 300 at offset 2400, then report the name of the first "
    "class you see",
]


def stage_install(workdir, budget):
    """Copy the app and write a config beside it, the way the suites do."""
    # TINYCMDR_TEST_APP lets the same scenario run against an OLD build (a rollback copy
    # in .archive/) so a change can be measured before/after without editing the tree.
    app = Path(os.environ.get("TINYCMDR_TEST_APP") or (BASE / "tinycmdr.py"))
    if not app.is_absolute():
        app = BASE / app
    shutil.copy2(app, workdir / "tinycmdr.py")
    fixture = json.loads((BASE / "tests" / "fixture-config.json").read_text(encoding="utf-8-sig"))
    cfg = {k: v for k, v in fixture.items() if not k.startswith("_")}
    cfg["llm"]["base_url"] = os.environ.get("TINYCMDR_TEST_BASE_URL",
                                           "http://127.0.0.1:8081/v1")
    cfg["llm"]["model"] = os.environ.get("TINYCMDR_TEST_MODEL", "main")
    # Cloud endpoints need a key. Kept out of this file on purpose: pass it in.
    cfg["llm"]["api_key"] = os.environ.get("TINYCMDR_TEST_API_KEY",
                                          cfg["llm"].get("api_key") or "none")
    cfg["llm"]["max_context_tokens"] = int(budget)
    cfg["web"] = {"enabled": False}
    cfg["search"] = {"anysearch_api_key": "", "tavily_api_key": "", "max_results": 5}
    (workdir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def load(workdir):
    spec = importlib.util.spec_from_file_location("fb_scenario", workdir / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["fb_scenario"] = fb
    spec.loader.exec_module(fb)
    return fb


def instrument(fb, metrics):
    """Count what the plan asks for without touching the app's behaviour."""
    real_exec = fb.Agent._exec_tool
    real_drop = fb.Agent._drop_oldest_block
    real_compact = fb.Agent._compact
    real_chat = fb.Agent._chat
    real_repair = fb._repair_tool_pairing
    real_problems = fb._tool_pairing_problems

    def chat(self, messages, *a, **kw):
        # Record what each call was given, per call: this is what makes "did the model have
        # X at the call that answered" answerable at all, instead of inferred from counts.
        blob = "\n".join(str(m.get("content") or "") for m in messages)
        metrics["payloads"] = metrics.get("payloads", 0) + 1
        tl = metrics.setdefault("timeline", [])
        if len(tl) < 60:
            tl.append({"ev": "call", "n": metrics["payloads"], "msgs": len(messages),
                       "est": self._messages_token_est(messages),
                       "codeword": RECALL_CODEWORD in blob,
                       "marker": any(str(m.get("content") or "").startswith(fb.ELISION_MARKERS)
                                     for m in messages)})
        return real_chat(self, messages, *a, **kw)

    def compact(self, messages):
        # Record the decision point: an over-budget turn that only shrinks tool output
        # never reaches a block drop, and "compactions: 0" alone cannot tell those apart.
        est = self._messages_token_est(messages)
        budget = self._context_budget() - fb.est_tokens(fb.volatile_context())
        n_before = len(messages)
        out = real_compact(self, messages)
        metrics["compact_calls"] = metrics.get("compact_calls", 0) + 1
        metrics.setdefault("compact_probes", [])
        if len(metrics["compact_probes"]) < 80:
            metrics["compact_probes"].append({"n": n_before, "est": est,
                                              "budget": budget,
                                              "over": est > budget,
                                              "shrunk_msgs": n_before - len(out)})
        return out

    def exec_tool(self, tool_call, ctx):
        out = real_exec(self, tool_call, ctx)
        name = (tool_call.get("function") or {}).get("name", "?")
        metrics["tool_calls"] += 1
        metrics["tool_names"].append(name)
        if out[2].startswith("NOT RE-EXECUTED"):
            metrics["duplicates_blocked"] += 1
        return out

    def drop(self, messages, marker):
        before = list(messages)
        ok = real_drop(self, messages, marker)
        if ok:
            metrics["compactions"] += 1
            metrics["blocks_dropped"] += len(before) - len(messages)
            # What a dropped block is actually made of: this is the material a
            # deterministic summary would have to work with (stage 1's design input).
            kept = {id(m) for m in messages}
            dropped = [m for m in before if id(m) not in kept]
            metrics.setdefault("dropped_blocks", [])
            tl = metrics.setdefault("timeline", [])
            if len(tl) < 60:
                tl.append({"ev": "drop", "n": len(dropped),
                           "roles": [m.get("role") for m in dropped][:6]})
            if len(metrics["dropped_blocks"]) < 20:
                metrics["dropped_blocks"].append({
                    "n_messages": len(dropped),
                    "roles": [m.get("role") for m in dropped],
                    "user_text": [str(m.get("content") or "")[:100] for m in dropped
                                  if m.get("role") == "user"],
                    "assistant_text": [str(m.get("content") or "")[:100] for m in dropped
                                       if m.get("role") == "assistant"],
                    "tool_names": [((tc.get("function") or {}).get("name"))
                                   for m in dropped if m.get("role") == "assistant"
                                   for tc in (m.get("tool_calls") or [])],
                })
        return ok

    def repair(messages):
        problems = real_problems(messages)
        out = real_repair(messages)
        if problems:
            metrics["pairing_repairs"] += 1
            metrics["pairing_problems_seen"] += len(problems)
        return out

    fb.Agent._exec_tool = exec_tool
    fb.Agent._compact = compact
    fb.Agent._chat = chat
    fb.Agent._drop_oldest_block = drop
    fb._repair_tool_pairing = repair
    # The two numbers that decide whether any of this fires at all.
    metrics["tool_output_max_chars"] = fb.CONFIG["agent"].get("tool_output_max_chars")
    metrics["max_context_tokens"] = fb.CONFIG["llm"].get("max_context_tokens")


def run_one(name, budget, label):
    workdir = Path(tempfile.mkdtemp(prefix=f"fbsim-{name}-"))
    metrics = {"scenario": name, "label": label, "tool_calls": 0,
               "tool_names": [], "compactions": 0, "blocks_dropped": 0,
               "duplicates_blocked": 0, "pairing_repairs": 0,
               "pairing_problems_seen": 0, "narration": 0, "progress_lines": 0}
    try:
        stage_install(workdir, budget)
        fb = load(workdir)
        instrument(fb, metrics)

        narration = []
        session = f"scenario-{name}-{int(time.time())}"
        t0 = time.time()
        answer = fb.AGENT.run(
            session, SCENARIOS[name],
            interim_cb=lambda text: (metrics.__setitem__("narration", metrics["narration"] + 1),
                                     narration.append(text)),
            progress_done_cb=lambda *a: metrics.__setitem__(
                "progress_lines", metrics["progress_lines"] + 1),
        )
        metrics["wall_s"] = round(time.time() - t0, 1)
        metrics["budget"] = int(budget)
        if name == "long" and not metrics["compactions"]:
            # A stage comparison is worthless if the thing under test never fired.
            metrics["note"] = ("no compaction: this run stayed under the budget, so it "
                               "cannot show a compaction effect. Re-run with a smaller "
                               "--budget.")
        # run() moves the accumulator to last_usage when it finishes (it also pops
        # live_usage), and adds secs/mutations/status on the way out.
        usage = (fb.AGENT.last_usage.get(session)
                 or fb.AGENT.live_usage.get(session) or {})
        metrics.update({
            "llm_calls": usage.get("calls", 0),
            "prompt_tokens": usage.get("prompt", 0),
            "completion_tokens": usage.get("completion", 0),
            "llm_secs": round(usage.get("llm_secs", 0.0), 1),
            "run_status": usage.get("status", ""),
            "mutations": usage.get("mutations", 0),
            "duplicates_labelled": usage.get("duplicates_labelled", 0),
            "escalated": bool(usage.get("escalated")),
            "context_retries": usage.get("context_retries", 0),
            "answer_chars": len(answer or ""),
            "answer_sha": hashlib.sha256((answer or "").encode()).hexdigest()[:12],
            "answer_head": (answer or "").strip().splitlines()[0][:110] if answer else "",
            "narration_head": narration[0][:90] if narration else "",
        })
        return metrics
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_session(budget, label, scenario="session"):
    """Three requests in one session, with per-turn metrics and the history's shape."""
    import collections
    turns = RECALL_TURNS if scenario == "recall" else SESSION_TURNS
    workdir = Path(tempfile.mkdtemp(prefix=f"fbsim-{scenario}-"))
    metrics = {"scenario": scenario, "label": label, "tool_calls": 0, "tool_names": [],
               "compactions": 0, "blocks_dropped": 0, "duplicates_blocked": 0,
               "pairing_repairs": 0, "pairing_problems_seen": 0, "narration": 0,
               "progress_lines": 0, "budget": int(budget)}
    try:
        stage_install(workdir, budget)
        if scenario == "recall":
            (workdir / "recall.txt").write_text(
                f"The code word is {RECALL_CODEWORD}.\n", encoding="utf-8")
        fb = load(workdir)
        instrument(fb, metrics)
        session = f"scenario-{scenario}-{int(time.time())}"
        per_turn, t0 = [], time.time()
        for i, turn in enumerate(turns, 1):
            before_calls, before_comp = metrics["tool_calls"], metrics["compactions"]
            t_turn = time.time()
            answer = fb.AGENT.run(session, turn)
            u = fb.AGENT.last_usage.get(session) or {}
            per_turn.append({
                "turn": i,
                "wall_s": round(time.time() - t_turn, 1),
                "llm_calls": u.get("calls", 0),
                "prompt_tokens": u.get("prompt", 0),
                "completion_tokens": u.get("completion", 0),
                "tool_calls": metrics["tool_calls"] - before_calls,
                "compactions": metrics["compactions"] - before_comp,
                "answer": (answer or "").strip()[:600],
                "answer_head": (answer or "").strip().splitlines()[0][:80] if answer else "",
            })
            if scenario == "recall" and i == 1:
                # Delete it so the only remaining source is the conversation itself. Without
                # this the model just re-reads the file and the test measures nothing.
                (workdir / "recall.txt").unlink(missing_ok=True)
        metrics["wall_s"] = round(time.time() - t0, 1)
        metrics["turns"] = len(per_turn)
        metrics["per_turn"] = per_turn
        if scenario == "recall":
            last = per_turn[-1]
            metrics["recall_codeword_correct"] = (
                RECALL_CODEWORD.lower() in str(last.get("answer") or "").lower())
            metrics["recall_final_tool_calls"] = last["tool_calls"]
        hist = fb.AGENT._history(session)
        metrics["hist_roles"] = dict(collections.Counter(m.get("role") for m in hist))
        metrics["hist_has_tool_messages"] = any(m.get("role") == "tool" for m in hist)
        metrics["hist_messages"] = len(hist)
        return metrics
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=sorted(SCENARIOS))
    ap.add_argument("--budget", default=os.environ.get("TINYCMDR_TEST_BUDGET", "24000"))
    ap.add_argument("--label", default="")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    for i in range(args.repeat):
        m = (run_session(args.budget, args.label, args.scenario)
             if args.scenario in ("session", "recall")
             else run_one(args.scenario, args.budget, args.label))
        print(json.dumps(m))


if __name__ == "__main__":
    main()
