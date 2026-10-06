"""The model-setup conversation: a bearer probe, and `model add` with no URL.

The defect these lock down: the probe that every model door used sent NO Authorization
header, so a hosted provider's 401 to GET /v1/models read as "not reachable", the model
list it needed never came back, and the key was asked last and written to `llm.api_key`
in config.json - a file the agent reads into a prompt. Now the key comes first, rides the
probe as a bearer, and lands in .env; a 401 is named as a KEY refusal.

    python tests/test_model_setup.py
"""
import contextlib
import http.server
import importlib.util
import io
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "tinycmdr.py"
PASSES, FAILS = [], []


def check(cond, what, extra=""):
    if cond:
        print(f"ok   {what}")
        PASSES.append(what)
    else:
        print(f"FAIL {what}\n     {extra}")
        FAILS.append(what)


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def serve(key="sk-good", ids=("cloud-a", "cloud-b"), status=200):
    """A provider-shaped /v1/models: bearer required, 401 otherwise."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.rstrip("/").endswith("/models"):
                self.send_response(404)
                self.end_headers()
                return
            if self.headers.get("Authorization") != "Bearer " + key:
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps({"data": [{"id": i} for i in ids]}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1], srv


def stage(dirpath):
    dirpath.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, dirpath / "tinycmdr.py")
    (dirpath / "config.json").write_text(json.dumps({
        "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "main"},
    }), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("ms_" + dirpath.name,
                                                  dirpath / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    work = Path(tempfile.mkdtemp(prefix="fbmodel-"))
    try:
        mod = stage(work / "m")
        port, srv = serve()
        base = "http://127.0.0.1:%d/v1" % port

        # ---- probe_endpoint: the bearer, and what a 401 MEANS ------------------------
        r = mod.probe_endpoint(base)
        check(not r["ok"] and r["status"] == 401,
              "a hosted endpoint without the key answers 401, not a dead host", r)
        r = mod.probe_endpoint(base, "sk-bad")
        check(not r["ok"] and r["status"] == 401,
              "...and a wrong key is still a 401", r)
        r = mod.probe_endpoint(base, "sk-good")
        check(r["ok"] and r["ids"] == ["cloud-a", "cloud-b"],
              "...and the right key carries the provider's model list", r)
        check(mod._probe_model_ids(base) is None
              and mod._probe_model_ids(base, "sk-good") == ["cloud-a", "cloud-b"],
              "_probe_model_ids keeps its None-means-no-answer contract", None)
        check("refused that key" in mod.probe_sentence(base, {"ok": False, "status": 401,
                                                              "error": "HTTP 401", "ids": []}),
              "a 401 is described as a key refusal", None)
        dead = mod.probe_endpoint("http://127.0.0.1:9/v1", "sk-good")
        check(not dead["ok"] and dead["status"] is None and dead["error"],
              "a dead host has no status and a reason", dead)

        # ---- the interactive `model add`: cloud, link first, then the key, then the list -
        mod2 = stage(work / "add")
        mod2._is_local_url = lambda url: False
        # kind, url, key, number, alias, then the cloud-failover consent (the link is
        # asked BEFORE the key: a key belongs to an endpoint - asked in this order,
        # 2026-10-04)
        answers = ["cloud", base, "sk-good", "2", "team-a", "y"]
        old_in = sys.stdin
        sys.stdin = FakeTTY("\n".join(answers) + "\n")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = mod2._verb_model_add_interactive({})
        finally:
            sys.stdin = old_in
        out = buf.getvalue()
        cfg = json.loads((work / "add" / "config.json").read_text(encoding="utf-8"))
        env = (work / "add" / ".env").read_text(encoding="utf-8")
        fbs = cfg["llm"].get("fallbacks") or []
        check(rc == 0 and len(fbs) == 1 and fbs[0]["base_url"] == base,
              "`model add` with no URL asks and writes a fallback", (rc, fbs))
        check(fbs[0].get("model") == "cloud-b" and fbs[0].get("alias") == "team-a",
              "...the NUMBER resolves to the advertised id, and the alias is kept", fbs)
        check(fbs[0].get("api_key_env") == "TINYCMDR_ENDPOINT1_API_KEY"
              and "TINYCMDR_ENDPOINT1_API_KEY=sk-good" in env,
              "...and the key goes to .env under the entry's api_key_env", env[-200:])
        check("sk-good" not in json.dumps(cfg), "the key is NEVER in config.json",
              json.dumps(cfg.get("llm")))
        check("adding an off-LAN endpoint asks about automatic failover, and y turns it on",
              cfg["llm"].get("allow_cloud_fallback") is True, cfg.get("llm"))
        check("reachable" in out and "cloud-a" in out,
              "it says the endpoint is reachable and lists what it advertised", out[-500:])

        # ---- the interactive `model add --primary`: key to TINYCMDR_LLM_API_KEY ------
        mod3 = stage(work / "primary")
        mod3._is_local_url = lambda url: False
        mod3._detect_window = lambda url, headers=None: 0
        answers = ["cloud", base, "sk-good", "1"]  # kind, url, key, model number
        old_in = sys.stdin
        sys.stdin = FakeTTY("\n".join(answers) + "\n")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = mod3._verb_model_add_interactive({"primary": True})
        finally:
            sys.stdin = old_in
        cfg = json.loads((work / "primary" / "config.json").read_text(encoding="utf-8"))
        env = (work / "primary" / ".env").read_text(encoding="utf-8")
        check(rc == 0 and cfg["llm"]["base_url"] == base
              and cfg["llm"]["model"] == "cloud-a",
              "--primary sets llm.base_url and llm.model", (rc, cfg.get("llm")))
        check("TINYCMDR_LLM_API_KEY=sk-good" in env
              and "api_key" not in cfg["llm"],
              "...and its key is .env's TINYCMDR_LLM_API_KEY, not config.json's api_key",
              (env[-160:], cfg["llm"]))

        # ---- helpers ----------------------------------------------------------------
        check(mod._next_endpoint_env([{"api_key_env": "TINYCMDR_ENDPOINT1_API_KEY"}])
              == "TINYCMDR_ENDPOINT2_API_KEY",
              "_next_endpoint_env skips a name already used", None)
        (work / "primary" / ".env").write_text("TINYCMDR_LLM_API_KEY=sk-file\n",
                                               encoding="utf-8")
        os.environ.pop("TINYCMDR_LLM_API_KEY", None)
        check(mod3._endpoint_key("TINYCMDR_LLM_API_KEY") == "sk-file",
              "_endpoint_key reads .env when the process did not inherit the value", None)

        srv.shutdown()
        srv.server_close()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
