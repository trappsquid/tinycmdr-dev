"""The toolsmith: making or importing a drop-in tool must be provable, not hopeful.

The public shapes ship three starter tools and the shapes doc; this is the tool that lets a
fresh install (and a model that has never seen a tool file) create one or import an existing
script. Its job is not to WRITE the file - that is a text write - but to prove the harness's
OWN loader accepts it: `check` runs load_tool_defs, the single reader create_tool and the
write verifier both use, so "OK" is the loader's verdict and not this tool's opinion.

    python tests/test_toolsmith.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_toolsmith_under_test", SRC)
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_toolsmith_under_test"] = fb
spec.loader.exec_module(fb)

PASSES = []
FAILS = []


def check(name, cond, detail=""):
    (PASSES if cond else FAILS).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


tool = fb.REGISTRY.get("toolsmith")
check("the toolsmith loads as a drop-in tool in this build", bool(tool))
if not tool:
    print("\n1 passed, 1 failed")
    sys.exit(1)
call = tool["fn"]

work = Path(tempfile.mkdtemp(prefix="fbtoolsmith-"))
ctx = {"config": fb.CONFIG, "session_key": "s-toolsmith"}


def run(**kw):
    kw.setdefault("dir", str(work))
    return str(call(kw, ctx))


# ---- it scaffolds a native tool the LOADER accepts ---------------------------------
out = run(action="new", name="disk_report", description="Disk use by top folder",
          args="path:str=., top:int=5, human:bool=True")
check("new writes the file", (work / "disk_report.py").exists(), out[:200])
check("new says so and names the file", "Wrote" in out and "disk_report.py" in out, out[:200])
check("new proves it through the harness's own loader", "OK: the loader registers" in out,
      out[:300])
text = (work / "disk_report.py").read_text(encoding="utf-8")
check("the scaffold carries the four names the loader looks for",
      all(k in text for k in ("NAME =", "DESCRIPTION =", "SCHEMA =", "def run(args, ctx)")))
check("the arg spec became a schema dict",
      all(k in text for k in ("path", "top", "human")), text[:300])
check("every argument with a default is optional (no required list)",
      "required" not in text.split("MUTATES")[0], text[:300])
out = run(action="new", name="needs_arg", description="x", args="label:str, count:int=2",
          dir=str(work))
check("an argument with no default becomes required",
      "required" in (work / "needs_arg.py").read_text(encoding="utf-8"), out[:200])
check("the scaffold is valid PYTHON, not JSON dressed as python",
      ": true" not in text and ": false" not in text and ": null" not in text, text[:300])
mod = {}
exec(compile(text, "disk_report.py", "exec"), mod)   # a scaffold that cannot run is a lie
check("the scaffold actually executes", callable(mod.get("run")))
check("its schema default survived as a bool",
      mod["SCHEMA"]["properties"]["human"].get("default") is True, mod["SCHEMA"])

# ---- check is the loader's verdict, not this tool's --------------------------------
out = run(action="check", name="disk_report")
check("check loads a good file", "OK: the loader registers disk_report" in out, out[:200])
(work / "broken.py").write_text(
    "NAME = 'broken_other'\nDESCRIPTION = 'x'\nSCHEMA = {}\ndef run(a, c):\n    return ''\n",
    encoding="utf-8")
out = run(action="check", name="broken")
check("check REFUSES a file whose NAME is not the file name",
      "REFUSED" in out and "file name" in out, out[:250])
(work / "syntaxbad.py").write_text("def run(:\n", encoding="utf-8")
out = run(action="check", name="syntaxbad")
check("check reports a file that does not parse", "REFUSED" in out or "parse" in out, out[:200])

# ---- importing an existing script (the portable shape) -----------------------------
script = work / "greet.sh"
script.write_text("echo hi\n", encoding="utf-8")
out = run(action="wrap", name="greet", description="Say hello", script=str(script),
          command=["bash", "greet.sh"], args="who:str")
check("wrap writes a manifest", (work / "greet.tool.json").exists(), out[:200])
check("and proves it through the loader", "OK: the loader registers greet" in out, out[:250])
manifest = json.loads((work / "greet.tool.json").read_text(encoding="utf-8"))
check("the manifest names the tool, its schema and its command",
      manifest["name"] == "greet" and manifest["command"] == ["bash", "greet.sh"]
      and manifest["schema"]["required"] == ["who"], manifest)
check("wrap refuses a script that is not there",
      "no script at" in run(action="wrap", name="ghost", script=str(work / "nope.sh"),
                            command=["bash", "nope.sh"]))
check("wrap refuses without a command",
      "needs command=" in run(action="wrap", name="greet2", script=str(script)))

# ---- the index ---------------------------------------------------------------------
out = run(action="list")
check("list names every tool file with its shape",
      "disk_report.py [native]" in out and "greet.tool.json [manifest]" in out, out[:400])
check("list reports the NAMES the model calls, not only the files",
      "):" in out and "disk_report" in out, out[:200])

# ---- refusals at the door ----------------------------------------------------------
check("a CORE tool name is refused",
      "CORE tool name" in run(action="new", name="shell", description="x"))
check("an existing file is refused without overwrite",
      "exists" in run(action="new", name="disk_report", description="again"))
check("overwrite=true replaces it",
      "Wrote" in run(action="new", name="disk_report", description="again", overwrite=True))
check("a name that is not a name is refused",
      "not a tool name" in run(action="new", name="Bad-Name", description="x"))
check("an unknown action explains the four that exist",
      "action must be one of" in run(action="frobnicate"))

# ---- and nothing here writes into the install's own tools/ -------------------------
check("the default folder is this install's tools/", "tools" in str(fb.TOOLS_DIR))
check("a run pointed elsewhere left the install's tools/ alone",
      not (fb.TOOLS_DIR / "disk_report.py").exists())

shutil.rmtree(work, ignore_errors=True)
print()
print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
sys.exit(1 if FAILS else 0)
