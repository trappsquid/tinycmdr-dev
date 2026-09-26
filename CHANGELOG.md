# Changelog

All notable changes to tinycmdr are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.24] - 2026-09-26

Phase 1 of the 2026-09-26 audit: the request envelope becomes a measured quantity instead of a
guess, a failed write can no longer destroy the file that was there, the guard tiers cover what
they claim, a fresh install completes on all three platforms by the documented path, and a slow
box stops paying for the harness's own impatience. `python tests/run_all.py` is the gate; each
entry below names the suite that pins it and the measurement that proved it.

The installer half of this release lands ON TOP of 1.0.22/1.0.23 rather than replacing them:
the questions those releases added (whether the page should be reachable from your network,
"Add another endpoint?", the Telegram lane, the Mattermost host:port split) and their guard
against taking a registration another install already owns (a launchd label, a systemd unit
name, a Windows task or Startup name) are all still here. What the installers lose on the way
is the page token echoed into the transcript and into the install log, a `config.json` that
shipped world-readable, and `chown user:user` - each described below with what it used to cost.

### The envelope (the premise)

Fixed
- The context budget was `max(4000, window − 7000 − max_tokens)` with the static half subtracted
  from nothing, and `max_tokens` sent unclamped. Measured: an 8,192-token endpoint received a
  9,275-token payload and a 16,384-token completion request — the request could not fit by
  construction, and the "budget" was a floor rather than a measurement. The static overhead
  (system prompt + the tool schemas the session actually sends) is now counted, the reply is
  clamped to `min(llm.max_tokens, window // 4)` (2,048 at an 8k window), and the messages budget
  is `max(1024, window − static − reply)` — 19,254 at 32,768 where the old arithmetic gave 9,384.
  `REPLY_HEADROOM` is gone; there is one computation (`Agent._envelope`) and `_compact`,
  `_force_shrink` and the request path all read it.
- A window below 8,192 is now REFUSED with the arithmetic on stdout (window, static, reply,
  budget) instead of sending a request the endpoint has to reject; below 16,384 it runs and says
  so. `llm.max_context_tokens` still overrides, and an endpoint that reports nothing falls back
  to the configured number, then to a conservative assumed window that the log names.
- The budget is measured against the CONVERSATION (`_conversation_token_est`, every message
  except the system prompt), not the whole payload: `static` already counts the system prompt,
  and counting it again made every compaction decision 3,012 tokens optimistic about what it had
  freed.
- Memory limits follow the WINDOW, not the model name: notes, fetch and tool output are
  `min(configured, window // 8)` and `history_exchanges` scales by the same envelope. Measured:
  an 8,000-char notes block on a 16,384-token window used to be most of the payload.
- `status`, `doctor`, `health` and the CLI banner print the five numbers
  (`window · static · reply · budget · remaining`), so the next report like this one is one line
  of output. `doctor` exits non-zero when the endpoint is below the minimum.
- A NUMBER in `llm.max_context_tokens` is still a CEILING on the messages budget: the envelope
  takes the tighter of it and what the endpoint serves, so a box restarted into a bigger window
  cannot raise a limit the operator set. (The first cut of this change let the window win
  outright; caught by the ledger suite.)
- `ps aux` is digested. It was the one process listing that slipped through the digest shapes
  (which required a dash or a pipe after `ps`) — measured: 190,499 chars / 56,029 estimated
  tokens rode into the context whole; it is 41 lines and ~2,072 tokens now. A bare `ps` counts
  only at the start of a command or after an operator, so `grep -i ps file` is untouched.

### Durability

Fixed
- `atomic_write_text`'s error handler fell back to a PLAIN write, so a denied rename, a full
  disk or a locked target turned the surviving file into a zero-byte or half-written one — the
  loss the function exists to prevent, committed by its own handler. It now retries under a
  second sibling temp name and then raises, leaving the previous file byte-identical and telling
  the caller it failed. Measured on the 20-item ledger: intact, and the failure surfaces.
- Every write preserves the file's mode (`fchmod` of the temp to the destination's mode, 0600 for
  a new file), so a secret cannot become world-readable on its way to `.env`, and a 0600 state
  file stays 0600.
- The remaining non-atomic writers go through the same door: tool output spilling, the procedure
  census (whose fixed `.tmp` name two concurrent writers shared), `update` replacing the live
  build, and `create_tool`'s file. `remember`'s rewrite is atomic; a failed replace leaves
  `notes.md` untouched.
- A `tools/*.py` that calls `sys.exit()` at import no longer bricks the install. `SystemExit` is a
  `BaseException`, so the loader's `except Exception` never saw it and the process died silently
  at the next start (`printf 'import sys\nsys.exit(3)' > tools/evil.py`). The loader now catches
  `BaseException` (re-raising a stop), names the type in the log, and `create_tool` removes the
  file it just wrote when the reload refuses it.

### Guards

Fixed
- The POSIX recursive deletes had no coverage in either tier: `rm -rf /etc`, `rm -r -f /`,
  `rm --recursive --force /`, `rm -rf ~/Documents`, `find / -delete` and
  `find / -exec rm -rf {} +` all ran ungated, because the only patterns required `r` and `f` in
  one flag word immediately before a bare `/`. The recursive-delete rule now reads the FLAGS in
  any order and spelling and the TARGET: a whole tree is refused, a named directory is confirmed.
- The false positives are gone: `dd if=/dev/zero of=/dev/null bs=1M count=100` (a measuring
  stick, not a disk), `ls /sbin/mkfs*` and `grep -rn mkfs` (inspecting the tooling) were all
  refused by the unanchored `\bmkfs\b` and `of=/dev/` patterns.
- The Windows machine-verb class the audit measured — `taskkill`, `diskpart /s`, `takeown`,
  `icacls`, `net user … /add`, `New-LocalUser`, `schtasks /delete`, `Set-ExecutionPolicy`,
  `Stop-Service`, `Stop-Process`, `reg delete`, `Clear-EventLog`, `wmic shadowcopy delete`,
  `git reset --hard`, `git clean -xfd` — is in the confirm tier now. `git clean -xfd` is there
  because the agent runs inside its own checkout, where `.gitignore` covers `.env`, `sessions/`
  and `notes.md`.
- PowerShell aliases and short forms are covered: `ri -r -fo C:\x`, `rm -r -fo C:\x`,
  `gci C:\x | ri -Recurse` are gated, and `-e`/`-ec`/`-EncodedCommand` is refused only with a
  base64-looking argument — `echo 'this note mentions -EncodedCommand'` is allowed again.
- `write_file`/`edit_file` on the bot's OWN files (`notes.md`, `tasks.json`, `tasks.md`,
  `atlas.md`, `field-notes.md`) go through the same decision the shell door gets; measured, a
  tool call replaced `notes.md` with no gate at all while `printf … > notes.md` was stopped.
- A `.tool.json` manifest's command walks the whole shell tier (it only met the content tier
  before), and an absolute-tier command is refused at LOAD, so a dropped-in manifest cannot
  carry one into the box.
- A `config.json` that REPLACES a shipped guard list can no longer silently downgrade the tiers:
  the shipped lists carry a version (v3), `<list>_extra` extends instead of replacing, the log
  names every pattern the box is not enforcing, and `doctor` exits non-zero on it.
- Endpoint error bodies and the run's recorded attempts are scrubbed before they are stored or
  printed, so a provider that echoes the API key in its 401 body cannot put it into the fatal
  notes, the usage footer, the log or the chat.
- The local page refuses a request whose `Host` is not loopback/configured or whose `Origin` is
  not same-origin (a page on any site could POST to a loopback-only page with no token — CSRF
  against shell access), reads the body under an absolute deadline, and rebinds a port inside
  the kernel's TIME_WAIT window on POSIX while `web_busy_note` stops claiming the port is free.

### Turn engine

Fixed
- The idle timer covered the PREFILL, so a healthy long prompt was declared wedged. The first
  byte is now bounded by `request_timeout` (that is the model thinking) and
  `stream_idle_seconds` applies only BETWEEN chunks.
- A stream that died after the first delta was handed back as the model's complete answer,
  because the reader-error check only fired when nothing had arrived at all. A reader error is
  fatal unless a terminal chunk or `[DONE]` arrived; the caller's same-endpoint retry handles it.
- Streamed tool calls without an `index` were all keyed on `0`, so two distinct calls MERGED —
  a run that asked for `echo A` and `echo B` executed `echo AB`. Fragments are keyed by `index`,
  else by `id`, else a new slot when a fragment starts a call; a byte-identical repeated fragment
  is no longer appended twice.
- A 400 that names `stream_options` or `chat_template_kwargs` (a provider saying "unknown
  argument") used to be classified FATAL, so a cosmetic difference killed the run. The field is
  dropped and the same endpoint is retried once.
- `llm.allow_cloud_fallback=false` now covers the whole failover chain, the primary included: a
  hosted `llm.base_url` was reached as the failover for a privacy-pinned local choice. The
  CHOSEN endpoint (the config primary, or the model `/model` pinned) still routes where it says.
- Locality is classified by resolution: `127.1`, `[::1]`, `0.0.0.0`, `*.local` and a LAN
  hostname (resolved to RFC1918) are local now, so failover and `stream_options` work on exactly
  the boxes this build targets; an unresolvable name stays remote.
- `OperatorStop` raised during TOOL execution escaped `Agent.run()` (only the model call had a
  handler) and every lane but Telegram/CLI catches `Exception`, so the answer was never posted,
  the progress line stayed open and the queued message was dropped. The turn now catches it and
  answers with the stop notice, and the lane boundary catches `BaseException`.
- The repeat guard's signature hashes the full canonical arguments instead of their first 400
  characters, so two calls that differ later are no longer treated as the same call.

### Install, Unix/macOS

Fixed
- A fresh Linux *user*-mode install aborted at the unit (`~/.config/systemd/user` is never
  created by systemd) after the venv, `config.json` and `.env` were on disk and before any unit,
  `enable` or start. The installer creates the unit's directory.
- `sudo bash install/uninstall-tinycmdr-macos.sh` — the line the docs print — removed nothing,
  because `sudo` resets `HOME` to `/var/root` and every removal path was derived from `$HOME`.
  Both installers resolve the INVOKING user (`SUDO_USER`, else the account behind the uid) and
  that account's home; an install made with `--label` records its label and the uninstaller reads
  it back; a removal that finds nothing says so and names the paths it checked, and the PATH
  wrapper and profile line are removed even when the folder stays.
- The release zip recorded permission bits without the file-type bits, so Finder-extracted
  `INSTALL-MACOS.command`, `UNINSTALL-MACOS.command` and `tinycmdr` landed `-rw-r--r--` and the
  double-click door could not run — for exactly the readers who have no terminal to `chmod +x`.
  Both zip writers are one `write_zip()` now and write Unix regular files with their modes;
  `maintenance/check-package-modes.py` proves it by extracting with `ditto -x -k`, the tool
  Archive Utility uses.
- The package omitted `maintenance/restart-tinycmdr-macos.sh` while the installer and the README
  print it as the day-two command; it is in `SHIP`, and the build refuses a package where
  `SHIP`'s `maintenance/` entries and `ALLOWED_MAINTENANCE` disagree.
- Installer-written `config.json` (which can hold a live `llm.api_key`) was world-readable beside
  a 0600 `.env`, and the install log held the page token in cleartext. Both installers write
  `config.json`, `.env` and the log 0600 and never echo the token.
- `--no-web` did not close the port on Linux (the no-token branch passed `--web` regardless, with
  no token minted, so the agent's HTTP API — which runs shell — was open to any local process on
  every boot); `--web-port` was ignored on an update. Both decide inside the real argument loop.
- On a Mac whose only interpreter was 3.9, `-y` could not complete: the fallback sat behind a
  live-terminal prompt that never consulted `--yes`. `-y` consents to the fetch, `--install-python`
  wins over `--python`, and 3.9 is refused by name with the supported band (3.10–3.12).
- `--secrets-file` carrying `TINYCMDR_MM_TOKEN` still installed "WITHOUT a chat account": the lane
  was decided before the file was read and `.env` got an empty `TINYCMDR_MM_TOKEN=` first (which
  `_load_env_file` keeps). The secrets file is read before the lane decision and managed keys are
  written once.
- `chown "$RUN_USER:$RUN_USER"` assumed the group is named after the user, which is not true on
  AD/LDAP/SSSD accounts, under `useradd -N`, or wherever `USERGROUPS_ENAB=no` — `chown: illegal
  group name` ended a Linux install right after `config.json`. The primary group comes from
  `id -gn`, and a chown that cannot work is a named warning.
- `--help` truncated its own header mid-sentence (so `--no-path` and `--force-python` were
  documented nowhere), and a headless run printed `/dev/tty: Device not configured` on every run.

### Install, Windows

**Verified at runtime on a real Windows box**, not just read: Windows 11 Pro build 26200,
OpenSSH 9.5, Python 3.12.10 — 40 checks pass, 0 fail. That includes a **plain
install run as a NON-elevated user**, which exits 0, writes `config.json`, builds the venv and
installs the deps with no administrator rights at all, and the fleet-kit refusal below, which
happens before anything is written. `tests/test_installer_windows.py` still pins the shipped
text for regressions.

The Windows installer stops finding out about a missing administrator three quarters of the way
through, and stops rewriting the machine's PATH on its way out.

Fixed
- A fleet kit's `as_service: true` walked past the elevation guard (which ran before
  `fleet-defaults.json` could set `-AsService`) and then died inside the *fallback*
  `Register-ScheduledTask`, which was bare — files, venv, `config.json` and `.env` already
  written, `INSTALL FAILED`, exit 2. Elevation is re-checked after the fleet defaults, and the
  fallback registration is caught and answered with "re-run as Administrator". Measured with a
  non-elevated logon: the refusal is **exit 1** (the installer's documented "bad input or
  missing prerequisite" — the guard refuses before it tries, so 1 and not 2), it names both the
  Administrator route and the non-admin lane, and nothing is written.
- An install with no chat lane and the local page off now says so when it registers no autostart
  entry. `-AsService` on such a box produced no task and no explanation, which reads like a
  failed install; the summary now names the reason (there is nothing to run in the background)
  and the switch that changes it. Found by driving the installer on Windows.
- Installing or removing rewrote the whole user PATH with `SetEnvironmentVariable`, which EXPANDS
  other installers' `%JAVA_HOME%\bin`-style entries on read and stores the expanded text back as
  `REG_SZ`. Both now edit `HKCU\Environment` directly (`DoNotExpandEnvironmentNames` on read,
  `ExpandString` on write).
- `-VerifyOnly` installed Python 3.12 before it verified anything; it now probes the install's own
  interpreter and changes nothing.
- The generated launchers were written `-Encoding ASCII` with an absolute path baked in, so a
  non-ASCII profile path could kill autostart; they are path-free (`%~dp0`,
  `WScript.ScriptFullName`) and ASCII by construction.
- The documented uninstall command omitted `-ExecutionPolicy Bypass` and hardcoded the default
  install dir; the README now shows the wrapper with `-InstallDir`.
- `tinycmdr restart` demanded elevation on every Windows install, including the Startup-shortcut
  lane where no task exists and a normal shell can restart the bot — a dead end the operator can
  never satisfy. Elevation is required only when a scheduled task supervises THIS install.
- `Stop-TinycmdrProcesses` could not see the supervisor (`tinycmdr-supervise.py`) or a
  `wscript.exe` launcher; the folder removal retries.
- The remaining W8–W11 items as reported: `tinycmdr.cmd`'s python fallback avoids the Microsoft
  Store stub, `-SkipTask` withholds only the task, the Python fallback is not hardcoded to amd64,
  and the uninstaller asks about the folder before removing the PATH entry.

### The gate

Added
- `tests/run_all.py`: discovers `tests/test_*.py`, runs each as its own subprocess with a
  per-file timeout, prints `file → pass/fail/skip`, and exits non-zero on any failure or skip.
  The suites that were only ever "run by hand" (and the `python -m pytest` lines in their
  docstrings, which never worked) are now one command with a real exit code.
- `requirements-test.txt` and one CI workflow (macOS + Linux running the gate, a Windows job
  running the pure-Python suites).
- Suites added by this phase: `test_envelope.py` (the arithmetic, the refusal, the memory caps,
  the static-overhead ceiling), `test_prefix_stability.py` (prefix reuse ≥ 90 %, exactly one
  trailing state block, disclosure at 80 tools), `test_guard_battery.py` (every destructive
  spelling gated, nothing else), `test_stream_calls.py` (prefill vs idle, torn streams, tool-call
  merging, the optional-field retry, locality, the stop path), `test_atomic_write.py`,
  `test_installer_unix.py`, `test_installer_windows.py`.

### Changed

- `llm.max_tokens` is a CEILING, not what is sent: the reply is clamped per request to
  `min(llm.max_tokens, window // 4)` unless a caller names one deliberately (the forced
  wrap-up and the cut-off-mid-think escalation, which only fire on a server that answered).
- The tool schemas the session will send are part of the budget, so adding 100 disclosed tools
  visibly lowers the messages budget instead of riding free.
- `agent.tool_disclosure` is unchanged; the tool INDEX still bounds the static prompt.
- `TINYCMDR_LOG_FILE` redirects the file log, and the task journal is written beside the
  ledger instead of always beside the build. Both existed so a test install could own its own
  files: the suites that only relocated the ledger still appended to `tinycmdr.log` in the
  checkout, where `git status` cannot show an ignored file. The gate's repo-tree report is
  what finally named it.

### Known, deferred to Phase 2

- The README's "~4.1K token overhead" predates this measurement: the static half is 5,236–5,322
  tokens as shipped, and trimming it is Phase 2's prompt-section work (audit D3/D10), together
  with the doc-number drift.


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
  `set: -
: invalid option` as soon as it resolved. `build-package.py` normalised `.sh` and
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
- The macOS host's `web.port` is 8787 again: the Hermes web UI that claimed 8787 there no longer
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
