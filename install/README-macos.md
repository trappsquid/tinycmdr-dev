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

    bash install/install-tinycmdr-macos.sh

It asks, at the terminal, for everything the bot needs and writes nothing until you
answer `Install now?`:

    Mattermost bot token (input hidden, Enter to skip for the local page)
    Mattermost server, no https:// (e.g. chat.example.com)
    Your Mattermost user id (optional, but without it the bot ignores your DMs)
    Model endpoint [http://127.0.0.1:8081/v1]
    Model id [main]
    API key for it (blank if it needs none)     only asked when the endpoint is not
                                                on this machine

Press Enter to take the value in brackets, and skip the token to install the local
page instead of a chat lane. Then it builds `~/tinycmdr`, writes `config.json` and
`.env` (mode 600), and loads the launchd agent.

Nothing is asked in a script or a pipe: every answer has a switch, and a run with no
terminal takes the switches and the defaults. With `install/fleet-defaults.json` in
the package the Mattermost host and the allowed user come from it, and `--yes` never
prompts at all:

    bash install/install-tinycmdr-macos.sh --token-file ~/bot-token --yes

The `tinycmdr` command is written to `/usr/local/bin` when that folder is writable
(a Homebrew machine), and to `~/.local/bin` otherwise. `~/.local/bin` is not on a
stock Mac's PATH, so the installer adds one marked line to `~/.zshrc`; open a new
terminal before typing `tinycmdr`, or run `~/tinycmdr/tinycmdr status` right away.
`--no-path` writes neither.

Options worth knowing:

    --allowed-user <mattermost-user-id>   who may command the bot (deny-by-default)
    --install-dir <path>                  default ~/tinycmdr
    --web-port <p> / --no-web             local API + health endpoint (default 8787)
    --token-file <file>                   read the token from a file instead of argv
    --use-fleet-model                     use the LAN model endpoint in
                                          fleet-defaults.json instead of the cloud one
    --model-base-url <url> --model <name> point it anywhere else
    --secrets-file <file>                 extra KEY=VALUE lines for .env (search API keys)
    --verify-only                         report on an install, change nothing
    --uninstall                           stop the agent, remove the agent and the folder

The token is read from a prompt if you do not pass one, so it never has to appear in
your shell history.

**Not a terminal person?** Double-click `INSTALL-MACOS.command` in the package, and
`UNINSTALL-MACOS.command` to remove it. Finder runs a `.command` in Terminal; it opens
a `.sh` in TextEdit, which is where most people get stuck. (If the file came from a
browser rather than the `curl` line above, macOS quarantines it: the first time,
right-click it and choose Open.)

## 4. Removing it

    sudo bash install/uninstall-tinycmdr-macos.sh     # or double-click UNINSTALL-MACOS.command

That stops the agent and removes the launchd job, the install folder and the PATH
wrapper. Use `sudo` when you installed with it: `/usr/local/bin` is root-owned, so a
user-mode uninstall cannot unlink the `tinycmdr` wrapper there - it now says so and
prints the one line to run by hand instead of stopping half-way through. A user-mode
install created no wrapper, so plain `bash install/uninstall-tinycmdr-macos.sh` is
enough. The Mattermost bot account and its token are yours to revoke separately.

## 5. Day to day

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

## 6. Which model answers

The installer asks, because only you know: a llama.cpp on this Mac, a box on your LAN,
or a hosted provider. Any OpenAI-compatible `/v1` root works. Enter takes
`http://127.0.0.1:8081/v1` with model `main` (the usual local llama.cpp shape).

Point it at a hosted endpoint and it asks for that endpoint's key, which is kept in
`config.json`'s `llm.api_key` - a hosted PRIMARY has no env var of its own, so that is
where the build looks. A key in `.env` is the tidier shape when the endpoint is a
FALLBACK entry instead (see `llm.fallbacks[].api_key_env` in `config.example.json`).

To skip the question on a machine that knows the answer: `--model-base-url` and
`--model`, or `--use-fleet-model` to take both from `install/fleet-defaults.json`.

## 7. What this does NOT do

- It does not install search API keys. Without them `web_search` returns an error; pass
  `--secrets-file` pointing at a file holding `TAVILY_API_KEY=...` / `ANYSEARCH_API_KEY=...`.
- It asks for the model endpoint's key but not for search keys: one is required for the
  bot to answer, the other only for the search tool.
- It does not create the bot account or the token (step 2).
- It does not touch anything outside `~/tinycmdr`, `~/Library/LaunchAgents` and the
  logs. `--uninstall` removes exactly those.
