"""Restart tinycmdr on the Windows fleet boxes through its own web API.

Remote `schtasks /run` answers "The request is not supported" on this fleet, and
killing the process does NOT make the scheduled task relaunch it. The supported
route (fleet-access skill) is the agent's own /restart command, which re-execs
itself and is supervised from there.

Config (port + token) is read from each host's own config.json over the admin
share, so no secret is passed on a command line.

usage: python maintenance/fleet-web-restart.py [ip ...]   (default .20 and .9)
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WANT = "2.5.4"
HOSTS = sys.argv[1:] or ["a LAN address", "a LAN address"]


def host_cfg(ip):
    p = Path("//%s/C$/tinycmdr/config.json" % ip)
    cfg = json.loads(p.read_text(encoding="utf-8"))
    web = cfg.get("web") or {}
    return int(web.get("port") or 8787), web.get("token") or ""


def post(ip, port, token, path, obj, timeout=30):
    req = urllib.request.Request(
        "http://%s:%d%s" % (ip, port, path),
        data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json", "X-tinycmdr-Token": token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def health(ip, port):
    try:
        req = urllib.request.Request("http://%s:%d/api/health" % (ip, port))
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode() or "{}")
    except Exception as e:                      # down mid-restart is expected
        return {"ok": False, "error": str(e)}


for ip in HOSTS:
    try:
        port, token = host_cfg(ip)
    except Exception as e:
        print("%s: cannot read config: %s" % (ip, e))
        continue
    before = health(ip, port)
    print("%s: before v%s (port %d)" % (ip, before.get("version"), port))
    try:
        r = post(ip, port, token, "/api/chat", {"message": "/restart"})
        print("%s: /restart -> %s" % (ip, (r.get("reply") or r)[:90]))
    except Exception as e:
        print("%s: /restart call ended (%s) - normal for a self-restart" % (ip, type(e).__name__))
    t0 = time.time()
    while time.time() - t0 < 150:
        time.sleep(5)
        h = health(ip, port)
        if h.get("ok") and h.get("version"):
            state = "restarted" if time.time() - t0 > 3 else "answered"
            print("%s: up again after %.0fs, v%s %s" % (ip, time.time() - t0, h["version"],
                                                        "OK" if h["version"] == WANT else "STILL OLD"))
            break
    else:
        print("%s: no answer 150s after /restart" % ip)
