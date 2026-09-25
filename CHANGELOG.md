# Changelog

All notable changes to tinycmdr are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.13] - 2026-09-25

### Changed
- **A machine shutdown or restart ASKS now instead of being unappealable.** The operator ordered a host restart from chat; the absolute tier refused the verb, and the run then spent 40+ steps writing a script and launching it through a tool, so the restart reached the host with the pattern never in sight - the block cost the yes, not the restart. `shutdown`, `poweroff`, `reboot` and `(Stop|Restart)-Computer` moved to `confirm_patterns`: quoted back to the operator, and declined on a lane with nobody to ask. The irreversible tier keeps disks, partitions, filesystems, shadow copies, the fork bomb and an encoded command blob; the prompt's shell line now names what it really blocks, at the same length.
- **A drop-in tool that spawns its own process gets the box's real shell and the safety tier.** `shell_argv` and `shell_guard` ride the tool context beside `confirm_cb`, and `process` uses both: a string command ran under the Windows command interpreter while the prompt says the shell is PowerShell (a bash-style and a PowerShell-style loop both died in it, and a third form exited 0 having echoed the command as text). A script launched through a tool was also the last route around the tier that refuses the same verb in the shell.

### Fixed
- **`search_files` did not work in the shape the prompt teaches.** Its own schema read `pattern` as a file-name glob and put the grep in `content`, while the route hint and the routing bullet both teach `{"pattern": "<regex>", "path": "<file or directory>"}` - so the taught call answered a confident "No matches." for a string the file held ten times. A file path is grepped directly now, `pattern` greps content as well as names, and `content` keeps its scoping job.
- **`create_tool` took four calls to land.** The name is derived from the code when the `name` argument is absent (the run had written it in the file's own header), empty code says so instead of writing a bad file, and any call that leaves out a declared argument is told which one and what the tool takes.
- **The tool-disclosure answers carried no diff.** Asked which tools were NOT in its list, a run called `list_tools` and `find_tools(all=true)` in one batch, read "22 of 22 are in your list", and answered "none are hidden" - its own sibling call had revealed them all a moment earlier. `find_tools all=true` now names the tools that were not in the list a moment ago, and `list_tools` names the reveal.
- **`process` ran a JSON argument list as a shell string** (`'["powershell.exe"' is not recognized`), and `toolsmith list` counted `lib/` helper files as callable tools.
- **A `done` that named no task listed only the ids**, so a run with two items open dropped the ledger for the rest of the run; it names every open item with its text now.
- **A repeat guard could be defeated by the harness's own hint.** The mint hint rides the second call's result, so the third identical call looked different and re-ran; the guard compares the tool's answer with harness annotations stripped.
- **A config field NAMED token/key/secret is masked from six characters**, not twelve. A ten-character web token was quoted into chat by a run that answered "where is the token file" - the sweep had skipped it.
- **`remember` could only append while its own schema promised "replace stale facts instead of stacking contradictions".** It takes `action=note|replace|forget`, the reply names the entry, the text and the budget instead of "OK: noted", and a near-duplicate entry is named with the replace call to use.

### Added
- **Minting: the harness keeps the census the model cannot have.** `logs/procedure-census.json` counts a command's vocabulary (the cmdlets or verbs it is made of) per RUN, and one line rides the third run's result naming the mint call. The second run is the threshold, because that is where a human says "this is the second time".
- **The operator is asked, once per procedure per week.** After a run that drove several hand-made calls, minted nothing, and either repeated the same request or executed a runbook by hand, the harness posts one line offering to build the tool.
- **The bot offers it in its own report.** A run whose census fired gets one line in its trailing block inviting it to mint or to say so in the report - and it does: "Routine and repetitive (this is the 3rd+ run of it on the box) - I can mint a small tool ... Your call."
- **Memory is visible and volunteered.** A memory write's progress line reads `memory`, a lookup that answered a durable-fact question gets one nudge on the result, the harness offers to keep the fact at run end, and the tools' own descriptions carry the judgment about when to mint or save.

## [1.0.12] - 2026-09-24

### Fixed
- **`list_tools` claimed tools the session did not hold:** the answer said all 22 core tools were already in the model's schema block while the payload carried 14 (`turn ... tools=14`). A run asked to build a tool read it, never reached for `create_tool`, and scaffolded the file through the shell. The answer now reports the count this session really holds, names the hidden tools, and prints a custom tool's file only when it differs from the tool name.
- **The run plan survived `/new`:** `AGENT.reset` cleared history, transcript and carry but not `_RUNS[key]`, the plan re-sent every turn, so a fresh session opened with the previous task's steps in its trailing block and burned the run on them. `_run_state_reset` rides the reset now.
- **The tool-file-as-script miss was only answered on the shell door:** code that ran or imported `tools/<name>.py` from `execute_code` walked past that guard (measured: eight calls at `toolsmith.py` in one run). Both doors give one answer now, including the file-name-to-tool-name mapping, and the tool is revealed so its schema is in the payload rather than only named in prose.
- **A file written into `./tools/` got no verdict until the next start:** `write_file` now runs the loader on it and rides the verdict (refused with the shape it needs, or the tool names it loads as).
- **The drop-in shim was missing Hermes' `tool_result`:** `from tools.registry import registry, tool_error, tool_result` raised ImportError and the whole ported file was refused. Added, with `tool_error(**extra)`.
- **Loading warnings named no route:** a refused drop-in file now says whether it is a ported Hermes-tree file (wrap the script as `<name>.tool.json`, or rewrite it with `create_tool`) or a non-conforming native one, and a successful load of a file that was not there at the last start says what it loaded as.
- **`reload_tool` could not reload a ported file:** a register-shape file answers to the name inside it, which need not be the file name (`hermes_todo.py` registers `todo_list`). Reload by tool name follows the registration the file already has.

### Added
- **An order that names a hidden tool reveals it before the first call** (`reveal_tools_named_in`, capped at four per order; asking for a tool to be built reveals `create_tool`), and `create_tool` reveals what it just made. Measured on one box, same order: seven `skill{action=list}` calls and zero calls to the two tools named before, versus the two tool calls and a finished run after.
- **`/tinycmdr <verb>` in chat:** the Mattermost lane dispatched only `/new`, `/stop`, `/restart`, `/model`, `/status` and `/undo`, so `/tinycmdr update` - the command the fleet is updated with - went to the model as ordinary text. Every management verb that makes sense in a channel runs there now and posts its output; `run`, `setup`, `token` and `restart` are refused by name because they need a terminal or have their own fast path.
- **`update` adopts the git path on a fresh install:** five of six fleet installs were folders rather than checkouts, so the verb had nothing to pull and answered with a usage line. It now clones the git metadata into place and checks out the published branch, writing tracked source only, and it reports the HEAD and build hash that moved. A host with no git binary says so instead of pretending.
- **Prior-run false interruption alerts:** Active turns were incorrectly flagged as interrupted because `_prior_run_unfinished()` evaluated the in-flight user message. Fixed by ignoring the active user turn during live execution.
- **Empty-ledger task error:** Calling `task action=done` without an ID when no tasks were active returned contradictory `no task #None`. Fixed with clear message indicating no active tasks.
- **PowerShell 5.1 command chaining with `&&`:** Windows PowerShell 5.1 rejected `&&` command separators. Added quote-aware translation to `; if ($?) { ... }` in `tool_shell`.
- **Carry store persistence across session reset:** Resetting a session with `/new` or `/reset` wiped conversation history but left the carry sidecar (`.carry.json`) in memory and on disk. Fixed by unlinking `.carry.json` and evicting in-memory carry in `AGENT.reset()`.
- **Task completion spin on empty ledger:** Calling `task action=done` when all ledger tasks were already closed returned an error instructing the model to add tasks, triggering repetitive retry loops. Fixed by returning a completion notice directing the model to deliver its report.
- **Line deletion residue in `edit_file`:** Deleting text via `edit_file` with `new_string=""` left blank lines in both exact full-line and fuzzy line-window replacements. Fixed line slicing and full-line matching so deleted lines leave no blank lines.
- **Process isolation guidance in `execute_code`:** Added runtime diagnostic hint on `NameError` reminding the model that snippets execute in isolated processes requiring self-contained imports.

## [1.0.11] - 2026-09-24

### Fixed
- **Premature stop after prior tool calls:** Runs that completed initial tools could still stop on an unfinished intention statement. Added a one-time prompt asking the model to proceed with the next tool call, with a plain `stopped short` note if it still stops.
- **Result claim detection:** Metric statements like "log says 12 errors" bypassed unverified claim checks. Added pattern matching for report verbs followed by counts on local files.

## [1.0.10] - 2026-09-24

### Added
- **Turn decision logging:** Added structured per-turn logging (`shape=`, tool schemas on wire, server prompt/completion tokens, reasoning chars, and harness nudge state) to record model turn decisions directly in logs.

## [1.0.9] - 2026-09-24

### Fixed
- **Status update spam:** Streaming and interstitial updates repeatedly posted identical progress lines. Updated matching to edit existing posts in-place and fold repeated tool cards (`(×2)`).
- **Infinite restatement loops:** Runs repeating the same status without making changes now receive a nudge at 3 repeats and stop cleanly at 6 repeats (`restate_stop_after`).

## [1.0.8] - 2026-09-24

### Added
- **File delivery tool:** Added `send_file` tool to upload and attach local files directly into Mattermost chat.

### Fixed
- **Interrupted turn transcript persistence:** Session history is now written to disk before the first model call, preserving orders across unexpected process restarts.
- **Unfinished turn recovery:** Flagged interrupted turns so subsequent "continue" orders properly resume open tasks.
- **Duplicate tool call refusal:** Canonical argument signature hashing added to ensure repeated identical calls are refused.
- **Downtime catch-up sweep:** Saved high-water post IDs in `state.json` to process unread chat messages arriving during downtime.
- **Per-path file locking:** Resolved canonical file paths across OS styles to eliminate concurrent write races on the same file.

## [1.0.7] - 2026-09-24

### Added
- **Non-root Linux installation:** Added `--mode user` support installing systemd user unit to `~/.config/systemd/user/` with linger enabled.

### Fixed
- **Installer PATH scoping:** Prevented installer scripts from overwriting system PATH wrappers if not pointing to the target install directory.
- **`.gitignore` line endings:** Normalized CRLF line endings that broke git ignore rules for `.env` and `config.json`.

## [1.0.6] - 2026-09-24

### Fixed
- **Search API key retention:** Prevented installer updates from clearing `TAVILY_API_KEY` and `ANYSEARCH_API_KEY` from existing `.env` files.

## [1.0.5] - 2026-09-24

### Fixed
- **Host config retention:** Prevented installer updates from overwriting existing `config.json` with `config.example.json` placeholders.

## [1.0.4] - 2026-09-24

### Fixed
- **macOS uninstaller scoping:** Scoped launchd plist removal to the specific install directory to prevent uninstalling co-located instances.
- **Non-interactive terminal detection:** Fixed installer hanging on token prompts when running without a TTY.

## [1.0.3] - 2026-09-24

### Fixed
- **Initial turn promise guard:** Added retry nudge when a fresh run answers with a promise to do work without making any tool calls.

## [1.0.2] - 2026-09-24

### Added
- **No-admin Windows install:** Defaulted Windows install to `%USERPROFILE%\tinycmdr` with user-level Startup shortcut.
- **Automated Python installation:** Added winget / python.org fallback bootstrap when Python 3.10+ is absent on Windows.

### Fixed
- **UAC path quoting:** Fixed space handling in Windows installer elevation wrappers.

## [1.0.1] - 2026-09-24

### Fixed
- **Installer bugfixes:** Fixed path quoting in Windows launcher and aligned default web dashboard port to 8787 across all platforms.

## [1.0.0] - 2026-09-20

### Added
- **Multi-Interface Architecture:** Unified command set across Interactive Terminal CLI (`tinycmdr`), LAN Web UI dashboard (`tinycmdr web` on port 8787), and background Chat Bot services (Mattermost and Telegram).
- **Prefix-Cache Efficiency:** Static prompt and visible schema footprint optimized to ~4,150 tokens. Dynamic runtime context is tail-anchored to maintain KV cache stability across turns for llama.cpp and vLLM.
- **Autonomous Operations Runtime:** Loop guard, stall watchdog, truthful `/stop` and mid-run steering, persistent task ledger, and spill indexing.
- **Zero-Infrastructure Footprint:** Single-process Python implementation with minimal dependencies, requiring zero external databases or containers.
