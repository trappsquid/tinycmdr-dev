<div align="center">

  <img src="assets/brand/avatar.png" alt="tinycmdr mascot" width="160" />

  # tinycmdr

  **The lightweight, single-file AI ops agent for your machines.**

  [![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=flat-square)](#quick-install)
  [![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)](https://www.python.org/)
  [![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

  *Run tasks, inspect systems, and manage your machines through local Web UI, terminal CLI, or chat.*
  <br>
  *No Docker required. No databases. No complex dependencies.*

</div>

---

### Why tinycmdr?

Most agentic frameworks require heavy infrastructure: multi-container Docker stacks, vector databases, and rigid cloud dependencies. 

**tinycmdr** takes the opposite approach:
- **Single-File Simplicity:** One Python file runs the entire agent runtime, task ledger, and tool suite.
- **Zero Heavy Setup:** Connects directly to any OpenAI-compatible local model (llama.cpp, Ollama, vLLM) or cloud endpoint (DeepSeek, etc.).
- **Multiple Ways to Interact:** Switch effortlessly between an interactive terminal session, a live browser dashboard, or chat DMs on Mattermost and Telegram.
- **Operator-First Safety:** Truthful tool execution, loop guards, step budgets, and persistent local memory that never leaves your machine.

---

## Quickstart (Under 1 Minute)

### 1. Configure
Run the guided interactive setup to connect your LLM endpoint (local or remote):
```bash
tinycmdr setup
```
*(Answer 2–3 simple questions or press Enter to accept sensible defaults).*

### 2. Launch
Choose how you want to interact:

```bash
# Option A: Interactive terminal session (fastest)
tinycmdr

# Option B: Live Web UI dashboard (browser view on http://127.0.0.1:8787)
tinycmdr web

# Option C: One-off task from your command line
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
| `tinycmdr model` | View active model and token usage |
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

The agent indexes the description and only loads the full runbook when your task requires it. Zero configuration, zero restart needed.

---

## License

MIT License. Free and open source for personal and enterprise ops.
