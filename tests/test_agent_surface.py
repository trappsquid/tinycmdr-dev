"""test_agent_surface - one merged suite (test_toolsmith, test_subagent_result).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: no member needed a namespace rewrite.
"""
import os
import sys


def _run(name, fn):
    """One member, its own snapshot: env, cwd and sys.path restored afterwards."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_path = list(sys.path)
    print("== member %s: start" % name)
    try:
        rc = fn()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
        sys.path[:] = saved_path
    rc = int(rc or 0)
    print("== member %s: exit %d" % (name, rc))
    return rc


def _suite_test_toolsmith():
    """The toolsmith: making or importing a drop-in tool must be provable, not hopeful.

The public shapes ship three starter tools and the shapes doc; this is the tool that lets a
fresh install (and a model that has never seen a tool file) create one or import an existing
script. Its job is not to WRITE the file - that is a text write - but to prove the harness's
OWN loader accepts it: `check` runs load_tool_defs, the single reader create_tool and the
write verifier both use, so "OK" is the loader's verdict and not this tool's opinion.

    python tests/test_agent_surface.py
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


def _suite_test_subagent_result():
    """A sub-agent's yield is TYPED, and an unparseable one is still the work.

delegate_task used to hand the parent raw prose, so "did the check pass" had to be read
out of a paragraph by the same model that asked for it. Now the sub-agent ends with one
fenced ```result block, the harness validates it, and the parent gets fields - which is
what makes "have an agent that did not write the work check it" a usable instruction.

Fail-soft is the point of half these checks: a sub-agent that ignores the contract, or
returns JSON with the wrong shape, still comes back with its answer attached and a
marker saying the shape is missing. Losing the work to a parse error would be worse than
losing the shape.

    python tests/test_agent_surface.py
    (the whole gate: python tests/run_all.py)
"""
    import importlib.util
    import json
    import os
    import sys
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    spec = importlib.util.spec_from_file_location("tinycmdr_subagent_under_test", SRC)
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_subagent_under_test"] = fb
    spec.loader.exec_module(fb)

    PASSES = []
    FAILS = []


    def check(name, cond, detail=""):
        (PASSES if cond else FAILS).append(name)
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


    def block(**kw):
        return "```result\n" + json.dumps(kw) + "\n```"


    OK = 'Checked the three claims.\n' + block(status="ok", summary="all three hold",
                                               evidence=["pytest: 12 passed"],
                                               blockers=[], followups=["ship it"])
    typed, why = fb.parse_subagent_result(OK)
    check("a well-formed yield parses", typed is not None, why)
    check("status survives", (typed or {}).get("status") == "ok")
    check("summary survives", (typed or {}).get("summary") == "all three hold")
    check("evidence is a list", (typed or {}).get("evidence") == ["pytest: 12 passed"])
    check("empty fields are empty, not missing", (typed or {}).get("blockers") == [])

    # ---- the parent's view -----------------------------------------------------------
    out = fb.render_subagent_result(typed, why, OK)
    check("the parent is told the yield is typed", "typed sub-agent result" in out, out[:80])
    check("with the status in the header", "status ok" in out, out[:80])
    check("and each field on its own line", "summary:" in out and "evidence:" in out
          and "blockers: none" in out and "followups: ship it" in out, out)
    check("the raw words still ride along", "Checked the three claims." in out, out)
    check("the machine block is not repeated as prose", "```result" not in out, out)

    # ---- the fail-soft paths ---------------------------------------------------------
    for name, text, why_bit in (("no block at all", "I looked around, everything seems fine.",
                                 "no ```result block"),
                                ("not JSON", "notes\n```result\n{not json}\n```",
                                 "is not JSON"),
                                ("a JSON array", "```result\n[1, 2]\n```", "not a JSON object"),
                                ("an invented status", block(status="probably", summary="x"),
                                 "not one of ok|blocked|failed"),
                                ("no summary", block(status="ok", summary="   "),
                                 "no summary")):
        t, w = fb.parse_subagent_result(text)
        check("%s is UNPARSED" % name, t is None and why_bit in w, w)
        r = fb.render_subagent_result(t, w, text)
        check("  ...and the answer is still handed over", "UNPARSED" in r
              and "read them as prose" in r, r[:120])

    check("an empty answer does not vanish", "(empty answer)" in
          fb.render_subagent_result(None, "no block", ""))

    # ---- statuses and shape edges ----------------------------------------------------
    t, w = fb.parse_subagent_result(block(status="blocked", summary="no key on the box",
                                          blockers=["needs DEEPSEEK_API_KEY"]))
    check("blocked carries its blocker", t["status"] == "blocked" and t["blockers"],
          (t, w))
    t, _ = fb.parse_subagent_result(block(status="failed", summary="suite is red",
                                          evidence="pytest: 3 failed"))
    check("a bare string field is accepted as one line",
          t["evidence"] == ["pytest: 3 failed"], t)
    t, _ = fb.parse_subagent_result(block(status="ok", summary="s",
                                          evidence=["e%d" % i for i in range(20)]))
    check("fields are capped, not unbounded", len(t["evidence"]) == 8, len(t["evidence"]))

    quoted = ("here is the shape I will use:\n" + block(status="ok", summary="example")
              + "\nthe real one:\n" + block(status="blocked", summary="real answer"))
    t, w = fb.parse_subagent_result(quoted)
    check("the LAST block wins", t["status"] == "blocked" and t["summary"] == "real answer", (t, w))
    check("a quoted example does not decide the yield", "example" not in t["summary"], t)

    long = "x" * 5000 + block(status="ok", summary="s")
    r = fb.render_subagent_result(None, "why", long)
    check("a runaway answer is trimmed with a pointer",
          "trimmed" in r and len(r) < 2600, len(r))

    # ---- the wiring: the contract rides the TASK ------------------------------------
    seen = {}


    def fake_run(key, text, **kw):
        seen["key"] = key
        seen["text"] = text
        seen["source"] = kw.get("source")
        return OK


    real_run, real_reset, real_relay = fb.AGENT.run, fb.AGENT.reset, fb._relay_callbacks
    try:
        fb.AGENT.run = fake_run
        fb.AGENT.reset = lambda key: None
        fb._relay_callbacks = lambda ctx, src: {}
        out = fb.tool_delegate_task({"task": "verify the last change"}, {"session_key": "s1"})
    finally:
        fb.AGENT.run, fb.AGENT.reset, fb._relay_callbacks = real_run, real_reset, real_relay

    check("the sub-agent is told how to end", fb._SUBAGENT_RESULT_CONTRACT in seen["text"],
          seen["text"][-120:])
    check("and the task is still at the front, unchanged",
          seen["text"].startswith("verify the last change"), seen["text"][:60])
    check("the parent gets the typed rendering back", "typed sub-agent result" in out, out[:100])
    check("the sub-agent runs in its own session", str(seen["key"]).startswith("sub-"),
          seen["key"])
    check("its lines are tagged as a subtask", str(seen["source"]).startswith("sub:"),
          seen["source"])
    check("the tool schema advertises the typed yield",
          "TYPED" in fb.CORE_TOOLS["delegate_task"]["schema"]["function"]["description"])
    check("a sub-agent cannot spawn a sub-agent",
          "cannot spawn further" in fb.tool_delegate_task({"task": "x"}, {"depth": 1}))

    print()
    print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
    sys.exit(1 if FAILS else 0)


def main():
    rc = 0
    for name, fn in (("test_toolsmith", _suite_test_toolsmith), ("test_subagent_result", _suite_test_subagent_result)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
