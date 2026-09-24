# Changelog

All notable changes to tinycmdr are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
