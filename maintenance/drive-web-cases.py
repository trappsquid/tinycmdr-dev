"""Drive the live bot through its web API, one FRESH session per case, and record what happens.

    python maintenance/drive-web-cases.py [case-name-filter]

Every case gets its own client id and a new web session (POST /api/sessions op=new), so nothing
is inherited except what the harness itself puts in the prompt - which is the point: these are the
use cases a person would type into the page, not a test suite's.

Each case records: the immediate reply (for fast-path commands), else the run's event stream until
it reports done, plus the tool calls, errors, duration and the usage footer. Results land in
$TINYCMDR_CASES_DIR/<case>.txt and a JSON summary beside them.
"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8787"
# Evidence lands here; point TINYCMDR_CASES_DIR somewhere durable if you want to keep it.
OUT = pathlib.Path(os.environ.get("TINYCMDR_CASES_DIR") or "/tmp/tinycmdr-web-cases")
OUT.mkdir(exist_ok=True)
TOKEN = ""
for line in (pathlib.Path.home() / "tinycmdr" / ".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("TINYCMDR_WEB_TOKEN="):
        TOKEN = line.split("=", 1)[1].strip()
CLIENT = "case-driver-%d" % int(time.time())
RESULTS = []
STATE = {}


def call(path, payload=None, client=True, timeout=60, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("X-Tinycmdr-Token", TOKEN)
    if client:
        req.add_header("X-Tinycmdr-Client", CLIENT)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body or "{}")
            except Exception:
                return r.status, {"_raw": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body or "{}")
        except Exception:
            return e.code, {"_raw": body}
    except Exception as e:                                        # noqa: BLE001
        return 0, {"_error": "%s: %s" % (type(e).__name__, e)}


def new_session(title):
    code, out = call("/api/sessions", {"op": "new", "title": title})
    if code != 200 or "key" not in out:
        return None, "could not create a session: %s %s" % (code, out)
    return out["key"], ""


def run_case(name, message, wait=180):
    """One case in its own fresh session. Returns a record dict."""
    rec = {"case": name, "message": message, "session": None, "reply": None,
           "events": [], "tool_calls": [], "errors": [], "done": False,
           "seconds": None, "raw_tail": "", "notes": []}
    started = time.time()
    global STATE
    STATE = {}
    key, err = new_session(name)
    if key is None:
        rec["errors"].append(err)
        RESULTS.append(rec)
        return rec
    rec["session"] = key

    code, out = call("/api/run", {"message": message, "session": key}, timeout=120)
    if code != 200:
        rec["errors"].append("POST /api/run -> %s %s" % (code, out))
        RESULTS.append(rec)
        return rec
    if out.get("immediate"):
        rec["reply"] = out.get("reply")
        rec["done"] = True
        rec["seconds"] = round(time.time() - started, 1)
        RESULTS.append(rec)
        return rec

    run_id = out.get("run_id")
    rec["run_id"] = run_id
    deadline = time.time() + wait
    seen = 0
    while time.time() < deadline:
        code, ev = call("/api/events?run_id=%s&since=%d" % (run_id, seen), timeout=30)
        if code != 200:
            rec["errors"].append("events -> %s %s" % (code, ev))
            break
        lines = ev.get("lines") or []
        seen += len(lines)
        for ln in list(lines) + list(ev.get("updates") or []):
            prev = STATE.get(ln.get("i"))
            if prev is None or ln.get("r", 0) >= prev.get("r", 0):
                STATE[ln.get("i")] = ln
            if ln not in lines:      # an update, not a new line: don't re-record it as new
                continue
            rec["events"].append(ln)
            kind = ln.get("kind")
            if kind == "tool":
                rec["tool_calls"].append((ln.get("name"), str(ln.get("text"))[:120]))
            if kind == "error":
                rec["errors"].append(str(ln.get("text"))[:200])
            if kind == "error" or "error" in str(ln.get("kind")):
                rec["errors"].append(str(ln.get("text"))[:200])
        if ev.get("done"):
            rec["done"] = True
            break
        time.sleep(1.0)
    rec["seconds"] = round(time.time() - started, 1)
    merged = sorted(STATE.values(), key=lambda l: l.get("i", 0))
    rec["events"] = merged                       # newest revision of every line, in order
    final = [l for l in merged
             if l.get("kind") in ("final", "answer", "bot", "assistant", "text")
             or l.get("kind") == "you"]
    final = [l for l in final if l.get("kind") != "you"]
    rec["reply"] = (final[-1].get("text") if final else "") or ""
    rec["raw_tail"] = "\n".join("%s: %s" % (l.get("kind"), str(l.get("text"))[:220])
                                for l in rec["events"][-14:])
    RESULTS.append(rec)
    return rec


def write_case_file(rec):
    p = OUT / ("%s.txt" % re.sub(r"[^a-z0-9_-]+", "-", rec["case"].lower()))
    lines = ["CASE      : %s" % rec["case"],
             "MESSAGE   : %s" % rec["message"],
             "SESSION   : %s" % rec["session"],
             "DONE      : %s   in %ss" % (rec["done"], rec["seconds"]),
             "TOOL CALLS: %d" % len(rec["tool_calls"])]
    for name, text in rec["tool_calls"]:
        lines.append("   - %s(%s)" % (name, text))
    if rec["errors"]:
        lines.append("ERRORS:")
        lines += ["   - %s" % e for e in rec["errors"]]
    lines.append("")
    lines.append("REPLY:" if rec["reply"] else "(no reply captured)")
    lines.append(rec["reply"] or "")
    lines.append("")
    lines.append("EVENT TAIL:")
    lines.append(rec["raw_tail"])
    p.write_text("\n".join(lines), encoding="utf-8")


CASES = [
    # Fast-path commands: no model involved, so these run whatever the endpoint is doing.
    ("verb-status", "/status"),
    ("verb-tasks", "/tasks"),
    ("verb-help", "/help"),
    ("verb-memory", "/memory"),
    ("verb-doctor", "/doctor"),
    ("verb-model", "/model"),
    ("verb-unknown", "/wibble"),
    ("verb-version", "/version"),
    ("verb-namespaced-status", "/tinycmdr status"),
    ("verb-undo-empty", "/undo"),
    ("verb-retry-empty", "/retry"),
    ("verb-new", "/new"),
    # Common human asks: these need the model.
    ("ask-disk-space", "What is using the most disk space on this machine right now?"),
    ("ask-port-listening", "Is anything listening on port 8080? Just answer yes or no with the process."),
    ("ask-log-check", "Check the last 20 lines of ~/tinycmdr/tinycmdr.log and tell me if anything looks wrong."),
    ("ask-out-of-scope", "What is the weather in Tokyo tomorrow?"),
    ("ask-remember", "Remember that the projector lives on the guest wifi."),
    ("ask-task-add", "Add a task to rotate the logs tomorrow morning."),
    ("ask-destructive", "Delete every file in /tmp/tc-scratch that I have not touched in a week."),
    ("ask-install", "Install jq with homebrew for me."),
    ("ask-session-recall", "Summarize what you have done in this conversation so far."),
    ("ask-after-new", "What was my previous question? Answer in one line."),
    ("ask-fresh-isolation", "What did I tell you in your other conversations today?"),
]


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    for i, (name, message) in enumerate(CASES, 1):
        if only and not any(o.lower() in name for o in only):
            continue
        print("=== [%d/%d] %s: %r" % (i, len(CASES), name, message[:60]), flush=True)
        rec = run_case(name, message)
        write_case_file(rec)
        print("    done=%s in %ss tools=%d errors=%d reply=%r"
              % (rec["done"], rec["seconds"], len(rec["tool_calls"]), len(rec["errors"]),
                 (rec["reply"] or "")[:120]), flush=True)
    (OUT / "summary.json").write_text(json.dumps(RESULTS, indent=2, default=str), encoding="utf-8")
    print("\nwrote %d case file(s) to %s" % (len(RESULTS), OUT))


if __name__ == "__main__":
    main()
