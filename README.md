<div align="center">

  <img src="assets/brand/avatar.png" alt="tinycmdr mascot" width="160" />

  # tinycmdr

  ### The High-Efficiency Agent Harness for Local & Self-Hosted LLMs
  *Sub-second TTFT · ~4K token overhead · 100% prefix-cache stable · Zero infrastructure.*

  [![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=flat-square)](#quick-install)
  [![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)](https://www.python.org/)
  [![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

  *Run tasks, execute code, and command your machines across Web UI, Terminal CLI, or Chat*
  <br>
  *Built specifically for llama.cpp, vLLM, Ollama, and local OpenAI-compatible endpoints.*

</div>

---

### The Problem with Agents on Local Models

Most agent frameworks (LangChain, CrewAI, AutoGen, OpenHands) were designed for massive cloud APIs with 128K context windows and infinite compute. When you point them at self-hosted models running on your own GPUs, they crawl:

- **Prompt Evaluation Latency (TTFT):** They dump 15,000–30,000 tokens of schemas and dynamic text at the head of every request, forcing your GPU to re-evaluate the prompt from scratch on every turn (10–20s delay).
- **Context Exhaustion:** They burn out typical 8K–32K local VRAM context budgets in 2 to 3 turns.
- **Inference Slot Wedges:** If a run hangs or loops, your local GPU slot stays wedged until restarted.
- **Infrastructure Sprawl:** They require multi-container Docker stacks, Postgres, Redis, and heavy dependency trees.

---

### Built for Local Inference (llama.cpp, vLLM, Ollama)

**tinycmdr is engineered from the ground up for self-hosted hardware:**

| Feature | Mainstream Frameworks | **tinycmdr** |
| :--- | :--- | :--- |
| **Fixed Prompt Overhead** | 15,000 – 30,000 tokens | **~4,150 tokens** (measured, static + visible schemas) |
| **Time to First Token (TTFT)** | 10 – 30 seconds (cache churn) | **Sub-second** (strict prefix-cache stable layout) |
| **KV Cache Behavior** | Invalidated every turn | **100% Warm** in llama.cpp / vLLM prefix cache |
| **Context Window Preservation** | Rapid exhaustion (dumps all tools) | **Tool Disclosure** (rare tools revealed on demand) |
| **Infrastructure Overhead** | Multi-container Docker, Vector DBs | **Single Python file**, zero database, no daemons |
| **Server Slot Protection** | Orphan runs lock GPU slots | **Truthful Stop & Steer** instantly frees the engine |

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
tinycmdr run "check why disk space is low and summarize largest folders"
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
