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
answer left empty so only the search section is doing anything.

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
        """A provider-shaped /v1/models: bearer required, 401 otherwise."""
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if not self.path.rstrip("/").endswith("/models"):
                    self.send_response(404)
                    self.end_headers()
                    return
                if self.headers.get("Authorization") != "Bearer " + key:
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
            check("adding an off-LAN endpoint asks about automatic failover, and y turns it on",
                  cfg["llm"].get("allow_cloud_fallback") is True, cfg.get("llm"))
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
