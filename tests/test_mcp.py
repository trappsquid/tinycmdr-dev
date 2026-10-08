"""MCP: the stdio client, its two protocol revisions, and the rent rule.

The stub servers here are real processes speaking newline-delimited JSON-RPC: one that
demands the stateless 2026-07-28 shape (every request must carry `_meta`), one that
demands the older `initialize` handshake first, and one that never answers. What is
graded: discovery, a call, the one-time fallback, process reuse, the honest errors
(unknown server/tool, no answer, dead server), and the rule that a box with no servers
configures registers no tool at all.

    python tests/test_mcp.py
"""
import importlib.util
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / (os.environ.get("TINYCMDR_TEST_APP")
              or os.environ.get("TINYCMDR_SRC") or "tinycmdr.py")
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-mcp"
STAGE.mkdir(parents=True, exist_ok=True)
STUB = STAGE / "mcp_stub.py"
STUB.write_text(r'''
import json, sys
legacy = "--legacy" in sys.argv
silent = "--silent" in sys.argv
srvreq = "--srvreq" in sys.argv
die_on_call = "--die-on-call" in sys.argv
stray = 12 if "--strays" in sys.argv else 0
initialized = False

def send(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()

def reply(o):
    # --srvreq: a server-initiated REQUEST whose id collides with the client's request,
    # sent just before the real reply. --strays: replies for ids the client never asked
    # for, sent ahead of the real one.
    if srvreq:
        send({"jsonrpc": "2.0", "id": o.get("id"), "method": "ping", "params": {}})
    for k in range(stray):
        send({"jsonrpc": "2.0", "id": 900000 + k, "result": {"content": [], "isError": False}})
    send(o)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if "id" not in msg:
        continue
    rid, method = msg.get("id"), msg.get("method")
    params = msg.get("params") or {}
    if silent:
        continue
    if not legacy and "_meta" not in params:
        reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32022,
             "message": "missing _meta: this server wants the stateless revision"}})
        continue
    if legacy and not initialized and method != "initialize":
        reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602,
             "message": "initialize first"}})
        continue
    if method == "initialize":
        initialized = True
        reply({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-06-18",
              "capabilities": {"tools": {}}, "serverInfo": {"name": "stub", "version": "1"}}})
        continue
    if method == "tools/list":
        res = {"tools": [{"name": "echo", "description": "echo the text back",
                          "inputSchema": {"type": "object",
                                          "properties": {"text": {"type": "string"}},
                                          "required": ["text"]}},
                         {"name": "boom", "description": "always fails",
                          "inputSchema": {"type": "object", "properties": {}}}]}
        if not legacy:
            res["resultType"] = "complete"
        reply({"jsonrpc": "2.0", "id": rid, "result": res})
        continue
    if method == "tools/call":
        if die_on_call:
            sys.stderr.write("boom: the tool call killed this server\n")
            sys.stderr.flush()
            sys.exit(4)
        if params.get("name") == "boom":
            res = {"content": [{"type": "text", "text": "boom failed"}], "isError": True}
            if not legacy:
                res["resultType"] = "complete"
            reply({"jsonrpc": "2.0", "id": rid, "result": res})
            continue
        if params.get("name") != "echo":
            reply({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "no such tool"}], "isError": True,
                **({} if legacy else {"resultType": "complete"})}})
            continue
        text = str((params.get("arguments") or {}).get("text", ""))
        res = {"content": [{"type": "text", "text": "echo: " + text}], "isError": False}
        if not legacy:
            res["resultType"] = "complete"
        reply({"jsonrpc": "2.0", "id": rid, "result": res})
        continue
    reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601,
         "message": "method not found"}})
''', encoding="utf-8")


def stage(name, servers=None):
    work = STAGE / name
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, work / "tinycmdr.py")
    (work / "config.json").write_text(
        json.dumps({"llm": {"model": "stub"}, "agent": {"mcp_servers": servers or {}}}),
        encoding="utf-8")
    spec = importlib.util.spec_from_file_location("tinycmdr_mcp_" + name,
                                                  work / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_mcp_" + name] = fb
    spec.loader.exec_module(fb)
    return fb


def stub_spec(*flags):
    return {"command": sys.executable, "args": [str(STUB)] + list(flags)}


ON = stage("on", servers={"stub": stub_spec()})
OFF = stage("off")
FAILURES, PASSES = [], []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"ok   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}: {detail}")


def test_the_modern_revision_discovers_and_calls():
    out = ON.tool_mcp({"action": "tools", "server": "stub"}, {})
    check("tools lists the server's tools", "echo" in out and "exposes 2 tool" in out, out)
    out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "echo",
                       "arguments": {"text": "hi"}}, {})
    check("call returns the tool's text", out.endswith("echo: hi"), out)
    check("...with the server and tool named", "stub/echo" in out, out)
    ent = ON._MCP_PROCS["stub"]
    check("the stateless revision was detected", ent["modern"] is True, ent["modern"])
    pid = ent["proc"].pid
    ON.tool_mcp({"action": "call", "server": "stub", "tool": "echo",
                 "arguments": {"text": "again"}}, {})
    check("the server process is reused between calls",
          ON._MCP_PROCS["stub"]["proc"].pid == pid, ON._MCP_PROCS["stub"]["proc"].pid)


def test_a_legacy_server_gets_the_handshake():
    ON.CONFIG["agent"]["mcp_servers"]["old"] = stub_spec("--legacy")
    try:
        out = ON.tool_mcp({"action": "tools", "server": "old"}, {})
        check("a pre-2026 server is discovered after the fallback",
              "echo" in out, out)
        ent = ON._MCP_PROCS["old"]
        check("...and is remembered as legacy", ent["modern"] is False, ent["modern"])
        out = ON.tool_mcp({"action": "call", "server": "old", "tool": "echo",
                           "arguments": {"text": "legacy"}}, {})
        check("a call on the legacy path answers", out.endswith("echo: legacy"), out)
    finally:
        ON.CONFIG["agent"]["mcp_servers"].pop("old", None)


def test_a_configured_command_is_screened_before_it_is_spawned():
    """The absolute tier, over agent.mcp_servers.

    The manifest loader for a dropped-in tool refuses a command that matches an
    absolute-tier pattern at load time, because that file arrives from outside the bot.
    config.json is the same kind of file - the agent's own write tools edit it - and its
    command reached subprocess.Popen with no tier at all (measured 2026-10-07). The
    refusal names the server and the pattern, and nothing is spawned.
    """
    # The command is blocked by the never tier AND the binary does not exist on any test
    # host, so the falsification run (pre-fix, where nothing screens it) cannot do damage:
    # Popen raises and the refusal is only about the pattern.
    ON.CONFIG["agent"]["mcp_servers"]["blocked"] = {
        "command": "mkfs.ext4", "args": ["/dev/sda1"]}
    try:
        out = ON.tool_mcp({"action": "call", "server": "blocked", "tool": "echo"}, {})
        check("an MCP command that matches the absolute tier is refused",
              out.startswith("ERROR") and "blocked" in out, out)
        check("...by the same rule the drop-in loader uses, and it says so",
              "safety pattern" in out and "in-band" in out, out)
        check("...and nothing was spawned for it", "blocked" not in ON._MCP_PROCS,
              sorted(ON._MCP_PROCS))
        out = ON.tool_mcp({"action": "list"}, {})
        check("the server list marks it REFUSED rather than offering it",
              "REFUSED" in out and "blocked" in out, out)
    finally:
        ON.CONFIG["agent"]["mcp_servers"].pop("blocked", None)


def test_the_errors_are_honest():
    out = ON.tool_mcp({"action": "tools", "server": "ghost"}, {})
    check("an unknown server is refused by name",
          out.startswith("ERROR") and "stub" in out, out)
    out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "nope"}, {})
    check("an unknown tool names the real ones",
          out.startswith("ERROR") and "echo" in out, out)
    out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "boom"}, {})
    check("a tool result with isError carries the ERROR prefix",
          out.startswith("ERROR: stub/boom") and "boom failed" in out, out)
    ON.CONFIG["agent"]["mcp_servers"]["quiet"] = stub_spec("--silent")
    ON.CONFIG["agent"]["mcp_timeout"] = 0.6
    try:
        out = ON.tool_mcp({"action": "tools", "server": "quiet"}, {})
        check("a server that never answers times out honestly",
              "no answer within" in out, out)
    finally:
        ON.CONFIG["agent"]["mcp_servers"].pop("quiet", None)
        ON.CONFIG["agent"]["mcp_timeout"] = 60
    ON.CONFIG["agent"]["mcp_servers"]["dead"] = {
        "command": sys.executable, "args": ["-c", "pass"]}
    try:
        out = ON.tool_mcp({"action": "tools", "server": "dead"}, {})
        check("a server that exits is reported, not retried forever",
              out.startswith("ERROR") and "exited" in out, out)
    finally:
        ON.CONFIG["agent"]["mcp_servers"].pop("dead", None)
    ON.CONFIG["agent"]["mcp_servers"]["nope"] = {"command": "definitely-not-a-binary-xyz"}
    try:
        out = ON.tool_mcp({"action": "tools", "server": "nope"}, {})
        check("a command that cannot start says so",
              out.startswith("ERROR") and "could not start" in out, out)
    finally:
        ON.CONFIG["agent"]["mcp_servers"].pop("nope", None)


def test_no_servers_means_no_tool():
    check("off: a box with no servers has no mcp tool", "mcp" not in OFF.CORE_TOOLS)
    out = OFF.tool_mcp({"action": "list"}, {})
    check("...and the tool itself says what to configure",
          out.startswith("ERROR") and "mcp_servers" in out, out)
    check("on: the same build with a server registers it", "mcp" in ON.CORE_TOOLS)
    check("...hidden from the default core", "mcp" not in ON._DEFAULT_CORE)
    check("...under the per-tool rent cap",
          len(json.dumps(ON.CORE_TOOLS["mcp"]["schema"])) <= 1200,
          len(json.dumps(ON.CORE_TOOLS["mcp"]["schema"])))


def _capture_log(module):
    """(seen, handler): the records a module logs while a block runs."""
    seen = []
    handler = logging.Handler()
    handler.emit = lambda r: seen.append(r.getMessage())
    module.log.addHandler(handler)
    return seen, handler


def _call(fn, *a, **k):
    """(result, error): run one door and turn a pre-fix raise into a graded FAIL."""
    try:
        return fn(*a, **k), ""
    except Exception as exc:                                     # noqa: BLE001
        return "", "%s: %s" % (type(exc).__name__, exc)


def test_a_wrong_servers_shape_answers_never_raises():
    saved = ON.CONFIG["agent"]["mcp_servers"]
    try:
        for bad in (["a", "b"], "stub", 3):
            ON.CONFIG["agent"]["mcp_servers"] = bad
            raised = ""
            try:
                out = ON.tool_mcp({"action": "list"}, {})
            except Exception as exc:                             # noqa: BLE001
                out, raised = "", "%s: %s" % (type(exc).__name__, exc)
            check("a %s mcp_servers answers an ERROR naming the map shape"
                  % type(bad).__name__,
                  not raised and out.startswith("ERROR") and "map" in out
                  and "command" in out, raised or out)
        ON.CONFIG["agent"]["mcp_servers"] = ["a", "b"]
        _name, _args, door = ON.AGENT._exec_tool(
            {"function": {"name": "mcp", "arguments": '{"action": "list"}'}},
            {"session_key": "mcp-shape"})
        check("...and the same answer comes out of the tool door",
              door.startswith("ERROR") and "map" in door, door)
        ON.CONFIG["agent"]["mcp_servers"] = {"bad": "oops"}
        out, raised = _call(ON.tool_mcp, {"action": "list"}, {})
        check("the list action survives a non-map entry",
              not raised and "bad" in out and "not a {command, args?} map" in out,
              raised or out)
        out = ON.tool_mcp({"action": "tools", "server": "bad"}, {})
        check("...and a named entry that is not a map says so",
              out.startswith("ERROR") and "not a map of" in out, out)
    finally:
        ON.CONFIG["agent"]["mcp_servers"] = saved


def test_a_bad_timeout_value_falls_back_with_one_warning():
    seen, handler = _capture_log(ON)
    ON.CONFIG["agent"]["mcp_timeout"] = "60s"
    try:
        out, raised = _call(ON.tool_mcp, {"action": "tools", "server": "stub"}, {})
    finally:
        ON.CONFIG["agent"]["mcp_timeout"] = 60
        ON.log.removeHandler(handler)
    warned = [m for m in seen if "mcp_timeout" in m]
    check("a non-numeric mcp_timeout falls back to the working default",
          not raised and "echo" in out, raised or out)
    check("...with one WARNING naming the key and the value",
          len(warned) == 1 and "60s" in warned[0], seen)


def test_a_zero_timeout_means_zero_not_sixty():
    ON.CONFIG["agent"]["mcp_servers"]["zero"] = stub_spec()
    ON.CONFIG["agent"]["mcp_timeout"] = 0
    try:
        t0 = time.monotonic()
        out = ON.tool_mcp({"action": "tools", "server": "zero"}, {})
        elapsed = time.monotonic() - t0
    finally:
        ON.CONFIG["agent"]["mcp_timeout"] = 60
        ON.CONFIG["agent"]["mcp_servers"].pop("zero", None)
    check("mcp_timeout 0 fails at once instead of hiding a 60",
          out.startswith("ERROR") and "within 0s" in out, out)
    check("...and it really is immediate", elapsed < 1.0, "%.3fs" % elapsed)


def test_a_failed_handshake_is_remembered():
    ON.CONFIG["agent"]["mcp_servers"]["quiet2"] = stub_spec("--silent")
    ON.CONFIG["agent"]["mcp_timeout"] = 0.3
    try:
        t0 = time.monotonic()
        first = ON.tool_mcp({"action": "tools", "server": "quiet2"}, {})
        pay = time.monotonic() - t0
        t1 = time.monotonic()
        second = ON.tool_mcp({"action": "tools", "server": "quiet2"}, {})
        again = time.monotonic() - t1
        ent = ON._MCP_PROCS["quiet2"]
        window = ent.get("fail_until", 0) - time.time()
        ent["fail_until"] = 0.0                     # pretend the window elapsed
        t2 = time.monotonic()
        ON.tool_mcp({"action": "tools", "server": "quiet2"}, {})
        retried = time.monotonic() - t2
    finally:
        ON.CONFIG["agent"]["mcp_timeout"] = 60
        ON.CONFIG["agent"]["mcp_servers"].pop("quiet2", None)
    check("a failed handshake is paid once, not on every call", again < 0.15,
          "%.3fs then %.3fs" % (pay, again))
    check("...and the cached answer is the same honest reason",
          second == first and "no answer within" in second, second)
    check("...the memory is a bounded window", 0 < window <= 60.5, window)
    check("...and past the window the call pays again", retried >= 0.25,
          "%.3fs" % retried)


def test_a_server_without_a_command_says_so():
    ON.CONFIG["agent"]["mcp_servers"]["nocmd"] = {"args": []}
    try:
        out = ON.tool_mcp({"action": "tools", "server": "nocmd"}, {})
    finally:
        ON.CONFIG["agent"]["mcp_servers"].pop("nocmd", None)
    check("a server configured without a command says exactly that",
          out.startswith("ERROR") and "WITHOUT a command" in out
          and "no such server" not in out, out)


def test_a_death_is_noticed_and_says_why():
    ON.CONFIG["agent"]["mcp_servers"]["crash"] = {
        "command": sys.executable,
        "args": ["-c", "import sys; sys.stderr.write('boom: bad module\\n'); "
                       "sys.exit(3)"]}
    ON.CONFIG["agent"]["mcp_timeout"] = 5
    try:
        t0 = time.monotonic()
        out = ON.tool_mcp({"action": "tools", "server": "crash"}, {})
        elapsed = time.monotonic() - t0
    finally:
        ON.CONFIG["agent"]["mcp_timeout"] = 60
        ON.CONFIG["agent"]["mcp_servers"].pop("crash", None)
    check("a server that dies is noticed inside the timeout, not after it",
          out.startswith("ERROR") and "exited" in out and elapsed < 2.5,
          "%s (%.2fs)" % (out, elapsed))
    check("...and its stderr is the reason it gives",
          "boom: bad module" in out, out)


def test_a_call_failure_carries_the_server_stderr():
    ON.CONFIG["agent"]["mcp_servers"]["dier"] = stub_spec("--die-on-call")
    ON.CONFIG["agent"]["mcp_timeout"] = 2
    try:
        listed = ON.tool_mcp({"action": "tools", "server": "dier"}, {})
        out = ON.tool_mcp({"action": "call", "server": "dier", "tool": "echo",
                           "arguments": {"text": "x"}}, {})
    finally:
        ON.CONFIG["agent"]["mcp_timeout"] = 60
        ON.CONFIG["agent"]["mcp_servers"].pop("dier", None)
    check("the call path works up to the crash", "echo" in listed, listed)
    check("a call that dies carries the server's stderr tail",
          out.startswith("ERROR") and "exited" in out
          and "boom: the tool call killed" in out, out)


def test_arguments_arriving_as_a_json_string():
    ON.CONFIG["agent"]["mcp_timeout"] = 0.8
    try:
        out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "echo",
                           "arguments": '{"text": "hi"}'}, {})
        bad = ON.tool_mcp({"action": "call", "server": "stub", "tool": "echo",
                           "arguments": '{"text": '}, {})
        notobj = ON.tool_mcp({"action": "call", "server": "stub", "tool": "echo",
                              "arguments": "[1, 2]"}, {})
    finally:
        ON.CONFIG["agent"]["mcp_timeout"] = 60
    check("a JSON-string arguments is parsed, not forwarded verbatim",
          out.endswith("echo: hi"), out)
    check("...and text that is not JSON answers a clear ERROR",
          bad.startswith("ERROR") and "does not parse" in bad, bad)
    check("...and a JSON value that is not an object says so",
          notobj.startswith("ERROR") and "must be a JSON object" in notobj, notobj)


def test_the_tool_name_is_matched_case_insensitively():
    out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "Echo",
                       "arguments": {"text": "Hi"}}, {})
    check("a differently-cased tool name is accepted", out.endswith("echo: Hi"), out)
    check("...and the call goes out under the server's spelling",
          "stub/echo ->" in out, out)
    out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "ECHO",
                       "arguments": {"text": "Yo"}}, {})
    check("...for any casing", out.endswith("echo: Yo"), out)
    out = ON.tool_mcp({"action": "call", "server": "stub", "tool": "ghost_tool"}, {})
    check("...and a genuinely absent tool still lists the real ones",
          out.startswith("ERROR") and "echo" in out, out)


def test_a_server_request_is_never_taken_for_a_reply():
    ON.CONFIG["agent"]["mcp_servers"]["srvreq"] = stub_spec("--srvreq")
    try:
        out = ON.tool_mcp({"action": "tools", "server": "srvreq"}, {})
        parked = ON._MCP_PROCS["srvreq"].get("parked") or {}
    finally:
        ON.CONFIG["agent"]["mcp_servers"].pop("srvreq", None)
    check("a server-initiated request with the in-flight id is not consumed as the reply",
          "echo" in out and "exposes 2 tool" in out, out)
    check("...and it is not parked as a reply either", parked == {}, parked)


def test_a_late_reply_is_parked_not_dropped():
    ON.CONFIG["agent"]["mcp_servers"]["strays"] = stub_spec("--strays")
    try:
        out = ON.tool_mcp({"action": "tools", "server": "strays"}, {})
        parked = ON._MCP_PROCS["strays"].get("parked") or {}
    finally:
        ON.CONFIG["agent"]["mcp_servers"].pop("strays", None)
    check("mismatched replies do not break the real answer", "echo" in out, out)
    check("...they are parked by id instead of dropped",
          parked.get(900011) is not None, sorted(parked))
    check("...bounded to the last few, oldest evicted",
          len(parked) == 8 and 900000 not in parked, sorted(parked))


def main():
    try:
        tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
        for fn in tests:
            fn()
    finally:
        for ent in list(ON._MCP_PROCS.values()):
            try:
                ent["proc"].kill()
            except Exception:
                pass
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
