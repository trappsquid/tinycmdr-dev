"""The LEGION: the rail of linked boxes, and nothing between the browser and them.

A cohort is another tinycmdr page on the LAN. This suite grades what the hub adds:
the configured list is shape-checked, the card probe gives the rail its version and
liveness, an off-LAN address warns once, and the overview is LOCAL state only - it
contacts no cohort on a page's clock. Opening the page is the browser's own
navigation, graded by tests/test_webui.py's OPEN COHORT block.

Offline and self-contained: loopback only, port 0, no model.

    python tests/test_legion.py
"""
import importlib.util
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
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


class QuietPeer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def make_card_peer(version="9.9.9"):
    """The public card one linked box serves, and a counter for how often it is read."""
    state = {"gets": 0, "auth": []}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):                              # keep the suite quiet
            pass

        def do_GET(self):
            state["gets"] += 1
            state["auth"].append(self.headers.get("Authorization") or "")
            if self.path.startswith("/.well-known/agent-card.json"):
                data = json.dumps({"name": "tinycmdr", "version": version,
                                   "capabilities": {"streaming": False}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = QuietPeer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


def test_cohort_list(mod):
    """The rail's list is the configured entries, shape-checked and name-sorted."""
    mod.CONFIG["agent"]["a2a_remotes"] = {
        "zeta": {"url": "http://127.0.0.1:1"},
        "alpha": {"url": "http://127.0.0.1:2", "token_env": "X"},
        "broken": "not-a-dict",
        "empty": {"url": ""},
    }
    names = list(mod.legion_cohorts())
    check(names == ["alpha", "zeta"],
          "configured boxes are name-sorted; malformed and empty entries are skipped",
          names)
    check(mod._legion_host("http://10.1.2.3:8790/some/path") == "10.1.2.3:8790",
          "the rail's host is host:port, never a path or credentials",
          mod._legion_host("http://10.1.2.3:8790/some/path"))
    check(mod._legion_cgnat("100.64.0.1") and mod._legion_cgnat("100.127.255.254")
          and not mod._legion_cgnat("100.128.0.1")
          and not mod._legion_cgnat("10.0.0.1")
          and not mod._legion_cgnat("box.example"),
          "the CGNAT classifier covers exactly 100.64.0.0/10")


def test_egress_warning(mod):
    """A public address warns once per entry; LAN and the overlay range stay quiet."""
    seen = []
    handler = logging.Handler()
    handler.emit = lambda rec: seen.append(rec.getMessage())
    mod.log.addHandler(handler)
    try:
        mod._LEGION_EGRESS_WARNED.clear()
        mod._legion_egress_check("pub", "http://8.8.8.8:8790")
        check("pub" in mod._LEGION_EGRESS_WARNED
              and any("does not resolve" in m for m in seen),
              "a public address warns, naming the entry", seen[-1:])
        n_warned = len(seen)
        mod._legion_egress_check("pub", "http://8.8.8.8:8790")
        check(len(seen) == n_warned, "...once per entry, not per pass")
        mod._legion_egress_check("lan", "http://127.0.0.1:8790")
        mod._legion_egress_check("cg", "http://100.101.102.103:8790")
        check("lan" not in mod._LEGION_EGRESS_WARNED
              and "cg" not in mod._LEGION_EGRESS_WARNED and len(seen) == n_warned,
              "loopback and the overlay's CGNAT range stay quiet",
              sorted(mod._LEGION_EGRESS_WARNED))
    finally:
        mod.log.removeHandler(handler)


def test_probe_and_overview(mod, srv, state):
    """The card gives the row its version; the overview is local state only."""
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    mod.CONFIG["agent"]["a2a_remotes"] = {
        "storage": {"url": url, "token_env": "LEGION_TEST_TOKEN"},
    }
    os.environ["LEGION_TEST_TOKEN"] = TOKEN
    mod._LEGION_CARDS.clear()
    entry = mod._legion_probe_card("storage")
    check(entry["ok"] and entry["card"]["version"] == "9.9.9",
          "the probe caches the card and its version", entry)
    check(state["auth"] and state["auth"][-1] == "Bearer " + TOKEN,
          "the probe carries the token from token_env", state["auth"][-1:])
    before = state["gets"]
    ov = mod.legion_overview()
    check(ov["cohorts"] == [{
              "name": "storage", "host": "127.0.0.1:%d" % srv.server_address[1],
              "url": url, "state": "ready", "error": "", "self": False,
              "version": "9.9.9", "numeral": 2}],
          "the overview names the row, its host, its version and its numeral",
          ov["cohorts"])
    check(ov["praetorium"]["host"] and ov["praetorium"]["version"],
          "the hub's own row is always present", ov["praetorium"])
    check("tabs" not in ov and "broadcast" not in ov,
          "there are no channel tabs and no fan-out in the overview", sorted(ov))
    check(state["gets"] == before,
          "reading the overview contacts no cohort (the census pays those calls)",
          (before, state["gets"]))
    # a dead box reads silent, with its error one hover away
    srv.shutdown()
    mod._LEGION_CARDS.clear()
    entry = mod._legion_probe_card("storage")
    row = mod._legion_row("storage")
    check(not entry["ok"] and row["state"] == "silent"
          and row["error"],
          "an unreachable box reads silent with its error", row)


def test_self_cohort(mod, srv):
    """A row that names the page being viewed is marked self, and refused as a cohort.

    His report (2026-10-21): a row pointing at the box he was already on opened a
    second copy of the page, and its HUB row pointed at itself - a row that did
    nothing. A cohort is another machine.
    """
    port = srv.server_address[1]
    here = "127.0.0.1:%d" % port
    url = "http://" + here
    own_name = "http://%s:%d" % (socket.gethostname(), port)
    mod.CONFIG["agent"]["a2a_remotes"] = {
        "storage": {"url": url, "token_env": "LEGION_TEST_TOKEN"},
        "me": {"url": own_name},
    }
    mod._LEGION_CARDS.clear()
    # the same authority the browser used IS this page
    rows = {r["name"]: r for r in mod.legion_overview(here)["cohorts"]}
    check(rows["storage"]["self"] is True,
          "a row at the page's own address is marked self", rows["storage"])
    # the box's own host name is caught without a Host too
    check(rows["me"]["self"] is True,
          "and so is one under this box's own host name", rows["me"])
    # ...while a loopback cohort on another port is another door, not this page
    rows = {r["name"]: r for r in mod.legion_overview("127.0.0.1:%d" % (port + 1))["cohorts"]}
    check(rows["storage"]["self"] is False,
          "a loopback cohort on another port is not this page", rows["storage"])
    # ADD/UPDATE refuse the same address before anything is written
    before = json.dumps(mod.CONFIG.get("agent", {}).get("a2a_remotes"), sort_keys=True)
    payload, code = mod.legion_add_remote("me2", url, "tok-self-suite-0123456789", here)
    check(code == 400 and "this box" in (payload.get("error") or ""),
          "ADD COHORT refuses the page's own address", (code, payload))
    payload, code = mod.legion_add_remote("me3", own_name, "tok-self-suite-0123456789",
                                          "127.0.0.1:1")
    check(code == 400 and "this box" in (payload.get("error") or ""),
          "...and this box's own host name", (code, payload))
    check(json.dumps(mod.CONFIG.get("agent", {}).get("a2a_remotes"), sort_keys=True) == before,
          "...with nothing written", mod.CONFIG.get("agent", {}).get("a2a_remotes"))


def main():
    mod, _stage = stage_module("tinycmdr_legion", "links")
    srv, state = make_card_peer()
    try:
        test_cohort_list(mod)
        test_egress_warning(mod)
        test_probe_and_overview(mod, srv, state)
        test_self_cohort(mod, srv)
    finally:
        srv.shutdown()
    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all legion checks passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
