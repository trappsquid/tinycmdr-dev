<div align="center">

  <img src="assets/brand/avatar.png" alt="tinycmdr mascot" width="160" />

  # tinycmdr

  ### The High-Efficiency Agent Harness for Local & Self-Hosted LLMs
  *Dramatically faster TTFT · ~4K token overhead · Prefix-cache optimized · Zero infrastructure.*

  [![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=flat-square)](#quick-install)
  [![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)](https://www.python.org/)
  [![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

  *Run tasks, execute code, and command your machines across Web UI, Terminal CLI, or Chat.*
  <br>
  *Built specifically for llama.cpp, vLLM, Ollama, and local OpenAI-compatible endpoints.*

</div>

---

### The Problem with Heavy Agent Harnesses on Self-Hosted LLMs

Most agent harnesses and multi-channel gateways (like OpenClaw or heavy multi-process stacks) are architected around massive cloud APIs or heavy Node.js/Docker runtimes. When you run them against self-hosted models (llama.cpp, vLLM, Ollama) on your own hardware, you hit immediate friction:

- **Prompt Ingestion Latency (TTFT):** Injecting 15,000–30,000 tokens of boilerplate system prompts, entire workspaces, and large tool schemas on an uncached turn stalls your GPU for 10–30 seconds before generating a single token.
- **Prefix Cache Thrashing:** Many harnesses scatter timestamps, dynamic status lines, or fluctuating conversation counters early in the prompt. This breaks KV prefix caching on every turn, forcing your engine to recompute the entire prompt over and over.
- **Bloated KV Cache VRAM Rent:** In modern local setups running 100K–256K contexts, KV cache memory is precious VRAM. Burning 20,000+ tokens on harness plumbing reduces room for actual reasoning, history depth, or concurrent slots.
- **Inference Slot Wedges:** When an agent loops or hangs, it locks an active GPU slot on your model server. Generic harnesses lack truthful cancellation, leaving orphan generations spinning.
- **Infrastructure Sprawl:** Multi-container topologies, external databases, Node.js gateways, and hundreds of dependencies just to run basic tools on your machine.

---

### Built for Local Inference (llama.cpp, vLLM, Ollama)

**tinycmdr is engineered from the ground up for self-hosted hardware:**

| Feature | Heavy Gateways & Harnesses (e.g. OpenClaw) | **tinycmdr** |
| :--- | :--- | :--- |
| **Fixed Prompt Overhead** | 15,000 – 30,000 tokens | **~4,150 tokens** (measured, static + visible schemas) |
| **Time to First Token (TTFT)** | Latency spikes on prompt re-eval | **Significantly faster** (skips prompt re-eval via warm prefix cache) |
| **KV Prefix Cache Behavior** | Invalidation on every turn | **Cache-Stable Prefix** (static prompt + schemas; volatile context at tail) |
| **KV Cache VRAM Footprint** | Heavy VRAM consumed by workspace injection | **Minimal KV Footprint** (dynamic disclosure keeps schemas lean) |
| **Extensibility** | Complex plugin architectures | **Hermes-Compatible Skills** (plain `SKILL.md` runbooks loaded on demand) |
| **Inference Slot Protection** | Wedged agent runs lock server slots | **Truthful Stop & Steer** frees slots immediately |
| **Host Footprint** | Multi-container Docker / Node.js runtime | **Single Python file**, zero database, no daemons |

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
| **Terminal CLI** | Fast sysadmin tasks, debugging, and terminal workflows. Beautiful TUI cards and full command history. | `tinycmdr` |
| **Web Dashboard** | Real-time streaming view of tool execution, file edits, and system state. Accessible from any LAN browser or phone. | `tinycmdr web` |
| **Chat Bot** | Managing your machines remotely via Mattermost or Telegram DMs with live progress updates. | Background service (auto-started) |

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

## Essential Commands

The same commands work everywhere: in your OS shell (`tinycmdr <verb>`), inside the interactive CLI (`/tinycmdr <verb>`), and in chat:

| Command | Description |
| :--- | :--- |
| `tinycmdr status` | System overview: active model, endpoint, context budget, and task counts |
| `tinycmdr model` | View active model status, context window, and routes |
| `tinycmdr model list` | Live query of available models across all configured endpoints |
| `tinycmdr model <name>` | Switch the active model for your session |
| `tinycmdr model add <url>` | Add a new model endpoint or fallback route |
| `tinycmdr setup` | Launch the guided configuration wizard |
| `tinycmdr logs [n]` | View the last *n* lines of the execution log |
| `tinycmdr restart` | Cleanly recycle the background service |

---

## Extending with Skills

tinycmdr supports drop-in skills. Simply create or drop runbook markdown files into the `skills/` directory:

```text
skills/
└── web-server/
    └── SKILL.md
```

The agent indexes the description into a compact reference and only loads the full runbook when your task requires it. Zero configuration, zero restart needed.

---

## License

MIT License. Free and open source for personal and enterprise hardware.
