# tinycmdr

A single-file Mattermost ops agent that runs on a Windows box: it takes requests
by DM, runs tools (shell, file reads, PowerShell), keeps durable notes and a task
ledger, and reports back with its own evidence. No framework, no server — one
Python file plus a skills folder.

- Version: see `VERSION` in `tinycmdr.py`
- Changelog: `CHANGELOG.md`
- Runs as a Mattermost **bot account**, driven by DM (or any channel it is in)

## Try it without a chat server

Mattermost is how a fleet drives this, but nothing here needs a chat server to run.
Three local paths, all built into the same file:

```powershell
python tinycmdr.py --cli              # interactive console, one session, no port
python tinycmdr.py --once "/status"   # one task, prints the answer, exits
python tinycmdr.py --web              # browser view on http://127.0.0.1:8787
```

`--web` serves the same agent as a local page, and it shows what the agent is doing
while it works: each tool call as it starts, its result, and the answer at the end.
You can type while a run is going and your message is handed to the agent mid-run,
and Stop ends the run after the current step. The page stays on loopback unless you
set `web.token`, which opens it to your LAN, or point `web.host` somewhere else.

The `cli/` folder inside this package is the same agent built for the console alone,
with no dependencies beyond Python itself. If you only ever want to talk to it from a
terminal, that file is the one to run.

All three need one thing first: an OpenAI-compatible model endpoint in `config.json`
(`llm.base_url`, `llm.model`, and an API key if the endpoint wants one).

## Install on a new Windows host

Extract the package anywhere and **double-click `install\install-tinycmdr.cmd`**. On a fleet host that
is the whole job: it reads `install\fleet-defaults.json` from the package (the fleet's Mattermost
host, model endpoint and allowed user), reuses the bot token from an existing `.env`, asks for one
only if there is none, registers the task, and verifies the install — nothing to hand-edit.

```
install-tinycmdr.cmd                     install / redo is just this
install-tinycmdr.cmd -Force              redo in place (stops the running bot first)
install-tinycmdr.cmd -Uninstall -Force   stop the task and remove the folder
install-tinycmdr.cmd -VerifyOnly         is it working? (no admin, no reinstall)
install-tinycmdr.cmd -MattermostTokenFile \\share\tinycmdr-token.txt
```
Switches always win over `fleet-defaults.json`. If the package has no `install\fleet-defaults.json`
(a standalone download rather than a fleet package), pass `-MattermostUrl`, `-MattermostTokenFile`,
`-AllowedUser` and `-ModelBaseUrl` yourself, or answer the installer's prompt.

From a shell, either of these works:

```powershell
powershell -ExecutionPolicy Bypass -File .\install\install-tinycmdr.ps1 -MattermostUrl chat.example.com
```

Do **not** run the `.ps1` by double-clicking it or via right-click → "Run with PowerShell": many
Windows machines ship with the script execution policy set to Restricted, so the window opens, prints
`sorry, running scripts is disabled on this system`, and closes before you can read it. If you see a
window vanish like that, that is what happened — use the `.cmd`, or pass `-ExecutionPolicy Bypass`
explicitly. Everything the installer does is transcribed to `%TEMP%\tinycmdr-install.log`, so a
failure always leaves the reason on disk.

It will:

1. find Python 3.10+ (or install 3.12 with `-InstallPython`)
2. install the single dependency (`requests`)
3. copy the app to `C:\tinycmdr` (override with `-InstallDir`)
4. write `config.json` (fresh web-UI token) and `.env` from the templates
5. write the hidden launcher (`tinycmdr-service.vbs`) and a manual one
   (`launch_tinycmdr.bat`)
6. register the scheduled task `tinycmdr` — logon +30s and boot +4min, S4U so it
   runs headless
7. start it and smoke-test `/status` over the local web UI

Then, to finish:

```
1. put the Mattermost bot token in  C:\tinycmdr\.env    (tinycmdr_MM_TOKEN=...)
2. put your Mattermost user id in  config.json          (mattermost.allowed_users)
3. point it at a model             config.json          (llm.base_url / llm.model)
   - the default is http://127.0.0.1:8081/v1, which only works if the model runs
     on this machine; pass -ModelBaseUrl for one on the network
4. restart:  Start-ScheduledTask tinycmdr
```

Useful switches: `-InstallDir`, `-TaskName`, `-BotName`, `-ModelBaseUrl`, `-Model`, `-Python <path>`,
`-MattermostTokenFile` (read the token from a file), `-Force` (redo), `-Uninstall`, `-VerifyOnly`,
`-SkipTask` (files only, no admin needed), `-NoStart`, `-NoPause`, `-EnableWeb` (opt-in local chat
page), `-WebPort`.

`install\fleet-defaults.json` carries no secrets — the bot token is written to `.env` at install
time, from `-MattermostToken`, `-MattermostTokenFile`, an existing `.env`, or one prompt.

## Install on a new Linux host (Debian/Ubuntu, systemd)

Copy the package anywhere on the host and run:

```
sudo bash install/install-tinycmdr.sh --token-file /tmp/token.txt --bot-name myhost
```

It reads `install/fleet-defaults.json` (Mattermost host, model endpoint, allowed user) when the
package has one; a standalone download has no fleet file, so pass `--mattermost-url`,
`--allowed-user` and `--model-base-url` instead. It builds
`/home/<user>/tinycmdr` with its own venv (`requests`, `mmpy_bot`, `croniter`), writes
`config.json` plus a mode-600 `.env` (the token never goes in `config.json`), installs and
**enables** the systemd unit `tinycmdr.service` — so it comes up at boot with nobody logged in
and systemd restarts it if it dies — then checks `/api/health` and the log.

```
install/install-tinycmdr.sh --verify-only      is it working? (no root needed)
sudo bash install/install-tinycmdr.sh --force  redo in place, keeping state and token
sudo bash install/install-tinycmdr.sh --uninstall
```

Switches: `--install-dir`, `--user`, `--bot-name`, `--model-base-url`, `--model`, `--web-port`,
`--no-web`, `--no-start`, `--no-deps`. The token comes from `--token` or `--token-file`; on a
redo it is reused from the existing `.env`. Ubuntu 22.04 ships python3 3.10 without
`python3-venv`, and the installer apt-installs that itself. Everything is transcribed to
`/tmp/tinycmdr-install.log`.

Operate it through systemd, not by hand:

```
systemctl status tinycmdr
journalctl -u tinycmdr -f
sudo systemctl restart tinycmdr        # or: sudo bash maintenance/restart-tinycmdr.sh
venv/bin/python tinycmdr.py --once "/status"
```

Two hosts must never share one bot token, and one host must never run two agents on one bot
account: two agents on one token would both answer every DM. Give each host its own bot account.

### What the target host needs

- Windows 10/11, PowerShell 5.1+ (built in)
- Python 3.10+ and the `requests` package (the installer handles both)
- Reachable Mattermost (usually over HTTPS) and, if using a local model, a
  reachable OpenAI-compatible endpoint (llama.cpp, etc.)
- A Mattermost **bot account** (System Console → Integrations → Bot Accounts)
  added to the team/channel you will talk to it from

The web UI binds `127.0.0.1` by default, so no firewall rule is needed. Set
`web.host` to `0.0.0.0` only if you want it on the LAN, and firewall it.

## Configuration

`config.json` is merged over the code's defaults, so it only needs the values you
actually change. Secrets do **not** go in it — environment variables (from `.env`)
override it: `tinycmdr_MM_TOKEN`, `TAVILY_API_KEY`, `ANYSEARCH_API_KEY`,
`tinycmdr_MODEL`, `tinycmdr_BASE_URL`.

Notable knobs:

```
llm.base_url / llm.model      which endpoint answers, e.g. http://10.0.0.5:8081/v1 + "main"
llm.max_context_tokens        must fit the server's per-request window (n_ctx / slots)
agent.max_steps / max_minutes run ceilings
agent.loop_dedupe_after       identical tool calls are refused after this many runs (default 2)
agent.loop_stop_repeats       identical calls before the run is stopped as a loop (default 6)
agent.checkin_minutes         progress check-in cadence on long runs
agent.debug_dump_dir          write every request body to disk (blank = off); use it
                              when the bot answers nonsense
mattermost.allowed_users      who may command the bot — leave empty and it ignores everyone
```

`config.example.json` is the whole thing: every key, what it does, what it defaults to,
with fake values (unroutable documentation addresses) so copying it cannot point at
somebody's real service. The installer copies it to `config.json` and fills in the four
values it knows; delete anything you do not care about and the code default applies.

**Sampling is deliberately not configurable.** temp/top_p/top_k are inherited
from whatever endpoint `llm.base_url` points at (llama.cpp reads the model file's
own metadata; a cloud provider uses its defaults), and the code strips those keys
from every request. Point it at a different model and it just works.

## Adding skills (drag and drop)

tinycmdr ships no skills and does not need any. It reads the `SKILL.md` convention common agent skill
libraries use, so a skill folder from one of those (or one you write) works as it is:

```
skills/<category>/<name>/SKILL.md          (or just skills/<name>/SKILL.md)

---
name: mail-server-admin
description: "Use when administering a self-hosted mail server."
---
```

Drop the folder in and it is live on the next message: no restart, no config, no index. The model
sees one line per skill and reads the body only when a task looks relevant, so a large library
costs prompt lines, not context. Copy the folder in rather than symlinking it, because a symlinked
skill folder is invisible to the scanner.

What a runbook cannot carry is the tools it names. Prose transfers as text; a step that calls
`computer_use`, `browser_navigate` or anything else this build does not have is an instruction for
a program that is not here, and the agent should say so rather than improvise. Every skill read
ends with the list of tools this install actually has, so the mismatch is visible on the page you
are reading it on, and a capability you need is a file in `tools/` (or one the agent writes with
`create_tool`), not something to hand-run from the runbook.

The `skills/README.md` in the download has the full contract.

## Operating it

```
/status      version, model, inherited sampling, session size, limits, uptime
/new         start a fresh conversation (clears this channel's history)
/undo        drop the last exchange
/stop        stop the run in progress
/model       show or change the model for this channel
/help        the rest
```
Durable memory lives in `notes.md` (capped, older entries spill to
`notes-archive.md`) and `tasks.json`/`tasks.md` (the ledger). Both are the bot's
own working state — back them up, don't hand-edit them while a run is active.

Logs: `tinycmdr.log` in the install folder. Restart: `maintenance\restart-tinycmdr.ps1`
(elevated) or `Stop-ScheduledTask tinycmdr; Start-ScheduledTask tinycmdr`.

## Verifying an install

```powershell
# the tests that ship with it (no Mattermost, no token, no port)
python tests\test_stall.py ; python tests\test_ledger.py ; python tests\test_checkin.py
#   Linux: venv/bin/python tests/test_stall.py   (same for ledger and checkin)
#   They are hermetic: run them here, in CI, or straight from an unzipped package
#   (the suites stage their own throwaway config.json; nothing is port-bound).
#   Install requirements.txt first. A suite runs on Windows and Linux, and a check
#   whose subject is absent on your host (no PowerShell, no chat driver) reports
#   "skip" with the reason rather than failing, so a skip is never a hidden pass.

# one local turn through the agent - proves the app AND the model endpoint work
python tinycmdr.py --once "reply with the single word: READY"
python tinycmdr.py --once "/status"
python tinycmdr.py --cli          # interactive local session
```

The local browser page is off by default (`web.enabled: false`) because the chat build is driven
from Mattermost and should not quietly open a port. `python tinycmdr.py --web` turns it on for that
process, which is the quickest way to try the whole thing: no server, no bot account, no token.
Pass `-EnableWeb` to the installer if you want the page served alongside the chat bot, which
generates a token in `web-token.txt` and binds loopback only.

## Uninstall

```powershell
Stop-ScheduledTask tinycmdr ; Unregister-ScheduledTask tinycmdr -Confirm:$false
Remove-Item C:\tinycmdr -Recurse -Force      # take .env and notes.md with it if that is fine
```

## Files

```
tinycmdr.py              the whole agent (single file, versioned)
config.example.json      every config key with fake values; installer copies it to config.json
.env.example             secrets template; installer copies it to .env
skills/                  markdown runbooks the agent loads on demand (see "Adding skills")
tests/                   test suites (run them after any edit)
install/                 installer for new hosts
maintenance/             host-maintenance scripts (restart helper is generic)
cli/tinycmdr-cli.py      the same agent, console only, no chat layer at all (see "Try it")
cli/README.txt           what that file is and which one to run
CHANGELOG.md             what changed and why, version by version
```
