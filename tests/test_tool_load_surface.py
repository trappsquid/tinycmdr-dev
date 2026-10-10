"""test_tool_load_surface - one merged suite (test_dropin_tools, test_tool_discovery).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: test_dropin_tools: globals()-> _ns.
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


def _suite_test_dropin_tools():
    """Drop-in tools: the three loader shapes, the starter tools, fetch_url multi.

Run:  python tests/test_tool_load_surface.py, or
      python tests/run_all.py --filter dropin_tools
Not pytest, deliberately: `check()` records a failure and the suite's exit code is the
verdict, so pytest would report this file green regardless of what the checks said.
Same shape as the other staged suites: imports the build under test as a module
(TINYCMDR_TEST_APP or TINYCMDR_SRC picks the build; default tinycmdr.py), works
in a temp tree, no network and no Mattermost connection.
"""
    import copy
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    import time
    import atexit
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / (os.environ.get("TINYCMDR_TEST_APP")
                  or os.environ.get("TINYCMDR_SRC") or "tinycmdr.py")
    spec = importlib.util.spec_from_file_location("tinycmdr_dropin_under_test", SRC)
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_dropin_under_test"] = fb
    spec.loader.exec_module(fb)

    TMP = Path(tempfile.mkdtemp(prefix="fbdropin-"))
    atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))
    FAILURES = []
    PASSES = []


    def check(name, cond, detail=""):
        if cond:
            PASSES.append(name)
        else:
            FAILURES.append(f"{name}: {detail}")
            print(f"FAIL {name}: {detail}")


    def tool_files_dir(name):
        """A fresh tools/ dir holding only what this test drops in."""
        d = TMP / name / "tools"
        d.mkdir(parents=True, exist_ok=True)
        return d


    # ---------------------------------------------------------------- the shapes

    def test_native_shape_loads():
        defs = fb.load_tool_defs(BASE / "tools" / "patch.py")
        check("native: the starter patch.py loads", len(defs) == 1, defs)
        name, desc, params, fn, mutates = defs[0]
        check("native: NAME/DESCRIPTION/SCHEMA come through",
              name == "patch" and bool(desc) and isinstance(params, dict), defs[0])
        check("native: MUTATES=True is honoured", mutates is True, mutates)
        check("native: run(args, ctx) is callable", callable(fn), fn)


    def test_register_shape_loads():
        d = tool_files_dir("reg")
        (d / "regtool.py").write_text(
            "from tools.registry import registry, tool_error\n"
            "def _hello(args, **kw):\n"
            "    return 'hello ' + str(args.get('who', 'world'))\n"
            "registry.register(name='hello',\n"
            "    schema={'name': 'hello', 'description': 'Say hello',\n"
            "            'parameters': {'type': 'object',\n"
            "                           'properties': {'who': {'type': 'string'}}}},\n"
            "    handler=lambda args, **kw: _hello(args))\n"
            "registry.register(name='bye',\n"
            "    schema={'name': 'bye', 'description': 'Say bye',\n"
            "            'parameters': {'type': 'object', 'properties': {}}},\n"
            "    handler=lambda args, **kw: 'bye', mutates=True)\n",
            encoding="utf-8")
        defs = fb.load_tool_defs(d / "regtool.py")
        check("register: two tools from one file", [t[0] for t in defs] == ["hello", "bye"],
              defs)
        out = defs[0][3]({"who": "mars"}, None)
        check("register: the handler runs (args dict in, text out)", out == "hello mars", out)
        check("register: mutates=True rides the register() call", defs[1][4] is True)

        (d / "gated.py").write_text(
            "from tools.registry import registry\n"
            "registry.register(name='nope', schema={'name': 'nope',\n"
            "    'description': 'never', 'parameters': {}},\n"
            "    handler=lambda args, **kw: 'x', check_fn=lambda: False)\n",
            encoding="utf-8")
        try:
            fb.load_tool_defs(d / "gated.py")
            check("register: a check_fn that says no is refused", False, "loaded anyway")
        except ValueError as e:
            check("register: a check_fn that says no is refused", "cannot run here" in str(e), e)

        (d / "envtool.py").write_text(
            "from tools.registry import registry\n"
            "registry.register(name='envy', schema={'name': 'envy',\n"
            "    'description': 'needs a key', 'parameters': {}},\n"
            "    handler=lambda args, **kw: 'x', requires_env=['TINYCMDR_NO_SUCH_KEY'])\n",
            encoding="utf-8")
        try:
            fb.load_tool_defs(d / "envtool.py")
            check("register: a missing requires_env is refused", False, "loaded anyway")
        except ValueError as e:
            check("register: a missing requires_env is refused", "env var" in str(e), e)


    def test_reload_tool_finds_a_ported_file_by_its_registered_name():
        """A register-shape file answers to the name inside it, which need not be the file name.

    reload_tool("todo_list") answered "no tools/todo_list.py or .tool.json to load" - so an
    imported tool could not be reloaded the way a native one can.
    """
        d = tool_files_dir("portedreload")
        (d / "ported_probe.py").write_text(
            "from tools.registry import registry\n"
            "registry.register(name='ported_probe_tool', schema={'name': 'ported_probe_tool',\n"
            "    'description': 'probe', 'parameters': {}},\n"
            "    handler=lambda args, **kw: 'probe ok')\n", encoding="utf-8", newline="")
        reg = type(fb.REGISTRY)(d)
        check("a ported file loads under its registered name", "ported_probe_tool" in reg.custom,
              sorted(reg.custom))
        ok, err = reg.reload_tool("ported_probe_tool")
        check("and reload_tool finds it by that name", ok, err)
        check("the file name is not the tool name (the point of the check)",
              not (d / "ported_probe_tool.py").exists())


    def test_manifest_shape_loads_and_runs():
        d = tool_files_dir("man")
        (d / "greet.tool.json").write_text(json.dumps({
            "name": "greet",
            "description": "Greet through a script of any language",
            "schema": {"type": "object",
                       "properties": {"who": {"type": "string"}}},
            "command": [sys.executable, "-c",
                        "import sys, json; a = json.load(sys.stdin); "
                        "print('hi', a.get('who', 'world'))"],
        }), encoding="utf-8")
        defs = fb.load_tool_defs(d / "greet.tool.json")
        check("manifest: loads", len(defs) == 1 and defs[0][0] == "greet", defs)
        out = defs[0][3]({"who": "mars"}, None)
        check("manifest: args in on stdin, stdout is the result",
              "hi mars" in out and out.startswith("exit_code=0"), out)

        (d / "bad.tool.json").write_text(json.dumps({"name": "bad"}),
                                         encoding="utf-8")
        try:
            fb.load_tool_defs(d / "bad.tool.json")
            check("manifest: no command is refused", False, "loaded anyway")
        except ValueError as e:
            check("manifest: no command is refused", "command" in str(e), e)


    def test_a_file_with_no_tool_is_refused():
        d = tool_files_dir("none")
        (d / "empty.py").write_text("x = 1\n", encoding="utf-8")
        try:
            fb.load_tool_defs(d / "empty.py")
            check("a toolless file is refused", False, "loaded anyway")
        except ValueError as e:
            check("a toolless file is refused", "no tool here" in str(e), e)
        (d / "half.py").write_text("NAME = 'half'\n", encoding="utf-8")
        try:
            fb.load_tool_defs(d / "half.py")
            check("half a native tool is refused", False, "loaded anyway")
        except ValueError as e:
            check("half a native tool is refused", "missing" in str(e), e)


    def test_a_tool_that_exits_the_interpreter_is_refused_not_fatal():
        """A drop-in tool calling sys.exit() killed the process during import.

    [RAN] `printf 'import sys\\nsys.exit(3)\\n' > tools/evil.py; python -c "import
    tinycmdr"` exited 3 with an empty log and no traceback, and every restart did the same,
    because `_load_path` caught only Exception. The file must be REFUSED - named, with the
    exception type - and the rest of the tools/ directory must still load.
    """
        d = tool_files_dir("sysexit")
        (d / "evil.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
        (d / "good.py").write_text(
            "NAME = 'good'\nDESCRIPTION = 'still loads'\nSCHEMA = {}\n"
            "def run(args, ctx):\n    return 'ok'\n", encoding="utf-8")
        reg = type(fb.REGISTRY)(d)                       # never raises
        check("a tool that exits the interpreter does not take the registry down",
              "good" in reg.custom, sorted(reg.custom))
        check("...and the exiting file is simply absent",
              "evil" not in reg.custom, sorted(reg.custom))
        ok, err = reg._load_path(d / "evil.py")
        check("the loader refuses it", not ok, err)
        check("...and names the exception type, not just '3'",
              "SystemExit" in str(err), err)


    def test_create_tool_rolls_back_a_file_that_cannot_load():
        """create_tool left the broken file on disk when the reload failed.

    [RAN] `create_tool` -> `SystemExit 9`, `tools/brk.py still on disk: True`, and
    two consecutive restarts both exited 9 with an empty log: the harness bricked itself.
    The file create_tool just wrote must be gone when it cannot be loaded.
    """
        d = tool_files_dir("rollback")
        saved_dir, saved_reg = fb.TOOLS_DIR, fb.REGISTRY
        fb.TOOLS_DIR = d
        fb.REGISTRY = type(fb.REGISTRY)(d)
        try:
            out = fb.tool_create_tool({"name": "evil", "code": "import sys\nsys.exit(3)\n"}, {})
            check("create_tool refuses a file that exits during import",
                  str(out).startswith("ERROR"), out)
            check("...and does not leave it behind for the next start",
                  not (d / "evil.py").exists(),
                  sorted(p.name for p in d.iterdir()))
            check("...and the registry is still usable", fb.REGISTRY.custom == {},
                  sorted(fb.REGISTRY.custom))
        finally:
            fb.TOOLS_DIR, fb.REGISTRY = saved_dir, saved_reg


    def test_registry_loads_all_three_shapes():
        d = tool_files_dir("all")
        shutil.copy(BASE / "tools" / "patch.py", d / "patch.py")
        (d / "regtool.py").write_text(
            "from tools.registry import registry\n"
            "registry.register(name='hello', schema={'name': 'hello',\n"
            "    'description': 'Say hello', 'parameters': {'type': 'object'}},\n"
            "    handler=lambda args, **kw: 'hello')\n",
            encoding="utf-8")
        (d / "greet.tool.json").write_text(json.dumps({
            "name": "greet", "description": "greet", "schema": {"type": "object"},
            "command": [sys.executable, "-c", "print('hi')"]}), encoding="utf-8")
        reg = fb.ToolRegistry(d)
        check("registry: all three shapes are callable",
              {"patch", "hello", "greet"} <= set(reg.custom), sorted(reg.custom))
        ok, err = reg.reload_tool("greet")
        check("registry: reload_tool finds a manifest by name", ok, err)
        ok, err = reg.reload_tool("nothing-here")
        check("registry: an unknown name says what it looked for",
              not ok and "no tools/" in str(err), err)


    def test_probe_and_loader_agree():
        d = tool_files_dir("probe")
        good = d / "hello.tool.json"
        good.write_text(json.dumps({
            "name": "hello", "description": "greet", "schema": {"type": "object"},
            "command": [sys.executable, "-c", "print('hi')"]}), encoding="utf-8")
        bad = d / "empty.py"
        bad.write_text("x = 1\n", encoding="utf-8")
        v = fb.probe_tool(good)
        check("probe_tool: manifest verdict is ok", v.get("ok") is True, v)
        v = fb.probe_tool(bad)
        check("probe_tool: toolless file verdict is not ok", v.get("ok") is False, v)
        (d / "renamed.py").write_text(
            "NAME = 'other_name'\nDESCRIPTION = 'one line'\n"
            "SCHEMA = {'type': 'object'}\n"
            "def run(args, ctx):\n    return 'ok'\n",
            encoding="utf-8")
        v = fb.probe_tool(d / "renamed.py")
        check("probe_tool: a native NAME that differs from the file is refused",
              v.get("ok") is False and "registers it as" in str(v.get("why")), v)
        (d / "port_tool.py").write_text(   # the ported-file naming convention
            "from tools.registry import registry\n"
            "registry.register(name='web_extract', schema={'name': 'web_extract',\n"
            "    'description': 'port', 'parameters': {}},\n"
            "    handler=lambda args, **kw: 'ok')\n",
            encoding="utf-8")
        v = fb.probe_tool(d / "port_tool.py")
        check("probe_tool: a register()-shape port keeps its own tool name",
              v.get("ok") is True and v.get("names") == ["web_extract"], v)
        # the write verifier's subprocess must reach the same verdicts. None means
        # the probe could not judge (no interpreter/config there) - a SKIP, and
        # anything it DOES judge must agree with probe_tool.
        for path in (good, bad):
            ok, why = fb._exercise_tool_load(path)
            expect = fb.probe_tool(path).get("ok")
            check(f"the loader probe agrees with the loader on {path.name}",
                  ok is None or ok is expect, f"{ok} vs {expect}: {why}")


    # ------------------------------------------------------------ the starters

    def test_patch_edits_and_keeps_the_file_crlf():
        d = TMP / "patch"
        d.mkdir(parents=True, exist_ok=True)
        target = d / "target.py"
        original = b"def one():\r\n    return 1\r\n\r\ndef two():\r\n    return 2\r\n"
        target.write_bytes(original)
        tool = fb.load_tool_defs(BASE / "tools" / "patch.py")[0][3]
        # the anchor is dedented and differently cased: exact match would fail
        out = tool({"path": str(target),
                    "old_string": "DEF ONE():\n    RETURN 1",
                    "new_string": "def one():\n    return 111"}, None)
        check("patch: fuzzy anchor matched and reported", "OK: patched" in out, out)
        check("patch: the diff comes back", "--- diff ---" in out and "+    return 111" in out, out)
        raw = target.read_bytes()
        check("patch: the file stays CRLF (no bare LF introduced)",
              raw.count(b"\n") == raw.count(b"\r\n"), raw)
        check("patch: the edit landed", b"return 111" in raw and b"return 2" in raw, raw)
        bak = target.with_name("target.py.bak").read_bytes()
        check("patch: the backup is byte-identical to the original", bak == original, bak)
        out = tool({"path": str(target), "old_string": "nothing like this",
                    "new_string": "x"}, None)
        check("patch: a missing anchor is a clear refusal",
              out.startswith("ERROR") and "four match modes" in out, out)


    def test_patch_ambiguity_and_replace_all():
        d = TMP / "patch2"
        d.mkdir(parents=True, exist_ok=True)
        target = d / "dup.txt"
        target.write_text("a\nX\nb\nX\n", encoding="utf-8")
        tool = fb.load_tool_defs(BASE / "tools" / "patch.py")[0][3]
        out = tool({"path": str(target), "old_string": "x", "new_string": "Y"}, None)
        check("patch: two matches without replace_all is refused",
              out.startswith("ERROR") and "occurs 2 times" in out, out)
        out = tool({"path": str(target), "old_string": "x", "new_string": "Y",
                    "replace_all": True}, None)
        check("patch: replace_all does both",
              "OK: patched" in out and target.read_text(encoding="utf-8") == "a\nY\nb\nY\n",
              out)


    def test_process_lifecycle():
        d = tool_files_dir("proc")
        shutil.copy(BASE / "tools" / "process.py", d / "process.py")
        tool = fb.load_tool_defs(d / "process.py")[0][3]
        # the STRING form with a quoted spaced exe path: this exact shape died with
        # 'is not recognized as an internal or external command' before the tool
        # took shell strings as shell strings. -u because stdout to a file is
        # block-buffered (the tool says so in its start note).
        long_cmd = f'"{sys.executable}" -u -c "import time; print(\'up\'); time.sleep(30)"'
        out = tool({"action": "start", "command": long_cmd}, None)
        check("process: start names an id and a log", "started b1" in out and "log:" in out, out)
        check("process: start warns about block-buffered child output",
              "block-buffered" in out, out)
        time.sleep(1.0)
        out = tool({"action": "status", "id": "b1"}, None)
        check("process: status says running", "running" in out, out)
        out = tool({"action": "output", "id": "b1"}, None)
        check("process: output tails the log", "up" in out, out)
        out = tool({"action": "kill", "id": "b1"}, None)
        check("process: kill works", "killed" in out, out)
        out = tool({"action": "status", "id": "b1"}, None)
        check("process: a killed job is finished", "finished" in out or "exit" in out, out)

        quick = [sys.executable, "-c", "print(42)"]   # the argv-list form
        tool({"action": "start", "command": quick}, None)
        out = tool({"action": "wait", "id": "b2", "timeout": 30}, None)
        check("process: wait returns on completion", "exit 0" in out, out)
        # the argv list sent as a JSON STRING: a session that never held this schema guesses
        # the shape
        tool({"action": "start",
              "command": json.dumps([sys.executable, "-c", "print(7)"])}, None)
        out = tool({"action": "wait", "id": "b3", "timeout": 30}, None)
        check("process: an argv list sent as a JSON string is repaired",
              "exit 0" in out, out)
        out = tool({"action": "list"}, None)
        check("process: list shows both jobs", "b1" in out and "b2" in out, out)
        out = tool({"action": "status", "id": "b9"}, None)
        check("process: an unknown id says so", out.startswith("ERROR"), out)

        # The harness hands a drop-in tool its own shell and its safety tier (measured
        # 2026-09-25 on that same box: string commands went to cmd.exe while the prompt says the shell
        # is PowerShell, and a .ps1 launched through this tool did what the shell tier refuses).
        # The context used to hardcode `powershell`, so on a Mac the job never started and both
        # checks read as "ERROR: no job 'b4'" rather than testing what they mean to. The shell
        # is the HOST's - and so is the loop it runs.
        if os.name == "nt":
            ps_ctx = {"shell_argv": lambda c: ["powershell", "-NoProfile", "-Command", c],
                      "shell_guard": lambda text: None}
            loop = 'for ($i=1; $i -le 2; $i++) { Write-Output ("tick " + $i) }'
        else:
            ps_ctx = {"shell_argv": lambda c: ["/bin/sh", "-c", c],
                      "shell_guard": lambda text: None}
            loop = 'for i in 1 2; do echo "tick $i"; done'
        tool({"action": "start", "command": loop}, ps_ctx)
        out = tool({"action": "wait", "id": "b4", "timeout": 30}, ps_ctx)
        check("process: a string command runs in the harness's own shell",
              "exit 0" in out, out)
        out = tool({"action": "output", "id": "b4"}, ps_ctx)
        check("process: and its loop really ran",
              "tick 1" in out and "tick 2" in out, out)
        guarded = {"shell_argv": lambda c: ["powershell", "-NoProfile", "-Command", c],
                   "shell_guard": lambda text: "BLOCKED: test refusal"}
        out = tool({"action": "start", "command": "mkfs.ext4 /dev/sda1"}, guarded)
        check("process: a refused command never starts", out.startswith("BLOCKED"), out)
        out = tool({"action": "start", "command": ["mkfs.ext4", "/dev/sda1"]}, guarded)
        check("process: an argv list is guarded too", out.startswith("BLOCKED"), out)


    # ------------------------------------------------------------ fetch multi

    class _FakeResp:
        def __init__(self, text):
            self._raw = text.encode("utf-8")
            self.encoding = "utf-8"
            # _fetch_page follows redirects by hand since 2026-10-09: it reads status_code
            # and Location before it reads the body.
            self.status_code = 200
            self.headers = {}
            self.closed = False
            self.pulls = 0

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            self.pulls += 1
            for i in range(0, len(self._raw), size):
                yield self._raw[i:i + size]

        def close(self):
            self.closed = True


    class _FakeRequests:
        def __init__(self):
            self.got = []
            self.resps = []

        def get(self, url, **kw):
            resp = _FakeResp(f"<html><body><p>page {len(self.got)} body</p>"
                             f"</body></html>")
            self.got.append((url, kw))
            self.resps.append(resp)
            return resp


    def test_fetch_url_takes_one_or_five():
        if not hasattr(fb, "tool_fetch_url"):
            print("  (skipped: this build cuts the web tools by design)")
            return
        real = fb.requests
        # `http://a/1` cannot resolve, so the egress gate refuses it while the default
        # (search.allow_cloud_egress=false) holds. This check is about url parsing and the
        # section headers; the gate is graded in tests/test_search_surface.py.
        fb.CONFIG["search"]["allow_cloud_egress"] = True
        try:
            fb.requests = _FakeRequests()
            out = fb.tool_fetch_url({"url": "http://a/1"}, None)
            check("fetch_url: a single url is unchanged (no section header)",
                  "---" not in out and "page 0 body" in out, out)
            fb.requests = _FakeRequests()
            out = fb.tool_fetch_url({"url": ["http://a/1", "http://a/2"]}, None)
            check("fetch_url: two urls, two labelled sections",
                  "--- http://a/1 ---" in out and "--- http://a/2 ---" in out
                  and "page 0 body" in out and "page 1 body" in out, out)
            check("fetch_url: every response is closed",
                  all(r.closed for r in fb.requests.resps), fb.requests.resps)
            check("fetch_url: the read is bounded and streamed (pull happened)",
                  all(r.pulls >= 1 for r in fb.requests.resps),
                  [r.pulls for r in fb.requests.resps])
            out = fb.tool_fetch_url({"url": []}, None)
            check("fetch_url: an empty list is an error", out.startswith("ERROR"), out)
        finally:
            fb.requests = real


    def test_manifest_commands_walk_the_confirm_tier():
        # security review 2026-09-23: a manifest command IS a sh -c / cmd /c string
        d = tool_files_dir("beltcmd")
        man = d / "belt_cmd.tool.json"
        man.write_text(json.dumps({
            "name": "belt_cmd",
            "description": "probe for the confirm tier",
            "schema": {"type": "object", "properties": {}},
            "command": "Remove-Item -Recurse -Force C:\\Temp"}), encoding="utf-8")
        defs = fb.load_tool_defs(man)
        out = defs[0][3]({}, {"session_key": "dropin"})
        check("manifest: a confirm-tier command is declined before it runs",
              out.startswith("DECLINED"), out[:160])


    def test_a_tools_file_not_seen_at_the_last_start_announces_itself():
        import logging as _logging
        d = tool_files_dir("prov")

        def _tool(who):
            return ("NAME = '%s'\nDESCRIPTION = 'probe'\n"
                    "SCHEMA = {'type': 'object', 'properties': {}}\n"
                    "def run(args, ctx=None):\n    return 'ok'\n" % who)

        (d / "old_hand.py").write_text(_tool("old_hand"), encoding="utf-8")
        reg = fb.ToolRegistry(d)
        rec = d.parent / "tools-provenance.json"
        seen = []
        handler = _logging.Handler()
        handler.emit = lambda r: seen.append(r.getMessage())
        fb.log.addHandler(handler)
        try:
            fb._PROVENANCE_DONE = False
            reg.note_provenance()
            check("provenance: a record-less install bootstraps silently",
                  rec.exists() and not seen, seen)
            (d / "later.py").write_text(_tool("later"), encoding="utf-8")
            seen.clear()
            fb._PROVENANCE_DONE = False
            reg.note_provenance()
            check("provenance: a file that appeared between starts announces itself",
                  any("later.py" in m for m in seen), seen)
            check("provenance: ...and both names are in the record",
                  sorted(json.loads(rec.read_text(encoding="utf-8"))["files"])
                  == ["later.py", "old_hand.py"], rec.read_text(encoding="utf-8"))
        finally:
            fb.log.removeHandler(handler)


    def test_tool_index_files_a_tool_on_its_declared_shelf():
        """The index's shelf for a tool: declared by the author, else derived, never missing.

    A declared CATEGORY is the author's own word for the shelf, and it is read from the
    SOURCE (a native file's module attribute, a manifest's "category"), not from the loaded
    module - re-exec'ing a tool to ask it a question is how a planted file runs twice. A
    file that declares nothing gets a derived shelf, which is why a dropped-in tool needs no
    edit to appear in the prompt's index.
    """
        d = tool_files_dir("index")
        (d / "sweep.py").write_text(
            'NAME = "sweep"\n'
            'DESCRIPTION = "Tidy the workshop"\n'
            'CATEGORY = "shop & tools"\n'
            'SCHEMA = {"type": "object", "properties": {}}\n'
            'def run(args, ctx):\n    return "ok"\n',
            encoding="utf-8", newline="")
        (d / "greet.tool.json").write_text(json.dumps({
            "name": "greet", "description": "Greet someone", "category": "messaging & chat",
            "schema": {"type": "object"},
            "command": [sys.executable, "-c", "print('hi')"]}), encoding="utf-8")
        for _n in ("plain", "small"):
            (d / ("%s.py" % _n)).write_text(
                'NAME = "%s"\n'
                'DESCRIPTION = "Report free space on a drive letter"\n'
                'SCHEMA = {"type": "object", "properties": {}}\n'
                'def run(args, ctx):\n    return "ok"\n' % _n,
                encoding="utf-8", newline="")
        reg = fb.ToolRegistry(d)
        check("a native CATEGORY is read off the source", reg.custom["sweep"]["category"]
              == "shop & tools", reg.custom["sweep"].get("category"))
        check("a manifest's category is read too",
              reg.custom["greet"]["category"] == "messaging & chat",
              reg.custom["greet"].get("category"))
        check("a file that declares nothing carries no shelf",
              reg.custom["plain"]["category"] == "", reg.custom["plain"].get("category"))
        block = fb.tool_index_block(reg.custom)
        check("the declared shelf is the line the tool is listed under",
              "shop & tools: sweep" in block, block)
        check("a derived shelf sits beside a declared one", "files & edit: plain" in block, block)
        check("and the block is names only, never description prose",
              "Tidy the workshop" not in block and "Report free space" not in block, block)
        _shelves = {fb._tool_category(n, tools=reg.custom) for n in reg.custom}
        check("the index is one line per SHELF, not one per tool",
              len(block.splitlines()) == len(_shelves) and len(reg.custom) > len(_shelves),
              (block, sorted(_shelves)))
        _keep = dict(fb.CONFIG["agent"])
        try:
            fb.CONFIG["agent"]["tool_index_max_names_per_line"] = 1
            wide = fb.tool_index_block(reg.custom)
            check("a capped NAME list names what it left out and the call that resolves it",
                  '+1 more (find_tools {"category": "files & edit"})' in wide, wide)
            check("...and it still shows one name per shelf, not none",
                  "files & edit: plain ..." in wide, wide)
            fb.CONFIG["agent"]["tool_index_max_categories"] = 1
            narrow = fb.tool_index_block(reg.custom)
            check("a capped CATEGORY list says how many shelves it hid",
                  "+2 more categories" in narrow and narrow.count("\n") == 1, narrow)
            # (the cap's own bound is graded at 300 tools in tests/tool_index_scale.py: on a
            #  three-shelf registry the overflow TEXT can make a tighter cap the longer render)
            check("and the tighter cap is the shorter render", len(narrow) < len(wide),
                  (narrow, wide))
        finally:
            fb.CONFIG["agent"].clear()
            fb.CONFIG["agent"].update(_keep)
        check("the caps are restored after the probe",
              fb.tool_index_block(reg.custom) == block)


    def main():
        tests = [v for k, v in sorted(_ns.items()) if k.startswith("test_")]
        for t in tests:
            print(f"--- {t.__name__}")
            try:
                t()
            except Exception as e:
                FAILURES.append(f"{t.__name__} raised: {type(e).__name__}: {e}")
                print(f"FAIL {t.__name__} raised: {type(e).__name__}: {e}")
        print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
        return 1 if FAILURES else 0
    _ns = dict(locals())
    return main()


def _suite_test_tool_discovery():
    """Tool discovery: a capability question must not be answered with a wrong tool.

calls, each answered "[HARNESS: now callable]" with a tool that does something else -

    "send Mattermost message to channel"            -> `schedule`  (the word "channel")
    "send Mattermost post message channel thread"   -> `blog`      (the word "post")
    "send mattermost message to agent channel via API" -> `delegate_task` ("agent")

- and then 35 minutes of rebuilding by hand a capability the box did not have. The word
overlap score is the defect: it fired on the words that appear in half the registry, so a
MISS now returns this box's whole remaining surface instead, and a query that names no
capability is told so instead of being guessed at.

    python tests/test_tool_load_surface.py
"""
    import importlib.util
    import json
    import logging
    import os
    import re
    import shutil
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")


    def shipped_tool_sources():
        """The tools/ files that are part of THIS repository, by absolute path - or None.

    tools/ is per-host by design: .gitignore carries everything but the starter files, and
    what an operator drops in is the operator's (the toolsmith grades those). The shelf rule
    below is a promise about the surface tinycmdr SHIPS, so it must not go red because a box
    added a tool - measured 2026-09-29 on macOS: two host tools with no category,
    130 passed / 1 failed, while the same tree archived to a clean checkout was 129/129.

    Asking git, rather than keeping a second list of what ships, is what keeps the answer
    from going stale. If git cannot answer, None exempts nothing and the rule stays as strict
    as it was.
    """
        try:
            out = subprocess.run(["git", "-C", str(BASE), "ls-files", "-z", "--", "tools"],
                                 capture_output=True, text=True, timeout=20)
        except Exception:
            return None
        if out.returncode != 0:
            return None
        return {(BASE / rel).resolve() for rel in out.stdout.split("\0") if rel}


    # A `python` reachable BY NAME for the checks that run one through the shell.
    # The shell tool runs its command in the real shell, so `python -c "print(123)"` needs an
    # interpreter called `python` on PATH. run_all.py invokes this suite as
    # `<venv>/bin/python tests/...`, which does NOT put that bin on the child's PATH: measured
    # 2026-09-26, the one-liner answered exit_code=127 and this suite went 125 passed / 2
    # failed purely from how it was started. The interpreter running this file always exists,
    # so make it reachable by that name.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    if shutil.which("python") is None:
        _shim_dir = Path(tempfile.mkdtemp(prefix="fbshell-python-"))
        _shim = _shim_dir / ("python.exe" if os.name == "nt" else "python")
        try:
            os.symlink(sys.executable, _shim)
        except (OSError, NotImplementedError):
            shutil.copy2(sys.executable, _shim)      # a copy is a poor alias, but it runs
        os.environ["PATH"] = str(_shim_dir) + os.pathsep + os.environ["PATH"]

    spec = importlib.util.spec_from_file_location("tinycmdr_discovery_under_test", SRC)
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_discovery_under_test"] = fb
    spec.loader.exec_module(fb)

    PASSES = []
    FAILS = []


    def check(name, cond, detail=""):
        (PASSES if cond else FAILS).append(name)
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


    def asked(session, **args):
        return fb.tool_find_tools(args, {"session_key": session})


    revealed = lambda out: "now callable" in out or "every remaining tool is now" in out
    TAIL = "Everything else on this machine"

    # ---- the three measured queries are the regression this suite exists for ---------
    # What was measured (a live Windows install, 2026-09-23): these three revealed a WRONG tool -
    # `schedule` (the word "channel"), `blog` ("post"), `delegate_task` ("agent") - and the box then
    # spent 35 minutes rebuilding a capability it already had. The REGRESSION is the wrong answer,
    # not the existence of an answer: on a box that owns a mattermost tool (a host that manages other installs' own
    # tree grew `tools/mattermost_ops.py` at 18:34 on 2026-09-25) revealing it for "send Mattermost
    # message to channel" is correct, and a suite that called that a failure would be red on the very
    # box the tool was written for.
    WRONG = ("`schedule`", "`blog`", "`delegate_task`")
    for i, q in enumerate(["send Mattermost message to channel",
                           "send Mattermost post message channel thread",
                           "send mattermost message to agent channel via API"]):
        out = asked("regress%d" % i, query=q)
        check("never the old wrong tool for %r" % q[:38],
              not any(w in out for w in WRONG), out[:160])
        if revealed(out):
            check("  ...a reveal names a tool that MATCHES the ask %r" % q[:30],
                  "mattermost" in out.lower(), out[:200])
        else:
            check("  ...a miss says so for %r" % q[:38], "No tool matched" in out, out[:120])
            check("  ...and nothing was put in the payload",
                  fb.hidden_tools("regress%d" % i) == fb.hidden_tools(None))
    _m = fb._match_tools("send Mattermost message to channel", 4, None)
    check("the score itself never returns one of the old wrong tools",
          not [t for t in _m if t in ("schedule", "blog", "delegate_task")], _m)

    # ---- a query that names a capability still reveals the right tool ----------------
    CAPS = [("keep noisy log digging out of my own context", "delegate_task"),
            ("subagent", "delegate_task"),
            ("schedule a job every morning", "schedule"),
            ("grep files by regex content", "search_files"),
            ("what did we do in a past session", "search_sessions"),
            ("fuzzy anchor edit a file", "patch"),
            ("restart the tinycmdr gateway", "tinycmdr_restart")]
    hidden0 = set(fb.hidden_tools(None))
    ran = 0
    for q, want in CAPS:
        if want not in hidden0:
            continue                      # cut from this build, or already always-visible
        ran += 1
        out = asked("cap%d" % ran, query=q)
        check("%r finds %s" % (q[:40], want), want in out and revealed(out), out[:140])
    check("the capability table ran against this build", ran >= 3, ran)

    # ---- a query with no discriminating word is told so, not guessed at --------------
    gen = "send a message to the channel"
    check("generic words are not scored on",
          fb.discriminating_words(gen) == [], fb.discriminating_words(gen))
    out = asked("s-gen", query=gen)
    check("a capability-free query says so", "names no capability" in out, out[:140])
    check("and it reveals nothing", not revealed(out))
    check("and it still names what exists", TAIL in out, out[:140])

    # ---- every discovery answer carries the remaining surface ------------------------
    out = asked("s-miss", query="zzzznothing")
    check("a miss keeps the contract the older tests pin",
          "No tool matched" in out and "all=true" in out, out[:120])
    check("a miss names the surface, not a bare name list", TAIL in out, out[:160])
    out = asked("s-hit", query="subagent")
    check("a hit names the surface too", TAIL in out, out[:160])
    out = asked("s-empty")
    check("an empty query is the 'show me everything' call",
          "Tools this box has that your list does not" in out and "call" in out, out[:140])
    check("an empty query reveals nothing", not revealed(out))
    check("the surface names runbooks for procedures", "runbooks" in out, out[:160])
    check("the surface is bounded", len(out) < 2500, len(out))
    check("a miss points at the runbooks as well", "runbooks" in asked("s-miss2", query="zzzznothing"))
    check("nothing is left hidden for a miss", fb.hidden_tools("s-miss") == fb.hidden_tools(None))

    # ---- the surface line is a bounded summary, not the whole registry ---------------
    tail = fb._surface_tail(None)
    check("the tail names hidden tools with what they do", " (" in tail and ":" not in tail[:2],
          tail[:120])
    check("the tail is bounded", len(tail) < 1200, len(tail))
    many = fb._surface_tail(None, limit=2)
    if len(hidden0) > 2:
        check("and it says how many it left out", "more, all=true" in many, many[-80:])
    check("no hidden tools, no tail", fb._surface_tail("done", exclude=sorted(hidden0)) == "")

    # ---- list_tools stays BOUNDED (pinned in test_stall too). Not one bare line any more:
    # under disclosure the payload carries part of the core set, and the old wording told the
    # model it already held all of them - measured 2026-09-24 on three boxes, where the run
    # that read it never reached for create_tool and scaffolded the file through the shell.
    # The answer now names the count it really has and the tools it does not; it is spent
    # only when the model asks, so the budget is a few hundred characters, not 220.
    out = fb.tool_list_tools({}, {})
    check("list_tools stays bounded", len(out) < 2500, len(out))
    check("and it forwards to discovery", "find_tools" in out, out)

    # ---- an unknown tool name teaches the surface, absent stays absent --------------
    _, _, out = fb.Agent._exec_tool(fb.AGENT, {"function": {"name": "delegate_tasksk",
                                                            "arguments": {}}},
                                    {"session_key": "s-unk"})
    check("an unknown name gets the closest matches", "unknown tool" in out and "find_tools" in out,
          out[:120])
    check("and the whole remaining surface", TAIL in out, out[:160])
    _, _, out = fb.Agent._exec_tool(fb.AGENT, {"function": {"name": "browser_navigate",
                                                           "arguments": {}}},
                                    {"session_key": "s-absent"})
    check("a tool this box never had still reads as absent",
          "exists on this box" in out and "find_tools" not in out, out[:140])
    _dropin = fb.TOOLS_DIR / "half_baked.py"
    _dropin.write_text("NAME = 'half_baked'\nDESCRIPTION = 'x'\nSCHEMA = {}\n"
                       "def run(args, ctx):\n    return ''\n", encoding="utf-8")
    try:
        _, _, out = fb.Agent._exec_tool(fb.AGENT, {"function": {"name": "half_baked",
                                                                "arguments": {}}},
                                        {"session_key": "s-dropin"})
        check("a tool file on disk that is not loaded is named, not denied",
              "exists on disk" in out and "next start" in out, out[:200])
    finally:
        _dropin.unlink()

    # ---- the hidden tools are NAMED in the static prompt (first operator drive, ---------
    # 2026-09-23). The model would not spend the discovery call: asked which tool edits by a
    # fuzzy anchor it answered edit_file and named 3 of the 7 hidden ones, and across three
    # runs it called find_tools ONCE. The names now ride the static prompt, generated from the
    # build so a new hidden tool cannot fall out of it.
    INV_MARK = "not in your tool list \u2014 call one by name and it stays for the session: "


    def inv_names(prompt):
        """The names the inventory line carries, parsed the way a reader would."""
        rows = [l for l in prompt.splitlines() if INV_MARK in l]
        if len(rows) != 1:
            return None
        after = rows[0].split(INV_MARK, 1)[1].split(" Those are core tools")[0]
        return [w.strip() for w in after.replace(".", "").split(",") if w.strip()]


    sp = fb.build_system_prompt()
    # A pre-fix build has no such helper: the gate must FAIL, not crash with an AttributeError.
    inv_line = getattr(fb, "hidden_inventory_line", lambda: "")()
    named = inv_names(sp)
    want_inv = [n for n in fb.hidden_tools(None) if n in fb.CORE_TOOL_NAMES]
    check("the static prompt carries ONE hidden-tool inventory line", named is not None, sp[-600:])
    check("it names every hidden core tool this build has", named == want_inv, (named, want_inv))
    named = named or []
    check("and invents none", bool(named) and all(n in fb.CORE_TOOL_NAMES for n in named), named)
    check("it is generated, not typed: the set is what hidden_tools() reports",
          named == sorted(set(named)) and inv_line.count(", ".join(named)) == 1, inv_line[:160])
    # The CONTRACT is "the inventory line is spliced directly after the preceding bullet, with
    # no blank line between them" - a blank field of its own would render it as a separate
    # paragraph. It used to be pinned as a byte sequence ending in the text of the bullet that
    # happened to precede it ("named in its prompt).\n- Also on this box"), which went red the
    # moment that bullet's trailing prose was trimmed (2026-09-27) - a change to the prompt,
    # not to the layout. Pinned structurally instead: exactly one inventory line, and the line
    # above it is the tail of a bullet.
    _lines = sp.splitlines()
    _inv_rows = [i for i, l in enumerate(_lines) if l.startswith("- Also on this box")]
    check("the inventory line directly follows the tool bullet (no blank field left behind)",
          len(_inv_rows) == 1 and _inv_rows[0] > 0
          and _lines[_inv_rows[0] - 1].startswith("- ")
          and _lines[_inv_rows[0] - 1].strip() != "",
          _inv_rows)
    check("the line is bounded", len(inv_line) < 320, len(inv_line))
    check("the prompt is still byte-identical across two builds",
          fb.build_system_prompt() == sp)
    check("the placeholder is a live field, not literal braces", "{inventory}" not in sp)
    check("the line says these are CORE tools (a live install mislabeled them custom)",
          "Those are core tools" in fb.hidden_inventory_line(), fb.hidden_inventory_line()[:200])
    check("and points at the custom block for the rest",
          "custom tools listed at the end" in fb.hidden_inventory_line())
    # The skill block is generated from the runbooks INSTALLED on the box, and ./skills is
    # gitignored (they are the operator's own procedures), so a clean clone has none and the
    # sentence above was never in the prompt: measured 2026-09-26, this check was the suite's
    # only real failure. Own the input instead of the host's disk, the way the parked-skill
    # block below does: point SKILLS_DIR at a temp dir, once with a runbook in it and once with
    # nothing, so both halves of the contract are graded.
    skdir = Path(tempfile.mkdtemp(prefix="fbskills-"))
    (skdir / "demo-runbook").mkdir()
    (skdir / "demo-runbook" / "SKILL.md").write_text(
        "---\nname: demo-runbook\ndescription: a fixture runbook\n---\nsteps\n",
        encoding="utf-8", newline="\n")
    (skdir / "empty").mkdir()
    _keep_skills_dir = fb.SKILLS_DIR
    try:
        fb.SKILLS_DIR = skdir / "demo-runbook"
        sp_skilled = fb.build_system_prompt()
        fb.SKILLS_DIR = skdir / "empty"
        sp_bare = fb.build_system_prompt()
    finally:
        fb.SKILLS_DIR = _keep_skills_dir
        shutil.rmtree(skdir, ignore_errors=True)
    check("prompt: the runbook block carries the rule this inventory depends on",
          "a skill is a runbook, not a tool" in sp_skilled)
    check("...and names the runbook that is installed",
          "demo-runbook" in sp_skilled)
    check("...and is absent when no runbook is installed (no dangling header)",
          "a skill is a runbook, not a tool" not in sp_bare
          and "Prose skills installed" not in sp_bare)

    # The index is bounded like every sibling surface (the tool index, the memory index and
    # the notes block all have a cap): descriptions are dropped for a tail of NAMES before a
    # name is lost, because a name is what the `skill` tool is asked for.
    _skcap = Path(tempfile.mkdtemp(prefix="fbskills-cap-"))
    for _i in range(8):
        _d = _skcap / ("runbook-%02d" % _i)
        _d.mkdir()
        (_d / "SKILL.md").write_text(
            "---\nname: runbook-%02d\ndescription: %s\n---\nbody\n"
            % (_i, "long description " * 30), encoding="utf-8", newline="\n")
    _keep_cap_dir = fb.SKILLS_DIR
    _keep_index_cap = fb.CONFIG["agent"].get("skills_index_max_chars")
    try:
        fb.SKILLS_DIR = _skcap
        fb.CONFIG["agent"]["skills_index_max_chars"] = 300
        _sp = fb.build_system_prompt()
        _lines = [l for l in _sp.splitlines()
                  if l.startswith("- runbook-") or "more skills" in l]
        check("an over-budget skills index drops descriptions before names",
              len(_lines) == 8 and "long description" not in _sp
              and sum(len(l) + 1 for l in _lines) <= 300,
              (_lines[:2], len(_lines)))
        fb.CONFIG["agent"]["skills_index_max_chars"] = 60
        _sp = fb.build_system_prompt()
        _lines = [l for l in _sp.splitlines()
                  if l.startswith("- runbook-") or "more skills" in l]
        check("...and names how many were dropped when even names overflow",
              len(_lines) < 8 and "more skills" in _sp, _lines)
        # An explicit 0 is the shipped DEFAULT, not an unreachable "unlimited" mode: the cap
        # getter folds 0 into the default and floors at 512, so the old `cap <= 0` branch was
        # dead code advertising a mode no config value could enter (run 22, A-2026-10-07-59).
        fb.CONFIG["agent"]["skills_index_max_chars"] = 0
        _sp0 = fb.build_system_prompt()
        _lines0 = [l for l in _sp0.splitlines()
                   if l.startswith("- runbook-") or "more skills" in l]
        check("an explicit 0 means the shipped default, not an unlimited index",
              "long description" not in _sp0
              and sum(len(l) + 1 for l in _lines0) <= 2000,
              (_lines0[:1], len(_lines0)))
    finally:
        fb.SKILLS_DIR = _keep_cap_dir
        if _keep_index_cap is None:
            fb.CONFIG["agent"].pop("skills_index_max_chars", None)
        else:
            fb.CONFIG["agent"]["skills_index_max_chars"] = _keep_index_cap
        shutil.rmtree(_skcap, ignore_errors=True)

    # The knob a reader can move must be in the file a reader opens: `skills_index_max_chars`
    # bounded the skills index (the one prompt budget that grows with the operator's OWN
    # collection) and appeared in no config file, no doc and no example - so an operator who
    # wanted it smaller had nothing to find, and one who set 0 saw no change (run 22,
    # A-2026-10-07-59). The template states the shipped default for every prompt-budget key.
    _example_path = BASE / "config.example.json"
    if not _example_path.exists():
        print("note  no config.example.json beside this suite (a staged pre-fix build): the "
              "template checks are skipped, they read the tree")
    else:
        _example_agent = json.loads(_example_path.read_text(encoding="utf-8"))["agent"]
        for _key in ("skills_index_max_chars", "skills_always_max_chars",
                     "context_files_max_chars"):
            check("config.example.json states the shipped default for %s" % _key,
                  _example_agent.get(_key) == fb.DEFAULT_CONFIG["agent"].get(_key),
                  (_example_agent.get(_key), fb.DEFAULT_CONFIG["agent"].get(_key)))

    # A SKILL.md edited between two builds MOVES the static prompt, which costs a full
    # re-prefill of the conversation - and the block's docstring claimed it was identical for
    # every call in a session, so nothing said a thing. Cost is not the reason to care
    # (measured 2026-10-07: 2.9 ms per build with 40 runbooks); knowing is (run 22, A-55).
    _skmove = Path(tempfile.mkdtemp(prefix="fbskills-move-"))
    (_skmove / "r1").mkdir()
    (_skmove / "r1" / "SKILL.md").write_text(
        "---\nname: r1\ndescription: first wording\n---\nbody\n", encoding="utf-8", newline="\n")
    _keep_move_dir = fb.SKILLS_DIR
    _move_log = []


    class _MoveHandler(logging.Handler):
        def emit(self, record):
            _move_log.append(record.getMessage())


    _move_handler = _MoveHandler()
    try:
        fb.SKILLS_DIR = _skmove
        fb.log.addHandler(_move_handler)
        fb.build_system_prompt()
        (_skmove / "r1" / "SKILL.md").write_text(
            "---\nname: r1\ndescription: second wording\n---\nbody\n",
            encoding="utf-8", newline="\n")
        fb.build_system_prompt()
    finally:
        fb.log.removeHandler(_move_handler)
        fb.SKILLS_DIR = _keep_move_dir
        shutil.rmtree(_skmove, ignore_errors=True)
    check("a skill edited between two builds is logged as a prefix move",
          any("STATIC prompt moved" in m for m in _move_log), _move_log)

    # Always-runbooks announce a cut and name what the budget skipped: a rule that quietly
    # loses its tail reads as a complete rule.
    _skalw = Path(tempfile.mkdtemp(prefix="fbskills-always-"))
    for _i in (0, 1):
        _d = _skalw / ("always-%02d" % _i)
        _d.mkdir()
        (_d / "SKILL.md").write_text(
            "---\nname: always-%02d\nalways: true\ndescription: rule\n---\n" % _i
            + ("RULE LINE %02d\n" % _i) * 40, encoding="utf-8", newline="\n")
    _keep_alw_dir = fb.SKILLS_DIR
    _keep_alw_cap = fb.CONFIG["agent"].get("skills_always_max_chars")
    try:
        fb.SKILLS_DIR = _skalw
        fb.CONFIG["agent"]["skills_always_max_chars"] = 300
        fb._ALWAYS_SKILLS_CACHE.update({"at": 0, "block": ""})
        _blk = fb.always_skills_block()
        check("an over-budget always-runbook is cut at a line boundary and marked",
              "truncated" in _blk and "RULE LINE 00" in _blk
              and "RULE LINE 39" not in _blk, _blk[-200:])
        check("...and the runbook the spent budget skipped is named",
              "SKIPPED" in _blk and "always-01" in _blk, _blk[-200:])
    finally:
        fb.SKILLS_DIR = _keep_alw_dir
        if _keep_alw_cap is None:
            fb.CONFIG["agent"].pop("skills_always_max_chars", None)
        else:
            fb.CONFIG["agent"]["skills_always_max_chars"] = _keep_alw_cap
        fb._ALWAYS_SKILLS_CACHE.update({"at": 0, "block": ""})
        shutil.rmtree(_skalw, ignore_errors=True)

    check("prompt: the routing bullet names the shell verbs it replaces",
          "Select-String" in sp and "findstr" in sp and "search_files {pattern, path}" in sp)
    check("prompt: the routing bullet carries search_files' own call shape",
          "{pattern, path}" in sp)

    # A tool made hidden by CONFIG must appear: that is the drift the hand-written list had.
    _keep_core = fb.CONFIG["agent"].get("core_tools")
    _keep_disc = fb.CONFIG["agent"].get("tool_disclosure")
    try:
        fb.CONFIG["agent"]["core_tools"] = [n for n in fb.core_tool_names() if n != "shell"]
        narrow = getattr(fb, "hidden_inventory_line", lambda: "")()
        check("a tool hidden by config shows up in the inventory", "shell" in narrow, narrow)
        check("...and the line is regenerated, not padded",
              inv_names(fb.build_system_prompt()) ==
              [n for n in fb.hidden_tools(None) if n in fb.CORE_TOOL_NAMES])
        fb.CONFIG["agent"]["core_tools"] = sorted(fb.CORE_TOOL_NAMES)
        none_line = getattr(fb, "hidden_inventory_line", lambda: "x")()
        check("nothing hidden, no dangling line", none_line == "", none_line)
        fb.CONFIG["agent"]["tool_disclosure"] = False
        off_line = getattr(fb, "hidden_inventory_line", lambda: "x")()
        check("disclosure off means no line (every tool is in the payload)",
              off_line == "" and inv_names(fb.build_system_prompt()) is None)
    finally:
        fb.CONFIG["agent"]["tool_disclosure"] = _keep_disc
        if _keep_core is None:
            fb.CONFIG["agent"].pop("core_tools", None)
        else:
            fb.CONFIG["agent"]["core_tools"] = _keep_core
    check("the suite left the config as it found it",
          inv_names(fb.build_system_prompt()) == want_inv)

    # ---- a "verification" that does not test the claim -------------------------------
    # Found in the same drive: it "proved" write_file wrote a file by reading that the file
    # exists. The clause is on the check bullet, where the mistake is made.
    check("prompt: the check must test the claim itself",
          "make the check test the claim itself" in sp)
    check("prompt: the exists-is-not-evidence case is named",
          "a file existing proves nothing about what is in it or who wrote it" in sp)
    check("prompt: the clause rides the check bullet",
          [l for l in sp.splitlines() if "make the check test the claim itself" in l][:1]
          and "Checking the work is the last step" in
          [l for l in sp.splitlines() if "make the check test the claim itself" in l][0])

    # ---- a miss must name the RIGHT door (drive round 3, 2026-09-23) ---------------------
    # The model went looking for a hidden tool's shape and used the skill tool for it, then typed
    # the tool name into the shell. Both are misses the harness can answer precisely.
    out = fb.tool_skill({"action": "read", "name": "search_files"}, {})
    check("the skill tool says a TOOL name is a tool, not a skill",
          "is a TOOL on this box" in out and "not a skill" in out, out[:160])
    check("and points at the call and at find_tools",
          "call it by name" in out and "find_tools" in out, out[:200])
    check("and carries the tool's arguments, so the miss costs no second hop",
          "Its arguments:" in out and "pattern" in out, out[:300])
    check("and stays bounded", 0 < len(out) < 900, len(out))
    out = fb.tool_skill({"action": "read", "name": "no-such-runbook-xyz"}, {})
    check("an ordinary skill miss stays an ordinary miss",
          "No skill named" in out and "is a TOOL" not in out, out[:120])
    check("and its name list is bounded on a box with many skills",
          len(out) < 1200 or "more (skill action=list" in out, len(out))

    # ---- parked skills are parked (2026-09-24) --------------------
    # skills/.imported-unused kept 76 SKILL.md runbooks for reference and skill_index()
    # rglob'd through them: the static prompt carried all 108 blurbs every call (5,366
    # of its 20,942 chars, measured 2026-09-23). A dot dir must never be indexed.
    _tmp = Path(tempfile.mkdtemp(prefix="tinycmdr-skills-"))
    (_tmp / "live").mkdir()
    (_tmp / "live" / "SKILL.md").write_text(
        "---\nname: liveskill\ndescription: a live one\n---\nbody\n",
        encoding="utf-8", newline="\n")
    (_tmp / ".imported-unused").mkdir()
    (_tmp / ".imported-unused" / "SKILL.md").write_text(
        "---\nname: parkedskill\ndescription: a parked one\n---\nbody\n",
        encoding="utf-8", newline="\n")
    _keep_skills_dir = fb.SKILLS_DIR
    try:
        fb.SKILLS_DIR = _tmp
        _names = [s["name"] for s in fb.skill_index()]
        check("a skill in a parked dot dir never reaches the index",
              "parkedskill" not in _names, _names)
        check("the live skill beside it still does",
              "liveskill" in _names, _names)
    finally:
        fb.SKILLS_DIR = _keep_skills_dir

    # ---- the operator-only switch reaches the TOOL, not just the prompt -------------
    # The switch used to stop at the prompt: `hide: true` trimmed the index while
    # skill{list} still served the runbook's name and skill{read} its body. Operator-only
    # now means absent from list/search, refused on read, and opened for a session only
    # when the OPERATOR names it in an order.
    _tmp2 = Path(tempfile.mkdtemp(prefix="tinycmdr-oponly-"))
    (_tmp2 / "open").mkdir()
    (_tmp2 / "open" / "SKILL.md").write_text(
        "---\nname: openskill\ndescription: a normal runbook\n---\nbody\n",
        encoding="utf-8", newline="\n")
    (_tmp2 / "vault").mkdir()
    (_tmp2 / "vault" / "SKILL.md").write_text(
        "---\nname: vaultrunbook\ndescription: operator-only runbook\nhide: true\n---\n"
        "SECRET BODY\n", encoding="utf-8", newline="\n")
    _keep2 = fb.SKILLS_DIR
    try:
        fb.SKILLS_DIR = _tmp2
        out = fb.tool_skill({"action": "list"}, {"session_key": "opo1"})
        check("an operator-only runbook is absent from skill list",
              "vaultrunbook" not in out and "openskill" in out, out)
        out = fb.tool_skill({"action": "read", "name": "vaultrunbook"},
                            {"session_key": "opo1"})
        check("reading it without the operator's word is refused",
              "operator-only" in out and "SECRET BODY" not in out, out[:160])
        out = fb.tool_skill({"action": "search", "name": "vaultrunbook", "topic": "secret"},
                            {"session_key": "opo1"})
        check("searching it is refused the same way",
              "operator-only" in out and "SECRET BODY" not in out, out[:160])
        out = fb.tool_skill({"action": "read", "name": "nope"}, {"session_key": "opo1"})
        check("a name miss does not leak operator-only runbook names",
              "vaultrunbook" not in out, out[:160])
        check("the operator naming it grants the session",
              bool(fb.grant_named_skills("opo1", "run the vaultrunbook procedure") and
                   fb._skill_granted([s for s in fb.skill_index()
                                      if s["name"] == "vaultrunbook"][0], "opo1")))
        out = fb.tool_skill({"action": "read", "name": "vaultrunbook"},
                            {"session_key": "opo1"})
        check("now the body serves, and the read says why",
              "SECRET BODY" in out and "operator" in out.lower(), out[:160])
        out = fb.tool_skill({"action": "read", "name": "vaultrunbook"},
                            {"session_key": "opo2"})
        check("the grant is per session, not per box", "operator-only" in out, out[:160])
        check("the folder name is an alias the operator can name",
              fb.grant_named_skills("opo3", "check the vault folder") != [])
        out = fb.tool_skill({"action": "read", "name": "vaultrunbook"},
                            {"session_key": "opo3"})
        check("naming the folder alias opened it too", "SECRET BODY" in out, out[:160])
        fb.grant_named_skills("opo4", "disvault is unrelated text")
        out = fb.tool_skill({"action": "read", "name": "vaultrunbook"},
                            {"session_key": "opo4"})
        check("a name inside a longer word does not grant",
              "operator-only" in out, out[:160])
    finally:
        fb._skill_grants.clear()
        fb.SKILLS_DIR = _keep2

    out = fb.tool_shell({"command": "list_tools"}, {"session_key": "s-bare"})
    check("a bare tool name typed into the shell is answered as a tool",
          "is a TOOL on this box" in out and "Call list_tools directly" in out
          and "schema is now in your tool list" in out, out[:160])
    out = fb.tool_shell({"command": "echo hi"}, {"session_key": "s-bare2"})
    check("a real command still runs", "exit_code=0" in out, out[:80])

    # ---- round 3: the same miss, piped; a gated write; a claim that is not a measurement ----
    out = fb.tool_shell({"command": "list_tools 2>&1 | Select-String \"delegate\""},
                        {"session_key": "s-bare3"})
    check("a tool name as the first token of a pipeline is answered as a tool too",
          "is a TOOL on this box" in out, out[:160])

    # ---- round 6: the tool RUN AS A SCRIPT (`python toolsmith.py ...`) -------------------
    # Found on a live install 2026-09-23: every tool file in ./tools/ is also a runnable script, so
    # this miss SUCCEEDS and the model never self-corrects. Told to build a tool, the run ran
    # `python toolsmith.py "action=new" ...` (which worked), and never made the `toolsmith` TOOL
    # CALL its prompt names.
    # The probe tool is REGISTERED HERE rather than naming a tool that happens to be in this
    # checkout: an earlier version of these checks named toolsmith and passed only while another
    # suite's leftovers sat in the shared staging dir. A check that depends
    # on a sibling suite, or on the repo's tools/ folder, is not a check.
    _keep_custom = dict(fb.REGISTRY.custom)
    fb.REGISTRY.custom["probetool"] = {
        "fn": lambda args, ctx: "staged probe ran",
        "source": "<test_tool_discovery>",
        "mutates": False,
        "endpoint_touching": False,
        "schema": {"type": "function", "function": {
            "name": "probetool",
            "description": "staged by this suite to prove the shell answers a tool run as a "
                           "script",
            "parameters": {"type": "object",
                           "properties": {"action": {"type": "string"}},
                           "required": []}}},
    }
    try:
        out = fb.tool_shell({"command": 'cd C:\\x; python probetool.py "action=new"'},
                            {"session_key": "s-script1"})
        check("a tool run as a script is answered as a tool",
              "is a TOOL on this box" in out and "Call probetool directly" in out
              and "schema is now in your tool list" in out, out[:160])
        check("...and the answer carries that tool's arguments",
              "Its arguments:" in out and "action" in out, out[:300])
        for _cmd in ("python -m probetool action=list",
                     "python C:\\somewhere\\tools\\probetool.py action=list",
                     "python tools/probetool.py action=list"):
            out = fb.tool_shell({"command": _cmd}, {"session_key": "s-script-" + _cmd[:8]})
            check("the same miss is answered for %r" % _cmd[:34],
                  "is a TOOL on this box" in out, out[:120])
    finally:
        fb.REGISTRY.custom.clear()
        fb.REGISTRY.custom.update(_keep_custom)

    # ---- round 7: the skill tool, with the RIGHT verb ------------------------------------
    # Nine of eleven skill calls in one round went to `skill{action:list|search, name:<tool>}`,
    # one answered with 8 KB of skill taxonomy. The door is the same whichever verb was guessed.
    # The name is the one a live install actually used, and it is a CORE tool, so this half needs no
    # staged registry entry.
    for _act in ("list", "search", "read"):
        out = fb.tool_skill({"action": _act, "name": "search_sessions", "topic": "x"}, {})
        check("the skill tool answers a TOOL name for action=%s too" % _act,
              "is a TOOL on this box" in out and "Its arguments:" in out, out[:180])
    # ---- no dead ends: every shape the schema permits answers actionably (2026-10-05) ----
    # Measured on Linux: a run stuck on a Mattermost token asked
    # `skill{"action":"search","topic":"mattermost token mmctl ..."}` with no name, got
    # `No skill named ''` - an empty name and nothing to try - and re-issued its last shell
    # call until the loop guard wrapped the run up. A bare topic searches the whole shelf now,
    # and the other schema-legal shapes say what to pass instead of answering with a blank.
    _tmp_search = Path(tempfile.mkdtemp(prefix="tinycmdr-search-"))
    (_tmp_search / "mm").mkdir()
    (_tmp_search / "mm" / "SKILL.md").write_text(
        "---\nname: mattermost-ops\ndescription: tokens\n---\n"
        "# Minting a token\n\nUse mmctl to mint a Mattermost token.\n",
        encoding="utf-8", newline="\n")
    (_tmp_search / "posters").mkdir()
    (_tmp_search / "posters" / "SKILL.md").write_text(
        "---\nname: poster-notes\ndescription: posters\n---\n"
        "# Posters\n\nThe printer takes A2.\n", encoding="utf-8", newline="\n")
    _keep_search = fb.SKILLS_DIR
    try:
        fb.SKILLS_DIR = _tmp_search
        out = fb.tool_skill({"action": "search", "topic": "sandwich"}, {})
        check("a bare topic search never answers with an empty name",
              "No skill named ''" not in out and "''" not in out, out[:160])
        out = fb.tool_skill({"action": "search", "topic": "mattermost token mmctl"}, {})
        check("a bare topic search reads every runbook and labels the hit",
              "mattermost-ops ::" in out and "Minting a token" in out, out[:160])
        out = fb.tool_skill({"action": "search", "name": "poster-notes", "topic": "printer"}, {})
        check("a named search still reads just that runbook, unlabelled",
              out.startswith("--- SKILL.md ::") and "mattermost" not in out, out[:120])
        out = fb.tool_skill({"action": "search"}, {})
        check("a search with no topic says to pass one", "`topic`" in out, out[:160])
        out = fb.tool_skill({"action": "read"}, {})
        check("a read with no name says a name is needed",
              "`name`" in out and "No skill named ''" not in out, out[:160])
        out = fb.tool_skill({"action": "Search", "topic": "mattermost"}, {})
        check("a capitalised verb is the same verb", "Minting a token" in out, out[:120])
        out = fb.tool_skill({"action": "find", "name": "poster-notes"}, {})
        check("an unhandled verb names the actions it has",
              "unknown action" in out and "No skill named" not in out, out[:160])
        out = fb.tool_skill({"action": "search", "topic": "MATTERMOST TOKEN"}, {})
        check("an uppercase topic still finds its words", "Minting a token" in out, out[:120])
    finally:
        fb.SKILLS_DIR = _keep_search
        shutil.rmtree(_tmp_search, ignore_errors=True)

    out = fb.tool_shell({"command": 'python -c "print(123)"'}, {"session_key": "s-script-c"})
    check("a plain interpreter one-liner still runs", "123" in out, out[:120])
    check("...and is not mistaken for a tool",
          "is a TOOL on this box" not in out, out[:120])

    _keep_confirm = fb.CONFIG["agent"].get("confirm_patterns")
    # The probe writes go to a temp dir, never into tests/sessions/: a suite grades the build,
    # and a write into the checkout is state the next run (and `git status`) has to explain.
    _wprobe = Path(tempfile.mkdtemp(prefix="fbdiscovery-writes-"))
    try:
        fb.CONFIG["agent"]["confirm_patterns"] = ["\\bdel\\s+/[a-z]*[sq]"]
        said = []
        res = fb.tool_write_file({"path": str(_wprobe / "gated-probe.cmd"),
                                  "content": "del /q /s C:\\nowhere\\x\n", "no_backup": True},
                                 {"session_key": "s-gate", "confirm_cb": lambda s: said.append(s) or True})
        check("a gated write asks the operator first", bool(said), said)
        check("and the result says the operator approved it",
              "confirm_patterns" in res and "approved" in res, res[:200])
        res2 = fb.tool_write_file({"path": str(_wprobe / "ungated-probe.txt"),
                                   "content": "just text\n", "no_backup": True},
                                  {"session_key": "s-gate2", "confirm_cb": lambda s: said.append(s) or True})
        check("an ordinary write carries no such line", "approved" not in res2, res2[:160])
    finally:
        if _keep_confirm is None:
            fb.CONFIG["agent"].pop("confirm_patterns", None)
        else:
            fb.CONFIG["agent"]["confirm_patterns"] = _keep_confirm
        shutil.rmtree(_wprobe, ignore_errors=True)

    typed_probe = {"status": "ok", "summary": "everything was fine",
                   "evidence": ["a diff"], "blockers": [], "followups": []}
    rendered = fb.render_subagent_result(typed_probe, "", "the sub-agent's prose")
    check("a typed sub-agent result is labeled a CLAIM, not a measurement",
          "CLAIM, not a tool result" in rendered, rendered[:200])
    check("and it says to re-check a specific fact", "Re-check" in rendered)
    check("the unparsed path keeps its own wording",
          "UNPARSED" in fb.render_subagent_result(None, "no block", "prose"))

    sp2 = fb.build_system_prompt()
    check("prompt: only a tool result proves a tool ran",
          "Only a TOOL RESULT proves a tool ran" in sp2)
    check("prompt: a sub-agent report is a claim, not a measurement",
          "A sub-agent's report is a CLAIM, not a measurement" in sp2)
    check("prompt: a result that is not in context means the call did not happen",
          "If a result is NOT in your context, that call did not happen in this run" in sp2)
    check("prompt: and mining files for it is named as the slow way",
          "the slowest way to answer" in sp2)

    # ---- search_files: the shape the prompt teaches must actually grep -------------------
    # Measured 2026-09-25 on a live install: the route hint and the routing bullet both teach
    # `search_files {"pattern": "<regex>", "path": "<file or directory>"}`, while the tool read
    # `pattern` as a NAME glob and the grep as `content`. The run followed the taught shape and
    # got a confident "No matches." for a string the file holds ten times - a silent wrong
    # answer, and the whole reason the tool is named in the prompt.
    srch = Path(tempfile.mkdtemp(prefix="fbtest-search-"))
    (srch / "one.py").write_text("alpha = 1\nblocked_patterns here\nbeta = 2\n", encoding="utf-8")
    (srch / "two.py").write_text("gamma\n", encoding="utf-8")
    out = fb.tool_search_files({"pattern": "blocked_patterns",
                                "path": str(srch / "one.py")}, {})
    check("search_files: a FILE path with a regex pattern greps it",
          "one.py:2:" in out and "blocked_patterns here" in out, out[:200])
    out = fb.tool_search_files({"pattern": "blocked_patterns", "path": str(srch)}, {})
    check("search_files: a DIRECTORY with a regex pattern greps its files",
          "one.py:2:" in out, out[:200])
    # Every match is reported, not one per file: the directory pass used to `break` after the
    # first hit, so a file holding a pattern four times answered with one line and no note
    # (found 2026-10-02 - a silent wrong answer, the failure this tool exists to avoid).
    (srch / "many.py").write_text("hit one\nnope\nhit two\nhit three\n", encoding="utf-8")
    out = fb.tool_search_files({"pattern": "hit ", "path": str(srch)}, {})
    check("search_files: every match in a file is reported, not just the first",
          "many.py:1:" in out and "many.py:3:" in out and "many.py:4:" in out, out[:300])
    out = fb.tool_search_files({"pattern": "*.py", "path": str(srch)}, {})
    check("search_files: a name glob still lists names",
          "two.py" in out and ":2:" not in out, out[:200])
    out = fb.tool_search_files({"pattern": "nothing_here_at_all", "path": str(srch)}, {})
    check("search_files: a real miss still answers No matches", out == "No matches.", out[:120])
    out = fb.tool_search_files({"content": "gamma", "path": str(srch)}, {})
    check("search_files: content= still greps (the schema-honest shape)",
          "two.py:1:" in out, out[:200])
    out = fb.tool_search_files({"content": "gamma", "pattern": "*.py", "path": str(srch)}, {})
    check("search_files: content= with a glob keeps the glob as the scope",
          "one.py" not in out and "two.py:1:" in out, out[:200])
    out = fb.tool_search_files({"path": str(srch / "missing.txt")}, {})
    check("search_files: a missing path still errors", out.startswith("ERROR"), out[:120])

    # ---- a file too big to content-scan is SKIPPED; say so, or the miss is a lie -----------
    # The directory scan skips files over 2 MB. Silently, that turned "find X under <dir>" into
    # a confident wrong answer whenever X lived in the largest file - which was found instantly
    # when the file was named directly (2026-10-02).
    big = srch / "huge.log"
    big.write_text("filler line\n" * 400000 + "NEEDLE-OVER-CAP\n", encoding="utf-8")
    check("the fixture is over the 2 MB content-scan cap", big.stat().st_size > 2_000_000,
          big.stat().st_size)
    out = fb.tool_search_files({"pattern": "NEEDLE-OVER-CAP", "path": str(srch)}, {})
    check("search_files: a skipped large file is named, not silently omitted",
          out.startswith("No matches.") and "NOT searched" in out and "huge.log" in out,
          out[:240])
    (srch / "small-needle.txt").write_text("NEEDLE-OVER-CAP\n", encoding="utf-8")
    out = fb.tool_search_files({"pattern": "NEEDLE-OVER-CAP", "path": str(srch)}, {})
    check("search_files: a small hit is returned AND the skip is still disclosed",
          "small-needle.txt" in out and "NOT searched" in out, out[:240])

    # ---- the disclosure answer cannot be misread as "nothing is hidden" -------------------
    # Measured 2026-09-25 on a live install: asked which tools were NOT in its list,
    # the run called list_tools and find_tools(all=true) in ONE batch, read "N of N", and
    # answered "None are hidden" - the sibling call had already revealed them all.
    fresh = "wp5-fresh"
    out = fb.tool_list_tools({}, {"session_key": fresh})
    _m = re.search(r"Core tools: (\d+) of (\d+) are in your list", out)
    check("list_tools on a fresh session says part of the core set is missing",
          _m is not None and _m.group(1) != _m.group(2)
          and "answer when you call them by name" in out, out[:400])
    check("list_tools: a fresh session names no reveal", "revealed earlier" not in out, out[-160:])
    revealed_now = fb.tool_find_tools({"all": True}, {"session_key": fresh})
    check("find_tools all=true says which tools were NOT in the list",
          "were NOT in your list a moment ago" in revealed_now, revealed_now[:200])
    check("find_tools all=true still says every one of them is now in the list",
          "every remaining tool is now in your list" in revealed_now, revealed_now[:200])
    after = fb.tool_list_tools({}, {"session_key": fresh})
    check("list_tools after a reveal names the reveal, so N of N cannot read as 'none hidden'",
          "revealed earlier in THIS session" in after and "create_tool" in after, after[:300])
    check("find_tools all=true on an already-visible set says so, not an empty diff",
          fb.tool_find_tools({"all": True}, {"session_key": fresh})
          == "All tools are already in your list for this session.")
    # ---- a CAPABILITY phrase reveals the tool that serves it --------------------------
    # Measured 2026-09-25 on the macOS box: a request to ATTACH a file, and the
    # run spent 22 calls and 194.8K prompt tokens echoing send_file's name in the shell, then
    # failed - the name-driven reveal above never fires for "attach it, do not just paste".
    _saved_core = fb.CONFIG["agent"].get("core_tools")
    fb.CONFIG["agent"]["core_tools"] = ["shell"]
    try:
        check("with send_file hidden, the capability phrase reveals it",
              fb.reveal_tools_named_in(
                  "disc-capability",
                  "save it as ~/mac-status.txt and attach it, do not just paste the text")
              == ["send_file"], sorted(fb.revealed_tools("disc-capability")))
        check("a neutral order still reveals nothing",
              fb.reveal_tools_named_in("disc-capability-none",
                                       "tell me how much disk is left on this box") == [])
        _missing = fb.pinned_core_tools_missing()
        # derived from _DEFAULT_CORE, not from a name spelled here: the default list is what
        # the pin is checked against, and it changed on 2026-09-27 (send_file and list_tools
        # moved behind disclosure), which made a hardcoded "send_file" assertion go stale.
        _expect = [n for n in fb._DEFAULT_CORE if n != "shell"
                   and n in (set(fb.CORE_TOOLS) | set(fb.REGISTRY.custom))]
        check("a pinned core_tools list is checked against _DEFAULT_CORE",
              sorted(_missing) == sorted(_expect), (_missing, _expect))
        _line = fb.capability_line("cli")
        check("and the startup line warns about the stale pin",
              "WARNING" in _line and any(n in _line for n in _expect), _line[:400])
    finally:
        if _saved_core is None:
            fb.CONFIG["agent"].pop("core_tools", None)
        else:
            fb.CONFIG["agent"]["core_tools"] = _saved_core
    check("an unpinned host is not warned", fb.pinned_core_tools_missing() == [],
          fb.pinned_core_tools_missing())
    check("and its capability line carries no warning",
          "WARNING" not in fb.capability_line("cli"))
    # ---- the tool tree: find_tools {category: ...} is the leaf the prompt points at -----
    # The static prompt carries the SKELETON (a shelf per line, the names on it) and the prose
    # sits behind this call: a description line per custom tool cost 167.8 ch / 49.4 est-tok
    # PER TOOL on every call, so the index is
    # capped and the leaf is on demand. Three things a shelf has to do: resolve from the label
    # the prompt shows (and from a shorter word for it), name its tools WITH descriptions, and
    # reveal NOTHING - a reveal is per-session schema rent that calling the tool pays anyway.
    # A-101: the A-79 class again - `_tool_category` raised on a
    # non-string while `_tool_blurb` beside it coerced. The pair must agree. The calls are
    # wrapped because the PRE-FIX build raises right here, and a crashing suite reports less
    # than a failing check (the rest of the suite never runs).
    def _safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:                                   # noqa: BLE001
            return "RAISED %s" % type(e).__name__


    check("_tool_category coerces a non-string instead of raising",
          _safe(fb._tool_category, None) == "" and _safe(fb._tool_category, 12345) == "",
          (_safe(fb._tool_category, None), _safe(fb._tool_category, 12345)))
    check("_tool_blurb coerces a non-string too",
          _safe(fb._tool_blurb, None) == "" and _safe(fb._tool_blurb, 12345) == "",
          (_safe(fb._tool_blurb, None), _safe(fb._tool_blurb, 12345)))

    _shelves = {}
    _other_shipped, _other_host = [], []
    _shipped_sources = shipped_tool_sources()
    for _n in sorted(set(fb.CORE_TOOLS) | set(fb.REGISTRY.custom)):
        _shelf = fb._tool_category(_n)
        _shelves.setdefault(_shelf, []).append(_n)
        if _shelf != fb._TOOL_CATEGORY_OTHER:
            continue
        # A tool that DOES file on a shelf stays graded below whether or not this box added it -
        # only an unclassifiable one is exempt, and only when the repository does not carry it.
        _src = (fb.REGISTRY.custom.get(_n) or {}).get("source")
        _host_added = bool(_shipped_sources is not None and _n not in fb.CORE_TOOLS
                           and _src is not None
                           and Path(_src).resolve() not in _shipped_sources)
        (_other_host if _host_added else _other_shipped).append(_n)
    check("every SHIPPED tool files on a shelf (nothing strays into `other`)",
          not _other_shipped, _other_shipped)
    if _other_host:
        # Said out loud rather than silently skipped: these were not graded, and a reader of this
        # run should know which rule applies to them (the toolsmith's, on the box that owns them)
        # instead of assuming the shelf rule covered them.
        print("     (not graded here - host tools this box added with no shelf: %s)"
              % ", ".join(_other_host))
    for _shelf, _members in sorted(_shelves.items()):
        _out = fb.tool_find_tools({"category": _shelf}, {"session_key": "cat-probe"})
        check("%r names its %d tools with what they do" % (_shelf, len(_members)),
              all(n in _out for n in _members) and "- " in _out, _out[:120])
    check("a shorter word for a shelf resolves too (the prompt's own labels are guessable)",
          "files & edit" in fb.tool_find_tools({"category": "files"},
                                               {"session_key": "cat-short"}))
    # The handler and the prompt both teach `category`; the schema must declare it too, or a
    # schema-conforming caller cannot send the call the prompt tells it to make.
    check("the find_tools schema declares `category`, the call the prompt teaches",
          "category" in str(fb.CORE_TOOLS["find_tools"]["schema"]),
          fb.CORE_TOOLS["find_tools"]["schema"])
    check("a category answer reveals nothing (a reveal is schema rent)",
          fb.visible_tool_names("cat-short") == fb.visible_tool_names("cat-never-used"))
    _out = fb.tool_find_tools({"category": "zzz-not-a-shelf"}, {"session_key": "cat-miss"})
    check("an unknown category names the real ones instead of guessing",
          "no category named" in _out and "files & edit" in _out, _out[:160])
    check("and the miss stays bounded", len(_out) < 600, len(_out))
    _sp = fb.build_system_prompt()
    check("prompt: the index header teaches the call that returns the prose",
          'find_tools {"category": "<cat>"}' in _sp)
    check("prompt: the sentence is one field, not a doubled line",
          _sp.count('find_tools {"category": "<cat>"}') == 1)
    check("prompt: the index block carries NAMES with no per-tool description line",
          all(not _sp.count("  %s: " % n) or _sp.count("  %s: " % n) == 1
              for n in fb.REGISTRY.custom) and
          all(fb.REGISTRY.custom[n]["schema"]["function"]["description"][:30] not in _sp
              for n in fb.REGISTRY.custom), [n for n in fb.REGISTRY.custom])

    # ---- a typo'd reveal TTL never reaches the request builder (A-99) --------------------
    # `float("bogus")` raised a ValueError out of revealed_tools -> visible_tool_names ->
    # select_tool_schemas, i.e. out of building the very next request: one typo in config and
    # the whole conversation dies before a token is sent. Guarded like _mcp_timeout.
    _saved_ttl = fb.CONFIG["agent"].get("reveal_ttl_secs")
    try:
        fb.CONFIG["agent"]["reveal_ttl_secs"] = "bogus"
        try:
            _ttl_out, _ttl_err = fb.select_tool_schemas("ttl-probe"), None
        except Exception as _e:                                    # noqa: BLE001 - the point
            _ttl_out, _ttl_err = None, _e
        check("a non-numeric reveal_ttl_secs does not raise out of the request builder",
              _ttl_err is None and isinstance(_ttl_out, list), _ttl_err)
        _ttl_fn = getattr(fb, "_reveal_ttl_secs", None)
        check("...it falls back to the shipped default of 1800s",
              _ttl_fn is not None and _ttl_fn() == 1800.0,
              _ttl_fn() if _ttl_fn else "no _reveal_ttl_secs")
        fb.CONFIG["agent"]["reveal_ttl_secs"] = 0
        check("...and a real 0 still means 'never decay'",
              _ttl_fn is not None and _ttl_fn() == 0.0,
              _ttl_fn() if _ttl_fn else "no _reveal_ttl_secs")
    finally:
        if _saved_ttl is None:
            fb.CONFIG["agent"].pop("reveal_ttl_secs", None)
        else:
            fb.CONFIG["agent"]["reveal_ttl_secs"] = _saved_ttl

    # ---- reveal_tools takes one name as a string, refuses a non-list (A-100) --------------
    _rt_name = sorted(fb.hidden_tools(None))[0] if fb.hidden_tools(None) else "search_files"
    fb.reveal_tools("rt-str", _rt_name)
    check("reveal_tools reads a bare string as ONE name, not its characters",
          fb.revealed_tools("rt-str") == {_rt_name}, fb.revealed_tools("rt-str"))
    try:
        fb.reveal_tools("rt-bad", 7)
        _rt_err = None
    except TypeError as _e:
        _rt_err = str(_e)
    check("reveal_tools refuses a non-list with a message naming what it wants",
          _rt_err is not None and "list" in _rt_err and "7" in _rt_err, _rt_err)

    # ---- find_tools honours name, topic and action (A-135) ------------------------------
    # `{}`, `action=list`, `action=search topic=...`, `action=read name=X` and a bogus name all
    # returned byte-identical text because the handler read only query/category/all.
    _base = fb.tool_find_tools({}, {"session_key": "ft-args"})
    _out = fb.tool_find_tools({"name": "search_files"}, {"session_key": "ft-name"})
    check("find_tools {name: X} reads that tool back, not the generic list", _out != _base,
          _out[:160])
    check("...and answers with that tool's own blurb and argument schema",
          "search_files" in _out and "args:" in _out, _out[:200])
    check("an unknown name says so instead of listing everything",
          "No tool named" in fb.tool_find_tools({"name": "no_such_tool_zzz"},
                                                {"session_key": "ft-unk"}))
    check("topic filters exactly as query does",
          fb.tool_find_tools({"topic": "zzzznothing"}, {"session_key": "ft-topic"})
          == fb.tool_find_tools({"query": "zzzznothing"}, {"session_key": "ft-topic"}))
    check("action=search with a topic searches, not lists",
          fb.tool_find_tools({"action": "search", "topic": "zzzznothing"},
                             {"session_key": "ft-search"}) != _base)
    check("action=list is the plain surface listing",
          fb.tool_find_tools({"action": "list"}, {"session_key": "ft-list"}) == _base)
    check("action=read without a name says what it needs",
          "needs `name`" in fb.tool_find_tools({"action": "read"}, {"session_key": "ft-rn"}))
    check("a bogus action names the vocabulary",
          "unknown action" in fb.tool_find_tools({"action": "wat"}, {"session_key": "ft-bad"}))
    check("the find_tools schema declares name, topic and action",
          all(k in str(fb.CORE_TOOLS["find_tools"]["schema"])
              for k in ("name", "topic", "action")))

    # ---- create_tool validates its action vocabulary before anything else (A-136) --------
    # action=delete used to fall into the create path and complain about a missing `code`.
    check("create_tool action=delete without a name says what it needs",
          "action=delete needs `name`" in fb.tool_create_tool({"action": "delete"}, {}))
    check("create_tool with a bogus action names the vocabulary",
          "unknown create_tool action" in fb.tool_create_tool({"action": "wat"}, {}))
    check("create_tool action=delete on an unknown tool says so, not 'no code'",
          "no custom tool named" in fb.tool_create_tool(
              {"action": "delete", "name": "no_such_zzz"}, {}))
    # A real delete, staged in a throwaway tools dir so the repository's own tools/ is untouched.
    _del_dir = Path(tempfile.mkdtemp(prefix="fb-toolsdel-"))
    _del_file = _del_dir / "probe_tool.py"
    _del_file.write_text("NAME = 'probe_tool'\n", encoding="utf-8")
    _reg_file = _del_dir / "ported_todo.py"
    _reg_file.write_text("NAME = 'todo_list'\n", encoding="utf-8")
    _saved_tools_dir = fb.TOOLS_DIR
    _saved_custom = dict(fb.REGISTRY.custom)
    try:
        fb.TOOLS_DIR = _del_dir
        fb.REGISTRY.custom["probe_tool"] = {
            "fn": lambda *a: "", "source": _del_file, "mutates": False, "category": "",
            "schema": {"type": "function", "function": {
                "name": "probe_tool", "description": "", "parameters": {}}}}
        fb.REGISTRY.custom["todo_list"] = {
            "fn": lambda *a: "", "source": _reg_file, "mutates": False, "category": "",
            "schema": {"type": "function", "function": {
                "name": "todo_list", "description": "", "parameters": {}}}}
        _del_out = fb.tool_create_tool({"action": "delete", "name": "probe_tool"},
                                       {"session_key": "ft-del"})
        check("create_tool action=delete removes the file and the registration, and says so",
              _del_out.startswith("OK: deleted tools/probe_tool.py")
              and "probe_tool" in _del_out and not _del_file.exists()
              and "probe_tool" not in fb.REGISTRY.custom, _del_out)
        _reg_out = fb.tool_create_tool({"action": "delete", "name": "todo_list"},
                                       {"session_key": "ft-del"})
        check("...and it resolves a register-style tool by its TOOL name, not its file name",
              _reg_out.startswith("OK: deleted tools/ported_todo.py")
              and "todo_list" in _reg_out and not _reg_file.exists()
              and "todo_list" not in fb.REGISTRY.custom, _reg_out)
    finally:
        fb.TOOLS_DIR = _saved_tools_dir
        fb.REGISTRY.custom.clear()
        fb.REGISTRY.custom.update(_saved_custom)
        shutil.rmtree(_del_dir, ignore_errors=True)

    # ---- create_tool refuses a name another CUSTOM tool already holds (A-137) -------------
    # Only CORE_TOOL_NAMES was checked, so create_tool on a name a register-style file held
    # (ported_todo.py registers todo_list) or a drop-in tools/<name>.py got the create path -
    # answered "got no code" and, with code, wrote a second file that shadowed the tool.
    _coll_dir = Path(tempfile.mkdtemp(prefix="fb-toolcoll-"))
    _coll_file = _coll_dir / "ported_todo.py"
    _coll_file.write_text("NAME = 'todo_list'\n", encoding="utf-8")
    _saved_tools2 = fb.TOOLS_DIR
    _saved_custom2 = dict(fb.REGISTRY.custom)
    try:
        fb.TOOLS_DIR = _coll_dir
        fb.REGISTRY.custom["todo_list"] = {
            "fn": lambda *a: "", "source": _coll_file, "mutates": False, "category": "",
            "schema": {"type": "function", "function": {
                "name": "todo_list", "description": "", "parameters": {}}}}
        _c1 = fb.tool_create_tool({"action": "new", "name": "todo_list"}, {})
        check("create_tool refuses a name an existing custom tool holds, naming the shelf",
              _c1.startswith("ERROR") and "already the name of a custom tool" in _c1
              and "tools/ported_todo.py" in _c1, _c1)
        (_coll_dir / "mcp.py").write_text("NAME = 'mcp'\n", encoding="utf-8")
        _c2 = fb.tool_create_tool({"action": "new", "name": "mcp"}, {})
        check("...and a name a drop-in tool FILE holds, before it asks for code",
              _c2.startswith("ERROR") and "tools/mcp.py already exists" in _c2, _c2)
    finally:
        fb.TOOLS_DIR = _saved_tools2
        fb.REGISTRY.custom.clear()
        fb.REGISTRY.custom.update(_saved_custom2)
        shutil.rmtree(_coll_dir, ignore_errors=True)

    # ---- tools_dir_verdict() with no path answers for the shelf (A-138) -------------------
    # `path` was required, so the README's "check one file through the harness's own loader"
    # pointed a human at a call that raised a bare TypeError instead of a verdict.
    try:
        _vd = fb.tools_dir_verdict()
    except TypeError as _e:
        _vd = "TypeError: %s" % _e
    check("tools_dir_verdict() with no argument gives the shelf verdict, not a TypeError",
          isinstance(_vd, str) and "./tools/" in _vd and "drop-in file" in _vd, _vd)

    print()
    print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
    sys.exit(1 if FAILS else 0)


def main():
    rc = 0
    for name, fn in (("test_dropin_tools", _suite_test_dropin_tools), ("test_tool_discovery", _suite_test_tool_discovery)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
