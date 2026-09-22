"""Drive a real run against a REMOTE Windows tinycmdr box and show its buffer.

usage: python maintenance/probe-remote-webui.py <ip> "<message>"
Reads the port and token from that host's own config.json over the admin share,
so no secret is passed on a command line.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

ip = sys.argv[1]
msg = sys.argv[2]
# The buffer carries '✓'/'✗' and '·', and this is run from a cp1252 console on
# Windows: without this the probe dies printing its own output (it raised
# UnicodeEncodeError on the first tool_done line, after the run had started).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                                # noqa: BLE001
    pass
cfg = json.loads(Path("//%s/C$/tinycmdr/config.json" % ip).read_text(encoding="utf-8"))
port = int(cfg["web"]["port"])
tok = cfg["web"]["token"]
base = "http://%s:%d" % (ip, port)
H = {"Content-Type": "application/json", "X-Tinycmdr-Token": tok}


def call(path, obj=None):
    req = urllib.request.Request(base + path,
                                 data=None if obj is None else json.dumps(obj).encode(),
                                 headers=H)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


print("health", call("/api/health"))
j = call("/api/run", {"message": msg})
rid, since = j["run_id"], 0
print("run", rid, "on", ip)
t0 = time.time()
kinds = {}
while time.time() - t0 < 300:
    v = call("/api/events?run_id=%s&since=%d" % (rid, since))
    for l in v["lines"]:
        since = l["i"] + 1
        kinds[l["kind"]] = kinds.get(l["kind"], 0) + 1
        print("LINE %3d %-9s %s" % (l["i"], l["kind"], l["text"][:95]))
    if v["done"]:
        print("DONE %.1fs steps=%s" % (v["elapsed"], v["steps"]))
        break
    time.sleep(1)
print("SUMMARY lines=%d %s" % (sum(kinds.values()), kinds))
if kinds.get("you", 0) > 1 or kinds.get("say", 0) > 4:
    print("BAD: still repeating")
else:
    print("OK: one message line, narration not stacked")
