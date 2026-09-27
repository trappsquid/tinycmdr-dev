"""Session-lifecycle probes (a human managing conversations) + cleanup of the test sessions.

    python3 /tmp/probe-sessions.py

Phase 1 drives create -> rename -> open -> delete, plus key-injection attempts at the boundary
where a session key becomes a filename. Phase 2 deletes ONLY the sessions the case driver
created (keys read out of the case files the driver wrote), so the operator's own conversations are
never touched.
"""
import json
import pathlib
import re
import sys

import importlib.util
spec = importlib.util.spec_from_file_location(
    "dh", pathlib.Path(__file__).with_name("drive-web-cases.py"))
dh = __import__("importlib.util").util.module_from_spec(spec)
sys.modules["dh"] = dh
spec.loader.exec_module(dh)

dh.CLIENT = "session-probe"
OK = []


def probe(label, cond, got=""):
    OK.append(bool(cond))
    print("%s%-52s %s" % ("ok  " if cond else "??  ", label, str(got)[:90]))


def sessions():
    code, out = dh.call("/api/sessions")
    return out.get("sessions") or []


def op(payload):
    return dh.call("/api/sessions", payload)


print("== lifecycle ==")
before = len(sessions())
code, out = op({"op": "new", "title": "probe-lifecycle"})
key = out.get("key")
probe("new returns a key", code == 200 and bool(key), key)
probe("the new session is listed", any(s.get("key") == key for s in sessions()))
probe("its file exists under sessions/", (pathlib.Path.home() / "tinycmdr" / "sessions" / (key + ".json")).exists())
code, out = op({"op": "rename", "key": key, "title": "renamed by probe"})
titles = [s.get("title") for s in out.get("sessions") or []]
probe("rename lands", code == 200 and "renamed by probe" in titles, titles[-3:])
code, out = op({"op": "open", "key": key})
probe("open lands", code == 200 and out.get("open") == key, out.get("open"))
code, out = op({"op": "delete", "key": key})
probe("delete lands", code == 200 and out.get("deleted") == key)
probe("it is gone from the list", not any(s.get("key") == key for s in sessions()))
probe("its file is gone", not (pathlib.Path.home() / "tinycmdr" / "sessions" / (key + ".json")).exists())

print("== key injection at the filename boundary ==")
for bad in ("../../etc/passwd", "..", ".hidden", "a/b", "web-../../x", "", "web-ok/../x",
            "x" * 200, "web-..-..-etc-passwd"):
    code, out = op({"op": "delete", "key": bad})
    probe("delete %-24r -> 404, never 5xx" % bad[:24], code in (400, 404), "%s %s" % (code, str(out)[:60]))

print("== unknown op ==")
code, out = op({"op": "wibble", "key": "web-x"})
probe("unknown op -> 400", code == 400, code)

print("== cleanup: only the sessions my cases created ==")
keys = set()
for f in dh.OUT.glob("*.txt"):
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.startswith("SESSION   : web-"):
            keys.add(line.split(":", 1)[1].strip())
live = {s.get("key"): s.get("title") for s in sessions()}
mine = sorted(k for k in keys if k in live)
print("     %d session(s) to remove, %d left alone" % (len(mine), len(live) - len(mine)))
for k in mine:
    code, out = op({"op": "delete", "key": k})
    if code != 200:
        print("     could not delete %s: %s %s" % (k, code, str(out)[:80]))
left = sessions()
remaining = [k for k in mine if any(s.get("key") == k for s in left)]
probe("every test session deleted", not remaining, remaining[:4])
print("\n     conversation count: %d before my probes, %d now" % (before, len(left)))
print("     failures: %d" % OK.count(False))
