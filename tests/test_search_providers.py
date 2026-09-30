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
    (audit, 2026-09-27).

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

    def _send(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        _Stub.calls.append(("POST", self.path, payload,
                            self.headers.get("Authorization")))
        self._send({"code": 0, "message": "success", "data": {"results": [
            {"title": "Stub AnySearch hit", "url": "https://example.invalid/a",
             "snippet": "anysearch snippet"}]}})

    def do_GET(self):
        _Stub.calls.append(("GET", self.path, parse_qs(urlparse(self.path).query), None))
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
check("the shipped chain is the two built-ins, in order",
      [p["kind"] for p in fb.DEFAULT_CONFIG["search"]["providers"]] == ["anysearch", "tavily"],
      fb.DEFAULT_CONFIG["search"]["providers"])
check("egress is ON by default - a configured chain that refuses every lookup reads as broken",
      fb.DEFAULT_CONFIG["search"]["allow_cloud_egress"] is True,
      fb.DEFAULT_CONFIG["search"]["allow_cloud_egress"])
check("the default chain names each key's .env variable",
      [p.get("api_key_env") for p in fb.DEFAULT_CONFIG["search"]["providers"]]
      == ["ANYSEARCH_API_KEY", "TAVILY_API_KEY"])

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
check("an unknown kind is a reported problem, not a silent drop",
      chain == [] and any("unknown kind" in p for p in problems), problems)
check("a searxng entry with no url is a reported problem too",
      any("needs its own url" in p for p in problems), problems)

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
check("...and the refusal names the flag that would allow it",
      "search.allow_cloud_egress" in out, out[:200])
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

os.environ.pop("TAVILY_API_KEY", None)
search_config([{"kind": "tavily"}, {"kind": "searxng", "url": BASE_URL}], False)
out = fb.tool_web_search({"query": "fall through"}, {})
check("a provider with no key set falls through to the next",
      "Stub SearxNG hit" in out, out[:200])

# A LOCAL url on purpose: an off-LAN one is refused by the gate before the missing key is
# ever read, so this would grade the gate instead of the fallback.
search_config([{"kind": "tavily", "url": BASE_URL}], False)
out = fb.tool_web_search({"query": "all fail"}, {})
check("when every provider fails the error names them",
      out.startswith("ERROR:") and "tavily" in out, out[:200])

search_config([{"kind": "nope"}, {"kind": "searxng"}], False)
out = fb.tool_web_search({"query": "typo"}, {})
check("a typo'd providers list is named in the refusal",
      out.startswith("BLOCKED:") and "unknown kind" in out, out[:300])

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

# ------------------------------------------------- a key in config.json is a copy
(STAGE / "config.json").write_text(json.dumps(
    {"search": {"anysearch_api_key": "leftover-key"}}), encoding="utf-8")
cfg, said = capturing(fb.load_config)
check("a search key left in config.json is IGNORED, with a warning that names .env",
      "IGNORED" in said and "ANYSEARCH_API_KEY" in said, said[:300])
check("...and the loaded config carries no such field",
      "anysearch_api_key" not in (cfg.get("search") or {}), cfg.get("search"))

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

_srv.shutdown()
print()
if FAILURES:
    print("%d check(s) failed: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("web search: the configured chain, and the consent that gates it")
