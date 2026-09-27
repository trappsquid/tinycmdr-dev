<div align="center">

  <img src="assets/brand/avatar.png" alt="tinycmdr mascot" width="160" />

  # tinycmdr

  ### An autonomous ops agent for self-hosted models

  *One Python file · three dependencies · any OpenAI-compatible endpoint — llama.cpp, vLLM, SGLang, Ollama, or a cloud API.*

  [![GitHub Release](https://img.shields.io/github/v/release/trappsquid/tinycmdr?style=flat-square)](https://github.com/trappsquid/tinycmdr/releases/latest)
  [![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=flat-square)](#install)
  [![Python](https://img.shields.io/badge/python-3.10--3.12-blue?style=flat-square)](https://www.python.org/)
  [![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

---

tinycmdr runs **on the machine it manages**. You send it a task; it does the work with real
tools (`shell`, `execute_code`, `edit_file`, `fetch_url`, …), narrates what it is doing, and keeps
its state in plain files beside itself. It is built for small and local models: the prompt is a
stable, cache-friendly prefix, and the runtime is built so a confused model cannot wedge your
inference slot.

Reach it from a **terminal**, a **browser page**, or **chat** (Mattermost, Telegram) — one
process, one vocabulary, the same conversations either way.

## Install

**Windows** — PowerShell, no administrator rights:

```powershell
irm https://github.com/trappsquid/tinycmdr/releases/latest/download/install.ps1 | iex
```

**Linux and macOS** — one line; it asks a few questions and writes nothing until you confirm:

```bash
curl -fsSL https://github.com/trappsquid/tinycmdr/releases/latest/download/install.sh | bash
```

Either installer fetches the newest build, creates its own private Python environment, and starts
the agent with the machine. Needs **Python 3.10–3.12** — other versions are refused by name, and
`--install-python` installs a supported one. Only a Linux *system* install and a macOS install
with `sudo` ask for root; a Windows install and a Linux *user* install never do.

By hand, if you prefer — download, unpack, run the installer inside:

| OS | download | then |
| :--- | :--- | :--- |
| Windows | [`tinycmdr-win.zip`](https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-win.zip) | double-click `INSTALL-WINDOWS.cmd` |
| Linux | [`tinycmdr-linux.tar.gz`](https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-linux.tar.gz) | `sudo bash install/install-tinycmdr.sh` |
| macOS | [`tinycmdr-macos.zip`](https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-macos.zip) | `bash install/install-tinycmdr-macos.sh` |

Those names always point at the newest build, so a link never needs re-pinning to a version.

<details>
<summary><b>Unattended installs, every switch, and a second install on one host</b></summary>

**Linux and macOS.** `--mode user|system` decides without a prompt and `--yes` takes the defaults
for your platform; a run with no terminal at all never asks. Every question has a switch:
`--mattermost-url`, `--allowed-user`, `--telegram-token`, `--telegram-ids`, `--model-base-url`,
`--model`, `--web-host <addr>`, `--web-port <p>`, `--no-web`, `--no-path`.

A **second** install on one host needs its own identity: `TINYCMDR_SERVICE=tinycmdr-work bash
install/install-tinycmdr.sh …` on Linux, `--label com.tinycmdr.work` on macOS. A service name
belongs to the host rather than to a folder, so the installer refuses instead of taking the first
install's autostart away.

**Windows** switches — `INSTALL-WINDOWS.cmd` asks the same questions when run bare:

```text
-InstallDir <folder>   somewhere other than %USERPROFILE%\tinycmdr
-TaskName <name>       the autostart entry this install owns (a second install needs its own)
-NoPath                leave the user PATH alone
-SkipTask              files only: no autostart
-VerifyOnly            report on an existing install, change nothing
-Uninstall [-Force]    stop it, remove the folder and the autostart entry
-AsService             boot-start task instead of a logon shortcut (needs an elevated shell)
-EnableWeb             serve the local page while the bot runs
-WebPort <p>           page port (default 8787)
-WebHost <addr>        0.0.0.0 to reach it from your network, 127.0.0.1 for this machine only
-TelegramToken <t>     a Telegram bot token
-TelegramIds <ids>     your numeric Telegram id(s), comma or space separated
-AddEndpoint <spec>    another endpoint, repeatable: "<base_url>;<model>;<alias>;<key>"
-NonInteractive        ask nothing: take the switches and the defaults
```

Day-to-day notes for a Mac: [`install/README-macos.md`](install/README-macos.md).
</details>

## Start it

```bash
tinycmdr setup        # once: point it at your model endpoint
tinycmdr              # terminal session
tinycmdr web          # browser page on http://127.0.0.1:8787
tinycmdr --once "…"   # one task, then exit
```

The page is where a working agent is easiest to watch: tool calls stream in as they happen, and a
message sent mid-run steers the run instead of queueing behind it. The page always needs the
token from `.env`; the installer asks whether other machines on your network may reach it
(`0.0.0.0`) or only this one (`127.0.0.1`).

## Use it

The same verbs work in a shell (`tinycmdr <verb>`), in the interactive CLI (`/tinycmdr <verb>`),
and in chat:

| Verb | What it does |
| :--- | :--- |
| `status` | version, folder, model, endpoint, context, log, instance |
| `doctor` | check this install and name what is wrong (exit 1 when something is) |
| `health` | one line and an exit code, no network — for scripts |
| `model` | the models this install can route to (asks the endpoints) |
| `model use <name>` · `model add <url>` · `model remove <x>` | switch, add or drop an endpoint |
| `tasks [--all]` | the task ledger: open, in progress, recently done |
| `logs [n]` · `version` · `proc` · `ports` | log tail, version, this folder's processes and listeners |
| `update` | pull the published build (`update <file\|zip\|folder>` puts one in place by hand) |
| `clean` · `token` · `config get\|set <dotted.key>` | junk in this folder, where secrets live, edit config.json |
| `restart` | restart through this host's own door (launchd, systemd, Task Scheduler) |
| `/stop` in the CLI or chat | cancel the run that is going, now |
| *a plain message mid-run* | steer it: the run folds your correction in at its next step |

`tinycmdr help` prints the same list, and so does `/tinycmdr help` in chat.

## Extend it

**Prose skills.** Drop a runbook folder into `skills/` and it is live on the next message. Each
one costs a single line of prompt index and is read in full only when the task looks relevant:

```text
skills/web-server/SKILL.md     # YAML frontmatter: name + description, then the runbook
```

**Custom tools.** Drop a `.py` (or a `<name>.tool.json` manifest) into `tools/` and it is
callable from the next call — listed in the prompt by name and shelf, with descriptions one
`find_tools` call away. The agent can write one for itself with `create_tool`.

Both stay flat as the folder grows: 5.9 characters of tool index per tool at 80 tools.

## Update it

```bash
tinycmdr update      # pull the published build
tinycmdr restart     # start running it
```

## Remove it

| OS | command |
| :--- | :--- |
| Windows | `INSTALL-WINDOWS.cmd -Uninstall -Force` — add `-InstallDir <folder>` if you did not take the default |
| Linux | `sudo bash ~/tinycmdr/install/install-tinycmdr.sh --uninstall` — add `--mode user` for a user install |
| macOS | double-click `UNINSTALL-MACOS.command`, or `bash ~/tinycmdr/install/uninstall-tinycmdr-macos.sh --uninstall` |

None of these touch your Mattermost bot account. Its token is yours to revoke in
**Profile → Security → Personal Access Tokens** once the agent is gone.

## Why it looks like this

Most harnesses assume a cloud endpoint behind a fat server. Against a self-hosted model the costs
are concrete: 15,000–30,000 tokens of boilerplate re-prefilled on every uncached turn, a volatile
prefix that invalidates the KV cache each request, and generation slots left running when an agent
loops. tinycmdr is one Python file with three dependencies (`requests`, `croniter`, `mmpy_bot`):
its fixed prompt is **~4.7K tokens on a clean unpack** (`tinycmdr status` prints this box's own
as `static` — it grows with the skills and tools you add; 4,929 on this install, measured
2026-09-27), volatile context sits at the tail so
the prefix stays cacheable, and the runtime guards the slot. Local failures never fall through to
a public API unless you set `allow_cloud_fallback`.

The long version — measured surface, budgets, failure handling, what it deliberately does not
have, and how it compares with other harnesses — is
[`docs/tinycmdr-what-it-is.md`](docs/tinycmdr-what-it-is.md).

---

## License

MIT. Free and open source for personal and enterprise hardware.
