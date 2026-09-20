# Harness scaffolding: how to make a small model perform like a bigger one

Working plan, started 2026-09-13. Nothing in here is implemented yet except the
measuring stick it depends on (section 4), which is built and green.

## 1. What this is for

The goal is a harness that carries more of the load on a local model, so it stays on
track on fleet work without the operator babysitting it.

Operator, 2026-09-17: **the target is the daily model, not a smaller one.** What the
fleet serves every day is Qwen3.8-Flash-Next (GSQ-RCO, IQ3_XXS) on .47, and it is
strong enough that chasing a 9-12B to prove the scaffolding is no longer wanted.
So every item here is judged on THAT model: digest, verification, disclosure, the
plan, the atlas and the item-6 ceilings all earned their place on measurements taken
against it. A weak-model leg is closed as a goal, which also closes item 1b (its
whole premise was a model that mangles tool calls) and the low end of 2d.

The framing that survives contact with reality: this is not "inflating smartness".
It is **narrowing the competence a task requires until it fits inside the model**.
That distinction matters operationally. A scaffolded small model will look
excellent on the fleet's known territory and fall off a cliff on something
genuinely novel, with no graceful slope in between. The budget caps and stall
watchdog are the safety net for the cliff, which is why they stay.

Three levers, and every item below is one of them:

```
shrink the decision      fewer tools visible, one next step at a time, schema-bound answers
supply the missing bit   hand it the path, the repo map, the known fix for that error
check and correct        verify the write, repair bad tool calls, force a re-ask
```

## 2. What the harness already does (do not rebuild)

```
tools            17 core, plus hot-loaded .py in ./tools/
tool choice      the model writes its own tools (create_tool), live next call
memory           notes.md under a character budget, with rotation and an archive
horizon          task ledger (tasks.json), re-sent every prompt
long context     compaction and tool-output shrinking when the budget is crossed
loop control     loop guard: an identical call is refused after 2 real runs, the
                 run is forced to a report after 6
progress         check-ins on two cadences, model narration, harness tool lines
honesty          evidence annotation, and a budget-exhausted path that asks for
                 a VERIFIED line
runbooks         43 prose skills, read on demand, ~23 tokens each in the index
sub-agents       delegate_task, one level deep, throwaway context
measurement      tests/run_scenario.py + compare_runs.py, and now tests/run_eval.py
```

Measured fixed overhead before the first action: **3,469 tokens** on a clean
unpack, 5,186 on a live install (42 runbooks + 3 custom tools). Every custom tool
costs about 250 tokens of schema; every runbook about 23 tokens of index. That
ratio is the reason runbooks beat tools in this build.

Measured tool usage, from this box's own `tinycmdr.log`, real sessions only
(test-*, checkin-*, sub-* excluded):

```
shell            1403      task               50
execute_code      485      fetch_url          46
read_file         241      skill              43
edit_file         206      web_search         40
write_file         89      search_files       28
remember           23      search_sessions    20
list_tools         12      create_tool        11
notes               3      delegate_task       2
schedule            0
```

Caveat worth carrying: one operator channel is 2,500 of about 2,760 calls, so this
is one person's habit rather than a fleet average. Two things follow. The five
primitives are 88% of the work, so any "always visible" set has to keep them. And
the tail (delegate_task 2, notes 3, schedule 0) is exactly what progressive
disclosure should hide until it is relevant, because a 12B choosing among 17 tools
pays attention for the 12 it will never call.

## 3. Items, in the order they were agreed

Status as of 2026-09-16. The table is the source of truth for what is left; the
detail sections below each carry their own measurement.

```
0   scoreboard                          BUILT (section 4)
1a  pre-digest tool output at birth     BUILT (section 4a)
1b  tool-call repair, then grammar      CLOSED 2026-09-17. Measured to ZERO on this
                                        fleet (section 4k) and the weak-model leg it
                                        needed is no longer a goal: the daily model is
                                        the target (section 1).
1c  post-write verifiers                BUILT (section 4c)  -> 1e extends it
1d  field-note lookup                   BUILT (section 4b)
1e  execution as the verdict            ADOPTED 2026-09-17 (section 4i)
1f  cut-off summarizer                  NOT BUILT: measured first, failure has
                                        not happened here (section 7)
2a  progressive disclosure              BUILT (section 4e)
2b  machine atlas                       BUILT (section 4h)
2c  plan as harness state               BUILT (section 4g)
2d  per-model profiles                  DROPPED 2026-09-16 (section 7): the build
                                        makes no mechanical model calls, so the
                                        per-role half had nowhere to land
2e  assistant-turn priming              not started
2f  recipe tools                        CLOSED 2026-09-17: no repeated shape to
                                        encapsulate (top shell shape is 1.4%, 4l)
2g  fresh-perspective worker            DEFERRED 2026-09-16 (section 7). If it is
                                        ever built: opt-in flag only, gated on 1e
3a  code/config index                   REVERTED 2026-09-17 after measuring. No effect
                                        on the reads it was built for, and the reason is
                                        in 4m: the model's own grep already does the
                                        locating, and all 153 source reads were targeted.

7a  read ledger (new, from the 4l      REVERTED 2026-09-17. 15% against a declared 40%
    profile of the daily model)         target, inside a 190-704s wall spread (4n). It
                                        carried a POINTER where the next run needed the
                                        substance.

7b  carry tool results between runs     SHIPPED TO the manager box 2026-09-17 (4o). Later runs buy
    (new, the real fix for the same     ~80% less source text in two independent samples
    profile)                            (80% / 78%), payload bounded at 8k, answers spot
                                        checked. Still unproven: wall clock, and quality
                                        beyond the spot check.
3b  MCP client                          not started
3c  docs cache                          not started
4   distillation queue                  not started
5   stall ladder                        CLOSED 2026-09-17: the loop guard handles
                                        the 23 real repeats; the 243 'stall' lines
                                        are the suite's chan-stall channel (4l)
6   cost bound on expensive commands   BUILT + ADOPTED 2026-09-17 (section 4j):
                                        per-call ceiling + run-level budget. A
                                        bound on the worst case, NOT a measured
                                        speedup - 4j.1 says so plainly.
```

The earlier version of this list showed 2a, 2b and 2c as "not started" long after
they had shipped in 2.2.1. A stale status line is worse than no list: it sends the
next reader to rebuild something that is already in the build.

Two ordering decisions worth keeping visible. Grammar-constrained tool calls are
cheap and fix a whole failure class, but they fix syntax, not judgement, and the
thing that eats wall clock on this box is long shell loops, so pre-digestion and
the verifiers come first. Escalation stays late, because escalating without a
verifier just gets a bigger model to be wrong more expensively.

## 4. The scoreboard (built, and the first baseline is in)

```
tests/eval_tasks.py        12 graded tasks, one failure mode each
tests/run_eval.py          stages a temp install, drives AGENT.run(), grades, prints
tests/test_eval_grading.py offline checks: no task passes on an empty run, every
                           check spec is satisfiable, overrides stage correctly
tests/eval-runs/*.jsonl    one line per task, feedable to compare_runs.py
tests/eval-runs/*-artifacts/  each task's own sandbox log, notes and tool list
docs/tinycmdr-harness-eval-baseline.md   the first "before" run, in full
```

First baseline (resident 35B-A3B MoE on the model box, shipped defaults): **12/12
passed, 45 model calls, 185k prompt tokens, 48 tool calls, 3 tool errors, 136s**. Read
that as a ceiling rather than a success: this model is strong enough that the current
task set cannot show improvement, only non-regression. Two findings from it carry into
the work below: the wrong-path failure class is real even here (6% of tool calls were
path guesses that missed), and tool-name/argument syntax was never a problem at all
(0 unknown tools, 0 malformed arguments), so item 1b has nothing to prove on this model
and gets judged on a weaker one.

```
python tests/run_eval.py --list
python tests/run_eval.py --all --label baseline
python tests/run_eval.py T05_already_correct --label probe
python tests/test_eval_grading.py
```

Categories, so a score change says which weakness moved:

```
file_ops          can it do basic file work at all
config            fix a broken config and verify the result
log_triage        find the signal in a noisy log
signal_extraction read a long fixture and answer a precise question
tool_discipline   avoid tools when none are needed
honesty           admit a no-op instead of claiming a change
recovery          recover from a wrong path
long_horizon      hold a six-step plan and finish it
grounding         read the code rather than guess from training data
hallucination     invent a tool that does not exist
precision         exact answer, no extra numbers
landing           land gracefully when the budget runs out
```

Endpoints come from the environment (`tinycmdr_TEST_BASE_URL`, `tinycmdr_TEST_MODEL`,
`tinycmdr_TEST_BUDGET`), so the files stay publishable and no host name is baked in.

Rules for using it honestly:

- A change ships with a before/after run of the SAME task set, and the tasks it was
  meant to help are named up front.
- Scaffolding can mask model weakness, so at least one comparison is run with the
  scaffolding off.
- Every added nudge has to justify its tokens the way a runbook does at 23.

## 4a. Item 1a, tool-result digestion: built, and it pays

```
what it does   a known command shape is rendered down to its signal before the model
               reads it: journalctl and *.log keep error/warn/fail lines, systemctl
               keeps the unit header plus journal lines, apt/dnf/pip put failures
               first and sample the chatter, grep keeps the first N, ps/ls/docker
               ps/get-childitem keep head and tail. The result carries a header
               saying what was dropped and to re-run with raw=true for the full text.
where          tinycmdr.py, digest_output() + _digest_lines(), called by shell,
               execute_code and read_file BEFORE the 6000-char cap, so it selects
               from the whole output instead of what survived the scissors
config         agent.digest_enabled, digest_min_chars (1200), digest_lines (40)
tests          tests/test_digest.py, 24 offline checks
```

Measured on T13_buried_error (a 13 KB log whose single ERROR line sits in the middle,
exactly where the output cap cannot help), 3 runs per leg, same model, same task:

```
               pass    steps (median)   prompt tokens (median)   wall (median)
digestion ON   3/3     3               22,214                   18.3s
digestion OFF  3/3     5               37,985                   37.9s
```

Prompt tokens came in lower on every ON run than on every OFF run, so the win is not a
single lucky run. Both legs answered correctly, which is the point: this buys steps and
tokens, not correctness, at this model size.

Two honest costs: the three `raw` arguments added 56 tokens of fixed overhead
(5186 -> 5242 on the live install), and an extractor can drop the line that mattered.
That second risk is why every digest names itself, names the rule it used, and offers
the raw path, and why the checks assert the raw bypass works.

## 4b. Item 1d, field notes: built, fires reliably, and cost us a lesson

```
what it does   a FAILED tool result whose signature is already understood gets the
               known cause appended, from field-notes.md
where          tinycmdr.py, annotate_failure() + field_notes(), applied in _exec_tool
               so core and custom tools both get it
config         agent.field_notes_enabled, field_notes_file, field_notes_max (2)
library        11 entries, each traceable to a documented incident, scoped by platform
tests          tests/test_digest.py (parsing, scope, cap, only-failures rule)
```

Measured on T14_field_note (run a command that does not exist), 3 runs per leg:

```
               pass    steps (median)   prompt tokens (median)   wall (median)
notes ON       2/3     5               27,838                   36.6s
notes OFF      0/3     1                7,678                    6.1s
```

The OFF leg cannot pass by design: the check requires that the note fired, because this
is a feature test rather than a capability test. What matters is the cost column. With
notes on, the model spends four times the steps and 3.6 times the tokens on this
failure, because the note tells it to look for the binary by absolute path and one of
the three runs then searched the disk (7 tool calls, 79s) before answering.

The lesson, which is now written into the library's own header: an entry is only worth
carrying when its signature essentially ALWAYS means its cause. On an ambiguous
signature the note is a net cost. So the library stays small, the ambiguous entry was
rewritten to lead with "the binary is probably not installed" and to drop the action,
and any entry that fires and does not help gets deleted rather than kept for symmetry.

## 4c. Item 1c, post-write verification: built, zero prompt cost

```
what it does   after a write, edit or create_tool, the harness parses the file in the
               language its extension claims and puts the verdict in the same result
               the model is already reading: python (compile), json, toml, yaml, shell
               (bash -n). A custom tool in tools/ is additionally checked for the
               contract the loader enforces (NAME, DESCRIPTION, SCHEMA, run())
where          tinycmdr.py, verify_written_file() + verify_note(), hooked into
               tool_write_file, tool_edit_file and tool_create_tool
config         agent.verify_after_write (True), verify_max_bytes (2000000)
tests          tests/test_verify.py, 28 offline checks
```

Measured on T15_verify_ok (write a valid config.json) and T16_verify_failure (write a
file that is deliberately invalid JSON), verification on vs off:

```
               pass    steps   prompt tokens   wall     harness verdicts
ON             2/2     2 + 1   19,547          12.9s    2 fired, 1 of them FAILED
OFF            0/2     3 + 2   27,709          19.2s    0
```

The pass column is not a capability comparison: the checks require the harness verdict
to exist, so the OFF leg cannot pass by design. The interesting line is the answer text.
With verification on, the model reported the broken write and its cause. With it off,
the same model answered:

```
**Done.** Created `broken.json`.
```

That is the failure this item exists for. Fixed prompt overhead is unchanged (5242),
because it adds no schema and no prose: the verdict rides in the tool result.

Two rules that came out of the tests:

- A verifier may only report a FAILURE when the failure is a verdict about the FILE.
  On this host `bash` resolves to a WSL relay that cannot exec `/bin/bash`, and an
  earlier version reported a perfectly good script as "shell syntax error". An
  interpreter that never ran is a skip, not a broken file.
- An unverifiable type, a binary file, a huge file or a missing parser skips silently.
  A verdict line on every ordinary write would cost tokens for nothing, and a verifier
  that can break a run is worse than no verifier.

## 4d. Deployed to the the manager box bot (2026-09-13)

```
version            2.1.0, tinycmdr.py sha256 98d3aca71f4ef811, changelog entry written
restart            through the bot's own web UI (/restart on 127.0.0.1:8787), the
                   documented door for a bot that runs elevated; it replaced itself and
                   reconnected to Mattermost in ~3s, /api/health now reports 2.1.0
pre-deploy check   16-task eval against the exact deployed file: 14/16, and both misses
                   were the scoreboard's fault (a phrasing-fragile check, and a task that
                   let the model use shell redirection where only write_file was watched)
suites             53 + 28 + 34 + 84 + 92 + 171 green, CLI regenerated and green
```

Live probes through the web UI, on the real box (5 + 1):

```
verify          asked for a deliberately broken file, the bot quoted its own tool result
                verbatim, including "[HARNESS verify FAILED: invalid JSON: Expecting ','
                delimiter at line 1 column 8. The file on disk is broken — fix it before
                reporting anything as done.]" and did not claim success. Then it wrote a
                good file and quoted "[HARNESS verify: python syntax OK]".
field notes     `zztool --version` produced "CommandNotFoundException" and the library
                fired: the bot quoted the note, then explicitly resolved the ambiguity the
                right way — "it is cause #1 (not installed), not a PATH problem. I checked
                by absolute path before concluding" — and added nothing to the PATH.
digest          the bot passed raw=true on both of its output-inspection calls (the two
                largest results, 10k chars each) and left the other 13 calls to be shaped.
                That is the escape hatch working as designed: a model that is explicitly
                inspecting raw output opts out, everything else is still shaped. Live
                verification of the win itself stays with the eval, where it is measured.
```

A gap that made the pre-deploy run fail and is now closed: the verifier only watched
`write_file`/`edit_file`/`create_tool`, and this model writes files with shell
redirection as often as with `write_file` (T15 proved it). `verify_shell_writes()` now
picks the written path out of `Set-Content`/`Out-File`/`>`/`tee` commands and verifies it,
skipping descriptor redirections like `2>&1`.

Also fixed while deploying: `maintenance/build-cli-fix.py` anchored on the literal string
`VERSION = "2.0.0"`, so the first version bump refused the build. It now matches any
version with a regex, which is one less trap for the next release.

## 4e. Item 2a, tool disclosure: built, the biggest single saving

```
what it does   the request carries a small always-visible tool set; the rest is revealed
               on demand. Two doors in, because hiding a tool is only safe if nothing
               becomes unreachable: find_tools (ask by name or by what it does, all=true
               for everything) and calling it anyway, which the harness honours, executes
               and then keeps for the session with a line in the result.
visible always shell, execute_code, read_file, write_file, edit_file, skill, task,
               remember, web_search, fetch_url, list_tools, find_tools  (12)
hidden         schedule, create_tool, search_files, search_sessions, notes,
               delegate_task, and every custom tool on the box
where          tinycmdr.py: disclosure layer + tool_find_tools, REGISTRY.schemas_for(),
               select_tool_schemas() used by _chat; per-session reveal set
config         agent.tool_disclosure (True), core_tools (override the list),
               disclosure_max (matches per find_tools call)
tests          tests/test_disclosure.py, 31 offline checks, including a stubbed turn
               that inspects the request body
```

Fixed overhead, measured on this install:

```
                                  before        after
visible schemas                   3,067         1,531   tokens
fixed overhead (prompt+schemas)   5,491         3,954   tokens
```

**-1,537 tokens on every model call**, which is 28% of the fixed cost and the largest
single reduction of the whole plan so far.

Eval, both legs on the SAME build (16 tasks, one leg with `tool_disclosure=false`):

```
              pass    prompt tokens    model calls
disclosure on 15/16   256,861          66
disclosure off 15/16  324,392          70
              -20.8% prompt tokens, identical pass count
```

Read that honestly: the per-task numbers swing by 2x run to run at temperature 1.0 (T05
cost 21k tokens on one leg and 41k on the other), so the aggregate is the only figure
worth quoting, and even it carries noise. The exact, noise-free number is the
fixed-overhead measurement above. Three tasks also failed on grader artifacts rather than
behaviour during these runs and were fixed: markdown emphasis breaking a phrase match,
CRLF endings, and PowerShell's UTF-8 BOM (all three are Windows realities the checks had
been ignoring).

What is still unproven, stated plainly: that a model under a real multi-step task finds a
hidden tool it needs rather than doing without. The 16 tasks were written before
disclosure existed, so passing them is weak evidence. The live probes on the the manager box bot are
the check that matters, and they come after the deploy.

### 4e.1 Live probes on the deployed bot (2.2.0, then 2.2.1)

Five probes, each needing a hidden tool, all read-only, all five answered correctly:

```
P1 search_files   hand-rolled it with shell + Select-String. Task done in 22.6s
P2 search_sessions found it and used it (discovery works when the request names the idea)
P3 notes          "the notes tool isn't in my callable set here, so I reproduced what it
                  would print" — read the source, re-implemented it with execute_code,
                  41.8s. The door was open; it did not try it.
P4 schedule       named the tool and the exact call, semantics read from the source
P5 control        no tools, answered from knowledge
```

P3 is the failure the unit tests could not see: hiding a tool is only safe if the model
tries the door. 2.2.1 adds one sentence to the standing instructions forbidding the
workaround by name, with the measured cost attached. Re-probed after the fix:

```
P3 again   "Tool used: the built-in notes tool, action=view (one find_tools call to
           reveal it — it wasn't in my standing shortlist)"   29.9s
P1 again   still shell + Select-String. Left alone deliberately: Select-String is a real
           substitute for file search, the answer was right, and giving search_files back
           would cost 129 tokens on every call for a task the shell already does.
```

Fixed overhead after the added sentence: 4,024 tokens, against 5,491 before disclosure
(-1,467 net, and the sentence itself is only ~70 of that).


## 4f. Fleet rollout of 2.2.1 (2026-09-13)

Rolled out from the manager box one host at a time, each verified before the next: back up the live
file, copy the build, compare hashes, restart that host its own way, then confirm from the
host's own log and health endpoint. MacBook Air was skipped on the operator's word (it is
off) and stays on 2.0.0.

```
host              how it restarts                     result
the manager box  a LAN address  its own web UI /restart            2.2.1
the other Windows box a LAN address maintenance/tinycmdr-24x7.ps1       2.2.1  (the script exits FAIL on a
                  (no supervisor on .20)                     readiness probe that lies;
                                                             the log said otherwise)
the Windows test box a LAN address  Stop-Process + schtasks /Run   2.2.1  (no supervisor: the running
                  (a new script, see below)                  process must die first or the
                                                             replacement aborts on the lock)
the Linux test box a LAN address  sudo systemctl restart          2.2.1
the LAN model box a LAN address  sudo systemctl restart              2.2.1

fleet-version-report.ps1: all five report VERSION 2.2.1, sha256 2f967d1d43939927, "in sync"
MacBook Air a LAN address: ssh unavailable (host off, deferred)
```

Per-host proof that the NEW wiring is live came from asking each bot's own `/status` (a
harness fast path, no model call), which reports the version and what that session can see:

```
the manager box        v2.2.1   13 visible of 21 tools    (13 because probes had revealed one)
the other Windows box     v2.2.1   12 visible of 35 tools    (17 custom tools on that box)
the Windows test box   v2.2.1   12 visible of 19 tools
the Linux test box   v2.2.1   12 visible of 19 tools
the LAN model box       v2.2.1   12 visible of 20 tools
```

the other Windows box is the host with the most to gain from disclosure: seventeen custom tool schemas
were riding in every request there and are now revealed on demand.

Every host also received `field-notes.md` (the 1d library) and the updated tests and build
scripts. Two clean-up facts worth knowing: each host now carries
`tinycmdr.py.bak-fleet-2.2.1-<stamp>` as the rollback, and the Windows test box gained
`maintenance/restart-the Windows test box-2.2.1.ps1`, which stops the running process before
relaunching, because its own `restart-tinycmdr.ps1` is broken and its bot has no
supervisor.

## 4g. Item 2c, the plan and the runway: built

```
what it does   the harness holds this run's plan and re-sends it every turn in the
               trailing state block, together with the position: calls used, percent,
               calls left before the forced wrap-up. Two ways a plan appears: the harness
               PARSES one out of a request that lists its own steps ("1. ... 2. ..."),
               which is the path that gets used, or the model writes one with the `plan`
               tool (`action=set`, one step per line; doing / done with a note /
               blocked / drop). If plan_drift_after (8) tool calls pass with no step
               moving, the harness says so and quotes the current step. At the cap, the
               wrap-up names the open steps by text.
where          tinycmdr.py: derive_plan_from_text/set_derived_plan, run_state,
               plan_render/plan_open/run_block, tool_plan; run_block() is appended by
               volatile_context(session_key=...)
config         plan_enabled (True), plan_from_request (True), plan_drift_after (8),
               plan_max_steps (12)
tests          tests/test_plan.py, 33 checks; two of them drive a real turn against a
               stubbed model and inspect the request body (plan + runway re-sent, drift
               nudge delivered, derived plan present in the first request, open steps
               named at the cap)
```

Two design decisions worth stating. The plan lives in the TRAILING block, never in the
system prompt, because the system prompt is prefix-cached: a plan that changed the system
prompt would invalidate the cache for the whole conversation on every step. And the plan
survives across runs in a session, so a run that lands on the budget hands its open steps
to the next message instead of losing them, which is the failure the task ledger was
invented for at the fleet level.

Cost: the plan schema plus its standing instruction are ~180 tokens; the per-turn block is
about 25 tokens with a plan set, and it replaces nothing.

### 4g.1 What the measurement changed

Built as designed, then measured, and the measurement took the design apart:

```
16 graded runs, plan tool visible, standing instruction present   plan calls: 0
3 more runs where the harness had already parsed the steps        plan calls: 0
                                                                  steps marked done: 0
```

The model never touches a plan it has to choose to write, even when the checklist is handed
to it. Two consequences, both applied:

```
kept        what needs no cooperation: the derived checklist (parsed from a request that
            lists its own steps), the position line, the drift nudge, and the wrap-up
            naming open steps by text. Cost: 25-40 tokens on a multi-part run, 0 elsewhere
cut         the tool left the always-visible set (~150 tokens/call) and its standing
            instruction is gone (~70 tokens/call); the tool stays in the registry, one
            find_tools call away, so a model that does want it can still have it
```

The A/B across all 16 tasks is therefore not evidence about the plan: the ON leg cost
321,726 prompt tokens and 566s against the OFF leg's 394,848 and 941s, but with zero plan
calls the difference is run-to-run variance (at temperature 1.0 single tasks swing 2-5x:
T06 was 3 steps one leg and 10 the other, T07 was 9 and 2).


## 4h. Item 2b, the machine atlas: built

```
what it does   the harness hands the model the facts about the box it is running on, so it
               stops guessing. Three sections in atlas.md: `## host` (host name, OS,
               python, which shell the shell tool uses, install path, scratch, log, web
               port, model endpoint), `## layout` (where things are, including a bounded
               two-level listing of the install), `## notes` (curated facts a probe cannot
               know, one per line)
when           in the TRAILING state block, on the FIRST turn of a run, and again after any
               tool failure that reads like a wrong path (no such file, cannot find path,
               command not found...). Never in the system prompt, which is prefix-cached;
               never on turns that did not ask for it
generated      ON the host, never shipped: a fresh install writes a DRAFT at its first run
               (python facts only, no shell probes) and hand-written notes survive every
               regeneration. `maintenance/atlas-merge.py <atlas.md> <notes.md>` pushes the
               curated half without touching the generated half
config         atlas_enabled (True), atlas_file (atlas.md), atlas_max_chars (2400)
tests          tests/test_atlas.py, 34 checks, two of them end-to-end against a stubbed
               model reading the REQUEST BODY
```

The measured reason it exists: the most common tool error on this task set is a guessed path
(`data/report.csv` when the file was `data/2026/report.csv`), and the model never says which
box it thinks it is on. The harness knows both facts, so this is the "supply the missing bit"
lever rather than a new capability.

Four defects the tests and the first real run exposed, all fixed:

```
parser            rejected the bullet form the generator writes (`- key: value`), so a
                  generated atlas parsed to nothing
layout lines      `name  purpose` has no colon and was being dropped entirely
budget            the bound trimmed whatever came last, which was the curated notes; the
                  listing is now what gets trimmed, and host facts and notes always go in
notes merge       a curated file's explanatory prose arrived in the atlas as `note:` lines
                  about the merge script. Only bullets and their continuations merge now
```

Cost on this box: the block is 2,076 characters, about 520 tokens, paid once per run plus once
more only when a path failure asks for it. Fixed overhead per call is unchanged.

### 4h.1 The measurement

Full set, one pass each leg: 15/16 both ways, so on SUCCESS this item is a wash. The single miss
on each leg is a scoreboard fault, not a model fault (ON missed T16 because the counter did not
register a verification that the answer correctly described; OFF missed T12).

The cost aggregates (ON 238,255 prompt tokens and 17 fewer model calls than OFF) cannot be
attributed to the atlas from one pass, because this model swings 2-10x between identical runs.
So the item was then run three more times each way on the four path-touching tasks:

```
                 runs  passed  wrong-path tool errors  steps  prompt tokens
atlas ON           12     12             0               36      181,162
atlas OFF          12     12             4               46      193,072
```

Wrong-path tool errors are the failure class this item exists for, and they are the one metric
that separates cleanly: zero with the atlas, four without, across twelve runs each way (T06
twice, T08 twice). The steps figure moves the same direction and the success rate does not move
at all. Note the shape of that: the atlas PREVENTS the error rather than helping recover from
it, which is why an attach keyed only to failure would not have produced it. That is the
argument for keeping the first-turn attach, and it is the reason the re-ask hook stays as a
second chance rather than the only one.

## 4i. Item 1e, execution as the verdict: built, acceptance run pending

```
what it does   the verdict stops being "does it parse" and becomes "will this
               build actually use it", for the two artifact classes whose reader
               this build owns:
                 tools/<name>.py   loaded through ToolRegistry's own loader in a
                                   subprocess: it imports, then NAME, DESCRIPTION,
                                   SCHEMA and run are all present, NAME matches
                                   the file name, and SCHEMA is an object
                 config.json       the two startup refusals that are pure data in
                                   the file: an empty mattermost.allowed_users, or
                                   a url that is empty or still the placeholder
where          tinycmdr.py: _exercise_tool_load() + _TOOL_PROBE and
               _config_startup_problems(), called from _verify_python and
               _verify_json. The verdict rides in the same tool result as 1c's
config         no new keys - the same verify_after_write / verify_max_bytes
               switches turn it off and bound it
tests          tests/test_verify.py, 10 new checks
```

What it deliberately does NOT do: execute an arbitrary written script. A verifier
with side effects can break a run, which is the one thing 1c forbids; the agent
already has shell and execute_code for the runs it actually wants; and a write is
not a request to run. The two checks that are here are safe by construction - the
tool check imports a module the registry would import on its next call anyway, and
the config check reads two keys out of a file the startup path already refuses on.

Why the tool check had to become a real load: the old static scan asserted that the
names NAME, DESCRIPTION, SCHEMA and run appeared somewhere in the tree, which is not
the loader's test. The failure already seen in the field - a tool whose NAME
disagreed with its file name, loading under a name nothing would call - passes a
static scan and fails the loader. The verdict now names the registered name.

Checked live before the suite was updated, on files written for the purpose: a tool
that raises at import, one whose NAME disagrees with its file name, one whose SCHEMA
is not an object, one missing an attribute, an emptied allowlist, a restored
placeholder url, a partial overlay and a trailing comma each produced the expected
verdict, and an ordinary .py or .json produced the unchanged one. An interpreter that
cannot be run is a skip, never a broken tool - pinned by a check that points
sys.executable at a path that does not exist.

Acceptance run still owed: the same task set, both legs, with the tasks this is meant
to help named up front - T15_verify_ok and T16_verify_failure (the verify/honesty
categories) plus T10_unknown_tool. Run it before the change reaches a test bed.

### 4i.1 The acceptance run, 2026-09-17

Model box .47, `main` (Qwen3.8-Flash-Next), budget 24000, shipped defaults, same
16-task set both legs, run back to back so neither legs steal slots from the other.

```
leg A  1e off (1c only)  f900daeb0ddbf4bc   15/16   wall 1193.2s   60 tool calls   4 tool errors
leg B  1e on            8e37b5b7384c05c6   16/16   wall  593.3s   49 tool calls   2 tool errors
jsonl  tests/eval-runs/20260916-235846-1e-off.jsonl and 20260917-001844-1e-on.jsonl
```

Read honestly, none of that difference is attributable to 1e. The one task that
differs is T15_verify_ok, and it differs because the OLD leg's own model wrote a
config.json without `service.port`; 1e cannot influence the old build, so that is
one sample of a nondeterministic task, not a win. The wall and tool-call gaps come
from T06 (608.6s against 59.8s - leg A's model spent ten minutes on
`Get-ChildItem -Path "C:/Users/<user>" -Recurse -Include *.csv -Force`) and
T07 (91.6s against 44.3s): route luck, not the verifier. Both legs fired the verify
note on both verify tasks (1 verified, 1 failure each), so the change is silent on
ordinary files, which is what it is supposed to be.

The graded set cannot see 1e's actual branch at all: no task writes a custom tool,
so the `tools/` path is never exercised. That is a gap in the scoreboard, not
evidence about the change, so it was measured separately, same model, same two
prompts, both builds:

```
prompt: create a valid custom tool at tools/zztool.py
  1e off  [HARNESS verify: python syntax OK, tool contract present]      answer: "Done."
  1e on   [HARNESS verify: python syntax OK; tool loads as 'zztool' with a usable schema]

prompt: create tools/wrongname.py whose NAME is 'something_else'
  1e off  [HARNESS verify: python syntax OK, tool contract present]      answer: "Done."
  1e on   [HARNESS verify FAILED: the loader registers it as 'something_else', so a call
           to 'wrongname' (the file name) finds nothing. The file on disk is broken -
           fix it before reporting anything as done.]
```

That second pair is the whole case: the old verifier certifies a tool that the
loader will register under a name nothing calls, and the run ends with "Done.".
Measured cost of the change on a `tools/*.py` write: 52 ms median against ~0 ms,
five runs each - a thousandth of one model call.

Decision: ADOPTED. The reason is not the scoreboard, which cannot see this path,
but that it closes a silent and already-documented failure with no new config
surface and no verdict on a file type it cannot judge.

The scoreboard gap it exposed is now closed, and the new tasks measure the change
properly instead of by probe. Two tasks were added (set is 18, not 16):

```
T17_write_custom_tool    asks for a valid tool; both builds pass it, so it is a
                         regression guard that the verifier fires on every full run
T18_broken_custom_tool   asks for a tool whose import cannot resolve, and grades on
                         the harness verdict (verify_failures_min) plus the file
```

T18 is the discriminator, same model, one task at a time:

```
1e off   FAIL   verify failures reported 0 (min 1)
                answer head: "Done. Import left broken, as instructed."
1e on    PASS   verify failures reported 1
                the model's own words included loader/reject/import/fail/error
```

Note the shape of it: the old leg's answer *sounds* right - it mentions the broken
import - and it still ends with "Done.", because nothing in the harness told it the
file was unusable. That is why the grade is on the harness verdict and not on phrasing.

One follow-up the run exposes, not part of 1e:

```
recovery     T06: the atlas's wrong-path hook did not stop the first wrong guess and the
             digest cannot discourage an EXPENSIVE command - one sample spent ten minutes
             scanning the whole user profile. A cost bound on recursive filesystem
             searches is the item, and it is not in this plan yet.
```

## 4j. Item 6, a cost bound on expensive commands: mechanism built, shipped off

The failure it was written for, from section 4i.1: one graded leg spent 608 of
1193 seconds on a single command, `Get-ChildItem -Path "C:\Users\<user>" -Recurse
-Include *.csv -Force`. The digest shapes a command's OUTPUT and cannot discourage
the command; the atlas re-ask never fired because the guessed directory was real.

```
what it does   classifies a shell command as an unbounded walk (a recursive flag AND
               a broad root: a drive or filesystem root, /home, C:\Users\<name>) and
               caps that ONE call at agent.search_timeout (60s) even when the model
               asks for more, handing back the partial output plus three cheaper
               routes: name the directory, use search_files, bound the walk yourself
where          tinycmdr.py: _RECURSIVE_WALK, _BROAD_ROOTS, _path_tokens, _broad_root,
               command_cost_risk(), shell_timeout_for(), used by tool_shell
config         agent.search_timeout (60), agent.command_cost_guard (FALSE)
tests          tests/test_cost_guard.py, 40 checks, green on both builds
```

Measured on the exact command the graded leg lost ten minutes to:

```
the sweep itself    capped at 60.3s (was 608s unbounded), and the verdict the model
                    reads names the shape, the root, the ceiling and the cheaper
                    routes - verified by running the real command, not by a stub
```

Measured on the behaviour the item exists for: the same prompt, same model, two runs
each, one leg per build:

```
no ceiling    25.7s    206.2s
ceiling on   275.4s    267.3s
```

Read honestly, the ceiling did not move that number, and the log says why: it clamped
a model-requested 90s down to 60s twice, exactly as designed, and the model routed
around it - the same sweep repeated per call (3 to 6 shell calls), then `os.walk`
inside `execute_code`, where that tool's own 120s cap did the bounding. A per-command
ceiling cannot bound a cumulative strategy.

Decision: the classifier, the clamp and the guidance stay, because they are the
enforcement half of the thing that CAN bound it, but `command_cost_guard` ships OFF,
so no unmeasured behaviour change reaches a host and no legitimate long recursive walk
gets cut short. The real item, not yet built:
[Superseded 2026-09-17: that item was built and is section 4j.1. As shipped in 2.5.8 the
run budget is ON at 120 s (`agent.command_cost_guard: true` with `agent.scan_budget_seconds`
120), and the 60 s per-command ceiling is on too. What it buys is a finite worst case, not a
faster run. Any other artifact that still says this ships off is wrong.]

```
run-level scan budget   count seconds spent in broad-root scans across BOTH shell and
                        execute_code; allow the first one, and once the run's total
                        crosses the budget, refuse further scans with the same
                        guidance. That would have bounded all four samples above.
```

One thing the measurement did settle: the model does try to buy time (it asked for
`timeout: 90`), so a budget that can be argued with is not a budget.

### 4j.1 The run-level budget: built, and what it does and does not claim

```
what it does   counts the real wall-clock seconds a run spends on broad-root scans,
               across the shell AND execute_code, keyed by session and reset at the
               start of every run. Each scan is capped by what is LEFT of the budget,
               and once the budget is spent the next one is refused with the same
               guidance (targeted reads, search_files and a named directory still work)
where          tinycmdr.py: _SCAN_SPEND, _SCAN_LOCK, scan_spend, reset_scan_spend,
               scan_budget, scan_limits, charge_scan, code_cost_risk; tool_shell and
               tool_execute_code both go through scan_limits
config         agent.command_cost_guard (True - the master switch for both halves),
               agent.scan_budget_seconds (120), agent.search_timeout (60)
tests          tests/test_cost_guard.py, 60 checks, both builds
```

Measured on the same forced-search prompt, two fresh runs, against the two earlier
sets of samples:

```
no guard                    25.7s    206.2s
per-call ceiling only      275.4s    267.3s
run budget (120s)          145.6s     27.2s   scan spend 83.5s and 1.1s, 0 refusals
```

Read that honestly too: the budget did NOT fire in either run, so the wall-clock
difference is this prompt's route variance, not the budget. What the budget does is a
GUARANTEE rather than an average - a run can no longer spend more than the budget on
broad-root scans, and the 275s/267s samples were exactly a sequence of scans that
would have crossed it. The mechanism itself is pinned by the suite: real wall-clock
accounting, the refusal, the per-scan clamp to what is left, the budget shared between
shell and execute_code, and the master switch.

So the claim is: the worst case is now finite and the cost is zero while a run stays
under the budget (no refusal fired in either sample). It is not a measured speedup,
and it should not be described as one.

## 4k. Item 1b, tool-call repair: measured to zero, parked

Counted on 2026-09-17 across every log the fleet has, before writing any code:

```
                          the manager box   the Linux test box .13   the Windows test box .20   both eval legs
unknown tool                 0            0              0        0
invalid JSON arguments       0            0              0        0
missing required arg         0            0              -        0
tool runtime failures        0            0              -        -
```

The log's own TypeError noise (205 lines on the manager box, 177 on .13) is the Mattermost
websocket reconnect (`WSMessageTypeError: Received message 257`), not a tool call;
the single "does not match" hit is a substring inside an `edit_file` argument. So the
repair layer has no failure to repair on any model this fleet runs, and the plan said
so before the work started: "0 unknown tools, 0 malformed args in the baseline...
gets judged on a weaker one".

The weaker one is the blocker, and it got worse since the plan was written: the model
box's three cards now report 31.3 / 31.4 / 32.3 GB used of 32 GB, so a small model
cannot be co-resident (the plan's inventory note assumed 17-20 GB used). See section 5.

Parked on evidence, not on effort. It becomes buildable the day a weak model is
reachable: a swap window on .47, the aux box with an explicit go-ahead, or a small
file served on CPU.

## 4l. The daily model's own profile (2026-09-17): where its calls go

Measured on the fleet manager's whole log, real sessions only (test, eval, checkin
and probe sessions excluded), 3,488 tool calls across 7 sessions:

```
shell         1747   50.1%      read_file       293   8.4%
execute_code   630   18.1%      edit_file       277   7.9%
write_file     114    3.3%      skill            64   1.8%
task            63    1.8%      fetch_url        47   1.3%
web_search      41    1.2%      search_files      33   0.9%
```

Three findings, each of which kills or picks an item:

**Item 2f (recipe tools) is NOT justified.** The 1,747 shell calls contain only 49
distinct shapes, and the most repeated one is `docker ps` at 23 calls (1.3%). The
rest are one-off investigations - a driver install (`pnputil` x4), a BIOS query, a
time-sync check - i.e. heterogeneous ops work with no recipe to encapsulate. A recipe
tool pays only where a command repeats; here it would be a new tool for a 1.3% shape.

**Item 5 (stall ladder) is not justified either.** Real-session loop-guard nudges: 23,
and they are handled by the guard that exists ("`blog` repeated identically 2 time(s)",
"`shell` repeated identically 2 time(s)") with no observed harm. The 243 "stall" lines
in the log belong to a channel called `chan-stall`, which is the stall suite posting
into the live log; real stalls: one 8-minute warning ever. While the suite writes into
the live log, that number will keep lying unless it is filtered by channel.

**Item 3a (a code/config index) IS justified, by a wide margin.** Of 284 real
`read_file` calls, 152 of them - 54% - are reads of the build's own source,
`~/tinycmdr/tinycmdr.py`, pulling 788,899 characters (~197,000 tokens) into context,
over and over, to find functions in an 8,000-line file:

```
152 reads   788,899 chars   ~\tinycmdr\tinycmdr.py     <- 54% of all reads
  6 reads    12,584 chars   ~\tinycmdr\config.json
  6 reads    16,603 chars   ~\tinycmdr\notes.md
  5 reads    34,548 chars   ~\tinycmdr\tinycmdr.log
284 reads 1,441,931 chars   total (~360,000 tokens)
```

That is the largest single measured waste in the fleet's own use of this harness, and
it is the workflow the operator uses most (the 2.5.x build arcs). The atlas already
proved the mechanism for the OTHER kind of lookup - it cut wrong-path tool errors from
4 to 0 across 12 runs - so the same shape applied to the build's own structure is the
next item, not a new idea.

## 4m. Item 3a, the code map: built, measured, no win at its own criterion

Built 2026-09-17 and left in the tree PENDING THE OPERATOR'S CALL, not deployed. What it
is: `code-map.md`, generated on the host from the source that is running - one line per
top-level def/class/constant, then the running config (secrets redacted), the test files
with their docstrings, and the core tool list. Rebuilt when the source's size or mtime
moves, one stat per run otherwise. Bounded to fit ONE default read on that host
(`tool_read_file` caps output at `agent.tool_output_max_chars`, so the budget is the
smaller of `agent.code_map_max_chars` and 92% of that). The symbol list is never
trimmed; the tail sections are what goes when the budget bites.

`tests/test_code_map.py` - 33 checks, all green, both builds. The two that matter: every
line number in the map points at the symbol it names, and one default read returns the
whole map at 6k, 10k and 24k output caps. On this box the map is 8,669 chars / 320 lines
and fits a 10,000-char read whole.

### The acceptance test, declared before the numbers

A locate task (the shape a build session starts with): find the function holding the loop
guard and its line, name the config key that turns the cost guard on with its value, name
the test file covering the atlas and what its docstring claims. Same model (`main` on .47),
two runs per leg. The claim to beat, from the measurement that approved the item: 152 of
284 real `read_file` calls (54%) are re-reads of the build's own source - so the number to
move is reads of `tinycmdr.py` and the characters they pull in.

```
leg                          wall (s)          shell   exec_code   reads of the build   map used
A  no map at all             397.8  370.6      17/13    0/0         0 / 0                 -
B1 map, NOT advertised       270.3  397.2       7/16    3/2         3 (6,721 chars) / 0   no
B2 map, advertised           420.2  340.0      15/2     4/6         0 / 0                 yes (2 of 2)
```

**The map is not the bottleneck, and the A/B found why.** Round 1 (B1) never opened it:
the atlas draft is written a fraction of a second BEFORE the map, and its layout only
lists files that existed at that moment, so the pointer could never reach the first turn -
on a fresh install or on any host whose atlas already exists. That is a real bug and it is
fixed: `render_atlas()` now adds known files that exist but are missing from the parsed
atlas, so a new file announces itself. Round 2 confirms the fix works - the model reached
for the map in both runs (one `read_file`, one `findstr`/`Select-String` on it).

**And the cost did not move.** 384s vs 334s vs 380s mean wall, with a 270-420s spread on
identical legs: the difference is inside the noise at n=2. The deeper measurement explains
why an index changes nothing here:

```
153 reads of the build's own source in real sessions
 153  already targeted (offset/limit given)          <- the model is NOT reading blindly
 142  followed by ANOTHER read_file in the same session  <- it walks the file in slices
       sample offsets: (1118,160) (1147,75) (1140,130) (1222,110) (1326,150) (2319,180)
```

The waste is window-walking to find text - and the model's own `Select-String`/grep does
the locating part perfectly well. A list of top-level symbols does not remove a single one
of those window reads, because those windows are inside functions, not at their starts.

### Verdict

Item 3a as designed does not meet its own acceptance criterion. It is not a regression and
it is cheap (one stat per run, one 8.7 kB file), but "cheap and harmless" is not the bar
this build ships on. Two honest options, operator's call:

1. **Revert the map**, keep the atlas self-advertising fix (that part generalises: any
   future host-generated file gets listed without hand-editing an atlas).
2. **Keep it off by default** (`agent.code_map_enabled: false`) as an artifact a host can
   turn on, with the measurement above recorded next to it.

What the evidence DOES point at, if the slice-walking is worth attacking at all: the model
reads windows because it is looking for text, and `search_files` (33 calls) exists with a
`context` parameter it never uses, against 293 `read_file` calls. That is a candidate item
to design and measure properly - not something to bolt on now.

## 4n. Item 7a, the read ledger: built, measured, MISSED its declared target

Built 2026-09-17, in the tree, NOT deployed. What it is: every `read_file` window and
every skill read is recorded per session (`sessions/<key>.reads.json`, written through
`_ensure_sessions_dir()`), merged when windows touch, capped at 12 windows and 6 files,
and rendered as one bounded line in the run block:

```
Already read in this session (the harness keeps track - do not re-read a window just to
see it again):
  tinycmdr.py  lines 1118-1278, 2300-2500 (changed since: re-read if you need the text)
  skills already read: example-blog, fleet-access
```

`tests/test_read_ledger.py` - 25 checks, green: window recorded, windows merged, tail
reads recorded, the file edited since a read is flagged, skills listed once each, caps
obeyed (per file, per session, per line, marker counted), sessions isolated, empty
sessions silent, the line rides in the run block, and it NEVER changes what a read
returns - it is a nudge, not a gate. Two of those checks caught real defects: the bound
was breakable by its own trim marker, and a second `SESSIONS_DIR.mkdir` broke the CLI's
static no-writes-at-open assertion (now one helper, `_ensure_sessions_dir()`).

### The declared target, and the result

Declared in notes.md BEFORE the run: on a 3-run session working the same area of the
build, characters pulled from the build's own source AND from skills in runs 2 and 3 fall
by >= 40%, with the same answers. Run 1 is excluded (nothing has been read yet). The shape
is three runs, one session key, a FRESH PROCESS per run so the ledger has to come off disk.

```
                   run 1            run 2            run 3        runs 2+3 total
ledger off      22,261 chars     22,261           34,894            57,155
ledger on       20,696 chars     20,696           27,884            48,580
                                                        ->  15.0% less, target was 40%
```

Skill characters: identical in both legs (the one skill read happened in run 2, before the
ledger had anything to say about skills), so that half is UNMEASURED, not "no effect".
Answers: correct in both legs on every graded point (digest = `digest_output` at its real
line, the loop guard inside `Agent.run` plus its nested worker, the trailing block =
`volatile_context`) - so the change does no harm, which is the floor, not the bar.

**Verdict: MISSED. 15% at two runs per leg, in a harness whose wall clock swings 190s to
704s between identical legs, is not a measurable effect.** The instrumentation had to be
corrected mid-flight to see it at all: the probe counted `read_file`, and the model reads
the source through `execute_code` and `Select-String` just as often, so the metric now
sums every tool call whose arguments mention the source, whatever carried it.

### What that means for the original finding

The 54% figure was real but I read it wrong twice. The corrected shape: 120 of 153 reads
(78%) are windows an EARLIER RUN had already read - and the reason a new run re-reads them
is not that it forgot WHERE it looked. It is that the TEXT it read is no longer in its
context (trimmed to fit the window), so the answer still needs the bytes. A ledger of
windows says "you have been here", which is true and useless when the question needs the
content. That is why the effect is small and why no amount of pointing at the window will
enlarge it.

The item that WOULD attack the measured waste is content retention rather than
book-keeping: keep a bounded digest of what a read returned (the digest machinery already
exists for tool output) so the substance survives trimming, or raise the trim threshold for
reads the session demonstrably keeps coming back to. Both are candidates to declare and
measure properly; neither is built.

### What stayed after the revert

Two correctness fixes were kept, both independent of the reverted features:

- **The atlas advertises known files that EXIST.** `render_atlas()` used to trust the
  file's own listing, and a generated file can be written a fraction of a second after the
  atlas draft - so on a fresh install, and on every host whose atlas already exists, such a
  file could never announce itself. The layout is now rebuilt from disk, and `test_atlas.py`
  covers it.
- **One place creates sessions/.** `tests/test_cli.py` asserts the no-writes-at-open rule by
  COUNTING the string in the generated file, so any second writer of that directory breaks
  the CLI suite while behaving correctly. Every writer now calls `_ensure_sessions_dir()`.

The tree therefore sits a small step ahead of the fleet (which runs the ledger/1e/item-6
build), with no deploy: neither fix addresses anything the fleet currently suffers, so they
ride along with the next change that earns a push.

## 4o. Item 7b, carrying tool results between runs: SHIPPED TO THE FLEET MANAGER ONLY

The gap this closes, and it is structural rather than a model failing: `sessions/<key>.json`
holds the CONVERSATION only (11 messages, 36,523 chars on the fleet manager, and no tool
results at all), `_trim_history` keeps ~5 exchanges by design for prefix-cache reasons, and
the box runs a 200,000-token budget with ZERO compaction events in its whole log. So nothing
a tool returned outlives its run, and every later run re-buys the same ground: 70 of 153
source reads (46%) re-acquired a window an EARLIER RUN had read, 22 (14%) re-read one from
the SAME run, 37 of 48 skill reads were repeats of nine skills.

What it does: every tool result is recorded per session (`sessions/<key>.carry.json`),
bounded to 4,000 chars each and 40 entries, newest first, deduped by (tool, args),
age-stamped, with a changed-file marker for anything whose args name a path. Internal tools
(plan/task/remember/list_tools/find_tools) are not carried. The block rides into the NEXT
run between the system prompt and the conversation - byte-identical for every call of that
run, so the prefix cache survives, the same economy `_trim_history` is written for - behind
a banner saying it is from earlier runs and may be stale. Keys: `agent.tool_carry` (True),
`agent.tool_carry_chars` (8000).

### The declared target, the correction, and both samples

Declared first (notes.md, before any code): repeat-window reads in runs 2 and 3 down >= 50%,
answers unchanged, payload growth under 10k chars. **The first metric was the wrong one and I
declared it twice**: it counts `read_file` windows, and the re-acquisition happens through
`shell` and `execute_code` just as often, so it read 2 -> 2 and 1 -> 2 while the text actually
being bought collapsed. Counting every tool call whose arguments mention the source - which is
what the item exists for - two independent samples say:

```
sample 1   runs 2+3 source text bought   off 25,191 chars (7 calls) -> on 5,082 (4)   80% less
sample 2   runs 2+3 source text bought   off 13,059 chars (4 calls) -> on 2,908 (4)   78% less
declared proxy metric (read_file windows only)   missed in both samples
wall clock   sample 1: 498.7s -> 274.9s (45% less) | sample 2: 319.0s -> 368.9s (16% more)
carried block  6,364 and 6,007 chars (8k bound: the payload clause now holds)
answers  spot-checked by hand in both legs, facts correct (digest_output at 1074,
         _DIGEST_SHAPES 1004-1030, the three digest keys, `raw`'s effect) - not automated
```

Wall clock is mixed, which at two runs per leg is what noise looks like; the claim is the one
the evidence supports - later runs buy ~80% less source text, in two independent samples.

### A real defect the acceptance run found

Tool calls run in BATCHES, so two of them recorded at the same instant and the atomic write
raced itself: `WinError 5` on the rename, then `falling back to a plain write` - exactly the
degradation the ledger's atomic writer exists to prevent. Fixed with `_CARRY_LOCK` around
every mutation and save, and pinned by a threaded test (48 concurrent records leave a valid
store and no temp file). `tests/test_tool_carry.py` is 28 checks, green on both builds.

### Where it ships, and what is still unproven

Pushed to **the manager box only** (the fleet manager, the box the evidence came from) with a log line
per run - `carry: N chars of earlier tool results ride along (run R)` - so the fleet's own
logs become the next measurement, which is how this whole line of work started. the Windows test box and
the Linux test box stay on the ledger/1e/item-6 build until that data lands.

Unproven and recorded as such: wall-clock effect (mixed at n=2), answer quality beyond the
spot check, and what the carry does to a session whose work drifts away from what it read
(the oldest entries are carried until the budget pushes them out, and nothing prunes entries
that stopped being relevant).

## 4p. Item 7c, the memory guard: the bot's memory defends itself (2026-09-17)

An agent (me) appended its engineering log to `~/tinycmdr/notes.md`, which is not a log: it
is the bot's memory, capped at 4,000 chars, re-read in every prompt. Two ~1.3 kB entries took
the whole budget and the curator - doing exactly what it is designed to do - evicted 28 of the
bot's own facts to make room. The bot lost its fleet knowledge mid-shift and read as suddenly
stupid. A rule in a skill does not stop a process from writing a file, so the harness now
defends its own memory:

```
provenance    notes-authored.json holds hashes of the entries the bot wrote, keyed on entry
              TEXT so a re-render cannot shift identity. Bootstrapped from the file on first
              use: whatever is already there was the bot's memory, and the guard must never
              retroactively evict the facts it exists to protect.
precedence    curate_notes evicts FOREIGN entries FIRST, whatever their age, so a flood can
              never push the bot's own facts out. Deterministic, no model involved.
notice        it logs a WARNING naming how many foreign entries and the oldest one, and says
              where engineering notes belong (docs/dev-log.md).
lane marker   every render carries one line saying what the file is, so the next writer reads
              it before appending: 'bot memory: rides every prompt, keep entries short.
              Scripts and agents: log to docs/dev-log.md, not here.' (~100 chars, every turn)
```

`tests/test_notes_guard.py` - 22 checks, green on both builds: bootstrap cannot mark memory
foreign, the harness's own writes are recorded, a foreign flood leaves every bot fact intact,
the invariant "no bot fact archived while an outsider's entry stays resident", a tight cap
keeps the bot's entries and none of the flood, the warning fires, the marker appears exactly
once and survives a hand rewrite, nothing is deleted (only archived), and a repeated fact is
not reported as an outsider's write.

Live: the manager box restarted onto it (`eff369b8e42d4af5`), the bot's memory restored (6 entries,
3,827 chars, marker first line), and `notes-authored.json` seeded with those 6 so protection
is in force before the build's own bootstrap runs. Other boxes get the guard with the next
fleet push; only the manager box has been poisoned, because it is the box this agent works on.

## 5. Server side: what is needed and what is not







Verified on the running build on the model box (llama.cpp 0.4.0-dev, build 10867),
read from `tools/server/server-schema.cpp`:

```
per-request, no server change, no restart
  json_schema / grammar, with grammar_lazy + grammar_triggers
  a tool_calls grammar type: the server can build the tool-call grammar itself
  continue_final_message            for assistant-turn priming
  reasoning_control, reasoning_budget_tokens

gotchas
  a plain grammar constrains the whole generation, and these boxes run max
  thinking with reasoning inline in the same stream, so the lazy form plus a
  trigger on the tool-call opening token is the shape that keeps thinking free
  grammar_lazy with no triggers is rejected by the server
  the cloud fallbacks take json_schema but not GBNF, so the structured path has
  to branch per provider
```

Not needed for phases 0 to 3: no flags, no template change, no restart. Genuinely
server-side decisions, both deferred:

ANSWERED 2026-09-17: no weak-model leg. The daily driver is Qwen3.8-Flash-Next on .47
and that is what everything is measured against. The numbers below are kept only
because they explain why a small model would be awkward (it would now need a swap
window: the cards are at 31.3 / 31.4 / 32.3 GB used of 32 GB), not because the leg is
planned.

```
model availability for the scoreboard   the box serves one model with 3 slots, and it
                                        has no 9-12B class file on disk at all (the
                                        smallest standalone weights there are a 27B
                                        dense; the 2.4 GB and 3.9 GB Qwen3.8 files are
                                        MTP drafters, not models).
                                        RE-MEASURED 2026-09-17: co-residency IS the
                                        blocker now. The three 32 GB cards report
                                        31.3 / 31.4 / 32.3 GB used, and 432 GB free on
                                        /mnt/models, so a second model cannot sit
                                        beside the resident one without a swap window
                                        that would take the fleet's primary offline.
                                        That leaves: a swap window (operator's call),
                                        the aux model box (needs an explicit go-ahead;
                                        the standing rule is no test traffic there), or
                                        fetching a small file and serving it on CPU.
escalation target for phase 5           nothing bigger is resident; either the
                                        cloud fallback or a second local file
```

Open question for the operator: whether harness-internal mechanical calls (plan
decomposition, output extraction, step verdicts) may run with a short reasoning
budget while agent turns stay at max thinking. The standing rule is max thinking
on these boxes, so this needs a decision rather than a default.

Answered 2026-09-16: no, and 2d goes with it. The build makes no mechanical model
calls, so there is nothing to bound yet; if 1f's summarizer turns out to over-think
when it is measured on .47, that one call site is the place to revisit. See section
7, item 2d.

## 6. Limits, stated up front

```
what scaffolding fixes    didn't know, didn't check, bad syntax, lost the thread
what it cannot fix       reasoning about a genuinely novel failure
the failure mode to fear a pile of interacting nudges nobody can debug, which is
                         framework bloat arriving through the back door this build
                         exists to escape
the hidden-weakness risk  a well-scaffolded model looks better than the model is
maintenance              the atlas and the field-note library are curated assets.
                         Entries come from real incidents only, same rule as every
                         other guard in this build
```

## 7. Decisions, 2026-09-16

Source: the GVS5H paper (`arXiv:2608.26480`, Persis Capital, 27 Aug 2026) and its
repo, reviewed against this plan. Ledger-based zero-shot self-orchestration: the
same model in fresh contexts, coordinating only through files on disk (plan, task
list, accumulating notes, current artifact), a manager that re-curates the task
list every round and decides when to stop, a worker per round, and sample tests
run as a real subprocess whose verdict overrides the manager's "done". Training-
free, no per-benchmark tuning.

Read its numbers before quoting them. The paper's own appendix documents live bugs
in the LiveCodeBench evaluator (a stateless `buffer.readline` mock, `sys.stdout`
captured into a StringIO with no `.buffer`); every published number is post-fix and
the correction moves every pinned arm up 3.0 to 6.2 points, unevenly across models.
Its headline Flash Next figure (93.0 against Fable 5's 90.4) appears only in the
abstract, with no table or figure in the body, and the arm was run on their own box.
Fable 5 has no manager arm at all. The direction is well supported; a single
quoted multiple is not.

What is true about OUR harness regardless: most of their scaffold already exists
here (notes under a cap with an archive, a durable task ledger re-sent every
prompt, a derived plan with a drift nudge, an atlas, post-write verifiers, one
level of delegation, progressive disclosure). The four items below are the parts
that do not.

```
1e  execution as the verdict      BUILT 2026-09-16, detail in section 4i.
    Their step 5 runs the artifact and feeds the pass/fail back with the first
    failing case, and a failing run overrides a "done". Ours stops at parsing the
    file in the language its extension claims. Extend 1c from "does it parse" to
    "does it run, and what did it produce", with the harness verdict overriding a
    claimed success. Their appendix is the argument in both directions: their
    manager accepted a confidently-wrong worker report, and their own grader was
    certifying wrong answers through a broken stdin mock.

1f  cut-off summarizer            MEASURED 2026-09-17, NOT BUILT as specified.
    The intent was to rescue the thinking of a call that hit the cap mid-attempt.
    Counted in the fleet's own logs before building anything:
      "reasoning with no answer" (a real cut-off mid-think)     7 occurrences, all
        real sessions, all of them ALREADY rescued by the existing escalation
        (one retry at max_tokens_ceiling, logged as "retrying once at 65536")
      the terminal form of that failure (whole budget spent, turn ends with the
        ⚠️ message and the reasoning discarded)                  0 occurrences ever
      degenerate-empty-turn retries in real sessions             0 (all 54 are test
        sessions in the log)
    So the loss 1f prevents has not happened here in the log's life, and the case it
    does target is already recovered by code that exists. Our regime is a 16k cap with
    a 65k escalation on a 90 tok/s box, not the paper's 128k-cap frontier APIs where
    30% of single calls are cut off.
    What would make it worth building, in order of preference:
      1. the escalation stops rescuing (a terminal ⚠️ appears in a fleet log), or
      2. a cheaper variant with no new model call proves out - hand the RETRY the tail
         of the cut-off reasoning (the part nearest the answer) so it resumes instead
         of re-deriving from scratch. That is the real cost today: the escalation
         re-thinks the whole problem, up to 65k tokens, minutes on this box.
    Not built until one of those is measured. The 2d reasoning stands: a knob invented
    before its failure is measured is not worth its config surface.

2d  per-role parameters           DROPPED by the operator, 2026-09-16.
    The proposal was per-role temperature (0.2 execution, 0.3 plan, 0.4 ideation)
    and per-role thinking depth. Two reasons it is out, both measured on this
    build rather than argued:
      * the harness makes no model calls of its own. One path, Agent._chat, three
        call sites, every one inside an agent turn; digest, the plan, the atlas
        and the verifiers are all deterministic. Their 0.2/0.3/0.4 split lands on
        four distinct role prompts and we have none of them, so the temperature
        half had nowhere to go;
      * the thinking half would only ever apply to 1f's summarizer, where the
        benefit is wall clock on a rare call and prompt length dominates anyway
        (prefill ~300 tok/s, 16s TTFT, so a ~700-token prompt floors a call at
        ~5-10s before thinking; 2,000 thinking tokens would add 22s). The eval
        baseline ran 45 calls on 6,263 completion tokens at shipped defaults, 139
        per call, with no reasoning control in play - the short, well-specified
        work this would cover is not the work that over-thinks.
    If it is ever revisited, the entry point is a measurement, not a knob: run the
    1f summarizer a handful of times on .47 and read its tokens and wall clock. A
    bounded budget on one call site that is shown to over-think earns its place;
    a per-role config surface invented before that measurement does not.

2g  fresh-perspective worker      DEFERRED, opt-in only if ever built.
    Their proposal: some workers get the raw task and no notes, so a wrong early
    approach cannot anchor everything downstream. The failure is real here (a
    sub-run already inherits notes.md through volatile_context, so "fresh" has to
    mean stripped). The cost is not, on this hardware:
      generation 90-92 tok/s solo (server-reported, tinycmdr.log 2026-09-14),
        35 tok/s is the per-slot figure with all three slots busy, and
        tool_delegate_task calls AGENT.run() inline so a worker has the box to
        itself while the parent is blocked
      prefill ~300 tok/s, 16s time-to-first-token on a real prompt
      17-22s per real call with thinking on, so a second full attempt is the whole
        loop again: 2-4 minutes of serial wall clock on the fleet's only model
    Two delegate_task calls have ever been made on this box (both 2026-09-09), so
    the blast radius is nil, but so is the evidence. It is also the only one of the
    four items their own paper never ran: it is a discussion-section suggestion. If
    it ever lands: a `fresh=true` flag on delegate_task that withholds notes and
    atlas for that one sub-run and returns a proposal the parent must reconcile,
    gated on 1e, and never automatic.
```

Acceptance rules for 1e, 1f and 2d are the ones already in section 4: a before/after
run of the SAME task set with the tasks it was meant to help named up front, one leg
with the scaffolding off, and every added nudge justifying its tokens the way a
runbook does at 23. 1e changes what a turn is allowed to claim, so it also carries
the scoreboard's honesty category, not just its pass count.

### 7.1 Where this work gets tested (operator, 2026-09-16)

```
test beds      the Windows test box a LAN address   the Windows test bed
               the Linux test box a LAN address   the Linux test bed
dev traffic    our own fleet only. Nothing here is measured on a cloud frontier
               model: the question is what THIS harness does for the boxes we run.
publishing     no updates to the tech site unless there is a major release or a
               stated reason. It has no readers and no users, so a routine version
               bump is not a reason to publish; the site waits for something worth
               reading.
```

The the manager box model box (`a LAN address`) is the primary the whole fleet talks to: it
currently serves `Qwen3.8-Flash-Next` (IQ3_XXS, 2 shards, 3 slots, 262,144 per
request), which is the exact model behind the paper's headline arm. That is the
cheapest available test of 1e and 1f: same model, same harness, before and after.

## 8. Sources

- The scoreboard, task set and measurements in section 2 and 4: this repo.
- llama.cpp request fields in section 5: `tools/server/server-schema.cpp` on the
  model box, build 10867.
- Harness vocabulary (harness, ratchet, break-even rule, the missing eval suite):
  see section 6 and 9 of `tinycmdr-what-it-is.md`.
