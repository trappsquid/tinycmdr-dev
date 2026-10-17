"""test_model_surface - one merged suite (test_setup, test_model_setup, test_profiles).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: no member needed a namespace rewrite.
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


def _suite_test_setup():
    """`tinycmdr setup` asks about web-search consent, and writes the answer.

The wizard covered the model endpoint and the two chat gateways, and nothing else: the
egress consent that `web_search`/`fetch_url` answer to was only asked by the INSTALLER
and by `tinycmdr config set`, so an existing install had no interactive door to it. A
lookup that needs the web then came back "BLOCKED ... allow_cloud_egress is false" with
the operator holding no obvious way to say yes.

The wizard is driven here through a fake tty (it refuses a pipe), with every other
answer left empty so only the search section is doing anything; the model section also
grades that an endpoint advertising other ids than the configured model still OFFERS the
configured one (reported 2026-10-12).

    python tests/test_model_surface.py
"""
    import contextlib
    import importlib.util
    import io
    import json
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    SRC = (Path(__file__).resolve().parent.parent
           / os.environ.get("TINYCMDR_SRC", "tinycmdr.py"))
    PASSES, FAILS = [], []


    def check(cond, what, extra=""):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}\n     {extra}")
        else:
            PASSES.append(what)
            print(f"ok   {what}")


    class FakeTTY(io.StringIO):
        """input() reads this, and isatty() lets the wizard past its guard."""

        def isatty(self):
            return True


    def stage(dirpath, egress):
        dirpath.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC, dirpath / "tinycmdr.py")
        (dirpath / "config.json").write_text(json.dumps({
            "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "main"},
            "search": {"allow_cloud_egress": egress},
        }), encoding="utf-8")
        return dirpath


    def drive(dirpath, answer, page=("", "", "", ""), extra=(), patch=None):
        """Run the wizard with every answer empty but the page's and the last one.

    Prompt order: local/cloud, url, model, Mattermost?, Telegram?, page-serve,
    page-LAN (only when serving), page-port (only when serving), page-token (only
    when serving), the web-search menu. `page` carries the four page answers; `answer`
    is the menu's, and `extra` carries what choosing "4" (add your own provider) asks
    after it (kind, url, label, and a key where its kind asks for one). `patch` is
    applied to the module before it runs - a `run_capture`/`is_elevated` fake, so no
    test run ever touches a real firewall.
    """
        spec = importlib.util.spec_from_file_location("setup_" + dirpath.name,
                                                      dirpath / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        if patch:
            patch(mod)
        mod.__dict__["_ANSWERS"] = ["", "", "", "", ""] + list(page) + [answer] + list(extra)
        old_in = sys.stdin
        sys.stdin = FakeTTY("\n".join(mod.__dict__["_ANSWERS"]) + "\n")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = mod.run_setup()
        finally:
            sys.stdin = old_in
        written = json.loads((dirpath / "config.json").read_text(encoding="utf-8"))
        return rc, buf.getvalue(), written, mod


    def main():
        work = Path(tempfile.mkdtemp(prefix="fbsetup-"))
        try:
            # a fresh install allows the off-LAN providers (choice 2)
            d = stage(work / "yes", False)
            rc, out, written, mod = drive(d, "2")
            check(rc == 0, f"the wizard completes ({rc})", out[-300:])
            check("off this machine" in out and "searxng" in out,
                  "the web-search step names the provider shapes and the consent", out[-500:])
            check(written.get("search", {}).get("allow_cloud_egress") is True,
                  "choosing 2 writes search.allow_cloud_egress = true",
                  json.dumps(written.get("search")))
            check(mod.CONFIG["search"]["allow_cloud_egress"] is True,
                  "and the running process picks it up without a restart")
            check("Web search" in out and "off-LAN providers allowed" in out,
                  "the summary reports it", out[-400:])

            # and choice 3 keeps search on this machine only
            d = stage(work / "no", True)
            rc, out, written, mod = drive(d, "3")
            check(rc == 0 and written["search"]["allow_cloud_egress"] is False,
                  "choosing 3 writes false", json.dumps(written.get("search")))
            check("this LAN only" in out, "and the summary says so", out[-300:])

            # choice 4 asks for the provider itself - url, optional key, label - the input
            # path for one of your OWN, and it is tried first
            d = stage(work / "add", True)
            rc, out, written, mod = drive(d, "4",
                                          extra=("http://127.0.0.1:8890", "", "mybox", "n"))
            _prov = (written.get("search") or {}).get("providers") or []
            check(rc == 0 and _prov and _prov[0] == {"kind": "generic",
                                                     "url": "http://127.0.0.1:8890",
                                                     "label": "mybox"},
                  "choosing 4 asks for the provider and writes it first",
                  json.dumps(_prov))

            # ...and a keyed one lands with its key in .env and only its NAME in config.json
            d = stage(work / "key", True)
            rc, out, written, mod = drive(
                d, "4", extra=("https://api.example.com/s", "sk-live-42", "", "n"))
            _prov = (written.get("search") or {}).get("providers") or []
            _env = (d / ".env").read_text(encoding="utf-8") if (d / ".env").exists() else ""
            check(rc == 0 and _prov and _prov[0].get("api_key_env") == "TINYCMDR_SEARCH1_API_KEY"
                  and "sk-live-42" not in json.dumps(_prov)
                  and "TINYCMDR_SEARCH1_API_KEY=sk-live-42" in _env,
                  "a typed key goes to .env; config.json only names the variable",
                  (json.dumps(_prov), _env[-120:]))

            # ...and "add another" builds the chain: the entries in order ARE the order tried
            d = stage(work / "chain", True)
            rc, out, written, mod = drive(
                d, "4", extra=("http://127.0.0.1:8891", "", "first", "y",
                               "http://127.0.0.1:8892", "", "second", "n"))
            _prov = (written.get("search") or {}).get("providers") or []
            check(rc == 0 and [p.get("label") for p in _prov[:2]] == ["first", "second"],
                  "add-another makes an ordered chain", json.dumps(_prov[:2]))

            # Enter keeps whatever is already there, both ways
            for current in (True, False):
                d = stage(work / ("keep-%s" % current), current)
                rc, out, written, mod = drive(d, "")
                check(written["search"]["allow_cloud_egress"] is current,
                      f"Enter keeps the current value ({current})",
                      json.dumps(written.get("search")))

            # ---- the page: the wizard asks the bind and mints the token ---------------
            # The page is the default door and the one secret this project mints for you.
            # An install that predates it has no TINYCMDR_WEB_TOKEN, and the wizard is where
            # a person says whether other machines may reach it.
            def _quiet(mod):
                mod.is_elevated = lambda: True
                mod.run_capture = lambda *a, **k: (1, "", "", False)

            d = stage(work / "page-lan", False)
            # an earlier wizard in THIS process minted a token and left it in os.environ
            os.environ.pop("TINYCMDR_WEB_TOKEN", None)
            rc, out, written, mod = drive(d, "", page=("y", "y", "", ""), patch=_quiet)
            web = written.get("web") or {}
            check(rc == 0 and web.get("host") == "0.0.0.0",
                  "answering yes puts the page on the network (web.host 0.0.0.0)", web)
            env = (d / ".env").read_text(encoding="utf-8") if (d / ".env").exists() else ""
            tok = mod._web_token()
            check(bool(tok) and ("TINYCMDR_WEB_TOKEN=%s" % tok) in env,
                  "the token the page requires is minted into .env", env[-120:])
            check("token" not in json.dumps(web),
                  "and never into config.json", json.dumps(web))
            check("page link" in out and ("#token=" + tok) in out,
                  "the wizard prints the link to open", out[-300:])
            _summary = (out[out.find("Page         :"):].splitlines() or [""])[0]
            check("Page         : http://" in _summary and "0.0.0.0" not in _summary,
                  "the summary names an address a browser can open, not the 0.0.0.0 bind",
                  _summary)
            check("firewall    :" in out,
                  "the LAN answer is completed - the firewall state is printed", out[-700:])

            d = stage(work / "page-keep", False)
            rc, out, written, mod = drive(d, "", page=("", "", "", ""))
            check(rc == 0 and (written.get("web") or {}).get("host") == "127.0.0.1",
                  "Enter keeps the loopback bind", written.get("web"))

            d = stage(work / "page-off", False)
            rc, out, written, mod = drive(d, "", page=("n", "", "", ""))
            web = written.get("web") or {}
            check(rc == 0 and web.get("enabled") is False,
                  "answering no turns the page off (web.enabled false)", web)
            env = (d / ".env").read_text(encoding="utf-8") if (d / ".env").exists() else ""
            check("TINYCMDR_WEB_TOKEN" not in env,
                  "and mints no token for a page that will not start", env[-120:])
            check("Page         : (disabled)" in out,
                  "the summary says so", out[-400:])

            # ---- the token is SETTABLE, not mint-or-nothing (operator, 2026-10-04) -----
            # The wizard used to mint the token or keep the host's own; an operator holding a
            # token from a password manager had to hand-edit .env afterwards. Typing one must
            # save it, use it for the link, and retire a stale web.token in config.json so
            # nothing silently outranks it.
            d = stage(work / "page-own-token", False)
            (d / "config.json").write_text(json.dumps({
                "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "main"},
                "search": {"allow_cloud_egress": False},
                "web": {"enabled": True, "token": "stale-config-token"},
            }), encoding="utf-8")
            os.environ.pop("TINYCMDR_WEB_TOKEN", None)
            rc, out, written, mod = drive(d, "", page=("y", "y", "", "my-own-LAN-token-4242"),
                                          patch=_quiet)
            env = (d / ".env").read_text(encoding="utf-8") if (d / ".env").exists() else ""
            check(rc == 0 and "TINYCMDR_WEB_TOKEN=my-own-LAN-token-4242" in env,
                  "a token typed at the prompt is saved to .env", env[-160:])
            check(mod._web_token() == "my-own-LAN-token-4242",
                  "and it is the token the page will require", mod._web_token())
            check("token" not in json.dumps(written.get("web") or {}),
                  "a stale web.token in config.json was retired, so it cannot outrank .env",
                  json.dumps(written.get("web")))
            check("#token=my-own-LAN-token-4242" in out,
                  "the printed link carries the operator's token", out[-300:])

            # ---- the endpoint is PROBED before the wizard moves on --------------------
            # Operator, 2026-09-30: "there should be a point in the interactive installer that
            # checks if your link is even reachable before it continues". The staged URL is
            # 127.0.0.1:9 - a real, refused port - so this is the real probe, not a stub.
            d = stage(work / "reach", False)
            rc, out, written, mod = drive(d, "")
            check("\u2717 no answer from http://127.0.0.1:9/v1" in out
                  and "tinycmdr model endpoint" in out,
                  "a stored endpoint that does not answer is named, once, and pointed at the fix",
                  out[-600:])
            check(rc == 0 and written["llm"]["base_url"] == "http://127.0.0.1:9/v1",
                  "...and the wizard still finishes: the rest of the install has work to do", rc)

            # a TYPED url that fails is re-asked: someone correcting a typo is in the loop
            d = stage(work / "typed", False)
            spec = importlib.util.spec_from_file_location("setup_typed", d / "tinycmdr.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            answers = ["", "http://127.0.0.1:9/v1", "http://127.0.0.1:9/v2",
                       "http://127.0.0.1:9/v3", "", "", "", "", "", "", "", ""]  # kind, urls,
            # model, MM, TG, page x4, egress
            old_in = sys.stdin
            sys.stdin = FakeTTY("\n".join(answers) + "\n")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = mod.run_setup()
            finally:
                sys.stdin = old_in
            out = buf.getvalue()
            written = json.loads((d / "config.json").read_text(encoding="utf-8"))
            check(out.count("\u2717 no answer from") == 3,
                  "a typed URL is checked every time it is typed", out[-800:])
            check("keeping it anyway" in out and written["llm"]["base_url"] == "http://127.0.0.1:9/v3",
                  "...and after three tries it keeps the last one and says how to fix it",
                  (written["llm"]["base_url"], out[-400:]))

            # an endpoint that DOES answer reports what it advertised, and its ids become the
            # model to choose from (the numbered list is the picker's shell form)
            d = stage(work / "live", False)
            spec = importlib.util.spec_from_file_location("setup_live", d / "tinycmdr.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            mod.probe_endpoint = lambda url, key=None, **kw: {
                "ok": True, "ids": ["qwen3-14b", "glm-4.6"], "status": 200, "error": ""}
            # kind (local), url, model NUMBER, Mattermost, Telegram, page x4, egress
            answers = ["", "http://127.0.0.1:8081/v1", "2", "", "", "", "", "", "", ""]
            old_in = sys.stdin
            sys.stdin = FakeTTY("\n".join(answers) + "\n")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = mod.run_setup()
            finally:
                sys.stdin = old_in
            out = buf.getvalue()
            written = json.loads((d / "config.json").read_text(encoding="utf-8"))
            check("\u2713 reachable" in out and "qwen3-14b" in out and "glm-4.6" in out,
                  "a reachable endpoint says so, and lists what it advertises", out[:600])
            check(written["llm"]["model"] == "glm-4.6",
                  "...and a NUMBER in that list resolves to the model it names",
                  written["llm"]["model"])

            # ...and the model the box is CONFIGURED with is offered, not silently
            # replaced: "" keeps "main" even though the endpoint advertises other ids
            # (reported 2026-10-12 - the chooser made the endpoint's first advertised id
            # the default, so ENTER switched the model away from the configured one)
            d = stage(work / "live-keep", False)
            spec = importlib.util.spec_from_file_location("setup_live_keep",
                                                          d / "tinycmdr.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            mod.probe_endpoint = lambda url, key=None, **kw: {
                "ok": True, "ids": ["qwen3-14b", "glm-4.6"], "status": 200, "error": ""}
            # kind (local), url, model ENTER (keep), Mattermost, Telegram, page x4, egress
            answers = ["", "http://127.0.0.1:8081/v1", "", "", "", "", "", "", "", ""]
            old_in = sys.stdin
            sys.stdin = FakeTTY("\n".join(answers) + "\n")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = mod.run_setup()
            finally:
                sys.stdin = old_in
            out = buf.getvalue()
            written = json.loads((d / "config.json").read_text(encoding="utf-8"))
            check("Model id (number or name) [main]" in out,
                  "the chooser offers the configured model as the default", out[:700])
            check(rc == 0 and written["llm"]["model"] == "main",
                  "...and Enter keeps it instead of the endpoint's first advertised id",
                  written["llm"]["model"])

            # ---- a CLOUD endpoint: key first, bearer probe, then the provider's list ----
            # The bug this exists for: the wizard probed with NO key, so a hosted provider's
            # 401 read as "not reachable" and the model list never came back. Now the key is
            # asked BEFORE the link and carried on GET /models.
            d = stage(work / "cloud", False)
            spec = importlib.util.spec_from_file_location("setup_cloud", d / "tinycmdr.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            seen = {}

            def fake_probe(url, key=None, **kw):
                seen["key"] = key
                if key == "sk-good":
                    return {"ok": True, "ids": ["deepseek-v4-flash", "deepseek-reasoner"],
                            "status": 200, "error": ""}
                return {"ok": False, "ids": [], "status": 401, "error": "HTTP 401"}

            mod.probe_endpoint = fake_probe
            # no real DNS, no real window probe: the wizard's cloud path calls both
            mod._is_local_url = lambda url: False
            mod._detect_window = lambda url, headers=None: 0
            # kind (cloud), url, key (wrong), key again (right) - the link is asked BEFORE
            # the key, so the key has an endpoint to belong to (asked in this order,
            # 2026-10-04) - then model NUMBER, context window (asked here because a hosted
            # endpoint reports none), MM, TG, page x4, egress
            answers = ["cloud", "https://api.example.com/v1", "sk-bad", "sk-good", "1",
                       "128k", "", "", "", "", "", "", ""]
            old_in = sys.stdin
            sys.stdin = FakeTTY("\n".join(answers) + "\n")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = mod.run_setup()
            finally:
                sys.stdin = old_in
            out = buf.getvalue()
            written = json.loads((d / "config.json").read_text(encoding="utf-8"))
            env = (d / ".env").read_text(encoding="utf-8")
            check("refused that key" in out,
                  "a cloud 401 is named as a KEY refusal, not a dead host", out[:800])
            check(seen.get("key") == "sk-good",
                  "...and the retry carries the corrected key on the bearer probe", seen)
            check(written["llm"]["base_url"] == "https://api.example.com/v1"
                  and written["llm"]["model"] == "deepseek-v4-flash",
                  "the cloud endpoint and its chosen model are written", json.dumps(written.get("llm")))
            check("TINYCMDR_LLM_API_KEY=sk-good" in env,
                  "...and the key goes to .env, never config.json", env[-200:])
            check("sk-good" not in json.dumps(written) and "sk-bad" not in json.dumps(written),
                  "...the key never reaches config.json", json.dumps(written.get("llm")))
            check(written["llm"].get("max_context_tokens") == 128000,
                  "the context window is ASKED at the endpoint and written (128k -> 128000)",
                  written["llm"].get("max_context_tokens"))

            # ---- the restart offer: setup's answers need the service to read them ------
            # A running service reads config.json at START, so a bind or a token typed
            # here is not what it serves until a restart - "setup did not fix it" was
            # exactly this gap (reported 2026-10-10). With an instance running the wizard
            # offers the one step that applies its answers, and runs it on yes.
            def _restart_patch(calls):
                def _patch(m):
                    m.__dict__["_verb_running"] = lambda: True

                    def _fake_restart():
                        calls.append("restart")
                        print("restart: back up (test)")
                        return 0
                    m.__dict__["_verb_restart"] = _fake_restart
                return _patch

            d = stage(work / "restart", False)
            calls = []
            rc, out, written, mod = drive(d, "", patch=_restart_patch(calls), extra=("",))
            check(rc == 0 and calls == ["restart"],
                  "a running instance: the wizard offers the restart and runs it on yes",
                  out[-400:])
            check("back up with these settings" in out,
                  "...and the summary says the service is back up", out[-400:])

            d = stage(work / "restart-no", False)
            calls = []
            rc, out, written, mod = drive(d, "", patch=_restart_patch(calls), extra=("n",))
            check(rc == 0 and not calls and "Run `tinycmdr restart`" in out,
                  "a declined offer keeps the plain restart instruction, unrun", out[-400:])

            d = stage(work / "restart-idle", False)
            rc, out, written, mod = drive(d, "")
            check(rc == 0 and "Run `tinycmdr restart`" not in out
                  and "nothing is running now" in out,
                  "nothing running: the summary says the next start takes the settings",
                  out[-400:])
        finally:
            shutil.rmtree(work, ignore_errors=True)

        print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
        return 1 if FAILS else 0
    return main()


def _suite_test_model_setup():
    """The model-setup conversation: a bearer probe, and `model add` with no URL.

The defect these lock down: the probe that every model door used sent NO Authorization
header, so a hosted provider's 401 to GET /v1/models read as "not reachable", the model
list it needed never came back, and the key was asked last and written to `llm.api_key`
in config.json - a file the agent reads into a prompt. Now the key comes first, rides the
probe as a bearer, and lands in .env; a 401 is named as a KEY refusal.

The offering defect (reported 2026-10-12): a configured model the endpoint no longer
advertises vanished from the picker and from its chooser - the cursor landed on the
add-endpoint row and ENTER walked into the add-endpoint interview. Every configured model
is offered now, the configured one first and marked current.

    python tests/test_model_surface.py
"""
    import contextlib
    import http.server
    import importlib.util
    import io
    import json
    import os
    import shutil
    import socketserver
    import sys
    import tempfile
    import threading
    from pathlib import Path

    SRC = (Path(__file__).resolve().parent.parent
           / os.environ.get("TINYCMDR_SRC", "tinycmdr.py"))
    PASSES, FAILS = [], []


    def check(cond, what, extra=""):
        if cond:
            print(f"ok   {what}")
            PASSES.append(what)
        else:
            print(f"FAIL {what}\n     {extra}")
            FAILS.append(what)


    class FakeTTY(io.StringIO):
        def isatty(self):
            return True


    def serve(key="sk-good", ids=("cloud-a", "cloud-b"), status=200):
        """A provider-shaped /v1/models: bearer required when a key is given."""
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if not self.path.rstrip("/").endswith("/models"):
                    self.send_response(404)
                    self.end_headers()
                    return
                if key and self.headers.get("Authorization") != "Bearer " + key:
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps({"data": [{"id": i} for i in ids]}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv.server_address[1], srv


    def stage(dirpath):
        dirpath.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC, dirpath / "tinycmdr.py")
        (dirpath / "config.json").write_text(json.dumps({
            "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "main"},
        }), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("ms_" + dirpath.name,
                                                      dirpath / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod


    def main():
        work = Path(tempfile.mkdtemp(prefix="fbmodel-"))
        try:
            mod = stage(work / "m")
            port, srv = serve()
            base = "http://127.0.0.1:%d/v1" % port

            # ---- probe_endpoint: the bearer, and what a 401 MEANS ------------------------
            r = mod.probe_endpoint(base)
            check(not r["ok"] and r["status"] == 401,
                  "a hosted endpoint without the key answers 401, not a dead host", r)
            r = mod.probe_endpoint(base, "sk-bad")
            check(not r["ok"] and r["status"] == 401,
                  "...and a wrong key is still a 401", r)
            r = mod.probe_endpoint(base, "sk-good")
            check(r["ok"] and r["ids"] == ["cloud-a", "cloud-b"],
                  "...and the right key carries the provider's model list", r)
            check(mod._probe_model_ids(base) is None
                  and mod._probe_model_ids(base, "sk-good") == ["cloud-a", "cloud-b"],
                  "_probe_model_ids keeps its None-means-no-answer contract", None)
            check("refused that key" in mod.probe_sentence(base, {"ok": False, "status": 401,
                                                                  "error": "HTTP 401", "ids": []}),
                  "a 401 is described as a key refusal", None)
            dead = mod.probe_endpoint("http://127.0.0.1:9/v1", "sk-good")
            check(not dead["ok"] and dead["status"] is None and dead["error"],
                  "a dead host has no status and a reason", dead)

            # ---- the interactive `model add`: cloud, link first, then the key, then the list -
            mod2 = stage(work / "add")
            mod2._is_local_url = lambda url: False
            # kind, url, key, number, alias, then the cloud-failover consent (the link is
            # asked BEFORE the key: a key belongs to an endpoint - asked in this order,
            # 2026-10-04)
            answers = ["cloud", base, "sk-good", "2", "team-a", "y"]
            old_in = sys.stdin
            sys.stdin = FakeTTY("\n".join(answers) + "\n")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = mod2._verb_model_add_interactive({})
            finally:
                sys.stdin = old_in
            out = buf.getvalue()
            cfg = json.loads((work / "add" / "config.json").read_text(encoding="utf-8"))
            env = (work / "add" / ".env").read_text(encoding="utf-8")
            fbs = cfg["llm"].get("fallbacks") or []
            check(rc == 0 and len(fbs) == 1 and fbs[0]["base_url"] == base,
                  "`model add` with no URL asks and writes a fallback", (rc, fbs))
            check(fbs[0].get("model") == "cloud-b" and fbs[0].get("alias") == "team-a",
                  "...the NUMBER resolves to the advertised id, and the alias is kept", fbs)
            check(fbs[0].get("api_key_env") == "TINYCMDR_ENDPOINT1_API_KEY"
                  and "TINYCMDR_ENDPOINT1_API_KEY=sk-good" in env,
                  "...and the key goes to .env under the entry's api_key_env", env[-200:])
            check("sk-good" not in json.dumps(cfg), "the key is NEVER in config.json",
                  json.dumps(cfg.get("llm")))
            check(cfg["llm"].get("allow_cloud_fallback") is True,
                  "adding an off-LAN endpoint asks about automatic failover, and y turns it on",
                  cfg.get("llm"))
            check("reachable" in out and "cloud-a" in out,
                  "it says the endpoint is reachable and lists what it advertised", out[-500:])

            # ---- the interactive `model add --primary`: key to TINYCMDR_LLM_API_KEY ------
            mod3 = stage(work / "primary")
            mod3._is_local_url = lambda url: False
            mod3._detect_window = lambda url, headers=None: 0
            answers = ["cloud", base, "sk-good", "1"]  # kind, url, key, model number
            old_in = sys.stdin
            sys.stdin = FakeTTY("\n".join(answers) + "\n")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = mod3._verb_model_add_interactive({"primary": True})
            finally:
                sys.stdin = old_in
            cfg = json.loads((work / "primary" / "config.json").read_text(encoding="utf-8"))
            env = (work / "primary" / ".env").read_text(encoding="utf-8")
            check(rc == 0 and cfg["llm"]["base_url"] == base
                  and cfg["llm"]["model"] == "cloud-a",
                  "--primary sets llm.base_url and llm.model", (rc, cfg.get("llm")))
            check("TINYCMDR_LLM_API_KEY=sk-good" in env
                  and "api_key" not in cfg["llm"],
                  "...and its key is .env's TINYCMDR_LLM_API_KEY, not config.json's api_key",
                  (env[-160:], cfg["llm"]))

            # ---- every configured model is OFFERED, on every door ----------------------
            # Reported 2026-10-12: the box advertised another id than config's model, so
            # the picker lost the configured row AND its current mark - the cursor sat on
            # the ADD row and ENTER walked into the add-endpoint interview - and the
            # endpoint's own chooser silently made its first advertised id the default.
            mod4 = stage(work / "picker")
            mod4.CONFIG["llm"]["base_url"] = base
            mod4.CONFIG["llm"]["model"] = "local-gguf-27b"
            mod4.CONFIG["llm"]["api_key"] = "sk-good"
            port2, srv2 = serve(ids=("fb-1", "fb-2"))
            mod4.CONFIG["llm"]["fallbacks"] = [
                {"base_url": "http://127.0.0.1:%d/v1" % port2, "model": "team-a",
                 "alias": "big", "api_key": "sk-good"}]
            cat = mod4.model_catalog(force=True)
            names = [e["name"] for e in cat]
            check(names == ["local-gguf-27b", "cloud-a", "cloud-b", "team-a", "big",
                            "fb-1", "fb-2"],
                  "the catalog offers the configured model first, then every endpoint's",
                  names)
            rows = mod4.model_pick_rows(cat, "local-gguf-27b")
            rows.insert(0, (mod4.MODEL_PICK_ADD, "", False))
            pick = mod4.ModelPick(rows, current="local-gguf-27b", title="Select model")
            check(pick.picked() == "local-gguf-27b",
                  "the picker opens ON the configured model, not on the ADD row",
                  pick.picked())
            # ...and a configured ALIAS is not shadowed by the configured-model row: its
            # entry must stay the fallback's (url and send_as), or a global switch to
            # the alias would route the call to the primary
            mod8 = stage(work / "aliaskeep")
            mod8.CONFIG["llm"]["base_url"] = base
            mod8.CONFIG["llm"]["model"] = "cm"
            mod8.CONFIG["llm"]["api_key"] = "sk-good"
            mod8.CONFIG["llm"]["fallbacks"] = [
                {"base_url": base, "model": "team-a", "alias": "cm",
                 "api_key": "sk-good"}]
            hit = mod8.model_entry("cm")
            check(bool(hit) and not hit["local"] and hit["send_as"] == "team-a",
                  "a configured alias still routes to its fallback, not the primary",
                  hit)
            opened = {}
            mod4.pick_terminal_ready = lambda: True

            def _capture(state):
                opened["state"] = state
                return None
            mod4.run_model_pick = _capture
            kept = mod4._choose_one_model(base, ["cloud-a", "cloud-b"], "local-gguf-27b")
            state = opened.get("state")
            check(bool(state) and state.rows[0][0] == "local-gguf-27b"
                  and state.rows[0][2],
                  "the endpoint's own chooser offers the configured model first, marked",
                  state and state.rows[:3])
            check(kept == "local-gguf-27b",
                  "...and ESC keeps it - not the endpoint's first advertised id", kept)
            rc = mod4._verb_model(["use", "local-gguf-27b"])
            cfg4 = json.loads((work / "picker" / "config.json").read_text(encoding="utf-8"))
            check(rc == 0 and cfg4["llm"]["model"] == "local-gguf-27b",
                  "`model use` reaches the configured model too",
                  (rc, cfg4["llm"]["model"]))
            srv2.shutdown()
            srv2.server_close()

            # the dead-endpoint question is only for a list with nothing else ON it
            mod5 = stage(work / "deadend")
            mod5.CONFIG["llm"]["base_url"] = "http://127.0.0.1:9/v1"
            mod5.CONFIG["llm"]["model"] = "main"
            mod5.CONFIG["llm"]["fallbacks"] = [
                {"base_url": base, "model": "team-a", "api_key": "sk-good"}]
            mod5.pick_terminal_ready = lambda: True
            mod5.run_model_pick = _capture
            opened.clear()
            old_in = sys.stdin
            sys.stdin = FakeTTY("y\n")        # would answer the offer if it asked
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    mod5._verb_model([])
            finally:
                sys.stdin = old_in
            out = buf.getvalue()
            check("not usable" in out and "Set up a local or cloud model now?" not in out
                  and bool(opened.get("state")),
                  "a dead primary with other endpoints opens the picker, no interview",
                  out[-300:])
            check(bool(opened.get("state"))
                  and any(r[0] == "main" for r in opened["state"].rows),
                  "...with the configured model on the list", opened.get("state")
                  and opened["state"].rows[:3])

            mod5.CONFIG["llm"]["fallbacks"] = []
            opened.clear()
            old_in = sys.stdin
            sys.stdin = FakeTTY("y\n")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    mod5._verb_model([])
            finally:
                sys.stdin = old_in
            check("Set up a local or cloud model now?" in buf.getvalue(),
                  "...while a one-row list still asks the question",
                  buf.getvalue()[-200:])

            # the chat door every messaging lane runs (`/model <name>` in Mattermost,
            # Telegram and the page all land in model_command) offers the configured
            # model too (reported 2026-10-12)
            mod7 = stage(work / "chatdoor")
            # The key form's door: a staged module has no web lane, so the wizard's
            # preflight would read "down". Pin the LAN shape for the flow tests; the
            # off/down hints get their own overrides in the key-form block below.
            _door_lan = lambda: ("lan", "http://127.0.0.1:9999")
            mod7._secret_door_state = _door_lan
            mod7.CONFIG["llm"]["base_url"] = base
            mod7.CONFIG["llm"]["model"] = "local-gguf-27b"
            mod7.CONFIG["llm"]["api_key"] = "sk-good"
            listing = mod7.model_command("mattermost:suite", "list")
            check("local-gguf-27b" in listing,
                  "the chat /model list offers the configured model", listing[:300])
            reply = mod7.model_command("mattermost:suite", "local-gguf-27b")
            check("Nothing switched" not in reply and "local-gguf-27b" in reply,
                  "...and switching to it is not refused", reply[:300])
            # the numbered menu (UX review, 2026-10-12): a number to pick by, the
            # model in use marked, and plain words where a model lives - not a URL
            # the person this list is for cannot act on
            _rows = [ln for ln in listing.splitlines() if "local-gguf-27b" in ln]
            check(_rows and "in use now" in _rows[0]
                  and _rows[0].strip().split(".")[0].isdigit(),
                  "...as a numbered menu marking the model in use", listing[:300])
            check("this box" in listing and "http://" not in listing,
                  "...with plain words for where a model lives, not a URL",
                  listing[:300])
            _first = mod7.model_catalog()[0]["name"]
            reply = mod7.model_command("mattermost:suite", "1")
            check("Nothing switched" not in reply and _first in reply,
                  "a number switches to that menu row", reply[:200])
            reply = mod7.model_command("mattermost:suite", "99")
            check("Nothing switched" in reply and "1–" in reply,
                  "a number outside the menu is refused with the range", reply[:200])

            # the doors a knowing user hits stay terse; the directions wait behind
            # `/model help` (his rule, 2026-10-16: no wall on the way to a switch or an
            # add). Bare `/model` used to be the menu, `/model add` (no link) the shell's
            # flag wall and, before the routing, "Nothing switched".
            _terse = mod7.model_command("mattermost:suite", "")
            check("Model for this conversation" in _terse and "/model help" in _terse
                  and "add or change the endpoint" not in _terse
                  and "\n  1. " not in _terse,
                  "bare `/model` is the status line and one pointer, nothing else",
                  _terse[:300])
            _help = mod7.model_command("mattermost:suite", "help")
            check("add or change the endpoint" in _help and "in use now" in _help
                  and "/model add <link>" in _help and "--key-env MY_KEY" in _help
                  and "token set" in _help,
                  "`/model help` lays out the options and the words", _help[:400])
            _add_row = str(len(mod7.model_catalog()) + 1)
            reply = mod7.model_command("mattermost:suite", _add_row)
            check("add or change the endpoint" in reply and "/model add <link>" in reply,
                  "the add row's number serves the same help", reply[:300])
            reply = mod7.model_command("mattermost:suite", "add")
            check("`local` or `cloud`" in reply and "cancel" in reply
                  and "Nothing switched" not in reply,
                  "a bare `model add` in chat asks the first question in the chat",
                  reply[:300])
            reply = mod7.model_command("mattermost:suite", "add cloud")
            check("Send the provider's link" in reply and "page" in reply,
                  "...and a kind word already typed starts at the link question",
                  reply[:300])

            _real_in = sys.stdin
            sys.stdin = FakeTTY("")        # isatty yes, then EOF: the NUL-device shape
            try:
                reply = mod7.model_command("mattermost:suite", "setup")
                check("needs a terminal to ask on" in reply and "Nothing switched" not in reply,
                      "`model setup` from chat refuses by name, never as an unknown model",
                      reply[:300])
                reply = mod7.verb_from_chat("model add")
                check("model add [<url>]" in reply and "EOFError" not in reply,
                      "...and the verb's own bare `add` answers usage on the NUL shape",
                      reply[:300])
            finally:
                sys.stdin = _real_in
            reply = mod7.model_command("mattermost:suite", "endpoint")
            check("primary endpoint:" in reply,
                  "`model endpoint` reads the endpoint from chat", reply[:300])
            # `use` runs before `add` on purpose: the add verb reloads CONFIG from the
            # staged config.json, which would drop this suite's in-memory endpoint patch
            reply = mod7.model_command("mattermost:suite", "use cloud-a")
            _cfg7 = json.loads((work / "chatdoor" / "config.json").read_text(encoding="utf-8"))
            check("default model:" in reply and _cfg7["llm"].get("model") == "cloud-a",
                  "`model use <name>` switches the default from chat too",
                  (reply[:300], _cfg7["llm"].get("model")))
            reply = mod7.model_command("mattermost:suite",
                                       "add http://127.0.0.1:9/v2 --force --model m")
            _cfg7 = json.loads((work / "chatdoor" / "config.json").read_text(encoding="utf-8"))
            check("fallback: http://127.0.0.1:9/v2 -> m" in reply
                  and any(f.get("base_url") == "http://127.0.0.1:9/v2"
                          for f in _cfg7["llm"].get("fallbacks") or []),
                  "`model add <url>` writes a fallback from chat",
                  (reply[:400], _cfg7["llm"].get("fallbacks")))

            # ...and a number picks the model to add, from what the endpoint advertises
            # (stdin is pinned: with a tty the shell picker would open, which a chat
            # command never does)
            (work / "chatdoor" / ".env").write_text(
                "TINYCMDR_ENDPOINT_TEST_KEY=sk-good\n", encoding="utf-8")
            _real_in = sys.stdin
            sys.stdin = io.StringIO("")
            try:
                reply = mod7.model_command(
                    "mattermost:suite",
                    "add %s --key-env TINYCMDR_ENDPOINT_TEST_KEY" % base)
                check("1) cloud-a" in reply and "reply `/model add" in reply,
                      "adding an endpoint that serves several models asks by number",
                      reply[:400])
                reply = mod7.model_command(
                    "mattermost:suite",
                    "add %s --key-env TINYCMDR_ENDPOINT_TEST_KEY --model 2" % base)
            finally:
                sys.stdin = _real_in
            _cfg7 = json.loads((work / "chatdoor" / "config.json").read_text(encoding="utf-8"))
            _stub_entry = [f for f in _cfg7["llm"].get("fallbacks") or []
                           if f.get("base_url") == base]
            check(_stub_entry and _stub_entry[0].get("model") == "cloud-b"
                  and "reach it with `/model cloud-b`" in reply,
                  "...and `--model 2` resolves to the second advertised id from chat",
                  (reply[:400], _stub_entry))

            # a keyless hosted probe says what to do, and `cloud <link>` is accepted -
            # the words the operator typed at the box (`add cloud https://api.deepseek.com`)
            port2, srv2 = serve()
            base2 = "http://127.0.0.1:%d/v1" % port2
            _real_in = sys.stdin
            sys.stdin = io.StringIO("")
            try:
                reply = mod7.model_command("mattermost:suite", "add %s" % base2)
                check("HTTP 401" in reply and "--key-env" in reply and "token set" in reply,
                      "a keyless hosted endpoint names the key step", reply[:400])
                reply = mod7.model_command(
                    "mattermost:suite",
                    "add cloud %s --key-env TINYCMDR_ENDPOINT_TEST_KEY --model 1" % base2)
            finally:
                sys.stdin = _real_in
            _cfg7 = json.loads((work / "chatdoor" / "config.json").read_text(encoding="utf-8"))
            check("-> cloud-a" in reply
                  and any(f.get("base_url") == base2 and f.get("model") == "cloud-a"
                          for f in _cfg7["llm"].get("fallbacks") or []),
                  "`add cloud <link>` drops the kind word and adds the endpoint",
                  (reply[:300], _cfg7["llm"].get("fallbacks")))
            srv2.shutdown()
            srv2.server_close()

            # ---- the in-chat model wizard: the questions asked in the conversation ------
            _wkey = "web:wizard"
            port3, srv3 = serve(key="")        # keyless: the wizard carries no key
            base3 = "http://127.0.0.1:%d/v1" % port3
            try:
                reply = mod7.model_command(_wkey, "add")
                check("local" in reply and "cloud" in reply and "cancel" in reply,
                      "the chat add opens with the shell wizard's first question",
                      reply[:300])
                reply = mod7._web_command("local", _wkey)
                check(reply[0] == "reply" and "Send the link" in reply[1]
                      and "<box>" in reply[1],
                      "the next plain message IS the answer: it asks the link",
                      str(reply)[:300])
                reply = mod7._web_command(base3, _wkey)
                check(reply[0] == "reply" and "1) cloud-a" in reply[1]
                      and "Reply the number" in reply[1],
                      "the link probes the endpoint and asks which advertised model",
                      str(reply)[:400])
                reply = mod7._web_command("2", _wkey)
                _cfg7 = json.loads((work / "chatdoor" / "config.json")
                                   .read_text(encoding="utf-8"))
                check(reply[0] == "reply" and "-> cloud-b" in reply[1]
                      and any(f.get("base_url") == base3 and f.get("model") == "cloud-b"
                              for f in _cfg7["llm"].get("fallbacks") or []),
                      "the number adds the endpoint from the chat, model and all",
                      (str(reply)[:300], _cfg7["llm"].get("fallbacks")))
                reply = mod7._web_command("2", _wkey)
                check(reply[0] == "task",
                      "...and the wizard is spent: the next message is not its answer",
                      str(reply)[:160])
            finally:
                srv3.shutdown()
                srv3.server_close()
            reply = mod7.model_command(_wkey, "add")
            reply = mod7._web_command("cloud", _wkey)
            check(reply[0] == "reply" and "https://api.deepseek.com/v1" in reply[1]
                  and "page" in reply[1],
                  "`cloud` starts at the provider-shaped link question", str(reply)[:300])
            reply = mod7._web_command("cancel", _wkey)
            check(reply[0] == "reply" and "Cancelled" in reply[1]
                  and mod7._web_command("local", _wkey)[0] == "task",
                  "`cancel` ends the wizard and the next message is a task again",
                  str(reply)[:200])
            mod7.model_command(_wkey, "add")
            _live = mod7._web_command("local", _wkey)
            _rows = getattr(mod7, "_MODEL_WIZARD", None)
            if _rows and _wkey in _rows:
                _rows[_wkey]["touched"] -= float(
                    getattr(mod7, "_MODEL_WIZARD_TTL", 600.0)) + 1.0
            _aged = mod7._web_command("local", _wkey)
            check(_live[0] == "reply" and _aged[0] == "task",
                  "an answered row is bounded: aged past its TTL it stops claiming",
                  (_live[0], _aged[0]))
            mod7.model_command(_wkey, "add")
            _one = mod7._web_command("maybe", _wkey)
            _two = mod7._web_command("maybe", _wkey)
            check(_one[0] == "reply" and "try again" in _one[1]
                  and _two[0] == "reply" and "Ending the endpoint add" in _two[1]
                  and mod7._web_command("cloud", _wkey)[0] == "task",
                  "a step's wrong answer is asked again once, then the add ends loudly",
                  (str(_one)[:140], str(_two)[:200]))
            mod7.model_command(_wkey, "add local")
            reply = mod7._web_command("http://127.0.0.1:9/v9", _wkey)
            check(reply[0] == "reply" and "did not answer" in reply[1]
                  and mod7._web_command("http://127.0.0.1:9/v9", _wkey)[0] == "task",
                  "a link nothing answers keeps the verb's own words and ends the add",
                  str(reply)[:300])
            mod7.model_command(_wkey, "add")
            mod7.model_command(_wkey, "list")
            check(mod7._web_command("cloud", _wkey)[0] == "task",
                  "any other `/model` word abandons the wizard instead of leaving it",
                  str(mod7._web_command("cloud", _wkey))[:160])

            # ---- the key form: a provider secret never crosses the chat ----------------
            try:
                # 1) the preflight names the two states that make the form impossible
                mod7._secret_door_state = lambda: ("off", "")
                mod7.model_command(_wkey, "add")
                _off = mod7._web_command("cloud", _wkey)
                check(_off[0] == "reply" and "web.enabled" in _off[1]
                      and "token set" in _off[1]
                      and mod7._web_command("local", _wkey)[0] == "task",
                      "page off: the cloud ask names the switch and ends the wizard",
                      str(_off)[:400])
                mod7._secret_door_state = lambda: ("down", "port 8790 held")
                mod7.model_command(_wkey, "add")
                _down = mod7._web_command("cloud", _wkey)
                check(_down[0] == "reply" and "not listening" in _down[1]
                      and "doctor" in _down[1] and "token set" in _down[1]
                      and mod7._web_command("local", _wkey)[0] == "task",
                      "page down: the cloud ask names the fault and the host command",
                      str(_down)[:400])
                # 2) page up: a 401 endpoint gets a one-time link, never a key question
                mod7._secret_door_state = _door_lan
                port_k, srv_k = serve()
                base_k = "http://127.0.0.1:%d/v1" % port_k
                try:
                    mod7.model_command(_wkey, "add")
                    _kind_k = mod7._web_command("cloud", _wkey)
                    _link = mod7._web_command(base_k, _wkey)
                    _sid = (str(_link[1]).split("/secret/", 1)[1].split(" ", 1)[0]
                            if "/secret/" in str(_link[1]) else "")
                    _env_name = (mod7._secret_door_take(_sid) or {}).get("env") or ""
                    check(_kind_k[0] == "reply" and "page" in _kind_k[1]
                          and _link[0] == "reply" and "needs a key" in _link[1]
                          and bool(_sid) and "never enters the chat" in _link[1],
                          "401: the wizard posts a one-time link, not a key question",
                          (str(_kind_k)[:200], str(_link)[:400]))
                    _typed = mod7._model_wizard_reply(_wkey, "sk-typed-into-the-chat")
                    check("goes on the page" in str(_typed)
                          and "sk-typed-into-the-chat" not in str(_typed),
                          "a key typed into the chat is refused, never taken",
                          str(_typed)[:200])
                    _code, _bad = mod7._secret_form_submit(_sid, {"key": "sk-bad"})
                    _env_now = (work / "chatdoor" / ".env").read_text(encoding="utf-8")
                    check(_code == 400 and "refused" in _bad.lower()
                          and "sk-bad" not in _env_now,
                          "a refused key is never written to .env",
                          (_code, _bad[:200]))
                    _code, _pick_html = mod7._secret_form_submit(_sid, {"key": "sk-good"})
                    _env_now = (work / "chatdoor" / ".env").read_text(encoding="utf-8")
                    _got = [ln for ln in _env_now.splitlines()
                            if _env_name and ln.startswith(_env_name + "=")]
                    check(_code == 200 and "Choose the model" in _pick_html
                          and "sk-good" not in _pick_html and bool(_got),
                          "an accepted key lands in .env; the page asks the model",
                          (_code, _pick_html[:300], _got))
                    if os.name != "nt":
                        _mode = oct((work / "chatdoor" / ".env").stat().st_mode & 0o777)
                        check(_mode == "0o600",
                              "the .env holding the key is mode 600", _mode)
                    _code, _done = mod7._secret_form_submit(_sid, {"model": "cloud-a"})
                    _cfg7 = json.loads((work / "chatdoor" / "config.json")
                                       .read_text(encoding="utf-8"))
                    _entry = [f for f in _cfg7["llm"].get("fallbacks") or []
                              if f.get("base_url") == base_k]
                    check(_code == 200 and "Added" in _done and bool(_entry)
                          and _entry[0].get("model") == "cloud-a"
                          and _env_name
                          and _entry[0].get("api_key_env") == _env_name,
                          "the model pick finishes the add, key env wired",
                          (_code, _done[:300], _entry))
                    check(mod7._secret_door_take(_sid) is None,
                          "the link burns after the add", _sid)
                    check(mod7._model_wizard_reply(_wkey, "hello") is None,
                          "...and the chat wizard row ends with it", None)
                finally:
                    srv_k.shutdown()
                    srv_k.server_close()
                # 3) an aged link is dead before it can write
                _sid2 = mod7._secret_door_mint("X_API_KEY", base_k, "w:ttl")
                with mod7._SECRET_DOOR_LOCK:
                    mod7._SECRET_DOOR[_sid2]["at"] -= mod7._SECRET_DOOR_TTL + 1
                check(mod7._secret_door_take(_sid2) is None,
                      "an aged link is dropped before it can write anything", _sid2)
            finally:
                mod7._secret_door_state = _door_lan

            # ---- helpers ----------------------------------------------------------------
            check(mod._next_endpoint_env([{"api_key_env": "TINYCMDR_ENDPOINT1_API_KEY"}])
                  == "TINYCMDR_ENDPOINT2_API_KEY",
                  "_next_endpoint_env skips a name already used", None)
            (work / "primary" / ".env").write_text("TINYCMDR_LLM_API_KEY=sk-file\n",
                                                   encoding="utf-8")
            os.environ.pop("TINYCMDR_LLM_API_KEY", None)
            check(mod3._endpoint_key("TINYCMDR_LLM_API_KEY") == "sk-file",
                  "_endpoint_key reads .env when the process did not inherit the value", None)

            srv.shutdown()
            srv.server_close()
        finally:
            shutil.rmtree(work, ignore_errors=True)

        print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
        return 1 if FAILS else 0
    return main()


def _suite_test_profiles():
    """Caps follow the model, not one global guess.

A local endpoint and a 200k cloud model were paying identical tool-output, fetch and
note caps, so a capable model was fed clipping it did not need - and the campaign kept
re-deriving facts its own notes had already been clipped out of.

Run:  python tests/test_model_surface.py
"""
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-profiles"
    FAILS = []


    def check(cond, what):
        print(("ok   " if cond else "FAIL ") + what)
        if not cond:
            FAILS.append(what)


    def load(model, profiles=None):
        """Stage the harness with a config naming this model, and load it."""
        if STAGE.exists():
            shutil.rmtree(STAGE, ignore_errors=True)
        STAGE.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC, STAGE / "tinycmdr.py")
        cfg = {"llm": {"model": model, "base_url": "http://127.0.0.1:9999/v1"}}
        if profiles:
            cfg["llm"]["profiles"] = profiles
        (STAGE / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        name = "tinycmdr_prof_%d" % (abs(hash(model)) % 100000)
        spec = importlib.util.spec_from_file_location(name, STAGE / "tinycmdr.py")
        fb = importlib.util.module_from_spec(spec)
        sys.modules[name] = fb
        spec.loader.exec_module(fb)
        return fb


    def main():
        # 1. no profiles: today's values, untouched, and nothing claims a profile
        fb = load("local-gguf-model")
        check(fb.PROFILE is None, "no profiles in config means no profile is applied")
        check(fb.CONFIG["agent"].get("tool_output_max_chars") == 10000,
              "the shipped tool-output cap stays 10000 by default")
        check("active_profile" not in fb.CONFIG["agent"],
              "and no profile is recorded, so nothing can be running silently")

        # 2. a matching profile wins, and only the keys it names move
        fb = load("deepseek-v4-flash", {"deepseek": {"tool_output_max_chars": 40000,
                                                     "memory_concept_max_chars": 8000}})
        check(fb.PROFILE and fb.PROFILE["profile"] == "deepseek",
              "the first profile key found in the model name wins")
        check(fb.CONFIG["agent"]["tool_output_max_chars"] == 40000,
              "its tool-output cap applies")
        check(fb.CONFIG["agent"]["memory_concept_max_chars"] == 8000,
              "its memory-concept cap applies")
        check(fb.CONFIG["agent"].get("fetch_max_chars") == 12000,
              "keys the profile does not name keep the value already in config")
        check(fb.CONFIG["agent"].get("active_profile") == "deepseek",
              "the winning profile is recorded, so it cannot hide")

        # 3. a profile that does not match changes nothing (no accidental catch-all)
        fb = load("local-gguf-model", {"deepseek": {"tool_output_max_chars": 40000}})
        check(fb.PROFILE is None, "a non-matching profile is not applied")
        check(fb.CONFIG["agent"]["tool_output_max_chars"] == 10000,
              "and the cap stays where it was")

        # 4. a profile cannot invent config keys
        fb = load("cloud-model", {"cloud": {"tool_output_max_chars": 50000,
                                            "not_a_real_key": 1}})
        check("not_a_real_key" not in fb.CONFIG["agent"],
              "a profile cannot write keys the harness does not read")

        # 5. a key matches a WHOLE WORD, and the LONGEST match wins
        # 2026-09-29: a bare substring meant `pro` matched `prometheus-14b` and `mini`
        # matched `MiniMax-M2`, and when two keys matched, dict order decided the winner instead
        # of the more specific key.
        fb = load("prometheus-14b", {"pro": {"tool_output_max_chars": 40000}})
        check(fb.PROFILE is None, "a key that merely SITS INSIDE a word does not match")
        fb = load("MiniMax-M2", {"mini": {"tool_output_max_chars": 40000}})
        check(fb.PROFILE is None, "  nor does one that begins a longer word")
        fb = load("llama3-8b", {"llama": {"tool_output_max_chars": 40000}})
        check(bool(fb.PROFILE) and fb.PROFILE["profile"] == "llama",
              "  while a digit after the key is still the same word")
        fb = load("deepseek-r1-distill-llama-8b",
                  {"deepseek": {"tool_output_max_chars": 20000},
                   "deepseek-r1": {"tool_output_max_chars": 40000}})
        check(bool(fb.PROFILE) and fb.PROFILE["profile"] == "deepseek-r1",
              "the MOST SPECIFIC key wins, not the one written first")
        check(fb.CONFIG["agent"]["tool_output_max_chars"] == 40000,
              "  and it is that key's caps that apply")

        print()
        if FAILS:
            print("%d failed: %s" % (len(FAILS), FAILS))
            sys.exit(1)
        print("all model-profile checks passed")


    main()


def main():
    rc = 0
    for name, fn in (("test_setup", _suite_test_setup), ("test_model_setup", _suite_test_model_setup), ("test_profiles", _suite_test_profiles)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
