# tinycmdr on a Mac

tinycmdr is one Python file plus three dependencies. On macOS it runs as a per-user
launchd agent, so it starts when you log in and comes back if it dies. Nothing here
needs `sudo`.

## 1. Python 3.12

    brew install python@3.12

Use 3.12 (or 3.10/3.11). Do **not** build the venv from 3.13 or newer: on those
versions pip resolves an ancient, broken `mmpy_bot`, and the bot starts without ever
connecting to Mattermost, which looks like a config problem and is not one. The
installer refuses 3.13+ and tells you this.

No Homebrew? Install Python 3.12 from python.org instead; the installer finds it.

Nothing at all, and no Homebrew either? The installer OFFERS to fetch a private
Python 3.12 for you when it cannot find one (or run it with `--install-python` to
skip the question). It uses `uv`, needs no password, and puts the interpreter
inside the install folder, so removing that folder removes it too.

## 2. A Mattermost bot account for this Mac

The Mac needs its OWN bot account. If it reuses one that another agent already
listens on, both answer the same message and you cannot tell which box replied.

In Mattermost: **System Console > Integrations > Bot Accounts > Add Bot**, then mint a
token for it (**Profile > Security > Personal Access Tokens** for a bot account, or
`POST /api/v4/users/{bot_user_id}/tokens`), and add the bot to the team so it appears
in your DM list.

## 3. Install

Unzip the package, open Terminal in that folder, and run:

    bash install/install-tinycmdr-macos.sh --token <the-bot-token>

That builds `~/tinycmdr`, writes `config.json` and `.env` (mode 600), and loads the
launchd agent. With `install/fleet-defaults.json` in the package it also picks up the
Mattermost host and the allowed user, so the token may be the only thing you pass.

Options worth knowing:

    --allowed-user <mattermost-user-id>   who may command the bot (deny-by-default)
    --install-dir <path>                  default ~/tinycmdr
    --web-port <p> / --no-web             local API + health endpoint (default 8788)
    --token-file <file>                   read the token from a file instead of argv
    --use-fleet-model                     use the LAN model endpoint in
                                          fleet-defaults.json instead of the cloud one
    --model-base-url <url> --model <name> point it anywhere else
    --secrets-file <file>                 extra KEY=VALUE lines for .env (search API keys)
    --verify-only                         report on an install, change nothing
    --uninstall                           stop the agent, remove the agent and the folder

The token is read from a prompt if you do not pass one, so it never has to appear in
your shell history.

## 4. Day to day

    bash ~/tinycmdr/maintenance/restart-tinycmdr-macos.sh            # restart
    bash ~/tinycmdr/maintenance/restart-tinycmdr-macos.sh status     # loaded? answering?
    bash ~/tinycmdr/maintenance/restart-tinycmdr-macos.sh logs       # last lines of stderr
    bash install/install-tinycmdr-macos.sh --verify-only             # full check

Logs: `~/tinycmdr/tinycmdr.log`, plus `~/tinycmdr/logs/launchd.out.log` and
`launchd.err.log`. The launchd agent itself is
`~/Library/LaunchAgents/com.tinycmdr.agent.plist`.

`/restart` in Mattermost also works: the bot spawns its own replacement and exits 0,
and the agent is set to restart only on a **non-zero** exit, so the two do not race.
For a process wedged inside a system call, use the restart script above.

## 5. Why the model defaults to cloud

A laptop leaves the LAN, so the installer defaults `llm.base_url` to the cloud
endpoint rather than an address that only resolves at home. To run against a model on
your own network, re-run the installer with `--use-fleet-model` (it reads the endpoint
from `install/fleet-defaults.json`), or pass `--model-base-url` and `--model` directly.

## 6. What this does NOT do

- It does not install search API keys. Without them `web_search` returns an error; pass
  `--secrets-file` pointing at a file holding `TAVILY_API_KEY=...` / `ANYSEARCH_API_KEY=...`.
- It does not create the bot account or the token (step 2).
- It does not touch anything outside `~/tinycmdr`, `~/Library/LaunchAgents` and the
  logs. `--uninstall` removes exactly those.
