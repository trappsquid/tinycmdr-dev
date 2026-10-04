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
import os
import shutil
import sys
import tempfile
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
initialized = False

def send(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()

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
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32022,
             "message": "missing _meta: this server wants the stateless revision"}})
        continue
    if legacy and not initialized and method != "initialize":
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602,
             "message": "initialize first"}})
        continue
    if method == "initialize":
        initialized = True
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-06-18",
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
        send({"jsonrpc": "2.0", "id": rid, "result": res})
        continue
    if method == "tools/call":
        if params.get("name") == "boom":
            res = {"content": [{"type": "text", "text": "boom failed"}], "isError": True}
            if not legacy:
                res["resultType"] = "complete"
            send({"jsonrpc": "2.0", "id": rid, "result": res})
            continue
        if params.get("name") != "echo":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "no such tool"}], "isError": True,
                **({} if legacy else {"resultType": "complete"})}})
            continue
        text = str((params.get("arguments") or {}).get("text", ""))
        res = {"content": [{"type": "text", "text": "echo: " + text}], "isError": False}
        if not legacy:
            res["resultType"] = "complete"
        send({"jsonrpc": "2.0", "id": rid, "result": res})
        continue
    send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601,
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
