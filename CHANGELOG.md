# Changelog

All notable changes to tinycmdr are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.8] - 2026-09-24

### Added
- **The agent can hand you a file.** A new `send_file` tool attaches a file from the machine into the chat it is answering in (`path`, plus an optional one-line `note`) and reports what actually happened: `sent: clip.mp4 (9,212,317 bytes) is now in this chat`. The harness could read and write files and had no way to deliver one, so an order to "send it here in chat" was impossible; a run asked to do it went looking for a way in (token files, `docker ps`, the chat API) for a file that had been on disk the whole time, and ended on a promise instead. One file per call, refused above `agent.send_file_max_bytes` (50 MB default) before the bytes move, and a lane that cannot carry a file (the console, a job with no destination) says so rather than pretending.

### Fixed
- **A conversation survives an interruption.** The session transcript was written only when a run ended, so a process killed mid-run - an update, a restart, a crash - took the operator's own message with it: the next run opened with an empty history and could only ask what "the task" was, while the tool results it had already produced were still on disk. The transcript is now written before the first model call.
- **"Continue" means resume.** A run that never answered the order it was given (an interruption, or the turn limit) is a named state now, and the single line a run after a wreck carries says what it is for: resume that unfinished work when the message asks to continue, leave it alone when the message asks something else. The turn limit is recorded like every other end, so that state is never invisible either.
- **A repeated tool call is refused, and a refusal that comes back stops the run.** The duplicate guard looked up its map by the raw argument string while entries were stored under the canonical signature, so a re-formatted identical call ran again while the loop counter watched it happen - one directory listing really ran three times inside a single chat turn. Both guards read one signature now, and a second refusal of the same call with nothing changed ends the run with the report it has.
- **An order posted while the bot was down is no longer dropped.** The catch-up sweep that exists for that case walked a high-water map each new process started empty, so it had no channel to ask about, and a post made inside the downtime never arrived over the websocket either. The map is carried in `state.json` now, together with the ids of the posts already handled, so a restart recovers what it missed and never replays what it answered.
- **One file, one lock.** Per-path locking keyed on the string the caller passed, so a Unix-style and a Windows-style spelling of one path took different locks and two edits of one file in a single batch could run at once: both reported success and one edit was silently lost. The key is the file now (expanded, resolved, case-folded where the platform does that).
- **A report with no tool call behind it is asked once to make the call.** A run that answered with a filled-in result - counts, contents, a hash - without running anything had the fabrication delivered as its answer, because the promise guard wants a stated intention and the evidence check wants a change verb, and a measured value is neither. A true-from-memory answer ("16 GB unified memory") still stays quiet: a bare quantity is not a claim about anything the run fetched.

## [1.0.7] - 2026-09-24

### Added
- **The Linux installer can install without root.** It now has two shapes, and asks which one you want when you run it at a terminal (`--mode system|user` decides without a prompt, `--yes` takes the default for who you are):
  - **system** (root): a unit in `/etc/systemd/system`, enabled at boot, passwordless sudo for the agent, verb in `/usr/local/bin`. This is what a fleet push gets, unchanged.
  - **user** (no root at all): a unit in `~/.config/systemd/user`, started at login through your own systemd instance, the verb in `~/.local/bin`, no sudo grant anywhere, and a note in `notes.md` stating that the agent has no sudo so it does not repeat a wrong "no root here" for days.
  Lingering (`systemctl --user` surviving logout) is enabled when allowed and the exact `sudo loginctl enable-linger <user>` command is printed when it is not. `sudo bash install-tinycmdr.sh --mode user` installs it for the invoking user without leaving anything root-owned.

### Fixed
- **A scoped install no longer overwrites a working install's PATH verb.** The wrapper is only written when the existing file already points at this install dir, the same rule the macOS plist and the sudoers file follow.
- **`.gitignore` had a doubled carriage return on every line, so no pattern matched.** `.env` and `config.json` showed up as untracked in a public repo; one `git add -A` would have published the bot token.
- **The shipped CLI README claimed a version that does not exist** ("From 1.0.7 a window this build owns stays open"), in the package readers actually download.
- **The GitHub repo description still said "Fast TTFT"** after the same claim was removed from the README.

## [1.0.6] - 2026-09-24

### Fixed
- **An update no longer drops the host's search keys.** The same file-survival bug as 1.0.5, one file over: `TAVILY_API_KEY` and `ANYSEARCH_API_KEY` were treated as installer-managed, so a run without a `--secrets-file` / `-SecretsFile` rewrote `.env` without them and web search went dead on a host that had them. They are the host's own keys; a secrets file still supplies them when the host has none, and takes precedence when it does.

## [1.0.5] - 2026-09-24

### Fixed
- **An update no longer overwrites the host's `config.json`.** All three installers rebuilt that file from the package's `config.example.json` every time they ran, so re-running the installer - and in particular an in-place update with `--force` / `-Force` - replaced a working install's settings with the example's placeholders: `mattermost.url` went back to `chat.example.com`, `allowed_users` came out empty, and the model endpoint moved to the loopback/cloud default. The bot then refused to start. Measured on the macOS bed 2026-09-24: an update to a newer release left a bot that could not connect, and its config had to be restored by hand. The host's own `config.json` is now the base whenever there is one, and each writer applies only what the run was actually told to change (a switch that was not passed no longer blanks a working value).

## [1.0.4] - 2026-09-24

### Fixed
- **The macOS uninstaller no longer takes another install down with it.** `--uninstall` booted out the launchd agent and deleted the plist even when that plist belonged to a DIFFERENT install, and the default label is shared: removing a probe install (`--install-dir /tmp/...`) stopped the box's real agent and deleted its plist. Measured on the macOS bed 2026-09-24 - a probe uninstall took a live bot offline. The plist is now removed only when it names the install directory being removed, the same scoping the PATH wrapper and the Linux cleanup already had.
- **The macOS installer no longer dies at its token prompt when nobody is there to answer it.** `read` returns non-zero at end-of-input, and under `set -euo pipefail` that killed the installer the moment stdin was not a keyboard: it stopped silently right after printing the prompt and left a half-copied folder behind, which then refused a retry without `--force`. A token-less run is a supported install - the agent serves its local page - so the prompt is now asked only when there is a terminal, and the run carries on to that lane.

### Changed
- **The Linux installer says so when no model was chosen**, instead of printing a blank model name in its summary. The reference config keeps the example's placeholder id, so an install that was never told which model to use now points at `llm.model` in `config.json` (or `--model <id>`).

## [1.0.3] - 2026-09-24

### Fixed
- **A run no longer ends on a promise.** When the model replied with what it was about to do and made no tool call at all ("I'll gather what we did in the previous session, then write and publish the post. Let me start by checking the carried results..."), the harness delivered that promise as the run's answer and stopped, so the task never started and every "continue" produced another promise. Measured on the macOS bed: four consecutive runs, one model call each, zero tool calls, nothing done. The run now takes one more turn with the model told to make the first tool call instead of describing it. It is bounded to a single retry, and it only fires when the run has made no tool call at all, so a report that follows real work is never touched.

### Documentation
- The download commands in the README are the standard download-extract-run shape, and they now change into the folder the archive actually extracts to before running the installer.

# Changelog

All notable changes to tinycmdr are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.2] - 2026-09-24

### Changed
- **Windows installer installs into your own profile and needs no administrator rights.** The default target is now `%USERPROFILE%\tinycmdr` instead of `C:\tinycmdr`, the dependencies go into a virtual environment inside that folder, and the agent starts at logon through a shortcut in your Startup folder. Nothing outside your profile is written, so Windows has nothing to elevate for. `-InstallDir` picks another folder; `-AsService` still registers the old boot-start scheduled task for hosts that want one (that switch alone needs an elevated shell, because Windows reserves boot-start tasks for administrators).
- **Windows installer fetches a missing Python by itself.** A machine with no Python 3.10+ used to end in `FAILED: no Python 3.10+ found`. It now installs Python 3.12 automatically (winget first, the python.org installer as a fallback), refreshes `PATH` for the rest of the run, and carries on.
- **Dependencies are checked, then installed, then verified.** The installer records which of `requests` / `mmpy_bot` / `croniter` are missing, installs only those into the install's own virtual environment, bootstraps `pip` with `ensurepip` when a Python has none, and rebuilds nothing on a re-run when the environment is already good.
- **A no-chat-account install now starts the page lane.** The hidden launcher tells the supervisor `--web` when the install has no chat account, so it serves the local page instead of respawning a bot that refuses to start without a token.

### Fixed
- **`install\install-tinycmdr.cmd` no longer asks for elevation on a double-click.** It previously relaunched itself elevated with an unquoted script path, so a username or folder containing a space (`C:\Users\<user>\...`) made the elevated PowerShell exit instantly with `Processing -File 'C:/Users/<user>' failed`. Switches typed on the command line now survive elevation as well.
- **A scripted install no longer hangs on a token prompt.** The Mattermost-token fallback prompt was gated on `-NoPause` (which the `.cmd` wrapper always passes) instead of on whether a human is present, so a `-NonInteractive` run waited forever for input.
- **Linux installer matches the rest of the project on the page port.** `install/install-tinycmdr.sh` defaulted its web fallback to 8788 while `config.example.json`, `tinycmdr.py` and the Windows installer all default to 8787. Its default install folder now resolves through `getent passwd`, so a home directory that is not `/home/<user>` is correct instead of guessed. `tinycmdr-supervise.py` is copied too, so the shipped watchdog is present on a fresh Linux install.

### Documentation
- README install section now links each platform's release download directly and gives the download-and-run commands for Windows, Linux and macOS.

## [1.0.1] - 2026-09-24

### Fixed
- **Windows Installer:** Quoted script invocation path during UAC elevation in `install/install-tinycmdr.cmd`. Resolves elevation failures when run from paths or usernames containing spaces.
- **Linux Installer:** Aligned default web dashboard port to 8787 in `install/install-tinycmdr.sh` (matching `config.example.json` and `install-tinycmdr.ps1`). Added dynamic home directory resolution via `getent passwd` for root and custom user setups. Added `tinycmdr-supervise.py` to the installation copy manifest.

### Changed
- **Documentation:** Restructured `README.md` to lead with core features, prefix-cache stability, ops runtime architecture, and unified interfaces. Replaced speculative latency phrasing with grounded explanations of prefix cache reuse.

## [1.0.0] - 2026-09-20

Initial public release of tinycmdr, the high-efficiency agent harness built for local and self-hosted LLMs.

### Added
- **Multi-Interface Architecture:** Unified command set across Interactive Terminal CLI (`tinycmdr`), LAN Web UI dashboard (`tinycmdr web` on port 8787), and background Chat Bot services (Mattermost and Telegram).
- **Prefix-Cache Efficiency:** Static prompt and visible schema footprint optimized to ~4,150 tokens. Dynamic runtime context is tail-anchored to maintain KV cache stability across turns for llama.cpp and vLLM.
- **Autonomous Operations Runtime:**
  - **Loop Guard:** Detects repetitive tool-call cycles, refuses repeat calls, and automatically resets upon filesystem state changes.
  - **Stall Watchdog:** Background monitor that flags stalled turns and releases wedged inference slots.
  - **Truthful Stop & Steer:** Real-time mid-run steering and a 3-state truthful `/stop` command that immediately releases server slots.
  - **Persistent Task Ledger:** File-backed task management (`tasks.json`) preserving multi-turn objectives across disconnects and restarts.
  - **Spill Indexing:** Offloads tool outputs exceeding size limits to `spill/` with compact disk pointers (`spill#N`).
- **Zero-Infrastructure Footprint:** Single-process Python implementation with minimal dependencies (`requests`, `croniter`, `mmpy_bot`), requiring zero Docker containers or external databases.
- **Extensibility:** Hermes-compatible prose skills (`SKILL.md`) loaded on demand (~23 tokens index rent), hot-loaded Python/PowerShell custom tools (`tools/`), and self-authoring tools (`create_tool`).
- **Platform Installers:** One-step installer scripts and service definitions for Windows (Scheduled Task), Linux (systemd unit), and macOS (launchd).
