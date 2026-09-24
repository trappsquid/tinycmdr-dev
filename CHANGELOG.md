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
- **`install\install-tinycmdr.cmd` no longer asks for elevation on a double-click.** It previously relaunched itself elevated with an unquoted script path, so a username or folder containing a space (`C:/Users/<user>\...`) made the elevated PowerShell exit instantly with `Processing -File 'C:\Users\David' failed`. Switches typed on the command line now survive elevation as well.
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
