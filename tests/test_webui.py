"""The page lane's contract: a token always, no server without one, files in and out.

This is the revived web UI's suite, written against the lane as it EXISTS now (the
original suite died with the old lane). Every check below is either a behaviour the
lane promises or an incident the old one paid for:

    * no token -> the start path MINTS one (into .env) rather than serving ungated; the
      old lane served loopback with no auth at all: CSRF against shell access.
      A host that upgrades into the page gets a token and a link, not homework.
      web.enabled false -> no server either.
    * the token is compared in constant time, and it never appears in the log.
    * Host/Origin rules: a cross-origin request is refused, a foreign Host is refused.
    * the body is capped BEFORE it is read; uploads have their own cap.
    * uploads land under ./uploads with a sanitised name; downloads serve ONLY a file
      the agent offered (a run line of kind 'file'), by run id + uid.
    * /api/health tells the truth and needs no token; the page itself needs none.
    * a second start on a busy port announces the running page instead of failing.

Offline and self-contained: loopback only, port 0, no model.

    python tests/test_webui.py
"""
import base64
import contextlib
import http.client
import importlib.util
import io
import json
import logging
import os
import re
import shutil
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
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
    check("the page is this install's door" in out0 and "tinycmdr setup" in out0,
          "and orients the operator (setup picks LAN vs loopback and the port)")
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
    check(b"id=lanetext" in body and b"id=lanedismiss" in body,
          "the lane banner has its own text span and a dismiss button")
    check(req("GET", "/icon.png")[0] == 200, "the icon is served")
    check(req("GET", "/manifest.webmanifest")[0] == 200, "the manifest is served")

    # ---- the page wears the HOST's theme, and the host's icon ---------------
    # theme.toml is host-owned and the terminal already reads it: the page reads the SAME
    # file (theme_palette), so a palette cannot apply to one and not the other -
    # 2026-10-04: "no colour theme to match the roman empire styling".
    builtin = base64.b64decode(fb.WEB_ICON_PNG_B64)
    check(req("GET", "/icon.png", limit=200000)[1] == builtin,
          "with no host art, the built-in icon is served")

    # ...and that built-in icon is OUR badge, not the deleted lane's blue placeholder: the
    # monogram on a blue disc read as somebody else's logo at 16px (
    # 2026-10-04: "what is that icon you are using that says fb?"). The embedded render is
    # filter-0 RGB, so this decodes without an unfilter pass.
    def _rgb(data):
        pos, idat, w, h = 8, b"", 0, 0
        while pos < len(data):
            ln = struct.unpack(">I", data[pos:pos + 4])[0]
            typ = data[pos + 4:pos + 8]
            c = data[pos + 8:pos + 8 + ln]
            pos += 12 + ln
            if typ == b"IHDR":
                w, h = struct.unpack(">II", c[:8])
            elif typ == b"IDAT":
                idat += c
            elif typ == b"IEND":
                break
        return w, h, zlib.decompress(idat)

    w, h, raw = _rgb(builtin)
    check((w, h) == (192, 192),
          f"the built-in fallback is the 192px badge ({w}x{h})")
    stride = w * 3
    red = blue = 0
    for y in range(h):
        row = raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)]
        for x in range(0, stride, 3):
            red += row[x]
            blue += row[x + 2]
    check(red > blue * 3 // 2,
          f"and it is the WARM roman emblem, not a blue placeholder (r={red} b={blue})")
    # the stage starts bare: copy the shell's own stylesheet in, or every check below
    # grades a page that cannot be styled at all
    (STAGE / "assets").mkdir(exist_ok=True)
    shutil.copy2(BASE / "assets" / "webui.css", STAGE / "assets" / "webui.css")
    theme_file = STAGE / "theme.toml"
    theme_file.write_text(
        'default = "suite"\n\n[themes.suite]\n'
        'background = "#010203"\npanel = "#040506"\ntext = "#070809"\nmuted = "#0a0b0c"\n'
        'gold = "#0d0e0f"\nember = "#101112"\nbronze = "#131415"\ncrimson = "#161718"\n'
        'error = "#191a1b"\nselection_bg = "#1c1d1e"\n', encoding="utf-8")
    try:
        code, body, _ = req("GET", "/", limit=400000)
        html = body.decode("utf-8", "replace").replace(" ", "")
        css = req("GET", "/page.css", limit=2000000)[1].decode("utf-8", "replace")
        check(code == 200 and "--bg:#010203" in css.replace(" ", ""),
              "the stylesheet takes its palette from the host's theme.toml", code)
        check("--gold:#0d0e0f" in css.replace(" ", "")
              and "--sel:#1c1d1e" in css.replace(" ", "")
              and "--ember:#101112" in css.replace(" ", ""),
              "every role the page paints with comes from that file")
        check("{{THEME}}" not in css and "{{THEME_COLOR}}" not in html,
              "and no placeholder is left in the stylesheet or the page")
        check('content="#010203"' in html,
              "the mobile chrome colour is the theme's background too")
        man = json.loads(req("GET", "/manifest.webmanifest")[1])
        check(man.get("background_color") == "#010203"
              and man.get("theme_color") == "#010203",
              "and so is the installed-app chrome", man)
    finally:
        theme_file.unlink()
    css = req("GET", "/page.css", limit=2000000)[1].decode("utf-8", "replace")
    check("--bg:#0d0f12" in css.replace(" ", ""),
          "with no theme file the built-in roman-night palette stands")

    (STAGE / "assets").mkdir(exist_ok=True)
    mine = b"\x89PNG\r\n\x1a\nsuite-icon"
    (STAGE / "assets" / "page-icon.png").write_bytes(mine)
    try:
        check(req("GET", "/icon.png", limit=200000)[1] == mine,
              "assets/page-icon.png becomes the favicon byte for byte")
    finally:
        (STAGE / "assets" / "page-icon.png").unlink()

    # ---- the mark: the transparent form, on the page and in the tab ----------
    html = req("GET", "/", limit=400000)[1].decode("utf-8", "replace")
    check(re.search(r'rel=icon href="/mark\.png\?v=', html)
          and re.search(r'apple-touch-icon href="/icon\.png\?v=', html),
          "the tab takes the transparent mark; the home-screen icon stays opaque")
    check("id=emptymark" in html and re.search(r'src="/mark\.png\?v=', html),
          "and the empty state carries the emblem when there is no chibi")
    check("Greetings," in html and "Commander." in html and "Start a new campaign" in html,
          "with the greeting and the way in")
    check(req("GET", "/mark.png")[0] == 200
          and req("GET", "/mark.png")[2].get("Content-Type") == "image/png",
          "the mark is served")
    check(req("GET", "/mark.png", limit=200000)[1] == builtin,
          "with no host art the mark falls back to the built-in icon")
    real_mark = BASE / "assets" / "page-mark.png"
    if real_mark.exists():
        shutil.copy2(real_mark, STAGE / "assets" / "page-mark.png")
        try:
            body = req("GET", "/mark.png", limit=400000)[1]
            check(body == real_mark.read_bytes(),
                  "assets/page-mark.png is served byte for byte")
            check(len(body) > 25 and body[25] == 6,
                  "and it is the TRANSPARENT form (PNG colour type 6)", body[25:26])
        finally:
            (STAGE / "assets" / "page-mark.png").unlink()

    # ---- the figure: the chibi when the host ships one ------------------------
    html = req("GET", "/", limit=400000)[1].decode("utf-8", "replace")
    check("{{EMPTY_ART}}" not in html and "{{BACKDROP}}" not in html,
          "no art placeholder is left in the served page")
    check("id=emptymark" in html and re.search(r'src="/mark\.png\?v=', html),
          "with no chibi the empty state falls back to the emblem")
    check(req("GET", "/chibi.png")[0] == 404,
          "and /chibi.png says so plainly")
    real_chibi = BASE / "assets" / "page-chibi.png"
    if real_chibi.exists():
        shutil.copy2(real_chibi, STAGE / "assets" / "page-chibi.png")
        try:
            html = req("GET", "/", limit=400000)[1].decode("utf-8", "replace")
            n_chibi = len(re.findall(r'src="/chibi\.png\?v=', html))
            check(n_chibi >= 1,
                  "with assets/page-chibi.png the hero shows the character (%d)" % n_chibi)
            check(re.search(r'id=medallionimg src="/mark\.png\?v=', html),
                  "and the header medallion keeps the badge (the design's pairing)")
            body = req("GET", "/chibi.png", limit=2000000)[1]
            check(body == real_chibi.read_bytes(),
                  "the chibi is served byte for byte")
            check(len(body) > 25 and body[25] == 6,
                  "with its alpha intact (PNG colour type 6)", body[25:26])
        finally:
            (STAGE / "assets" / "page-chibi.png").unlink()

    # ---- the backdrop photo, the fonts, the command slab ---------------------
    # The operator's photo (2026-10-04) is the permanent replacement for the drawn
    # colonnade; the fonts stay bundled ("because this is a local application"), and the
    # command slab is a strong destination for the eye.
    html = req("GET", "/", limit=400000)[1].decode("utf-8", "replace")
    check('class="colonnade"' not in html,
          "with no photo the stage draws no backdrop")
    (STAGE / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE / "assets" / "roman-temple-spring.jpg",
                 STAGE / "assets" / "roman-temple-spring.jpg")
    for name in ("cinzel-600.woff2", "cinzel-700.woff2", "inter.woff2",
                 "jetbrains-mono-400.woff2"):
        shutil.copy2(BASE / "assets" / "fonts" / name, STAGE / "assets" / "fonts" / name)
    html = req("GET", "/", limit=400000)[1].decode("utf-8", "replace")
    jpg = req("GET", "/temple.jpg", limit=1000000)
    check('class="colonnade"' in html,
          "the backdrop element is in the stage when the photo is there")
    check('aria-hidden="true"' in html.split('class="colonnade"')[0][-200:]
          or 'aria-hidden="true"' in html,
          "...and it is decorative, never a click target")
    check(jpg[0] == 200 and jpg[2].get("Content-Type") == "image/jpeg"
          and len(jpg[1]) > 100000,
          "the photo is served at /temple.jpg, as a JPEG, whole",
          (jpg[0], jpg[2].get("Content-Type"), len(jpg[1])))
    check(jpg[1][:3] == b"\xff\xd8\xff", "...with the JPEG signature")
    check(bool(re.search(r'src="/(chibi|mark)\.png\?v=[0-9]', html)),
          "the stage figure's URL carries the app version (an update invalidates the "
          "day-long art cache - 'new background but old chibi', 2026-10-04)")
    check("temple.jpg?v=" in css, "...and the backdrop URL in the stylesheet does too")
    for name in ("cinzel-700.woff2", "inter.woff2", "jetbrains-mono-400.woff2"):
        r = req("GET", "/fonts/" + name, limit=200000)
        check(r[0] == 200 and r[2].get("Content-Type") == "font/woff2",
              "%s is served (%d bytes)" % (name, len(r[1])))
        check(r[1][:4] == b"wOF2", "...with the woff2 signature")
    check(req("GET", "/fonts/../tinycmdr.py")[0] == 404,
          "a font slug that is not ours is refused (no traversal)")
    check("@font-face" in css and "Cinzel" in css and "JetBrains Mono" in css,
          "and the stylesheet declares the families itself (no CDN)")
    check("Issue a command" in html and ">Dispatch" in html
          and "prompt-sigil" in html,
          "the command deck keeps its sigil, its placeholder and its Dispatch")

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

    # ---- the browser's one handover: header in, HttpOnly cookie out ----------
    # The token arrives in the URL fragment once; the page POSTs it here and forgets it,
    # so no URL, no history entry and no localStorage keeps it afterwards (
    # 2026-10-04: "the token should not be visible in the browser url").
    code, body, hdr = req("POST", "/api/login", TOK)
    cookie = hdr.get("Set-Cookie") or ""
    check(code == 200, "POST /api/login with the header: 200", code)
    check("tinycmdr_token=" in cookie and "HttpOnly" in cookie
          and "SameSite=Strict" in cookie,
          "it answers with an HttpOnly, SameSite=Strict cookie", cookie)
    check(req("GET", "/api/tasks", {"Cookie": "tinycmdr_token=%s" % token})[0] == 200,
          "the cookie alone authenticates a later request")
    check(req("GET", "/api/tasks", {"Cookie": "tinycmdr_token=nope"})[0] == 401,
          "a wrong cookie: 401")
    check(req("POST", "/api/login", {"X-Tinycmdr-Token": "wrong"})[0] == 401,
          "and the handover refuses a wrong token")
    check(req("GET", "/api/login")[0] == 401,
          "GET /api/login is the page's probe: no token, no cookie -> 401")
    _probe = req("GET", "/api/login", TOK)
    check(_probe[0] == 200 and json.loads(_probe[1]).get("ok") is True,
          "...and a browser that has one gets the probe's ok payload - NOT the log tail "
          "(/api/login must sit above /api/log)",
          "%s %r" % (_probe[0], _probe[1][:80]))
    check(req("POST", "/api/run", body=b'{"message":"hi"}')[0] == 401,
          "a POST without a token: 401 (it never reaches the agent)")

    # ---- search INSIDE conversations (operator, 2026-10-04) --------------------
    _sess = STAGE / "sessions"
    _sess.mkdir(parents=True, exist_ok=True)
    (_sess / "web-searchable.json").write_text(json.dumps([
        {"role": "user", "content": "how do I rotate the page token"},
        {"role": "assistant",
         "content": "run tinycmdr token set TINYCMDR_WEB_TOKEN"}]), encoding="utf-8")
    _r = req("GET", "/api/search?q=rotate%20token", TOK)
    _j = json.loads(_r[1]) if _r[0] == 200 else {}
    check(_r[0] == 200 and any("rotate" in (m.get("snippet") or "").lower()
                              for m in (_j.get("matches") or [])),
          "search inside conversations finds message text, not just titles",
          (_r[0], (_r[1] or b"")[:120]))
    check(req("GET", "/api/search?q=rotate")[0] == 401,
          "and the search sits behind the token like everything else")
    check(json.loads(req("GET", "/api/search?q=x", TOK)[1]).get("matches") == [],
          "a one-character query scans nothing")

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

        # ---- the auto-open fires once per token+port per window (2026-10-05) -------
        # A restart ladder or a day of gate runs used to open one tab per start; each
        # carried the token of the process that opened it, so a pile of them read
        # "token not accepted" after any rotation. The marker lives in BASE_DIR.
        marker = fb.BASE_DIR / ".web-open"
        marker.unlink(missing_ok=True)
        check(fb._browser_open_due("tokA", 8790, ttl=3600) is True,
              "the first auto-open for a token+port is due")
        check(fb._browser_open_due("tokA", 8790, ttl=3600) is False,
              "...and the same one inside the window is not (no stacked tabs)")
        check(fb._browser_open_due("tokB", 8790, ttl=3600) is True,
              "a ROTATED token opens at once (the stale tab is what was refused)")
        marker.unlink(missing_ok=True)
        check(fb._browser_open_due("tokA", 8790, ttl=0) is True,
              "an expired window opens again")
        saved_env = os.environ.get("TINYCMDR_NO_BROWSER")
        os.environ["TINYCMDR_NO_BROWSER"] = "1"
        try:
            check(fb._browser_possible() is False,
                  "TINYCMDR_NO_BROWSER=1 forbids the auto-open outright")
        finally:
            if saved_env is None:
                os.environ.pop("TINYCMDR_NO_BROWSER", None)
            else:
                os.environ["TINYCMDR_NO_BROWSER"] = saved_env
        # The RUNNER owns that guard so a suite added later cannot leak a tab.
        sys.path.insert(0, str(BASE / "tests"))
        import run_all  # noqa: E402
        child = run_all.child_env(BASE / "tests" / "test_webui.py", BASE)
        check(child.get("TINYCMDR_NO_BROWSER") == "1",
              "run_all gives every suite the no-browser guard", child.get("TINYCMDR_NO_BROWSER"))
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
