"""toolsmith - make, import or index a drop-in tool for this harness.

The harness loads three shapes from ./tools/ (tools/README.md is the long form):

    native     <name>.py with NAME / DESCRIPTION / SCHEMA / run / MUTATES.
               NAME IS THE FILE NAME. This is the shape create_tool writes.
    register   any .py that calls registry.register(...) at import; several
               tools per file; the shape agent tool libraries use.
    manifest   <name>.tool.json wrapping ANY script in ANY language: the
               call's args arrive as one JSON object on stdin, stdout is the
               result. The portable shape.

    toolsmith(action="new", name="disk_report", description="Disk use by mount",
              args="path:str=., top:int=5, human:bool=True")
    toolsmith(action="wrap", name="greet", description="Say hello via greet.sh",
              script="C:/scripts/greet.sh", command=["bash", "greet.sh"],
              args="who:str")
    toolsmith(action="list")
    toolsmith(action="check", name="disk_report")

`new` and `wrap` write the file and then run the harness's OWN loader on it (the same
load_tool_defs create_tool and the write verifier use), so "OK" means the loader accepted
it - not that this tool thinks it looks right. `list` indexes what ./tools/ has: shape,
the names it registers, and whether each one loads.
"""

import ast
import json
import os
import pprint
import re
import subprocess
import sys
import tempfile
from pathlib import Path

NAME = "toolsmith"
DESCRIPTION = ("Make, import or index a drop-in tool: scaffold a native tool from a short arg "
               "spec, wrap any existing script as a manifest tool, list what tools/ has and "
               "which names it registers, or check one file through the harness's own loader. "
               "When you have just done the same job by hand twice, or walked a runbook step "
               "by step, offer to mint it here instead of doing it again.")
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["new", "wrap", "list", "check"],
                   "description": "new = scaffold a native tool; wrap = import a script as a "
                                  "manifest tool; list = index tools/; check = load one file"},
        "name": {"type": "string", "description": "Tool name: lowercase letters, digits and "
                                                  "underscores. It IS the file name."},
        "description": {"type": "string",
                        "description": "One line the model reads when it chooses tools."},
        "args": {"type": "string",
                 "description": "Argument spec for new/wrap: 'path:str=., top:int=5, raw:bool'. "
                                "A default after '=' makes the argument optional."},
        "script": {"type": "string", "description": "wrap: path to the existing script."},
        "command": {"description": "wrap: argv list (or one shell string) that runs the script."},
        "mutates": {"type": "boolean", "description": "true if the tool changes local state."},
        "dir": {"type": "string",
                "description": "Folder to write to (default: this install's tools/)."},
        "overwrite": {"type": "boolean", "description": "replace an existing tool file."},
    },
    "required": ["action"],
}
MUTATES = True

_TYPES = {"str": "string", "string": "string", "int": "integer", "bool": "boolean",
          "float": "number", "number": "number", "list": "array", "json": "object",
          "array": "array", "object": "object"}
_NAME_RX = re.compile(r"^[a-z][a-z0-9_]*$")

_NATIVE_TEMPLATE = '''"""{description}

Written by toolsmith. The four names below are the whole contract: NAME is the
FILE name, SCHEMA is a JSON object, run(args, ctx) returns text (an exception
becomes a tool error), and MUTATES says whether this changes local state.
"""

import json


NAME = "{name}"
DESCRIPTION = {description}
SCHEMA = {schema}
MUTATES = {mutates}


def run(args, ctx):
    # ctx["shell"](command) runs a shell command the way the shell tool does, and
    # ctx["config"] is the bot config. Return clear text; raise nothing if you can.
{body}
'''

_MANIFEST_TEMPLATE = {
    "name": "",
    "description": "",
    "schema": {},
    "command": [],
    "timeout": 120,
    "mutates": False,
}

_BODY_STUB = '''    raise SystemExit(
        "TODO: {name} is a scaffold. Implement run() in "
        "%s" % __file__)'''

_CHECK_SNIPPET = '''import importlib.util, json, sys
from pathlib import Path

app = Path(r"{app}")
spec = importlib.util.spec_from_file_location("toolsmith_probe", app)
m = importlib.util.module_from_spec(spec)
sys.modules["toolsmith_probe"] = m
spec.loader.exec_module(m)
try:
    defs = m.load_tool_defs(Path(r"{target}"))
    print("OK " + json.dumps([str(d[0]) for d in defs]))
except Exception as exc:
    print("REFUSED " + str(exc))
'''


def _tools_dir(args):
    if args.get("dir"):
        return Path(str(args["dir"])).expanduser()
    return Path(__file__).resolve().parent


def _app_path():
    """The build beside this file: the loader this tool defers to lives in it."""
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "tinycmdr.py",):
        if cand.exists():
            return cand
    return None


def _parse_args(spec):
    """'path:str=., top:int=5' -> (properties, required)."""
    props, required = {}, []
    for chunk in str(spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            name, kind, default = chunk, "str", None
        else:
            name, kind = chunk.split(":", 1)
            kind, _, default = kind.partition("=")
            default = default.strip() or None
        name = name.strip()
        kind = kind.strip().lower() or "str"
        if not name:
            continue
        prop = {"type": _TYPES.get(kind, "string")}
        if kind in ("str", "string"):
            prop["description"] = "the %s" % name.replace("_", " ")
        if default is not None:
            if prop["type"] == "integer":
                try:
                    prop["default"] = int(default)
                except ValueError:
                    prop["default"] = 0
            elif prop["type"] == "number":
                try:
                    prop["default"] = float(default)
                except ValueError:
                    prop["default"] = 0.0
            elif prop["type"] == "boolean":
                prop["default"] = default.lower() in ("1", "true", "yes", "on")
            else:
                prop["default"] = default
        else:
            required.append(name)
        props[name] = prop
    return props, required


def _schema_for(args):
    props, required = _parse_args(args.get("args"))
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def _load_check(target, ctx):
    """Run the HARNESS's loader on a file (the one reader, not a second opinion)."""
    app = _app_path()
    if app is None:
        return "check unavailable: no build beside tools/ (call it in the install folder)"
    snippet = _CHECK_SNIPPET.format(app=str(app), target=str(target))
    fd, tmp = tempfile.mkstemp(suffix=".py", prefix="toolsmith-check-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(snippet)
        # An argv LIST, never a shell string: a quoted interpreter path with spaces is
        # mangled by the platform's parser (measured on Windows: PowerShell answered
        # "Unexpected token 'C:\Users\...'" and the check reported no verdict at all).
        proc = subprocess.run([sys.executable, tmp], capture_output=True, text=True,
                              timeout=120)
        out = (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return "check failed to run: %s" % exc
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    text = str(out)
    for line in text.splitlines():
        if line.startswith("OK "):
            names = json.loads(line[3:].strip() or "[]")
            return "OK: the loader registers %s from that file" % ", ".join(names)
        if line.startswith("REFUSED "):
            return "REFUSED by the loader: %s" % line[8:].strip()
    return "check produced no verdict: %s" % text.strip()[:300]


def _existing_names(path):
    """What a file registers, by shape - cheap text/AST read, no import."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.name.endswith(".tool.json"):
        try:
            return "manifest", [str(json.loads(text).get("name") or "")], ""
        except Exception as exc:
            return "manifest", [], "not valid JSON: %s" % exc
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return "python", [], "does not parse: %s" % exc
    top = {t.id for n in tree.body if isinstance(n, ast.Assign)
           for t in n.targets if isinstance(t, ast.Name)}
    has_run = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run"
                  for n in tree.body)
    if {"NAME", "DESCRIPTION", "SCHEMA"} <= top and has_run:
        return "native", [path.stem], ""
    names = re.findall(r'name\s*=\s*["\']([A-Za-z0-9_]+)["\']', text)
    if names:
        return "register", sorted(set(names)), ""
    return "none", [], ("none of the three shapes: needs NAME/DESCRIPTION/SCHEMA + run(), "
                        "registry.register() calls, or to be a .tool.json")


def _write(path, text, args):
    if path.exists() and not args.get("overwrite"):
        return "ERROR: %s exists. Pass overwrite=true to replace it, or pick another name." % path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(text)
    return ""


def _check_name(name):
    if not name:
        return "ERROR: a tool needs a name (it is the file name too)."
    if not _NAME_RX.match(name):
        return ("ERROR: %r is not a tool name. Lowercase letters, digits and underscores, "
                "starting with a letter." % name)
    if name == NAME:
        return "ERROR: that name belongs to this tool."
    build = _app_path()
    if build is not None:
        try:
            src = build.read_text(encoding="utf-8", errors="replace")
            if re.search(r'^\s*"%s":\s*\{' % re.escape(name), src, re.M):
                return ("ERROR: %r is a CORE tool name in this build - a custom tool under it "
                        "would shadow or confuse it. Pick another." % name)
        except OSError:
            pass
    return ""


def run(args, ctx):
    action = str(args.get("action") or "").strip().lower()
    name = str(args.get("name") or "").strip()
    tdir = _tools_dir(args)

    if action == "list":
        if not tdir.is_dir():
            return "No tools/ folder at %s yet - the first tool written creates it." % tdir
        files = sorted([p for p in tdir.iterdir()
                        if p.name.endswith(".py") or p.name.endswith(".tool.json")]
                       + sorted(p for p in tdir.glob("lib/*.py")))
        if not files:
            return "tools/ at %s holds no tool files yet." % tdir
        lines = []
        for p in files:
            shape, names, err = _existing_names(p)
            where = "lib/" if p.parent != tdir else ""
            if p.parent != tdir:
                # lib/ is NOT read by the loader (only tools/*.py and tools/*.tool.json
                # are), so a helper listed like a tool invites a call to a name that does
                # not exist. Measured 2026-09-25 driving the fleet box: `toolsmith list` answered 10
                # names where the registry really holds 6, and the four extras were lib
                # helpers (blog_lint, humanize_native, owui_humanizer, tech_guide_humanize).
                lines.append("- %s%s [helper library - imported by a tool, NOT callable "
                             "by name]" % (where, p.name))
                continue
            lines.append("- %s [%s] %s" % (p.name, shape,
                                           ", ".join(n for n in names if n) or (err or "-")))
        return ("Tools in %s (%d file(s); the model calls the NAMES, not the files):\n%s"
                % (tdir, len(files), "\n".join(lines)))

    if action in ("new", "wrap"):
        bad = _check_name(name)
        if bad:
            return bad
        path = tdir / (name + ".tool.json" if action == "wrap" else name + ".py")
        if action == "new":
            # pprint, not json: this is a PYTHON file, and a JSON `true` in a dict literal
            # is a NameError at import (the tool's own `check` caught exactly that on the
            # first scaffold it wrote).
            text = _NATIVE_TEMPLATE.format(
                name=name,
                description=repr(str(args.get("description")
                                     or "TODO: one line, what this does")),
                schema=pprint.pformat(_schema_for(args), indent=4, sort_dicts=False),
                mutates=repr(bool(args.get("mutates"))),
                body=_BODY_STUB.format(name=name))
        else:
            script = str(args.get("script") or "").strip()
            if not script:
                return "ERROR: wrap needs script= (the file this tool runs)."
            if not Path(script).expanduser().exists():
                return "ERROR: no script at %s" % script
            command = args.get("command")
            if not command:
                return ("ERROR: wrap needs command= - the argv list that runs the script, "
                        "e.g. [\"bash\", \"greet.sh\"]. It runs with tools/ as the working "
                        "directory.")
            manifest = dict(_MANIFEST_TEMPLATE)
            manifest.update({"name": name,
                             "description": str(args.get("description")
                                                or "TODO: one line, what this does"),
                             "schema": _schema_for(args),
                             "command": command,
                             "mutates": bool(args.get("mutates"))})
            text = json.dumps(manifest, indent=2) + "\n"
        err = _write(path, text, args)
        if err:
            return err
        verdict = _load_check(path, ctx)
        return ("Wrote %s (%d chars).\n%s\nNext: it is callable by name %r once this process "
                "loads it - a file already on disk is live at the next start, and create_tool "
                "is the immediate route for a tool you rewrite often." % (path, len(text),
                                                                          verdict, name))

    if action == "check":
        if not name:
            return "ERROR: check needs name= (the tool file to load)."
        cands = [tdir / (name + ".py"), tdir / (name + ".tool.json")]
        target = next((p for p in cands if p.exists()), None)
        if target is None:
            return ("ERROR: no %s.py or %s.tool.json in %s" % (name, name, tdir))
        shape, names, err = _existing_names(target)
        return ("%s [%s] %s\n%s" % (target, shape, ", ".join(n for n in names if n) or "-",
                                    _load_check(target, ctx)))

    return ("ERROR: action must be one of new, wrap, list, check (got %r). This tool makes "
            "tools: new scaffolds a native one, wrap imports an existing script, list indexes "
            "tools/, check loads one file." % action)
