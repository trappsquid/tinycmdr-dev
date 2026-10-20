"""test_model_profiles - the curated catalog and the picker that applies it.

A user cannot be expected to know which flags a model needs. The catalog is data
(assets/model-profiles.json): each row names a model family at ADD time and seeds the
facts the request path already reads - the reasoning echo, fields the endpoint rejects,
the documented window. The endpoint still wins: a learned fact replaces the seed, the
disagreement is said once as a model note, and the request path never reads the catalog
or a model name (test_name_hygiene.py grades that boundary).

    python tests/test_model_profiles.py     [TINYCMDR_SRC=/path/to/other/tinycmdr.py]
"""
import os
import sys

# The ok/FAIL lines quote the page and the catalog; a legacy console (cp1252) must not
# crash on their punctuation. Same hardening as the other suites.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                       # noqa: BLE001
        pass


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


def _suite_catalog():
    """The catalog is data the docs vouch for, and every row says something usable.

    python tests/test_model_profiles.py
    """
    import json
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    FAILS = []

    def check(cond, what, extra=""):
        if cond:
            print("ok   %s" % what)
        else:
            FAILS.append(what)
            print("FAIL %s\n     %s" % (what, extra))

    asset = BASE / "assets" / "model-profiles.json"
    check(asset.exists(), "the catalog ships (assets/model-profiles.json)")
    data = json.loads(asset.read_text(encoding="utf-8-sig"))
    rows = (data or {}).get("profiles") or []
    check(bool(rows), "the catalog holds rows (%d)" % len(rows))
    docs = (BASE / "docs" / "model-contracts.md").read_text(encoding="utf-8-sig")
    ids, tokens = [], []
    wires = ("chat", "responses", "messages")
    levels = ("on", "off", "none", "auto")
    for row in rows:
        rid = str(row.get("id") or "")
        ids.append(rid)
        check(bool(rid) and bool(row.get("label")),
              "row %r has an id and a label" % (rid or row))
        check(isinstance(row.get("match"), list)
              and all(isinstance(t, str) and t for t in row["match"]),
              "row %r has a match list of strings" % rid)
        tokens.extend(str(t).lower() for t in row["match"])
        check(str(row.get("doc") or "") in docs,
              "row %r names a row that exists in docs/model-contracts.md (%r)"
              % (rid, row.get("doc")))
        check(str(row.get("wire") or "") in wires,
              "row %r declares a wire this build knows (%r)" % (rid, row.get("wire")))
        check(str(row.get("reasoning") or "") in levels,
              "row %r declares a reasoning level (%r)" % (rid, row.get("reasoning")))
        check(not row.get("complete_message")
              or str(row.get("reasoning") or "").lower() == "on",
              "row %r asks for the complete message only where the echo is on" % rid)
        try:
            ctx = int(row.get("context") or 0)
        except (TypeError, ValueError):
            ctx = -1
        check(ctx >= 0, "row %r carries a context number (%r)" % (rid, row.get("context")))
        check(row.get("vision") in (True, False, None),
              "row %r says vision yes, no, or unknown (%r)" % (rid, row.get("vision")))
        check(str(row.get("sampling") or "") == "omit",
              "row %r keeps sampling omitted (this build sends none)" % rid)
        check(isinstance(row.get("drop") or [], list),
              "row %r lists dropped fields as a list" % rid)
        check(str(row.get("when") or "") in ("", "local"),
              "row %r names a selection rule this build knows (%r)"
              % (rid, row.get("when")))
    check(len(ids) == len(set(ids)), "ids are unique (%s)" % ids)
    check(len(tokens) == len(set(tokens)),
          "no two rows claim the same model-id token (%s)" % tokens)
    check(sum(1 for r in rows if r.get("when") == "local") == 1,
          "exactly one row is chosen by a local endpoint")
    check(any(r.get("id") == "anonymous" for r in rows),
          "the catalog carries the 'not listed' row the picker always offers")
    return 1 if FAILS else 0


def _suite_seed_and_override():
    """Selecting a row seeds facts the request path reads; learning still wins.

    python tests/test_model_profiles.py
    """
    import importlib.util
    import json
    import shutil
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    FAILS = []

    def check(cond, what, extra=""):
        if cond:
            print("ok   %s" % what)
        else:
            FAILS.append(what)
            print("FAIL %s\n     %s" % (what, extra))

    work = Path(tempfile.mkdtemp(prefix="fbprofiles-"))
    try:
        shutil.copy2(SRC, work / "tinycmdr.py")
        (work / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE / "assets" / "model-profiles.json",
                     work / "assets" / "model-profiles.json")
        (work / "config.json").write_text(json.dumps({
            "llm": {"base_url": "https://api.example.test/v1", "model": "whatever"}}),
            encoding="utf-8")
        spec = importlib.util.spec_from_file_location("profiles_build",
                                                      work / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["profiles_build"] = mod
        spec.loader.exec_module(mod)

        url = "https://api.example.test/v1"
        check(mod._profile_for_model("kimi-k3-0905").get("id") == "kimi-k3",
              "a K3 id matches its row")
        check(mod._profile_for_model("some-unknown-model") is None,
              "an unknown cloud id matches nothing")
        check(mod._profile_for_model("whatever", "http://127.0.0.1:8081/v1").get("id")
              == "local", "a local endpoint lands on the local row")
        check(mod._profile_for_model("kimi-k3", "http://127.0.0.1:8081/v1").get("id")
              == "kimi-k3", "a model match beats the local rule")

        row = mod.model_profile_row("kimi-k3")
        applied, kept = mod._apply_profile(row, url)
        facts = mod._endpoint_facts(url)
        check(applied.get("reasoning") == "on" and applied.get("window") == 1000000,
              "the row seeds the echo and the documented window (%r)" % applied)
        check(facts.get("reasoning") == "on" and facts.get("window") == 1000000,
              "the facts store carries them (%r)" % facts)
        check((facts.get("profile") or {}).get("id") == "kimi-k3",
              "the row is recorded so doctor can name it")
        check(mod._preset_window(url) == (1000000, "profile"),
              "the envelope reads the seeded window when the endpoint reports none")
        check(mod._replay_completed_ok(url) is True,
              "a seeded echo lets a completed turn replay")
        msgs = [{"role": "user", "content": "a"},
                {"role": "assistant", "content": "b", "reasoning_content": "thought"},
                {"role": "user", "content": "c"}]
        check(any(m.get("reasoning_content")
                  for m in mod._messages_for_wire(msgs, url)),
              "the completed turn really carries the field on the wire")

        # The endpoint teaches a different answer: learning wins.
        mod._endpoint_note(url, reasoning="off")
        facts = mod._endpoint_facts(url)
        check(facts.get("reasoning") == "off",
              "a learned fact overwrites the seed (%r)" % facts.get("reasoning"))
        note = (facts.get("notices") or {}).get("profile:reasoning") or ""
        check("expected reasoning=on" in note and "answered off" in note,
              "the disagreement is recorded as a model note (%r)" % note)
        said = []
        mod._surface_model_notices(url, said.append)
        mod._surface_model_notices(url, said.append)
        check(len(said) == 1 and said[0].startswith("Model note: ")
              and "expected reasoning=on" in said[0],
              "and it is posted once on the surface (%r)" % said)
        applied, kept = mod._apply_profile(row, url)
        check("reasoning" in kept and mod._endpoint_facts(url).get("reasoning") == "off",
              "re-applying the row never undoes what the endpoint taught")

        # Switching rows replaces the old row's own seed instead of stacking.
        u2 = "https://switch.example.test/v1"
        mod._apply_profile(mod.model_profile_row("kimi-k3"), u2)
        mod._apply_profile(mod.model_profile_row("groq"), u2)
        check(mod._endpoint_facts(u2).get("reasoning") == "off",
              "a second row replaces the first row's seed")
        mod._apply_profile(mod.model_profile_row("local"), u2)
        check("reasoning" not in mod._endpoint_facts(u2),
              "a row that seeds nothing removes the previous row's unlearned seed")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if FAILS else 0


def _suite_add_flow():
    """`model add` picks the row, applies it, and refuses a name it does not know.

    python tests/test_model_profiles.py
    """
    import contextlib
    import http.server
    import importlib.util
    import io
    import json
    import shutil
    import socketserver
    import tempfile
    import threading
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    FAILS = []

    def check(cond, what, extra=""):
        if cond:
            print("ok   %s" % what)
        else:
            FAILS.append(what)
            print("FAIL %s\n     %s" % (what, extra))

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.rstrip("/").endswith("/models"):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps({"data": [{"id": "kimi-k3-0905"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/v1" % srv.server_address[1]
    srv2 = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    url2 = "http://127.0.0.1:%d/v1" % srv2.server_address[1]

    work = Path(tempfile.mkdtemp(prefix="fbprofadd-"))
    try:
        shutil.copy2(SRC, work / "tinycmdr.py")
        (work / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE / "assets" / "model-profiles.json",
                     work / "assets" / "model-profiles.json")
        (work / "config.json").write_text(json.dumps({
            "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "main",
                    "fallbacks": []}}), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("profadd_build",
                                                      work / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["profadd_build"] = mod
        spec.loader.exec_module(mod)

        def verb(argv):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = mod.run_verb(argv)
            return rc, buf.getvalue()

        rc, out = verb(["model", "add", url, "--model", "kimi-k3-0905",
                        "--profile", "nope"])
        check(rc == 2 and "no profile named" in out,
              "an unknown profile is refused by name (%r)" % out)

        rc, out = verb(["model", "add", url])
        cfg = json.loads((work / "config.json").read_text(encoding="utf-8"))
        fbs = (cfg.get("llm") or {}).get("fallbacks") or []
        check(rc == 0 and len(fbs) == 1,
              "the endpoint was added (%r, %r)" % (rc, fbs))
        check("profile: Kimi K3" in out and "seeded: reasoning, window" in out,
              "the add says which row it used and what it seeded (%r)" % out)
        facts = mod._endpoint_facts(url)
        check(facts.get("reasoning") == "on" and facts.get("window") == 1000000,
              "auto-detection wrote the seeds (%r)" % facts)

        rc, out = verb(["model", "add", url2, "--model", "kimi-k3-0905",
                        "--profile", "none"])
        check(rc == 0 and "profile:" not in out,
              "--profile none adds the endpoint with no guess (%r)" % out)
        check("profile" not in mod._endpoint_facts(url2),
              "--profile none seeds nothing")

        rc, out = verb(["model", "profiles"])
        check(rc == 0 and "kimi-k3" in out and "anonymous" in out
              and "a profile is a starting point" in out,
              "`model profiles` lists the catalog (%r)" % out)
    finally:
        srv.shutdown()
        srv2.shutdown()
        shutil.rmtree(work, ignore_errors=True)
    return 1 if FAILS else 0


def _suite_notes_and_page():
    """The add-time probes become notes, and the page offers the same picker.

    python tests/test_model_profiles.py
    """
    import importlib.util
    import json
    import shutil
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    FAILS = []

    def check(cond, what, extra=""):
        if cond:
            print("ok   %s" % what)
        else:
            FAILS.append(what)
            print("FAIL %s\n     %s" % (what, extra))

    work = Path(tempfile.mkdtemp(prefix="fbprofnotes-"))
    try:
        shutil.copy2(SRC, work / "tinycmdr.py")
        (work / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE / "assets" / "model-profiles.json",
                     work / "assets" / "model-profiles.json")
        (work / "config.json").write_text(json.dumps({
            "llm": {"base_url": "https://api.example.test/v1", "model": "main"}}),
            encoding="utf-8")
        spec = importlib.util.spec_from_file_location("profnotes_build",
                                                      work / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["profnotes_build"] = mod
        spec.loader.exec_module(mod)

        url = "https://gateway.example.test/v1"
        k3 = mod.model_profile_row("kimi-k3")
        claude = mod.model_profile_row("claude-gateway")

        # A messages-only server: the cost sentence is recorded as a model note.
        mod._messages_wire_probe = lambda url=None, timeout=6: True
        said = mod._profile_notes(claude, url)
        note = (mod._endpoint_facts(url).get("notices") or {}).get("profile:wire") or ""
        check(said and "prompt caching" in note,
              "a messages-only endpoint gets the cost note (%r)" % note)

        # The profile expected messages; the endpoint answers chat.
        u2 = "https://chatonly.example.test/v1"
        mod._messages_wire_probe = lambda url=None, timeout=6: False
        said = mod._profile_notes(claude, u2)
        note = (mod._endpoint_facts(u2).get("notices") or {}).get("profile:wire") or ""
        check("expected a messages-only endpoint" in note and "uses the wire" in note,
              "a profile that contradicts the wire says so (%r)" % note)

        # The profile says the model sees; /props says it cannot.
        u3 = "https://blind.example.test/v1"
        mod._messages_wire_probe = lambda url=None, timeout=6: False
        mod._endpoint_vision = lambda url=None: (
            False, "the endpoint's /props reports modalities.vision=false")
        said = mod._profile_notes(k3, u3)
        note = (mod._endpoint_facts(u3).get("notices") or {}).get("profile:vision") or ""
        check("would not be attached" in note,
              "a vision veto is said against the profile's claim (%r)" % note)

        # The page's model step offers the catalog and sends the chosen id.
        page = mod._secret_form_html(
            {"stage": "model", "url": "https://api.example.test/v1",
             "env": "TINYCMDR_ENDPOINT1_API_KEY", "ids": ["kimi-k3-0905"]}, "sid")
        check('name="profile"' in page and 'value="kimi-k3"' in page
              and "not listed" in page,
              "the page's key form carries the same picker (%r)" % page[:160])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if FAILS else 0


def main():
    members = [
        ("catalog", _suite_catalog),
        ("seed_and_override", _suite_seed_and_override),
        ("add_flow", _suite_add_flow),
        ("notes_and_page", _suite_notes_and_page),
    ]
    failed = 0
    for name, fn in members:
        failed |= _run(name, fn)
    if failed:
        print("\n%d member(s) failed" % sum(1 for _ in members))
        return 1
    print("\nall model-profile checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
