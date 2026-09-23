"""Drop-in tools: the three loader shapes, the starter tools, fetch_url multi.

Run:  python tests/test_dropin_tools.py
      python -m pytest -q tests/test_dropin_tools.py
Same shape as tests/test_ledger.py: imports the build under test as a module
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
    out = tool({"action": "list"}, None)
    check("process: list shows both jobs", "b1" in out and "b2" in out, out)
    out = tool({"action": "status", "id": "b9"}, None)
    check("process: an unknown id says so", out.startswith("ERROR"), out)


# ------------------------------------------------------------ fetch multi

class _FakeResp:
    def __init__(self, text):
        self._raw = text.encode("utf-8")
        self.encoding = "utf-8"
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


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"--- {t.__name__}")
        try:
            t()
        except Exception as e:
            FAILURES.append(f"{t.__name__} raised: {type(e).__name__}: {e}")
            print(f"FAIL {t.__name__} raised: {type(e).__name__}: {e}")
    print(f"\n{len(PASSES)} checks passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
