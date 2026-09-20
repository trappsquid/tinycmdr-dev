"""Offline checks for the local web UI: the no-chat-server surface.

The page is only useful if it shows what the agent is doing WHILE it works, so
most of these checks drive a real run through the HTTP endpoints and inspect the
line buffer the browser polls. Nothing here touches the network beyond
127.0.0.1, and no real model is called.

Three things are deliberate:
  * the model stub blocks on events the test controls, so "steer mid-run" and
    "stop mid-run" are deterministic instead of racing a fast stub;
  * the heartbeat ("generating") is checked at the unit level, because the real
    heartbeat is rate-limited to one every 4s and would make this suite slow;
  * /api/chat is checked too, because scripts and the fleet tooling use it.

    python tests/test_webui.py
"""
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import run_scenario  # noqa: E402

FAILS = []
TOKEN = "test-token-webui"


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


class FakeResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def tool_call_reply(name, args, cid="c1"):
    return {"choices": [{"message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": cid, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)}}]},
        "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def text_reply(text):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def make_stub(fb, seen, plan):
    """plan: list of callables(payload) -> response. Past the end: a text reply."""
    state = {"i": 0}

    def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                  stream=False):
        seen.append(json.loads(json.dumps(payload)))
        i = state["i"]
        state["i"] += 1
        fn = plan[i] if i < len(plan) else (lambda p: text_reply("extra turn"))
        return FakeResp(fn(payload))

    fb._post_watchdog = fake_post


def blob(payload):
    return "\n".join(str(m.get("content") or "") for m in payload.get("messages", []))


def _http_once(url, payload=None, token=TOKEN, timeout=15, client=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("X-tinycmdr-Token", token)
    if client is not None:
        req.add_header("X-tinycmdr-Client", client)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def http(url, payload=None, token=TOKEN, timeout=15, attempts=3, client=None):
    """One call, retried on a connection-level abort.

    Answering an unauthorized POST while its body was still being written made
    Windows reset the connection instead of delivering the 401 (the handler now
    drains the body first). A retry here keeps this suite from reporting a
    transient socket teardown as a defect, and a persistent one still fails."""
    last = None
    for i in range(attempts):
        try:
            return _http_once(url, payload, token, timeout, client)
        except (ConnectionError, OSError) as e:
            last = e
            time.sleep(0.2)
    return 0, {"error": f"connection: {last}"}


def get_text(url, token=TOKEN):
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-tinycmdr-Token", token)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode()


def wait_for(fn, timeout=30.0, interval=0.03):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return None


def collect(base, run_id, until_done=True, timeout=30.0):
    """Poll /api/events the way the page does, and return (lines, last state)."""
    since, lines, state = 0, [], {}
    end = time.time() + timeout
    while time.time() < end:
        code, j = http(f"{base}/api/events?run_id={run_id}&since={since}")
        if code != 200:
            return lines, {"error": code}
        for l in j.get("lines", []):
            since = l["i"] + 1
            lines.append(l)
        state = j
        if j.get("done"):
            break
        if not until_done:
            break
        time.sleep(0.05)
    return lines, state


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbweb-"))
    srv = None
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)

        if not hasattr(fb, "WEB_PAGE"):
            # the console build is generated with the whole chat/web layer cut
            # out, so there is nothing here to check. Skip, do not fail.
            print("skipped: this build has no web layer (console build)")
            return 0

        # the page's own contract: a bottom bar plus the four endpoints
        page = fb.WEB_PAGE
        for probe in ("id=bar", "id=log", "/api/run", "/api/events",
                      "/api/steer", "/api/stop", "id=stop"):
            check(probe in page, f"the page carries {probe}")

        # -- unit level: the heartbeat must not look like work ----------------
        run = fb.WebRun("unitrun", "web")
        run.on_progress("generating", "13.4 tok/s, 220 chars")
        check(run.lines == [], "a 'generating' heartbeat adds no line")
        check(run.steps == 0, "a heartbeat does not count as a tool call")
        check("tok/s" in run.status, "the heartbeat updates the status line instead")
        run.on_tool_done("shell", {}, "exit_code=1\nerror: no such file", 0.4)
        check(run.lines and run.lines[-1]["kind"] == "tool_fail",
              "a failed tool output renders as tool_fail")
        run.on_tool_done("shell", {}, "exit_code=0\nfine", 0.4)
        check(run.lines[-1]["kind"] == "tool_done", "...and a good one as tool_done")
        run.on_progress("shell", '{"command": "df -h"}')
        check(run.steps == 1 and run.lines[-1]["text"].startswith("shell("),
              "a real tool call adds a line and counts one step")

        # -- unit level: narration GROWS one line, it does not stack ----------
        # The callbacks deliver cumulative snapshots; appending each one painted
        # ~190 near-identical lines for a 5-second run on a live box.
        nar = fb.WebRun("narration", "web")
        nar.on_narration("The sky is blue", False, True)
        nar.on_narration("The sky is blue", False, False)   # the endpoint re-sends
        nar.on_narration("The sky is blue because of Rayleigh scattering", False, False)
        check(len(nar.lines) == 1, "streamed narration grows one line, not many")
        check(nar.lines[0]["text"].endswith("scattering"),
              "...and that line carries the newest text")
        check(nar.lines[0]["i"] == 0, "...keeping its index so the page repaints it")
        nar.on_narration("**", False, False)
        nar.on_narration("**", False, False)
        nar.on_narration("**", False, False)
        check(len(nar.lines) == 2,
              "a repeated fragment is a no-op, not a new line each time")
        nar.on_narration("A brand new thought", False, False)
        check(len(nar.lines) == 3, "a genuinely new line still appends")
        nar.on_interim("Checking what holds the lock:")
        check(len(nar.lines) == 4 and nar.lines[-1]["kind"] == "thinking",
              "an interim note gets its own line, marked as thinking")
        nar.on_interim("Checking what holds the lock: the db file")
        check(len(nar.lines) == 4, "and it grows in place too")
        # The agent flags EVERY turn's narration as final, so a run that goes on
        # to more tool calls emits several of them. Painting those as the answer
        # put an answer bubble above the tool lines with the real answer below.
        mid = fb.WebRun("midanswer", "web")
        mid.on_narration("Let me check the disk first.", True, True)
        check(mid.lines[-1]["kind"] == "say",
              "a final-flagged narration mid-run is not painted as the answer")
        check(mid.lines[-1]["uid"] == "midanswer#0",
              "...and every line carries a stable uid for the page to key on")
        mid.on_progress("shell", '{"command": "df -h"}')
        mid.on_tool_done("shell", {}, "exit_code=0", 0.2)
        check([l["kind"] for l in mid.lines] == ["say", "tool", "tool_done"],
              "the run keeps working after it has 'answered' once")
        mid.on_narration("The disk is fine and nothing is running hot.", True, True)
        check(mid.lines[-1]["kind"] == "say",
              "the last narration is still not the answer until the run ends")
        mid.finish()
        check(mid.lines[-1]["kind"] == "final"
              and mid.lines[-1]["text"].startswith("The disk is fine"),
              "the run's last text becomes the answer once, at the end")
        check(mid.lines[0]["kind"] == "say",
              "...and the earlier narration stays a 'say' line, so nothing is "
              "drawn above and below the tool output")
        sfin = fb.WebRun("saytofinal", "web")
        sfin.on_interim("**")
        sfin.on_narration("**the manager box** confirmed.", True, False)
        check(len(sfin.lines) == 1 and sfin.lines[0]["kind"] in ("thinking", "say"),
              "a 'thinking' fragment grows into the answer line, not beside it")
        sfin.finish()
        check(len(sfin.lines) == 1 and sfin.lines[0]["kind"] == "final",
              "and it becomes the answer when the run ends")

        page_src = fb.WEB_PAGE
        check("function reconcile(" in page_src and "l.uid" in page_src,
              "the page rebuilds the transcript by reconciling the server's "
              "ordered lines, keyed by uid")
        check("(runId+'#'+i)" not in page_src and "const nodes" not in page_src,
              "the page no longer keys nodes by line index - index keying is how "
              "a new line took the place of an older one at the top of the log")
        check("since=0" in page_src and "add('you'" not in page_src,
              "the page reads the whole buffer every poll and never echoes the "
              "operator's own line locally")
        check("/api/live" in page_src, "a reloaded page re-attaches to a live run")
        check("{{VERSION}}" in page_src, "the page is stamped with its version")

        # -- start the server on an ephemeral port ----------------------------
        fb.CONFIG["web"] = {"enabled": True, "port": 0, "host": "127.0.0.1",
                            "token": TOKEN}
        srv = fb.run_webui()
        check(srv is not None, "run_webui hands back the server object")
        port = srv.server_address[1]
        base = f"http://127.0.0.1:{port}"

        code, j = http(f"{base}/api/health", token=None)
        check(code == 200 and j.get("ok") is True, "health answers without a token")
        check(j.get("version") == fb.VERSION, "health reports the version")

        # -- auth -------------------------------------------------------------
        for path, body in (("/api/run", {"message": "hi"}),
                           ("/api/events", None), ("/api/steer", {"message": "x"}),
                           ("/api/stop", {})):
            code, _ = http(f"{base}{path}", body, token=None)
            check(code == 401, f"{path} refuses a missing token")
            code, _ = http(f"{base}{path}", body, token="wrong")
            check(code == 401, f"{path} refuses a wrong token")
        code, _ = http(f"{base}/api/events?run_id=nope")
        check(code == 404, "an unknown run id is a 404, not an empty success")
        code, _ = http(f"{base}/api/live", None, token=None)
        check(code == 401, "/api/live refuses a missing token")
        code, j = http(f"{base}/api/live")
        check(code == 200 and j.get("run_id") is None,
              "/api/live reports no run while nothing is going")

        with urllib.request.urlopen(urllib.request.Request(f"{base}/"), timeout=15) as r:
            html = r.read().decode()
            hdrs = {k.lower(): v for k, v in r.headers.items()}
        check("Message tinycmdr" in html, "the page itself is served")
        check("no-store" in hdrs.get("cache-control", ""),
              "the page is served no-store: a browser cannot keep running "
              "yesterday's client after the server was fixed")
        check(fb.VERSION in html and "{{VERSION}}" not in html,
              "the served page carries the version that is actually running")

        # -- a real run, followed the way the browser follows it --------------
        seen = []
        ev = {"first": threading.Event(), "go1": threading.Event(),
              "second": threading.Event(), "go2": threading.Event()}

        def call0(payload):
            ev["first"].set()
            ev["go1"].wait(20)
            return tool_call_reply("shell", {"command": "echo tinycmdr-web-test"})

        def call1(payload):
            ev["second"].set()
            ev["go2"].wait(20)
            return text_reply("The disk is fine and nothing is running hot.")

        make_stub(fb, seen, [call0, call1])
        code, j = http(f"{base}/api/run", {"message": "check the disk"})
        check(code == 200 and j.get("run_id"), f"a run starts ({j})")
        check(j.get("busy") is False, "it is not reported busy on the first run")
        run_id = j.get("run_id")

        check(wait_for(ev["first"].is_set), "the run reached the model")
        code, j = http(f"{base}/api/live")
        check(j.get("run_id") == run_id,
              "/api/live names the run that is going, so a reload can re-attach")

        code, j = http(f"{base}/api/run", {"message": "another one"})
        check(code == 200 and j.get("busy") is True and j.get("run_id") == run_id,
              "a second run on the same session is refused as busy")

        code, j = http(f"{base}/api/steer",
                       {"run_id": run_id, "message": "also check the free space"})
        check(code == 200 and j.get("queued") is True, "a mid-run message is queued")

        ev["go1"].set()
        check(wait_for(ev["second"].is_set), "the run went on to a second turn")
        check(any("also check the free space" in blob(p) for p in seen),
              "the steering message reached the model in the next request")
        check(any("tinycmdr-web-test" in blob(p) for p in seen[-1:]),
              "the tool result was fed back, so the run is a real loop")

        ev["go2"].set()
        lines, state = collect(base, run_id)
        kinds = [l["kind"] for l in lines]
        text = "\n".join(l["text"] for l in lines)
        check(kinds and kinds[0] == "you" and "check the disk" in lines[0]["text"],
              "the first line is what the operator typed")
        check("tool" in kinds, "the tool call appears as it starts")
        check("tool_done" in kinds, "and its result appears when it finishes")
        check(kinds[-1] == "final", "the answer arrives as a final line")
        check("disk is fine" in text, "the answer text is in the buffer")
        check(state.get("done") is True, "the run reports done")
        check(state.get("steps") == 1, f"one tool call counted ({state.get('steps')})")
        check(all("t" in l for l in lines), "every line carries a timestamp")
        check(all(l.get("uid") for l in lines),
              "every line carries a uid, so the page can key on something stable")
        check(len({l["uid"] for l in lines}) == len(lines), "uids are unique")

        # polling from the end is a no-op, not a replay
        _, again = http(f"{base}/api/events?run_id={run_id}&since=99")
        check(again.get("lines") == [], "polling past the end returns no lines")
        check(again.get("done") is True, "...and still the final state")

        # -- stop -------------------------------------------------------------
        seen2 = []
        ev2 = {"seen": threading.Event(), "go": threading.Event()}

        def slow0(payload):
            ev2["seen"].set()
            ev2["go"].wait(20)
            return tool_call_reply("shell", {"command": "echo too-late"})

        make_stub(fb, seen2, [slow0])
        code, j = http(f"{base}/api/run", {"message": "start something long"})
        stop_id = j.get("run_id")
        check(wait_for(ev2["seen"].is_set), "the second run reached the model")
        code, j = http(f"{base}/api/stop", {"run_id": stop_id})
        check(code == 200 and j.get("stopping") is True, "stop is accepted")
        ev2["go"].set()
        lines, state = collect(base, stop_id)
        text = "\n".join(l["text"] for l in lines)
        check(state.get("done") is True, "the stopped run ends")
        check(sum(1 for l in lines if l["kind"] == "system"
                  and "stop" in l["text"].lower()) == 1,
              "the buffer records the stop exactly once")
        check("🛑" in text, "the buffer shows it was stopped by the operator")
        check("too-late" not in text, "the tool after the stop never ran")

        # -- fast commands, and the old synchronous endpoint ------------------
        code, j = http(f"{base}/api/run", {"message": "/status"})
        check(code == 200 and j.get("immediate") is True,
              "a fast command answers at once through /api/run")
        check("tinycmdr" in (j.get("reply") or ""), "and returns its text")

        code, j = http(f"{base}/api/chat", {"message": "/status"})
        check(code == 200 and j.get("reply"), "/api/chat still answers (scripts use it)")

        seen3 = []
        make_stub(fb, seen3, [lambda p: text_reply("plain sync answer")])
        code, j = http(f"{base}/api/chat", {"message": "just answer me"})
        check("plain sync answer" in (j.get("reply") or ""),
              "/api/chat still runs a full turn and returns the answer")

        # -- conversations: a browser owns its own, and they survive a reload --
        # The lane used to hardcode one session key, so nothing could be listed
        # or reopened. What is checked here is the whole point of the change: a
        # conversation is on disk, a reload paints it back, and another browser
        # cannot see it unless it is handed the key.
        code, j = http(f"{base}/api/sessions", token=None)
        check(code == 401, "/api/sessions refuses a missing token")
        code, j = http(f"{base}/api/sessions", client="A")
        check(code == 200 and j.get("sessions") is not None,
              "/api/sessions answers a browser")
        check(j.get("client") == "A", "the server reads the browser id it was sent")
        check(j.get("open") == "web",
              "a browser with nothing open lands on the shared conversation")

        code, j = http(f"{base}/api/sessions", {"op": "new"}, client="A")
        conv = j.get("key")
        check(code == 200 and conv and conv.startswith("web-"),
              f"a new conversation is created ({conv})")
        check(j["sessions"][0]["key"] == conv and j["sessions"][0]["owner"] == "mine",
              "...and it is this browser's")
        code, j = http(f"{base}/api/sessions", client="B")
        check(all(s["key"] != conv for s in j["sessions"]),
              "another browser does not see it")
        code, j = http(f"{base}/api/sessions?all=1", client="B")
        check(any(s["key"] == conv for s in j["sessions"]),
              "the token holder can ask for every conversation on the host")

        seen4 = []
        make_stub(fb, seen4, [lambda p: text_reply("nothing much is running.")])
        code, j = http(f"{base}/api/run",
                       {"message": "what is running", "session": conv}, client="A")
        conv_run = j.get("run_id")
        check(code == 200 and conv_run, f"a run starts in that conversation ({j})")
        lines, state = collect(base, conv_run)
        check(state.get("done") is True, "the run finishes")
        check([l["kind"] for l in lines] == ["you", "final"],
              f"its lines are its own ({[l['kind'] for l in lines]})")

        logfile = workdir / "sessions" / f"{conv}.web.jsonl"
        check(wait_for(lambda: logfile.exists(), timeout=5),
              "the finished run is on disk, so a reload has something to paint")
        code, tr = http(f"{base}/api/session?key={conv}", client="A")
        check(code == 200 and tr.get("key") == conv, "the conversation is readable")
        check(len(tr.get("runs") or []) == 1
              and tr["runs"][0]["run_id"] == conv_run,
              "it holds exactly the run that was just done")
        check([l["text"] for l in tr["runs"][0]["lines"]] ==
              [l["text"] for l in lines],
              "and the painted lines match the run, in order")
        check(all(l.get("uid") for l in tr["runs"][0]["lines"]),
              "the lines keep their uids, so the page can key on them")

        # a restart: the in-memory run is gone and the file is all that is left
        fb.AGENT.histories.pop(conv, None)
        fb.WEB_RUNS.clear()
        code, tr = http(f"{base}/api/session?key={conv}", client="A")
        check(code == 200 and tr["runs"][0]["run_id"] == conv_run,
              "a restart does not lose the conversation")
        code, j = http(f"{base}/api/live?session={conv}", client="A")
        check(code == 200 and j.get("run_id") is None,
              "/api/live is scoped to the conversation it is asked about")

        code, j = http(f"{base}/api/sessions",
                       {"op": "rename", "key": conv, "title": "disk checks"},
                       client="A")
        row = next(s for s in j["sessions"] if s["key"] == conv)
        check(row.get("title") == "disk checks", "a conversation can be renamed")
        code, j = http(f"{base}/api/sessions", {"op": "open", "key": conv},
                       client="A")
        check(j.get("open") == conv, "a browser can say which one it has open")
        code, j = http(f"{base}/api/sessions", client="A")
        check(j.get("open") == conv, "...and the list reports it back")
        code, j = http(f"{base}/api/sessions", {"op": "open", "key": "web"},
                       client="A")
        check(j.get("open") == "web", "and switch back")

        code, j = http(f"{base}/api/session?key=../../config.json", client="A")
        check(code == 200 and j.get("key") == "web",
              "a key that is not a key falls back to the shared conversation")
        code, j = http(f"{base}/api/sessions",
                       {"op": "delete", "key": "../../config.json"}, client="A")
        check(code == 404, "...and cannot be deleted")
        code, j = http(f"{base}/api/sessions", {"op": "nonsense"}, client="A")
        check(code == 400, "an unknown op is a 400, not a silent success")

        code, j = http(f"{base}/api/sessions", {"op": "delete", "key": conv},
                       client="A")
        check(code == 200 and j.get("deleted") == conv, "a conversation can be deleted")
        check(not logfile.exists(), "...and its run log goes with it")
        check(all(s["key"] != conv for s in j["sessions"]), "...and it leaves the list")
        code, j = http(f"{base}/api/session?key={conv}", client="A")
        check(code == 200 and (j.get("runs") or []) == [],
              "a deleted conversation reads as empty, not as an error")

        # what a conversation holds is bounded, so a long-lived host cannot fill
        # its disk with one chat
        code, j = http(f"{base}/api/sessions", {"op": "new"}, client="A")
        capped = j.get("key")
        fb.web_runlog_append(capped, "r1", 0,
                            [{"i": 0, "uid": "r1#0", "kind": "you",
                              "text": "x" * 50, "t": 0, "r": 0}])
        fb.WEB_RUNLOG_KEEP = 3
        for n in range(6):
            fb.web_runlog_append(capped, f"r{n + 2}", 0,
                                [{"i": 0, "uid": f"r{n + 2}#0", "kind": "you",
                                  "text": "y", "t": 0, "r": 0}])
        kept = fb.web_runlog(capped)
        check(len(kept) == 3, f"the run log keeps the newest few ({len(kept)})")
        check(kept[-1]["run_id"] == "r7", "...and the newest one is the last")
        fb.WEB_RUNLOG_KEEP = 60
        http(f"{base}/api/sessions", {"op": "delete", "key": capped}, client="A")

        # -- the panels: the host's own ledger, jobs, log and inventory --------
        # None of this was reachable from a browser, so the same facts were
        # fetched by typing a command into the chat. Read-only on purpose.
        for path in ("/api/tasks", "/api/jobs", "/api/log", "/api/inventory",
                     "/api/commands"):
            code, _ = http(f"{base}{path}", token=None)
            check(code == 401, f"{path} refuses a missing token")

        code, j = http(f"{base}/api/commands")
        cmds = j.get("commands") or []
        check(code == 200 and any(c.get("cmd") == "/status" for c in cmds),
              "the command list is served, so the composer can complete it")
        check(all(c.get("help") for c in cmds),
              "every command carries its one-line explanation")

        code, j = http(f"{base}/api/run", {"message": "/help"})
        helptext = j.get("reply") or ""
        check(code == 200 and all(c["cmd"] in helptext for c in cmds),
              "/help is generated from that same list, so the two cannot drift")

        code, j = http(f"{base}/api/tasks")
        check(code == 200 and isinstance(j.get("items"), list),
              "the ledger is readable (empty on a fresh install, not an error)")
        code, j = http(f"{base}/api/jobs")
        check(code == 200 and isinstance(j.get("jobs"), list)
              and "scheduler" in j,
              "the scheduler's jobs are readable")
        code, j = http(f"{base}/api/log?lines=5")
        check(code == 200 and isinstance(j.get("lines"), list)
              and len(j["lines"]) <= 5 and j.get("path") == "tinycmdr.log",
              f"the log tail is capped at what was asked for "
              f"({len(j.get('lines') or [])} lines)")
        code, j = http(f"{base}/api/inventory")
        check(code == 200 and isinstance(j.get("skills"), list)
              and isinstance(j.get("tools"), list) and "spill" in j,
              "the skill, tool and spill inventory is readable")

        # ...and it lists the skills a host actually has. A panel that quietly
        # reports "0 skills" on a box with 108 of them looked fine in the suite
        # until this: the first cut sorted the records instead of the names.
        skills_dir = workdir / "skills" / "demo-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: a runbook this panel must show\n---\n\nbody\n",
            encoding="utf-8")
        saved = fb.SKILLS_DIR
        try:
            fb.SKILLS_DIR = workdir / "skills"
            code, j = http(f"{base}/api/inventory")
            check(code == 200 and "demo-skill" in (j.get("skills") or []),
                  f"the inventory names the skills on this host ({j.get('skills')})")
        finally:
            fb.SKILLS_DIR = saved
        check(j.get("version") == fb.VERSION and j.get("host"),
              "and it names the host and version it came from")

        # -- a read never writes a registry entry ------------------------------
        # The build's rule is that opening it does nothing: the registry grows on
        # an operator action, never because a page asked a question.
        reg = workdir / "web-sessions.json"
        before = reg.read_text(encoding="utf-8") if reg.exists() else "{}"
        http(f"{base}/api/health")
        http(f"{base}/api/session?key=web-00000000", client="A")
        http(f"{base}/api/sessions", client="A")
        after = reg.read_text(encoding="utf-8") if reg.exists() else "{}"
        check(after == before,
              "reading a page or an unknown conversation does not grow the registry")

        srv.shutdown()
        srv.server_close()

        print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all web UI checks passed'}")
        return 1 if FAILS else 0
    finally:
        try:
            if srv is not None:
                srv.shutdown()
                srv.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
