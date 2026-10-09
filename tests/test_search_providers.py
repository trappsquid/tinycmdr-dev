"""Web search: the provider chain, and the egress consent that gates it.

Two claims are asserted here, because both are easy to state in a comment and get wrong in
the code:

  * the chain is CONFIGURED (`search.providers`) - a host can insert its own provider, a
    SearxNG on the LAN or a paid key for a built-in, at install or later, with no code
    change;
  * an off-LAN provider is NOT called unless the operator allowed it. The model lane has
    carried this rule since the failover pin (`llm.allow_cloud_fallback`); search shipped
    with a third-party default and no gate at all, so a keyless install sent the model's
    query to api.anysearch.com with nobody asked and nothing on screen saying so
    (review, 2026-09-27).

Hermetic: the providers are served by a stub on 127.0.0.1, so this grades with no network.
"Off-LAN" here means a name that resolves nowhere, which `_is_local_url` classifies as
REMOTE deliberately - the pessimistic branch that never invents an off-LAN send out of a
failure to answer.

    python tests/test_search_providers.py
"""
import importlib.util
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-search"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
(STAGE / "config.json").write_text(json.dumps({"search": {}}), encoding="utf-8")
spec = importlib.util.spec_from_file_location("tinycmdr_search", STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_search"] = fb
spec.loader.exec_module(fb)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("ok   %s" % name)
    else:
        FAILURES.append(name)
        print("FAIL %s: %s" % (name, detail))


# --------------------------------------------------------------- the stub provider
class _Stub(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):        # the runner's log stays readable
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_raw(self, body, ctype="text/html"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        _Stub.calls.append(("POST", self.path, payload,
                            self.headers.get("Authorization")))
        if "/post405" in self.path:
            self.send_response(405)
            self.end_headers()
            return
        if "/fail" in self.path:
            self._send({"detail": "bad key", "message": "quota exhausted"}, 500)
            return
        if "/empty" in self.path:
            self._send({"results": []})
            return
        self._send({"code": 0, "message": "success", "data": {"results": [
            {"title": "Stub AnySearch hit", "url": "https://example.invalid/a",
             "snippet": "anysearch snippet"}]}})

    def do_GET(self):
        _Stub.calls.append(("GET", self.path, parse_qs(urlparse(self.path).query), None))
        if self.path.startswith("/goto-page"):
            # A RELATIVE Location: the follower must urljoin it, not hand it to the client.
            self.send_response(302)
            self.send_header("Location", "/page")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/goto-off"):
            self.send_response(302)
            self.send_header("Location", "http://no-such-host.invalid/x")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if "/fail" in self.path:
            self._send({"detail": "bad key", "message": "quota exhausted"}, 500)
            return
        if "/empty" in self.path:
            self._send({"results": []})
            return
        if self.path.startswith("/bigscript"):
            # iter_content reads in 64 KiB chunks and the loop breaks AFTER the chunk
            # that crosses the budget, so the cut lands at 65536 bytes: the script must
            # OPEN before it and CLOSE after it for the defect to be reachable.
            self._send_raw("<!doctype html><html><body><p>HELLO PAGE</p>"
                           + ("<!-- pad -->" * 4500)
                           + "<script>var marker='JS_SOURCE_MARKER';"
                           + ("x" * 50000)
                           + "</script><p>TAIL AFTER THE SCRIPT</p></body></html>")
            return
        self._send({"results": [{"title": "Stub SearxNG hit",
                                 "url": "https://example.invalid/s",
                                 "content": "searxng snippet"}]})


_srv = HTTPServer(("127.0.0.1", 0), _Stub)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
BASE_URL = "http://127.0.0.1:%d" % _srv.server_address[1]
REMOTE = "https://api.anysearch.com/v1/search"


def search_config(providers, egress):
    fb.CONFIG["search"]["providers"] = providers
    fb.CONFIG["search"]["allow_cloud_egress"] = egress
    # The config.json reloads above can leave the section without the defaults the
    # loader had merged at import; the tool reads max_results unconditionally.
    fb.CONFIG["search"].setdefault("max_results",
                                   fb.DEFAULT_CONFIG["search"]["max_results"])
    _Stub.calls.clear()


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.text = []

    def emit(self, record):
        self.text.append(record.getMessage())


def capturing(fn):
    """Run fn with the module log captured -> (result, everything it said)."""
    h = _Capture()
    fb.log.addHandler(h)
    old, fb.log.level = fb.log.level, logging.DEBUG
    try:
        out = fn()
    finally:
        fb.log.removeHandler(h)
        fb.log.level = old
    return out, "\n".join(h.text)


# ------------------------------------------------------------------- the defaults
check("the shipped chain is ONE built-in (anysearch) - nothing ships that needs a key",
      [p["kind"] for p in fb.DEFAULT_CONFIG["search"]["providers"]] == ["anysearch"],
      fb.DEFAULT_CONFIG["search"]["providers"])
check("egress is ON by default - a configured chain that refuses every lookup reads as broken",
      fb.DEFAULT_CONFIG["search"]["allow_cloud_egress"] is True,
      fb.DEFAULT_CONFIG["search"]["allow_cloud_egress"])
check("the default chain names each key's .env variable",
      [p.get("api_key_env") for p in fb.DEFAULT_CONFIG["search"]["providers"]]
      == ["ANYSEARCH_API_KEY"])

# ------------------------------------------------------------ what is on this LAN
check("a name that resolves nowhere is REMOTE (the pessimistic branch)",
      fb._is_local_url(REMOTE) is False)
check("loopback is LOCAL", fb._is_local_url(BASE_URL + "/") is True)

# F-07: the locality verdict expires. A hostname that first resolved private and later
# points off-LAN (DNS moved) must be re-classified, or every gate - failover, search and
# fetch egress - keeps saying "local" and traffic leaves with allow_cloud_egress: false.
_local_calls = []
_state = {"local": True}
_real_host_is_local = fb._host_is_local
try:
    fb._LOCAL_URL_CACHE.clear()
    fb._host_is_local = lambda h: (_local_calls.append(h), _state["local"])[1]
    check("a fresh hostname is classified and cached",
          fb._is_local_url("http://lan-box:8080/") is True and len(_local_calls) == 1,
          _local_calls)
    _state["local"] = False
    check("...the cached verdict is reused inside the TTL",
          fb._is_local_url("http://lan-box:8080/") is True and len(_local_calls) == 1,
          _local_calls)
    _verdict, _at = fb._LOCAL_URL_CACHE["lan-box"]
    fb._LOCAL_URL_CACHE["lan-box"] = (_verdict, _at - fb.WINDOW_TTL - 1)
    check("...but past the TTL the verdict is re-derived",
          fb._is_local_url("http://lan-box:8080/") is False and len(_local_calls) == 2,
          _local_calls)
finally:
    fb._host_is_local = _real_host_is_local
    fb._LOCAL_URL_CACHE.clear()

# --------------------------------------------------------------- resolving the chain
search_config([{"kind": "searxng", "url": BASE_URL}], False)
chain, problems = fb._search_providers()
check("a configured searxng entry resolves with no key env",
      len(chain) == 1 and chain[0]["kind"] == "searxng" and chain[0]["api_key_env"] == "",
      chain)
check("...and it produces no complaints", problems == [], problems)

search_config([{"kind": "nope"}, {"kind": "searxng"}], False)
chain, problems = fb._search_providers()
check("a kind with no url and no adapter is a reported problem, never a silent drop",
      chain == [] and len(problems) == 2
      and all("needs its own url" in p for p in problems), problems)
check("...and the report names the entry and what it is missing",
      "entry 1" in problems[0] and "nope" in problems[0], problems)

search_config(["anysearch"], False)
chain, problems = fb._search_providers()
check("a bare kind string is accepted and takes the built-in url",
      chain and chain[0]["kind"] == "anysearch"
      and chain[0]["url"] == fb._SEARCH_DEFAULT_URL["anysearch"], chain)

# ------------------------------------------------------------------- the egress gate
search_config([{"kind": "anysearch", "url": REMOTE}], False)
out = fb.tool_web_search({"query": "anything"}, {})
check("with egress off, an off-LAN provider is REFUSED",
      out.startswith("BLOCKED:"), out[:120])
check("...and nothing was sent to it", _Stub.calls == [], _Stub.calls)
check("...and the refusal names the door that would allow it",
      "tinycmdr search allow true" in out, out[:400])
usable, withheld, problems = fb._search_chain_in_use()
check("...the chain in use is empty and the withheld list is not",
      usable == [] and len(withheld) == 1 and problems == [], (usable, withheld))

search_config([{"kind": "anysearch", "url": REMOTE}], True)
usable, withheld, problems = fb._search_chain_in_use()
check("the same entry is usable once the operator allows egress",
      len(usable) == 1 and withheld == [], (usable, withheld))

# --------------------------------------------- a LAN provider needs no permission
search_config([{"kind": "searxng", "url": BASE_URL}], False)
out = fb.tool_web_search({"query": "lan query"}, {})
check("a LAN provider answers with egress off",
      "Stub SearxNG hit" in out and not out.startswith("BLOCKED"), out[:200])
check("...and it was asked in the shape a SearxNG expects",
      any(c[0] == "GET" and c[2].get("format") == ["json"] and c[2].get("q") == ["lan query"]
          for c in _Stub.calls), _Stub.calls)

search_config([{"kind": "searxng", "url": BASE_URL},
               {"kind": "anysearch", "url": REMOTE}], False)
fs = fb.tool_web_search({"query": "mixed"}, {})
check("a LAN answer says the off-LAN entries were withheld",
      "Stub SearxNG hit" in fs and "withheld" in fs and "allow_cloud_egress" in fs, fs[:300])

# ---------------------------------------------------- order, payloads and fallbacks
search_config([{"kind": "anysearch", "url": BASE_URL + "/v1/search"},
               {"kind": "searxng", "url": BASE_URL}], False)
out = fb.tool_web_search({"query": "first wins"}, {})
check("the first provider that answers wins",
      "Stub AnySearch hit" in out, out[:200])
check("...and the call carried the query and max_results",
      _Stub.calls and _Stub.calls[0][2] == {"query": "first wins", "max_results": 5},
      _Stub.calls[:1])

search_config([{"kind": "searxng", "url": "http://127.0.0.1:9"},
               {"kind": "searxng", "url": BASE_URL}], False)
out = fb.tool_web_search({"query": "fall through"}, {})
check("a provider that cannot answer falls through to the next",
      "Stub SearxNG hit" in out, out[:200])

# A LOCAL url on purpose: an off-LAN one is refused by the gate before the failure is
# ever seen, so this would grade the gate instead of the fallback.
search_config([{"kind": "searxng", "url": "http://127.0.0.1:9"}], False)
out = fb.tool_web_search({"query": "all fail"}, {})
check("when every provider fails the error names them",
      out.startswith("ERROR:") and "searxng" in out, out[:200])

search_config([{"kind": "nope"}, {"kind": "searxng"}], False)
out = fb.tool_web_search({"query": "typo"}, {})
check("a typo'd providers list is named in the refusal",
      out.startswith("BLOCKED:") and "needs its own url" in out, out[:300])

# ------------------------------------------------------------------ fetch_url's gate
search_config([{"kind": "anysearch", "url": REMOTE}], False)
out = fb.tool_fetch_url({"url": "https://example.invalid/page"}, {})
check("fetch_url refuses an off-LAN url while egress is off",
      out.startswith("BLOCKED:"), out[:150])
check("...and says a LAN url is still allowed", "on this LAN is always allowed" in out, out[:300])
_Stub.calls.clear()
out = fb.tool_fetch_url({"url": BASE_URL + "/page"}, {})
check("...while a LAN url is fetched normally",
      not out.startswith("BLOCKED") and _Stub.calls != [], (out[:80], _Stub.calls))
search_config([{"kind": "anysearch", "url": REMOTE}], True)
out = fb.tool_fetch_url({"url": BASE_URL + "/page"}, {})
check("and the flag does not break a LAN fetch", not out.startswith("BLOCKED"), out[:80])

# ---------------------------------------------- a redirect stays under the same gate
search_config([{"kind": "anysearch", "url": REMOTE}], False)
_Stub.calls.clear()
out = fb.tool_fetch_url({"url": BASE_URL + "/goto-page"}, {})
check("a LAN redirect is followed by hand, one hop at a time",
      "Stub SearxNG hit" in out and not out.startswith("BLOCKED"), out[:200])
check("...with both hops really requested", ["/goto-page", "/page"] ==
      [c[1] for c in _Stub.calls if c[0] == "GET"], _Stub.calls)
_Stub.calls.clear()
out = fb.tool_fetch_url({"url": BASE_URL + "/goto-off"}, {})
check("a LAN url that redirects off-LAN is refused before the second hop",
      out.startswith("BLOCKED:") and "no-such-host.invalid" in out, out[:300])
check("...and the off-LAN hop was never requested",
      [c[1] for c in _Stub.calls if c[0] == "GET"] == ["/goto-off"], _Stub.calls)
search_config([{"kind": "anysearch", "url": REMOTE}], True)
_Stub.calls.clear()
out = fb.tool_fetch_url({"url": BASE_URL + "/goto-page"}, {})
check("the flag lets a redirect run as usual", "Stub SearxNG hit" in out, out[:120])

# ---------------------------------------- a local fetch goes direct, not via a proxy
# With http_proxy set, requests sent even http://127.0.0.1/ through the proxy (measured
# 2026-10-09): the "local" fetch left the box and nothing in the gate saw it. The proxy
# stub here IS this suite's server, so what it receives would be the absolute-form
# "GET http://127.0.0.1:PORT/page" instead of the path-only form a direct request has.
search_config([{"kind": "anysearch", "url": REMOTE}], False)
_Stub.calls.clear()
_saved_proxy = {k: os.environ.get(k) for k in ("http_proxy", "no_proxy", "NO_PROXY")}
os.environ["http_proxy"] = BASE_URL
os.environ["no_proxy"] = ""
os.environ.pop("NO_PROXY", None)
try:
    out = fb.tool_fetch_url({"url": BASE_URL + "/page"}, {})
finally:
    for _k, _v in _saved_proxy.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v
_got = [c[1] for c in _Stub.calls if c[0] == "GET"]
check("a local fetch ignores an environment proxy (it is reached directly)",
      "Stub SearxNG hit" in out and _got == ["/page"], (out[:80], _got))

# ------------------------------------------------- a key in config.json is a copy
(STAGE / "config.json").write_text(json.dumps(
    {"search": {"anysearch_api_key": "leftover-key"}}), encoding="utf-8")
cfg, said = capturing(fb.load_config)
check("a search key left in config.json is IGNORED, with a warning that names .env",
      "IGNORED" in said and "ANYSEARCH_API_KEY" in said, said[:300])
check("...and the loaded config carries no such field",
      "anysearch_api_key" not in (cfg.get("search") or {}), cfg.get("search"))

# ------------------------------------------- an entry's state: would it answer?
# Every supported kind answers without a key (anysearch's anonymous tier; a searxng box),
# so the only thing that makes an entry unable is the off-LAN consent.
_anon = {"kind": "anysearch", "url": REMOTE, "label": "anysearch",
         "api_key_env": "ANYSEARCH_API_KEY"}
check("anysearch with no key IS ready (the anonymous tier)",
      fb._search_entry_ready(_anon, True) is True
      and "anonymous" in fb._search_entry_state(_anon, True),
      fb._search_entry_state(_anon, True))
check("a LAN provider is ready with egress off",
      fb._search_entry_ready({"kind": "searxng", "url": BASE_URL, "label": "x",
                              "api_key_env": ""}, False) is True)
check("an off-LAN provider is not ready with egress off",
      fb._search_entry_ready(_anon, False) is False
      and fb._search_entry_state(_anon, False) == "off-LAN, refused",
      fb._search_entry_state(_anon, False))

# tavily was a built-in alongside anysearch; it is GONE as a kind and an adapter (its API
# refused keyless calls, so it shipped as dead weight). A config that still names it is
# accepted like any other url: the OPEN contract is a JSON POST, so a provider this file
# has never heard of needs a url, not a code change.
check("tavily is no longer a kind or an adapter",
      "tavily" not in fb._SEARCH_DEFAULT_URL
      and "tavily" not in fb._SEARCH_PROVIDERS_BY_KIND
      and "tavily" not in fb._SEARCH_DEFAULT_KEY_ENV,
      sorted(fb._SEARCH_PROVIDERS_BY_KIND))
search_config([{"kind": "tavily", "url": REMOTE}], False)
_chain, _problems = fb._search_providers()
check("a config naming an unknown kind is accepted as a generic provider",
      _problems == [] and len(_chain) == 1 and _chain[0]["kind"] == "generic"
      and _chain[0]["label"] == "tavily", (_chain, _problems))
search_config([{"kind": "nope"}], False)
_chain, _problems = fb._search_providers()
check("...while a kind with no url of its own is still a named problem",
      _chain == [] and _problems and "needs its own url" in _problems[0],
      (_chain, _problems))

# The open contract itself: a POST of {query, max_results}, the key as Bearer, answered
# with results - and a 405 retried as searxng's GET.
_generic = {"kind": "generic", "url": BASE_URL + "/generic", "label": "custom",
            "api_key_env": ""}
search_config([_generic], True)
idx = len(_Stub.calls)
out = fb.tool_web_search({"query": "open contract"}, {})
shot = _Stub.calls[idx] if len(_Stub.calls) > idx else None
check("a url alone is a JSON POST of {query, max_results}",
      shot and shot[0] == "POST" and shot[2] == {"query": "open contract",
                                                 "max_results": 5},
      shot)
check("...and the answer's results list is read", "Stub AnySearch hit" in out, out[:200])
os.environ["MY_PROVIDER_KEY"] = "sekret-bearer"
try:
    _keyed = dict(_generic, api_key_env="MY_PROVIDER_KEY")
    search_config([_keyed], True)
    idx = len(_Stub.calls)
    out = fb.tool_web_search({"query": "keyed"}, {})
    shot = _Stub.calls[idx] if len(_Stub.calls) > idx else None
    check("...with its key as Authorization: Bearer",
          shot and shot[3] == "Bearer sekret-bearer", shot)
finally:
    os.environ.pop("MY_PROVIDER_KEY", None)
search_config([{"kind": "generic", "url": BASE_URL + "/post405", "label": "getonly"}],
              True)
idx = len(_Stub.calls)
out = fb.tool_web_search({"query": "fallback"}, {})
verbs = [c[0] for c in _Stub.calls[idx:]]
check("a 405 on the POST retries as GET ?q=&format=json (the searxng shape)",
      verbs[:2] == ["POST", "GET"] and "Stub SearxNG hit" in out, (verbs, out[:200]))

# The BLOCKED line is what the MODEL relays, so it must name doors the operator can
# actually type - "web search is disabled" and nothing else is not an answer.
search_config([{"kind": "anysearch", "url": REMOTE}], False)
out = fb.tool_web_search({"query": "doors"}, {})
check("the refusal names the allow door by its verb",
      "tinycmdr search allow true" in out, out[:400])
check("...and the add-your-own door",
      "tinycmdr search add <url>" in out, out[:400])

# ------------------------------------------- building an entry: the key's home is .env
envp = STAGE / ".env"
envp.unlink(missing_ok=True)
entry, err = fb._search_entry_build("brave", REMOTE, "my-brave", "", "")
check("a kind the file never heard of is kept as its name (the generic POST calls it)",
      err == "" and entry == {"kind": "brave", "url": REMOTE, "label": "my-brave"},
      (entry, err))
entry, err = fb._search_entry_build("", BASE_URL + "/", "", "", "")
check("no kind at all means generic, and the label defaults to the url's host",
      err == "" and entry["kind"] == "generic"
      and entry["label"] == BASE_URL.split("//", 1)[1], entry)
_bad, _berr = fb._search_entry_build("bad kind!", BASE_URL, "", "", "")
check("a kind with junk in it is refused, with the shape spelled out",
      _bad is None and "short name" in _berr, _berr)
entry, err = fb._search_entry_build("searxng", BASE_URL + "/", "mybox", "", "")
check("a searxng entry needs no key and its url is trimmed",
      err == "" and entry == {"kind": "searxng", "url": BASE_URL, "label": "mybox"},
      entry)
entry2, err2 = fb._search_entry_build("anysearch", REMOTE, "", "", "sekret-key")
check("a typed key lands in .env under a GENERATED name, and only NAMED in the entry",
      err2 == "" and entry2["api_key_env"] == "TINYCMDR_SEARCH1_API_KEY"
      and "sekret-key" not in json.dumps(entry2)
      and "TINYCMDR_SEARCH1_API_KEY=sekret-key" in envp.read_text(encoding="utf-8"),
      (entry2, envp.read_text(encoding="utf-8")[-120:] if envp.exists() else ""))
entry3, _err3 = fb._search_entry_build("anysearch", REMOTE, "", "", "second-key")
check("...and a second keyed provider gets its own name",
      entry3["api_key_env"] == "TINYCMDR_SEARCH2_API_KEY", entry3)

# ------------------------------------------------------------------ the .env doors
os.environ["TINYCMDR_SEARCH_PROVIDERS"] = '[{"kind": "searxng", "url": "http://127.0.0.1:9"}]'
os.environ["TINYCMDR_SEARCH_EGRESS"] = "yes"
cfg, said = capturing(fb.load_config)
check("TINYCMDR_SEARCH_PROVIDERS lands typed, not as a string",
      (cfg["search"]["providers"][0].get("kind") == "searxng"), cfg["search"]["providers"])
check("TINYCMDR_SEARCH_EGRESS accepts yes/on/true", cfg["search"]["allow_cloud_egress"] is True)

os.environ["TINYCMDR_SEARCH_EGRESS"] = "banana"
cfg, said = capturing(fb.load_config)
check("a typo in the egress value is refused, not read as False",
      cfg["search"]["allow_cloud_egress"] is fb.DEFAULT_CONFIG["search"]["allow_cloud_egress"]
      and "could not be parsed" in said, said[:200])
for _n in ("TINYCMDR_SEARCH_PROVIDERS", "TINYCMDR_SEARCH_EGRESS"):
    os.environ.pop(_n, None)

# ------------------------------------------------------------- the doors on top
import contextlib
import io


def _verb(*args):
    """Run the real `config` verb against the stage's config.json, quietly."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = fb._verb_config(list(args))
    return rc, buf.getvalue()


rc, said = _verb("set", "search.providers", '[{"kind": "searxng", "url": "http://127.0.0.1:9"}]')
check("config set search.providers accepts a chain (the post-install door)",
      rc == 0 and "searxng" in said, (rc, said[:120]))
rc, said = _verb("get", "search.providers")
check("...and reads it back", rc == 0 and "searxng" in said, (rc, said[:120]))
rc, said = _verb("set", "search.anysearch_api_key", "secret")
check("config set refuses a provider KEY and points at .env",
      rc == 2 and "token set" in said, (rc, said[:160]))
rc, said = _verb("set", "agent.max_steps", "7")
check("...while an ordinary setting is still accepted", rc == 0, (rc, said[:120]))

# --------------------------- A-2026-10-08-146: an HTTP error is not an empty result
search_config([{"kind": "searxng", "url": BASE_URL + "/fail"},
               {"kind": "searxng", "url": BASE_URL}], False)
out = fb.tool_web_search({"query": "error falls through"}, {})
check("a provider's HTTP error falls through to the next provider",
      "Stub SearxNG hit" in out, out[:200])
search_config([{"kind": "searxng", "url": BASE_URL + "/empty"},
               {"kind": "searxng", "url": BASE_URL}], False)
out = fb.tool_web_search({"query": "empty falls through"}, {})
check("...and an empty result set does too (another index may hold the query)",
      "Stub SearxNG hit" in out, out[:200])
search_config([{"kind": "searxng", "url": BASE_URL + "/fail"}], False)
out = fb.tool_web_search({"query": "the only one fails"}, {})
check("the failure is NAMED, never answered as 'No results'",
      out.startswith("ERROR:") and "500" in out, out[:200])

# ---------------------- A-2026-10-08-150: a byte cut inside a <script> is not page text
_script_page = fb._fetch_page(BASE_URL + "/bigscript", 1000)
check("a cut landing inside an inline <script> does not return the script source",
      "JS_SOURCE_MARKER" not in _script_page, _script_page[:200])
check("...and the text before the block is kept",
      "HELLO PAGE" in _script_page, _script_page[:120])

_srv.shutdown()
print()
if FAILURES:
    print("%d check(s) failed: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("web search: the configured chain, and the consent that gates it")
