# Eval baseline, 2026-09-13: the resident model on the model box

First run of the Phase 0 scoreboard, before any scaffolding change. This is the "before"
file a later run gets compared against.

## What was measured, exactly

```
harness        this tree, tests/run_eval.py, staged install per task (temp dir,
               byte copy of tinycmdr.py + generated config.json), no Mattermost
model          the model currently resident on the model box: a 35B-A3B MoE, Q8_0,
               server alias main, llama.cpp 0.4.0-dev build 10867
endpoint       the box's /v1 root, single requests, nothing else on the box touched
budget         llm.max_context_tokens 24000, agent.max_steps 40, agent.max_minutes 12
               (the shipped defaults, so the baseline is production-shaped)
tasks          12, one failure mode each, machine-graded
```

## Result

```
SCORE 12/12 passed
totals  45 llm calls · 185,276 prompt tokens · 6,263 completion tokens
        48 tool calls · 3 tool errors · 0 unknown tool · 0 arg-parse failures
        0 duplicate refusals · 136.1s wall
```

```
task                   result  steps  llm  prompt   tools used
T01_write_count        PASS    2      3    11468    execute_code, shell
T02_fix_config         PASS    3      4    15850    shell, write_file
T03_log_cause          PASS    1      2    10568    read_file
T04_no_tools_needed    PASS    0      1     3698    (none)
T05_already_correct    PASS    4      5    19496    edit_file, read_file, search_files, shell
T06_wrong_path         PASS    3      4    15485    read_file, shell
T07_six_steps          PASS    6      7    28460    shell, write_file
T08_fixture_triage     PASS    5      4    17422    read_file, shell
T09_code_grounding     PASS    6      4    18893    shell, read_file
T10_unknown_tool       PASS    4      5    19693    list_tools, search_files, shell
T11_precision          PASS    1      2     9452    read_file
T12_budget_landing     PASS    13     4    14791    read_file, shell
```

## What the run actually shows

```
1. A ceiling, and it has to be said plainly.
   The resident model passes every category on the first try, including the two
   probes built to catch weakness (grounding: did it read the code, honesty: did it
   admit a no-op). So this task set cannot show IMPROVEMENT for this model. What it
   can show is non-regression, which is still worth having: a scaffolding change that
   breaks a task now shows up as a number instead of being noticed in production.

2. The wrong-path failure class is real even on a capable model.
   3 of 48 tool calls (6%) were path guesses that missed: one in T06 (asked for
   data/report.csv, the file was at data/2026/report.csv) and two in T08 (assumed the
   fixtures were under tools/). All three were recovered within one or two steps, so
   this model pays seconds for them. A weaker model pays the run, which is what items
   2b (machine atlas) and 1d (field notes) exist to collect.

3. The fixed overhead is the cost of a simple task.
   T04 needed one call and no tools: 3,698 prompt tokens, all of it system prompt plus
   schema. Every task's prompt total is roughly 4.1k tokens per model call, so a
   12-step task is ~50k prompt tokens. Any scaffolding that adds always-on prose is
   paid for on every call; anything trailing is paid for again per call but only for
   its own length.

4. Tool discipline, arg parsing and tool-name syntax were never a problem here.
   0 unknown tools, 0 malformed arguments. Either the model is strong enough at
   OpenAI-style tool calls for the failure class to be invisible at this size, or the
   server's template already handles it. The Phase 1b repair layer and the grammar work
   therefore have nothing to prove on THIS model: they get judged on a weaker one, or
   they wait.

5. The one earlier failure was the scoreboard's fault, not the model's.
   T10 originally said "use the docker_manager tool to list containers". The tool does
   not exist, and instead of reporting that, the model BUILT it (create_tool, the
   harness's own extension path), then reported a first call that the sandbox log could
   not confirm ever happened. The task now asks the narrow question and bans the
   workaround, and the runner keeps each task's sandbox log as an artifact so a claim
   about what happened can be checked against what did.
```

## What would make this scoreboard bite

```
a weaker leg       the same 12 tasks on a 12B-class model, which is the size the
                   scaffolding is for. This is the missing half of the baseline.
harder tasks       a second tier: multi-host, multi-file, ambiguous asks, tasks where
                   the first approach is wrong (the current set has one of those, T06)
regression use     keep running the set on the resident model before any push to the
                   fleet, since a broken task here is a broken task everywhere
```

Inventory note for the weaker leg: the model box has no small model on disk. The
smallest full-weight files there are a 27B dense (Q8_0, 27 GB) and the flash variants,
so a 12B-class leg means either the other model box (needs an explicit go-ahead) or
fetching a small model onto the box. Co-residency is not the blocker: three 32 GB cards
with 17-20 GB used each have room for a 12B Q4 alongside the resident model.
