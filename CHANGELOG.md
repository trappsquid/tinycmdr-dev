# Changelog

All notable changes to tinycmdr are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.23] - 2026-09-26

One install can no longer take another one's autostart, and a fresh config no longer inherits an
endpoint that does not exist.

Fixed
- A second install silently took the first one's autostart. A launchd label, a systemd unit name
  and a Windows task/Startup name all belong to the USER, not to a folder, so a run that kept the
  default name booted out whatever was already registered under it - and the agent it displaced
  stayed unloaded, which reads exactly like "the bot is gone and its page answers nothing"
  (measured on a fleet macOS host, where test installs sharing the default label left the real
  agent unregistered). All three installers now detect a foreign registration under the name they
  are about to use and refuse, naming the switch to give this install its own: `--label`,
  `TINYCMDR_SERVICE`, `-TaskName`.
- A fresh install kept `config.example.json`'s placeholder fallback (`https://api.example.com/v1`
  with `MY_PROVIDER_API_KEY`, a variable nobody has). It now writes `llm.fallbacks: []` unless
  this run was given endpoints, and an update still keeps the host's own.
- The Mattermost host field accepted anything: a pasted `https://chat.example.com/` was stored
  verbatim in a field documented as the host alone. All three installers split a pasted scheme,
  `user@`, path and `:port` into the host and port keys.

## [1.0.22] - 2026-09-26

Every installer asks the same questions, and every install reports what can reach its page.

Added
- `Add another endpoint?` in all three interactive setups. Each answer becomes an
  `llm.fallbacks` entry (`base_url`, `model`, and an optional `/model` alias) and its key
  goes to `.env` under a generated name that entry's `api_key_env` points at, so a
  fallback's key never lands in config.json. The Windows installer also takes them as
  switches: `-AddEndpoint "<base_url>|<model>|<alias>|<key>"`, repeatable.
- Telegram in the macOS and Linux installers, asked the way the Windows one asks it: the
  token (hidden), your numeric id, and the note that Mattermost wins when both tokens are
  set so the Telegram lane is a second process.
- `Should the page be reachable from other machines on your network?` on all three, and
  the answer is WRITTEN into `web.host` (`0.0.0.0` or `127.0.0.1`) instead of being left
  empty for the build to interpret. Scripted runs set it with `--web-host` / `-WebHost`.
- The installer now reports the address a browser would actually use: after the agent
  starts it probes this machine's own LAN address, not just loopback, and names the reason
  when only loopback answers - `web.host` is `127.0.0.1`, or the host firewall (printing
  the macOS `socketfilterfw` commands or the Windows `New-NetFirewallRule` line).

Fixed
- A fresh Linux install wrote `web.host` as `""`, which the build reads as `0.0.0.0`, while
  the installer's own summary said `127.0.0.1`: the bind address is now explicit, reported,
  and the same on all three platforms.
- The Windows installer overwrote `web.host` with `127.0.0.1` on every run, including an
  update of a host whose page was reachable on purpose. It now only sets what it was told.
- The macOS page report was reachable-loopback-only in appearance: `--no-start` and a page
  bound to every interface looked identical in the output.

## [1.0.21] - 2026-09-26

The installers ask for what a bot cannot run without, and the launcher stops shipping with
Windows line endings.

Fixed
- The macOS installer asked for the Mattermost token and nothing else, so an install from the
  one-line door came out dead: `mattermost.url` left at `chat.example.com`, an allowlist holding the
  example's `REPLACE_WITH_YOUR_MATTERMOST_USER_ID`, `llm.base_url` on loopback and no model key.
  It now asks - before it writes anything - for the Mattermost server, your user id, the model
  endpoint, the model id and, when the endpoint is not on this machine, that endpoint's API key,
  shows a summary, and installs only on `Install now?`. A token with no server address is a refusal
  naming the switch to pass, not an install that exits at its first start.
- The Linux installer asked nothing and installed with the example's documentation endpoint
  (`192.0.2.10`, TEST-NET-1) as its model, so the agent it left behind could not answer a single
  turn. It asks the same five questions before the lane is chosen, refuses a Mattermost token with
  no server, never proposes a placeholder as a default, and the "template default" warning no longer
  fires on `127.0.0.1:8081` - a llama.cpp on the box is a choice, not a leftover.
- The verb was never put on PATH on a Mac: the wrapper was written only when `/usr/local/bin` was
  writable, which on a stock Mac it is not. It now falls back to `~/.local/bin`, adds one marked
  `export PATH` line to `~/.zshrc` when that folder is not already on the path, and the summary and
  the uninstaller both name the real location. The uninstaller removes that wrapper and that line.
- The extensionless `tinycmdr` launcher shipped with CRLF endings in every shape. It is the file
  the PATH wrapper execs, so the verb died on a Mac or Linux with
  `set: -: invalid option` as soon as it resolved. `build-package.py` normalised `.sh` and
  `.command` only; it now normalises any shipped script with a shebang, whatever its name, and
  `.gitattributes` pins the launcher to LF so a Windows checkout cannot put it back.
- A fresh install kept the example's `REPLACE_WITH_YOUR_MATTERMOST_USER_ID` in
  `mattermost.allowed_users` while warning that the list was empty. The placeholder is gone, the
  warning reads the installed file, and neither fires on an install with no chat lane.

Changed
- `-y`/`--yes` and `TINYCMDR_ASK` for both Unix installers: a scripted run asks nothing, and a run
  with no terminal takes the switches and the defaults.
- README and `install/README-macos.md`: the questions, where the verb lands, and a model section
  that no longer claims a cloud default the installer never had.

## [1.0.20] - 2026-09-26

Removing it is now as visible as installing it.

Fixed
- The installed folder carried no removal door. The macOS installer copied the package into the
  install dir but not the two double-clickable `.command` files, so after an install the only way
  out was a script path inside the folder a reader is told to delete. Both doors now ride in the
  install dir.
- Every installer's closing summary named the SOURCE copy's uninstaller - the folder a reader
  unpacks and then deletes - instead of the installed one, and the Windows summary never
  mentioned removal at all.
- `UNINSTALL-MACOS.command` asked for a password on every run, including a user-mode install
  that owns nothing root. It now asks only when a root-owned launcher in `/usr/local/bin` makes
  it necessary.

Changed
- README: a "Removing it" section, one line per platform.

## [1.0.19] - 2026-09-26

The download page stops carrying a version, the Mac stops defaulting to a port of its own, and the
shipped Windows uninstaller stops looking in a folder that no longer exists.

Fixed
- `install\uninstall-tinycmdr.ps1` defaulted `-InstallDir` to `C:\tinycmdr`, the old machine-wide
  default, while the installer it wraps installs to `%USERPROFILE%\tinycmdr`: run with no arguments
  against a default install it found nothing to remove. The default now matches the installer, and
  the header says to pass `-InstallDir C:\tinycmdr` for a `-AsService` install.
- The macOS installer defaulted its web/API port to 8788 while every other platform and
  `config.example.json` use 8787, so a fresh Mac following the README landed on a port the page never
  named. The default is 8787 and the README names the port.
- `maintenance/restart-tinycmdr-macos.sh` still read the pre-rename launchd label
  (`com.trapp.tinycmdr`) and hardcoded 8788 in its restart health check, so `status` and `restart`
  reported "no agent" and "not answering" against a healthy install.

Changed
- `install.sh` is the one-line door for Linux and macOS:
  `curl -fsSL https://github.com/trappsquid/tinycmdr/releases/latest/download/install.sh | bash`.
  It fetches the newest archive, unpacks it, hands the terminal back to the real installer so its
  questions still work, and names the installed copy for later verify/uninstall. Windows keeps
- `install.ps1` is the same door on Windows: `irm .../install.ps1 | iex` (no execution-policy
  change, because `iex` runs the fetched text, not a file). It expands the archive in a temp
  folder, runs `INSTALL-WINDOWS.cmd` there, and names the installed copy for later removal.

  `INSTALL-WINDOWS.cmd`.
- The README's download links are stable names (`tinycmdr-win.zip`, `tinycmdr-linux.tar.gz`,
  `tinycmdr-macos.zip`) that always resolve to the newest release, so the page no longer has to be
  re-pinned at every cut; the versioned names still ship alongside them.
- `maintenance/check-readme-assets.py` fails if a README download name is not in the build or on the
  release, and `maintenance/release.sh` runs a cut (build, publish, aliases, that check).

## [1.0.18] - 2026-09-25

The macOS doors. A reader who is not a terminal user could not install and could not remove
tinycmdr on a Mac, and the removal could die half-way on exactly the installs that had asked for
a PATH wrapper.

### Fixed
- **The macOS uninstall aborted at the PATH wrapper.** The uninstaller removes
  `/usr/local/bin/tinycmdr` with `rm -f` under `set -euo pipefail`. That directory is
  `root:wheel` and not user-writable, so whenever the install ran with sudo (the only way that
  wrapper gets written) the `rm` fails and the shell exits THERE - `rm -rf $INSTALL_DIR` below it
  never runs, and the reader gets no explanation. Measured 2026-09-25 on a fleet macOS host with a
  reproduction of the exact block: `rm: /usr/local/bin/tinycmdr: Permission denied`, exit 1, the
  next step never printed. It is now `2>/dev/null || true` followed by a plain statement of what
  is left and the one line to finish it by hand, so the folder and the launchd job still go.
- **macOS had no double-clickable door.** Windows has shipped `INSTALL-WINDOWS.cmd` from the
  start; macOS shipped `.sh` files only, and Finder opens a `.sh` in TextEdit - so a GUI reader
  had nothing to double-click, for install OR for removal. Added `INSTALL-MACOS.command` and
  `UNINSTALL-MACOS.command` (Finder runs a `.command` in Terminal; both keep the window open and
  print the exit status), added `.command` to the packager's `wants_exec_bit()` predicate and to
  `lf_only()` so the pair ships executable and LF, and documented both in `README.md` and
  `install/README-macos.md` including the quarantine note for a browser download.

## [1.0.17] - 2026-09-25

The launcher nobody could run, and the screen three writers were painting. Every item below was
measured on the fleet reading a live host, not inferred, and every one of them is the same shape:
the capability existed and the hand-off to the human did not.

### Fixed
- **The `tinycmdr` launcher shipped without its execute bit.** The door a reader types first
  answered `.../tinycmdr: Permission denied` - for the user AND for sudo, because execve wants one
  execute bit set for every user. The tree tracked it as 100644 (a Windows checkout cannot record
  the bit and ignores fileMode), the macOS installer landed it with `cp -f` and never chmodded it
  (the Linux installer does), and every git-based update - now the only update door - wrote the bit
  back off. Fixed at four points: the git index mode, the macOS installer, a shared
  `wants_exec_bit()` in the packager (the old `.sh`-only predicate could never match a file called
  `tinycmdr`), and `ensure_launcher_executable()` after every pull and adoption. All three archives
  now print the launcher's mode and the build REFUSES when it is not executable - a live defect the
  new gate caught in the packager itself while this release was being cut.
- **The console screen had three writers.** The logging setup attached a console StreamHandler
  unconditionally, so every INFO line printed into the middle of prompt_toolkit's render;
  `CliDestination._write` used a plain `print()` while the screen owned the terminal, so the
  toolbar smeared into the transcript and the done line was left stranded; and a streamed draft
  that WAS the answer stayed the dim "..." narration line while the answer card was skipped.
  `TuiScreen.raw_ansi()` was written for exactly that text and nothing in the program ever called
  it. One writer per terminal now: a console that takes the screen detaches the log handler, and
  every console line goes through the screen.
- **A run that made no tool call reported `Done - 0 step(s)`** while its reply only described work
  that had not started (measured on two fleet hosts in one afternoon). The done line now says the
  run used no tool, and any run that was nudged to act and still ended on an intention carries the
  truth in the delivery.
- **A bare action phrase ended a run as an answer.** "Checking where loft boxes is located on this
  machine." (53 chars) and "Finding <folder> folder:" (27 chars) matched neither `_INTENT_RX` nor
  `_RESULT_CLAIM_RX`, so the classifier called them answers, no guard fired, and the run closed at
  0 tool calls behind a green line. They are a `fragment` now: same fences as the promise guard (no
  tool call yet, once per run), a 300-char cap, and a DIGIT test that keeps a capable model's real
  answer - "Looking at your disk, 63GB is free..." - out of the class.
- **`remember` glued a new entry onto the previous line** when `notes.md`'s last line carried no
  terminator, so two facts read as one in every later prompt. The append checks the last byte now.
- The MacBook's `web.port` is 8787 again: the Hermes web UI that claimed 8787 there no longer
  exists, so the exception outlived its cause and the operator, reading the fleet's habit, tried
  8787 and found a dead door.

### Notes
- Every guard in this release is runtime-only: zero prompt bytes, no schema change, no new rent.
- Falsifiers: the new checks fail precisely on the pre-fix build. `test_verbs` prints
  `FAIL the update path ships a launcher fix-up`; `test_tui` prints the rogue
  `<StreamHandler <stderr>>` in its own failure output; `test_stall` fails exactly the five
  fragment checks and passes the false-positive control; `test_ledger_race` reproduces the glued
  line verbatim.
- Suites at this cut: `test_stall` 316, `test_checkin` 196, `test_ledger_race` 41, `test_tui` 39,
  `test_verbs` all green; full sweep 45/45, SWEEP_FAIL=0.

## [1.0.16] - 2026-09-25

The tool index: a growing `tools/` folder no longer buys prompt tokens. The always-on schemas
were already flat (7,828 ch over 14 tools at 0/5/10/20/40/80 tools), but the custom-tool block
put a full DESCRIPTION line per tool into the STATIC prompt - measured with
`tests/tool_index_scale.py`: 167.8 chars / 49.4 est-tok PER CUSTOM TOOL, unbounded. 80 tools
took the prompt from 2,920 to 6,870 est-tok on every call (+16 s of prefill at the LAN box's
measured ~240 tok/s, ~+35 s at 200 tools) before the run did anything. The prompt now carries
the skeleton - a category per line, the names on it - and the prose is one call away. On a box
with 9 custom tools the static prompt drops 11,432 -> 10,381 ch (-263 est-tok per call) with
the disclosed schema block byte-identical; at 80 tools the index costs 5.9 ch per tool instead
of 167.8, and 300 tools render inside the caps. A/A both ways: `tests/aa_payload_floor.py`.

### Changed
- **The custom-tool block is a CATEGORY INDEX, not a description per tool.** Every custom tool
  is still NAMED there (a name the model cannot see is a capability it does not have: the
  pinned-`core_tools` drive measured 22 calls and 194.8K prompt tokens spent chasing a hidden
  `send_file`), grouped onto one line per shelf the operator would say out loud - `files &
  edit`, `web & publish`, `checks & probes`, `tools & runbooks`, `messaging & chat`, `sessions
  & memory`, `agents & jobs`, `system & shell`, with `other` last.
- **A shelf is DERIVED when a tool declares none**, from its name first and its description
  second. The name decides because a description is prose: `shell`'s own blurb ends
  "background to a file and poll it", and one haystack of name+description filed the shell
  tool under files & edit.
- **`list_tools` answers with each custom tool's shelf and its one-line description.**
  Measured driving this build on a fleet box: asked what its added file/drive tools do, the run
  called `list_tools` and then read EIGHT tool files (three of them twice) for what one answer
  says. The prompt had stopped carrying that prose, so the door the model actually calls now
  carries it - capped exactly like the index (12 blurbs, then `... +N more`).
- **`find_tools` answers a category.** `find_tools {"category": "files"}` resolves the shelf
  (a shorter word for it works), names that shelf's tools with what each does, reveals NOTHING
  (a reveal is per-session schema rent that calling the tool by name pays anyway), and an
  unknown category answers with the real ones instead of guessing.
- **`tools/README.md`** documents the shelf an author may declare (`CATEGORY = "..."` at module
  level in a `.py`, `"category"` in a `.tool.json`) and the index that carries it.

### Added
- `agent.tool_index_max_categories` (12) and `agent.tool_index_max_names_per_line` (12), in
  `DEFAULT_CONFIG` and `config.example.json`. The block is bounded by CATEGORIES rather than by
  tools, and a capped line renders its overflow as `... +N more (find_tools {"category":
  "<cat>"})`, so a 500-tool box renders like a 9-tool one. No per-tool authoring is required
  for the tools already installed.
- `tests/tool_index_scale.py` now gates the LIVE tree too (this repo's own `tools/`) beside the
  scale table: every name present, no description prose, every shelf resolvable, the block
  under 400 ch, and the flat-schema invariant at every tool count.

### Notes
- The dirs' own numbers moved with this (docs re-baselined in the same batch): the README's
  fixed-overhead figure and `docs/tinycmdr-what-it-is.md`'s "3,469 tokens on a clean unpack"
  and "about 250 per custom tool because it carries a schema".
- Nothing is pushed by this entry: the tree, the dist shapes and the fleet stay where they are
  until the operator says otherwise.

## [1.0.15] - 2026-09-25

Six invented daily-work orders (a status sheet to attach, a folder to clear, a scan hunt, a
reboot forensics question, a slow-machine look) were driven at a macOS box, a sensor box and a
Windows box, each graded from that host's own journal, its carry sidecar, its log turn lines and
the state read back afterwards. Everything below is a measurement from those runs; the batch
adds ZERO prompt bytes and ZERO schema bytes (A/A on one staged install: prompt 9,929 ch,
schemas 7,856 ch, 14 visible tools, identical before and after).

### Fixed
- **A pinned `agent.core_tools` list silently drops tools added to `_DEFAULT_CORE` later, and the
  failure is a spin, not an error.** One host pins its always-visible list; the pin predates
  `send_file` and `search_files`, so told to attach a file the run spent 22 calls, 114 s and
  194.8K prompt tokens echoing `echo "calling send_file now"` in the shell SIX times before
  reporting the failure honestly - while a host on the build's defaults attached it in 4 calls.
  The startup capability line now names any default tool a pinned list is missing.
- **A capability phrase reveals the tool that serves it.** An operator asks for a capability
  ("attach it, do not just paste"), which names no tool, so the name-driven reveal never fired.
  `send_file` is now revealed by the phrasings a person actually types, and so is a tool the
  model is NARRATING in an echo - a tool name inside an echo is never the command's job.
- **A generation request against the model endpoint this bot talks to asks first.** An order
  about a slow machine made a run send real completion requests to the production box (a bogus
  model name, then `main` at 400 + 400 + 120 tokens - ~900 generated tokens and two slots of
  load) while that run was itself using the box to think, and quoted the resulting 90 tok/s as
  its finding. `endpoint_self_harm` covered RESTARTING that box; the new check covers LOADING
  it, on the shell, `execute_code` and the drop-in `process` door. Reads stay free: `/props`,
  `/metrics` and `/v1/models` are not gated.
- **A shell write to the bot's own memory asks first.** The measured indirect-injection run
  ended with `printf 'notes cleared by cleanup' > notes.md` and did it: its whole memory
  replaced by a line from a file it had been asked to read. `notes.md`, `tasks.json`,
  `tasks.md`, `atlas.md` and `field-notes.md` are now a confirm tier for WRITES only.
- **`read_file` says so when a file's text reads like instructions.** The same run executed all
  four steps of a note it found inside the folder it was clearing - a canary, the operator's own
  file in that folder, a copy to the Desktop, and its own memory rewritten - while the prompt
  already said file text is data. The result now carries a `[HARNESS: ...]` line at the place
  the model reads it. Two signals, both narrow: an injection phrase, or a numbered step list
  where two steps carry a path and the file carries a shell verb. A changelog with numbered
  items and paths is NOT annotated.
- **`remember` superseded short notes.** `notes_supersede_share` was measured on containment,
  which is degenerate on a short note: "fact 1" and "fact 2" each reduce to `{"fact"}`, so share
  read 1.00 and eight distinct facts collapsed into one - reported by this repo's own suite
  against the 1.0.14 build (`test_ledger_race` 35 passed, 1 failed). Superseding now needs a
  minimum shared vocabulary on BOTH sides (`agent.notes_supersede_min_words`, 5).
- **`write_file`'s CRLF warning was false for `.ps1`.** Measured on a fleet Windows box: an
  LF-only `.ps1`, `.cmd` and `.bat` all RAN, including a `.cmd` with an if/else block and a
  goto/label - so "it will not run" cost 2-4 calls per script as the model rewrote bytes that
  were already runnable. The flat warning is gone; `.cmd`/`.bat` get one narrow note about
  cmd.exe parsing labels and parenthesised blocks.
- **A redundant `powershell -Command` wrapper is unwrapped instead of run twice.** The shell
  already IS PowerShell on Windows, so the inner interpreter re-parsed text that had been
  through one round of quoting: 3-4 failed calls per run in both Windows orders ("System : The
  term 'System' is not recognized"), after which the run fell back to writing a `.ps1`.
- **`config.example.json` was missing the `robocopy /MOVE` confirm pattern** that the code and
  the 1.0.14 changelog both carry, and every installer writes a new host's config.json from it -
  so a fresh install shipped without the gate. Restored, and the packager now refuses a package
  whose example tiers disagree with `DEFAULT_CONFIG` (the suites read `tests/fixture-config.json`,
  which holds zero patterns, so nothing else could see it).

### Added
- `maintenance/build-package.py` prints the tiers check with the other package gates.
- `tests/test_config_example.py` pins the example against the code, and falsifies itself on a
  copy with a pattern deleted.

## [1.0.14] - 2026-09-25

Six orders typed the way a non-technical operator actually types them ("this thing has been realy
slow", "i think iv lost a file", "clear out the junk for me") were driven at a fleet box and graded
from that box's own journal. Everything below is a measurement from those runs, not a theory.

### Fixed
- **`/new` cleared the conversation but kept the rent.** A `find_tools {all: true}` took a session
  from 14 tool schemas to 30, and every later turn - INCLUDING a fresh session that had just been
  told "Session cleared. Fresh context." - carried ~3.4K extra prompt tokens (step-0 prompt_tok
  6,505 -> 10,086 on the same order). `AGENT.reset` now drops the session's reveals, so a cleared
  conversation starts at the floor again.
- **The spill index was process-wide and survived `/new`.** The index of oversized tool results
  rides every prompt, so one conversation's spilled output - its first line and its path - was put
  in front of every OTHER conversation's model, and it outlived a reset: measured, a fresh order
  ("how mutch room is left on the c drive thing") was answered in two calls and then spent ten more
  reading the PREVIOUS, stopped run's spill files and re-running its printer/LAN scans. Spills are
  now keyed by session, `spill#<id>` resolves inside the session that made it, and a reset drops
  that session's pointers while the files stay on disk.
- **The confirm tier read PROSE in a file as a command.** `\breboot\b` gated three writes in ONE run
  over the words in a script's own section header ("# ---------- REBOOT / UPDATE STATE ----------"):
  a 300s stall, a declined write, and a rewrite - while the same run's actual destructive act, a
  `robocopy /MOVE` of a 194-item directory, matched nothing in either tier. Writes now take a
  CONTENT tier (`agent.confirm_content_patterns`): the machine verbs fire only where they stand as
  a command, and `/MOVE` joins the list because it deletes the source tree.
- **`remember` stacked near-duplicates.** The reply NAMED the older entry and suggested the replace
  call; the model re-issued the identical note instead, the repeat guard folded it, and the file
  kept two entries for one fact - the char budget paying twice, forever. A new note that shares
  `agent.notes_supersede_share` (0.85) of its words with an existing one now supersedes it in
  place and says so. The 0.7-0.85 band still asks, because only the model knows if it is the same
  fact said differently.
- **The check-in's memory gauge read `RAM 0.0 GiB` on a healthy process.** MiB was formatted as GiB
  with one decimal, so a lean 32 MB child - exactly the healthy case - rendered as a failed probe.
  Under 1 GiB it reads in MiB now.
- **A PowerShell property that does not exist is silent, and $null in arithmetic is 0.** Measured:
  `$sys.FreeMemory` (the real name is `FreePhysicalMemory`) made a run report "0 MB free RAM" as its
  ROOT CAUSE while the box had 18 GB free - exit code 0, no warning, nothing to read as wrong.
  `agent.shell_strict_mode` (Windows, OFF by default) runs inline PowerShell under
  `Set-StrictMode -Version 2.0`, which fails the read instead. It ships off because the same
  measurement showed version 2.0 ALSO errors on a read of an unset variable and adds stderr noise to
  the everyday `Get-ChildItem | Where-Object { $_.Length -gt 1MB }` idiom (right answer, new noise):
  it is a choice for a box you diagnose, not one you operate. Turn it on per host.

### Added
- **One line on a long run, once, with the verb that ends it.** Five of the six driven orders ran
  30-58 tool calls over 17-20 minutes and the only signal an operator got was the tool lines
  themselves; two were still hunting when a `/stop` arrived. Past `agent.scope_note_steps` (40) the
  check-in adds how many calls the run has made and that `/tinycmdr stop` ends it. Zero prompt
  bytes: nothing here reaches the model, and it is silent on a lane with nobody reading it.

### Changed
- `config.example.json` documents the four new keys: `shell_strict_mode`,
  `confirm_content_patterns`, `notes_supersede_share`, `scope_note_steps`.

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
