#!/usr/bin/env python3
"""smoke-install - install from the BUILT artifact, then run it. The one loop no suite closes.

Every other installer check (tests/test_installer_unix.py, test_installer_windows.py,
test_installer_parity.py) installs from a tree the suite assembles itself with its own
fixtures and a fake venv. Nothing in the gate ever touches what build-package.py actually
produces - the zip and the tarball, the artifacts every other host downloads - so "the
package installs and runs" was a claim with no exit code behind it (the same gap
development.md §1 exists to kill). This is that check.

What it does, in order, against an install the caller has ALREADY made from the package:

  1. points the install at a model endpoint (a hermetic stub it starts itself, or --base-url)
  2. `config set` the two keys that decide where the model is, through the app's own verb -
     so the config writer is exercised, not bypassed with a JSON edit
  3. `doctor`   - must report "no problems found" (this is what makes the endpoint step real:
                  doctor fails when the model endpoint does not answer)
  4. `health`   - the one line a supervisor reads must print, naming this build, this lane,
                  this model and this endpoint. Exit code is 1 by design here: a --no-start
                  install is not running, and health says so rather than lying (see its
                  docstring) - so the LINE is graded, not the return code.
  5. `--once`   - drives one full turn through the installed tree and the endpoint, and the
                  stub grades that a real chat/completions request arrived.

Pointing at a real model instead of the stub (a LAN box, a fleet endpoint):

    python3 maintenance/smoke-install.py --install-dir DIR --base-url http://box:8081/v1 --model main

With --base-url there is nothing to grade the reply against - a real model will not say
"READY" - so only the exit code and a non-empty answer are required. In CI the URL comes
from a repository secret and never appears in a tracked file: a LAN address in this tree
would be refused by maintenance/leak-gate.py, which is the point.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STUB_REPLY = "READY"
STEP = re.compile(r"^tinycmdr(?:\.py)? v\d", re.M)


def check(name, cond, detail=""):
    """Same shape as the suites': one line, PASS/FAIL, the evidence on failure."""
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond and detail:
        for line in str(detail).strip().splitlines()[-8:]:
            print("       | " + line)
    return bool(cond)


class _StubHandler(BaseHTTPRequestHandler):
    """An OpenAI-shaped endpoint the installed tree can actually talk to.

    The same shape tests/test_prefix_stability.py stages, kept minimal: the two GETs the
    envelope probes (/v1/models, /props) and the one POST a turn makes. Every POST is
    recorded so the caller can grade that the request really left the process.
    """
    model = "main"
    calls = []

    def log_message(self, *a):          # a smoke run is not a request log
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
            self._send({"data": [{"id": type(self).model, "object": "model",
                                  "max_model_len": 32768}]})
        elif path.endswith("/props"):
            self._send({"default_generation_settings": {"n_ctx": 32768}})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).calls.append({"path": self.path, "model": body.get("model")})
        self._send({"id": "c1", "object": "chat.completion", "model": body.get("model"),
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": STUB_REPLY}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}})


def start_stub(model):
    """Return (base_url, server). Caller must shutdown()."""
    _StubHandler.model = model
    _StubHandler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d/v1" % port, server


def run(python, install_dir, args, timeout=180):
    # encoding/errors, never plain text=True: the app reconfigures its stdout to UTF-8, and
    # a bare text=True decodes with the LOCALE codec (cp1252 on Windows), so a byte like
    # 0x81 raised UnicodeDecodeError inside subprocess.run and the smoke died on a decoding
    # fault instead of grading the install (measured by the Windows CI job, 2026-10-03).
    return subprocess.run([python, str(Path(install_dir) / "tinycmdr.py"), *args],
                          cwd=install_dir, capture_output=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def set_config(python, install_dir, key, value):
    return run(python, install_dir, ["config", "set", key, value])


def find_python(install_dir):
    """The interpreter the install made for itself - the venv, where there is one."""
    for rel in ("venv/bin/python", "venv/Scripts/python.exe"):
        cand = Path(install_dir) / rel
        if cand.exists():
            return str(cand)
    return sys.executable


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install-dir", required=True,
                    help="a tree installed from the built package (its tinycmdr.py is run)")
    ap.add_argument("--python", default="",
                    help="interpreter to run the install with (default: its own venv)")
    # `or` not a plain get(): CI sets these from a repository secret/variable that may be
    # absent, which arrives as an EMPTY string rather than unset - and "" must mean "use
    # the default", not "no model"/"no endpoint".
    ap.add_argument("--base-url", default=os.environ.get("TINYCMDR_SMOKE_BASE_URL") or "",
                    help="model endpoint to use instead of the hermetic stub "
                         "(default: $TINYCMDR_SMOKE_BASE_URL, else a local stub)")
    ap.add_argument("--model", default=os.environ.get("TINYCMDR_SMOKE_MODEL") or "main")
    args = ap.parse_args()

    install_dir = Path(args.install_dir).resolve()
    python = args.python or find_python(install_dir)
    if not (install_dir / "tinycmdr.py").exists():
        print("smoke-install: no tinycmdr.py in %s - is that an install?" % install_dir,
              file=sys.stderr)
        return 2
    print("smoke-install: %s  (python %s)" % (install_dir, python))
    if args.base_url:
        print("smoke-install: endpoint %s model %s (real - stub assertions off)"
              % (args.base_url, args.model))

    server = None
    stub = not args.base_url
    if stub:
        base_url, server = start_stub(args.model)
    else:
        base_url = args.base_url

    failures = 0
    try:
        # 1+2. Point the install at the endpoint through its own config verb.
        for key, value in (("llm.base_url", base_url), ("llm.model", args.model),
                           ("llm.stream", "false")):
            got = set_config(python, install_dir, key, value)
            failures += not check("config set %s %s" % (key, value), got.returncode == 0,
                                  (got.stdout or "") + (got.stderr or ""))

        # 3. doctor: the INSTALL is coherent and the endpoint answers. Deliberately NOT
        # "no problems found": these installs are CLI-only, and doctor names the missing
        # chat token as a problem under the Windows installer's placeholder config. What
        # this smoke is for is the install and the model link.
        got = run(python, install_dir, ["doctor"])
        out = (got.stdout or "") + (got.stderr or "")
        failures += not check("doctor: the endpoint was reached (not NO ANSWER)",
                              "NO ANSWER" not in out, out)
        failures += not check("doctor: the install folder is writable",
                              "folder    : writable" in out, out)
        failures += not check("doctor: the HTTP layer is present",
                              "dep requests: ok" in out, out)

        # 4. health: the one line a supervisor reads, naming this build.
        got = run(python, install_dir, ["health"])
        out = (got.stdout or "") + (got.stderr or "")
        failures += not check("health: prints the version/lane/model/endpoint line",
                              bool(STEP.search(got.stdout or "")) and "model " + args.model in (got.stdout or "")
                              and base_url in (got.stdout or ""), out)
        failures += not check("health: reports no DOWN lane",
                              " is DOWN" not in out, out)

        # 5. --once: one whole turn, through the installed tree and the endpoint.
        got = run(python, install_dir, ["--once", "reply with the single word: " + STUB_REPLY],
                  timeout=300)
        out = (got.stdout or "") + (got.stderr or "")
        # A turn with no endpoint exits 0 and prints an honest "infrastructure failure"
        # card (measured 2026-10-02 against a refused port), so the return code alone
        # grades nothing here - the card must be absent.
        failures += not check("--once exited 0", got.returncode == 0, out)
        failures += not check("--once actually ran (no infrastructure-failure card)",
                              "infrastructure failure" not in out and "did not run" not in out,
                              out)
        if stub:
            failures += not check("--once reached the endpoint",
                                  len(_StubHandler.calls) >= 1, out)
            failures += not check("--once printed the model's reply",
                                  STUB_REPLY in (got.stdout or ""), out)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()

    if failures:
        print("smoke-install: %d check(s) FAILED" % failures)
        return 1
    print("smoke-install: the package installs and runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
