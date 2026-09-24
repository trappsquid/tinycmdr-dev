<div align="center">

  <img src="assets/brand/avatar.png" alt="tinycmdr mascot" width="160" />

  # tinycmdr

  ### The High-Efficiency Agent Harness for Local and Self-Hosted LLMs
  *Fast TTFT · ~4.1K token overhead · Prefix-cache stable · Zero infrastructure.*

  [![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=flat-square)](#quick-install)
  [![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)](https://www.python.org/)
  [![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

  *Run tasks, execute code, and command your machines across Web UI, Terminal CLI, or Chat.*
  <br>
  *Built specifically for llama.cpp, vLLM, Ollama, and local OpenAI-compatible endpoints.*

</div>

---

## Why tinycmdr?

Most agent harnesses were built for cloud API budgets and heavy server clusters. When run against local hardware, they choke your GPU with massive prompts, thrash your KV cache, and crash without cleaning up inference slots.

**tinycmdr is built from the ground up for self-hosted intelligence.** It pairs an ultra-lean prompt footprint with an autonomous operations runtime designed to protect your hardware and keep long-running tasks on track.

### Key Advantages and Features

- **Instant TTFT and Stable Prefix Caching:** Fixed overhead is kept to ~4,150 tokens (static prompt + schemas). Dynamic, volatile context is strictly anchored to the tail of the prompt. Your model engine caches the entire prefix across turns, eliminating prompt re-evaluation delays and saving valuable KV cache VRAM.
- **Autonomous Ops Runtime:**
  - **Loop Guard:** Detects repetitive tool-call cycles. Refuses identical calls after 2 repeats and halts runaway spins after 6, resetting automatically when a real state mutation (file write, edit) occurs.
  - **Stall Watchdog and Self-Healing:** Monitors long runs, alerts on stalls, and automatically frees stuck slots. Built-in listener watchdogs recover dropped websockets and restart cleanly without losing session state.
  - **Truthful Stop and Mid-Run Steering:** `/tinycmdr stop` has three verified truthful states (`stopping`, `already flagged`, `nothing running`) and frees GPU server slots immediately. Use `steer` to inject corrections into an active run without aborting.
  - **Persistent Task Ledger (`tasks.json`):** Tracks open, in-progress, completed, and abandoned items on disk. Multi-step work survives network drops and process restarts.
  - **Spill Indexing (`spill/`):** Outputs exceeding character limits spill cleanly to indexed disk files (`spill#N`) rather than bloating the context window or silently truncating vital output.
- **Three Unified Interfaces, One Vocabulary:**
  - **Terminal CLI:** Interactive TUI cards, live streaming output, and command history.
  - **Web Dashboard:** Real-time LAN browser dashboard on port 8787 for monitoring tool execution, inspecting state, and reviewing diffs.
  - **Chat Bot:** Native background service integration with Mattermost and Telegram for remote administration.
  - The exact same verbs (`status`, `model`, `steer`, `stop`, `tasks`, `logs`, `restart`) work identically across shell, web, and chat.
- **Zero Infrastructure, Single-File Architecture:**
  - Runs as a single Python file with only 3 lightweight dependencies (`requests`, `croniter`, `mmpy_bot`).
  - No Docker containers, no background databases (Postgres/Redis), no Node.js runtime. Inspectable and auditable in minutes.
- **Zero-Bloat Skills and Hot-Loaded Tools:**
  - **Hermes-Compatible Prose Skills (`SKILL.md`):** Markdown operational runbooks index at only ~23 tokens each in the prompt, loading full procedures into context only when triggered.
  - **Hot-Loaded Custom Tools (`tools/`):** Drop a `.py` or `.ps1` script into `./tools/` and it becomes callable on the next turn without restarting the agent. The agent can even author its own tools via `create_tool`.
  - **Native Cron Scheduling:** Run scheduled autonomous health checks, backups, and maintenance runs in the background.
- **Privacy-Guarded and LAN-First:**
  - Designed for local endpoints with configurable fallbacks. Strict privacy gates prevent local failures from falling through to public cloud APIs unless explicitly permitted (`allow_cloud_fallback`).

---

## Comparison: tinycmdr vs. Heavy Agent Stacks

| Feature | Heavy Gateways and Frameworks (e.g. OpenClaw, OpenHands) | **tinycmdr** |
| :--- | :--- | :--- |
| **Fixed Prompt Overhead** | 15,000 – 30,000+ tokens | **~4,150 tokens** (measured, static prompt + core schemas) |
| **KV Prefix Cache** | Invalidation on every turn (front-loaded status/time) | **Prefix-Cache Stable** (volatile context anchored at tail) |
| **Time to First Token (TTFT)** | 10–30s latency spikes on prompt re-eval | **Near-instantaneous** (serves from warm prefix cache) |
| **KV Cache VRAM Footprint** | Massive VRAM reserved for harness plumbing | **Minimal** (dynamic disclosure keeps schemas lean) |
| **Runtime Reliability** | Loops spin unchecked; wedged agents lock GPU slots | **Loop Guard + Stall Watchdog + Slot-Freeing Stop** |
| **Task State Management** | In-memory only or complex database tables | **Transparent Task Ledger** (`tasks.json` on disk) |
| **Tool Spill Handling** | Silent string truncation or context overflow | **Spill Index** (`spill/` storage with `spill#N` pointers) |
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

Most agent frameworks were designed for cloud LLMs like GPT-4 or Claude. Running them on self-hosted inference (llama.cpp, vLLM, Ollama) exposes severe bottlenecks:

- **Prompt Ingestion Latency (TTFT Spikes):** Injecting 15,000–30,000 tokens of boilerplate prompts and schemas on an uncached turn stalls local GPUs for 10–30 seconds before generating the first token.
- **Prefix Cache Thrashing:** Many frameworks place dynamic timestamps, counters, or random IDs near the top of the prompt. This invalidates KV cache on every turn, forcing full prompt re-computation over and over.
- **KV Cache VRAM Exhaustion:** In long-context setups (32K–256K), KV cache consumes substantial VRAM. Wasting 20,000+ tokens on harness overhead crowds out actual reasoning space, file context, and multi-slot concurrency.
- **Inference Slot Wedging:** When an agent loops or crashes, generic harnesses leave generation calls hanging, locking active GPU slots on the model server.
- **Container and Dependency Sprawl:** Heavy frameworks demand multi-container Docker stacks, Redis, Postgres, and Node.js runtimes just to run basic shell commands on a machine.

tinycmdr eliminates this waste by providing a lightweight, transparent single-process runtime optimized for local compute.

---

## Quick Install

### Windows
1. Extract the release archive.
2. Double-click **`INSTALL-WINDOWS.cmd`**.
   *(Registers a light background service and puts `tinycmdr` on your PATH).*

### Linux (Ubuntu / Debian / systemd)
```bash
sudo bash install/install-tinycmdr.sh
```

### macOS (launchd)
```bash
bash install/install-tinycmdr-macos.sh
```

---

## License

MIT License. Free and open source for personal and enterprise hardware.
