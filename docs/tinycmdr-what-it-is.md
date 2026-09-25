# tinycmdr: what it actually is

Working definition, extracted from this tree on 2026-09-13 (v1.9.32, `tinycmdr.py` sha256
`b91917a6095d9817`). Everything in sections 1 to 3 was read out of the code, config, and tests in this
repo. Section 6 is a comparison against projects other people maintain, and every claim about those
projects is labelled with where it came from.

Short version: tinycmdr is a single-file, single-process agent that lives on each machine and is driven
from self-hosted Mattermost. It is an operator's agent, not a coding agent. Its distinguishing work is
the runtime around the model call: budgets, a stall watchdog, a loop guard, a task ledger, check-ins,
mid-run steering, truthful stop and restart semantics, and prose runbooks it reads on demand.

## 1. Measured surface

```
code                5,847 lines / 286 KB in ONE file, no package, no framework
dependencies        requests, croniter, mmpy_bot (3)
processes           one; no daemon, no gateway, no database
interfaces          Mattermost bot (DMs + @mentions), --cli, --once "task",
                    small web UI (:8788: chat + /api/health + token header)
core tools          22, of which 14 are always-on; the rest answer by name (section 2)
custom tools        three file shapes load from ./tools/ (native .py, register-style .py,
                    <name>.tool.json); 9 on the manager box, and the agent writes its own
                    with create_tool. The prompt lists them by SHELF, descriptions on demand
chat commands       31 (section 3.1)
prose skills        43 runbook folders on the manager box (SKILL.md, read on demand)
tests               4,345 lines across three suites + 2 harness scripts;
                    474 assertions green on a clean unpack of the shipped archive
config              config.json, 5 blocks: llm (18 keys), mattermost, search, web,
                    agent (50 keys, all of section 3 is configurable)
state on disk       sessions/*.json (per channel), notes.md, tasks.json (ledger),
                    jobs.json (cron), uploads/, logs
```

## 2. Tool surface (22 core, 14 always-on)

```
shell           run a command on this machine (bash / PowerShell), per-call timeout
execute_code    run Python in-process for parsing, math, log wrangling
read_file       read text, including UTF-16 and null-padded files
write_file      create/overwrite a file
edit_file       exact-string replacement, automatic .bak
search_files    ripgrep-backed content and filename search
fetch_url       fetch and strip a page to text
web_search      pluggable search backends (AnySearch, Tavily) with fallback
create_tool     the agent writes a new tool; hot-loaded, live on the next call
list_tools      list what exists, core and custom
schedule        cron entries (croniter) for recurring jobs
task            task ledger: add, list, complete, drop
notes           long-term memory file, append with budgets and archiving
remember        shorter-form memory write
search_sessions past conversations, across channels
delegate_task   sub-agent with a fresh context for one subtask
skill           list, read, or search the prose runbooks
send_file       attach a file to the chat the operator is reading
find_tools      ask for a tool by name or by what it does; a category returns a shelf
plan            the run's own plan, re-sent every turn with the position
experiment      what this box has already tested, so an arm is not run twice
ask_user        one blocking question to the operator, and the run stops for the answer
```

Custom tools are the extension path, and there are three shapes to write one in: a native `.py`
(NAME, DESCRIPTION, SCHEMA, `run(args, ctx)`), a register-style `.py` that calls
`registry.register()` at import, or a `<name>.tool.json` manifest that runs any script in any
language with the arguments on stdin. A hand-dropped file loads at the NEXT START (`create_tool`
writes one that is live at the next call). A `.ps1` in `./tools/` is NOT a tool - the loader reads
`*.py` and `*.tool.json`. The manager box's nine are the file and disk reports (`big_files`,
`dir_usage`, `drive_space`), the fuzzy-anchor edit (`patch`), background jobs (`process`), the
self-restart helper, the Docker update check, the blog tool, and `toolsmith`, the tool that
writes tools.

## 3. The runtime around the model call (the distinctive half)

This is where the project actually spent its 5,800 lines and its 474 assertions. All of it is
config-driven, and each item below exists because something went wrong in the field first.

### 3.1 Commands: `/tinycmdr <verb>` in chat, `tinycmdr <verb>` in a shell

One word for both, because the operator got tired of being asked which one to type:
same verbs, same behaviour, whichever door you are at. Chat needs the prefix (a client
only sends `/`-lines that match a registered command); a shell does not. A bare
`tinycmdr` in a shell opens a session, because that is what a person at a keyboard
means by it.

```
new / reset      fresh session for this conversation
stop             stop the run, with truthful states (stopping / already flagged / nothing running)
restart          hand the process over to whoever supervises it, then come back
retry / undo     re-run, or back up N turns and re-prompt
model            per-conversation model or endpoint, /tinycmdr model <name> --global everywhere
pause / resume   hold new tasks until told otherwise
steer            inject a correction into a run that is already going
queue            inspect or clear what is waiting
status           version, endpoint, model, sampling policy, budgets, guardrail state
context          what is filling the context window
compact          compress the conversation, keeping the recent tail
compress
skills           list/read/search the prose runbooks from chat
sessions         list, inspect, search stored sessions
memory / profile operator memory and the bot's own profile
agents           the sub-agent/delegation view
goal / title     naming and intent for the session
commands         the command index itself
background / bg  run something without blocking the conversation
save             export the session
usage / update / version / whoami / sethome / help (h)
```

### 3.2 Budgets and limits (defaults from `config.example.json`)

```
max_steps 40                hard stop on tool calls per run
max_minutes 10              wall clock per run
shell_timeout 180           per command
tool_output_max_chars 6000  what a tool may hand back into context
max_context_tokens          context budget, with `context` reporting the fill
history_exchanges           session depth kept in the prompt
notes_max_chars / per-note / keep / archive_days   memory caps and rotation
tasks_max_open / done_keep  ledger caps
```

### 3.3 Failure handling (the part most harnesses do not have)

```
loop guard          an identical (tool, args) call is refused after 2, the run stops after 6;
                    any real mutation (write/edit/create_tool) clears the memory, because
                    "fix it, then run the same check again" is the legitimate case
stall watchdog      warn at 8 minutes without progress, abandon the run at 20 and say so
task ledger         tasks.json; open/done/abandoned, with a cap and an archive
check-ins           every 5 minutes (or N steps) one status line, tool lines merged, colour-coded:
                    green = model narration, amber = tool ran, red ONLY on failure
infrastructure      endpoint unreachable/rejecting files a red Done line instead of a fake answer
                    failure is not silently converted into prose
failover            primary plus ordered fallbacks; a local failure does not fall through to the
                    internet unless allow_cloud_fallback says so (a privacy gate, not a preference)
streaming           streamed model calls, with an idle bound so a trickling endpoint is bounded
catch-up            after a restart, sweep the last 30 minutes of its own channel
restart             /tinycmdr restart hands the process to the supervisor and announces when it is back
stop                idempotent; reads on the listener thread even while a wedged worker owns the
                    channel; "nothing running" is only said when that is true
```

### 3.4 Context and memory model

```
sessions/          one JSON per channel; a conversation is a channel, so no bleed between them
notes.md           append-only dated memory, read back into the prompt under a character budget,
                   with rotation and an archive for what ages out
search_sessions    recall across channels, no embeddings, no vector store
compact/compress   in-run context compression, keeping the recent tail
delegate_task      sub-agent with a throwaway context (one level only, refuses to nest), inherits
                   the conversation's model
skills             prose runbooks, indexed by folder, read on demand rather than carried in the
                   prompt (the fixed prompt cost is what motivated the whole build)
```

### 3.5 Safety, as configured today

```
allowed_users       deny by default: only listed Mattermost users are answered
blocked_patterns    regex blocklist before execution (shipped example blocks rm -rf / and mkfs)
confirm_patterns    optional chat yes/no gate for commands the operator picks
tool_output caps    what a tool may inject back into the context
redaction           keys/tokens kept out of argv by convention and by the tool wrappers
```

What that is not: a sandbox. There is no container, no seccomp, no capability drop, no per-tool
permission model. The agent runs as the login user on the host, with that user's rights. The blocklist
is a regex, so obfuscation beats it, and untrusted text that reaches the model (a fetched page, a
chat message) can ask it to run something. That is stated plainly in the shipped documentation
because it is the real threat model.

## 4. Design choices, and why

```
no framework        the framework it replaced cost 16K+ tokens before the first tool call; this one
                    measures 5,333 tokens of fixed overhead AS SENT on a clean unpack of the
                    shipped archive (system prompt + the 14 tool schemas a request really carries
                    + the skills index; 3,022 of it is the prompt). Each prose runbook costs
                    about 23 tokens of index, and each custom tool costs its NAME on its shelf's
                    line - 5.9 chars per tool measured at 80 tools - with its schema riding along
                    only while a session has revealed it. A tool fleet no longer competes with
                    the runbooks for the prompt. On a prefill-bound local model that difference
                    is minutes before the first action.
one file            auditable end to end by one person; you can read the whole agent
no database         sessions/jobs/notes/tasks are JSON/text next to the bot
no daemon           the bot IS the process; systemd / launchd / a scheduled task supervises it
one bot per host    one token, one identity, one machine; DMs are unambiguous
chat as the UI      Mattermost is the operator's existing tool; a bot account per machine
prose skills        runbooks the agent reads when relevant, rather than code it must be rebuilt for
self-written tools  the agent extends itself, and the new tool is live on the next call
```

### 4.1 How the overhead figure is produced

The bot computes it at startup and prints it, from the same expression used here:

```
python -c "import sys,json; sys.path.insert(0,'.'); import tinycmdr as fb; \
  print(fb.est_tokens(fb.build_system_prompt() + json.dumps(fb.select_tool_schemas(None))))"
# clean unpack of tinycmdr-1.0.16-linux.tar.gz -> 5333   (the 14 schemas a request SENDS)
# the same tree counting EVERY schema held      -> 7912   (25 schemas: openai_schemas())
```

Measured 2026-09-25 on a clean unpack of the shipped archive; the 2026-09-13 figure was 3,469 on
1.9.32, before the tool set and the guard prose grew, and every later number since has been a
measurement of a different tree. What a request SENDS is the two-line expression above: the
system prompt plus the always-on schemas. The registry holds eleven more schemas that only a
session which asked for them carries, which is why the old expression (every schema the registry
holds) reads 7,912 here and the sent floor is 5,333. The deltas are the interesting part: about
23 tokens per runbook of index, and a custom tool now costs its NAME on its shelf's line (5.9
chars per tool at 80 tools, measured with `tests/tool_index_scale.py`) instead of a schema or a
description line on every call. The figure moves with the number of skills and custom tools
installed, which is why the post should quote the unpack number and say what it was measured on.

## 5. What it does NOT have

Grouped by what a team-managed project would treat as table stakes.

```
Execution safety
  no container or VM sandbox per session (OpenHands runs each session in its own Docker runtime)
  no security analyzer or policy engine on individual actions
  no per-tool or per-path permission prompt; no allow/deny rules per command beyond the regex list
  no read-only mode, no dry-run, no diff-approval step before a write lands

Interop and ecosystem
  no MCP client or server (0 references), so none of that ecosystem's tools
  no ACP/agent-protocol integration, no plugin market
  no LSP or language tooling; no repo map, no symbol index, no call-graph tooling (an Aider
  strength); search is ripgrep plus the model's own reading
  no native Anthropic/Google message formats: OpenAI-compatible endpoints only
  no browser automation built in (the fleet uses a separately installed computer-use driver where
  a host has one)

Agent architecture
  no plan/act phases, no explicit plan-approval gate, no graph or state-machine orchestration
  no sub-agent fan-out: delegate_task is one level deep by design and refuses to nest
  no parallel worker pool beyond one worker per channel
  no RAG or vector memory, no embeddings, no document ingestion pipeline
  no voice input/output; vision is a config flag with limited use

Engineering maturity
  no benchmark or eval harness (no SWE-bench-style numbers, no scoreboard)
  no telemetry, no tracing, no structured run records beyond the log and the optional payload dump
  no packaging or upgrade path beyond the archive: version drift is detected by file hash
  no docs site, no contributor guide, no release process (no signed releases, no changelog automation)
  no multi-user model: one operator, one allowlist, no per-user permissions or quotas
  tests are unit-level (474 assertions); there is no end-to-end suite against a real model
```

## 6. Where this sits in the 2026 harness landscape

The vocabulary settled in 2026. LangChain's definition (March 2026): a harness is every piece of code,
configuration and execution logic that is not the model. Four layers follow from it: the model reasons,
the harness runs one agent, a framework composes several, a platform runs many harnesses for a team.
tinycmdr is a harness, and a deliberately single-layer one: no framework inside it, no platform around
it.

The harnesses people actually compare, as listed on 19 August 2026 (winder.ai):

```
Claude Code        Anthropic, proprietary, TS, Anthropic models only, 142k stars
Codex              OpenAI, Apache-2.0, Rust + TS, OpenAI models only, 107k stars
OpenCode           Anomaly, MIT, TS, 75+ providers including local models, 199k stars
Qwen Code          Alibaba, Apache-2.0, TS, 27.2k stars
DeepSeek Harness   MIT, TS, every component a plugin including the loop, 95k stars in about two
                   days after its 13 Aug 2026 developer preview
Goose, Zed Agent, OpenHands, Pydantic AI Harness    the rest of that nine
Aider              git-native pair programmer, deliberately left out of unattended-run comparisons
                   because it is built for short supervised runs
```

The closest thing to tinycmdr's own shape is OpenClaw: a self-hosted Node.js gateway that connects
Discord, Google Chat, iMessage, Matrix, Microsoft Teams, Signal, Slack, Telegram and WhatsApp to
agents. It carries a workspace of instruction and memory files (AGENTS.md, SOUL.md, IDENTITY.md,
USER.md, MEMORY.md), channel plugins, tool policy and sandboxing options, heartbeats every 30 minutes
by default, cron jobs and wakeups, subagent sessions, a dashboard, and companion apps on macOS, iOS,
Android and Windows.

Same job, different choices:

```
                     tinycmdr                            OpenClaw
runtime              Python, one file, 3 dependencies    Node.js, plugin architecture
channels             Mattermost, the fleet's own server  Discord, WhatsApp, Slack, iMessage,
                                                         Teams, Signal, Matrix, Telegram, Zalo,
                                                         WebChat
identity and memory  notes.md plus 43 prose skills read   AGENTS.md/SOUL.md/MEMORY.md injected into
                     on demand (about 40 tokens per       every session
                     skill in the prompt index)
always-on behaviour  check-ins every 5 minutes during a   heartbeats every 30 minutes, cron jobs,
                     run, cron entries for scheduled      wakeups
                     jobs
safety               allowed_users allowlist, a regex      tool policy, sandbox options, gateway
                     blocklist, an optional confirm gate   auth, per-channel allowFrom
reach                one bot per machine, ops on my boxes  personal assistant, anything with a chat
                                                         client in front of it
```

Two standards converged while this was being built, and tinycmdr speaks neither: MCP for tools
(Anthropic, donated to the Linux Foundation's Agentic AI Foundation in 2026) and AGENTS.md for
instructions (OpenAI's, same foundation), with the Agent Client Protocol letting harnesses drive each
other. tinycmdr's extension path is a hot-loaded `.py` file and its instructions are Hermes-style
SKILL.md folders. That is a real interop gap, and a cheap one to close if it ever matters: an MCP
client is one file, and SKILL.md to AGENTS.md is a translation.

Two pieces of framing from that same literature describe this build better than any feature list:

- Osmani's ratchet: anytime an agent makes a mistake, engineer a solution so that it cannot make that
  mistake again, with every rule traceable to a real failure and no rule added before one. Every guard
  in tinycmdr exists because something broke on a box first, and the changelog is the record: the loop
  guard after a tool was re-issued eight times, /stop after it lied about an abandoned run, the task
  ledger after work evaporated between turns.
- The break-even rule from that comparison: a harness earns its keep once there are around ten
  distinct jobs that can be described in markdown. This one carries 43.

And the limitation the same sources name, which applies here: the thing that tells you the choice was
right is an evaluation suite, and it is the piece most often missing. tinycmdr has 474 unit assertions
and no evaluation suite, so none of the Terminal-Bench or SWE-bench numbers in that field can be held
against it, in either direction. That is the honest comparison, not a favourable one.

## 7. Compared with harnesses other people maintain

Claims about other projects come from their own docs or papers, linked in section 9. "Observed"
means read out of this repo.

```
                              tinycmdr (observed)        OpenHands              Claude Code            Aider
-----------------------------------------------------------------------------------------------
shape                         one 5.8k-line file,         full platform:         closed-source CLI      CLI pair
                              one process, no daemon      agent server + SDK     + IDE + web
execution                     directly on the host,       per-session Docker     local machine with     local machine
                              as the login user           sandbox runtime        permission prompts
coding work                   incidental: it edits        its core purpose:      its core purpose:      its core purpose:
                              files and runs commands     issues, PRs, tests     code across a repo     diff-based edits
                              when an ops task needs it
provider coverage             OpenAI-compatible           multi-LLM routing      Anthropic + others     many, incl.
                              endpoints + fallbacks       (100+ providers)       via gateways           local models
extensions                    .py tools hot-loaded,       MCP, SDK, custom       MCP, subagents,        config + models
                              prose skills                tools, microagents     hooks, plugins
guardrails                    budgets, loop guard,        security analyzer,     permission model,      git diff before
                              stall watchdog, ledger,     action confirmation    hooks, sandbox        apply, git commits
                              check-ins, confirm gate
sessions/memory               per-channel JSON,           conversation store,    session files,         chat history per
                              notes.md, ledger,           context condenser      CLAUDE.md memory       repo
                              search, no vectors
sub-agents                    one level, throwaway        delegation in SDK      subagents (own         no
                              context                                            context, MCP access)
interfaces                    Mattermost bot, --cli,      web UI, CLI,           terminal, IDE,         terminal, IDE
                              --once, small web UI        IDE, API               headless mode
ops features                  stall watchdog, task        runtime lifecycle      hooks for enforcing    git-native undo
                              ledger, check-ins, live     control, security      workflow at commit     and diff review
                              steering, /stop, restart
maturity signal               one operator, 474           large team, papers,    vendor-maintained      large OSS user
                              assertions, 43 skills       funding, ecosystem     product                base, docs site
```

The four coding harnesses above do a different job from tinycmdr and from OpenClaw: they are built to
change a repository, and their features follow from that. tinycmdr's peers in shape are self-hosted
chat-driven assistants, which is section 6.

Framework stacks (LangGraph, CrewAI, OpenAI Agents SDK, AutoGen) sit at a different layer: they are
libraries for building graphs of agents, with no opinion about the machine the agent runs on, no chat
surface, no ops runtime. Comparing tinycmdr to them mostly measures "library versus finished tool".

## 8. Where it is genuinely ahead, honestly stated

```
fixed prompt overhead     ~4,150 tokens measured, against 16K+ on the framework it replaced
readability               5,847 lines, one file, no dependency tree to audit
ops runtime               stall watchdog, task ledger, periodic check-ins, live steering, and a
                          /tinycmdr stop that reports the truth about three different states
self-extension            a new tool is a .py file the agent writes itself, live on the next call
prose skills              the runbooks are plain markdown an operator can read and edit mid-incident
deployment surface        three dependencies, no daemon, no database, works offline on a LAN with a
                          local model server, and the chat server is self-hosted
per-host identity         one bot account per machine, so "which box am I talking to" is never a guess
```

## 9. How to say what it is

In the 2026 vocabulary, this is a harness: the code around the model that runs one agent, with no
framework inside it and no platform around it. Its closest widely used peer in shape is OpenClaw (a
self-hosted chat-driven assistant); its closest peers in job are the coding harnesses, which do
something it was never built to do.

For the post, the definition that survives comparison:

> tinycmdr is a single-file agent harness embedded on each machine in my fleet, driven from my own
> Mattermost server. It is not a coding agent and it does not try to be one. It is an operations agent:
> it runs commands, reads logs, edits configs, schedules checks, and reports in chat, under a runtime
> built around budgets, a stall watchdog, a loop guard, a task ledger, and honest stop and restart
> semantics. Where a team-maintained harness gives you a sandbox, an ecosystem, and a permission model,
> this gives you 5,800 readable lines, three dependencies, and a runtime that assumes things will go
> wrong on a box you cannot see.

And the honest limitation sentence, which is what makes the claim credible:

> What it does not have is what a team buys you: a sandbox, a plugin ecosystem, protocol interop,
> benchmarks, and people. It runs as me, on my boxes, and the blocklist is a regex.

## 10. Sources for the comparison claims

- OpenHands repository and docs: https://github.com/OpenHands/openhands and
  https://docs.openhands.dev/sdk/guides/security (per-session Docker runtime, security analyzer and
  action-confirmation policy)
- OpenHands SDK paper: https://arxiv.org/html/2511.03690v2 (multi-LLM routing, sandboxed execution,
  security analysis as built-in layers)
- Claude Code docs: https://code.claude.com/docs/en/hooks (hooks, subagents, MCP tool access,
  headless mode, permission model)
- Aider docs: https://aider.chat/docs/repomap.html (repository map, git integration, LSP discussion)
- OpenClaw: https://openclaw.ai/ and https://docs.openclaw.ai/start/openclaw (self-hosted gateway,
  channels, workspace files, heartbeats, cron, status and health commands)
- Harness landscape and the nine-harness list with star counts: https://winder.ai/ai-agent-harness-comparison/
  (checked 19 August 2026), including the LangChain definition of a harness, the ten-jobs break-even
  rule, and the evaluation-suite gap
- Anatomy of an agent harness: https://www.langchain.com/blog/the-anatomy-of-an-agent-harness
- Components of a coding agent: https://magazine.sebastianraschka.com/p/components-of-a-coding-agent
- Harness engineering and the ratchet: https://addyosmani.com/blog/agent-harness-engineering/
- Platform layer framing: https://www.mongodb.com/company/blog/technical/agent-harness-why-llm-is-smallest-part-of-your-agent-system
- Agentic AI Foundation (MCP, AGENTS.md, Goose donations): https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation
- tinycmdr's own numbers: this repo, `config.example.json`, `tests/`, and the fixed-overhead figure
  printed by the bot at startup.
