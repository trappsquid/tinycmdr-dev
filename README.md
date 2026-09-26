<div align="center">

  <img src="assets/brand/avatar.png" alt="tinycmdr mascot" width="160" />

  # tinycmdr

  ### High-Efficiency Agent Harness for Local and Self-Hosted LLMs
  *~4.1K token overhead · Stable prefix caching · Built-in ops guards · Zero infrastructure.*

  [![GitHub Release](https://img.shields.io/github/v/release/trappsquid/tinycmdr?style=flat-square)](https://github.com/trappsquid/tinycmdr/releases/latest)
  [![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=flat-square)](#quick-install)
  [![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)](https://www.python.org/)
  [![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

  *Run tasks, execute code, and command your machines across Web UI, Terminal CLI, or Chat.*
  <br>
  *Built specifically for llama.cpp, vLLM, Ollama, and local OpenAI-compatible endpoints.*

</div>

---

## Why tinycmdr?

Most agent harnesses were built for cloud API endpoints with remote server infrastructure. When deployed against self-hosted models on local hardware, large boilerplate prompts and volatile prefixes cause recurrent prompt re-evaluations and unnecessary KV cache consumption.

**tinycmdr is built specifically for self-hosted setups.** It pairs a lean, prefix-stable prompt footprint with an operations runtime designed to handle failures gracefully and protect inference slots.

### Key Advantages and Features

- **Stable Prefix Caching (~4.1K Token Overhead):** Fixed overhead is measured at ~4,150 tokens (static system prompt + core schemas). Volatile context is anchored to the tail of the prompt. Local inference engines (llama.cpp, vLLM) can keep the prefix warm in KV cache across turns, reducing prefill time and preserving VRAM for longer context history.
- **Autonomous Ops Runtime:**
  - **Loop Guard:** Detects repetitive tool-call cycles. Refuses identical calls after 2 repeats and halts runaway spins after 6, resetting automatically when a disk mutation (file write, edit) occurs.
  - **Stall Watchdog and Self-Healing:** Monitors execution progress, flags stalled turns, and frees stuck inference slots. Listener watchdogs automatically reconnect dropped websockets and recover without losing session state.
  - **Truthful Stop and Mid-Run Steering:** `/tinycmdr stop` has three verified truthful states (`stopping`, `already flagged`, `nothing running`) and disconnects generation immediately to release GPU slots. Use `steer` to inject corrections into an active run without aborting.
  - **Persistent Task Ledger (`tasks.json`):** Tracks open, in-progress, completed, and abandoned items on disk. Multi-step work survives network drops and process restarts.
  - **Spill Indexing (`spill/`):** Outputs exceeding character limits spill to indexed disk files (`spill#N`) with clean pointers rather than overflowing the context window or silently dropping data.
- **Three Unified Interfaces, One Vocabulary:**
  - **Terminal CLI:** Interactive TUI cards, live streaming output, and command history.
  - **Web Dashboard:** LAN browser dashboard on port 8787 for monitoring tool execution, inspecting state, and reviewing diffs.
  - **Chat Bot:** Background service integration with Mattermost and Telegram for remote administration.
  - The exact same verbs (`status`, `model`, `steer`, `stop`, `tasks`, `logs`, `restart`) work identically across shell, web, and chat.
- **Zero Infrastructure, Single-File Architecture:**
  - Runs as a single Python file with 3 standard dependencies (`requests`, `croniter`, `mmpy_bot`).
  - No Docker containers, no background databases, no Node.js runtime. Inspectable and auditable in a single file.
- **Zero-Bloat Skills and Hot-Loaded Tools:**
  - **Hermes-Compatible Prose Skills (`SKILL.md`):** Markdown operational runbooks index at only ~23 tokens each in the prompt, loading full procedures into context only when triggered.
  - **Hot-Loaded Custom Tools (`tools/`):** Drop a native `.py` (or a register-style `.py`, or a `<name>.tool.json` manifest) into `./tools/` and it is callable from the next start; `create_tool` writes one that is live at the next call. Every tool is listed in the prompt by NAME and shelf, with descriptions one `find_tools` call away, so a growing `tools/` folder stays flat in the payload (measured: 5.9 chars of index per tool at 80 tools, against 167.8 before).
  - **Native Cron Scheduling:** Run scheduled health checks, backups, and maintenance runs in the background.
- **Privacy-Guarded and LAN-First:**
  - Configurable fallback order. Strict privacy gates prevent local failures from falling through to public cloud APIs unless explicitly enabled (`allow_cloud_fallback`).

---

## Comparison: tinycmdr vs. Heavy Agent Stacks

| Feature | Heavy Gateways and Frameworks (e.g. OpenClaw, OpenHands) | **tinycmdr** |
| :--- | :--- | :--- |
| **Fixed Prompt Overhead** | 15,000 – 30,000+ tokens | **~5,300 tokens** (measured on a clean unpack of the shipped archive: system prompt + the 14 schemas a request sends) |
| **KV Prefix Cache** | Invalidation on every turn (front-loaded status/time) | **Prefix-Cache Stable** (volatile context anchored at tail) |
| **Prompt Ingestion / Prefill** | Full prompt re-evaluation on uncached turns | **Reuses prefix cache** (only new turns/tail evaluated) |
| **KV Cache VRAM Footprint** | Large VRAM reserved for framework boilerplate | **Minimal** (compact prompt + on-demand runbook loading) |
| **Runtime Reliability** | Loops can spin unchecked; wedged agents lock GPU slots | **Loop Guard + Stall Watchdog + Slot-Freeing Stop** |
| **Task State Management** | In-memory only or external database tables | **Transparent Task Ledger** (`tasks.json` on disk) |
| **Tool Spill Handling** | Silent truncation or context overflow | **Spill Index** (`spill/` storage with `spill#N` pointers) |
| **Deployment Footprint** | Multi-container Docker, Node.js gateway, external DB | **Single Python file**, 3 dependencies, zero containers |
| **Interfaces** | Single-purpose CLI or heavy web portal | **TUI CLI + Web UI (:8787) + Mattermost/Telegram Bot** |
| **Extension Model** | Complex plugin SDKs or container rebuilds | **Prose Skills (`SKILL.md`) + hot-loaded `.py`/`.tool.json` tools** |

---

## Quickstart (Under 1 Minute)

### 1. Configure
Run the guided setup to connect your local endpoint (or cloud fallback):
```bash
tinycmdr setup
```
*(Tests connection live against `/v1/models` and sets sensible defaults).*

### 2. Launch
Choose how you want to interact:

```bash
# Option A: Interactive terminal session (fastest)
tinycmdr

# Option B: Live Web UI dashboard (browser view on http://127.0.0.1:8787)
tinycmdr web

# Option C: Direct one-off command
tinycmdr --once "check why disk space is low and summarize largest folders"
```

---

## Three Ways to Command Your Machine

| Interface | Best For | How to Launch |
| :--- | :--- | :--- |
| **Terminal CLI** | Fast sysadmin tasks, debugging, and terminal workflows. Beautiful TUI cards and command history. | `tinycmdr` |
| **Web Dashboard** | Real-time streaming view of tool execution, file edits, and system state. Accessible from any LAN browser or mobile device. | `tinycmdr web` |
| **Chat Bot** | Managing your machines remotely via Mattermost or Telegram DMs with live progress updates. | Background service (auto-started) |

---

## Essential Commands

The same verbs work across every interface: in your OS shell (`tinycmdr <verb>`), inside the interactive CLI (`/tinycmdr <verb>`), and in chat:

| Command | Description |
| :--- | :--- |
| `tinycmdr status` | System overview: active model, endpoint, context budget, and task counts |
| `tinycmdr model` | View active model status, context window, and routes |
| `tinycmdr model list` | Live query of available models across all configured endpoints |
| `tinycmdr model <name>` | Switch the active model for your session |
| `tinycmdr model add <url>` | Add a new model endpoint or fallback route |
| `tinycmdr steer <text>` | Inject live instructions or corrections into an active run |
| `tinycmdr stop` | Truthfully cancel an in-flight run and immediately free GPU slots |
| `tinycmdr tasks` | Inspect open, in-progress, and completed items in the task ledger |
| `tinycmdr setup` | Launch the guided configuration wizard |
| `tinycmdr logs [n]` | View the last *n* lines of the execution log |
| `tinycmdr restart` | Cleanly recycle the background service and catch up on missed events |

---

## Extending with Skills and Tools

### 1. Prose Skills (`skills/`)
tinycmdr supports drop-in Hermes-compatible runbooks. Drop runbook markdown folders into `skills/`:

```text
skills/
└── web-server/
    └── SKILL.md
```

Each skill adds only ~23 tokens of summary index to the static prompt. The agent reads the full markdown file on demand only when the task requires it. Zero configuration, zero restarts.

### 2. Hot-Loaded Tools (`tools/`)
Drop a Python (`.py`) or PowerShell (`.ps1`) script into `tools/`:

```python
# tools/check_service.py
def check_service(name: str) -> str:
    """Check system service status."""
    ...
```

The loader picks it up immediately on the next call. The agent can also use `create_tool` to write new custom tools for itself on the fly.

---

## The Problem We Solve: Why Local LLMs Need a Different Harness

Most agent frameworks were designed for cloud LLMs with dedicated remote infrastructure. Running them on self-hosted inference (llama.cpp, vLLM, Ollama) exposes practical friction points:

- **Prompt Ingestion Latency:** Injecting 15,000–30,000 tokens of boilerplate prompts and schemas requires heavy prefill compute before generating the first token on uncached turns.
- **Prefix Cache Thrashing:** Many frameworks place dynamic timestamps, counters, or random identifiers near the top of the prompt. This invalidates the KV cache on every turn, forcing the inference engine to recompute the entire prompt.
- **KV Cache VRAM Consumption:** In long-context setups (32K–256K), KV cache consumes substantial GPU VRAM. Burning tens of thousands of tokens on harness plumbing reduces headroom for reasoning, file context, and multi-user concurrency.
- **Inference Slot Wedging:** When an agent loops or encounters an error, generic harnesses often leave generation requests active, locking limited GPU slots on the model server.
- **Container and Dependency Sprawl:** Heavy frameworks often require multi-container Docker topologies, Redis, Postgres, and Node.js runtimes to run basic shell commands on a machine.

tinycmdr addresses these bottlenecks with an auditable single-process runtime built around token efficiency and execution guards.

---
## Quick Install

Linux and macOS: one line, below. Windows: download [`tinycmdr-win.zip`](https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-win.zip) from the [latest release](https://github.com/trappsquid/tinycmdr/releases/latest) and double-click **`INSTALL-WINDOWS.cmd`** inside it. Windows needs no administrator rights at all, a Linux *user* install needs none either, and only the Linux *system* install and a macOS install with `sudo` ask for root.

The links below are stable names that always point at the newest build, so they never need re-pinning to a version.

### Windows

```powershell
irm https://github.com/trappsquid/tinycmdr/releases/latest/download/install.ps1 | iex
```

That fetches the newest archive, expands it and runs the installer inside it. Pass the installer's own switches through when you want to decide without being asked:

```powershell
iex "& { $(irm https://github.com/trappsquid/tinycmdr/releases/latest/download/install.ps1) } -InstallDir D:\tinycmdr"
```

Or do it by hand: download [`tinycmdr-win.zip`](https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-win.zip), extract it, and double-click **`INSTALL-WINDOWS.cmd`** in the extracted folder, or from PowerShell:

```powershell
curl.exe -LO https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-win.zip
Expand-Archive tinycmdr-win.zip
cd tinycmdr-*          # the folder inside carries the version
.\INSTALL-WINDOWS.cmd
```

It installs into `%USERPROFILE%\tinycmdr`, builds its own Python environment inside that folder, adds `tinycmdr` to your user PATH, and starts the agent at your next logon. Nothing is written outside your profile, so Windows never asks you to elevate.

### Linux (Ubuntu / Debian / systemd)

```bash
curl -fsSL https://github.com/trappsquid/tinycmdr/releases/latest/download/install.sh | bash
```

That fetches the newest archive, unpacks it and runs the installer inside it. With no switch it asks, at a terminal, which kind you want: **system** (boots with the machine, needs root, the agent gets passwordless sudo) or **user** (starts when you log in, no root anywhere, the agent cannot use sudo). Then it asks for what the bot cannot work without - the Mattermost server, your user id, the model endpoint and that endpoint's key - and writes nothing until you answer `Install now?`. Every answer has a switch, and `--yes` (or a run with no terminal at all) asks nothing. Decide without being asked by passing the installer's own switches through:

```bash
curl -fsSL https://github.com/trappsquid/tinycmdr/releases/latest/download/install.sh | bash -s -- --mode user
curl -fsSL https://github.com/trappsquid/tinycmdr/releases/latest/download/install.sh | bash -s -- --mode system --yes
```

Or do it by hand:

```bash
curl -LO https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-linux.tar.gz
tar -xzf tinycmdr-linux.tar.gz
cd tinycmdr-*          # the folder inside carries the version
sudo bash install/install-tinycmdr.sh
```

Run it at a terminal with no switch and it asks; `--mode system|user` decides without a prompt, and `--yes` takes the default for who you are.

### macOS (launchd)

```bash
curl -fsSL https://github.com/trappsquid/tinycmdr/releases/latest/download/install.sh | bash
```

With no switch it asks for what the bot cannot work without, and writes nothing until you answer `Install now?`:

```text
Mattermost bot token (input hidden, Enter to skip for the local page):
Mattermost server, no https:// (e.g. chat.example.com):
Your Mattermost user id (optional, but without it the bot ignores your DMs):
Model endpoint [http://127.0.0.1:8081/v1]:
Model id [main]:
API key for it (blank if it needs none):     <- only when the endpoint is not on this machine
```

Every answer has a switch (`--mattermost-url`, `--allowed-user`, `--model-base-url`, `--model`), so a scripted install asks nothing: `--yes` takes the defaults, and a run with no terminal at all (a pipe, a fleet push) never prompts.

Or do it by hand:

```bash
curl -LO https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-macos.zip
unzip tinycmdr-macos.zip
cd tinycmdr-*          # the folder inside carries the version
bash install/install-tinycmdr-macos.sh
```

The `tinycmdr` verb lands in `/usr/local/bin` when that folder is writable (a Homebrew machine), and in `~/.local/bin` otherwise - one `export PATH` line is added to `~/.zshrc` for it, so open a new terminal before typing `tinycmdr`. Until then: `~/tinycmdr/tinycmdr status`. `--no-path` writes neither.

Or skip the terminal: double-click **`INSTALL-MACOS.command`** in the extracted folder (macOS runs a `.command`; it opens a `.sh` in TextEdit). To remove it, double-click **`UNINSTALL-MACOS.command`**, or run `bash install/uninstall-tinycmdr-macos.sh`. Use `sudo` for the uninstall if the install left a root-owned launcher in `/usr/local/bin`; the uninstaller removes the `~/.local/bin` one by itself, and reports what it could not remove instead of stopping part-way.

The local web/API page listens on port **8787** on every platform (loopback unless you set a token). `--web-port <p>` moves it and `--no-web` closes it.

### Installer switches (Windows)

```text
-InstallDir <folder>   install somewhere other than %USERPROFILE%\tinycmdr
-NoPath                leave your user PATH alone
-SkipTask              files only: no autostart entry
-VerifyOnly            report on an existing install, change nothing
-Uninstall [-Force]    stop it, remove the folder and the autostart entry
-AsService             register a boot-start scheduled task instead of a
                       logon shortcut (this one needs an elevated shell,
                       because Windows reserves boot-start tasks for
                       administrators)
```

## Removing it

Whatever installed it can remove it, from the folder it installed into:

**Windows** - the package door, or the copy that ships inside the install:

```powershell
INSTALL-WINDOWS.cmd -Uninstall
powershell -File "$env:USERPROFILE\tinycmdr\install\uninstall-tinycmdr.ps1" -Force
```

**Linux** - the uninstaller inside the install (`--mode user` for a user install, which needs no sudo):

```bash
sudo bash ~/tinycmdr/install/install-tinycmdr.sh --uninstall
```

**macOS** - double-click **`UNINSTALL-MACOS.command`** in the install folder (`~/tinycmdr`); it asks for a password only when the install left a root-owned launcher in `/usr/local/bin`. Or from a terminal:

```bash
bash ~/tinycmdr/install/uninstall-tinycmdr-macos.sh --uninstall
```

None of these touch your Mattermost bot account. Its token is dead to you once the agent is gone - revoke it in **Profile > Security > Personal Access Tokens**.

---

## License

MIT License. Free and open source for personal and enterprise hardware.
