<div align="center">

  <img src="assets/tinycmdr-chibi.png" alt="tinycmdr" width="190">

  # tinycmdr

  ### An autonomous ops agent for self-hosted models

  *One Python file · one import at startup (six installable) · any OpenAI-compatible endpoint — llama.cpp, vLLM, SGLang, Ollama, or a cloud API.*

  [![GitHub Release](https://img.shields.io/github/v/release/trappsquid/tinycmdr?style=flat-square)](https://github.com/trappsquid/tinycmdr/releases/latest)
  [![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=flat-square)](#install)
  [![Python](https://img.shields.io/badge/python-3.10--3.12-blue?style=flat-square)](https://www.python.org/)
  [![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

---

Runs **on the machine it manages**: you send a task, it uses real tools (`shell`, `execute_code`,
`edit_file`, `fetch_url`, …), narrates its work, keeps state in plain files beside itself.

## Problems → what it does

| With a self-hosted model, the cost is | tinycmdr |
| :--- | :--- |
| 15–30K tokens of harness boilerplate re-prefilled every uncached turn | **~4.1K-token fixed prompt** on a clean unpack (est_tokens; the endpoint read ~3.5K in September) — `tinycmdr status` prints this install's |
| a volatile prefix that invalidates the KV cache every request | static prefix, volatile context **tail-anchored** |
| a looping or stuck model wedging your inference slot | loop guard, per-run scan budget, stall watchdog, supervised restart |
| "the UI" requiring a hosted service or an open LAN port | built-in **browser page**, token-gated, loopback by default — or CLI, `--once`, Mattermost/Telegram |
| no way to hand files back and forth | page **uploads** (drag-drop/paste) and **downloads** for files the agent offers |
| upgrade procedures per version | **one update command from every version**; host files never overwritten |
| infrastructure sprawl | no database, no container, no daemon: one process, plain files |
| secrets leaking into prompts | tokens live in `.env` only; `config.json` never holds one |

## Doors

| Door | For |
| :--- | :--- |
| **Page** (default) | live cards, file upload/download, conversation rail, tasks/jobs/log/inventory — browser, phone on your LAN |
| Terminal (`--app`, `--cli`) | ops on the box |
| Chat (Mattermost / Telegram) | steering from anywhere; uploads both ways |
| `--once "…"` | scripts |

One process serves whichever are configured; same sessions, notes, skills, model switches.

## Install

**Windows** — PowerShell, no administrator rights:

```powershell
irm https://github.com/trappsquid/tinycmdr/releases/latest/download/install.ps1 | iex
```

**Linux and macOS** — one line; it asks a few questions and writes nothing until you confirm:

```bash
curl -fsSL https://github.com/trappsquid/tinycmdr/releases/latest/download/install.sh | bash
```

Needs **Python 3.10–3.12** (`--install-python` installs one). Only a Linux *system* install and a
macOS install with `sudo` ask for root.

| OS | download | then |
| :--- | :--- | :--- |
| Windows | [`tinycmdr-win.zip`](https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-win.zip) | double-click `INSTALL-WINDOWS.cmd` |
| Linux | [`tinycmdr-linux.tar.gz`](https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-linux.tar.gz) | `sudo bash install/install-tinycmdr.sh` |
| macOS | [`tinycmdr-macos.zip`](https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-macos.zip) | `bash install/install-tinycmdr-macos.sh` |

Stable names always point at the newest build. `SHA256SUMS` covers all eight published files
(checksummed, not signed — it catches corruption, not a replaced release):

```bash
base=https://github.com/trappsquid/tinycmdr/releases/latest/download
curl -fsSLO $base/tinycmdr-linux.tar.gz && curl -fsSLO $base/SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing     # macOS: shasum -a 256 -c SHA256SUMS
```

<details>
<summary><b>Unattended installs, every switch, a second install on one host</b></summary>

**Linux.** `--mode user|system`, `--yes`; a run with no terminal never asks. **macOS** has one
kind of install — a per-user launchd agent, `--no-launchd` to skip it — so it takes `--yes` and
has no `--mode`. Every question has a switch: `--mattermost-url`, `--allowed-user`,
`--telegram-token`, `--telegram-ids`, `--model-base-url`, `--model`, `--web-host`, `--web-port`,
`--web-token`, `--no-web`, `--no-path`.

**A second install on one host** needs its own identity: `TINYCMDR_SERVICE=tinycmdr-work …` on
Linux, `--label com.tinycmdr.work` on macOS, `-TaskName` on Windows.

**Windows** — `INSTALL-WINDOWS.cmd` asks the same questions when run bare:

```text
-InstallDir <folder>   somewhere other than %USERPROFILE%\tinycmdr
-TaskName <name>       the autostart entry this install owns
-NoPath                leave the user PATH alone
-SkipTask              files only: no autostart
-VerifyOnly            report on an existing install, change nothing
-Uninstall [-Force]    stop it, remove the folder and the autostart entry
-AsService             boot-start task instead of a logon shortcut (elevated shell)
-TelegramToken <t>     a Telegram bot token
-TelegramIds <ids>     your numeric Telegram id(s)
-AddEndpoint <spec>    another endpoint, repeatable: "<base_url>;<model>;<alias>;<key>"
-WebHost <addr>        page bind: 127.0.0.1 (default) or 0.0.0.0
-WebPort <p>           page port (default 8790)
-NoWeb                 install without the page
-NonInteractive        ask nothing
```

Mac day-to-day notes: [`install/README-macos.md`](install/README-macos.md).
</details>

## Start

```bash
tinycmdr setup        # once: endpoint, chat gateways, search consent
tinycmdr              # the page, opens in your browser (the default door)
tinycmdr cli          # the console alone: this folder, inline cards, no page
tinycmdr --app        # the full-screen session alone, no page
tinycmdr --once "…"   # one task, then exit
tinycmdr --no-web     # the page off for this run (the lanes/service only)
```

**Keys (session):** `Enter` send · `↑`/`↓` line, `PgUp`/`PgDn` page, `Ctrl-Home`/`Ctrl-End` ends ·
`Ctrl-C` stop run / quit idle · `Ctrl-D`/`Ctrl-Q`/`Esc` quit · `Ctrl-Y` copy newest item (again:
walk back) · `Ctrl-B` copy transcript. Wheel scroll: `TINYCMDR_APP_MOUSE=1`.

**Page:** token-gated always (`TINYCMDR_WEB_TOKEN` in `.env`; the installer mints one, and so does
the first start of a host that has none — an install that upgraded into the page). `tinycmdr setup`
asks the three things nobody can infer: loopback or LAN, the port, and the token (Enter keeps the
host's own or mints one; a token you bring replaces it — `--web-token <t>` does the same at
install). Loopback by default; `0.0.0.0` puts it on your network — token in cleartext there, so
trust the network. Rotate/reprint:

```bash
tinycmdr token set TINYCMDR_WEB_TOKEN   # empty value mints a fresh one, prints the link
tinycmdr web                            # print the tokenized link (opens a browser when there is one)
```

**Chat:** whichever token you configure runs that lane; both → one process serves both.

## Use

`tinycmdr <verb>` in a shell, `/tinycmdr <verb>` in the session and in chat:

| Verb | Does |
| :--- | :--- |
| `status` | version, folder, model, endpoint, context, log, instance |
| `doctor` | check this install, name what is wrong (exit 1) |
| `health` | one line + exit code, no network |
| `model` | picker; `model setup` wizard; `model endpoint [<url>]` read/fix; `model use/add/remove` |
| `logs [n]` · `version` · `proc` | log tail, version, process + lock state |
| `update` | fetch the published build (`update <file\|zip\|folder>` by hand) |
| `clean` · `token` · `config get\|set <dotted.key>` | folder junk, secrets, config.json |
| `restart` | through this host's own door (launchd, systemd, Task Scheduler) |
| `/stop` in the session/chat | cancel the run now |
| a plain message mid-run | steer: folded in at the next step |

## Extend

| Drop this | Get this |
| :--- | :--- |
| `skills/<name>/SKILL.md` (YAML frontmatter: name + description; optional `globs:`, `always: true`, and `hide: true` for operator-only, then the runbook) | live next message; one prompt-index line, read in full only when relevant; an operator-only runbook opens only when the operator names it |
| `tools/<name>.py` or `<name>.tool.json` | callable next call; listed by name and shelf, descriptions one `find_tools` call away |
| `create_tool` | the agent writes its own |

Flat as it grows: ~5.7 characters of tool index per tool at 80 tools.

## Update

```bash
tinycmdr update      # fetch the published build (verified against SHA256SUMS)
tinycmdr restart     # start running it (chat restarts itself)
```

Never overwrites `config.json`, `.env`, `soul.md`, notes, `tools/`, `skills/`, `theme.toml`.
A missing `theme.toml` or `soul.md` is recreated from the shipped default
(`theme.default.toml`, `soul.example.md`) on start; an edited one is never touched.
An old or broken install updates with the same command (the launcher falls back to the published
`update.sh` / `update.ps1`).

## Remove

| OS | Command |
| :--- | :--- |
| Windows | `INSTALL-WINDOWS.cmd -Uninstall -Force` (+ `-InstallDir <folder>` if not default) |
| Linux | `sudo bash ~/tinycmdr/install/install-tinycmdr.sh --uninstall` (+ `--mode user`) |
| macOS | `UNINSTALL-MACOS.command`, or `bash ~/tinycmdr/install/uninstall-tinycmdr-macos.sh --uninstall` |

Your Mattermost bot token is not touched; revoke it in **Profile → Security → Personal Access
Tokens**.

## Develop

This repository is the runtime - `tinycmdr.py`, the installers, the examples and the assets.
Development happens in [`trappsquid/tinycmdr-dev`](https://github.com/trappsquid/tinycmdr-dev),
which carries the full history, the release tooling and the suites that grade it; from this
checkout `python3 tinycmdr.py doctor` is the check you have, and `tinycmdr doctor` on an installed
box is the same command.

What it is, measured — surface, budgets, failure handling, comparisons:
[`docs/tinycmdr-what-it-is.md`](docs/tinycmdr-what-it-is.md). The GUI tool's host notes (TCC
grants and the stale case, Windows/Linux pitfalls, image routing):
[`docs/computer-use.md`](docs/computer-use.md).

## History

This repository is the install surface: it starts at the tree released as **v1.0.87**, and its
release carries the artifacts. The project's full history, its tags, its development tree and the
note explaining the 2026-10-07 sanitisation of that history live in
[`trappsquid/tinycmdr-dev`](https://github.com/trappsquid/tinycmdr-dev) — including how to check
that the sanitisation did not touch shipped code (the v1.0.87 archives are byte-identical to the
ones published before it). Releases are unsigned.

## License

MIT. Free and open source for personal and enterprise hardware.
