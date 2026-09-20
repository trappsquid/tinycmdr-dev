tinycmdr - a small ops agent that runs in a terminal window
===========================================================

What this is
------------
One Python file that you talk to in a console window. It runs shell commands,
reads and edits files, takes notes, and writes its own small tools. It is not a service, it installs nothing, and it has no chat server
behind it: you open it, ask for something, and close it when you are done.

Closed by design: no web search and no URL fetching, no provider keys, no telemetry,
no update check, no package install. The model endpoint in config.json is the only
thing on the network it will ever talk to. When a question needs information from
outside, it says so rather than guessing.

Requirements
------------
Python 3.10 or newer, and nothing else. There are no packages to install: this
build uses only the Python standard library, so there is nothing to pip or conda
install and nothing to fetch at run time.

An Anaconda or Miniconda base environment counts, as long as it is 3.10 or newer,
and there is nothing to install into it: no pip, no conda, no packages. If the
machine has no Python at all, install it from a source your environment approves
before running this.

Running it
----------
From a prompt, in this folder:

    python tinycmdr.py                       interactive
    python tinycmdr.py --once "check disk space"     one task, then exit
    python tinycmdr.py --version             which build, and which folder
    python tinycmdr.py --help                the flags

There is no launcher script of any kind in this folder: no .bat, no .cmd, no
.ps1, no installer. Environments that whitelist executables block those outright,
so this build is a plain Python file that you start with the Python you already
have.

Name the interpreter when you start it. Do not rely on double-clicking the file,
or on typing its path on its own, on a managed machine: there the .py extension
belongs to whatever program your environment registered for it - an editor, a
script host, a wrapper - and that program is what opens, instead of the agent.

    python tinycmdr.py        the interpreter you name runs the agent
    tinycmdr.py               whatever owns .py on this machine runs instead

The first line this build prints names the build, the model and the endpoint it is
talking to, so there is nothing to guess (`--version` adds the interpreter and the
folder it was started from):

    tinycmdr 1.0.5 (cli build) - <model> at <endpoint>

No line like that means Python did not start it. Check the interpreter first with
`python --version` or `py -3 --version`; if Python is not on PATH, use the
Anaconda Prompt, or give the full path: `"C:\path\to\python.exe" tinycmdr.py`.

This build also opens no windows of its own. It never allocates a second console,
never starts a detached copy of itself, and never hands a file to another program.
The only processes it can start are the shell command you asked it to run and, when
one of those has to be killed, `taskkill`; both are created with no window of their
own (CREATE_NO_WINDOW), so a long run does not flicker windows onto your desktop.

On Windows, the Anaconda Prompt is the simplest way in: it already has python on
PATH. Open it, change to this folder, and run the command above:

    cd /d C:\path\to\tinycmdr-cli
    python tinycmdr.py

First run
---------
Copy the example that sits in this folder to config.json, then fill in three
fields. Nothing is created for you: this build has no wizard, writes no
config.json, and touches nothing in the folder until you ask it to do something.

    Windows         copy config.example.json config.json
    Linux / macOS   cp config.example.json config.json

(or just rename it, which is the same thing)

Then open config.json and set:

    llm.base_url   the endpoint you are approved to reach, OpenAI-compatible
    llm.model      the model id that endpoint serves
    llm.api_key    the key that service issued you (leave empty for a local one)

Start it with `python tinycmdr.py`. With no config.json it prints those same steps
and exits 2, having written nothing.

The whole configuration is that one endpoint entry:

    {
      "llm": {
        "base_url": "https://the-model-service/v1",   the endpoint this agent may reach
        "api_key":  "the key that service issued",    required by a remote service
        "model":    "the model id that service serves"
      }
    }

The key is sent as an Authorization: Bearer header. A remote endpoint without one is
refused at startup, with that message, rather than letting the first request come back
401. A local or loopback endpoint needs no key. Certificates are not configured here:
the agent uses the machine's own trust store, so if your service presents a certificate
issued by your organisation, that certificate belongs in the system store.

The endpoint is whatever your environment approves, and it has to speak the OpenAI
chat-completions shape, because that is the only protocol this agent speaks:

    POST  {base_url}/chat/completions
    (Authorization: Bearer <key> only if you configured one)
    {"model": ..., "messages": [...], "tools": [...], "max_tokens": ..., "stream": true}

    answered with either a JSON completion or an SSE stream, and function/tool calls
    in the usual OpenAI form. GET {base_url}/models is used once, by /model, to list
    what the service offers; nothing else is ever requested

If your gateway is fronted differently (a different auth header, a different path),
say so: the request is built in one place and adapting it is a small change, not a
rewrite.

Everything else has a default inside tinycmdr.py and is not needed to start: turn
limits, timeouts, the budgets that cap a run, the notes and ledger sizes, the
safety patterns. config.example.json is that same single entry, ready to fill in.

Commands
--------
    /help            the list
    /new             forget the conversation so far
    /model [NAME]    show the model, or switch to another one
    /status          version, endpoint, context use, notes, tasks, skills
    /tasks           the task ledger for this machine
    /notes           what it has written down about this machine
    /skills          the runbooks it can load
    /tools           every tool it has right now
    /usage           tokens and time for the last run
    /stop            cancel the run that is in flight
    /exit            quit

Anything else you type is a request, in plain language:

    check why the backup job failed last night and fix it
    what is using port 8080
    read the last 200 lines of the service log and tell me what matters

Typing, while it is working
---------------------------
You do not have to wait for a run to finish. Type at any time:

  a request      sent in at the next step, and it overrides what the agent was
                 doing. Nothing typed is lost, even at the last moment: text that
                 arrives too late becomes the next thing it answers.
  /stop          cancels the run in flight. The read-only commands (/help,
                 /usage) also answer straight away.
  /new, /model   queued until the current run ends, because a run reads the
                 conversation and the model name when it starts. It says so.
  Ctrl-C         stops the run in flight; a second Ctrl-C quits.

It also narrates what it is about to do, one line at a time, before each tool
call it makes, so you can see the direction it is taking and stop or redirect it
rather than reading the plan afterwards.

What it keeps, and where
------------------------
Everything lives in this folder, next to the script: sessions/ (the
conversation, so it remembers), notes.md (facts it decided to keep),
tasks.json (the ledger), tinycmdr.log (what it did). Nothing is written to your
home directory or to the registry. To move the agent, move the folder.

None of those files exists until there is something to keep. Opening the program
creates nothing: no log file, no sessions/, no tools/, no notes, no config.json.
tinycmdr.log appears with its first line, sessions/ with its first session, and
tools/ the first time a tool is written. `--version` and `--help` leave the
folder exactly as they found it.

Recurring work
--------------
This build has no internal scheduler, on purpose: nothing runs while the window
is closed. For anything that has to happen on a timer, let your operating system
run a one-shot:

    Linux    */15 * * * *  /path/to/tinycmdr.py --once "check disk space"
    Windows  Task Scheduler, program: python.exe, arguments: tinycmdr.py --once "check disk space"

Runbooks (optional)
-------------------
Put folders containing a SKILL.md into skills/ and the agent will list them and
read the relevant one before working in that area. The folder ships empty; your
own runbooks are the useful ones.

A runbook is prose and travels as-is; the tools it names do not. One written for
another harness may call tools this build does not have, and every skill read ends
with the list of tools this install does have, so the gap is visible on the same
page as the steps that assume it. A capability you need belongs in tools/ (the
contract is in that folder), not improvised from a runbook.

The machine atlas (atlas.md)
----------------------------
atlas.md sits beside the script: a map of the machine it is running on, written
for this platform and shipped with it. Nothing regenerates it - it is a document,
not state - so edit it to match the machine in front of you and your edit stays.
The agent reads it on the first turn of a run, and again after a failure that
looks like a wrong path, which is the moment a guessed path costs the most.

    ## host     one "- key: value" per line (OS family, which shell it runs, ...)
    ## layout   one "- path  what it is" per line
    ## notes    the facts worth keeping: the native commands for this OS, and what
               the environment does and does not allow

The Windows archive carries the Windows atlas, the Linux archive the Linux one.
It is bounded by agent.atlas_max_chars (2400); set agent.atlas_enabled: false to
keep it out of the prompt entirely, or point agent.atlas_file at a file of your
own. Delete it and the agent simply has no map: no file comes back.

Safety
------
A small pattern filter refuses the obvious destructive commands (formatting a
disk, wiping a volume, shutdown, rm -rf /), but it is a seatbelt, not a
sandbox: the code-execution tool is not filtered at all. The real limits are the
account this runs under and the machine it runs on. Run it as a normal user, on
a machine you would be happy to hand to a careful colleague.

Running it in a locked-down environment
---------------------------------------
The build is aimed at a network where one endpoint is approved and nothing else is
reachable. What that means concretely:

    one destination   every HTTP call resolves through llm.base_url. There is no other host
                      literal anywhere in the file, which is checkable:  grep -n 'http' tinycmdr.py
    no install step   standard library only, so nothing is fetched or pip-installed at run time
    inert startup     opening it creates nothing and checks nothing: no config.json is
                      written for you, no log, no sessions/, no tools/. The files that
                      record what it did appear only once it has done something. atlas.md
                      is the one file in the folder from the start, and it is a document
                      shipped with the build, not something the agent produced
    no update check   it never asks a channel what version is current, and it never starts
                      an update or an install
    no credentials    it sends no auth header and reads no certificate of its own, so
                      nothing here needs provisioning: the platform it runs behind does that
    plain http       a note is printed (nothing is logged until there is a log line) if the
                      endpoint is http and not loopback: the conversation and the tool
                      output it carries travel unencrypted
    local records     sessions/, notes.md, tasks.json and tinycmdr.log are ordinary files in
                      this folder. Audit them, back them up, or delete them; nothing is sent
                      anywhere except the endpoint
    honest limits     the pattern filter is a seatbelt, not a sandbox, and the code-execution
                      tool is not filtered at all. The controls that matter are the account the
                      process runs under and what that account can reach
    what leaves       the conversation, the tool calls it decides on, and the tool output it
                      sends back for the model to read. Everything else stays on the machine

If something looks wrong
------------------------
No output at all, and no `tinycmdr ...` line

    Python did not start this file. On a managed machine the .py extension
    belongs to another program, so that program opened instead of the agent.
    Start it with an interpreter named on the command line - `python tinycmdr.py`
    or `py -3 tinycmdr.py` - which is what takes the file association out of the
    path. Check the interpreter first with `python --version`; if it is not on
    PATH, use the Anaconda Prompt, or the full path to your approved python.exe.

A window appeared on its own, or a storm of cmd / PowerShell windows

    The agent opens no window: it allocates no console, and the only processes it
    can start are the shell command you asked for and a taskkill, both with no
    window of their own. So a window that keeps appearing belongs to the machine,
    not to the agent - almost always the program that owns .py, or a wrapper
    around it, starting again and again. Close the window you launched from
    (that is what stops the loop), then start the agent as above, with the
    interpreter named. Two commands show which program owns the extension, and
    one shows what code was actually running:

        assoc .py                 the association for .py
        ftype Python.File         the command behind it, if that is Python
        Get-WinEvent -LogName Microsoft-Windows-PowerShell/Operational `
          -MaxEvents 40           the scripts that ran, if script-block logging
                                  is on (event id 4104)

A window flashes and disappears

    Something stopped it at startup: no config.json beside the file, or a
    config.json it refuses (a base_url that lost its "://" while being edited is
    the usual one, and the message quotes what it actually read). Double-clicked,
    that window used to close the instant it printed. From 1.0.7 a window this
    build owns stays open with the reason on screen until you press Enter. On an
    older archive, open a prompt, change to this folder and run
    `python tinycmdr.py`, and the message stays put.

