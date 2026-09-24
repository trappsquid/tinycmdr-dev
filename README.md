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
  - **Hot-Loaded Custom Tools (`tools/`):** Drop a `.py` or `.ps1` script into `./tools/` and it becomes callable on the next turn without restarting the agent. The agent can also author its own tools via `create_tool`.
  - **Native Cron Scheduling:** Run scheduled health checks, backups, and maintenance runs in the background.
- **Privacy-Guarded and LAN-First:**
  - Configurable fallback order. Strict privacy gates prevent local failures from falling through to public cloud APIs unless explicitly enabled (`allow_cloud_fallback`).

---

## Comparison: tinycmdr vs. Heavy Agent Stacks

| Feature | Heavy Gateways and Frameworks (e.g. OpenClaw, OpenHands) | **tinycmdr** |
| :--- | :--- | :--- |
| **Fixed Prompt Overhead** | 15,000 – 30,000+ tokens | **~4,150 tokens** (measured, static prompt + core schemas) |
| **KV Prefix Cache** | Invalidation on every turn (front-loaded status/time) | **Prefix-Cache Stable** (volatile context anchored at tail) |
| **Prompt Ingestion / Prefill** | Full prompt re-evaluation on uncached turns | **Reuses prefix cache** (only new turns/tail evaluated) |
| **KV Cache VRAM Footprint** | Large VRAM reserved for framework boilerplate | **Minimal** (compact prompt + on-demand runbook loading) |
| **Runtime Reliability** | Loops can spin unchecked; wedged agents lock GPU slots | **Loop Guard + Stall Watchdog + Slot-Freeing Stop** |
| **Task State Management** | In-memory only or external database tables | **Transparent Task Ledger** (`tasks.json` on disk) |
| **Tool Spill Handling** | Silent truncation or context overflow | **Spill Index** (`spill/` storage with `spill#N` pointers) |
| **Deployment Footprint** | Multi-container Docker, Node.js gateway, external DB | **Single Python file**, 3 dependencies, zero containers |
| **Interfaces** | Single-purpose CLI or heavy web portal | **TUI CLI + Web UI (:8787) + Mattermost/Telegram Bot** |
| **Extension Model** | Complex plugin SDKs or container rebuilds | **Prose Skills (`SKILL.md`) + Hot-loaded `.py`/`.ps1` tools** |

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

Download the archive for your platform from the [latest release](https://github.com/trappsquid/tinycmdr/releases/latest), unpack it, and run the installer inside. No administrator rights are needed, and the installer downloads Python 3.12 for you if the machine has none.

### Windows

Download [`tinycmdr-1.0.6-win.zip`](https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-1.0.6-win.zip), extract it, and double-click **`INSTALL-WINDOWS.cmd`** in the extracted folder. Or from PowerShell:

```powershell
curl.exe -LO https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-1.0.6-win.zip
Expand-Archive tinycmdr-1.0.6-win.zip
cd tinycmdr-1.0.6
.\INSTALL-WINDOWS.cmd
```

It installs into `%USERPROFILE%\tinycmdr`, builds its own Python environment inside that folder, adds `tinycmdr` to your user PATH, and starts the agent at your next logon. Nothing is written outside your profile, so Windows never asks you to elevate.

### Linux (Ubuntu / Debian / systemd)

```bash
curl -LO https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-1.0.6-linux.tar.gz
tar -xzf tinycmdr-1.0.6-linux.tar.gz
cd tinycmdr-1.0.6
sudo bash install/install-tinycmdr.sh
```

### macOS (launchd)

```bash
curl -LO https://github.com/trappsquid/tinycmdr/releases/latest/download/tinycmdr-1.0.6-macos.zip
unzip tinycmdr-1.0.6-macos.zip
cd tinycmdr-1.0.6
bash install/install-tinycmdr-macos.sh
```

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

---

## License

MIT License. Free and open source for personal and enterprise hardware.
