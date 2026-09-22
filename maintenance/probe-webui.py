"""Drive a run through a tinycmdr web UI and report the line buffer.

Usage: python probe-webui.py <base-url> <token> "<message>"
Prints one line per buffer line (index, kind, text) and a summary: how many
'you' lines and how many near-identical 'say' lines the page would have drawn.
"""
import json
import sys
import time
import urllib.request

base, token, msg = sys.argv[1], sys.argv[2], sys.argv[3]
H = {"Content-Type": "application/json", "X-Tinycmdr-Token": token}


def call(path, obj=None):
    req = urllib.request.Request(base + path,
                                 data=None if obj is None else json.dumps(obj).encode(),
                                 headers=H)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


j = call("/api/run", {"message": msg})
rid, since = j.get("run_id"), 0
print("run", rid, "busy", j.get("busy"), "steered", j.get("steered"))
t0 = time.time()
kinds = {}
while time.time() - t0 < 300:
    v = call("/api/events?run_id=%s&since=%d" % (rid, since))
    for l in v["lines"]:
        since = l["i"] + 1
        kinds[l["kind"]] = kinds.get(l["kind"], 0) + 1
        print("LINE %3d %-9s %s" % (l["i"], l["kind"], l["text"][:100]))
    if v["done"]:
        print("DONE %.1fs steps=%s" % (v["elapsed"], v["steps"]))
        break
    time.sleep(0.7)
total = sum(kinds.values())
print("SUMMARY lines=%d %s" % (total, kinds))
if kinds.get("you", 0) > 1:
    print("BAD: the operator's message is echoed %d times" % kinds["you"])
if kinds.get("say", 0) > 4:
    print("BAD: %d 'say' lines - narration is stacking, not growing" % kinds["say"])
if kinds.get("say", 0) <= 4:
    print("OK: narration did not stack")
