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
    * web.port 0 means "the OS picks a free port": every reader that NAMES a port (the
      a2a card, `tinycmdr web`, doctor, the setup summary, the firewall hints) says the
      port that was actually bound - recorded by run_webui as lane_up "port N" - and only
      falls back to the published 8790 when nothing was recorded.

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
import socket
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
# TINYCMDR_SRC points this at a reverted copy, so a fix can be watched going red
# (tests/run_all.py clears it for a normal run).
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

# A suite never opens a browser tab - and that has to include a HAND-RUN suite. run_all.py
# passes TINYCMDR_NO_BROWSER=1 to its children, but running this file directly (which its
# own header invites) left the guard unset, and the in-process server below auto-opened
# the operator's browser on every start. The suite carries
# the guard itself now, and a check below fails any suite that starts a web surface
# without it.
os.environ.setdefault("TINYCMDR_NO_BROWSER", "1")

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
    check(fb.scrub(minted) == "«redacted»",
          "the minted token is a secret to scrub() from the moment it exists",
          fb.scrub(minted)[:60])
    # The token file is the one file the agent never reads into a prompt, so the mode it
    # is created with is the point: on POSIX the product chmods it 0600 (see _env_set),
    # and "no group or world bits" IS that claim. Windows has no such bits - st_mode
    # reports 0o666 for a writable file however it was created, and chmod there carries
    # only the read-only attribute - so the same claim is graded as what Windows DOES
    # guarantee: the file stayed the owner's to read AND rewrite (a chmod that landed the
    # read-only bit on it would break the next token rotation), and it is a file inside
    # the install dir rather than somewhere else on the box.
    _env_mode = fb.ENV_FILE.stat().st_mode
    if os.name == "nt":
        check(os.access(fb.ENV_FILE, os.R_OK | os.W_OK)
              and (_env_mode & 0o600) == 0o600
              and fb.ENV_FILE.parent == fb.BASE_DIR,
              "the token file is the owner's to read and rewrite, in the install dir",
              (oct(_env_mode & 0o777), str(fb.ENV_FILE.parent)))
    else:
        check((_env_mode & 0o077) == 0,
              "not group- or world-readable", oct(_env_mode & 0o777))
    check("minted TINYCMDR_WEB_TOKEN" in out0, "and the start says so", out0[:80])
    check("the page is this install's door" in out0 and "tinycmdr setup" in out0,
          "and orients the operator (setup picks LAN vs loopback and the port)")
    check("#token=" not in out0 and "tinycmdr web" in out0,
          "the captured start prints no token and names the door to the link")
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
    ver_link = ("/releases/tag/v" + fb.VERSION).encode()
    check(b'id=ver class="brand-version" href="' in body and ver_link in body,
          "the header version stamp links to this build's release notes", body[:120])
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

    # ---- the legion routes: token-gated like everything, empty without cohorts -------
    check(req("GET", "/api/legion")[0] == 401,
          "the legion route without a token: 401")
    _lg = req("GET", "/api/legion", TOK)
    _lgj = json.loads(_lg[1]) if _lg[0] == 200 else {}
    check(_lg[0] == 200 and _lgj.get("cohorts") == [] and _lgj.get("tabs") == []
          and _lgj.get("praetorium", {}).get("version"),
          "a hub with no cohorts answers an empty legion (and its own version)",
          (_lg[0], (_lg[1] or b"")[:140]))
    check(req("GET", "/api/legion/lines?remote=ghost", TOK)[0] == 404
          and req("GET", "/api/legion/session?remote=ghost", TOK)[0] == 404,
          "session/lines for a cohort this hub does not have: 404")
    check(req("POST", "/api/legion", TOK, b'{"op":"send","remote":"ghost","message":"x"}')[0] == 404,
          "an order to a cohort this hub does not have: 404 (an order names a "
          "CONFIGURED remote, never an address)")
    check(req("POST", "/api/legion", TOK, b'{"op":"zzz"}')[0] == 400,
          "an unknown legion op: 400")
    check(req("POST", "/api/legion", None, b'{"op":"send"}')[0] == 401,
          "a legion POST without a token: 401")

    # ---- ADD COHORT: the page writes a new remote into the hub's own config -----
    # The dialog posts a box the operator runs - URL + its page token - and the hub
    # probes the card BEFORE anything is written, so an unreachable box changes
    # nothing. The token lands in .env under a name ending in _TOKEN on purpose: it
    # is the same sweep every other secret rides, and the hub must resolve it with
    # no restart (this process was just handed the value).
    class _CohortHandler(BaseHTTPRequestHandler):
        def log_message(self, *a):                              # keep the suite quiet
            pass

        def _send(self, payload, status=200):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/.well-known/agent-card.json"):
                self._send({"name": "tinycmdr", "version": "9.9.9",
                            "capabilities": {"extensions": [
                                {"uri": fb.A2A_RUN_LINES_URI, "required": False}]}})
            else:
                self._send({"error": "not found"}, 404)

    class _CohortServer(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            pass                                    # a hung-up probe is not a suite error

    _cohort = _CohortServer(("127.0.0.1", 0), _CohortHandler)
    threading.Thread(target=_cohort.serve_forever, daemon=True).start()
    cohort_url = "http://127.0.0.1:%d" % _cohort.server_address[1]
    _CNAME = "TINYCMDR_COHORT_STORAGE_TOKEN"
    _CTOK = "tok-cohort-suite-0123456789"

    def _add(payload):
        code, body, _h = req("POST", "/api/legion", TOK, json.dumps(payload).encode())
        try:
            return code, json.loads(body)
        except Exception:                                           # noqa: BLE001
            return code, {}

    _saved_web = dict(fb.CONFIG["web"])   # the take-effect below reloads CONFIG from
    code, j = _add({"op": "add-remote", "remote": "storage", "url": cohort_url,
                    "token": _CTOK})
    fb.CONFIG["web"] = _saved_web         # ... disk + env, which the suite's in-memory
                                          # web block must survive (the reload itself
                                          # is the product's normal path)
    check(code == 200 and j.get("added", {}).get("ok") is True
          and j.get("added", {}).get("lines") is True
          and j.get("added", {}).get("card", {}).get("version") == "9.9.9",
          "ADD COHORT probes the card and reports what it found",
          (code, json.dumps(j.get("added"))[:180]))
    _names = [(c or {}).get("name") for c in (j.get("cohorts") or [])]
    check(_names == ["storage"] and j["cohorts"][0].get("lines") is True
          and "127.0.0.1" in (j["cohorts"][0].get("host") or ""),
          "the overview it answers with carries the new cohort", _names)
    check((fb.CONFIG.get("agent") or {}).get("a2a_remotes", {}).get("storage")
          == {"url": cohort_url, "token_env": _CNAME},
          "the running config knows it without a restart",
          (fb.CONFIG.get("agent") or {}).get("a2a_remotes"))
    _cfg = json.loads((STAGE / "config.json").read_text(encoding="utf-8"))
    check(((_cfg.get("agent") or {}).get("a2a_remotes") or {}).get("storage")
          == {"url": cohort_url, "token_env": _CNAME},
          "config.json on disk carries the remote and its token_env name")
    _env = fb.ENV_FILE.read_text(encoding="utf-8") if fb.ENV_FILE.exists() else ""
    check(("%s=%s" % (_CNAME, _CTOK)) in _env,
          "the token landed in .env under a name the sweep knows")
    if os.name != "nt":
        check((fb.ENV_FILE.stat().st_mode & 0o777) == 0o600,
              "and .env is mode 0600 after the write",
              oct(fb.ENV_FILE.stat().st_mode & 0o777))
    _sess = json.loads(req("GET", "/api/sessions", TOK)[1])
    check(_sess.get("legion") == 1,
          "the sessions reply (the page's own clock) counts it", _sess.get("legion"))
    check(_CTOK not in req("GET", "/api/legion", TOK)[1].decode("utf-8", "replace"),
          "the legion overview never echoes the token")

    # every refusal keeps the disk as it was: a name, the hub's own channel, a dupe,
    # a non-http address, a missing token, and a box nothing answers at
    _cfg_before = (STAGE / "config.json").read_text(encoding="utf-8")
    code, j = _add({"op": "add-remote", "remote": "bad name!", "url": cohort_url,
                    "token": _CTOK})
    check(code == 400 and j.get("error"), "a malformed cohort name: 400", (code, j))
    code, j = _add({"op": "add-remote", "remote": "praetorium", "url": cohort_url,
                    "token": _CTOK})
    check(code == 400 and "praetorium" in (j.get("error") or ""),
          "the hub's own channel name: 400", (code, j))
    code, j = _add({"op": "add-remote", "remote": "storage", "url": cohort_url,
                    "token": _CTOK})
    check(code == 400 and "already" in (j.get("error") or ""),
          "a name already configured: 400", (code, j))
    code, j = _add({"op": "add-remote", "remote": "second",
                    "url": "ftp://127.0.0.1", "token": _CTOK})
    check(code == 400 and "http" in (j.get("error") or ""),
          "an address that is not http(s): 400", (code, j))
    code, j = _add({"op": "add-remote", "remote": "second", "url": cohort_url,
                    "token": "   "})
    check(code == 400 and j.get("error"), "a missing token: 400", (code, j))
    code, j = _add({"op": "add-remote", "remote": "second",
                    "url": "http://127.0.0.1:1", "token": _CTOK})
    check(code == 502 and j.get("error"),
          "a box nothing answers at: 502, not a false success", (code, j))
    check((STAGE / "config.json").read_text(encoding="utf-8") == _cfg_before,
          "the refused adds wrote nothing to config.json")
    check(req("POST", "/api/legion",
              None, b'{"op":"add-remote","remote":"x","url":"http://127.0.0.1:1",'
                    b'"token":"y"}')[0] == 401,
          "an add-remote POST without a token: 401")

    # ---- the browser's one handover: header in, HttpOnly cookie out ----------
    # The token arrives in the URL fragment once; the page POSTs it here and forgets it,
    # so no URL, no history entry and no localStorage keeps it afterwards (
    # 2026-10-04: "the token should not be visible in the browser url").
    code, body, hdr = req("POST", "/api/login", TOK)
    cookie = hdr.get("Set-Cookie") or ""
    # The name carries the PORT: cookies are host-scoped (RFC 6265), so one name for every
    # install on a host means the second page's login overwrites the first's cookie.
    _cname = "tinycmdr_token_%d" % port
    check(code == 200, "POST /api/login with the header: 200", code)
    check(_cname + "=" in cookie and "HttpOnly" in cookie
          and "SameSite=Strict" in cookie,
          "it answers with an HttpOnly, SameSite=Strict cookie named for the port", cookie)
    check(req("GET", "/api/tasks", {"Cookie": "%s=%s" % (_cname, token)})[0] == 200,
          "the cookie alone authenticates a later request")
    check(req("GET", "/api/tasks", {"Cookie": "%s=nope" % _cname})[0] == 401,
          "a wrong cookie: 401")
    check(req("GET", "/api/tasks", {"Cookie": "tinycmdr_token=%s" % token})[0] == 401,
          "the un-suffixed name does not authenticate (one name per port)")
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
    check(req("GET", "/api/legion", {**TOK, "Origin": "http://evil.example"})[0] == 403
          and req("POST", "/api/legion",
                  {**TOK, "Origin": "http://evil.example"}, b'{"op":"zzz"}')[0] == 403,
          "the legion routes ride the same Origin guard")
    # Cookies are host-scoped, not port-scoped: a page on another port of this same name
    # still carries the operator's session cookie, so the Origin check has to compare the
    # port too (measured 2026-10-05 with a real browser: even the CORS-blocked response
    # left the request delivered - a conversation was created through it).
    check(req("GET", "/api/tasks",
              {**TOK, "Origin": "http://127.0.0.1:%d" % (port + 1)})[0] == 403,
          "an Origin on another port of the same name: 403")
    check(fb._web_authority("[::1]:8790") == ("::1", "8790")
          and fb._web_authority("box") == ("box", "")
          and fb._web_authority("127.0.0.1") != fb._web_authority("127.0.0.1:8790"),
          "the authority compare keeps host and port", (
              fb._web_authority("[::1]:8790"), fb._web_authority("box"),
              fb._web_authority("127.0.0.1")))
    code, body, _ = req("GET", "/api/tasks", {**TOK, "Origin": base})
    check(code == 200, "a same-origin request passes", code)

    # ---- saturation answers BUSY, and never to the box's own probes ----------
    # Measured 2026-10-05: 40 idle sockets made /api/health abort mid-connection, and the
    # restart doors probe it with `curl -sf` - a page that is merely busy read as a dead
    # box. Loopback (this process's own probes) is exempt from the cap now; a LAN peer
    # over the cap gets an HTTP 503 instead of a silent close.
    idle = []
    try:
        for _ in range(srv.MAX_CONN + 8):
            try:
                idle.append(socket.create_connection(("127.0.0.1", port), timeout=5))
            except OSError:
                break
        deadline = time.time() + 5
        while srv._conn_live < srv.MAX_CONN and time.time() < deadline:
            time.sleep(0.05)
        check(srv._conn_live >= srv.MAX_CONN,
              "the connection cap is reachable (%d live)" % srv._conn_live)
        code, body, _ = req("GET", "/api/health", timeout=10)
        check(code == 200 and b'"ok"' in body,
              "a loopback health probe still answers while the cap is full",
              (code, body[:60]))

        class _FakeSock:
            def __init__(self):
                self.sent, self.closed = b"", False

            def sendall(self, data):
                self.sent += data

            def close(self):
                self.closed = True

        saved = srv._conn_live
        srv._conn_live = srv.MAX_CONN
        fake = _FakeSock()
        srv.process_request(fake, ("192.0.2.9", 4242))
        srv._conn_live = saved
        check(b"503" in fake.sent and b"Retry-After" in fake.sent and fake.closed,
              "an over-cap peer is answered 503, not a silent close", fake.sent[:60])
    finally:
        for s in idle:
            try:
                s.close()
            except OSError:
                pass
        deadline = time.time() + 5
        while srv._conn_live >= srv.MAX_CONN and time.time() < deadline:
            time.sleep(0.05)

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
        # The warm thread starts asynchronously, so the COUNT is waited for rather than
        # sampled: on a loaded CI runner the thread may not have reached the stub in the
        # few milliseconds the request took, and the check read the race, not the rule
        # (measured 2026-10-09, macos-latest: {'n': 0} on a tree that passes locally).
        _t0 = time.time()
        while calls["n"] < 1 and time.time() - _t0 < 10:
            time.sleep(0.1)
        check(calls["n"] >= 1, "the resolver runs in the background (bounded wait)",
              calls)
    finally:
        fb.socket = real_socket

    # ---- the body cap is checked BEFORE the read ----------------------------
    big = b"x" * (fb.WEB_BODY_MAX + 64)
    code, body, _ = req("POST", "/api/run", {**TOK, "Content-Type": "application/json"},
                        big)
    # 413, not 400 (A-208): the client can tell "send less" from "your JSON is broken",
    # and the same body never reaches the parser.
    check(code == 413 and b"too large" in body,
          "an oversized body is refused with 413 (send less), without reading it",
          (code, body[:80]))

    # ---- uploads ------------------------------------------------------------
    small = b"suite upload payload"
    code, body, _ = req("POST", "/api/upload?name=" + urllib.parse.quote("../../evil name.txt"),
                        TOK, small)
    saved = json.loads(body) if code == 200 else {}
    check(code == 200, "an upload with a token lands", code)
    # Containment, stated in the platform's own terms: the product builds the stored path
    # with the OS separator (uploads/x here, uploads\x on Windows - the reply is a path a
    # local agent hands to read_file), so `startswith("uploads/")` graded the separator
    # rather than the escape it names. What must hold on every platform: a RELATIVE path,
    # no .. component, exactly one level deep - a direct child of uploads/ - and a stored
    # NAME with no separator in it at all (the sanitiser's own promise, and the only thing
    # an escape ever needs; graded on the reply's `name`, which is a bare base name).
    _saved_rel = str(saved.get("path", ""))
    _saved_abs = STAGE / _saved_rel            # resolved the way the product built it
    _stored = str(saved.get("name", ""))
    check(_saved_rel and not os.path.isabs(_saved_rel)
          and ".." not in Path(_saved_rel).parts
          and _saved_abs.parent.resolve() == (STAGE / "uploads").resolve()
          and _stored and not _stored.startswith("..")
          and "/" not in _stored and "\\" not in _stored,
          "the stored name cannot escape uploads/", (_saved_rel, _stored))
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

    # ...and a path that stats but cannot be OPENED is answered, not promised: a
    # directory line kept in a run log (they were offerable before attach() refused
    # them) used to get 200 with a Content-Length and no body at all, which hangs the
    # operator's fetch and parks the handler thread on that socket.
    run_dir = fb._web_new_run("web")
    run_dir.add("file", str(STAGE))
    _st_dir, _bd_dir, _hd_dir = req(
        "GET", "/api/download?run=%s&uid=%s" % (
            run_dir.id, urllib.parse.quote(run_dir.lines[-1]["uid"], safe="")),
        TOK, timeout=3)
    check(_st_dir == 404,
          "a file that cannot be opened answers 404, not a Content-Length with no body",
          (_st_dir, _hd_dir.get("Content-Length"), _bd_dir[:60]))

    # ---- attach -------------------------------------------------------------
    dest = fb.WebDestination(run2)
    msg = dest.attach(str(uploaded))
    check("attached to the page" in msg and run2.lines[-1]["kind"] == "file",
          "attach() offers the file as a line", msg[:60])
    check(dest.attach("/nope/missing.bin").startswith("NOT SENT"),
          "attach() refuses what is not a file")
    # ...and "not a file" has to include a DIRECTORY: it stats fine, so an attach that
    # only stat()s offers it, and /api/download then commits a Content-Length and writes
    # no body - the operator's fetch never settles (measured 2026-10-07). The other
    # lanes' attach() already checked is_file().
    _attach_dir = STAGE / "attach-a-directory"
    _attach_dir.mkdir(exist_ok=True)
    _attach_lines = len(run2.lines)
    check(dest.attach(str(_attach_dir)).startswith("NOT SENT")
          and len(run2.lines) == _attach_lines,
          "attach() refuses a directory, not just a missing path",
          run2.lines[_attach_lines - 1:])

    # ---- the door's framing: HTTP/1.1, HEAD, method refusals, body shape ----
    # Every check below drives the real Handler over raw HTTP; none needs a browser.
    def probe(method, path, headers=None, body=None, timeout=10):
        """(status, headers, body, http_version) over a fresh connection.

        A dropped connection (no reply at all) comes back as
        (None, {}, b"<why>", None) so a check FAILS on it rather than the suite
        dying with a traceback - the exact shape A-206's crash produced."""
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read(), r.version
        except Exception as e:                                   # noqa: BLE001
            return None, {}, ("%s: %s" % (type(e).__name__, e)).encode(), None
        finally:
            c.close()

    # A-202: this class defaulted to HTTP/1.0, so every poll paid a fresh TCP
    # handshake and closed the connection. Two requests on ONE connection is the
    # real proof, not the version string alone.
    c1 = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c1.request("GET", "/api/health")
        _r1 = c1.getresponse()
        _ver, _b1 = _r1.version, _r1.read()
        c1.request("GET", "/api/health")
        _r2 = c1.getresponse()
        _code2, _b2 = _r2.status, _r2.read()
        check(_ver == 11 and _r1.status == 200 and _code2 == 200 and _b2 == _b1,
              "the door speaks HTTP/1.1 and reuses the connection for the next poll",
              (_ver, _r1.status, _code2))
    except Exception as e:                                       # noqa: BLE001
        check(False, "the door speaks HTTP/1.1 and reuses the connection for the next poll",
              "%s: %s" % (type(e).__name__, e))
    finally:
        c1.close()

    # ...and every response carries an exact Content-Length: that is what makes
    # keep-alive safe (an unframed body is read to EOF, i.e. the next reply).
    framed = []
    for _m, _p, _h, _d in (("GET", "/", TOK, None),
                           ("GET", "/api/health", None, None),
                           ("GET", "/api/tasks", TOK, None),
                           ("GET", "/api/tasks", None, None),          # 401
                           ("GET", "/api/jobs", TOK, None),
                           ("GET", "/api/inventory", TOK, None),
                           ("GET", "/api/log", TOK, None),
                           ("GET", "/api/commands", TOK, None),
                           ("GET", "/api/session?key=web-none", TOK, None),
                           ("GET", "/api/legion", TOK, None),
                           ("GET", "/api/legion/lines?remote=ghost", TOK, None),  # 404
                           ("POST", "/api/legion", TOK, b'{"op":"zzz"}'),       # 400
                           ("GET", "/api/download?run=x&uid=y", TOK, None),   # 404
                           ("GET", "/api/login", TOK, None),
                           ("GET", "/page.css", None, None),
                           ("GET", "/manifest.webmanifest", None, None),
                           ("GET", "/icon.png", None, None),
                           ("GET", "/mark.png", None, None),
                           ("GET", "/fonts/nope.woff2", None, None),   # 404
                           ("GET", "/nope", None, None),              # 404
                           ("POST", "/api/sessions", TOK, b'{"op":"zzz"}'),  # 400
                           ("POST", "/api/login", TOK, b""),          # 200
                           ("OPTIONS", "/api/run", TOK, None)):       # 405
        _st, _hd, _bd, _ = probe(_m, _p, _h, _d)
        cl = _hd.get("Content-Length")
        framed.append((_m, _p, _st, cl, len(_bd) if isinstance(_bd, bytes) else -1))
    check(all(cl is not None and int(cl) == n for _, _, _, cl, n in framed),
          "every route frames its reply with an exact Content-Length", framed)

    # ...and an error answered BEFORE the body is read must still leave the
    # connection usable (drained, not poisoned): the 401's body is discarded and
    # the next request on the same socket answers.
    c2 = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c2.connect()
        c2.auto_open = 0        # a silent reconnect would hide a poisoned socket
        c2.request("POST", "/api/run", body=b'{"message":"hi"}')     # no token -> 401
        _a = c2.getresponse()
        _code_a, _ = _a.status, _a.read()
        c2.request("GET", "/api/health")
        _code_b = c2.getresponse().status
        check(_code_a == 401 and _code_b == 200,
              "a body sent with a refused POST is drained; the SAME connection survives",
              (_code_a, _code_b))
    except Exception as e:                                       # noqa: BLE001
        check(False, "a body sent with a refused POST is drained; the SAME connection "
                     "survives", "%s: %s" % (type(e).__name__, e))
    finally:
        c2.close()

    # ...and that drain is idempotent per REQUEST, not per CONNECTION: one Handler
    # instance serves every request on a kept-alive socket, so a flag the previous
    # request set was read as this one's. Measured 2026-10-07: the first GET drains
    # (and sets `_body_taken`), the next POST is refused 401 without reading its body,
    # and the third request on that connection was answered with Python's stock 501
    # for the leftover bytes while the socket closed.
    c3 = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c3.connect()
        c3.auto_open = 0
        c3.request("GET", "/api/health")
        _g1 = c3.getresponse()
        _g1.read()
        c3.request("POST", "/api/run", body=b'{"message":"hi"}')     # no token -> 401
        _g2 = c3.getresponse()
        _g2.read()
        c3.request("GET", "/api/health")
        _g3 = c3.getresponse()
        _g3.read()
        check(_g1.status == 200 and _g2.status == 401 and _g3.status == 200,
              "the drain is per request: a refusal after an earlier GET on the same "
              "connection leaves it usable", (_g1.status, _g2.status, _g3.status))
    except Exception as e:                                       # noqa: BLE001
        check(False, "the drain is per request: a refusal after an earlier GET on the "
                     "same connection leaves it usable", "%s: %s" % (type(e).__name__, e))
    finally:
        c3.close()

    # A-203: HEAD was Python's stock 501, so `curl -I` read a healthy bot as dead.
    _st, _hd, _bd, _ = probe("HEAD", "/api/health")
    check(_st == 200 and _bd == b"" and int(_hd.get("Content-Length") or 0) > 0,
          "HEAD /api/health answers the GET headers with no body",
          (_st, len(_bd), _hd.get("Content-Length")))
    _st, _hd, _bd, _ = probe("HEAD", "/api/tasks", TOK)
    check(_st == 200 and _bd == b"",
          "HEAD runs the routing (a gated route still answers its status)", (_st, _bd[:40]))

    # A-204 / A-205: the stock 501 is an HTML page no JSON client can read.
    for _meth in ("OPTIONS", "PUT", "DELETE", "PATCH"):
        _st, _hd, _bd, _ = probe(_meth, "/api/run", TOK, b"")
        try:
            _j = json.loads(_bd)
        except Exception:
            _j = {}
        check(_st == 405 and _j.get("error") == "method not allowed"
              and _meth in (_j.get("method") or ""),
              "%s is refused in the API's JSON shape, not a 501 HTML page" % _meth,
              (_st, _bd[:80]))

    # A-206: a JSON body that is not an object used to reach `body.get(...)` and
    # raise, dropping the connection with zero bytes.
    for _path in ("/api/run", "/api/sessions", "/api/steer", "/api/stop", "/api/chat"):
        for _shape in (b"[]", b'["x"]', b'"hi"', b"0", b"true", b"null"):
            _st, _hd, _bd, _ = probe("POST", _path, TOK, _shape)
            ok = _st == 400 and b"expected a JSON object body" in _bd
            check(ok, "a %s body to %s is refused with 400 naming the shape"
                      % (_shape.decode(), _path), (_st, _bd[:90]))

    # A-207: `Transfer-Encoding: chunked` was neither decoded nor refused - the body
    # read as the integer 0, the caller got "empty message", and the chunk bytes were
    # left for the next keep-alive request. It now answers 411 and closes.
    _st, _hd, _bd, _ = probe("POST", "/api/run",
                             {**TOK, "Transfer-Encoding": "chunked"},
                             b"7\r\n{\"a\":1}\r\n0\r\n\r\n")
    try:
        _j = json.loads(_bd)
    except Exception:
        _j = {}
    check(_st == 411 and "Content-Length" in (_j.get("error") or "")
          and _hd.get("Connection") == "close",
          "a chunked body is refused with 411 and the connection closed, not misread",
          (_st, _hd.get("Connection"), _bd[:80]))

    # ...and a second request on that same socket gets no reply at all (the leftover
    # chunk bytes are never parsed as a request line).
    _sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        _sock.sendall(b"POST /api/run HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                      b"X-Tinycmdr-Token: %s\r\nTransfer-Encoding: chunked\r\n\r\n"
                      b"7\r\n{\"a\":1}\r\n0\r\n\r\n" % (port, token.encode()))
        _d = b""
        while b"\r\n\r\n" not in _d:
            _c = _sock.recv(4096)
            if not _c:
                break
            _d += _c
        _after = b""
        try:
            _sock.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % port)
            _after = _sock.recv(4096)
        except OSError:
            _after = b""          # the socket is gone: exactly what must happen
        check(b"411" in _d.split(b"\r\n", 1)[0] and b"HTTP/" not in _after,
              "no request is parsed out of a refused chunked body's leftover bytes",
              (_d[:40], _after[:40]))
    finally:
        _sock.close()

    # A-208: over the cap is 413 ("send less"), already checked above; A-210/A-211: a
    # body that stalls is 408, and a Content-Length that is not a count is 411 - the
    # two used to be one 400 that named neither.
    for _cl in ("abc", "-1", ""):
        _st, _hd, _bd, _ = probe("POST", "/api/run", {**TOK, "Content-Length": _cl})
        check(_st == 411 and b"Content-Length" in _bd,
              "a Content-Length of %r is refused with 411, naming the length"
              % _cl, (_st, _bd[:80]))
    _saved_dl = fb.WEB_BODY_DEADLINE
    fb.WEB_BODY_DEADLINE = 0.5
    try:
        _sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            _sock.sendall(b"POST /api/run HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                          b"X-Tinycmdr-Token: %s\r\nContent-Type: application/json\r\n"
                          b"Content-Length: 1000\r\n\r\n{\"m\""
                          % (port, token.encode()))
            _t0 = time.time()
            _d = b""
            while b"\r\n\r\n" not in _d:
                _c = _sock.recv(4096)
                if not _c:
                    break
                _d += _c
            _dt = time.time() - _t0
            check(b"408" in _d.split(b"\r\n", 1)[0] and _dt < 5,
                  "a body that stalls is answered 408 (a stall is not a syntax error)",
                  (round(_dt, 2), _d[:70]))
        finally:
            _sock.close()

        # ...and the deadline belongs to the WHOLE body (A-120): with the timeout
        # re-armed per recv, a client trickling one byte inside every window used to
        # hold the worker - and keep its body being read - for as long as it liked.
        # With a token the parse stalls to 408, without one the drain stalls to 401;
        # both must come back at the deadline, not at 40 x the trickle interval.
        for _hdr, _want, _why in ((b"X-Tinycmdr-Token: %s\r\n" % token.encode(), b"408",
                                   "at a parser"),
                                  (b"", b"401", "before auth")):
            _sock = socket.create_connection(("127.0.0.1", port), timeout=10)
            _stop = threading.Event()

            def _trickle(_sock=_sock, _stop=_stop):
                try:
                    while not _stop.is_set():
                        _sock.sendall(b"{")
                        time.sleep(0.2)
                except OSError:
                    pass

            try:
                _sock.sendall(b"POST /api/run HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                              b"Content-Type: application/json\r\n" % port
                              + _hdr + b"Content-Length: 40\r\n\r\n")
                threading.Thread(target=_trickle, daemon=True).start()
                _t0 = time.time()
                _d = b""
                while b"\r\n\r\n" not in _d:
                    _c = _sock.recv(4096)
                    if not _c:
                        break
                    _d += _c
                _dt = time.time() - _t0
                check(_want in _d.split(b"\r\n", 1)[0] and _dt < 3,
                      "a body that trickles %s is cut off at the deadline, not fed "
                      "for ever" % _why,
                      (round(_dt, 2), _d[:70]))
            finally:
                _stop.set()
                _sock.close()
    finally:
        fb.WEB_BODY_DEADLINE = _saved_dl

    # A-212/A-213: loopback is exempt from the LAN cap but not from ALL limits - a
    # SECOND, larger cap bounds the thread count a local process can hold.
    _cap2 = getattr(srv, "MAX_CONN_EXEMPT", None)
    check(isinstance(_cap2, int) and _cap2 > srv.MAX_CONN,
          "loopback has its own, larger cap instead of no cap at all", _cap2)
    if isinstance(_cap2, int):
        class _FakeSock2:
            def __init__(self):
                self.sent, self.closed = b"", False

            def sendall(self, data):
                self.sent += data

            def close(self):
                self.closed = True

        _saved = srv._conn_live
        srv._conn_live = _cap2
        _fake = _FakeSock2()
        srv.process_request(_fake, ("127.0.0.1", 4242))
        srv._conn_live = _saved
        check(b"503" in _fake.sent and _fake.closed,
              "a loopback peer past the second cap is answered 503 like a LAN peer",
              _fake.sent[:60])

    # A-215: the pre-auth 403 used to carry the live port and a copy-pasteable
    # `ssh -N -L` tunnel recipe. `accepts` stays; the tunnel does not.
    _st, _hd, _bd, _ = probe("GET", "/api/tasks", {"Host": "evil.example"})
    _low = _bd.decode("latin1").lower()
    check(_st == 403 and "ssh -n -l" not in _low and "tunnel" not in _low,
          "the pre-auth 403 names no port and no tunnel recipe", (_st, _bd[:120]))

    # A-216/A-217: a bare (port-less) IPv6 Host and a trailing-dot FQDN Host are the
    # SAME box; both used to be refused.
    for _h in ("[::1]", "127.0.0.1.", "localhost."):
        _st, _, _, _ = probe("GET", "/api/tasks", {**TOK, "Host": _h})
        check(_st == 200, "Host %r is recognised as this box" % _h, _st)

    # A-219: a reverse proxy that presents an Origin without the page's port gets a
    # 403 that NAMES the disagreement, not one that only suggests web.host.
    _st, _, _bd, _ = probe("GET", "/api/tasks", {**TOK, "Origin": "http://127.0.0.1"})
    check(_st == 403 and b"disagree" in _bd and b"Origin" in _bd,
          "the origin-mismatch 403 explains that Origin and Host must agree",
          (_st, _bd[:140]))

    # A-221: a 401 that saw a stale cookie clears it, so a rotated token does not leave
    # the browser 401ing on a value nothing removes.
    _st, _hd, _bd, _ = probe("GET", "/api/tasks",
                             {"Cookie": "tinycmdr_token_%d=stale-from-before" % port})
    _sc = _hd.get("Set-Cookie") or ""
    check(_st == 401 and ("tinycmdr_token_%d=" % port) in _sc and "Max-Age=0" in _sc,
          "a 401 presenting a stale cookie clears it (Max-Age=0)", (_st, _sc))

    # A-223/A-224: query values are percent-decoded the way the page encodes them, and a
    # duplicated key keeps its FIRST value (what a log line shows).
    for _q, _want in (("caf%C3%A9", "café"), ("hello+world", "hello world"),
                      ("%25", "%"), ("a%2Bb", "a+b")):
        _st, _, _bd, _ = probe("GET", "/api/search?q=" + _q, TOK)
        try:
            _got = json.loads(_bd).get("query")
        except Exception:
            _got = None
        check(_st == 200 and _got == _want,
              "?q=%s decodes to %r" % (_q, _want), _got)
    _st, _, _bd, _ = probe("GET", "/api/search?q=first&q=second", TOK)
    check(json.loads(_bd).get("query") == "first",
          "a duplicated query key keeps the FIRST value", json.loads(_bd).get("query"))

    # A-225: keys are minted lowercase, so a shift-key slip reaches the conversation
    # that exists instead of naming a second, empty one - and the regex is lowercase.
    check(not fb._web_key_ok("WEB"),
          "an upper-case session key is refused by WEB_KEY_RX")
    _st, _, _bd, _ = probe("GET", "/api/session?key=WEB", TOK)
    check(_st == 200 and json.loads(_bd).get("key") == "web",
          "?key=WEB resolves to the existing conversation 'web'",
          json.loads(_bd).get("key"))

    # A-226: the request target is bounded; past it the answer is 414, not a walk of
    # every routing branch.
    _st, _, _bd, _ = probe("GET", "/" + "z" * 9000)
    check(_st == 414, "an over-long request target is refused with 414", (_st, _bd[:60]))

    # ---- A-235: a conversation belongs to the browser that made it ----------
    CA = {"X-Tinycmdr-Token": token, "X-Tinycmdr-Client": "suiteA"}
    CB = {"X-Tinycmdr-Token": token, "X-Tinycmdr-Client": "suiteB"}

    def _mk(hdr, title=""):
        _c, _b, _ = req("POST", "/api/sessions", hdr,
                        json.dumps({"op": "new", "title": title}).encode())
        return json.loads(_b)["key"] if _c == 200 else None

    key_a = _mk(CA, "A's own")
    check(bool(key_a) and fb.web_entry(key_a) is not None, "browser A made a conversation")
    check(req("POST", "/api/sessions", CB,
              json.dumps({"op": "rename", "key": key_a, "title": "hijacked"}).encode())[0] == 403,
          "a second browser cannot RENAME the first browser's conversation")
    check(req("POST", "/api/sessions", CB,
              json.dumps({"op": "open", "key": key_a}).encode())[0] == 403,
          "...nor open it as its own")
    # A SECOND conversation to aim a cross-client DELETE at, so the read/open guards
    # below grade a conversation nobody deleted.
    key_d = _mk(CA, "A's deletable")
    _del = req("POST", "/api/sessions", CB,
               json.dumps({"op": "delete", "key": key_d}).encode())
    check(_del[0] == 403 and fb.web_entry(key_d) is not None,
          "...nor DELETE it (that removes its runlog for good)", (_del[0], _del[1][:90]))
    _run_in = probe("POST", "/api/run", CB,
                    json.dumps({"message": "/tinycmdr help", "session": key_a}).encode())
    check(_run_in[0] == 403,
          "...nor start a run inside it (which would extend its transcript)",
          (_run_in[0], _run_in[2][:90]))
    # the refusal names whose it is
    check(b"another browser on this host" in _del[1],
          "the refusal says whose conversation it is", _del[1][:120])
    # READING stays open: the rail's shared view is documented, and A-236 keeps it
    _see = req("GET", "/api/sessions?all=1", CB)
    _keys = [s.get("key") for s in (json.loads(_see[1]).get("sessions") or [])]
    check(_see[0] == 200 and key_a in _keys,
          "a second browser can still READ every conversation on the host", (_see[0], key_a in _keys))
    # the OWNER still works, and the SHARED conversation (no owner) stays open
    check(req("POST", "/api/sessions", CA,
              json.dumps({"op": "rename", "key": key_a, "title": "mine"}).encode())[0] == 200,
          "the owning browser still renames its own conversation")
    shared = fb.web_new_session("")
    check(req("POST", "/api/sessions", CB,
              json.dumps({"op": "rename", "key": shared, "title": "shared"}).encode())[0] == 200,
          "the SHARED conversation (no owner) is still anyone's to rename")
    key_b = _mk(CB, "B's own")
    check(req("POST", "/api/sessions", CB,
              json.dumps({"op": "delete", "key": key_b}).encode())[0] == 200,
          "a browser still deletes its OWN conversation")
    check(req("POST", "/api/sessions", CA,
              json.dumps({"op": "open", "key": key_a}).encode())[0] == 200,
          "the owner still opens its own conversation")
    # ...and a caller with no client header - the installer's probe, curl, a script -
    # keeps driving the shared conversation exactly as before.
    _cli = probe("POST", "/api/run", TOK, json.dumps({"message": "/tinycmdr help"}).encode())
    check(_cli[0] == 200 and b'"immediate": true' in _cli[2].lower(),
          "a caller with no client header still drives the shared conversation",
          (_cli[0], _cli[2][:80]))

    # ---- A-236: the shared view is an accepted exposure, and it says so ------
    _see = req("GET", "/api/sessions?all=1", CA)
    _allkeys = [s.get("key") for s in (json.loads(_see[1]).get("sessions") or [])]
    check(_see[0] == 200 and key_a in _allkeys,
          "?all=1 lists every conversation on the host to any token holder (the "
          "documented shared view, an accepted exposure)")
    _html = req("GET", "/", limit=400000)[1].decode("utf-8", "replace")
    check("id=allclients" in _html and "not just this one" in _html,
          "the rail's toggle spells out that it means every browser on this host, "
          "not just this one")

    # ---- A-237: the per-client bound takes the pruned runlog with it ---------
    # The registry stopped at WEB_SESSION_MAX conversations per client, but the
    # pruned conversation's <key>.web.jsonl stayed on disk for ever.
    for _ in range(fb.WEB_SESSION_MAX):
        fb.web_new_session("suiteZ")
    _zk = [s.get("key") for s in fb._web_state()["sessions"]
           if (s.get("client") or "") == "suiteZ"]

    def _age(st):
        for _s in st["sessions"]:
            if (_s.get("client") or "") == "suiteZ" and _s.get("key") in _zk:
                _s["last_active"] = float(_zk.index(_s["key"]))
        return True

    fb._web_state(_age)
    _oldest, _kept = _zk[0], _zk[1]
    fb._web_runlog_path(_oldest).write_text(
        json.dumps({"run_id": "r", "lines": []}) + "\n", encoding="utf-8")
    fb._web_runlog_path(_kept).write_text(
        json.dumps({"run_id": "r2", "lines": []}) + "\n", encoding="utf-8")
    fb.web_new_session("suiteZ")            # the 51st: the oldest is pruned
    check(fb.web_entry(_oldest) is None,
          "the per-client bound drops the oldest conversation from the registry")
    check(not fb._web_runlog_path(_oldest).exists(),
          "...and deletes its runlog with it, so sessions/ cannot grow without bound",
          str(fb._web_runlog_path(_oldest)))
    check(fb._web_runlog_path(_kept).exists(),
          "...while a conversation inside the bound keeps its runlog")

    # ---- A-244: /api/steer is bound to the run's OWN browser ----------------
    # This route answers a parked question and approves a confirm-tier command,
    # so a run id seen in the shared rail must not be enough.
    run_a = fb._web_new_run("web")
    run_a.client = "suiteA"                 # a run browser A started
    row_a = run_a.opener("Approve the dangerous command?", ["yes", "no"], 30)
    _st, _b = req("POST", "/api/steer", CB,
                  json.dumps({"run_id": run_a.id, "message": "yes"}).encode())[:2]
    check(_st == 403 and row_a.get("answer") is None and not row_a["ev"].is_set(),
          "a second browser cannot answer another browser's parked question",
          (_st, _b[:120], row_a.get("answer")))
    check(b"another browser on this host" in _b,
          "the steer refusal names whose run it is", _b[:120])
    _st, _b = req("POST", "/api/steer", CA,
                  json.dumps({"run_id": run_a.id, "message": "yes"}).encode())[:2]
    check(_st == 200 and row_a.get("answer") == "yes",
          "the OWNING browser still answers it", (_st, _b[:80], row_a.get("answer")))
    run_s = fb._web_new_run("web")          # no client header was ever involved:
    row_s = run_s.opener("Confirm?", ["yes"], 30)   # the shared conversation, nobody's
    _st = req("POST", "/api/steer", CB,
              json.dumps({"run_id": run_s.id, "message": "yes"}).encode())[0]
    check(_st == 200 and row_s.get("answer") == "yes",
          "a run in the SHARED conversation is still answerable by any browser",
          (_st, row_s.get("answer")))

    # ---- A-119: /api/stop is bound to the run's OWN browser, and is truthful ---
    # The one mutating route with no owner check: a run id seen in the shared rail
    # was enough to cancel another browser's run, "stopping: true" came back for a
    # run already done, and a second stop drew a second "stop requested" line.
    run_119 = fb._web_new_run("web")
    run_119.client = "suiteA"
    _st, _b = req("POST", "/api/stop", CB,
                  json.dumps({"run_id": run_119.id}).encode())[:2]
    check(_st == 403 and not run_119.cancel.is_set()
          and not any(l["text"] == "stop requested" for l in run_119.lines),
          "a second browser cannot stop another browser's run",
          (_st, _b[:120], run_119.cancel.is_set()))
    check(b"another browser on this host" in _b,
          "the stop refusal names whose run it is", _b[:120])
    _st, _b = req("POST", "/api/stop", CA,
                  json.dumps({"run_id": run_119.id}).encode())[:2]
    check(_st == 200 and b'"stopping": true' in _b.lower() and run_119.cancel.is_set(),
          "the OWNING browser stops it", (_st, _b[:80]))
    _st, _b = req("POST", "/api/stop", CA,
                  json.dumps({"run_id": run_119.id}).encode())[:2]
    _asked = sum(1 for l in run_119.lines if l["text"] == "stop requested")
    check(_st == 200 and _asked == 1,
          "a second stop is idempotent: no second stop-requested line",
          (_st, _b[:80], [l["text"] for l in run_119.lines]))
    fb._finish_web_run(run_119, fb.RunReporter(fb.WebDestination(run_119), "web"),
                       "done before the stop")
    _st, _b = req("POST", "/api/stop", CA,
                  json.dumps({"run_id": run_119.id}).encode())[:2]
    check(_st == 409 and b"finished" in _b.lower(),
          "a run already done is not answered 'stopping'", (_st, _b[:80]))
    run_119s = fb._web_new_run("web")       # no client ever involved: nobody's
    _st = req("POST", "/api/stop", CB,
              json.dumps({"run_id": run_119s.id}).encode())[0]
    check(_st == 200 and run_119s.cancel.is_set(),
          "a run in the SHARED conversation is stoppable by any browser", _st)

    # ---- A-245: the answer is drawn exactly once ----------------------------
    # The dead `run.answered` latch read False for ever, so a run that had already
    # drawn its own final line got a SECOND one at the end.
    _r245 = fb._web_new_run("web")
    _d245 = fb.WebDestination(_r245)
    _d245.line("final", "the run drew this answer itself")
    fb._finish_web_run(_r245, fb.RunReporter(_d245, "web"), "a fallback answer")
    _finals = [l["text"] for l in _r245.lines if l["kind"] == "final"]
    check(_finals == ["the run drew this answer itself"],
          "a run that already drew a final answer gets no second copy", _finals)
    _r245b = fb._web_new_run("web")
    fb._finish_web_run(_r245b, fb.RunReporter(fb.WebDestination(_r245b), "web"),
                       "the only answer")
    _finalsb = [l["text"] for l in _r245b.lines if l["kind"] == "final"]
    check(_finalsb == ["the only answer"],
          "a run that drew no final line still gets its answer exactly once", _finalsb)

    # ---- A-246: a question's OUTCOME is written down -------------------------
    # A timeout left the ❓ line with nothing after it: a reload could not tell
    # waiting from timed-out from answered.
    _r246 = fb._web_new_run("web")
    _ans246 = fb.WebDestination(_r246).ask("Answer me?", ["yes", "no"], 1.0)
    _sys246 = [l["text"] for l in _r246.lines if l["kind"] == "system"]
    check(_ans246 is None
          and any("no answer within" in t and "closed" in t for t in _sys246),
          "a question nobody answers leaves a 'no answer within Ns' line", _sys246)
    # ...and the same for a question asked through the run's own ask_user door
    # (ask_operator): the web door had no post_done, so that path recorded nothing.
    _r246b = fb._web_new_run("web")
    _st246, _ = fb.ask_operator("web", "Anybody there?", ctx={"ask_door": _r246b},
                                timeout=1)
    _lt246 = [l["text"] for l in _r246b.lines]
    check(_st246 == "timeout" and any("no answer" in t.lower() for t in _lt246),
          "a question the door times out on records its outcome too", (_st246, _lt246))

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
        # ...and the guard must hold for a suite run BY HAND, not only under run_all: this
        # file starts the in-process server and used to rely on the runner's env (measured
        # 2026-10-05: `python tests/test_webui.py` auto-opened the operator's browser).
        # Every suite that can start a web surface must carry the guard itself.
        _leaky = []
        for _p in sorted((BASE / "tests").glob("test_*.py")):
            _text = _p.read_text(encoding="utf-8", errors="replace")
            if re.search(r"run_webui\(|start_web_surface\(", _text) \
                    and "TINYCMDR_NO_BROWSER" not in _text:
                _leaky.append(_p.name)
        check("every suite that can start the page carries the no-browser guard",
              not _leaky, _leaky)
    dt = time.time() - t0
    out = buf.getvalue()
    check(again is None and "already serving" in out and dt < 1.0,
          "a second start announces the running page (no bind retry)", round(dt, 2))
    check("#token=" not in out and "tinycmdr web" in out,
          "the captured second start prints no token either")
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

    # ---- the verb LOOKS before it promises (2026-10-10) ---------------------
    # A link printed for a page that is not answering resolved to nothing and read as
    # the product lying. Dark: the probe's verdict, rc 1, and no tokenized link.
    _s = socket.socket()
    _s.bind(("127.0.0.1", 0))
    dead = _s.getsockname()[1]
    _s.close()
    fb.CONFIG["web"] = {"enabled": True, "host": "127.0.0.1", "port": dead, "token": token}
    buf = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = fb._verb_web()
    out = buf.getvalue() + err.getvalue()
    check(rc == 1 and "no page is answering" in out and (":%d/" % dead) in out,
          "a dark page: the web verb says so, with the port's URL", out[-240:])
    check("#token=" not in out,
          "...and hands out no tokenized link for a page nobody serves", out[-240:])
    check(("start it the way this host does" in out) or ("restart the service" in out),
          "...and names the door that starts it", out[-240:])

    # the RECORD is what the reason quotes: a failed bind by its reason, a stale success
    # as stale (never dressed up as a failure)
    _reason_fn = getattr(fb, "_page_reason_sentence", None)
    check(callable(_reason_fn),
          "the build has the dark-page reason helper (the probe's other half)",
          "a pre-fix build has no reason helper")
    _d = fb._lane_state_read()
    _d.setdefault("lanes", {})["web"] = {
        "ok": False, "detail": "port %d is held by another process" % dead, "since": 1.0}
    fb.LANE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fb.LANE_STATE_FILE.write_text(json.dumps(_d), encoding="utf-8")
    _reason = _reason_fn() if callable(_reason_fn) else ""
    check("held by another process" in _reason,
          "a failed bind's record is what the dark-page reason quotes", _reason)
    _d["lanes"]["web"] = {"ok": True, "detail": "port %d" % dead, "since": 1.0}
    fb.LANE_STATE_FILE.write_text(json.dumps(_d), encoding="utf-8")
    _reason = _reason_fn() if callable(_reason_fn) else ""
    check("nothing answers now" in _reason and "held by another" not in _reason,
          "a STALE success is named as stale, never as a failure", _reason)

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
    check("#token=" not in out,
          "the announce off a terminal (a supervisor's captured log) carries no token",
          out[:240])
    check("the tokenless output names the door to the link",
          "tinycmdr web" in out, out[:240])

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    buf = _Tty()
    with contextlib.redirect_stdout(buf):
        fb._announce_web(8790, open_browser=False)
    check("#token=" + token in buf.getvalue(),
          "at a terminal the announce still hands out the tokenized link",
          buf.getvalue()[:240])

    # ---- web.port 0 means "the OS picks", and the readers must say which port ----
    # Eight readers collapsed a configured 0 to 8790, so a 0 host printed links to a port
    # it is not serving - and on a box where something else holds 8790, to the wrong
    # program. The bound port is what run_webui records (lane_up "port N"), and every
    # reader names it through web_port_effective (measured 2026-10-06).
    _eff = getattr(fb, "web_port_effective", None)
    check(callable(_eff), "the build has the effective-port helper",
          "web_port_effective is missing (a pre-fix build)")

    def _named_port():
        return _eff() if callable(_eff) else None

    def _record(port, ok=True):
        """The lane record run_webui writes: lane_up on a bind, lane_down when it fails."""
        data = fb._lane_state_read()
        data.setdefault("lanes", {})["web"] = {"ok": ok, "since": 1.0,
                                               "detail": "port %d" % port}
        fb.LANE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fb.LANE_STATE_FILE.write_text(json.dumps(data), encoding="utf-8")

    BOUND = 18790                 # a port this suite never bound, so it cannot be a
                                  # coincidence with the default or the live server
    fb.CONFIG["web"] = {"enabled": True, "host": "127.0.0.1", "port": 0,
                        "token": token}
    _record(BOUND)
    check(_named_port() == BOUND,
          "web.port 0 + a bind record: the helper names the bound port", _named_port())
    check(fb.a2a_base_url() == "http://127.0.0.1:%d" % BOUND,
          "...and a2a's card URL names it, not 8790", fb.a2a_base_url())
    check((fb.a2a_card().get("supportedInterfaces") or [{}])[0].get("url")
          == "http://127.0.0.1:%d/a2a" % BOUND,
          "...and the published agent card advertises it",
          (fb.a2a_card().get("supportedInterfaces") or [{}])[:1])
    # The verb probes before it prints now, so the page the record names must actually
    # answer: one tiny server ON BOUND, then the link must name it.
    class _LanesOK(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b'{"ok": true, "lanes": {}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    _srvB = ThreadingHTTPServer(("127.0.0.1", BOUND), _LanesOK)
    threading.Thread(target=_srvB.serve_forever, daemon=True).start()
    try:
        buf = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = fb._verb_web()
        check(rc == 0 and (":%d/" % BOUND) in buf.getvalue(),
              "...and `tinycmdr web` prints that port (probed, then the link)",
              buf.getvalue()[:80] + err.getvalue()[:80])
    finally:
        _srvB.shutdown()
        _srvB.server_close()
    check(fb._web_port(fb.CONFIG["web"]) == (BOUND, None),
          "...and doctor's page line follows", fb._web_port(fb.CONFIG["web"]))

    # 0 with nothing recorded: the published default is a GUESS for a reader, and the
    # configured 0 stays on disk exactly as written.
    fb.LANE_STATE_FILE.unlink(missing_ok=True)
    check(_named_port() == 8790,
          "web.port 0 with no record falls back to 8790", _named_port())
    check(fb.a2a_base_url() == "http://127.0.0.1:8790",
          "...and a2a says 8790 there too", fb.a2a_base_url())
    check(fb.CONFIG["web"]["port"] == 0,
          "...and the configured 0 was not rewritten", fb.CONFIG["web"]["port"])

    # A real configured port outranks the record, unchanged behaviour.
    _record(BOUND)
    fb.CONFIG["web"]["port"] = 8788
    check(_named_port() == 8788 and fb.a2a_base_url() == "http://127.0.0.1:8788",
          "a configured 8788 wins over the record",
          "%s %s" % (_named_port(), fb.a2a_base_url()))

    # A FAILED record's detail names the port it TRIED - which is the 0 itself. Reading it
    # back as a bound port would hand out "port 0" as an address.
    fb.CONFIG["web"]["port"] = 0
    _record(0, ok=False)
    check(_named_port() == 8790,
          "a failed lane record is not read as a bound port", _named_port())
    fb.CONFIG["web"]["port"] = 0
    fb.LANE_STATE_FILE.unlink(missing_ok=True)

    # ---- A-124: the port-holder lookup can actually answer -----------------------
    # web_busy_note ran "(ss || netstat -ltnp) | grep"; macOS has no ss, and its
    # netstat -p refuses without a protocol argument and prints no pids - so on
    # the platform the verb exists for, the holder could never be named. lsof is
    # what answers there; a box with no lookup tool at all still gets a plain None
    # (the note is written for that case), never a guess.
    _holder124 = fb._port_holder(port)
    if os.name == "nt" or sys.platform == "darwin" \
            or shutil.which("lsof") or shutil.which("ss") or shutil.which("netstat"):
        check(_holder124 is not None and str(os.getpid()) in str(_holder124),
              "the port-holder lookup names the process listening on the port",
              _holder124)
    else:
        check(_holder124 is None,
              "with no lookup tool installed the holder is a plain None, not a guess",
              _holder124)

    # =====================================================================
    # run 13, pass C (A-227 .. A-261): the POST chain, the run buffer, the
    # assets and the CLI verbs. Every fix below has its check fail against a
    # pre-fix build (TINYCMDR_SRC); the refutations are pinned as behaviour.
    # =====================================================================

    # ---- A-121: /a2a checks the token BEFORE the body, and a body refusal keeps
    # its own status - `_body() or {}` fed 411/413/408 refusals to the JSON-RPC
    # path as an empty request, which answered "Invalid Request" for a body it had
    # already refused.
    fb.CONFIG["web"]["a2a"] = True
    try:
        _st121, _hd121, _bd121, _ = probe(
            "POST", "/a2a", {**TOK, "Content-Length": "abc"})
        check(_st121 == 411 and b"Content-Length" in _bd121,
              "a /a2a body with no usable length is refused 411, not read as empty",
              (_st121, _bd121[:90]))
        _st121b, _hd121b, _bd121b, _ = probe("POST", "/a2a", TOK, b"[]")
        check(_st121b == 400 and b"expected a JSON object body" in _bd121b,
              "a /a2a body that is not a JSON object is refused in the door's words",
              (_st121b, _bd121b[:90]))
        _st121c, _hd121c, _bd121c, _ = probe("POST", "/a2a", None,
                                             b'{"jsonrpc": "2.0", "id": 1}')
        check(_st121c == 401 and b"unauthorized" in _bd121c,
              "an unauthenticated /a2a call is still refused, before its body",
              (_st121c, _bd121c[:90]))
    finally:
        del fb.CONFIG["web"]["a2a"]

    # ---- A-227: naming YOUR OWN conversation runs there, not in the shared one --
    # A client whose message is resolved to the shared "web" conversation IS steered
    # into the run going there - that conversation is documented as nobody's. The
    # audit's shape (a caller that named its OWN conversation) does not reproduce.
    _real_wd = fb._web_drive
    _wd_calls = []
    fb._web_drive = lambda run, text: _wd_calls.append((run.session_key, text))
    _own227 = fb.web_new_session("suite227", "mine")
    _live227 = fb._web_new_run("web")
    _live227.add("you", "shared run going")
    _st227, _hd227, _bd227, _ = probe(
        "POST", "/api/run",
        {**TOK, "X-Tinycmdr-Client": "suite227", "Content-Type": "application/json"},
        json.dumps({"message": "hello mine", "session": _own227}).encode())
    fb._web_drive = _real_wd
    check(_st227 == 200 and b'"busy": false' in _bd227
          and _wd_calls == [(_own227, "hello mine")]
          and not any("mid-run" in l["text"] for l in _live227.lines),
          "a run named for your own conversation starts there; the shared one is untouched",
          (_st227, _bd227[:80], _wd_calls))
    _live227.done = True

    # ---- A-228/229/230: /api/chat is ONE run, in the conversation it names ------
    # A stub driver, held open on a gate, so the check can see the run registered and
    # a second POST refused - no model is called.
    _real_agent_run = fb.AGENT.run
    _seen_keys = []
    _seen_kwargs = []
    _gate = threading.Event()

    def _stub_run(key, text, *a, **kw):
        _seen_keys.append(key)
        _seen_kwargs.append(dict(kw))
        _gate.wait(5)
        return "stub answer in %s" % key

    fb.AGENT.run = _stub_run
    _chatkey = fb.web_new_session("suiteChat", "chat run")
    fb._web_state(lambda st: [s.update({"last_active": 1.0})
                              for s in st["sessions"]
                              if s.get("key") == _chatkey] or True)
    _first = {}

    def _chat_first():
        _first["out"] = req("POST", "/api/chat",
                            {**TOK, "X-Tinycmdr-Client": "suiteChat",
                             "Content-Type": "application/json"},
                            json.dumps({"message": "hello chat",
                                        "session": _chatkey}).encode())
    _t = threading.Thread(target=_chat_first)
    _t.start()
    for _ in range(400):
        if fb._web_active_run(_chatkey) is not None:
            break
        time.sleep(0.01)
    _active228 = fb._web_active_run(_chatkey)
    _second = req("POST", "/api/chat",
                  {**TOK, "X-Tinycmdr-Client": "suiteChat",
                   "Content-Type": "application/json"},
                  json.dumps({"message": "second", "session": _chatkey}).encode())
    _gate.set()
    _t.join(10)
    fb.AGENT.run = _real_agent_run
    check(_active228 is not None and _active228.session_key == _chatkey
          and _seen_keys == [_chatkey],
          "a /api/chat task runs the conversation the body NAMED, and is registered",
          (_seen_keys, _chatkey))
    check(_second[0] == 409 and b"already going" in _second[1],
          "a second /api/chat in the same conversation is refused while one runs",
          (_second[0], _second[1][:90]))
    check(_first.get("out", (-1,))[0] == 200
          and b"stub answer in %s" % _chatkey.encode() in _first["out"][1],
          "...and the first still answers with the run's text",
          _first.get("out", (None, b""))[:1])
    check((fb.web_entry(_chatkey) or {}).get("last_active", 0) > 1.0,
          "/api/chat touches the conversation in the registry (so the rail does not "
          "prune a conversation whose transcript is growing)",
          (fb.web_entry(_chatkey) or {}).get("last_active"))
    # A-111: the route hands the run its OWN doors - the cancel event the page's Stop
    # sets, the steer queue the page's steer writes into, and the reporter the stall
    # watchdog finishes through. It used to call AGENT.run bare, so all three were
    # dropped: Stop answered "stopping" and nothing read it, a steer was shown but never
    # reached the model, and a chat past 20 minutes was abandoned while its thread kept
    # working.
    _kw111 = _seen_kwargs[0] if _seen_kwargs else {}
    check(_kw111.get("cancel_event") is _active228.cancel,
          "a /api/chat run is wired to the run's own cancel event (Stop reaches it)",
          _kw111.get("cancel_event"))
    check(callable(_kw111.get("steer_cb")) and _kw111.get("steer_cb") == _active228.take_steer,
          "...and to the run's own steer queue", _kw111.get("steer_cb"))
    _active228.steer.append("a steered word")
    _steer_cb111 = _kw111.get("steer_cb")
    check(callable(_steer_cb111) and _steer_cb111() == [("web", "a steered word")],
          "...which drains what the page steered into it", _steer_cb111)
    check(getattr(_active228, "reporter", None) is not None,
          "...and the reporter is kept on the run for the watchdog")
    check(_kw111.get("ask_door") is None and _kw111.get("confirm_cb") is None,
          "...while a scripted caller still gets no ask or confirm door",
          (_kw111.get("ask_door"), _kw111.get("confirm_cb")))

    # ---- A-112: a Stop releases a parked question, and says so ----------------
    # WebDestination.ask waited its full wait (300 s) and then drew "no answer within
    # 300s", which misdescribes an operator's Stop as silence. The wait now polls the
    # run's cancel (the chat lane's ask_operator does the same every 3 s).
    _run112 = fb._web_new_run("suite112", "")
    _dest112 = fb.WebDestination(_run112)
    _asked112 = {}

    def _ask112():
        _t0 = time.time()
        _asked112["answer"] = _dest112.ask("confirm this?", ["yes", "no"], wait=30)
        _asked112["took"] = time.time() - _t0

    _t112 = threading.Thread(target=_ask112, daemon=True)
    _t112.start()
    time.sleep(0.3)
    _run112.cancel.set()
    _t112.join(5)
    _texts112 = [l.get("text") or "" for l in _run112.lines]
    check(not _t112.is_alive() and _asked112.get("took", 99) < 1.5
          and _asked112.get("answer") is None,
          "a parked question returns at once when the run is stopped, not after the wait",
          (_t112.is_alive(), _asked112))
    check(any("stopped" in t.lower() for t in _texts112)
          and not any("no answer within" in t for t in _texts112),
          "...and the closing line says stopped, not 'no answer within 30s'",
          _texts112[-3:])
    _run112.done = True
    # ---- A-113: a request may only NAME a page conversation ------------------
    # A sidecar stem ("web-abc.carry" - what the search panel shows for
    # sessions/web-abc.carry.json) and another lane's session key both passed
    # WEB_KEY_RX and were ADOPTED: the run wrote its history list over whatever the
    # name held on disk, and the rail owned it from then on.
    check(fb.web_resolve_session("", "web-abc.carry") == "web",
          "a sidecar stem is not adopted as a page conversation",
          fb.web_resolve_session("", "web-abc.carry"))
    check(fb.web_resolve_session("", "mattermost-chan-7") == "web",
          "...and neither is another lane's session key",
          fb.web_resolve_session("", "mattermost-chan-7"))
    check(fb.web_resolve_session("", "web") == "web",
          "the shared conversation is still nameable")
    _made113 = fb.web_new_session("suite113", "s113")
    check(fb.web_resolve_session("suite113", _made113) == _made113,
          "...and so is a registered page conversation",
          (fb.web_resolve_session("suite113", _made113), _made113))
    _real_run113 = fb.AGENT.run
    _keys113 = []

    def _stub113(key, text, *a, **kw):
        _keys113.append(key)
        return "stub 113 answer"

    fb.AGENT.run = _stub113
    try:
        _st113, _bd113, _hd113 = req("POST", "/api/run",
                                     {**TOK, "X-Tinycmdr-Client": "suite113",
                                      "Content-Type": "application/json"},
                                     json.dumps({"message": "hello",
                                                 "session": "web-abc.carry"}).encode())
        for _ in range(400):
            if _keys113:
                break
            time.sleep(0.01)
    finally:
        fb.AGENT.run = _real_run113
    check(_keys113 == [_made113] and fb.web_entry("web-abc.carry") is None,
          "a run asked to start in a sidecar name runs in this browser's own "
          "conversation and registers nothing under that name",
          (_keys113, _made113, fb.web_entry("web-abc.carry"), _st113, _bd113[:200]))

    # ---- A-114: the LAN address is the ROUTE's, never the resolver's guess -----
    # A stock Debian/Ubuntu/Raspberry Pi OS answers its own hostname with 127.0.1.1,
    # so the resolver-first pick announced "http://127.0.1.1:8790/" and never put the
    # real LAN IP into the Host check; a Hyper-V/WSL/Docker vEthernet can sort before
    # the real NIC. The default-route address (a UDP connect, no packet sent) is the
    # answer, and ALL of 127/8 is skipped in the fallback.
    _real_out114 = getattr(fb, "_web_outbound_ip", None)
    _real_lh114 = fb._web_local_hosts
    _cache114 = set(fb._WEB_HOSTS_CACHE) if fb._WEB_HOSTS_CACHE is not None else None
    try:
        fb._web_local_hosts = lambda: {"127.0.1.1", "127.0.0.1", "localhost", "10.9.8.7"}
        fb._web_outbound_ip = lambda: "172.16.4.4"
        check(fb._web_lan_ip() == "172.16.4.4",
              "the LAN address announced is the default route's, not the resolver's "
              "first answer", fb._web_lan_ip())
        fb._web_local_hosts = lambda: {"127.0.1.1", "127.0.0.1", "localhost"}
        fb._web_outbound_ip = lambda: ""
        check(fb._web_lan_ip() == "",
              "...and without a route answer every 127/8 name is skipped (Debian's "
              "127.0.1.1 is not an address anyone can visit)", fb._web_lan_ip())
        if _cache114 is not None:
            fb._web_local_hosts = _real_lh114
            fb._WEB_HOSTS_CACHE.clear()
            fb._WEB_HOSTS_CACHE.update({"127.0.0.1", "localhost"})
            fb._web_outbound_ip = lambda: "172.16.4.4"
            fb._web_hosts_resolve()
            check("172.16.4.4" in fb._web_local_hosts(),
                  "the Host check knows this box's own route address, so a LAN visitor "
                  "typing the IP is not refused", sorted(fb._web_local_hosts())[:8])
    finally:
        fb._web_local_hosts = _real_lh114
        if _real_out114 is not None:
            fb._web_outbound_ip = _real_out114
        if _cache114 is not None:
            fb._WEB_HOSTS_CACHE.clear()
            fb._WEB_HOSTS_CACHE.update(_cache114)


    # ---- A-115: a hand-edited web.port cannot kill the start -------------------
    # int() on the value raised ValueError ("nope"), TypeError (null) and
    # OverflowError (a JSON 1e999) out of main() before any other lane started, and
    # 70000 passed int() and then failed the bind with OverflowError - which the bind
    # retry's `except OSError` never caught.
    _pint115 = getattr(fb, "_web_port_int", None)
    check(callable(_pint115) and _pint115("nope") == 8790,
          "a non-numeric web.port falls back to 8790", _pint115 and _pint115("nope"))
    check(callable(_pint115) and _pint115(None) == 8790,
          "...a null does too", _pint115 and _pint115(None))
    check(callable(_pint115) and _pint115(float("inf")) == 8790,
          "...and an overflowing one", _pint115 and _pint115(float("inf")))
    check(callable(_pint115) and _pint115(70000) == 8790,
          "...and one outside the port range", _pint115 and _pint115(70000))
    check(callable(_pint115) and _pint115(0) == 0 and _pint115("8791") == 8791,
          "...while 0 (let the OS pick) and a numeric string are honoured",
          (_pint115 and _pint115(0), _pint115 and _pint115("8791")))
    # and the START path itself survives one: the real server may bind 8790, so the
    # check accepts None (a genuinely held port) as long as nothing raised.
    _saved_web115 = json.loads(json.dumps(fb.CONFIG.get("web") or {}))
    _saved_stall115 = fb._web_stall_minutes
    fb._web_stall_minutes = lambda kind: 0
    fb.CONFIG["web"] = {"enabled": True, "host": "127.0.0.1", "port": "nope",
                        "token": token}
    _bad115, _err115 = None, ""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            _bad115 = fb.run_webui()
    except BaseException as e:                      # the pre-fix shape: it raised out
        _bad115, _err115 = None, "%s: %s" % (type(e).__name__, e)
    finally:
        fb._web_stall_minutes = _saved_stall115
        fb.CONFIG["web"] = _saved_web115
    check(_err115 == "" and (_bad115 is None
                             or _bad115.server_address == ("127.0.0.1", 8790)),
          "a start with a hand-edited web.port binds the default instead of dying",
          (_bad115 and _bad115.server_address, _err115))
    if _bad115 is not None:
        try:
            _bad115.shutdown()
            _bad115.server_close()
        except Exception:                                        # noqa: BLE001
            pass

    # ---- A-116: '::1' binds, and an unresolvable host says so ------------------
    # QuietServer inherited AF_INET, so web.host "::" (a wildcard in several places)
    # could never bind and the gaierror came back as "another process holds the port".
    _saved_web116 = json.loads(json.dumps(fb.CONFIG.get("web") or {}))
    _sink116 = _Collect()
    fb.log.addHandler(_sink116)
    try:
        fb.CONFIG["web"] = {"enabled": True, "host": "::1", "port": 0, "token": token}
        _v6err116, _srv116 = "", None
        try:
            _srv116 = fb.run_webui()
        except BaseException as e:               # the pre-fix shape: a gaierror escapes
            _v6err116 = "%s: %s" % (type(e).__name__, e)
        check(_srv116 is not None and _srv116.server_address[1] > 0,
              "a v6 host ('::1') binds instead of dying with a gaierror",
              (_v6err116, _srv116))
        if _srv116 is not None:
            try:
                _srv116.shutdown()
                _srv116.server_close()
            except Exception:                                    # noqa: BLE001
                pass
        fb.CONFIG["web"] = {"enabled": True, "host": "no-such-host.invalid", "port": 0,
                            "token": token}
        _sink116.lines.clear()
        _t0 = time.time()
        _bad116 = fb.run_webui()
        _took116 = time.time() - _t0
        check(_bad116 is None and _took116 < 3.0
              and any("does not resolve" in ln for ln in _sink116.lines),
              "a host that does not resolve is refused at once, in words (not six "
              "retries ending in 'held by another process')",
              (_took116, _sink116.lines[-2:]))
    finally:
        fb.log.removeHandler(_sink116)
        fb.CONFIG["web"] = _saved_web116

    # ---- A-117: live runs are capped host-wide --------------------------------
    # _web_register_run evicted only DONE runs, so the table's bound did nothing while
    # runs were live: the key space is open, so N keys meant N agent threads against
    # the LLM endpoint (and with 40+ live runs a finished one was evicted before its
    # page made the final poll).
    _cap117 = getattr(fb, "WEB_RUN_LIVE_MAX", None)
    _saved_runs117 = dict(fb.WEB_RUNS)
    _real_run117 = fb.AGENT.run

    def _stuff_live117():
        fb.WEB_RUNS.clear()
        for _i in range(_cap117 or 8):
            _r = fb.WebRun("caprun%02d" % _i, "capkey%02d" % _i, "")
            fb.WEB_RUNS[_r.id] = _r

    try:
        fb.AGENT.run = lambda key, text, *a, **kw: "cap probe answer"
        _stuff_live117()
        _got117 = fb._web_claim_run("fresh-key-117", "")
        check(bool(_cap117) and _got117 == (None, "cap"),
              "a fresh conversation cannot start a run when the host is at its live "
              "cap", (_cap117, _got117))
        _st117, _bd117, _hd117 = req("POST", "/api/run",
                                     {**TOK, "Content-Type": "application/json"},
                                     json.dumps({"message": "cap probe",
                                                 "session": "web"}).encode())
        check(_st117 == 429 and b"too many runs" in _bd117,
              "...and the route says so (429), not a crash", (_st117, _bd117[:120]))
        fb.WEB_RUNS["caprun00"].done = True
        _ok117 = fb._web_claim_run("fresh-key-117b", "")
        check(_ok117[1] is None and _ok117[0] is not None,
              "...while a slot freed by a finished run is usable at once", _ok117)
        if _ok117[0] is not None:
            _ok117[0].done = True
    finally:
        fb.AGENT.run = _real_run117
        fb.WEB_RUNS.clear()
        fb.WEB_RUNS.update(_saved_runs117)
    # ...and the claim is ATOMIC: three simultaneous chats in one conversation start
    # exactly one run. The look and the registration used to be two steps, so all
    # three passed the check and all three ran (the measured shape of A-228).
    #
    # Three REAL requests arriving together is not something this suite can schedule -
    # on a Windows runner they arrived after the first run had already finished, so each
    # claimed the now-free conversation and three SEQUENTIAL 200s read as three
    # simultaneous runs (CI, 2026-10-08, the false red this replaces). So the claim's own
    # gap is widened instead: a run takes 500ms to construct, which sits INSIDE the
    # critical section of a one-step claim and BEFORE the registration of a two-step one.
    # A two-step claim then lets all three callers through (all three hold the run open,
    # the wait below times out, and the check reads three runs and three 200s) on any
    # platform and any scheduler - while a one-step claim refuses the two latecomers
    # however they are scheduled. The winner is held open until the other two have
    # ANSWERED, so nothing here depends on how fast a request reaches the server.
    _RealWebRun = fb.WebRun
    _gate2 = threading.Event()
    _racekey = fb.web_new_session("suiteRace", "race")
    _race_codes = []
    _race_starts = []

    class _SlowRun(_RealWebRun):
        """A run that takes a moment to exist: the window a two-step claim leaves open."""

        def __init__(self, *a, **kw):
            time.sleep(0.5)
            super().__init__(*a, **kw)

    def _stub_race(key, text, *a, **kw):
        _race_starts.append(key)
        _gate2.wait(30)
        return "ok"

    fb.WebRun = _SlowRun
    fb.AGENT.run = _stub_race

    def _hit():
        _race_codes.append(req("POST", "/api/chat",
                               {**TOK, "X-Tinycmdr-Client": "suiteRace",
                                "Content-Type": "application/json"},
                               json.dumps({"message": "go",
                                           "session": _racekey}).encode())[0])
    _ts2 = [threading.Thread(target=_hit) for _ in range(3)]
    for _t2 in _ts2:
        _t2.start()
    _deadline = time.time() + 8           # under the client's own 20s timeout, so a
    while time.time() < _deadline and len(_race_codes) < 2:   # two-step build answers
        time.sleep(0.02)                  # 200s rather than timing the callers out
    _gate2.set()
    for _t2 in _ts2:
        _t2.join(30)
    fb.AGENT.run = _real_agent_run
    fb.WebRun = _RealWebRun
    check(_race_codes.count(200) == 1 and _race_codes.count(409) == 2
          and len(_race_starts) == 1,
          "three simultaneous /api/chat calls in one conversation start exactly one run",
          (_race_codes, "runs started: %d" % len(_race_starts)))

    # ---- A-231: a platform-reserved upload name, and a write that fails ---------
    check("*" not in fb._web_safe_name("report*final.pdf")
          and ":" not in fb._web_safe_name("a:b.txt")
          and "|" not in fb._web_safe_name("a|b.txt")
          and '"' not in fb._web_safe_name('a"b.txt'),
          "an upload name cannot carry a platform-reserved character",
          fb._web_safe_name("report*final.pdf"))
    _ts231 = int(time.time())
    for _off in range(4):
        (STAGE / "uploads" / ("%d_blocked.txt" % (_ts231 + _off))).mkdir(exist_ok=True)
    _st231, _hd231, _bd231, _ = probe("POST", "/api/upload?name=blocked.txt", TOK,
                                      b"payload")
    check(_st231 == 400 and b"cannot be stored" in _bd231,
          "an upload name that cannot be written ANSWERS; it does not drop the socket",
          (_st231, _bd231[:90]))

    # ---- A-232: a truncated stored name is reported back ------------------------
    _st232, _hd232, _bd232, _ = probe(
        "POST", "/api/upload?name=" + urllib.parse.quote("q" * 300 + ".txt"), TOK, b"x")
    _j232 = json.loads(_bd232)
    check(_st232 == 200 and _j232.get("name_note")
          and len(_j232.get("name", "")) < 200,
          "a rewritten/truncated upload name is named in the reply",
          (_st232, _j232.get("name"), _j232.get("name_note")))

    # ---- A-123: the upload-name cut counts BYTES, not characters -----------------
    # NAME_MAX counts bytes, and 120 characters of Han is 360 of them: with the
    # "%d_" prefix the write failed ENAMETOOLONG on Linux instead of being cut.
    _cjk123 = "\u6f22" * 100
    _safe123 = fb._web_safe_name(_cjk123)
    check(0 < len(_safe123.encode("utf-8")) <= 120,
          "a long non-ASCII upload name is cut to 120 BYTES",
          (len(_safe123), len(_safe123.encode("utf-8"))))
    _st123, _hd123, _bd123, _ = probe(
        "POST", "/api/upload?name=" + urllib.parse.quote(_cjk123), TOK, b"cjk")
    _j123 = json.loads(_bd123) if _st123 == 200 else {}
    check(_st123 == 200 and _j123.get("bytes") == 3,
          "an upload with that name lands instead of failing ENAMETOOLONG",
          (_st123, _bd123[:120]))

    # ---- A-238: the registry as a whole is bounded ------------------------------
    _regmax = getattr(fb, "WEB_REGISTRY_MAX", None)
    # the shared conversation, as a header-less script makes it - and OLD, so a bound
    # that ignored the protection would take it
    fb.web_touch("web")
    fb._web_state(lambda st: [s.update({"last_active": 1.0})
                              for s in st["sessions"]
                              if s.get("key") == "web"] or True)
    for _i in range(230):
        fb.web_new_session("flood%d" % (_i % 7))
    _regn = len(fb._web_state()["sessions"])
    check(bool(_regmax) and _regmax <= 300 and _regn <= _regmax,
          "the conversation registry is bounded in AGGREGATE, not only per client",
          (_regmax, _regn))
    check(fb.web_entry("web") is not None,
          "...and the SHARED conversation is never the one the bound takes")
    # ...and the OTHER door: a header-less script starting a run in a conversation
    # nobody registered goes through web_touch, which appends the entry.
    for _i in range(30):
        fb.web_touch("invented-%d" % _i)
    _regn2 = len(fb._web_state()["sessions"])
    check(bool(_regmax) and _regn2 <= _regmax,
          "...and a run in an invented conversation cannot grow it either", _regn2)

    # ---- A-239: the client id is injective past the cut -------------------------
    check(fb._web_client({"X-Tinycmdr-Client": "a" * 40})
          != fb._web_client({"X-Tinycmdr-Client": "a" * 32 + "b"}),
          "two client ids differing past the 32-character cut are different ids")
    check(fb._web_client({"X-Tinycmdr-Client": "3f2a0b1c9d8e7f60"})
          == "3f2a0b1c9d8e7f60",
          "a clean id (what the page mints) is returned unchanged")

    # ---- A-240: a blank rename is refused, not applied --------------------------
    _k240 = fb.web_new_session("suite240", "keep me")
    _st240, _hd240, _bd240, _ = probe(
        "POST", "/api/sessions",
        {**TOK, "X-Tinycmdr-Client": "suite240", "Content-Type": "application/json"},
        json.dumps({"op": "rename", "key": _k240, "title": "  "}).encode())
    check(_st240 == 400 and (fb.web_entry(_k240) or {}).get("title") == "keep me",
          "a rename to blank is refused and leaves the title alone",
          (_st240, (fb.web_entry(_k240) or {}).get("title")))

    # ---- A-241/242/243: the run buffer's identity, view and out-of-range --------
    _r241 = fb._web_new_run("buf241")
    _r241.add("you", "line0")
    _r241.add("system", "line1")
    _r241.add("final", "line2")
    _uids_241 = [l["uid"] for l in _r241.lines]
    _r241.drop_line(0)
    check([l["uid"] for l in _r241.lines] == _uids_241[1:]
          and [l["i"] for l in _r241.lines] == [0, 1],
          "drop_line renumbers the index but keeps every uid the page holds",
          [(l["i"], l["uid"]) for l in _r241.lines])
    _v242 = _r241.view(1, 0)
    check(not ({l["uid"] for l in _v242["lines"]}
               & {l["uid"] for l in _v242["updates"]}),
          "view() never reports one line as both new and updated", _v242)
    _sink243 = _Collect()
    fb.log.addHandler(_sink243)
    _r243 = fb._web_new_run("buf243")
    _r243.add("you", "one")
    _r243.set_line(999, "final", "x")
    _r243.drop_line(500)
    fb.log.removeHandler(_sink243)
    check(any("out of range" in m for m in _sink243.lines),
          "an out-of-range set_line/drop_line leaves a trace in the log",
          _sink243.lines[:2])

    # ---- A-247: a checkpoint APPENDS; the read dedupes --------------------------
    _k247 = "log247suite"
    fb._web_runlog_path(_k247).unlink(missing_ok=True)
    for _i in range(3):
        fb.web_runlog_append(_k247, "r%d" % _i, _i,
                             [{"i": 0, "uid": "u", "kind": "final", "text": "x",
                               "r": 1}])
    _lines247 = len(fb._web_runlog_path(_k247).read_text(encoding="utf-8").splitlines())
    fb.web_runlog_append(_k247, "r2", 2,
                         [{"i": 0, "uid": "u", "kind": "final", "text": "grew",
                           "r": 2}])
    _lines247b = len(fb._web_runlog_path(_k247).read_text(encoding="utf-8").splitlines())
    _recs247 = fb.web_runlog(_k247)
    check(_lines247b == _lines247 + 1,
          "a runlog checkpoint appends one record instead of rewriting the whole file",
          (_lines247, _lines247b))
    check(len(_recs247) == 3 and _recs247[-1]["lines"][0]["text"] == "grew",
          "and the appended repeat is the NEWEST record for its run id, not a duplicate",
          [r.get("run_id") for r in _recs247])

    # ---- A-248: the transcript is bounded by the runlog's cap -------------------
    _k248 = "log248suite"
    fb._web_runlog_path(_k248).unlink(missing_ok=True)
    for _i in range(fb.WEB_RUNLOG_KEEP + 5):
        fb.web_runlog_append(_k248, "s%03d" % _i, _i,
                             [{"i": 0, "uid": "u", "kind": "final", "text": "x",
                               "r": 1}])
    _runs248 = len(fb.web_transcript(_k248)["runs"])
    check(_runs248 <= fb.WEB_RUNLOG_KEEP + 1,
          "the transcript a page paints on load is bounded by the run cap",
          _runs248)

    # ---- A-249: a transcript READ does not write the runlog ---------------------
    _k249 = "legacy249suite"
    fb.AGENT.histories[_k249] = [{"role": "user", "content": "hello"},
                                 {"role": "assistant", "content": "hi"}]
    fb._web_runlog_path(_k249).unlink(missing_ok=True)
    _t249 = fb.web_transcript(_k249)
    check(len(_t249["runs"]) == 1 and not fb._web_runlog_path(_k249).exists(),
          "reading a legacy conversation's transcript rebuilds it without writing",
          (len(_t249["runs"]), fb._web_runlog_path(_k249).exists()))

    # ---- A-250/251: downloads resume, and a grown file is refused honestly ------
    _dl250 = STAGE / "range.bin"
    _dl250.write_bytes(b"0123456789" * 4)
    _r250 = fb._web_new_run("dl250suite")
    _r250.add("file", str(_dl250))
    _u250 = urllib.parse.quote(_r250.lines[-1]["uid"], safe="")
    _st250, _hd250, _bd250, _ = probe(
        "GET", "/api/download?run=%s&uid=%s" % (_r250.id, _u250),
        {**TOK, "Range": "bytes=5-9"})
    check(_st250 == 206 and _bd250 == b"56789"
          and _hd250.get("Content-Range") == "bytes 5-9/40",
          "a Range download answers 206 with just those bytes",
          (_st250, _bd250, _hd250.get("Content-Range")))
    _st250b = probe("GET", "/api/download?run=%s&uid=%s" % (_r250.id, _u250),
                    {**TOK, "Range": "bytes=500-600"})[0]
    _st250c, _hd250c, _bd250c, _ = probe(
        "GET", "/api/download?run=%s&uid=%s" % (_r250.id, _u250), TOK)
    check(_st250b == 416, "an unsatisfiable range answers 416", _st250b)
    check(_st250c == 200 and len(_bd250c) == 40
          and _hd250c.get("Accept-Ranges") == "bytes",
          "no Range: the whole file, and it advertises that it can resume",
          (_st250c, len(_bd250c), _hd250c.get("Accept-Ranges")))
    _cap251 = STAGE / "cap251.bin"
    _cap251.write_bytes(b"x" * 10)
    _r251 = fb._web_new_run("dl251suite")
    _r251.add("file", str(_cap251))
    _u251 = urllib.parse.quote(_r251.lines[-1]["uid"], safe="")
    _oldmax251 = fb.SEND_FILE_MAX
    fb.SEND_FILE_MAX = 5
    try:
        _st251, _hd251, _bd251, _ = probe(
            "GET", "/api/download?run=%s&uid=%s" % (_r251.id, _u251), TOK)
    finally:
        fb.SEND_FILE_MAX = _oldmax251
    check(_st251 == 413 and b"too large" in _bd251,
          "a file that grew past the cap is refused with a sentence, not served",
          (_st251, _bd251[:80]))

    # ---- A-122: a download that runs short CLOSES -------------------------------
    # _file promised Content-Length, hit EOF mid-stream (the file shrank under the
    # open reader) and simply stopped writing: the connection stayed keep-alive, so
    # the client waited on bytes that were never coming. A truncated body must be
    # seen as a truncated body, which on the wire means the close.
    _big122 = STAGE / "shrink.bin"
    _big122.write_bytes(b"s" * (16 * 1024 * 1024))
    _r122 = fb._web_new_run("dl122suite")
    _r122.add("file", str(_big122))
    _u122 = urllib.parse.quote(_r122.lines[-1]["uid"], safe="")
    _sock = socket.create_connection(("127.0.0.1", port), timeout=15)
    try:
        _sock.sendall(b"GET /api/download?run=%s&uid=%s HTTP/1.1\r\n"
                      b"Host: 127.0.0.1:%d\r\nX-Tinycmdr-Token: %s\r\n\r\n"
                      % (_r122.id.encode(), _u122.encode(), port, token.encode()))
        _hdr = b""
        while b"\r\n\r\n" not in _hdr:
            _hdr += _sock.recv(4096)
        _m122 = re.search(rb"Content-Length: (\d+)", _hdr)
        _promised122 = int(_m122.group(1)) if _m122 else -1
        time.sleep(0.3)                    # the server fills the buffers and blocks
        os.truncate(_big122, 0)            # the file shrinks under the reader
        _got122 = 0
        _closed122 = False
        _sock.settimeout(5)
        try:
            while True:
                _chunk = _sock.recv(1 << 16)
                if not _chunk:
                    _closed122 = True
                    break
                _got122 += len(_chunk)
        except socket.timeout:
            pass
        check(_promised122 == 16 * 1024 * 1024 and 0 <= _got122 < _promised122
              and _closed122,
              "a download that runs short closes the connection instead of parking "
              "the client on a promise it can never fulfil",
              (_promised122, _got122, _closed122))
    finally:
        _sock.close()

    # ---- A-252/253/254: assets carry an ETag, survive a vanished file, and are
    #      templated once ------------------------------------------------------
    _orig_font253 = fb._web_font_path
    fb._web_font_path = lambda n: STAGE / "assets" / "fonts" / "vanished.woff2"
    try:
        _st252 = probe("GET", "/fonts/inter.woff2")[0]
    finally:
        fb._web_font_path = _orig_font253
    check(_st252 == 404,
          "a font that vanishes between the lookup and the read answers 404, not a "
          "dropped socket", _st252)
    _st253, _hd253, _bd253, _ = probe("GET", "/icon.png")
    _etag253 = _hd253.get("ETag")
    _st253b, _hd253b, _bd253b, _ = probe("GET", "/icon.png",
                                         {"If-None-Match": _etag253 or ""})
    check(_st253 == 200 and bool(_etag253) and _st253b == 304 and _bd253b == b"",
          "a static asset carries an ETag and an unchanged one revalidates 304",
          (_st253, _etag253, _st253b, len(_bd253b)))
    _art253 = STAGE / "assets" / "page-icon.png"
    _art253.write_bytes(b"\x89PNG\r\n\x1a\n" + b"new art")
    _st253c, _hd253c, _bd253c, _ = probe("GET", "/icon.png")
    _art253.unlink()
    check(_st253c == 200 and _hd253c.get("ETag") != _etag253,
          "new bytes on disk are served with a new ETag", _hd253c.get("ETag"))
    _c254 = {"n": 0}
    _tv254 = fb._web_theme_vars

    def _count254():
        _c254["n"] += 1
        return _tv254()

    fb._web_theme_vars = _count254
    try:
        _st254, _hd254, _bd254, _ = probe("GET", "/page.css")
        req("GET", "/page.css", limit=2000000)
    finally:
        fb._web_theme_vars = _tv254
    check(_c254["n"] <= 1,
          "an unchanged stylesheet is themed once, not once per request", _c254["n"])
    _st254b = probe("GET", "/page.css",
                    {"If-None-Match": _hd254.get("ETag") or ""})[0]
    check(bool(_hd254.get("ETag")) and _st254b == 304,
          "the stylesheet revalidates with a 304 instead of being re-read",
          (_hd254.get("ETag"), _st254b))

    # ---- A-233/234: uploads/ never ages out, and a same-second name overwrites -----
    # Both are in the ledger already (A-40 and A-39, ACCEPTED: the fix there is a random
    # suffix). Pinned so the disposition is visible in the suite, not re-litigated.
    _up_before = len(list((STAGE / "uploads").glob("*")))
    _dup_a = probe("POST", "/api/upload?name=pinned-dup.bin", TOK, b"one")
    _dup_b = probe("POST", "/api/upload?name=pinned-dup.bin", TOK, b"two")
    _up_after = len(list((STAGE / "uploads").glob("*")))
    _dupfiles = sorted((STAGE / "uploads").glob("*_pinned-dup.bin"))
    check(_dup_a[0] == 200 and _dup_b[0] == 200 and _dupfiles
          and _dupfiles[-1].read_bytes() == b"two"
          and _up_after >= _up_before + 1,
          "a same-second upload name overwrites, and uploads/ ages nothing out "
          "(A-233/234, ledger A-40/A-39 accepted)", (_up_before, _up_after))

    # ---- A-255: a non-ASCII download name rides RFC 5987 ------------------------
    _caf255 = STAGE / "caf\u00e9.pdf"
    _caf255.write_bytes(b"%PDF-1.4")
    _r255 = fb._web_new_run("dl255suite")
    _r255.add("file", str(_caf255))
    _u255 = urllib.parse.quote(_r255.lines[-1]["uid"], safe="")
    _st255, _hd255, _bd255, _ = probe(
        "GET", "/api/download?run=%s&uid=%s" % (_r255.id, _u255), TOK)
    _cd255 = _hd255.get("Content-Disposition") or ""
    check("filename*=UTF-8''caf%C3%A9.pdf" in _cd255
          and "filename=caf_.pdf" in _cd255,
          "a non-ASCII download name is carried by the RFC 5987 filename*",
          _cd255)

    # ---- A-256/257: the panels clamp and leak nothing ---------------------------
    # The file log is per-suite under the gate (run_all.py points TINYCMDR_LOG_FILE at a
    # fresh file), so seed the file this route reads: the check grades the CLAMP, not
    # who happened to log first (it read the tree's own log when run by hand).
    _lp256 = Path(fb._log_path)
    _lp256.parent.mkdir(parents=True, exist_ok=True)
    with _lp256.open("a", encoding="utf-8") as _fh256:
        _fh256.write("seed: the log panel has a line to clamp\n")
    _st256, _hd256, _bd256, _ = probe("GET", "/api/log?lines=99999", TOK)
    _j256 = json.loads(_bd256)
    check(_st256 == 200 and 0 < len(_j256.get("lines", [])) <= 500,
          "the log panel clamps ?lines to its bound instead of erroring",
          len(_j256.get("lines", [])))
    _st257, _hd257, _bd257, _ = probe("GET", "/api/health")
    check(_st257 == 200 and b"lanes" in _bd257
          and token.encode() not in _bd257,
          "health needs no token, names the lanes and leaks no token", _st257)

    # ---- A-258: the command box validates before it mutates ---------------------
    _st258, _bd258, _hd258 = req("POST", "/api/chat",
                                 {**TOK, "Content-Type": "application/json"},
                                 json.dumps({"message": "/model junk-model"}).encode())
    _rep258 = (json.loads(_bd258) or {}).get("reply") or ""
    check("Nothing switched" in _rep258 and "Known names" in _rep258,
          "the command box refuses a model no endpoint advertises (no silent "
          "re-pointing)", _rep258[:90])

    # ---- A-260: a port-0 host with a LIVE, RECORDED page announces --------------
    _saved260 = dict(fb.CONFIG["web"])
    fb.CONFIG["web"] = {"enabled": True, "host": "127.0.0.1", "port": 0,
                        "token": token}
    _record(port)
    _buf260 = io.StringIO()
    with contextlib.redirect_stdout(_buf260):
        _again260 = fb.start_web_surface(open_browser=False)
    if _again260 is not None:
        _again260.shutdown()
        _again260.server_close()
    fb.CONFIG["web"] = _saved260
    check(_again260 is None and "already serving" in _buf260.getvalue(),
          "web.port 0 + a recorded bind: a second start announces the live page "
          "instead of binding a second", _buf260.getvalue().strip()[:80])

    # ---- A-261: JSON replies are UTF-8, not \u-escaped --------------------------
    _st261, _hd261, _bd261, _ = probe("POST", "/api/chat",
                                      {**TOK, "Content-Type": "application/json"},
                                      json.dumps({"message": "/new"}).encode())
    check(_st261 == 200 and "\U0001f504".encode("utf-8") in _bd261
          and b"\\ud83d" not in _bd261,
          "a JSON reply is sent as UTF-8, not \\u-escaped", _bd261[:60])

    # ---- the registry's OTHER half is bounded too -------------------------------
    # `open` is one entry per client id, and a client id is a header the caller invents:
    # a kiosk, a browser with cleared localStorage or a rotating-client script grew it
    # without bound while `sessions` stayed under its own cap - the file is rewritten
    # whole on every mutation (measured 2026-10-07: 300 invented ids left 300 entries
    # and 0 conversations). Run last: the bound drops the oldest entries, which are
    # this suite's own earlier clients.
    _openmax = getattr(fb, "WEB_OPEN_MAX", None)
    for _oi in range(int(_openmax or 0) + 40):
        fb.web_set_open("kiosk-%d" % _oi, "web")
    _open_n = len(fb._web_state()["open"])
    check(bool(_openmax) and _open_n <= _openmax,
          "the 'last open conversation' map is bounded too, not one entry per client id",
          (_openmax, _open_n))
    check(fb.web_open_key("kiosk-%d" % (int(_openmax or 0) + 39)) is not None,
          "...and the newest client still has its key",
          fb.web_open_key("kiosk-%d" % (int(_openmax or 0) + 39)))
    fb.web_set_open("ghost-client", "web-gone-forever")
    check("ghost-client" not in fb._web_state()["open"],
          "...while an entry pointing at a conversation that is gone goes with it",
          sorted(fb._web_state()["open"])[:4])

    # ---- a WEDGED web run must not hold its conversation for ever ---------------
    # The chat lane's stall guard walks its own table; a browser run lives in WEB_RUNS,
    # where nothing timed out. A crash released the conversation (_finish_web_run runs in
    # a finally); a HANG did not - done stayed False, so every later /api/run and /api/chat
    # in that conversation was answered "busy" with no warning while the check-in cadence
    # kept saying "still on it" (measured 2026-10-07).
    _st_run = fb._web_new_run("stallsuite")
    _t0 = fb.now_mono()
    fb._web_stall_tick(warn_m=1, kill_m=5, now=_t0)          # first sighting: no verdict
    check(not _st_run.done and not _st_run.lines,
          "a just-seen web run is not touched by the watchdog", _st_run.lines)
    fb._web_stall_tick(warn_m=1, kill_m=5, now=_t0 + 61)
    check(not _st_run.done and any("Still on it" in l["text"] for l in _st_run.lines),
          "a quiet run is warned in its own transcript, not just in the log",
          [l["text"] for l in _st_run.lines])
    fb._web_stall_tick(warn_m=1, kill_m=5, now=_t0 + 122)
    check(sum(1 for l in _st_run.lines if "Still on it" in l["text"]) == 1,
          "...once, not on every tick",
          [l["text"] for l in _st_run.lines])
    _st_run.add("tool", "still working")
    fb._web_stall_tick(warn_m=1, kill_m=5, now=_t0 + 200)
    check(not _st_run.done and fb._web_active_run("stallsuite") is _st_run,
          "output resets the clock, so a working run is never abandoned")
    fb._web_stall_tick(warn_m=1, kill_m=5, now=_t0 + 200 + 301)
    check(_st_run.done and _st_run.cancel.is_set()
          and any("wedged" in l["text"] for l in _st_run.lines),
          "a wedged run is abandoned: cancelled, ended, and said so where the page looks",
          ([l["text"] for l in _st_run.lines], _st_run.done))
    check(fb._web_active_run("stallsuite") is None,
          "...which frees the conversation it was holding", fb.WEB_RUNS.keys())

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
