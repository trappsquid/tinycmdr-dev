"""The LEGION mesh: a hub's tab per cohort, over the A2A protocol both ends speak.

Two halves are graded here, because both are this build's:

  * the COHORT half (a box a hub drives): `tinycmdr/GetRunLines` returns the same
    uid-keyed lines the page draws, the frame is created before the run and bounded
    with the task store, `web.a2a_policy: read_only` refuses every write/exec tool
    for runs that arrived over /a2a while the box's own page keeps them, and the
    card declares the extension without claiming streaming;
  * the HUB half: the order registers and returns immediately (a cohort that hangs
    costs a dedicated thread, never a handler thread), the task store answers when
    the socket did not, a restart is repaired from the cohort's own store - never
    by re-sending a side-effecting order - and the store and tab lists are bounded.

Offline and self-contained: loopback only, port 0, no model. The cohort's model half
is stubbed (`drive_run` replaced in the staged copy); the hub talks to stub A2A peers
on 127.0.0.1.

    python tests/test_legion.py
"""
import importlib.util
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
# TINYCMDR_SRC points this at a reverted copy, so a fix can be watched going red
# (tests/run_all.py clears it for a normal run).
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

# A suite never opens a browser tab, even when run by hand.
os.environ.setdefault("TINYCMDR_NO_BROWSER", "1")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

FAILS = []
TOKEN = "tok-legion-suite-0123456789"


def check(cond, what, detail=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}: {detail}")
    else:
        print(f"ok   {what}")


def stage_module(name, tag):
    stage = Path(tempfile.gettempdir()) / ("tinycmdr-test-legion-" + tag)
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, stage / "tinycmdr.py")
    shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
                 stage / "config.json")
    spec = importlib.util.spec_from_file_location(name, stage / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod, stage


# ------------------------------------------------------------------- the stub peer
# A minimal, spec-shaped A2A cohort: card, SendMessage (blocking, like the spec's
# default), GetTask, and the run-lines extension. `mode="slow"` holds the blocking
# reply until the SECOND GetTask, which is exactly the socket-timed-out shape the
# hub's store-read exists for - and the task stored is the id the CLIENT minted, so
# a correct client never runs the order twice.
def is_working(task):
    return ((task.get("status") or {}).get("state")) == "TASK_STATE_WORKING"


def completed_task(tid, answer):
    reply = {"messageId": "m1", "role": "ROLE_AGENT", "taskId": tid,
             "parts": [{"text": answer}]}
    return {"id": tid, "contextId": "ctx",
            "status": {"state": "TASK_STATE_COMPLETED", "message": reply,
                       "timestamp": "t"},
            "artifacts": [{"artifactId": "a1", "parts": [{"text": answer}]}],
            "history": [reply]}


class QuietPeer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A client that hung up (the hub's read timeout) is not this suite's problem.
        pass


def make_peer(declare_lines=True, mode="fast", frame_spec=None):
    state = {"tasks": {}, "calls": [], "runs": 0, "frames": {}, "gets": 0,
             "released": False,
             "frame_spec": frame_spec or [("tool", "🔧 uptime"), ("say", "load is fine")]}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):                              # keep the suite quiet
            pass

        def _json(self, payload, status=200):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/.well-known/agent-card.json"):
                caps = {"streaming": False, "pushNotifications": False}
                if declare_lines:
                    caps["extensions"] = [
                        {"uri": "urn:tinycmdr:run-lines:v1", "required": False}]
                self._json({"name": "tinycmdr", "version": "9.9.9",
                            "capabilities": caps})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:                                   # noqa: BLE001
                body = {}
            state["calls"].append((body.get("method"),
                                   self.headers.get("Authorization") or ""))
            if (self.headers.get("Authorization") or "") != "Bearer " + TOKEN:
                self._json({"error": "unauthorized"}, 401)
                return
            m = body.get("method")
            rid = body.get("id")
            if m == "SendMessage":
                msg = (body.get("params") or {}).get("message") or {}
                tid = str(msg.get("taskId") or "")
                if tid in state["tasks"]:
                    # the spec's idempotency: a known id is answered, not re-run
                    self._json({"jsonrpc": "2.0", "id": rid,
                                "result": state["tasks"][tid]})
                    return
                state["runs"] += 1
                text = ((msg.get("parts") or [{}])[0] or {}).get("text", "")
                task = {"id": tid, "contextId": msg.get("contextId"),
                        "status": {"state": "TASK_STATE_WORKING", "timestamp": "t"},
                        "history": [msg]}
                state["tasks"][tid] = task
                # "slow": the reply comes after the second GetTask (the socket-timed-out
                # shape). "held": nothing releases it but the test itself, so a check that
                # must look at a LIVE run has no race at all.
                wait_for = {"slow": 20, "held": 120}.get(mode)
                if wait_for:
                    deadline = time.time() + wait_for
                    while time.time() < deadline and not state["released"]:
                        time.sleep(0.05)
                task = completed_task(tid, "the peer says: " + text)
                state["tasks"][tid] = task
                self._json({"jsonrpc": "2.0", "id": rid, "result": task})
                return
            if m == "GetTask":
                tid = str((body.get("params") or {}).get("id") or "")
                task = state["tasks"].get(tid)
                if task is None:
                    self._json({"jsonrpc": "2.0", "id": rid,
                                "error": {"code": -32001,
                                          "message": "TaskNotFoundError"}})
                    return
                if mode == "slow" and is_working(task):
                    state["gets"] += 1
                    if state["gets"] >= 2:
                        state["released"] = True
                self._json({"jsonrpc": "2.0", "id": rid, "result": task})
                return
            if m == "tinycmdr/GetRunLines":
                tid = str((body.get("params") or {}).get("taskId") or "")
                task = state["tasks"].get(tid)
                if task is None:
                    self._json({"jsonrpc": "2.0", "id": rid,
                                "error": {"code": -32001,
                                          "message": "TaskNotFoundError"}})
                    return
                if not declare_lines:
                    self._json({"jsonrpc": "2.0", "id": rid,
                                "error": {"code": -32004,
                                          "message": "UnsupportedOperationError"}})
                    return
                lines = state["frames"].setdefault(tid, [])
                spec = state["frame_spec"]
                if len(lines) < len(spec):
                    k, t = spec[len(lines)]
                    lines.append({"i": len(lines), "uid": "%s#%d" % (tid, len(lines)),
                                  "kind": k, "text": t, "r": len(lines), "t": 0})
                done = not is_working(task)
                if done and (not lines or lines[-1].get("kind") != "final"):
                    answer = ((task.get("status") or {}).get("message") or {}) \
                        .get("parts", [{}])[0].get("text", "")
                    lines.append({"i": len(lines), "uid": "%s#%d" % (tid, len(lines)),
                                  "kind": "final", "text": answer, "r": 99, "t": 0})
                self._json({"jsonrpc": "2.0", "id": rid,
                            "result": {"taskId": tid, "lines": lines, "done": done,
                                       "status": "done" if done else "working",
                                       "steps": len(lines), "elapsed": 1.0}})
                return
            self._json({"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32004,
                                  "message": "UnsupportedOperationError"}})

    srv = QuietPeer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


def wait_record_done(mod, name, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if mod.legion_record(name).get("last_state") != "working":
            return True
        time.sleep(0.1)
    return False


# ------------------------------------------------------------------ the cohort half
def test_cohort_frame(coh):
    """The frame: created before the run, streamed as uid-keyed lines, finished once."""
    coh.CONFIG["web"]["a2a"] = True
    ran = {"n": 0, "source": "", "text": ""}

    def fake_drive(session_key, text, reporter, **kw):
        ran["n"] += 1
        ran["source"] = kw.get("source")
        ran["text"] = text
        reporter.dest.line("tool", "🔧 uptime")
        reporter.note("looking around")
        return "all good on " + text

    saved = coh.drive_run
    coh.drive_run = fake_drive
    try:
        task, err = coh.a2a_handle("SendMessage", {"message": {
            "messageId": "m1", "role": "ROLE_USER", "taskId": "T-frame-1",
            "contextId": "ctx-1", "parts": [{"text": "hey"}]}})
        check(err is None and task["id"] == "T-frame-1"
              and ((task.get("status") or {}).get("state")) == "TASK_STATE_COMPLETED",
              "the message ran and its task completed", (err, task))
        check(ran["source"] == "a2a",
              "the run carries source=a2a (what web.a2a_policy reads)", ran["source"])
        view, verr = coh.a2a_handle("tinycmdr/GetRunLines", {"taskId": "T-frame-1"})
        check(verr is None and view.get("taskId") == "T-frame-1" and view.get("done"),
              "GetRunLines answers the frame, done", verr)
        kinds = [l["kind"] for l in (view.get("lines") or [])]
        texts = [l.get("text") for l in (view.get("lines") or [])]
        check("tool" in kinds and "final" in kinds
              and "all good on hey" in texts and texts.count("all good on hey") == 1,
              "...with the tool line and exactly one final answer line", kinds)
        uids = [l["uid"] for l in view["lines"]]
        check(all(u.startswith("T-frame-1#") for u in uids)
              and len(set(uids)) == len(uids),
              "...keyed by stable unique uids", uids)
        check(ran["n"] == 1, "the model half ran once", ran["n"])
        repeat, _ = coh.a2a_handle("SendMessage", {"message": {
            "messageId": "m1", "role": "ROLE_USER", "taskId": "T-frame-1",
            "contextId": "ctx-1", "parts": [{"text": "hey"}]}})
        check(ran["n"] == 1 and repeat["id"] == "T-frame-1",
              "a re-sent taskId is answered from the store, never re-run", ran["n"])
        _v, e2 = coh.a2a_handle("tinycmdr/GetRunLines", {"taskId": "no-such"})
        check(e2 and e2["code"] == -32001,
              "GetRunLines for an unknown task: TaskNotFoundError", e2)
        _t2, e3 = coh.a2a_handle("SendMessage", {"message": {
            "parts": [{"text": "x"}], "taskId": "T-frame-2", "contextId": "ctx-2"}})
        check(e3 is None and "T-frame-2" in coh._A2A_FRAMES,
              "a second task gets its own frame")
        # the frame bound moves WITH the task store: push tasks out, their frames go too
        # (_a2a_store takes the lock itself; do not wrap it in another acquire)
        for i in range(coh.A2A_MAX_TASKS + 2):
            coh._a2a_store({"id": "bulk-%d" % i,
                            "status": {"state": "TASK_STATE_COMPLETED"}})
        with coh._A2A_LOCK:
            tasks_len, frames = len(coh._A2A_TASKS), dict(coh._A2A_FRAMES)
        check(tasks_len == coh.A2A_MAX_TASKS and not frames,
              "overflowing the task store evicted the frames with their tasks",
              (tasks_len, len(frames)))
        _v, e4 = coh.a2a_handle("tinycmdr/GetRunLines", {"taskId": "T-frame-2"})
        check(e4 and e4["code"] == -32001,
              "a task whose frame was evicted answers TaskNotFoundError", e4)
    finally:
        coh.drive_run = saved


def test_cohort_policy_and_card(coh):
    """web.a2a_policy: read_only binds only A2A runs; the card declares the extension."""
    coh.CONFIG["web"]["a2a_policy"] = "read_only"
    ctx = {"source": "a2a"}
    check(str(coh.resolve_approval("shell", {"command": "ls"}, ctx) or "")
          .startswith("REFUSED"),
          "an A2A run cannot shell on a read-only cohort")
    check(str(coh.resolve_approval("write_file", {"path": "x", "content": "y"}, ctx)
              or "").startswith("REFUSED"),
          "...nor write files")
    check(str(coh.resolve_approval("create_tool", {"name": "x", "code": "1"}, ctx)
              or "").startswith("REFUSED"),
          "...nor mint a tool (a write tier)")
    check(coh.resolve_approval("read_file", {"path": "x"}, ctx) is None,
          "...while reads pass")
    check(coh.resolve_approval("read_file", {"path": "x"}, {"source": "main"}) is None
          and coh.resolve_approval("shell", {"command": "ls"}, {"source": "main"})
          is None,
          "the same box's own page is unaffected, shell included")
    check(str(coh.resolve_approval("some_dropin", {}, ctx) or "").startswith("REFUSED"),
          "an unknown tool fails closed under read_only")
    coh.CONFIG["web"]["a2a_policy"] = "typo"
    check(coh._a2a_policy() == "full",
          "a typo'd policy reads as the historical behaviour, not as a refusal")
    coh.CONFIG["web"]["a2a_policy"] = "full"
    check(coh.resolve_approval("shell", {"command": "ls"}, ctx) is None,
          "with the default policy a peer keeps the tools this door always had")
    card = coh.a2a_card()
    exts = (card.get("capabilities") or {}).get("extensions") or []
    check(any(e.get("uri") == coh.A2A_RUN_LINES_URI and e.get("required") is False
              for e in exts),
          "the card declares the run-lines extension as optional", exts)
    check(card["capabilities"]["streaming"] is False,
          "...and still declares streaming unsupported")


# --------------------------------------------------------------------- the hub half
def hub_with_peer(hub, srv, name):
    remotes = hub.CONFIG["agent"].setdefault("a2a_remotes", {})
    remotes[name] = {"url": "http://127.0.0.1:%d" % srv.server_address[1],
                     "token_env": "LEGION_TEST_TOKEN"}
    os.environ["LEGION_TEST_TOKEN"] = TOKEN


def test_hub_targets(hub):
    """a2a_remote_target: one resolver for the tool, the relay and the card probe."""
    remotes = hub.CONFIG["agent"].setdefault("a2a_remotes", {})
    remotes["weird"] = {"url": "ssh://box:8790"}
    remotes["shaped"] = "not-a-dict"
    urls = hub.legion_cohorts()
    check("weird" in urls and "shaped" not in urls
          and hub.a2a_remote_target("weird")[2].startswith("ERROR")
          and hub.a2a_remote_target("ghost")[2].startswith("ERROR"),
          "a non-http URL and a malformed entry are refused, named, by one resolver",
          (hub.a2a_remote_target("weird")[2], hub.a2a_remote_target("ghost")[2]))
    remotes.pop("weird", None)
    remotes.pop("shaped", None)


def test_hub_store_bounds(hub):
    """Tabs and cohort records are bounded and pruned to what is configured."""
    remotes = hub.CONFIG["agent"].setdefault("a2a_remotes", {})
    names = sorted("c%02d" % i for i in range(12))
    for n in names:
        remotes[n] = {"url": "http://127.0.0.1:1"}
    tabs = []
    for n in names:
        tabs = hub.legion_tab_set("browser-x", n)
    check(len(tabs) == hub.LEGION_TABS_MAX and tabs == names[-hub.LEGION_TABS_MAX:],
          "a browser keeps at most LEGION_TABS_MAX cohort tabs, newest kept", tabs)
    tabs = hub.legion_tab_set("browser-x", names[-1], open_=False)
    check(names[-1] not in tabs and len(tabs) == hub.LEGION_TABS_MAX - 1,
          "closing a tab drops it", tabs)
    hub._legion_record(names[0], context_id="ctx", last_task_id="T1",
                       last_state="done", started=1)
    remotes.pop(names[0], None)
    check(names[0] not in (hub._legion_state()["cohorts"] or {}),
          "a cohort removed from the config is pruned from the store")
    check(all(n in names for n in hub.legion_tabs("browser-x")),
          "...and out of the tab lists")
    for n in names:
        remotes.pop(n, None)


def test_hub_egress_warning(hub):
    """A cohort pointed off-LAN warns once; LAN and the overlay's CGNAT range do not."""
    seen = []
    handler = logging.Handler()
    handler.emit = lambda rec: seen.append(rec.getMessage())
    hub.log.addHandler(handler)
    try:
        hub._LEGION_EGRESS_WARNED.clear()
        hub._legion_egress_check("pub", "http://8.8.8.8:8790")
        check("pub" in hub._LEGION_EGRESS_WARNED
              and any("does not resolve" in m for m in seen),
              "a public address warns, naming the entry", seen[-1:])
        n_warned = len(seen)
        hub._legion_egress_check("pub", "http://8.8.8.8:8790")
        check(len(seen) == n_warned, "...once per entry, not per pass")
        hub._legion_egress_check("lan", "http://127.0.0.1:8790")
        hub._legion_egress_check("cg", "http://100.101.102.103:8790")
        check("lan" not in hub._LEGION_EGRESS_WARNED
              and "cg" not in hub._LEGION_EGRESS_WARNED and len(seen) == n_warned,
              "loopback and the overlay's CGNAT range stay quiet",
              sorted(hub._LEGION_EGRESS_WARNED))
    finally:
        hub.log.removeHandler(handler)
    check(hub._legion_cgnat("100.64.0.1") and hub._legion_cgnat("100.127.255.254")
          and not hub._legion_cgnat("100.128.0.1")
          and not hub._legion_cgnat("10.0.0.1")
          and not hub._legion_cgnat("box.example"),
          "the CGNAT classifier covers exactly 100.64.0.0/10")


def test_hub_dispatch(hub, state):
    """An order: registered immediately, streamed live, one run on the peer, done once.

    Against a HELD peer (nothing finishes the task but this test), so the live view is
    observed with no race: release only after the live assertions.
    """
    hub._legion_probe_card("coh")
    row = hub._legion_row("coh")
    check(row["state"] == "ready" and row["version"] == "9.9.9"
          and row["lines"] is True,
          "the card probe fills the rail row (ready, version, lines)", row)
    t0 = time.time()
    rid, err, code = hub.legion_send("coh", "check the disks")
    took = time.time() - t0
    # 2 s, not sub-second: the claim itself is dict work plus two small state writes and
    # the write is what a Windows runner's real-time scanner slows down.
    check(err is None and code == 200 and rid and took < 2.0,
          "legion_send registers and returns at once (no handler thread held)",
          (err, code, round(took, 3)))
    run = hub._web_active_run(hub.legion_key("coh"))
    check(run is not None and run.id == rid, "the order is a live run while it goes")
    got = None
    deadline = time.time() + 10
    while time.time() < deadline:
        v = hub.legion_lines("coh")
        if v.get("run_id") == rid and len(v.get("lines") or []) >= 2:
            got = v
            break
        time.sleep(0.1)
    check(got is not None
          and any(l.get("kind") == "you"
                  and "check the disks" in (l.get("text") or "")
                  for l in got["lines"])
          and any(l.get("kind") == "tool" for l in got["lines"]),
          "the live view starts with the hub's own order line and carries the "
          "cohort's streaming lines", got)
    check(all(a == "Bearer " + TOKEN for _m, a in state["calls"]),
          "every relay call carried the bearer from token_env", state["calls"][:2])
    state["released"] = True                  # let the peer finish the held run
    check(wait_record_done(hub, "coh", timeout=30),
          "the order finishes and the record says so", hub.legion_record("coh"))
    check(state["runs"] == 1, "the peer ran the order exactly once", state["runs"])
    tr = hub.web_transcript(hub.legion_key("coh"))
    lines = [l for r in tr["runs"] for l in (r.get("lines") or [])]
    check(any(l.get("kind") == "final"
              and "the peer says: check the disks" in (l.get("text") or "")
              for l in lines),
          "the answer is in the cohort's persisted transcript",
          [l.get("kind") for l in lines])
    check(any(l.get("kind") == "tool" for l in lines),
          "...with the cohort's own tool lines from the extension")
    row = hub._legion_row("coh")
    check(row["task"]["id"] == rid and row["state"] == "ready",
          "the rail row holds the finished task and reads ready again", row)
    sess = hub.legion_session("coh")
    check(sess["remote"] == "coh" and sess["runs"]
          and any(l.get("kind") == "you" for run in sess["runs"]
                  for l in (run.get("lines") or [])),
          "legion_session repaints the same transcript a reload shows",
          [r["run_id"] for r in sess["runs"]])


def test_hub_busy_refusal(hub):
    """One order per cohort at a time: a second one is refused in words the page shows."""
    run, busy = hub._web_claim_run(hub.legion_key("coh"), "",
                                   make=lambda rid: hub.LegionRun(rid, "coh", "ctx"))
    check(busy is None, "the claim takes an idle cohort", busy)
    try:
        rid2, err2, code2 = hub.legion_send("coh", "one more thing")
        check(rid2 is None and code2 == 409 and "already on campaign" in (err2 or ""),
              "a second order while one runs: 409 with the reason", (err2, code2))
    finally:
        run.done = True
        hub.WEB_RUNS.pop(run.id, None)


def test_hub_task_store_fallback(hub, state):
    """The socket times out; the task store answers; the order is not re-run."""
    saved = hub.CONFIG["agent"].get("a2a_timeout")
    hub.CONFIG["agent"]["a2a_timeout"] = 2      # the read timeout the POST hits
    gets0 = state["gets"]
    try:
        rid, err, code = hub.legion_send("slow", "slow one")
        check(err is None and code == 200, "the slow order is accepted", (err, code))
        ok = wait_record_done(hub, "slow", timeout=60)
        check(ok, "the store read carried it to done", hub.legion_record("slow"))
        check(state["runs"] == 1,
              "the slow order ran exactly once on the peer (idempotent by taskId)",
              state["runs"])
        check(state["gets"] > gets0,
              "the hub polled the task store after the transport gave up",
              (gets0, state["gets"]))
        lines = [l for r in hub.web_transcript(hub.legion_key("slow"))["runs"]
                 for l in (r.get("lines") or [])]
        check(any(l.get("kind") == "final" and "slow one" in (l.get("text") or "")
                  for l in lines),
              "the answer came back through the store",
              [l.get("text") for l in lines if l.get("kind") == "final"])
    finally:
        if saved is None:
            hub.CONFIG["agent"].pop("a2a_timeout", None)
        else:
            hub.CONFIG["agent"]["a2a_timeout"] = saved


def test_hub_lines_fallback(hub):
    """A cohort without the extension: the answer still lands, lines simply do not."""
    hub._legion_probe_card("old")
    check(hub._legion_row("old")["lines"] is False,
          "a card without the extension declares lines=False",
          hub._legion_row("old"))
    rid, err, code = hub.legion_send("old", "old friend")
    check(err is None and code == 200, "an order to an older cohort is accepted",
          (err, code))
    check(wait_record_done(hub, "old", timeout=30),
          "it finishes through the blocking reply", hub.legion_record("old"))
    lines = [l for r in hub.web_transcript(hub.legion_key("old"))["runs"]
             for l in (r.get("lines") or [])]
    check(any(l.get("kind") == "final" and "old friend" in (l.get("text") or "")
              for l in lines),
          "the final answer is drawn even without the extension",
          [l.get("text") for l in lines])


def test_hub_unreachable(hub):
    """A dead cohort: send returns, the row reads silent, the run fails loudly."""
    hub.CONFIG["agent"].setdefault("a2a_remotes", {})["dead"] = {
        "url": "http://127.0.0.1:1", "token_env": "LEGION_TEST_TOKEN"}
    saved_max = hub.LEGION_TASK_MAX_SECONDS
    saved_slow = hub.LEGION_SLOW_POLL_SECONDS
    hub.LEGION_TASK_MAX_SECONDS = 6
    hub.LEGION_SLOW_POLL_SECONDS = 1.0
    try:
        hub._legion_probe_card("dead")
        check(hub._legion_row("dead")["state"] == "silent"
              and hub._legion_row("dead")["error"],
              "an unreachable cohort reads silent with its error",
              hub._legion_row("dead"))
        t0 = time.time()
        rid, err, code = hub.legion_send("dead", "anyone home?")
        took = time.time() - t0
        check(err is None and code == 200 and took < 2.0,
              "the order to a dead cohort still returns at once", (err, round(took, 3)))
        check(wait_record_done(hub, "dead", timeout=45),
              "and gives up by its own deadline", hub.legion_record("dead"))
        check(hub.legion_record("dead").get("last_state") == "failed",
              "the record says failed")
        texts = " ".join(
            str(l.get("text") or "")
            for r in hub.web_transcript(hub.legion_key("dead"))["runs"]
            for l in (r.get("lines") or []))
        check("not answering" in texts or "never reported" in texts,
              "the transcript says what happened, in words", texts[:200])
        check("nothing was re-sent" in texts or "re-issue" in texts,
              "...and that nothing was re-run silently", texts[:200])
    finally:
        hub.LEGION_TASK_MAX_SECONDS = saved_max
        hub.LEGION_SLOW_POLL_SECONDS = saved_slow
        hub.CONFIG["agent"]["a2a_remotes"].pop("dead", None)


def test_hub_restart_repair(hub, state):
    """A hub that died mid-order repairs itself from the cohort's own store."""
    state["tasks"]["T-42"] = completed_task("T-42", "disks are fine")
    hub._legion_record("coh", context_id="ctx-1", last_task_id="T-42",
                       last_state="working", started=time.time() - 10)
    hub.web_runlog_append(hub.legion_key("coh"), "T-42", time.time() - 10,
                          [{"i": 0, "uid": "T-42p0", "kind": "you",
                            "text": "check the disks", "t": 0, "r": 0}])
    hub._LEGION_REATTACH_AT.clear()
    hub._legion_census_pass()
    check(hub.legion_record("coh").get("last_state") == "done",
          "the repair closed the record", hub.legion_record("coh"))
    lines = [l for r in hub.web_transcript(hub.legion_key("coh"))["runs"]
             if r.get("run_id") == "T-42" for l in (r.get("lines") or [])]
    check(any(l.get("kind") == "final" and l.get("text") == "disks are fine"
              for l in lines),
          "the answer landed in the transcript", lines[-2:])
    check(len([l for l in lines if l.get("kind") == "final"]) == 1,
          "...exactly once")
    # ...and a task the cohort does NOT know is reported, never re-sent
    hub._legion_record("coh", last_task_id="T-vanished", last_state="working",
                       started=time.time() - 10)
    hub._LEGION_REATTACH_AT.clear()
    hub._legion_census_pass()
    lines = [l for r in hub.web_transcript(hub.legion_key("coh"))["runs"]
             for l in (r.get("lines") or [])]
    check(hub.legion_record("coh").get("last_state") == "failed"
          and any("nothing was re-run" in str(l.get("text") or "") for l in lines),
          "a task the cohort lost is REPORTED, never re-sent", lines[-2:])
    check(state["runs"] == 1,
          "no re-attach probe ever re-ran an order", state["runs"])


def test_hub_no_cohorts(hub):
    """A hub with nothing configured answers an empty legion and starts no clock."""
    saved = hub.CONFIG["agent"].get("a2a_remotes")
    hub.CONFIG["agent"]["a2a_remotes"] = {}
    try:
        ov = hub.legion_overview("")
        check(ov["cohorts"] == [] and ov["tabs"] == [] and ov["praetorium"]["version"],
              "an empty hub answers an empty overview with its own version", ov)
        rid, err, code = hub.legion_send("anyone", "hello")
        check(rid is None and code == 404 and "no cohort" in (err or ""),
              "and refuses an order to a cohort it does not have", (err, code))
    finally:
        hub.CONFIG["agent"]["a2a_remotes"] = saved or {}


def main():
    coh, _stage1 = stage_module("tinycmdr_legion_cohort", "cohort")
    hub, _stage2 = stage_module("tinycmdr_legion_hub", "hub")
    # "coh" is HELD (the dispatch test looks at a live run), "slow" answers after the
    # second GetTask (the timed-out socket), "old" speaks no run-lines extension
    srv, state = make_peer(declare_lines=True, mode="held")
    srv_slow, state_slow = make_peer(declare_lines=True, mode="slow")
    srv_old, _state_old = make_peer(declare_lines=False, mode="fast")
    try:
        test_cohort_frame(coh)
        test_cohort_policy_and_card(coh)

        hub_with_peer(hub, srv, "coh")
        hub_with_peer(hub, srv_slow, "slow")
        hub_with_peer(hub, srv_old, "old")

        test_hub_targets(hub)
        test_hub_store_bounds(hub)
        test_hub_egress_warning(hub)
        test_hub_no_cohorts(hub)
        test_hub_dispatch(hub, state)
        test_hub_busy_refusal(hub)
        test_hub_task_store_fallback(hub, state_slow)
        test_hub_lines_fallback(hub)
        test_hub_unreachable(hub)
        test_hub_restart_repair(hub, state)
    finally:
        for s in (srv, srv_slow, srv_old):
            s.shutdown()

    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all legion checks passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
