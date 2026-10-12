"""/api/health stays open, but its payload has two tiers (A-2026-10-05-38).

The endpoint must answer a token-less `curl -sf` from anywhere, so it never needs a
token and never stops being 200. What it SAYS, though, is split: `pid`, the per-lane
map and `config_changed` describe this box and go only to a caller already ON it (a
loopback peer) or one holding the token; any other peer gets `ok` and `version` alone.

Offline and self-contained: loopback only, port 0, no model.

    python tests/test_fix_health.py

TINYCMDR_SRC points this at a reverted copy so the fix can be watched going red
(tests/run_all.py clears it for a normal run).
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

os.environ.setdefault("TINYCMDR_NO_BROWSER", "1")

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-fix-health"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
             STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_fix_health",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_fix_health"] = fb
spec.loader.exec_module(fb)

FAILS = []


def check(cond, what, detail=""):
    if not cond:
        FAILS.append(what)
        print("FAIL %s: %s" % (what, detail))
    else:
        print("ok   %s" % what)


def _payload(**kw):
    """health_payload with the ARGS the fix added; pre-fix takes none."""
    try:
        return fb.health_payload(**kw)
    except TypeError:
        return None


def _classify(peer):
    """The peer classifier; None when the build predates the fix."""
    fn = getattr(fb, "_peer_is_loopback", None)
    return None if fn is None else fn(peer)


def main():
    fb._browser_possible = lambda: False

    # ---- the peer classifier ------------------------------------------------
    # A loopback peer is the same trust boundary as this process; the v4-mapped form is
    # the SAME peer as 127.0.0.1 (a dual-stack v6 listener reports it for a v4 client).
    for peer, want in (("127.0.0.1", True), ("::1", True),
                       ("::ffff:127.0.0.1", True),
                       ("203.0.113.5", False), ("198.51.100.9", False),   # RFC 5737
                       ("172.16.4.4", False), ("8.8.8.8", False), ("", False)):
        got = _classify(peer)
        check(got is want, "_peer_is_loopback(%r) is %s" % (peer, want), got)

    # ---- the payload split, at the function --------------------------------
    # A non-token holder off-box gets the door answer and NOTHING about this box.
    off = _payload(authed=False, client_is_loopback=False)
    check(off is not None,
          "health_payload takes the (authed, client_is_loopback) tier inputs")
    if off is not None:
        check(set(off) == {"ok", "version"},
              "an off-box caller without the token gets ok+version only", sorted(off))
        check(isinstance(off.get("ok"), bool) and off.get("version") == fb.VERSION,
              "...as a boolean and this build's version", off)
        check("pid" not in off and "lanes" not in off and "config_changed" not in off,
              "...and no pid, lane map or config_changed", sorted(off))

    # A loopback caller without the token is the installer/monitor: full payload.
    near = _payload(authed=False, client_is_loopback=True)
    if near is not None:
        check({"ok", "version", "pid", "lanes", "config_changed"} <= set(near),
              "an unauthenticated loopback peer gets the full payload", sorted(near))
        check(isinstance(near.get("pid"), int),
              "...with this process's pid", near.get("pid"))

    # A token holder gets the full payload from anywhere.
    tok = _payload(authed=True, client_is_loopback=False)
    if tok is not None:
        check({"ok", "version", "pid", "lanes", "config_changed"} <= set(tok),
              "the token widens an off-box caller to the full payload", sorted(tok))

    # ---- the live route: the handler wires the split -----------------------
    token = "tok-fix-health-suite-0123456789"
    fb.CONFIG["web"] = {"enabled": True, "host": "127.0.0.1", "port": 0,
                        "token": token}
    srv = fb.run_webui()
    if srv is None:
        check(False, "the web surface starts with a token")
        print()
        print("%d check(s) failed" % len(FAILS))
        return 1
    port = srv.server_address[1]
    base = "http://127.0.0.1:%d" % port

    def req(path, headers=None):
        r = urllib.request.Request(base + path, method="GET",
                                   headers=headers or {})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read(4000) or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read(4000) or b"{}")

    try:
        # (a) unauthenticated, from loopback: the full payload.
        code, body = req("/api/health")
        check(code == 200, "a token-less loopback health probe answers 200", code)
        check("pid" in body and "lanes" in body and "config_changed" in body
              and body.get("version") == fb.VERSION,
              "...with the full payload for a caller on this box", sorted(body))

        # (b) the SAME request seen as a non-loopback peer: the minimal payload.
        # The peer address is the socket's; force the classification to off-box so the
        # handler's wiring (not a second code path) is what is exercised.
        real_peer = getattr(fb, "_peer_is_loopback", None)
        try:
            fb._peer_is_loopback = lambda peer: False
            code, body = req("/api/health")
            check(code == 200,
                  "an off-box token-less probe STILL answers 200 (curl -sf)", code)
            check(set(body) == {"ok", "version"},
                  "an off-box caller gets ok+version only", sorted(body))
            check("pid" not in body and "lanes" not in body
                  and "config_changed" not in body,
                  "...and no pid, lane map or config_changed", sorted(body))

            # ...but the token still widens it to the full payload off-box.
            code, body = req("/api/health", {"X-Tinycmdr-Token": token})
            check(code == 200 and "pid" in body and "lanes" in body,
                  "the token restores the full payload for an off-box caller",
                  sorted(body))
        finally:
            if real_peer is not None:
                fb._peer_is_loopback = real_peer
            elif hasattr(fb, "_peer_is_loopback"):
                del fb._peer_is_loopback
    finally:
        srv.shutdown()
        srv.server_close()

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all /api/health tier checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
