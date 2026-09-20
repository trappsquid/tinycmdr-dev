# tinycmdr-cli: the chatless build (work plan)

Status: **BUILT 2026-09-13** (source `tinycmdr-cli.py`, 4,205 lines, generated from tinycmdr.py v2.0.0
by anchored cuts; see "Built" at the end of this document for what differed from the plan).
Originally written at the end of the Hermes session so a fresh session could execute it cold.
Everything below was measured against `tinycmdr.py` v2.0.0 (sha256 `ee2631a9a5de9da4`).

## 1. What is being built, and the decisions behind it

A second build of the same agent for terminal use. Open it, use it, close it. No chat gateway, no
daemon, no background presence.

David's calls, in his words:

- Strip Mattermost and every chat gateway. Interact with it in a cmd window, the way Hermes's CLI
  worked.
- A single API endpoint in `config.json`, everything else removed. No fallbacks.
- No watchdog, no keep-alive. The process exists while the window is open and not otherwise.
- No cron and no scheduler tool. If a recurring job matters, the OS runs a one-shot.
- No web UI.
- Packaging: one source of truth with a build flag is easier than a hand fork, so do that.

## 2. Why this is mostly subtraction

Five seams already exist, which is why this is a day of work and not a rewrite:

```
mmpy_bot is imported lazily inside run_bot()            --cli already runs with no chat extras
report(channel_id, text) (line 1881)                    delivers to stdout when there is no reporter
the loop is keyed by session key, not channel           run_cli() already uses the key "cli"
the Scheduler already tolerates a missing reporter      it passes None callbacks today
the Mattermost layer is one contiguous block            lines 4051 to 5501, nothing interleaved
```

## 3. The cuts

Delete, in one pass, from a copy of `tinycmdr.py`:

```
the Mattermost block (4051-5501, ~1,450 lines)
  MattermostDispatcher, ProgressReporter, status_text, bar_props, want_color, _CatchUpMessage,
  _web_command, run_webui, run_bot, the websocket listener, the catch-up sweep, attachments and
  uploads, the allowed_users gate, the /bg path, _exit_code and _tool_preview (used nowhere else)
the stall watchdog                          ~13 references, stall_warn_minutes / stall_abandon_minutes
check-ins and the status-line cadence       ~32 references
the single-instance lock                    bot mode only; the CLI never takes it
the restart handover                        _note_restart, the announce_restart state, os.execv path
the Scheduler class and the schedule tool   ~150 lines
```

Keep, deliberately, because none of it is a keep-alive:

```
max_steps, max_minutes, shell_timeout, tool_output_max_chars   runaway bounds, Ctrl-C is the backstop
request_timeout, request_grace, stream_idle_seconds            what stops a wedged model call hanging
                                                               the terminal forever
loop_dedupe_after, loop_stop_repeats                           the loop guard. A weak local model will
                                                               re-issue the same call forty times
blocked_patterns, confirm_patterns                             the confirm gate already prompts yes/no
                                                               in the CLI
notes and ledger keys, skills, all 17 tools                    nothing here knows what a channel is
streaming model calls                                          becomes terminal output
```

## 4. Config target

From five blocks to three. `mattermost` (7 keys) and `web` (5 keys) go entirely.

```
llm      keep 12 of 18:  base_url, api_key, model, max_turns, max_context_tokens, max_tokens,
                         final_max_tokens, max_tokens_ceiling, request_timeout, request_grace,
                         retry_after_max, no_think, stream, stream_idle_seconds
         drop:           fallbacks, allow_cloud_fallback  (and with them the failover and privacy gate,
                         about 20 lines around line 3013)

agent    keep 21 of 39:  bot_name, history_exchanges, max_steps, max_minutes, shell_timeout,
                         tool_output_max_chars, notes_max_chars, notes_max_note_chars,
                         notes_keep_entries, notes_archive_days, tasks_max_open, tasks_done_keep,
                         progress_updates, loop_dedupe_after, loop_stop_repeats, blocked_patterns,
                         confirm_patterns, subagent_model, show_usage, debug_dump_dir, color_coded
         drop 18:         the 12 checkin_* keys, stall_warn_minutes, stall_abandon_minutes,
                         catch_up_seconds, catch_up_max_minutes, vision, announce_restart

search   optional: keep the 4 keys if web search stays useful in a terminal. David's call.
```

## 5. The new CLI

Replace `run_cli()` (line 5502, which today has exactly one verb, `reset`) with a Hermes-style
session, roughly 300 lines:

```
prompt and output      streamed answer text straight to stdout, tool calls as single ANSI-coloured
                       lines (green narration, amber tool, red failure, the same three meanings the
                       chat colour bars carried), a usage line after each run
verbs                  /new /model /status /stop /compact /tasks /notes /jobs /skills /sessions /help
                       (map onto the command handlers the bot layer already had)
stop                   Ctrl-C interrupts the run; OperatorStop already exists for this
modes                  interactive by default, --once "<task>" for scripted one-shots, which is how
                       the OS scheduler drives recurring work
gone                   --jobs, the queue, steering, /bg
```

The scheduled-job replacement, to be documented in the README:

```
Linux    */15 * * * *  <dir>/.venv/bin/python <dir>/tinycmdr-cli.py --once "check disk, report over 90%"
Windows  Task Scheduler, same command line
```

## 6. Tests

```
keep, adjusted    tests/test_ledger.py   ledger, memory, compaction (minus steering and dispatcher)
                  tests/test_stall.py    loop guard, dedupe, stop path (minus dispatcher, watchdog,
                                         check-in and catch-up sections)
drop              tests/test_checkin.py  the whole suite: check-ins do not exist in this build
add               ~10 checks             the CLI verbs, the streaming callback, the Ctrl-C path, and a
                                         config that must refuse an unknown block
target            about 300 checks, green from a clean unpack of the new archive
```

## 7. Packaging

`maintenance/build-package.py --cli` emits `dist/tinycmdr-cli-<version>-{win,linux}-public.zip|tar.gz`
from the same source tree, and the CLI variant ships `config.cli.example.json` instead of the five-block
example. The main build is untouched, and both keep the same `VERSION` so drift stays detectable by
hash (`maintenance/fleet-version-report.ps1`).

## 8. Verification checklist before calling it done

```
1  python tinycmdr-cli.py --once "hostname and free disk"  works against a 12-key llm config
2  grep -c mmpy_bot tinycmdr-cli.py                        zero
3  grep -ci mattermost tinycmdr-cli.py                      zero
4  grep -c "channel_id" tinycmdr-cli.py                     zero
5  suites green from a clean unpack of the published archive
6  runs on Python 3.10 (two fleet hosts are on 3.10, and it is the floor the installers accept)
7  the file imports and starts with no network at all except the one endpoint
8  fleet-version-report.ps1 shows the CLI file hash, so a fork cannot hide
```

## 9. Open questions for David

- Web search in the CLI build: keep the `search` block, or strip it too?
- Whether the CLI variant should keep `delegate_task` (sub-agents) now that nothing is asynchronous.
  My default: keep it, it is still useful for farming out a self-contained subtask.


## Built: what differed from this plan (2026-09-13)

Every estimate in the plan held except these, all measured after the fact:

```
third-party HTTP     the plan said "requests -> urllib shim, ~12 call sites". Done, but the shim
                     keeps the NAME requests: both suites monkeypatch fb.requests.post and build
                     fb.requests.HTTPError, so renaming would have meant rewriting the suites. It
                     also exposes ConnectionError and Timeout, because requests did, and the
                     fixture tests assert on those types.
the clock            kept. The trailing block still carries the live timestamp, which is why a
                     terminal session can answer "what time is it" with no tool call.
the config           three blocks shipped (llm 14 keys, search 3, agent 20) rather than the
                     "llm 12" first written here: stream, stream_idle_seconds and no_think were
                     kept, because a terminal session against a slow local model needs them.
first run            a wizard was added (endpoint, model, key) since the end state is drag the
                     folder, double-click, chat. config.example.json is generated from the build's
                     own DEFAULT_CONFIG by maintenance/build-cli-package.py, so it cannot drift.
launcher             tinycmdr.bat ships next to tinycmdr.py: a lot of Windows machines (the Windows test box
                     included, checked) have no .py file association, so double-clicking the .py
                     does nothing. The .bat finds Python, runs the agent in its own folder, and
                     keeps the window open. It also proved the 3.13+ refusal is gone: py -3 picked
                     3.14.7 and the agent ran.
tests                test_ledger runs 171 green against this build (11 chat-only tests are skipped
                     by name, controlled by tinycmdr_SRC). test_stall and test_checkin cannot run
                     here at all: they are built on the dispatcher and the check-in cadence. A new
                     tests/test_cli.py (57 checks) covers the shim, the wizard, the single-endpoint
                     config, the verbs, the launcher-free code paths and the tool loop.
```

Test bed: `C:\tinycmdr-cli` on the Windows test box (a LAN address), beside its Mattermost bot, which is
untouched. It ran `--version`, `--help`, a real task against the LAN endpoint
(`http://a LAN address:8081/v1`, the model that box already uses) and `tinycmdr.bat --once ...`.

Release: `maintenance/build-cli-package.py` -> `dist/tinycmdr-cli-2.0.0-{win-public.zip,
linux-public.tar.gz}`, through the same public gate as the Mattermost release (clean), with a
clean-unpack check that runs `--version` and the CLI suite from the extracted folder.

### Second pass: the DoD / IL5 constraints (2026-09-13, same day)

David's environment is a DoD one, pointing at a single IL5-approved endpoint, and the
rules there rewrote part of the plan:

```
web search          removed entirely, and the URL-fetch tool with it: the only network
                    destination in the build is llm.base_url. tests/test_cli.py asserts
                    it structurally and it is grep-checkable in the shipped file
scripts             no .bat, no .cmd, no .ps1, no installer. Executable whitelisting blocks
                    those, so the folder is tinycmdr.py, README.txt and config.example.json,
                    started as: python tinycmdr.py   (Anaconda Prompt works and needs no
                    packages installed, since the build is standard library only)
config shape        one endpoint entry: base_url + model. No key plumbing and no certificate
                    handling (the platform authenticates); no Authorization header is sent unless
                    a key is configured
plain http          a warning is logged (not a refusal) for a non-loopback http endpoint
shell               agent.shell = "powershell" | "cmd", for hosts where PowerShell is
                    restricted. The prompt names whichever is in force
version             1.0.0, its own line rather than riding on the bot build's 2.0.0
```

Final shape: 4,172 lines, sha256 `46dcacad0469dc55`, `tests/test_cli.py` 90 checks,
`tests/test_ledger.py` 171 against this build, archives `dist/tinycmdr-cli-1.0.0-*`, and a
live run on the Windows test box started with the box's own Python exactly as the README says.

Enterprise defaults, measured against the target model: the prompt budget is 500,000 tokens for a 1M-token window (compaction therefore almost never fires), and a rejected output cap is halved and retried once, which matters because DeepSeek-class providers cap output at 8,192 while the local models this build inherited had no such ceiling.
