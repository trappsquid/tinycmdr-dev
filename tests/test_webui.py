"""The page lane's contract: a token always, no server without one, files in and out.

This is the revived web UI's suite, written against the lane as it EXISTS now (the
original suite died with the old lane). Every check below is either a behaviour the
lane promises or an incident the old one paid for:

    * no token -> the start path MINTS one (into .env) rather than serving ungated; the
      old lane served loopback with no auth at all: CSRF against shell access, BUGREPORT
      S7. A host that upgrades into the page gets a token and a link, not homework.
      web.enabled false -> no server either.
    * the token is compared in constant time, and it never appears in the log.
    * Host/Origin rules: a cross-origin request is refused, a foreign Host is refused.
    * the body is capped BEFORE it is read (S8); uploads have their own cap.
    * uploads land under ./uploads with a sanitised name; downloads serve ONLY a file
      the agent offered (a run line of kind 'file'), by run id + uid.
    * /api/health tells the truth and needs no token; the page itself needs none.
    * a second start on a busy port announces the running page instead of failing.

Offline and self-contained: loopback only, port 0, no model.

    python tests/test_webui.py
"""
import contextlib
import http.client
import importlib.util
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "tinycmdr.py"

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-webui"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
             STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_webui", STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_webui"] = fb
spec.loader.exec_module(fb)

FAILS = []


def check(cond, what, detail=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}: {detail}")
    else:
        print(f"ok   {what}")


class _Collect(logging.Handler):
    """Every record the module logs while it runs, for the "token never logged" check."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:
            pass


def main():
    token = "tok-webui-suite-0123456789"
    base = None
    # A suite never opens a browser: on a macOS CI runner webbrowser.open really does
    # launch Safari (the job's cleanup kills it), which is a side effect a test must not
    # have and a source of platform-only slowness.
    fb._browser_possible = lambda: False

    # ---- no token: the server MINTS one, and never serves ungated ---------------
    # An install that predates the page - or a first start on a fresh host - has no
    # token in .env. The rule is "no token, no server", not "no token, no page": the
    # start path makes the token it requires (into .env, never config.json) and then
    # serves gated, so an upgrade introduces the page instead of stranding it behind a
    # command the operator has to be told about.
    fb.CONFIG["web"] = {"enabled": True, "host": "127.0.0.1", "port": 0}
    os.environ.pop("TINYCMDR_WEB_TOKEN", None)
    if fb.ENV_FILE.exists():
        fb.ENV_FILE.unlink()
    check(fb.run_webui() is None,
          "run_webui itself still refuses to serve without a token")
    check("token" not in (fb.CONFIG.get("web") or {}),
          "and the low-level start writes no token into config.json")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        srv0 = fb.start_web_surface(open_browser=False)
    out0 = buf.getvalue()
    minted = fb._web_token()
    check(srv0 is not None,
          "start_web_surface mints the token it needs instead of giving up")
    check(bool(minted) and len(minted) >= 32,
          "the minted token is long", len(minted or ""))
    check(fb.ENV_FILE.exists()
          and ("TINYCMDR_WEB_TOKEN=%s" % minted) in fb.ENV_FILE.read_text(encoding="utf-8"),
          "it lands in .env (the one file the agent cannot read into a prompt)")
    check((fb.ENV_FILE.stat().st_mode & 0o077) == 0,
          "not group- or world-readable", oct(fb.ENV_FILE.stat().st_mode & 0o777))
    check("minted TINYCMDR_WEB_TOKEN" in out0, "and the start says so", out0[:80])
    check("#token=" + minted in out0, "the link it prints carries it")
    port0 = srv0.server_address[1]

    def probe0(path, headers=None):
        """(status, seconds) against the freshly minted server - never raises, so a slow
        platform reports as a fact instead of killing the suite with a traceback."""
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (port0, path),
                                   headers=headers or {})
        t0 = time.time()
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, time.time() - t0
        except urllib.error.HTTPError as e:
            return e.code, time.time() - t0
        except Exception as e:                                        # noqa: BLE001
            return type(e).__name__, time.time() - t0

    try:
        code0, dt0 = probe0("/api/commands")
        check(code0 == 401, "the server it started is gated: no header -> 401",
              (code0, round(dt0, 2)))
        code1, dt1 = probe0("/api/commands", {"X-Tinycmdr-Token": minted})
        check(code1 == 200, "the minted token authenticates", (code1, round(dt1, 2)))
    finally:
        srv0.shutdown()
        srv0.server_close()

    fb.CONFIG["web"] = {"enabled": False, "host": "127.0.0.1", "port": 0,
                        "token": token}
    check(fb.run_webui() is None, "web.enabled false: no server")
    check(fb.start_web_surface(open_browser=False) is None,
          "start_web_surface honours the switch")

    # ---- the real server ----------------------------------------------------
    fb.CONFIG["web"] = {"enabled": True, "host": "127.0.0.1", "port": 0,
                        "token": token}
    sink = _Collect()
    fb.log.addHandler(sink)
    srv = fb.run_webui()
    fb.log.removeHandler(sink)
    check(srv is not None, "with a token the server starts")
    port = srv.server_address[1]
    base = "http://127.0.0.1:%d" % port
    check(not any(token in m for m in sink.lines),
          "the token never appears in a log line", sink.lines)

    def req(method, path, headers=None, body=None, timeout=20, limit=4000):
        r = urllib.request.Request(base + path, method=method, data=body,
                                   headers=headers or {})
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.status, resp.read(limit), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(400), dict(e.headers)
        except Exception as e:                                   # noqa: BLE001
            return -1, str(e).encode(), {}

    TOK = {"X-Tinycmdr-Token": token}

    # ---- the page and its static bits need no token -------------------------
    code, body, _ = req("GET", "/", limit=400000)
    check(code == 200 and b"<html" in body.lower(), "GET / serves the page", code)
    check(b"location.hash" in body,
          "the page takes its token from the URL fragment first", body[:60])
    check(req("GET", "/icon.png")[0] == 200, "the icon is served")
    check(req("GET", "/manifest.webmanifest")[0] == 200, "the manifest is served")

    # ---- health tells the truth and needs no token --------------------------
    code, body, _ = req("GET", "/api/health")
    try:
        health = json.loads(body)
    except Exception:
        health = {}
    check(code == 200 and health.get("version"),
          "health answers without a token and names the version", code)
    check("lanes" in health and "config_changed" in health,
          "health carries the lane state a monitor needs", sorted(health))

    # ---- auth ---------------------------------------------------------------
    check(req("GET", "/api/tasks")[0] == 401, "a data route without a token: 401")
    check(req("GET", "/api/tasks", {"X-Tinycmdr-Token": "wrong"})[0] == 401,
          "a wrong token: 401")
    check(req("GET", "/api/tasks", TOK)[0] == 200, "the right token: 200")
    check(req("POST", "/api/run", body=b'{"message":"hi"}')[0] == 401,
          "a POST without a token: 401 (it never reaches the agent)")

    # ---- Host and Origin ----------------------------------------------------
    # timeout well above the settle cap: an unknown Host waits for the background
    # resolver once (up to 3s) before being refused, and a 5s client deadline sat right
    # on that boundary - measured: this check timed the suite out on a macOS runner.
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.putrequest("GET", "/api/tasks", skip_host=True)
    conn.putheader("Host", "evil.example")
    conn.endheaders()
    r = conn.getresponse()
    conn.close()
    check(r.status == 403, "a foreign Host: 403", r.status)
    check(req("GET", "/api/tasks", {**TOK, "Origin": "http://evil.example"})[0] == 403,
          "a cross-origin request: 403")
    code, body, _ = req("GET", "/api/tasks", {**TOK, "Origin": base})
    check(code == 200, "a same-origin request passes", code)

    # ---- the resolver is NOT on the request path ----------------------------
    # Measured on a macOS CI runner: getfqdn/gethostbyname_ex took >5s, and because the
    # Host check ran them per request, the FIRST request to a freshly started server
    # timed out (test_page_upgrade and this suite both died that way). The fast names
    # (loopback, this box's hostname, web.host) answer immediately; the resolver's
    # answers are merged in from a background thread.
    real_socket = fb.socket
    calls = {"n": 0}

    class _SlowSock:
        def __getattr__(self, name):
            return getattr(real_socket, name)

        def getfqdn(self, *a):
            calls["n"] += 1
            time.sleep(30)
            return "slow.example"

        def gethostbyname_ex(self, *a):
            calls["n"] += 1
            time.sleep(30)
            return ("slow", [], ["192.0.2.7"])

    fb._WEB_HOSTS_CACHE = None          # force a fresh warm under the slow resolver
    fb._WEB_HOSTS_WARM = None
    fb.socket = _SlowSock()
    try:
        t0 = time.time()
        code, _, _ = req("GET", "/api/tasks", TOK, timeout=3)
        dt = time.time() - t0
        check(code == 200 and dt < 2.0,
              f"a request never waits on the resolver ({code}, {round(dt, 2)}s)")
        check(calls["n"] >= 1, "the resolver runs in the background", calls)
    finally:
        fb.socket = real_socket

    # ---- the body cap is checked BEFORE the read ----------------------------
    big = b"x" * (fb.WEB_BODY_MAX + 64)
    code, _, _ = req("POST", "/api/run", {**TOK, "Content-Type": "application/json"},
                     big)
    check(code == 400, "an oversized body is refused without reading it", code)

    # ---- uploads ------------------------------------------------------------
    small = b"suite upload payload"
    code, body, _ = req("POST", "/api/upload?name=" + urllib.parse.quote("../../evil name.txt"),
                        TOK, small)
    saved = json.loads(body) if code == 200 else {}
    check(code == 200, "an upload with a token lands", code)
    check(saved.get("path", "").startswith("uploads/")
          and ".." not in saved.get("path", ".."),
          "the stored name cannot escape uploads/", saved.get("path"))
    check((STAGE / saved.get("path", "uploads/missing")).read_bytes() == small,
          "the bytes on disk are the bytes sent")
    check(req("POST", "/api/upload?name=x.bin", None, small)[0] == 401,
          "an upload without a token: 401")
    old_max = fb.WEB_UPLOAD_MAX
    fb.WEB_UPLOAD_MAX = 4
    check(req("POST", "/api/upload?name=big.bin", TOK, b"0123456789")[0] == 413,
          "an upload over the cap: 413")
    fb.WEB_UPLOAD_MAX = old_max

    # ---- downloads serve ONLY what the agent offered ------------------------
    uploaded = STAGE / saved["path"]
    run = fb._web_new_run("web")
    run.add("file", str(uploaded))
    uid = run.lines[-1]["uid"]
    enc = urllib.parse.quote(uid, safe="")
    code, body, hdr = req("GET", "/api/download?run=%s&uid=%s" % (run.id, enc), TOK)
    check(code == 200 and body == small, "an offered file downloads", code)
    check("attachment" in (hdr.get("Content-Disposition") or ""),
          "it downloads as an attachment", hdr.get("Content-Disposition"))
    check(req("GET", "/api/download?run=%s&uid=%s" % (run.id, enc), None)[0] == 401,
          "a download without a token: 401")
    check(req("GET", "/api/download?run=%s&uid=%s" % (run.id,
                                                      urllib.parse.quote("zz#9")),
              TOK)[0] == 404,
          "a uid that is not an offered file: 404")
    run2 = fb._web_new_run("web")
    run2.add("you", "no file here")
    check(req("GET", "/api/download?run=%s&uid=%s" % (run2.id,
                                                      urllib.parse.quote(run2.lines[-1]["uid"])),
              TOK)[0] == 404,
          "a non-file line is not downloadable")

    # ---- attach -------------------------------------------------------------
    dest = fb.WebDestination(run2)
    msg = dest.attach(str(uploaded))
    check("attached to the page" in msg and run2.lines[-1]["kind"] == "file",
          "attach() offers the file as a line", msg[:60])
    check(dest.attach("/nope/missing.bin").startswith("NOT SENT"),
          "attach() refuses what is not a file")

    # ---- a second start announces instead of fighting -----------------------
    fb.CONFIG["web"]["port"] = port
    buf = io.StringIO()
    t0 = time.time()
    with contextlib.redirect_stdout(buf):
        again = fb.start_web_surface(open_browser=False)
    dt = time.time() - t0
    out = buf.getvalue()
    check(again is None and "already serving" in out and dt < 1.0,
          "a second start announces the running page (no bind retry)", round(dt, 2))
    check("#token=" + token in out, "the announcement carries the tokenized link")
    fb.CONFIG["web"]["port"] = 0

    # ---- the web verb -------------------------------------------------------
    fb._browser_possible = lambda: False
    buf = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = fb._verb_web()
    check(rc == 0 and "#token=" + token in buf.getvalue(),
          "the web verb prints the tokenized link", buf.getvalue()[:60])
    saved_tok = fb.CONFIG["web"].pop("token", None)
    os.environ.pop("TINYCMDR_WEB_TOKEN", None)
    if fb.ENV_FILE.exists():
        fb.ENV_FILE.unlink()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = fb._verb_web()
    check(rc == 0 and "minted TINYCMDR_WEB_TOKEN" in buf.getvalue(),
          "the web verb mints a token when the host has none", buf.getvalue()[:80])
    check("#token=" + (fb._web_token() or "\u0000") in buf.getvalue(),
          "and prints the link built from it")
    fb.CONFIG["web"]["token"] = saved_tok

    # ---- LAN announcement order (no bind: the announce path only) -----------
    fb.CONFIG["web"] = {"enabled": True, "host": "0.0.0.0", "port": 8790,
                        "token": token}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fb._announce_web(8790, open_browser=False)
    out = buf.getvalue()
    first = out.strip().splitlines()[0] if out.strip() else ""
    lan = fb._web_lan_ip()
    if lan:
        check(("tinycmdr page: http://%s:8790/" % lan) in first
              and "127.0.0.1" not in first,
              "a LAN bind leads with the box's LAN address", first)
    else:
        check("127.0.0.1" in first,
              "no LAN address known: loopback leads", first)
    check("cleartext" in out, "and says the token travels in cleartext there")

    srv.shutdown()
    srv.server_close()

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all web UI checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
