"""test_a2_surface - one merged suite (test_a2a, test_a2ui).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: test_a2a: globals()-> _ns; test_a2ui: globals()-> _ns.
"""
import os
import sys


def _run(name, fn):
    """One member, its own snapshot: env, cwd and sys.path restored afterwards."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_path = list(sys.path)
    print("== member %s: start" % name)
    try:
        rc = fn()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
        sys.path[:] = saved_path
    rc = int(rc or 0)
    print("== member %s: exit %d" % (name, rc))
    return rc


def _suite_test_a2a():
    """A2A: the card, the JSON-RPC lifecycle, and the client tool's two halves.

The model half sits behind a hook, so everything the mesh depends on is graded
without an endpoint: the card a peer discovers, SendMessage -> a Task whose state and
parts follow the spec, the client's taskId as an idempotency key (a WORKING placeholder
is stored before the run, so a retry is answered and not re-run), the error codes a
client will meet (TaskNotFound, NotCancelable, UnsupportedOperation,
ContentTypeNotSupported, VersionNotSupported), and the client tool against a live stub
server. The last check group stages a SECOND copy with
`a2a_remotes` set, because the whole rent argument is that a box with no peers
registers no tool at all.

    python tests/test_a2_surface.py
"""
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / (os.environ.get("TINYCMDR_TEST_APP")
                  or os.environ.get("TINYCMDR_SRC") or "tinycmdr.py")
    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-a2a"
    STAGE.mkdir(parents=True, exist_ok=True)


    def stage(name, remotes=None):
        work = STAGE / name
        work.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC, work / "tinycmdr.py")
        cfg = {"llm": {"model": "stub"},
               "agent": {"a2a_remotes": remotes or {}}}
        (work / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("tinycmdr_a2a_" + name,
                                                      work / "tinycmdr.py")
        fb = importlib.util.module_from_spec(spec)
        sys.modules["tinycmdr_a2a_" + name] = fb
        spec.loader.exec_module(fb)
        return fb


    OFF = stage("off")
    FAILURES, PASSES = [], []


    def check(name, cond, detail=""):
        if cond:
            PASSES.append(name)
            print(f"ok   {name}")
        else:
            FAILURES.append(name)
            print(f"FAIL {name}: {detail}")


    def test_the_card_is_what_a_peer_discovers():
        card = OFF.a2a_card()
        for key in ("name", "description", "version", "supportedInterfaces",
                    "capabilities", "defaultInputModes", "defaultOutputModes", "skills"):
            check(f"the card carries {key}", key in card, sorted(card))
        iface = (card.get("supportedInterfaces") or [{}])[0]
        check("the interface is JSON-RPC on /a2a",
              iface.get("protocolBinding") == "JSONRPC"
              and str(iface.get("url", "")).endswith("/a2a"), iface)
        check("...and declares protocol version 1.0",
              iface.get("protocolVersion") == "1.0", iface)
        check("streaming and push are honestly declared off",
              card["capabilities"].get("streaming") is False
              and card["capabilities"].get("pushNotifications") is False,
              card["capabilities"])
        check("the bearer scheme is named for the page token",
              "bearer" in (card.get("securitySchemes") or {}), card.get("securitySchemes"))
        check("the card advertises at least one skill", bool(card.get("skills")), card)


    def test_a_client_task_id_is_its_retry_handle():
        """A peer whose read timed out resends the same taskId. The id must be stored
    before the run (so GetTask answers WORKING while the run is up), a retry must
    return that same stored task, and a run must never happen twice for one id."""
        calls, seen = [], {}

        def retry_message():
            return {"messageId": "retry-1", "role": "ROLE_USER",
                    "parts": [{"text": "how full is the disk?"}], "taskId": "client-task-1"}

        def hook(text, ctx):
            calls.append(text)
            if len(calls) > 1:
                # A build without the placeholder re-enters here for the retry; the
                # check below then sees a second run instead of a stored task.
                return "one answer", False
            with OFF._A2A_LOCK:
                seen["stored"] = OFF._A2A_TASKS.get("client-task-1")
            seen["gettask"] = OFF.a2a_handle("GetTask", {"id": "client-task-1"})
            seen["retry"] = OFF.a2a_handle("SendMessage", {"message": retry_message()})
            return "one answer", False

        saved = OFF._A2A_RUN_HOOK
        OFF._A2A_RUN_HOOK = hook
        try:
            task, err = OFF.a2a_handle("SendMessage", {"message": {
                "messageId": "first-1", "role": "ROLE_USER",
                "parts": [{"text": "how full is the disk?"}],
                "contextId": "ctx-retry", "taskId": "client-task-1"}})
            check("SendMessage with a client taskId answers under that id",
                  err is None and task["id"] == "client-task-1", err or task)
            stored = seen.get("stored")
            check("during the run the id is already stored as a WORKING task",
                  bool(stored) and stored["id"] == "client-task-1"
                  and stored["status"]["state"] == "TASK_STATE_WORKING", stored)
            check("the placeholder is a well-formed task (timestamp + history)",
                  bool(stored) and bool(stored["status"].get("timestamp"))
                  and stored["history"][0]["messageId"] == "first-1"
                  and stored.get("contextId") == "ctx-retry", stored)
            got, gerr = seen.get("gettask") or (None, None)
            check("GetTask during the run answers WORKING, not TaskNotFoundError",
                  gerr is None and got["status"]["state"] == "TASK_STATE_WORKING",
                  gerr or got)
            again, aerr = seen.get("retry") or (None, None)
            check("a second SendMessage for the in-flight id answers the stored task",
                  aerr is None and again["id"] == "client-task-1"
                  and again["status"]["state"] == "TASK_STATE_WORKING", aerr or again)
            check("...and the model half ran exactly once",
                  calls == ["how full is the disk?"], calls)
            check("the finished task replaces the placeholder",
                  task["status"]["state"] == "TASK_STATE_COMPLETED", task["status"])
            repeat, rerr = OFF.a2a_handle("SendMessage", {"message": retry_message()})
            check("a repeat SendMessage after completion returns the finished task",
                  rerr is None and repeat["status"]["state"] == "TASK_STATE_COMPLETED",
                  rerr or repeat)
            check("...still without running the model half again",
                  calls == ["how full is the disk?"], calls)
            # A threaded door can have two runs land under one id; the second finished
            # store must keep the first result rather than clobber it.
            with OFF._A2A_LOCK:
                settled = OFF._A2A_TASKS["client-task-1"]
            OFF._a2a_store({"id": "client-task-1", "history": [],
                            "status": {"state": "TASK_STATE_FAILED"}})
            with OFF._A2A_LOCK:
                after = OFF._A2A_TASKS["client-task-1"]
            check("a second finished store for the id keeps the first result",
                  after is settled and after["status"]["state"] == "TASK_STATE_COMPLETED",
                  after["status"])
        finally:
            OFF._A2A_RUN_HOOK = saved
            with OFF._A2A_LOCK:
                OFF._A2A_TASKS.pop("client-task-1", None)
                if "client-task-1" in OFF._A2A_TASKS_ORDER:
                    OFF._A2A_TASKS_ORDER.remove("client-task-1")


    def test_send_message_returns_a_task():
        OFF._A2A_RUN_HOOK = lambda text, ctx: ("root is 65% full", False)
        try:
            msg = {"messageId": "m1", "role": "ROLE_USER",
                   "parts": [{"text": "how full is the disk?"}], "contextId": "ctx-1"}
            task, err = OFF.a2a_handle("SendMessage", {"message": msg})
            check("SendMessage answers with a task, not an error", err is None, err)
            check("the task is COMPLETED", task["status"]["state"] == "TASK_STATE_COMPLETED",
                  task["status"])
            check("...and carries the answer as a text part",
                  task["status"]["message"]["parts"][0]["text"] == "root is 65% full",
                  task["status"]["message"])
            check("...as an artifact too",
                  task["artifacts"][0]["parts"][0]["text"] == "root is 65% full",
                  task["artifacts"])
            check("...and the client's message in history",
                  len(task["history"]) == 2 and task["history"][0]["role"] == "ROLE_USER",
                  task["history"])
            check("the context id is echoed", task.get("contextId") == "ctx-1", task)
            got, err = OFF.a2a_handle("GetTask", {"id": task["id"]})
            check("GetTask reads the same task back", err is None and got["id"] == task["id"],
                  err or got)
            listed, _ = OFF.a2a_handle("ListTasks", {})
            check("ListTasks counts it", listed["totalSize"] >= 1
                  and listed["nextPageToken"] == "", listed)
            # A task ring bigger than one page must hand out a real continuation token: an
            # empty one while tasks remain tells a spec-following client the page is last.
            OFF.a2a_handle("SendMessage",
                           {"message": {"messageId": "m-page2", "role": "ROLE_USER",
                                        "parts": [{"text": "second"}]}})
            _all, _ = OFF.a2a_handle("ListTasks", {})
            _p1, _ = OFF.a2a_handle("ListTasks", {"pageSize": 1})
            check("a non-final page carries a continuation token",
                  _all["totalSize"] >= 2 and _p1["pageSize"] == 1
                  and _p1["nextPageToken"] == "1", (_all["totalSize"], _p1))
            _p2, _ = OFF.a2a_handle("ListTasks", {"pageSize": 100,
                                                  "pageToken": _p1["nextPageToken"]})
            check("...and the following page carries the rest and ends cleanly",
                  len(_p2["tasks"]) == _all["totalSize"] - 1
                  and _p2["nextPageToken"] == "", _p2)
            OFF._A2A_RUN_HOOK = lambda text, ctx: ("boom", True)
            failed, _ = OFF.a2a_handle("SendMessage",
                                       {"message": {"messageId": "m2", "role": "ROLE_USER",
                                                    "parts": [{"text": "x"}]}})
            check("a failed run is TASK_STATE_FAILED",
                  failed["status"]["state"] == "TASK_STATE_FAILED", failed["status"])
        finally:
            OFF._A2A_RUN_HOOK = None


    def test_the_error_map():
        msg, _ = OFF._a2a_text_of({"parts": [{"data": {"x": 1}}]})
        check("a non-text part is ContentTypeNotSupportedError (-32005)",
              OFF.a2a_handle("SendMessage", {"message": {"parts": [{"data": {}}]}})[1]["code"]
              == -32005)
        check("an empty parts list is Invalid params (-32602)",
              OFF.a2a_handle("SendMessage", {"message": {"parts": []}})[1]["code"] == -32602)
        check("a text-empty message is Invalid params (-32602)",
              OFF.a2a_handle("SendMessage",
                             {"message": {"parts": [{"text": "  "}]}})[1]["code"] == -32602)
        check("an unknown task is TaskNotFoundError (-32001)",
              OFF.a2a_handle("GetTask", {"id": "nope"})[1]["code"] == -32001)
        check("cancelling is TaskNotCancelableError (-32002)",
              OFF.a2a_handle("CancelTask", {"id": "x"})[1]["code"] == -32002)
        check("streaming is UnsupportedOperationError (-32004)",
              OFF.a2a_handle("SendStreamingMessage", {})[1]["code"] == -32004)
        check("a 2.x A2A-Version is VersionNotSupportedError (-32009)",
              OFF.a2a_handle("SendMessage", {}, version="2.0")[1]["code"] == -32009)
        check("...while 1.0 passes the version gate",
              OFF.a2a_handle("GetTask", {"id": "nope"}, version="1.0")[1]["code"] == -32001)
        status, resp = OFF.a2a_rpc({"id": 7, "method": "GetTask", "params": {"id": "nope"}})
        check("a bad envelope is Invalid Request (-32600) with status 400",
              status == 400 and resp["error"]["code"] == -32600, (status, resp))
        status, resp = OFF.a2a_rpc({"jsonrpc": "2.0", "id": 7, "method": "GetTask",
                                    "params": {"id": "nope"}})
        check("a well-formed call answers 200 with the error block",
              status == 200 and resp["error"]["code"] == -32001 and resp["id"] == 7,
              (status, resp))
        status, resp = OFF.a2a_http("/a2a", {"jsonrpc": "2.0", "id": 1, "method": "GetTask",
                                             "params": {}}, token_ok=False)
        check("without a token the endpoint is 401", status == 401, (status, resp))
        status, resp = OFF.a2a_http("/.well-known/agent-card.json", {}, token_ok=False)
        check("the card needs no token", status == 200 and resp.get("name") == "tinycmdr",
              (status, resp))


    class Stub(BaseHTTPRequestHandler):
        """A minimal A2A peer: a card and one SendMessage answer."""
        calls = []

        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            Stub.calls.append(("GET", self.path, dict(self.headers)))
            if self.path.startswith("/.well-known/agent-card.json"):
                self._json({"name": "peer-box", "version": "9.9.9",
                            "description": "a stub peer", "skills": [{"name": "stub"}]})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            Stub.calls.append(("POST", self.path, dict(self.headers), body))
            text = body.get("params", {}).get("message", {}).get("parts", [{}])[0].get("text", "")
            self._json({"jsonrpc": "2.0", "id": body.get("id"),
                        "result": {"id": "peer-task-1",
                                   "status": {"state": "TASK_STATE_COMPLETED",
                                              "message": {"role": "ROLE_AGENT",
                                                          "parts": [{"text": "peer says: " + text}]}},
                                   "artifacts": [{"artifactId": "a1",
                                                  "parts": [{"text": "peer says: " + text}]}]}})


    def test_the_client_tool_against_a_live_peer():
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            OFF.CONFIG["agent"]["a2a_remotes"] = {
                "peer": {"url": "http://127.0.0.1:%d" % port, "token_env": "A2A_STUB_TOKEN"}}
            os.environ["A2A_STUB_TOKEN"] = "stub-token"
            try:
                out = OFF.tool_a2a({"action": "list"}, {})
                check("list names the configured remote", "peer -> http://127.0.0.1" in out, out)
                out = OFF.tool_a2a({"action": "card", "remote": "peer"}, {})
                check("card reads the peer's card", "peer-box" in out and "9.9.9" in out, out)
                out = OFF.tool_a2a({"action": "send", "remote": "peer",
                                    "message": "status?"}, {})
                check("send returns the peer's answer", "peer says: status?" in out, out)
                post = [c for c in Stub.calls if c[0] == "POST"][-1]
                check("...with the bearer from token_env",
                      post[2].get("Authorization") == "Bearer stub-token", post[2])
                check("...and a spec-shaped message",
                      post[3]["params"]["message"]["role"] == "ROLE_USER"
                      and post[3]["params"]["message"]["parts"][0]["text"] == "status?",
                      post[3])
                out = OFF.tool_a2a({"action": "send", "remote": "ghost", "message": "x"}, {})
                check("an unknown remote is refused by name", out.startswith("ERROR"), out)
                out = OFF.tool_a2a({"action": "send", "remote": "peer", "message": ""}, {})
                check("send with no words is refused", out.startswith("ERROR"), out)
            finally:
                OFF.CONFIG["agent"]["a2a_remotes"] = {}
                os.environ.pop("A2A_STUB_TOKEN", None)
        finally:
            srv.shutdown()


    def test_a_box_with_no_peers_registers_no_tool():
        check("off: a box with no remotes has no a2a tool", "a2a" not in OFF.CORE_TOOLS,
              sorted(OFF.CORE_TOOLS)[:6])
        on = stage("on", remotes={"peer": {"url": "http://127.0.0.1:1"}})
        check("on: the same build with remotes registers it", "a2a" in on.CORE_TOOLS,
              "not registered")
        check("...hidden from the default core", "a2a" not in on._DEFAULT_CORE)
        check("...under the per-tool rent cap",
              len(json.dumps(on.CORE_TOOLS["a2a"]["schema"])) <= 1200,
              len(json.dumps(on.CORE_TOOLS["a2a"]["schema"])))


    def test_an_infra_failure_is_a_failed_task():
        """A run whose endpoint never answered returns a composed answer; to a peer that
    is a FAILED task, the same verdict the done line goes red on."""
        saved_drive, saved_usage = OFF.drive_run, dict(OFF.AGENT.last_usage)
        try:
            OFF.drive_run = lambda *a, **k: "no endpoint answered"
            OFF.AGENT.last_usage["a2a-ctx-infra"] = {"infra_failed": True}
            answer, failed = OFF.a2a_run("x", "ctx-infra")
            check("an infra-failed run reports failed", failed is True and answer,
                  (failed, answer))
            task, _ = OFF.a2a_handle("SendMessage", {"message": {
                "messageId": "m3", "role": "ROLE_USER", "parts": [{"text": "x"}],
                "contextId": "ctx-infra"}})
            check("...and the task says TASK_STATE_FAILED",
                  task["status"]["state"] == "TASK_STATE_FAILED", task["status"])
        finally:
            OFF.drive_run = saved_drive
            OFF.AGENT.last_usage.clear()
            OFF.AGENT.last_usage.update(saved_usage)


    def test_a_bad_page_size_is_the_params_error():
        """pageSize was the one peer field ListTasks converted without a guard: a string
    ("ten"), a list or a map raised out of a2a_handle - and out of a2a_rpc, so the HTTP
    door dropped the connection - where pageToken beside it has answered the params
    error since it was written. Measured 2026-10-07."""
        try:
            res, err = OFF.a2a_handle("ListTasks", {"pageSize": "ten"})
        except Exception as e:                                       # noqa: BLE001
            res, err = "RAISED", "%s: %s" % (type(e).__name__, e)
        check("a non-numeric pageSize is a params error, not a crash",
              res is None and isinstance(err, dict) and err.get("code") == -32602, (res, err))
        try:
            status, body = OFF.a2a_rpc({"jsonrpc": "2.0", "id": 4, "method": "ListTasks",
                                        "params": {"pageSize": ["nope"]}})
        except Exception as e:                                       # noqa: BLE001
            status, body = "RAISED", "%s: %s" % (type(e).__name__, e)
        check("...and the JSON-RPC shim answers it as an error object",
              status == 200 and body.get("error", {}).get("code") == -32602, (status, body))
        ok, err2 = OFF.a2a_handle("ListTasks", {"pageSize": 2})
        check("...while a real number still pages", err2 is None and ok["pageSize"] <= 2,
              (ok, err2))


    def main():
        tests = [v for k, v in sorted(_ns.items()) if k.startswith("test_")]
        for fn in tests:
            fn()
        print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
        return 1 if FAILURES else 0
    _ns = dict(locals())
    return main()


def _suite_test_a2ui():
    """A2UI cards: the envelope, this renderer's catalog, and the door's honesty.

The agent draws on the page with a standard payload (A2UI v1.0 `createSurface`), so
what is graded here is the contract a renderer depends on: a well-formed payload is
accepted and summarized, and every malformed one is refused BY NAME rather than
half-drawn. The tool must also never pretend: with no surface on the lane it says so.

    python tests/test_a2_surface.py
"""
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / (os.environ.get("TINYCMDR_TEST_APP")
                  or os.environ.get("TINYCMDR_SRC") or "tinycmdr.py")
    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-a2ui"
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    spec = importlib.util.spec_from_file_location("tinycmdr_a2ui_under_test",
                                                  STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_a2ui_under_test"] = fb
    spec.loader.exec_module(fb)
    FAILURES, PASSES = [], []


    def check(name, cond, detail=""):
        if cond:
            PASSES.append(name)
            print(f"ok   {name}")
        else:
            FAILURES.append(name)
            print(f"FAIL {name}: {detail}")


    def card(**over):
        surface = {"surfaceId": "c1",
                   "components": [{"id": "root", "component": "Column",
                                   "children": ["t1", "d1"]},
                                  {"id": "t1", "component": "Text", "text": "Disk report",
                                   "variant": "title"},
                                  {"id": "d1", "component": "Divider"}]}
        surface.update(over.pop("surface", {}))
        out = {"version": "v1.0", "createSurface": surface}
        out.update(over)
        return out


    def test_a_well_formed_card_is_accepted_and_summarized():
        ok, why, summary = fb.a2ui_validate(card())
        check("a card from this catalog is accepted", ok, why)
        check("the summary is the first text", summary == "Disk report", repr(summary))
        out = fb.tool_render_ui({"payload": card()}, {"render_ui": lambda p, s:
                                                      "OK: card on the page (%s)" % s})
        check("the tool hands the payload to the lane's door",
              out == "OK: card on the page (Disk report)", out)


    def test_the_envelope_is_enforced():
        bad = card(version="v0.9")
        check("a wrong version is refused", not fb.a2ui_validate(bad)[0])
        check("...by name", "version" in fb.a2ui_validate(bad)[1], fb.a2ui_validate(bad)[1])
        check("a payload without createSurface is refused",
              not fb.a2ui_validate({"version": "v1.0"})[0])
        check("an empty component list is refused",
              not fb.a2ui_validate(card(surface={"components": []}))[0])
        check("a missing root is refused",
              "root" in fb.a2ui_validate(card(surface={"components": [
                  {"id": "x", "component": "Text", "text": "hi"}]}))[1])


    def test_this_catalog_is_enforced():
        ok, why, _ = fb.a2ui_validate(card(surface={"components": [
            {"id": "root", "component": "Chart"}]}))
        check("an unknown component is refused", not ok)
        check("...and the error names the catalog", "Card" in why and "Chart" in why, why)
        check("a duplicate id is refused",
              "duplicate" in fb.a2ui_validate(card(surface={"components": [
                  {"id": "root", "component": "Column", "children": ["t"]},
                  {"id": "t", "component": "Text", "text": "a"},
                  {"id": "t", "component": "Text", "text": "b"}]}))[1])
        check("a dangling child id is refused",
              "not in this payload" in fb.a2ui_validate(card(surface={"components": [
                  {"id": "root", "component": "Column", "children": ["ghost"]}]}))[1])
        check("a self-referencing root is refused",
              not fb.a2ui_validate(card(surface={"components": [
                  {"id": "root", "component": "Column", "children": ["root"]}]}))[0])
        check("...and the reason names it",
              "reachable more than once" in fb.a2ui_validate(card(surface={"components": [
                  {"id": "root", "component": "Column",
                   "children": ["root"]}]}))[1])
        check("a child listed twice is refused",
              not fb.a2ui_validate(card(surface={"components": [
                  {"id": "root", "component": "Column", "children": ["t", "t"]},
                  {"id": "t", "component": "Text", "text": "a"}]}))[0])
        check("a shared child (a diamond) is refused",
              not fb.a2ui_validate(card(surface={"components": [
                  {"id": "root", "component": "Column", "children": ["a", "b"]},
                  {"id": "a", "component": "Column", "children": ["t"]},
                  {"id": "b", "component": "Column", "children": ["t"]},
                  {"id": "t", "component": "Text", "text": "x"}]}))[0])
        check("a Text with children is refused",
              "cannot have children" in fb.a2ui_validate(card(surface={"components": [
                  {"id": "root", "component": "Column", "children": ["t"]},
                  {"id": "t", "component": "Text", "text": "x", "children": ["root"]}]}))[1])
        check("a Text without text is refused",
              "needs text" in fb.a2ui_validate(card(surface={"components": [
                  {"id": "root", "component": "Column", "children": ["t"]},
                  {"id": "t", "component": "Text"}]}))[1])
        check("a data binding is a legal Text",
              fb.a2ui_validate(card(surface={"components": [
                  {"id": "root", "component": "Text", "text": {"path": "/x"}}],
                  "dataModel": {"x": "hello"}}))[0])


    def test_caps_and_the_door():
        long_text = "x" * (fb._A2UI_MAX_TEXT + 1)
        check("an over-long Text is refused",
              "cap" in fb.a2ui_validate(card(surface={"components": [
                  {"id": "root", "component": "Text", "text": long_text}]}))[1])
        many = [{"id": "root", "component": "Column",
                 "children": ["c%d" % i for i in range(fb._A2UI_MAX_COMPONENTS + 1)]}]
        many += [{"id": "c%d" % i, "component": "Text", "text": "x"}
                 for i in range(fb._A2UI_MAX_COMPONENTS + 1)]
        check("an over-long component list is refused",
              "cap" in fb.a2ui_validate(card(surface={"components": many}))[1])
        wide = card(surface={"components": [
            {"id": "root", "component": "Text", "text": "y" * 1999}]})
        wide["createSurface"]["pad"] = "z" * 5000
        out = fb.tool_render_ui({"payload": wide}, {"render_ui": lambda p, s: "OK"})
        check("a payload over the char cap is refused by the tool",
              out.startswith("ERROR: the payload is") and "cap" in out, out[:160])
        out = fb.tool_render_ui({"payload": card()}, {})
        check("with no door the tool says the lane has no surface",
              out.startswith("ERROR: this lane has no surface"), out[:160])
        out = fb.tool_render_ui({"payload": "{not json"}, {"render_ui": lambda p, s: "OK"})
        check("a payload that is not JSON is refused", out.startswith("ERROR: payload is not JSON"),
              out[:120])
        seen = {}
        out = fb.tool_render_ui({"payload": card()},
                                {"render_ui": lambda p, s: seen.update(p=p, s=s) or "OK"})
        check("...and a good call reaches the door with the payload intact",
              seen.get("p", {}).get("createSurface", {}).get("surfaceId") == "c1", seen)


    def test_the_tool_is_hidden_by_default():
        names = set(fb.REGISTRY.tools) if hasattr(fb.REGISTRY, "tools") else set()
        check("render_ui is registered", "render_ui" in fb.CORE_TOOLS or "render_ui" in names,
              sorted(names)[:8])
        check("...and not in the default-visible core",
              "render_ui" not in fb._DEFAULT_CORE, fb._DEFAULT_CORE)
        check("its schema is under the per-tool rent cap",
              len(json.dumps(fb.CORE_TOOLS["render_ui"]["schema"])) <= 1200,
              len(json.dumps(fb.CORE_TOOLS["render_ui"]["schema"])))


    def main():
        tests = [v for k, v in sorted(_ns.items()) if k.startswith("test_")]
        for fn in tests:
            fn()
        print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
        return 1 if FAILURES else 0
    _ns = dict(locals())
    return main()


def main():
    rc = 0
    for name, fn in (("test_a2a", _suite_test_a2a), ("test_a2ui", _suite_test_a2ui)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
