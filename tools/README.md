# Drop-in tools

Anything in this folder is a tool for the agent: a file dropped in here is
read when the bot next starts, and a tool written with `create_tool` is live
at once. No config edit is needed either way. Two starter tools ship with
tinycmdr and are also the examples for each shape:

    patch.py        one targeted edit per call with fuzzy anchors (the tool to
                    reach for when edit_file's exact match fails)
    process.py      background a long job, then status/wait/output/kill it

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

`run` returns text. `ctx["shell"](cmd)` runs a shell command the way the shell
tool does (guards and all) and `ctx["config"]` is the bot config. Handle your
errors and return a message: an exception becomes a tool error, which works but
tells the model less.

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

The handler receives the args dict and returns text. A file from another agent
harness that speaks this shape drops in as it is.

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
      "mutates": false
    }

`command` is an argv list (or one shell string). It runs with this folder as
its working directory. The call's arguments arrive as ONE JSON object on stdin,
and the script's stdout is the tool result (stderr is shown when the exit code
is nonzero). `timeout` is seconds (default 120). `mutates` marks a state change.

## How a tool is found and called

The model sees one line per tool in its prompt and calls it by NAME (not by
file name). Tools outside its always-on list are still named there (disclosure
holds back their argument schemas, not their existence), and `list_tools` /
`find_tools` name everything on the box with what each one does. `create_tool` writes native files for the model and
verifies through the same loader these shapes use, so what it reports as loaded
really loaded.
