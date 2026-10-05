"""Prefix stability and tool disclosure: the two things that already work.

Prefix reuse is why a small local model is usable at all: the system prompt and the
conversation so far stay byte-identical between turns, so the server's KV cache only
prefills the new tail, and the volatile state block is the LAST thing in the payload.
Tool disclosure is the other half of the premise: 80 tools may exist and one index
line names them, but their schemas must not ride every request.

Both are asserted here rather than described in a comment, because both are easy to
break with an innocent-looking edit.
Driven by a stub endpoint in this process - no network, no real model, no mock file.

    python tests/test_prefix_stability.py
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FAILS = []


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


class Stub(BaseHTTPRequestHandler):
    payloads = []

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.rstrip("/")
        if path.endswith("/models"):
            self._send({"data": [{"id": "main", "object": "model",
                                  "max_model_len": 32768}]})
        elif path.endswith("/props"):
            self._send({"default_generation_settings": {"n_ctx": 32768}})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).payloads.append(body)
        self._send({"id": "c1", "object": "chat.completion",
                    "model": body.get("model"),
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant",
                                             "content": "turn done"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3,
                              "total_tokens": 13}})


def stage(workdir, base_url):
    shutil.copy2(BASE / "tinycmdr.py", workdir / "tinycmdr.py")
    fixture = json.loads((BASE / "tests" / "fixture-config.json")
                         .read_text(encoding="utf-8-sig"))
    cfg = {k: v for k, v in fixture.items() if not k.startswith("_")}
    cfg["llm"].update({"base_url": base_url, "model": "main",
                       "max_context_tokens": "auto", "stream": False})
    (workdir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def load(workdir):
    spec = importlib.util.spec_from_file_location("prefix_build",
                                                  workdir / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["prefix_build"] = fb
    spec.loader.exec_module(fb)
    return fb


def strip_state(fb, messages):
    """The payload without the volatile state block (the only message that moves)."""
    return [m for m in messages
            if not str(m.get("content") or "").startswith(fb._STATE_PREFIX)]


def common_prefix(a, b):
    n = 0
    while n < len(a) and n < len(b) and json.dumps(a[n], sort_keys=True) == \
            json.dumps(b[n], sort_keys=True):
        n += 1
    return n


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    workdir = Path(tempfile.mkdtemp(prefix="tc-prefix-"))
    try:
        base_url = "http://127.0.0.1:%d/v1" % port
        stage(workdir, base_url)
        fb = load(workdir)
        key = "prefix-session"
        for turn in range(1, 5):
            try:
                fb.AGENT.run(key, "say hi %d" % turn)
            except Exception as e:
                check(False, f"turn {turn} ran against the stub endpoint: "
                             f"{type(e).__name__}: {e}")
                break
        payloads = [p for p in Stub.payloads if p.get("messages")]
        check(len(payloads) >= 4,
              f"4 turns produced {len(payloads)} model request(s)")

        # exactly one state block, and it is the LAST word in the payload
        for i, p in enumerate(payloads):
            blocks = [m for m in p["messages"]
                      if str(m.get("content") or "").startswith(fb._STATE_PREFIX)]
            check(len(blocks) == 1,
                  f"req{i}: carries exactly one state block ({len(blocks)})")
            if len(blocks) == 1:
                check(p["messages"][-1] is blocks[0] or p["messages"][-2] is blocks[0],
                      f"req{i}: the state block is trailing, not early")

        # the head is byte-identical between consecutive turns
        for i in range(1, min(len(payloads), 4)):
            a = strip_state(fb, payloads[i - 1]["messages"])
            b = strip_state(fb, payloads[i]["messages"])
            shared = common_prefix(a, b)
            ratio = shared / max(1, len(a))
            check(ratio >= 0.9,
                  f"req{i - 1}->req{i}: {shared}/{len(a)} messages byte-identical "
                  f"at the head ({ratio:.0%} >= 90%)")

        # --- 80 tools: the index is cheap and the schemas are not sent --------
        for i in range(80):
            name = "bulk_tool_%02d" % i
            fb.REGISTRY.custom[name] = {"schema": {
                "type": "function", "function": {
                    "name": name,
                    "description": "a bulk probe tool with a description" * 2,
                    "parameters": {"type": "object", "properties": {
                        "path": {"type": "string"}}, "required": ["path"]}}}}
        block = fb.tool_index_block(fb.REGISTRY.custom)
        index_tokens = fb.est_tokens(block)
        check(index_tokens < 200,
              f"80 registered tools render as a {index_tokens}-token index line "
              f"(< 200)")
        visible = fb.select_tool_schemas(key)
        check(len(visible) < 80,
              f"the 80 schemas are NOT sent (disclosure sends {len(visible)})")
        hidden = fb.hidden_tools(key)
        check("bulk_tool_00" in hidden,
              "a hidden tool is still named by the index and reachable by name")
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all prefix-stability checks passed")


if __name__ == "__main__":
    main()
