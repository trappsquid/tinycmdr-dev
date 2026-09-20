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
        # A lane is a buffer plus the reporter every interface shares, so these
        # are the REPORTER's checks, run through the web destination.
        def web_lane(run_id="unitrun", session="web"):
            b = fb.WebRun(run_id, session)
            return b, fb.RunReporter(fb.WebDestination(b), session)

        run, rep = web_lane()
        rep.progress("generating", "13.4 tok/s, 220 chars")
        check(run.lines == [], "a 'generating' heartbeat adds no line")
        check(rep.steps == 0, "a heartbeat does not count as a tool call")
        check("tok/s" in run.status, "the heartbeat updates the status line instead")
        rep.tool_done("shell", {}, "exit_code=1\nerror: no such file", 0.4)
        check(run.lines and run.lines[-1]["kind"] == "tool_fail",
              "a failed tool output renders as tool_fail")
        rep.tool_done("shell", {}, "exit_code=0\nfine", 0.4)
        check(run.lines[-1]["kind"] == "tool_done", "...and a good one as tool_done")
        rep.progress("shell", '{"command": "df -h"}')
        check(rep.steps == 1 and "df -h" in run.lines[-1]["text"],
              "a real tool call adds a line and counts one step")

        # -- unit level: narration GROWS one line, it does not stack ----------
        # The callbacks deliver cumulative snapshots; appending each one painted
        # ~190 near-identical lines for a 5-second run on a live box.
        nar, nar_rep = web_lane("narration")
        nar_rep.narration("The sky is blue", False, True)
        nar_rep.narration("The sky is blue", False, False)   # the endpoint re-sends
        nar_rep.narration("The sky is blue because of Rayleigh scattering", False, False)
        check(len(nar.lines) == 1, "streamed narration grows one line, not many")
        check(nar.lines[0]["text"].endswith("scattering"),
              "...and that line carries the newest text")
        check(nar.lines[0]["i"] == 0, "...keeping its index so the page repaints it")
        # One rule for every lane, and it is the chat lane's (v1.9.29): an
        # identical fragment is a no-op, a fragment that does not extend the line
        # replaces it in place, and a NEW TURN opens a new line. Comparing a new
        # turn against the line's current text instead of the text it was OPENED
        # with is what put nine identical posts on a looping run.
        nar_rep.narration("**", False, False)
        nar_rep.narration("**", False, False)
        check(len(nar.lines) == 1,
              "a fragment that does not extend the growing line replaces it in "
              "place, it does not stack")
        check(nar.lines[0]["text"] == "💬 **",
              f"...and the line says the newest thing ({nar.lines[0]['text']!r})")
        nar_rep.narration("A brand new thought", False, True)
        check(len(nar.lines) == 2, "a new turn opens a new line")
        nar_rep.note("Checking what holds the lock:")
        check(len(nar.lines) == 3 and nar.lines[-1]["kind"] == "thinking",
              "an interim note gets its own line, marked as thinking")
        nar_rep.note("Checking what holds the lock: the db file")
        check(len(nar.lines) == 3,
              "a second note inside the gap does not add another line")
        # The agent flags EVERY turn's narration as final, so a run that goes on
        # to more tool calls emits several of them. Painting those as the answer
        # put an answer bubble above the tool lines with the real answer below.
        mid, mid_rep = web_lane("midanswer")
        mid_rep.narration("Let me check the disk first.", True, True)
        check(mid.lines[-1]["kind"] == "say",
              "a final-flagged narration mid-run is not painted as the answer")
        check(mid.lines[-1]["uid"] == "midanswer#0",
              "...and every line carries a stable uid for the page to key on")
        mid_rep.progress("shell", '{"command": "df -h"}')
        mid_rep.tool_done("shell", {}, "exit_code=0", 0.2)
        check([l["kind"] for l in mid.lines] == ["say", "tool", "tool_done"],
              "the run keeps working after it has 'answered' once")
        mid_rep.narration("The disk is fine and nothing is running hot.", True, True)
        check(mid.lines[-1]["kind"] == "say",
              "the last narration is still not the answer until the run ends")
        # The answer itself is posted by whoever drove the run (the browser run,
        # the chat lane, one code path now): what the REPORTING layer guarantees
        # is that the draft goes when it turns out to be that answer, so the same
        # words are never on the screen twice.
        before = len(mid.lines)
        mid_rep.narration_drop()
        check(len(mid.lines) == before - 1,
              "the draft is taken back when it becomes the answer")
        check(mid.lines[0]["kind"] == "say",
              "...and the narration above the tool lines stays, so the plan does not "
              "vanish with it")
        sfin, sfin_rep = web_lane("saytofinal")
        sfin_rep.note("**")
        sfin_rep.narration("**the manager box** confirmed.", True, False)
        check([l["kind"] for l in sfin.lines] == ["thinking", "say"],
              f"the interstitial note and the streamed text are two tones on one "
              f"vocabulary ({[l['kind'] for l in sfin.lines]})")
        sfin.finish()
        check(sfin.done and not any(l["kind"] == "final" for l in sfin.lines),
              "ending the run does not invent an answer line - whoever drove the "
              "run posts the answer, in every lane")

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
        check(kinds[-1] == "final",
              "the answer is the LAST thing the run produced, under everything "
              "it did to find it")
        check("Done" in (state.get("status") or ""),
              f"and the done line lives in the lane's own status surface, the way "
              f"the chat lane edits its status post ({state.get('status')!r})")
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
        check(lines[0]["kind"] == "you" and "final" in
              [l["kind"] for l in lines],
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

        # -- a delegated subtask is visible in the lane, tagged ---------------
        # Until now delegate_task ran with NO callbacks at all: a subtask could
        # work for half an hour and the lane showed nothing but the call that
        # started it. It now reports through the parent's reporter under its own
        # source, so its lines read as theirs.
        del_run, del_rep = web_lane("delegate")
        make_stub(fb, [], [
            # the parent delegates, the SUBTASK does real work (a tool call), then
            # answers: the working part is what used to be invisible
            lambda p: tool_call_reply("delegate_task", {"task": "look at the disk"}),
            lambda p: tool_call_reply("shell", {"command": "echo sub-working"}),
            lambda p: text_reply("the sub-agent says the disk is fine"),
        ])
        fb.AGENT.run("delegate", "check the disk with a sub-agent",
                     progress_cb=del_rep.progress, progress_done_cb=del_rep.tool_done,
                     interim_cb=del_rep.note, narration_cb=del_rep.narration,
                     say_cb=del_rep.say)
        del_text = "\n".join(l["text"] for l in del_run.lines)
        check("delegate_task" in del_text,
              "the call that started the subtask is in the lane")
        check("↳ sub:" in del_text,
              f"a delegated subtask reports into the lane under its own source "
              f"({[l['text'][:44] for l in del_run.lines[-3:]]})")
        check("look at the disk" in del_text,
              "...naming the subtask it belongs to")

        # -- a job scheduled from a page reports INTO that conversation --------
        # It used to carry channel_id None, so it fired with no reporter and no
        # place for its answer: the operator scheduled work in the browser and
        # never heard about it again.
        job_conv = http(f"{base}/api/sessions", {"op": "new"}, client="A")[1]["key"]
        fb.SCHEDULER.jobs["nightly"] = {"task": "check the backup",
                                        "channel_id": f"web:{job_conv}",
                                        "cron": "0 3 * * *", "next": 0}
        seen_job = []
        make_stub(fb, seen_job, [lambda p: text_reply("the backup job ran fine")])
        saved_dispatcher = fb.SCHEDULER.dispatcher
        try:
            fb.SCHEDULER.dispatcher = None
            fb.SCHEDULER._fire("nightly", fb.SCHEDULER.jobs["nightly"])
        finally:
            fb.SCHEDULER.dispatcher = saved_dispatcher
        code, tr = http(f"{base}/api/session?key={job_conv}", client="A")
        job_lines = [l for r in (tr.get("runs") or []) for l in r["lines"]]
        job_text = "\n".join(l["text"] for l in job_lines)
        check("check the backup" in job_text,
              f"a job scheduled from a page reports into that conversation "
              f"({job_lines[:2]})")
        check("the backup job ran fine" in job_text,
              "...and its answer lands there, not nowhere")
        check("final" in [l["kind"] for l in job_lines],
              "...as an answer line the operator can read")
        code, j = http(f"{base}/api/sessions", {"op": "delete", "key": job_conv},
                       client="A")
        check(code == 409 and "job" in (j.get("error") or ""),
              f"a conversation a job reports into cannot be deleted under it "
              f"({code} {j.get('error')})")
        fb.SCHEDULER.jobs.pop("nightly", None)
        code, j = http(f"{base}/api/sessions", {"op": "delete", "key": job_conv},
                       client="A")
        check(code == 200 and j.get("deleted") == job_conv,
              "...until the job that reports into it is gone")

        # -- a run is on disk WHILE it runs ------------------------------------
        # A run used to be written only when it finished, so a restart landing on
        # top of one left nothing: measured 2026-09-20, when a long browser
        # research task was cut off and its conversation kept no trace of the
        # work. Checkpointing is one small atomic write every twenty seconds.
        ckpt_key = http(f"{base}/api/sessions", {"op": "new"}, client="A")[1]["key"]
        ck_run = fb.WebRun("ckpt-run", ckpt_key)
        saved_gap = fb.WEB_RUNLOG_CHECKPOINT
        try:
            fb.WEB_RUNLOG_CHECKPOINT = 0.0
            ck_run.add("you", "a long job, still going")
            ck_run.add("tool", "shell(Get-ChildItem C:/temp)")
            ck_path = workdir / "sessions" / f"{ckpt_key}.web.jsonl"
            on_disk = ck_path.read_text(encoding="utf-8") if ck_path.exists() else ""
            check("Get-ChildItem C:/temp" in on_disk and "still going" in on_disk,
                  "a running run is already on disk, so a restart cannot swallow it")
            code, tr = http(f"{base}/api/session?key={ckpt_key}", client="A")
            texts = [l["text"] for r in (tr.get("runs") or []) for l in r["lines"]]
            check(sum(1 for t in texts if "still going" in t) == 1,
                  f"...and the page shows it once, not twice ({texts})")
        finally:
            fb.WEB_RUNLOG_CHECKPOINT = saved_gap
            http(f"{base}/api/sessions", {"op": "delete", "key": ckpt_key},
                 client="A")

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

        # -- the lanes must report the same run the same way -------------------
        # The operator's report: "the streaming that I am familiar with in
        # Mattermost is not reflected in the web UI". Mattermost goes through
        # ProgressReporter; the web run subscribes to the callbacks directly, and
        # the whole reporting layer - the command preview, the exit code, the
        # FAILURE REASON, the periodic ⏳ line - simply was not there. These
        # checks pin the two together, so the next divergence fails a suite
        # instead of being noticed on a phone.
        class FakeChannel:
            """What ProgressReporter needs from a dispatcher: post, edit, delete."""
            def __init__(self):
                self.timeline = []

            def _post(self, channel_id, root_id, text, color=None):
                self.timeline.append(text)
                return f"post{len(self.timeline)}"

            def _edit(self, msg_id, channel_id, text, color=None):
                self.timeline.append(text)

            def _delete(self, msg_id, channel_id):
                pass

        agent_cfg = fb.CONFIG["agent"]
        saved = {k: agent_cfg.get(k) for k in
                 ("checkin_steps", "checkin_minutes", "progress_updates",
                  "checkin_per_tool", "checkin_tool_preview_chars")}
        try:
            agent_cfg["progress_updates"] = True
            agent_cfg["checkin_steps"] = 1          # fire the ⏳ line on the first step
            agent_cfg["checkin_minutes"] = 0
            agent_cfg["checkin_per_tool"] = True
            channel = FakeChannel()
            rep = fb.ProgressReporter(channel, "chan-1", None, "web")
            mm_run = fb.WebRun("parity", "web")
            web_rep = fb.RunReporter(fb.WebDestination(mm_run), "web")

            call = '{"command": "Get-ChildItem C:/temp -Recurse"}'
            failed = "exit_code=1\npermission denied while opening C:\\temp\\locked"
            rep.progress("shell", call)
            web_rep.progress("shell", call)
            rep.tool_done("shell", call, failed, 2.4)
            web_rep.tool_done("shell", call, failed, 2.4)
            mm_text = "\n".join(channel.timeline)
            web_text = "\n".join(l["text"] for l in mm_run.lines)

            check("Get-ChildItem C:" in mm_text and "Get-ChildItem C:" in web_text,
                  "parity: the command preview reaches the browser, as it does chat")
            tool_line = next((l["text"] for l in mm_run.lines
                              if l["kind"] == "tool"), "")
            check("Get-ChildItem C:/temp -Recurse" in tool_line,
                  f"parity: the call line is the command it is running "
                  f"({tool_line!r})")
            check('{"command"' not in tool_line,
                  "parity: not the escaped JSON argument as it arrived")
            for fact in ("2.4s", "exit 1", "permission denied"):
                check(fact in mm_text and fact in web_text,
                      f"parity: the finished call reports '{fact}' in both lanes")
            check(any(l["kind"] == "tool_fail" for l in mm_run.lines),
                  "parity: a failed call is a tool_fail line, so the page draws it "
                  "red (the chat lane says the same thing with a red bar)")
            check(any(l["kind"] == "tool_fail" for l in mm_run.lines),
                  "...and it is a tool_fail line, so the page can color it")

            # the ⏳ check-in: same cadence, same WORDS, one implementation
            check("⏳" in mm_text, f"the chat lane posts its check-in ({mm_text[-90:]})")
            check("⏳" in web_text,
                  f"the web lane posts the same check-in ({web_text[-90:]})")
            same = fb.checkin_line("web", 7, 305, "shell", call)
            check(rep.checkin_text(7, 305, "shell", call) == same,
                  "parity: both lanes draw the check-in from one function")
            check("step 7" in same and "5m05s in" in same,
                  f"...and it says how long and which step ({same})")

            # a command matching a confirm pattern must be ASKED about, not
            # silently declined - that was the web lane's behaviour, and it made
            # the browser unable to run work the chat lane would have run
            agent_cfg["confirm_patterns"] = ["echo CONFIRM-ME"]
            asked = fb.WebRun("confirm", "web")
            asked_rep = fb.RunReporter(fb.WebDestination(asked), "web")

            def say_yes():
                for _ in range(50):
                    time.sleep(0.1)
                    with asked.lock:
                        row = got[0]
                    if row is not None:
                        row["answer"] = "yes"
                        row["ev"].set()
                        return

            got = [None]
            real_opener = asked.opener

            def opener(question, options, wait, label=None):
                row = real_opener(question, options, wait, label)
                got[0] = row
                return row

            asked.opener = opener
            threading.Thread(target=say_yes, daemon=True).start()
            out = fb.tool_shell({"command": "echo CONFIRM-ME"},
                                {"confirm_cb": asked_rep.confirm})
            check("DECLINED" not in out,
                  f"confirm: an approved command runs ({str(out)[:60]!r})")
            check(any(l["kind"] == "ask" for l in asked.lines),
                  "confirm: the browser is shown the command and asked")
            check(any("CONFIRM-ME" in l["text"] for l in asked.lines),
                  "confirm: ...with the command itself in the question")
            check(any("confirmed" in l["text"] for l in asked.lines),
                  "confirm: and the verdict lands in the transcript")

            # no answer: the command is skipped, and the page says why
            quiet = fb.WebRun("confirm-timeout", "web")
            quiet_rep = fb.RunReporter(fb.WebDestination(quiet), "web")
            verdict = quiet_rep.confirm("echo CONFIRM-ME", wait=0.4)
            check(verdict is False, "confirm: silence is not consent")
            check(any("no answer" in l["text"] for l in quiet.lines),
                  "confirm: and the transcript shows the timeout, not a shrug")
        finally:
            for k, v in saved.items():
                if v is None:
                    agent_cfg.pop(k, None)
                else:
                    agent_cfg[k] = v

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
