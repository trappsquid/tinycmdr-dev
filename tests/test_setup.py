"""`tinycmdr setup` asks about web-search consent, and writes the answer.

The wizard covered the model endpoint and the two chat gateways, and nothing else: the
egress consent that `web_search`/`fetch_url` answer to was only asked by the INSTALLER
and by `tinycmdr config set`, so an existing install had no interactive door to it. A
lookup that needs the web then came back "BLOCKED ... allow_cloud_egress is false" with
the operator holding no obvious way to say yes.

The wizard is driven here through a fake tty (it refuses a pipe), with every other
answer left empty so only the search section is doing anything.

    python tests/test_setup.py
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

SRC = Path(__file__).resolve().parent.parent / "tinycmdr.py"
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


def drive(dirpath, answer, page=("", "", "", "")):
    """Run the wizard with every answer empty but the page's and the last one.

    Prompt order: local/cloud, url, model, Mattermost?, Telegram?, page-serve,
    page-LAN (only when serving), page-port (only when serving), page-token (only
    when serving), egress. `page` carries the four page answers; `answer` is the
    egress one.
    """
    spec = importlib.util.spec_from_file_location("setup_" + dirpath.name,
                                                  dirpath / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.__dict__["_ANSWERS"] = ["", "", "", "", ""] + list(page) + [answer]
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
        # a fresh install says yes
        d = stage(work / "yes", False)
        rc, out, written, mod = drive(d, "y")
        check(rc == 0, f"the wizard completes ({rc})", out[-300:])
        check("off this LAN" in out,
              "the search question is asked, and names what it means", out[-500:])
        check(written.get("search", {}).get("allow_cloud_egress") is True,
              "answering yes writes search.allow_cloud_egress = true",
              json.dumps(written.get("search")))
        check(mod.CONFIG["search"]["allow_cloud_egress"] is True,
              "and the running process picks it up without a restart")
        check("Web search" in out and "off-LAN providers allowed" in out,
              "the summary reports it", out[-400:])

        # and no turns it back off
        d = stage(work / "no", True)
        rc, out, written, mod = drive(d, "n")
        check(rc == 0 and written["search"]["allow_cloud_egress"] is False,
              "answering no writes false", json.dumps(written.get("search")))
        check("this LAN only" in out, "and the summary says so", out[-300:])

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
        d = stage(work / "page-lan", False)
        # an earlier wizard in THIS process minted a token and left it in os.environ
        os.environ.pop("TINYCMDR_WEB_TOKEN", None)
        rc, out, written, mod = drive(d, "", page=("y", "y", "", ""))
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
        check("Page         : http://0.0.0.0:8790" in out,
              "the summary reports the bind", out[-400:])

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
        rc, out, written, mod = drive(d, "", page=("y", "y", "", "my-own-LAN-token-4242"))
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
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
