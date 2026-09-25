# Changelog

All notable changes to tinycmdr are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.12] - 2026-09-24

### Fixed
- **Prior-run false interruption alerts:** Active turns were incorrectly flagged as interrupted because `_prior_run_unfinished()` evaluated the in-flight user message. Fixed by ignoring the active user turn during live execution.
- **Empty-ledger task error:** Calling `task action=done` without an ID when no tasks were active returned contradictory `no task #None`. Fixed with clear message indicating no active tasks.
- **PowerShell 5.1 command chaining with `&&`:** Windows PowerShell 5.1 rejected `&&` command separators. Added quote-aware translation to `; if ($?) { ... }` in `tool_shell`.

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
