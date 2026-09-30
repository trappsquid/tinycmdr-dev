# tinycmdr on a Mac

tinycmdr is one Python file plus three dependencies. On macOS, with a chat account
(a Mattermost or Telegram bot token), it runs as a per-user launchd agent that starts
when you log in and comes back if it dies. With no chat token there is nothing remote to
serve: the files are installed but no agent is registered, and you drive it with
`--app` (the full-screen session a bare `tinycmdr` opens), `--cli` (the same session as
inline cards) / `--once`. Nothing here needs `sudo`.

## 1. Python 3.10-3.12

    brew install python@3.12

tinycmdr runs on **3.10, 3.11 or 3.12** - the band is the band, in both directions:

  * 3.13 or newer: pip resolves an ancient, broken `mmpy_bot`, and the bot starts
    without ever connecting to Mattermost, which looks like a config problem and is not
    one. The installer refuses these and says so (`--force-python` tries anyway).
  * 3.9 or older: the config writer calls `Path.write_text(newline=...)`, a 3.10
    keyword, so the install ends in a `TypeError` deep inside the writer. The installer
    refuses 3.9 by name too.

No Homebrew? Install Python 3.12 from python.org instead; the installer finds it.

Nothing at all, and no Homebrew either? The installer OFFERS to fetch a private
Python 3.12 for you when it cannot find one (or run it with `--install-python` to
skip the question). It uses `uv`, needs no password, and puts the interpreter
inside the install folder, so removing that folder removes it too.

The offer is reachable with no terminal: `--install-python` fetches unconditionally
(it beats a `--python` that points somewhere unusable), and `-y` consents to the fetch
when there is no interpreter at all. `-y` on a box with only python 3.9 still refuses -
that version cannot be made to work, and the message names the band and the switch.

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

    Mattermost bot token (input hidden, Enter to skip)
    Mattermost server, no https:// (e.g. chat.example.com)
    Your Mattermost user id (optional, but without it the bot ignores your DMs)
    Also install a Telegram bot lane (a token from @BotFather)? [y/N]
    Model endpoint [http://127.0.0.1:8081/v1]
    Model id [main]
    API key for it (blank if it needs none)     only asked when the endpoint is not
                                                on this machine
    Add another endpoint? [y/N]                 repeatable. Each one is an
                                                llm.fallbacks entry, tried in order
                                                when the primary fails; its key goes
                                                to .env (api_key_env), never config.json
    Install now? [Y/n]

Press Enter to take the value in brackets. Skipping the token installs the files with
**no chat account**: a session (`--cli`) and a one-shot (`--once`) work right away, but
nothing remote is served and no launchd agent is registered (a lane-less agent would
exit at once and KeepAlive would loop it). Re-run the installer with a token to
register the agent. With a chat account it builds `~/tinycmdr`, writes `config.json`
and `.env` (mode 600), and loads the launchd agent.

Before it finishes it asks the endpoint you named for its metadata, once, from the venv's
own python - the same binary the agent runs - and prints how many tokens that endpoint
serves per request. On macOS that first connection to a LAN address is what raises the
**Local Network** prompt, so it is deliberately made here, while you are watching, rather
than from a launchd job at boot where nobody can answer it. It is a check, never fatal.

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
    --token-file <file>                   read the token from a file instead of argv
    --use-fleet-model                     use the LAN model endpoint in
                                          fleet-defaults.json instead of the cloud one
    --model-base-url <url> --model <name> point it anywhere else
    --secrets-file <file>                 extra KEY=VALUE lines for .env (search API
                                          keys, and the bot token: TINYCMDR_MM_TOKEN
                                          from it chooses the chat lane)
    --python <path> / --install-python    build the venv from this interpreter / fetch
                                          a private 3.12 with uv
    --force-python                        accept a 3.13+ interpreter (mmpy_bot will not
                                          connect: you are on your own)
    --label <name>                        launchd label (default com.tinycmdr.agent);
                                          recorded in the install folder for --uninstall
    --no-path                             do not put the `tinycmdr` verb on PATH
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
user-mode uninstall cannot unlink the `tinycmdr` wrapper there - it says so and prints
the one line to run by hand instead of stopping half-way through. A user-mode install
created no wrapper, so plain `bash install/uninstall-tinycmdr-macos.sh` is enough.

Under `sudo` the uninstaller resolves **you** (the `SUDO_USER`), not root's `$HOME`:
before 1.0.22 it derived every path from `$HOME`, which `sudo` resets to `/var/root` -
so the `sudo` line above found nothing, printed `done.` and exited 0 while the agent
kept running. It also reads the launchd label out of the install folder, so an install
made with `--label <something>` is removable without passing that label again. When
there is nothing to remove (wrong `--install-dir`, already uninstalled) it says so and
names the paths it checked, instead of reporting success. The Mattermost bot account and
its token are yours to revoke separately.

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

### If the endpoint does not answer

The symptom is on this side, not the server's: `tinycmdr status` prints "the endpoint did
not answer its metadata probe", `tinycmdr doctor` says "the model endpoint at ... did not
answer", a run fails with `no LLM endpoint answered: ... [Errno 61] Connection refused`,
and the log warns "could not detect the endpoint's context length - assuming a 14,349-token
window" while the agent carries on with that small window, compacting hard.

On a Mac, a private address that looks exactly like a dead box is often the **Local Network
permission**. macOS raises that prompt in whatever process dials, and the agent dials from a
launchd job at boot, where nobody can answer it - so it can sit denied while the model box
serves everything else just fine.

    System Settings -> Privacy & Security -> Local Network -> allow the Python this
    install created (~/tinycmdr/venv/bin/python)

That list collects one entry per Python binary you have ever run; the venv's is the one that
matters, and approving it also fixes the window detection, so the agent stops assuming 14,349
tokens. None of this applies to an endpoint on this Mac (`127.0.0.1`): loopback is not the
Local Network.

## 7. What this does NOT do

- Web search is OFF unless you allow it to leave this machine. The installer asks (and
  `--search-egress true` answers without a prompt); the answer lands in `.env` as
  `TINYCMDR_SEARCH_EGRESS`. While it is off, an off-LAN provider is refused with a `BLOCKED`
  line naming the setting rather than being called - a provider on your own LAN never needs it.
- It does not install search API keys. Put `TAVILY_API_KEY=...` / `ANYSEARCH_API_KEY=...` in a
  file passed as `--secrets-file`, or add them to `~/.tinycmdr/.env` later. With no key at all
  anysearch still answers on its anonymous tier - off this machine, rate-limited, which is why
  the flag above exists.
- Provider order and endpoints are `search.providers` in `config.json`; a `searxng` entry keeps
  search on your network. Add or change one later with
  `tinycmdr config set search.providers '<json array>'`.
- It asks for the model endpoint's key but not for search keys: one is required for the
  bot to answer, the other only for the search tool.
- It does not create the bot account or the token (step 2).
- It does not touch anything outside `~/tinycmdr`, `~/Library/LaunchAgents` and the
  logs. `--uninstall` removes exactly those.
