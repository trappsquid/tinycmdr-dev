"""The page's replies name the product, not the interpreter, and refuse to be framed.

Two findings, one server:

  * A-2026-10-12-04 - BaseHTTPRequestHandler's stock Server header is
    "BaseHTTP/0.6 Python/3.12.10": the stdlib and this box's exact interpreter patch
    level, handed to anything that reads a banner. The fix overrides version_string()
    so every reply says "tinycmdr" and the fingerprint is gone.
  * A-2026-10-05-41 - the page is the shell-execution door and shipped with no browser
    posture at all: no CSP (so it could be framed inside another origin and its base
    retargeted), no X-Frame-Options, no nosniff, no Referrer-Policy. The four now go
    out from end_headers(), the one point every reply passes through - so the download
    route keeps streaming (nothing buffers it) and a route added later cannot forget
    them.

Offline and self-contained: loopback only, port 0, no model, no browser.

    python tests/test_fix_webheaders.py
    TINYCMDR_SRC=../pre-fix/tinycmdr.py python tests/test_fix_webheaders.py   # must go red
"""
import http.client
import importlib.util
import os
import shutil
import socket
import sys
import tempfile
import urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
# TINYCMDR_SRC points this at a reverted copy, so a fix can be watched going red
# (tests/run_all.py clears it for a normal run).
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

# A fresh home AND a fresh BASE_DIR: the staged build's BASE_DIR is its own file's
# parent, so the temp dir below becomes the install root and nothing - the .env it
# reads a token from, the lane record it writes - lands in the operator's checkout.
STAGE = Path(tempfile.mkdtemp(prefix="fbfixwebheaders-"))
HOME = STAGE / "home"
HOME.mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(HOME)
os.environ["USERPROFILE"] = str(HOME)
os.environ.pop("TINYCMDR_WEB_TOKEN", None)
# A suite never opens a browser tab, and the in-process server below must not try.
os.environ.setdefault("TINYCMDR_NO_BROWSER", "1")

shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
             STAGE / "config.json")
# The stage starts bare, so the page's own stylesheet - a host asset, not a string in
# tinycmdr.py - is copied in: without it /page.css is a 404 and the asset check below
# would grade the 404 instead.
(STAGE / "assets").mkdir(exist_ok=True)
shutil.copy2(BASE / "assets" / "webui.css", STAGE / "assets" / "webui.css")
spec = importlib.util.spec_from_file_location("tinycmdr_fix_webheaders",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_fix_webheaders"] = fb
spec.loader.exec_module(fb)

PASSES = []
FAILS = []

# What the four headers must say. The CSP is graded by directive: the page's script and
# style are inline in its own HTML, so the shipped policy relaxes script-src/style-src
# with 'unsafe-inline' - the three directives the finding names verbatim are what a
# fingerprint-free, unframeable, unretargetable page is graded on.
REQUIRED = {
    "content-security-policy": ("default-src 'self'", "frame-ancestors 'none'",
                                "base-uri 'none'"),
    "x-frame-options": ("DENY",),
    "x-content-type-options": ("nosniff",),
    "referrer-policy": ("no-referrer",),
}


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print("ok   %s" % name)
    else:
        FAILS.append(name)
        print("FAIL %s: %s" % (name, detail))


def headers_of(pairs):
    """Received header pairs -> {lowercase name: value}, one value per name (every
    reply here sends each of these once). Accepts str pairs (http.client) and bytes
    pairs (a header block read off a raw socket) alike."""
    out = {}
    for name, value in pairs:
        if isinstance(name, (bytes, bytearray)):
            name = bytes(name).decode("latin-1")
            value = bytes(value).decode("latin-1")
        out.setdefault(str(name).lower(), str(value))
    return out


def security_missing(hdrs):
    """'' when the four are present and say what they must, else why not."""
    for name, needles in REQUIRED.items():
        value = hdrs.get(name) or ""
        if not value:
            return "%s is missing" % name
        for needle in needles:
            if needle not in value:
                return "%s=%r does not carry %r" % (name, value, needle)
    return ""


def fingerprint_of(hdrs):
    """'' when no header names the interpreter, else the value that did."""
    for name, value in hdrs.items():
        if "Python/" in value or "BaseHTTP" in value:
            return "%s: %s" % (name, value)
    return ""


def parse_head(raw):
    """A raw response's header block -> (status line, {header: value})."""
    head, _, _ = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    hdrs = headers_of([tuple(line.split(b": ", 1)) for line in lines[1:]
                       if b": " in line])
    return lines[0], hdrs


def main():
    token = "tok-webheaders-0123456789"
    fb._browser_possible = lambda: False
    fb.CONFIG["web"] = {"enabled": True, "host": "127.0.0.1", "port": 0,
                        "token": token}
    srv = fb.run_webui()
    check("the web server starts on the staged build", srv is not None)
    if srv is None:
        return 1
    port = srv.server_address[1]

    def get(path, hdrs=None, method="GET"):
        """(status, {header: value}, body) over a fresh connection."""
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            conn.request(method, path, headers=hdrs or {})
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, headers_of(resp.getheaders()), body
        finally:
            conn.close()

    try:
        # ---- the fingerprint is gone from every reply ---------------------------
        for what, path in (("the page", "/"), ("an asset", "/icon.png"),
                           ("JSON", "/api/health"), ("a 404", "/no-such-route"),
                           ("the manifest", "/manifest.webmanifest")):
            _, hdrs, _ = get(path)
            check("no header on %s names the interpreter" % what,
                  fingerprint_of(hdrs) == "", fingerprint_of(hdrs))
            check("%s says Server: tinycmdr" % what,
                  hdrs.get("server") == "tinycmdr", hdrs.get("server"))

        # ---- the four headers ride the page, an asset and a JSON reply ----------
        status, hdrs, body = get("/")
        check("GET / serves the page", status == 200 and b"<html" in body.lower(),
              status)
        check("the page carries the security headers", not security_missing(hdrs),
              security_missing(hdrs))

        status, hdrs, _ = get("/page.css")
        check("GET /page.css serves the stylesheet", status == 200, status)
        check("the stylesheet carries them", not security_missing(hdrs),
              security_missing(hdrs))

        status, hdrs, _ = get("/icon.png")
        check("GET /icon.png serves the icon", status == 200, status)
        check("a static image asset carries them", not security_missing(hdrs),
              security_missing(hdrs))

        status, hdrs, body = get("/api/health")
        check("GET /api/health answers JSON",
              status == 200 and body.strip().startswith(b"{"), (status, body[:40]))
        check("a JSON reply carries them", not security_missing(hdrs),
              security_missing(hdrs))

        status, hdrs, _ = get("/no-such-route")
        check("an unknown route is 404", status == 404, status)
        check("even a 404 carries them", not security_missing(hdrs),
              security_missing(hdrs))

        status, hdrs, body = get("/api/run", method="PUT")
        check("PUT is refused 405 in the API's own shape",
              status == 405 and b"method not allowed" in body, (status, body[:80]))
        check("a 405 carries them", not security_missing(hdrs),
              security_missing(hdrs))

        # HEAD is the same headers with the body dropped, and keeps them too.
        status, hdrs, body = get("/", method="HEAD")
        check("HEAD / answers the GET headers with no body",
              status == 200 and body == b"" and int(hdrs.get("content-length", 0)) > 0,
              (status, len(body), hdrs.get("content-length")))
        check("HEAD carries the headers and no fingerprint",
              not security_missing(hdrs) and fingerprint_of(hdrs) == "",
              (security_missing(hdrs), fingerprint_of(hdrs)))

        # A 304 revalidation still passes through end_headers(), so it carries them.
        _, hdrs, _ = get("/icon.png")
        etag = hdrs.get("etag")
        status, hdrs304, _ = get("/icon.png", {"If-None-Match": etag or ""})
        check("a conditional asset request revalidates 304", status == 304, status)
        check("the 304 carries them", not security_missing(hdrs304),
              security_missing(hdrs304))

        # ---- the streaming route still streams ----------------------------------
        # The download route writes a Content-Length and then the bytes; a header pass
        # that buffered replies would show up here as the whole body arriving with the
        # headers, or the Content-Length going missing.
        big = STAGE / "big.bin"
        big.write_bytes(os.urandom(8 * 1024 * 1024))
        run = fb._web_new_run("webheaderssuite")
        run.add("file", str(big))
        uid = run.lines[-1]["uid"]
        url = "/api/download?run=%s&uid=%s" % (run.id, urllib.parse.quote(uid, safe=""))
        promised = big.stat().st_size

        sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        try:
            sock.sendall(b"GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                         b"X-Tinycmdr-Token: %s\r\n\r\n"
                         % (url.encode(), port, token.encode()))
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
            status_line, dl = parse_head(raw)
            rest = raw.split(b"\r\n\r\n", 1)[1]
            check("the download answers 200", b" 200 " in status_line, status_line)
            check("the stream keeps its Content-Type",
                  dl.get("content-type") == "application/octet-stream",
                  dl.get("content-type"))
            check("the stream is not re-framed into chunked",
                  "transfer-encoding" not in dl, dl.get("transfer-encoding"))
            check("the stream carries them", not security_missing(dl),
                  security_missing(dl))
            check("the stream names no interpreter", fingerprint_of(dl) == "",
                  fingerprint_of(dl))
            check("the stream promises the file's own length",
                  int(dl.get("content-length", -1)) == promised,
                  (dl.get("content-length"), promised))
            # The point: the headers were readable while only a sliver of an 8 MiB body
            # had come across, so the reply is streamed, not assembled and then sent.
            check("the body is still arriving when the headers land (not buffered)",
                  len(rest) < promised, (len(rest), promised))

            got, reads = len(rest), 0
            while got < promised:
                chunk = sock.recv(1 << 16)
                if not chunk:
                    break
                got += len(chunk)
                reads += 1
            check("the whole body streams to the client",
                  got == promised and reads >= 1, (got, promised, reads))

            # And the connection survives the stream: HTTP/1.1 keep-alive framing is
            # intact, so a second request on the same socket still answers.
            sock.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n"
                         % port)
            raw2 = b""
            while b"\r\n\r\n" not in raw2:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw2 += chunk
            status2, second = parse_head(raw2)
            body2 = raw2.split(b"\r\n\r\n", 1)[1]
            need = int(second.get("content-length", 0) or 0)
            while len(body2) < need:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                body2 += chunk
            check("the same connection serves the next request after a stream",
                  b" 200 " in status2 and need > 0 and len(body2) == need
                  and body2.strip().startswith(b"{"),
                  (status2, len(body2), need))
            check("...with the headers and no fingerprint",
                  not security_missing(second) and fingerprint_of(second) == "",
                  (security_missing(second), fingerprint_of(second)))
        finally:
            sock.close()
    finally:
        srv.shutdown()
        srv.server_close()

    print()
    print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
    if FAILS:
        print("%d failed: %s" % (len(FAILS), FAILS))
        return 1
    print("the server names itself and refuses to be framed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(STAGE, ignore_errors=True)
    sys.exit(rc)
