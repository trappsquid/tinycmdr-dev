<div align="center">

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

Reach it from a **terminal** or **chat** (Mattermost, Telegram) — one process, one
vocabulary, the same conversations either way.

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

Every release carries a `SHA256SUMS` asset covering all eight published files. Verify what you
downloaded before running it:

```bash
base=https://github.com/trappsquid/tinycmdr/releases/latest/download
curl -fsSLO $base/tinycmdr-linux.tar.gz && curl -fsSLO $base/SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing     # macOS: shasum -a 256 -c SHA256SUMS
```

Releases are checksummed but **not signed** — there is no project key, so the sums protect
against a corrupted or truncated download, not against the release itself being replaced.

<details>
<summary><b>Unattended installs, every switch, and a second install on one host</b></summary>

**Linux.** `--mode user|system` decides without a prompt and `--yes` takes the defaults for your
platform; a run with no terminal at all never asks. **macOS** has one kind of install — a
per-user launchd agent, `--no-launchd` to skip it — so it takes `--yes` and everything below,
and has no `--mode`. Every question has a switch: `--mattermost-url`, `--allowed-user`,
`--telegram-token`, `--telegram-ids`, `--model-base-url`, `--model`, `--no-path`.

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
-TelegramToken <t>     a Telegram bot token
-TelegramIds <ids>     your numeric Telegram id(s), comma or space separated
-AddEndpoint <spec>    another endpoint, repeatable: "<base_url>;<model>;<alias>;<key>"
-NonInteractive        ask nothing: take the switches and the defaults
```

Day-to-day notes for a Mac: [`install/README-macos.md`](install/README-macos.md).
</details>

## Start it

```bash
tinycmdr setup        # once: endpoint, chat gateways, search consent
tinycmdr              # the session as a full-screen terminal app
tinycmdr --cli        # the same session, inline cards in the scrollback
`tinycmdr --once "…"   # one task, then exit
```

A bare `tinycmdr` opens that session as a full-screen app in the terminal. Type and press Enter to
send; `↑`/`↓` scroll a line, `PgUp`/`PgDn` a page, `Ctrl-Home`/`Ctrl-End` jump to either end;
`Ctrl-C` stops the run in flight (and quits when nothing is running), `Ctrl-D`/`Ctrl-Q`/`Esc` quit,
leaving the last answer in the scrollback. The app draws in an alternate screen, so the terminal's
own scrollback is not there while it runs - the app's keys are the way back up. The mouse wheel
scrolls the transcript too, but only with capture on: `TINYCMDR_APP_MOUSE=1 tinycmdr`. Capture is
off by default so native selection and copy keep working without a modifier key.

`/tinycmdr model` opens the same picker inside the app: the list of models this install can route
to, the one in use marked, `↑`/`↓` to move, typing to filter, Enter to switch. If the endpoint itself
is wrong, `/tinycmdr model endpoint <url>` reads or replaces it - it is refused unless it answers
(`--force` overrides) - and then offers that endpoint's models to pick from.

To take an item OUT of the app, `Ctrl-Y` copies the newest one - the answer, a tool call, a tool
result, a question - as its own text rather than the frame it was painted in, and pressing it again
walks back through the transcript an item at a time; the status line names what landed and where it
was. `Ctrl-B` copies the whole transcript. The text goes to every clipboard door this host has: its
own tool (`pbcopy`, `clip`, `wl-copy`/`xclip`), the terminal over OSC 52 (which works through ssh,
and which tmux needs `set-clipboard on` for), and `tinycmdr-copy.txt` in the temp directory, mode
0600, because a terminal that refuses OSC 52 says nothing at all.

A chat lane is where a working agent is easiest to watch: tool calls stream in as they happen,
and a message sent mid-run steers the run instead of queueing behind it. Neither lane is
primary: whichever token you configure is the lane that runs, and both share the same
sessions, notes, tasks and skills as the terminal.

A chat account is optional. With **no** Mattermost and no Telegram token the install is a
**CLI-only** one: `tinycmdr` opens a session and `tinycmdr --once "<task>"` runs one task,
with nothing remote to serve. With **both** tokens set there is nothing to guess, so a bare
start refuses and names `--telegram` / `--mattermost`.

## Use it

The same verbs work in a shell (`tinycmdr <verb>`), in the interactive CLI (`/tinycmdr <verb>`),
and in chat:

| Verb | What it does |
| :--- | :--- |
| `status` | version, folder, model, endpoint, context, log, instance |
| `doctor` | check this install and name what is wrong (exit 1 when something is) |
| `health` | one line and an exit code, no network — for scripts |
| `model` | pick a model from a list you move through (↑↓, type to filter, Enter) |
| `model use <name>` · `model add <url>` · `model remove <x>` | switch, add or drop an endpoint |
| `model endpoint [<url>]` | read the endpoint, or correct it - a typo is refused unless `--force` |
| `tasks [--all]` | the task ledger: open, in progress, recently done |
| `logs [n]` · `version` · `proc` | log tail, version, this install's process and lock state |
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
its fixed prompt is **~3.5K tokens on a clean unpack**, measured with the endpoint's own
tokenizer (the harness's estimator, which the 5,400-token gate uses, reads 4,145 - it is
deliberately conservative; `tinycmdr status` prints this box's own), and 3,633 on this install,
volatile context sits at the tail so
the prefix stays cacheable, and the runtime guards the slot. Local failures never fall through to
a public API unless you set `allow_cloud_fallback`. Web search is the same shape, one lane over:
the installers ask whether it may leave the machine (and `tinycmdr setup` asks later), search is
ON once you say yes, and `search.allow_cloud_egress: false` is the opt-out that REFUSES every
off-LAN provider - `fetch_url` included. A SearxNG on your own LAN never needs the flag, and
neither does a fetch from it.

The long version — measured surface, budgets, failure handling, what it deliberately does not
have, and how it compares with other harnesses — is
[`docs/tinycmdr-what-it-is.md`](docs/tinycmdr-what-it-is.md).

---

## Run the gate

The suites need no model, no endpoint and no chat token, and `tests/run_all.py` is the gate a
release is cut against - the same command CI runs on macOS and Linux (the Windows job runs the
pure-Python subset):

```bash
python3.12 -m venv venv                    # 3.10-3.12; run_all.py refuses anything else
venv/bin/pip install -r requirements.txt -r requirements-test.txt
venv/bin/python tests/run_all.py           # non-zero if any suite fails
```

A suite that cannot run exits 77 and counts as **red**, so a machine that grades nothing cannot
report success. `--select 'tests/test_ledger*.py'` narrows the run while you work on one suite.

Before pushing, `bash maintenance/pre-push.sh` decides the cheap things - the leak gate, that the
published numbers are still regenerated from the tree, and that the work ledger's anchors agree
with the repository - in about a second. Install it as the hook once per clone:

```bash
printf '#!/bin/sh\nexec bash "$(git rev-parse --show-toplevel)/maintenance/pre-push.sh"\n' \
    > .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

## What is live, what is dev, what is on disk

On a box with more than one checkout - the install the bot runs from, the tree releases are cut
from, a backup clone - the roles are stated once and every fact is read from the tree itself:

```bash
python3 maintenance/where.py           # the table: version, commit, tag, changes, distance from origin
python3 maintenance/where.py --check   # non-zero when a tree that must be clean is not
python3 maintenance/where.py --remote  # ... plus what GitHub has NOW (network, read-only)
```

There is no map to keep in sync, on purpose: a hand-written one lived outside this repository,
went two releases stale, and named a scratch tree that had been deleted. `where.py --check` also
runs in `maintenance/pre-push.sh`, because a live tree with an uncommitted change to a tracked
file is invisible until the next `git pull` there fails.

The shipped table declares `live` and `dev` - true of any box. A box with a backup clone or an
ops workspace declares its own in `maintenance/where-roles.json` (gitignored, because a path on
somebody's share is not source):

```json
[{"role": "backup", "path": "~/somewhere/tinycmdr", "why": "pre-rewrite history"}]
```

A host entry may also **override** a shipped role by name, and a role may declare that it shares
another's tree - the shape a box uses when it develops in the install itself:

```json
[{"role": "dev", "same_as": "live", "why": "one tree: code work happens in the install"}]
```

The full development contract - the flow, the gate, what is deliberately not in git, and where the
truth about this box lives - is [`docs/development.md`](docs/development.md).

---

## License

MIT. Free and open source for personal and enterprise hardware.
