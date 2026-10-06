# Drop-in tools

Anything in this folder is a tool for the agent: a file dropped in here is
read when the bot next starts, and a tool written with `create_tool` is live
at once. No config edit is needed either way. Four starter tools ship with
tinycmdr and are also the examples for each shape:

    patch.py        one targeted edit per call with fuzzy anchors (the tool to
                    reach for when edit_file's exact match fails)
    process.py      background a long job, then status/wait/output/kill it
    toolsmith.py    make, import or index a drop-in tool: scaffold a native tool,
                    wrap an existing script as a manifest, list what tools/ has
                    and check one file through the harness's own loader
    computer_use.py see and drive this machine's screen: capture the screen or a
                    window (screenshots reach the model when agent.vision is on),
                    then click/type/scroll by element number or point - macOS,
                    Windows and Linux, no third-party driver. Run
                    `python tools/computer_use.py` for its own checks, or
                    action=doctor for the permission/grant state.

Delete them, edit them, or add your own. Three file shapes are recognised.

## 1. Native: <name>.py

The shape create_tool writes. NAME is the FILE name (one name per thing),
and the file defines four things:

    NAME = "my_tool"
    DESCRIPTION = "one line the model reads when it chooses tools"
    SCHEMA = {"type": "object",
              "properties": {...},           # JSON-schema of the arguments
              "required": [...]}

    def run(args, ctx):
        ...
        return "clear text result"

    MUTATES = True        # only if the tool changes local state
    CATEGORY = "files & edit"   # optional: the shelf it is listed under in the prompt

`run` returns text. Handle your errors and return a message: an exception becomes a tool
error, which works but tells the model less.

`ctx` is the tool's environment; the keys are (this list is the one the `create_tool`
description points at):

    config       the bot config (the same dict the harness reads)
    depth        how deep this run is (0 = the top-level run; higher for delegated ones)
    model        the model this session runs on
    session_key  this session's key (hidden-tool state is keyed on it)
    source       what this run reports through
    channel_id   the lane's channel id
    shell        ctx["shell"](cmd) runs a command the way the shell tool does (guards and all)
    shell_argv   the box's real shell as an argv prefix (PowerShell on Windows, bash elsewhere)
    shell_guard  run one command through the same guard the shell tool applies
    cancel_event set by /stop: check it in any loop and return early
    report       {"say","progress","note","narration","drop","tool_done"}: the run's reporting callbacks
    ask_door     how this run asks the operator a question (None when the lane has no door)
    confirm_cb   how a tool raises its own confirm-tier question (None when the lane has none)
    send_file    hand the operator a file (None when the lane has no door)
    render_ui    draw a card (A2UI) when the lane has a renderer
    tool_images  True when this build attaches {"text":..., "images":[...]} returns to the model

Every native tool this tree ships - and every file `toolsmith action=new` writes - ends
with a standalone block, so the file is also a runnable script:

    python my_tool.py who=world count=3

parses each `key=value` argument (JSON values are decoded), calls `run(args, {})` and
prints the result. That is the smoke-test path; a tool that needs the run's `ctx`
(shell, send_file, report) is called through the harness.

## 2. Register-style: anything.py

The shape agent tool libraries use. The file calls `registry.register()` at
import time and may register SEVERAL tools in one file:

    from tools.registry import registry, tool_error

    def hello(args, **kw):
        return "hello " + str(args.get("who", "world"))

    registry.register(
        name="hello",
        schema={"name": "hello", "description": "Say hello",
                "parameters": {"type": "object",
                               "properties": {"who": {"type": "string"}}}},
        handler=lambda args, **kw: hello(args),
        check_fn=lambda: True,          # optional: skip when it cannot run here
        requires_env=["SOME_KEY"],      # optional: skip without the env var
        mutates=True)                   # optional: the tool changes local state

The handler receives the args dict and returns text.

**A file from another agent harness loads only if it imports NOTHING but
`tools.registry`.** The shim here provides exactly three names - `registry`,
`tool_error`, `tool_result` - and nothing else from the other tree. That is a
stricter condition than "a tool from another harness" usually meets, and the
difference is worth knowing before promising anyone a drop-in: measured
2026-09-28, `computer_use` from a predecessor harness pulls in **11 more modules
of its own tree** across 26 import statements (its own constants, environments,
approval and config modules), so it does not load here. It is not a one-file
tool - it is 14 modules and 4,199 lines with a third-party binary behind it.
The loader refuses such a file and names the reason; when it does, wrap the
script it drives as a manifest (shape 3) or rewrite it with `create_tool`
(shape 1). Single-file libraries that only register tools do load as they are;
whole tools from a harness usually do not.

## 3. Manifest: <name>.tool.json

The portable shape: ANY script in ANY language, wrapped in a JSON file that
says what it is called and how to run it. The name is the file name here
too: greet.tool.json carries "name": "greet".

    {
      "name": "greet",
      "description": "Greet someone via the greet.sh script",
      "schema": {"type": "object",
                 "properties": {"who": {"type": "string"}}},
      "command": ["bash", "greet.sh"],
      "timeout": 60,
      "mutates": false,
      "category": "messaging & chat"
    }

`command` is an argv list (or one shell string). It runs with this folder as
its working directory. The call's arguments arrive as ONE JSON object on stdin,
and the script's stdout is the tool result (stderr is shown when the exit code
is nonzero). `timeout` is seconds (default 120). `mutates` marks a state change.

## How a tool is found and called

The model sees your tool's NAME in its prompt, on the line of the shelf it files
under (its `CATEGORY`/`category` if you set one, otherwise derived from the name and
the description), and calls it by NAME - not by file name. What each tool DOES is one
`find_tools {category: ...}` call away: the prompt carries the names and the cats, the
prose is on demand, because a description line per tool grows with the tool count. Tools
outside the always-on list are named there too (disclosure holds back their argument
schemas, not their existence), and `list_tools` / `find_tools` name everything on the
box with what each one does. `create_tool` writes native files for the model and
verifies through the same loader these shapes use, so what it reports as loaded
really loaded.

A shelf you invent is a new line in the index: keep it a few words the operator would
say out loud ("files & edit", "checks & probes"), because a category nobody would guess
is a tool nobody finds.
