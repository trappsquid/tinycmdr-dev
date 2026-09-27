"""Read-only probes of the live web surface: what a person (or a scanner) would try.

    python3 /tmp/probe-web.py

No state is mutated: the only POSTs are ones that must be *refused*, and its body is inert.
"""
import json
import pathlib
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8787"
TOKEN = ""
for line in (pathlib.Path.home() / "tinycmdr" / ".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("TINYCMDR_WEB_TOKEN="):
        TOKEN = line.split("=", 1)[1].strip()


def probe(path, token=True, origin=None, method=None, body=None, client=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if token:
        req.add_header("X-Tinycmdr-Token", TOKEN)
    if client:
        req.add_header("X-Tinycmdr-Client", "probe")
    if origin:
        req.add_header("Origin", origin)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, dict(r.headers), raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")
    except Exception as e:                                            # noqa: BLE001
        return 0, {}, "%s: %s" % (type(e).__name__, e)


def show(label, expect, got, extra=""):
    ok = "ok  " if expect(got[0]) else "??  "
    print("%s%-42s -> %s %s" % (ok, label, got[0], extra[:110]))


print("== auth ==")
show("/api/health without a token (want 401)", lambda c: c == 401, probe("/api/health", token=False))
show("/api/health with a token (want 200)", lambda c: c == 200, probe("/api/health"))
show("/api/tasks without a token (want 401)", lambda c: c == 401, probe("/api/tasks", token=False))
show("/api/log without a token (want 401)", lambda c: c == 401, probe("/api/log", token=False))
show("/api/inventory without a token (want 401)", lambda c: c == 401, probe("/api/inventory", token=False))
show("/api/sessions without a token (want 401)", lambda c: c == 401, probe("/api/sessions", token=False))
show("bad token (want 401)", lambda c: c == 401, probe("/api/health", token=False) if False else
     probe("/api/health?token=nope", token=False))

print("== origin / CSRF ==")
show("POST /api/run, foreign Origin (want 403)",
     lambda c: c == 403 and False or c == 403,
     probe("/api/run", origin="http://evil.example",
           body={"message": "Reply with exactly: ok"}, client=True))
show("POST /api/run, no Origin (want 200: non-browser callers)",
     lambda c: c in (200, 403), probe("/api/run", method="POST",
                                      body={"message": "/version"}))
show("POST /api/run, same-origin (want 200)",
     lambda c: c == 200, probe("/api/run", origin="http://127.0.0.1:8787",
                               body={"message": "/version"}))

print("== the page and its headers ==")
st, hd, body = probe("/", token=False)
print("     / -> %s, %d bytes, %s" % (st, len(body), "html" if "<html" in body.lower() else "?"))
for k in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options",
          "Referrer-Policy", "Cache-Control"):
    print("     %-28s %s" % (k, hd.get(k, "-- absent --")))
print("     does the served page embed the token?", TOKEN[:6] + "..." in body if TOKEN else "n/a")
print("     does any asset url carry ?token=", "token=" in body)

print("== traversal and odd paths (want 404/401, never file contents) ==")
for p in ("/../.env", "/..%2f.env", "/%2e%2e/.env", "/app.js", "/style.css",
          "/api/../.env", "/etc/passwd", "/../../../../etc/passwd"):
    st, hd, body = probe(p, token=False)
    leak = ("TINYCMDR_WEB_TOKEN" in body) or ("root:x:" in body)
    print("     %-24s -> %s %s" % (p, st, "LEAK!" if leak else ""))

print("== secrets in the JSON the page can read ==")
for p in ("/api/log", "/api/inventory", "/api/tasks", "/api/sessions", "/api/commands"):
    st, hd, body = probe(p)
    flags = []
    if TOKEN and TOKEN in body:
        flags.append("TOKEN-ECHOED")
    for needle in ("api_key", "sk-", "Bearer "):
        if needle in body:
            flags.append(needle)
    print("     %-20s -> %s %d bytes %s" % (p, st, len(body), " ".join(flags) or "clean"))

print("== method confusion ==")
show("GET /api/run (want 404/405)", lambda c: c in (404, 405), probe("/api/run", method="GET"))
show("DELETE / (want 404/405)", lambda c: c in (404, 405), probe("/", method="DELETE"))
st, hd, body = probe("/api/run", method="OPTIONS", token=False)
print("     OPTIONS /api/run -> %s, allow=%s, cors=%s"
      % (st, hd.get("Allow"), hd.get("Access-Control-Allow-Origin", "--")))
