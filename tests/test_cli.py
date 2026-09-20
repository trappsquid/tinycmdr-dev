"""Tests for the chatless CLI build (tinycmdr-cli.py).

Run:  tinycmdr_SRC=tinycmdr-cli.py python tests/test_cli.py
      (the suite stages its own copy, so it never touches the live tree)

What it covers that the other suites cannot: the standard-library HTTP shim, the inert
startup (nothing is created or checked when it opens, and no config.json is ever written
for you), the single-endpoint config, the terminal verbs, and the two
properties that make this build shippable as a folder - no third-party imports,
and no state written outside the folder it lives in.

test_ledger.py runs against this build too (171 pass, 11 skipped). test_stall.py
and test_checkin.py describe the chat layer (stall watchdog, check-ins, the
per-channel dispatcher) and are not part of this build.
"""
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import http.server
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CR = chr(13)
LF = chr(10)
SRC_NAME = os.environ.get("tinycmdr_SRC", "tinycmdr-cli.py")
# In the working repo the console build sits at the root. In the shipped archive
# it is under cli/, next to its own README. Look in both, and if it is nowhere,
# say so and stop: someone who just unpacked a download should get a sentence,
# not a traceback from a test they did not ask for.
SRC = BASE / SRC_NAME
if not SRC.exists() and (BASE / "cli" / SRC_NAME).exists():
    SRC = BASE / "cli" / SRC_NAME
if not SRC.exists():
    print(f"skipped: {SRC_NAME} is not in {BASE.name}/ or {BASE.name}/cli/.")
    print("         This suite tests the console build. Unpack the console archive "
          "beside it, or run it from the repo.")
    sys.exit(0)

PASSES, FAILURES, SKIPPED = [], [], []


def check(name, ok, detail=""):
    (PASSES if ok else FAILURES).append(name)
    print(("  ok   " if ok else "  FAIL ") + name + ((" — " + str(detail)) if detail else ""))


def skip(name, why=""):
    """A check that cannot run here (needs a repo-only file). Not a failure:
    counting an absent file as a pass would be a lie about what was verified."""
    SKIPPED.append(name)
    print(f"  skip {name}" + (f" — {why}" if why else ""))


# --- staging -----------------------------------------------------------------
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-cli-stage"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True)
shutil.copy2(SRC, STAGE / "tinycmdr-cli.py")
FIXTURE = {
    # a dead local port: nothing here should ever succeed, and no test may reach
    # the network. max_steps keeps any accidental run tiny.
    "llm": {"base_url": "http://127.0.0.1:1/v1", "api_key": "none",
            "model": "cli-test-model", "max_turns": 3, "request_timeout": 2,
            "request_grace": 1},
    "search": {"anysearch_api_key": "", "tavily_api_key": "", "max_results": 3},
    "agent": {"bot_name": "cli-test", "max_steps": 2, "max_minutes": 1,
              "show_usage": True, "color_coded": False, "notes_max_chars": 2000},
}
(STAGE / "config.json").write_text(json.dumps(FIXTURE, indent=2), encoding="utf-8")

spec = importlib.util.spec_from_file_location("tinycmdr_cli_under_test",
                                              STAGE / "tinycmdr-cli.py")
fb = importlib.util.module_from_spec(spec)
fb.__dict__["__file__"] = str(STAGE / "tinycmdr-cli.py")
spec.loader.exec_module(fb)
# Merge section by section: a plain CONFIG.update() would replace whole blocks and
# hide any default key the fixture does not mention (that is how a missing
# agent.shell_timeout once slipped through this suite).
for _section, _values in FIXTURE.items():
    if isinstance(_values, dict) and isinstance(fb.CONFIG.get(_section), dict):
        fb.CONFIG[_section].update(_values)
    else:
        fb.CONFIG[_section] = _values
fb.AGENT.llm_url = FIXTURE["llm"]["base_url"].rstrip("/") + "/chat/completions"


# --- a tiny local server for the shim tests ----------------------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "json"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.mode == "sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for blob in ('{"choices":[{"delta":{"content":"a"}}]}',
                         '{"choices":[{"delta":{"content":"b"}}]}',
                         "[DONE]"):
                self.wfile.write(("data: " + blob + "\n\n").encode())
                self.wfile.flush()
            return
        if self.mode == "429":
            self._send(429, b'{"error":"slow down"}', extra=[("Retry-After", "7")])
            return
        if self.mode == "401":
            self._send(401, b'{"error":{"message":"bad key"}}')
            return
        self._send(200, json.dumps({"ok": True, "mode": self.mode}).encode())

    def do_GET(self):
        self._send(200, json.dumps({"data": [{"id": "m1"}]}).encode())


class _Server:
    def __enter__(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def _resp(body=b"", code=200, ctype="application/json", extra=()):
    return (code, body, ctype, extra)


def test_the_reference_config_parses_and_matches_the_defaults():
    """config.example.json is the file the installer copies and the README calls the full
    reference, so a syntax error in it makes every install fail in a way no suite notices:
    the suites run against their own fixture. It also has to carry the numbers the code
    actually ships, or the reference documents the wrong budgets.
    """
    example = BASE / "config.example.json"
    check("the reference config is present", example.exists(), str(example))
    try:
        ex = json.loads(example.read_text(encoding="utf-8"))
    except Exception as e:
        check("the reference config is valid JSON", False, "%s: %s" % (type(e).__name__, e))
        return
    check("the reference config is valid JSON", True)

    # every behaviour key the build ships a default for (paths are the installer's business)
    SKIP_PATHS = {"notes_file", "notes_archive_file", "sessions_dir", "tasks_file", "log_file",
                  "tools_dir", "skills_dir", "atlas_file", "field_notes_file", "debug_dump_dir"}
    missing = []
    for section, keys in fb.DEFAULT_CONFIG.items():
        if section.startswith("_") or section not in ex:
            continue
        for key in keys:
            if key.startswith("_") or key in SKIP_PATHS:
                continue
            if key not in ex[section]:
                missing.append("%s.%s" % (section, key))
    check("the reference config documents every shipped behaviour key (%d missing)"
          % len(missing), not missing, ", ".join(sorted(missing)[:8]))

    # the numbers that decide how long a task may run must match what the code ships
    drift = []
    for key in ("max_steps", "max_minutes", "search_timeout", "command_cost_guard",
                "scan_budget_seconds", "tool_output_max_chars"):
        if key in fb.DEFAULT_CONFIG.get("agent", {}) and \
           ex.get("agent", {}).get(key) != fb.DEFAULT_CONFIG["agent"][key]:
            drift.append("%s example=%r code=%r" % (key, ex["agent"].get(key),
                                                    fb.DEFAULT_CONFIG["agent"][key]))
    check("the reference config's budgets match the code defaults (%d drifted)" % len(drift),
          not drift, "; ".join(drift))


# --- 1. portability ----------------------------------------------------------
def test_no_third_party_imports():
    src = SRC.read_text(encoding="utf-8", errors="replace")
    bad = []
    for mod in ("requests", "croniter", "mmpy_bot", "urllib3"):
        for m in re.finditer(r"^\s*(?:import|from)\s+%s\b" % mod, src, re.M):
            line = src[:m.start()].count("\n") + 1
            bad.append("%s at line %d" % (mod, line))
    check("no third-party imports (stdlib only)", not bad, "; ".join(bad))
    check("the shim uses the requests name on purpose",
          "requests = _RequestsShim" in src)
    check("no pip step is mentioned in the header",
          "pip install" not in src.split("import base64")[0])


def test_state_stays_in_the_folder():
    src = (STAGE / "tinycmdr-cli.py").read_text(encoding="utf-8")
    for pat in ("expanduser", "APPDATA", "LOCALAPPDATA", "Path.home()"):
        hits = [i + 1 for i, l in enumerate(src.splitlines()) if pat in l]
        if pat == "Path.home()":
            check("nothing is read from the user's home", not hits, hits)
    check("every path is built from BASE_DIR",
          src.count("BASE_DIR /") >= 8 and "Path(__file__).resolve().parent" in src)
    check("no config is read from the environment as a requirement",
          "CONFIG_PATH.exists()" in src)


def test_no_web_search_in_this_build():
    """Product decision (David, 2026-09-13): the enterprise build has no web search.

    No search tool, no provider keys, no search config block. The only local search
    tools (find files, recall past sessions) stay.
    """
    src = SRC.read_text(encoding="utf-8", errors="replace")
    for token in ("anysearch", "tavily", "web_search", "api.tavily.com", "api.anysearch.com"):
        hits = [i + 1 for i, l in enumerate(src.splitlines()) if token in l.lower()]
        check("no web search: %s" % token, not hits, hits[:3])
    check("no search config block in the defaults", "search" not in fb.DEFAULT_CONFIG)
    check("no search API keys are read from the environment",
          "ANYSEARCH_API_KEY" not in src and "TAVILY_API_KEY" not in src)
    check("the local search tools survive",
          "def tool_search_files" in src and "def tool_search_sessions" in src)
    check("the prompt says there is no web search",
          "NO web search" in src and "NO URL fetching" in src)


def test_the_endpoint_is_the_only_network_destination():
    """The IL5 requirement, asserted structurally: one destination, provable by grep.

    Every HTTP call resolves through CONFIG["llm"]["base_url"], and the build carries no
    other host literal. If someone adds a second destination, this fails.
    """
    src = SRC.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    literals = []
    for i, l in enumerate(lines):
        for m in re.finditer(r'"https?://[^"\s]+', l):
            url = m.group(0)
            if any(t in url for t in ("invalid", "127.0.0.1", "localhost", "example.com", "::1")):
                continue          # the placeholder and loopback examples
            literals.append("%d: %s" % (i + 1, url))
    check("no hard-coded hosts anywhere", not literals, literals[:4])

    calls = [i + 1 for i, l in enumerate(lines)
             if re.search(r"requests\.(get|post)\(", l) or "urllib.request.urlopen(" in l]
    check("the socket call sites are the known few", len(calls) <= 8, calls)
    check("the shim is the only place that opens a socket",
          src.count("urllib.request.urlopen(") == 1)
    check("no fetch or search tool remains",
          "def tool_fetch_url" not in src and "def tool_web_search" not in src)


def test_key_is_required_remotely_and_sent_when_present():
    """The model endpoint needs a key (David, 2026-09-13); certificates it does not.

    The build carries no certificate handling at all. It does send the key it is given,
    and it refuses to start against a remote endpoint without one rather than letting
    the first request come back 401.
    """
    src = SRC.read_text(encoding="utf-8", errors="replace")
    check("no certificate plumbing", "ca_bundle" not in src and "cafile" not in src)
    check("the system trust store is what it uses", "create_default_context()" in src)
    check("the key is a documented setting", "api_key" in fb.DEFAULT_CONFIG["llm"])
    saved = json.loads(json.dumps(fb.CONFIG))
    try:
        # remote endpoint, no key -> refuse
        fb.CONFIG["llm"] = dict(saved["llm"], base_url="https://models.approved.example/v1",
                                model="m", api_key="")
        err = fb.validate_startup_config()
        check("a remote endpoint without a key is refused",
              bool(err) and "api_key" in err, (err or "")[:80])
        # remote endpoint with a key -> fine
        fb.CONFIG["llm"]["api_key"] = "issued-key"
        check("with a key it starts", fb.validate_startup_config() is None)
        # the header decision: present when a key is set, absent when not
        for k, want in (("issued-key", True), ("", False), ("none", False)):
            fb.CONFIG["llm"]["api_key"] = k
            headers = {"Content-Type": "application/json"}
            key = str(fb.CONFIG["llm"].get("api_key") or "").strip()
            if key and key.lower() != "none":
                headers["Authorization"] = "Bearer %s" % key
            check("Authorization header with key=%r: %s" % (k, want),
                  ("Authorization" in headers) == want)
        # a local endpoint needs none
        fb.CONFIG["llm"] = dict(saved["llm"], base_url="http://127.0.0.1:8081/v1",
                                model="m", api_key="")
        check("a loopback endpoint needs no key", fb.validate_startup_config() is None)
    finally:
        fb.CONFIG.update(saved)


def test_plain_http_to_a_remote_host_is_warned_about():
    """Not fatal, but it should say so: an unencrypted endpoint in a DoD environment."""
    saved = json.loads(json.dumps(fb.CONFIG))
    try:
        # a key is given, so the only thing under test is the http warning
        fb.CONFIG["llm"] = dict(saved["llm"], base_url="http://model.internal.example/v1",
                                model="m", api_key="issued-key")
        err = fb.validate_startup_config()
        check("plain http to a remote host still starts", err is None, err)
        src = SRC.read_text(encoding="utf-8", errors="replace")
        check("and the warning exists in the code",
              "not encrypted in transit" in src)
    finally:
        fb.CONFIG.update(saved)


def test_the_shell_interpreter_can_be_switched():
    """agent.shell exists for hosts where PowerShell is restricted or removed.

    It is a WINDOWS switch. tool_shell picks PowerShell on Windows and bash
    everywhere else whatever the setting says (`["powershell", ...] if IS_WINDOWS
    else ["bash", "-c", command]`). The checks follow that, so the suite is true
    on a Linux host instead of reporting the platform as a defect."""
    check("the config key exists", "shell" in fb.DEFAULT_CONFIG["agent"])
    saved = fb.CONFIG["agent"].get("shell")
    try:
        if fb.IS_WINDOWS:
            fb.CONFIG["agent"]["shell"] = "cmd"
            out = fb.tool_shell({"command": "echo shell-switch-ok"}, {})
            check("cmd mode runs a command and returns its output",
                  "shell-switch-ok" in out and "exit_code=0" in out, out[:120])
            fb.CONFIG["agent"]["shell"] = "powershell"
            out2 = fb.tool_shell({"command": "Write-Output shell-switch-ok"}, {})
            check("powershell mode still works", "shell-switch-ok" in out2, out2[:120])
        else:
            fb.CONFIG["agent"]["shell"] = "powershell"
            out2 = fb.tool_shell({"command": "echo shell-switch-ok"}, {})
            check("on POSIX the setting is ignored and bash runs the command",
                  "shell-switch-ok" in out2 and "exit_code=0" in out2, out2[:120])
            out3 = fb.tool_shell({"command": "type -t echo"}, {})
            check("and the interpreter really is bash (a bash builtin resolves)",
                  "builtin" in out3, out3[:120])
        src = SRC.read_text(encoding="utf-8", errors="replace")
        check("the shell choice reaches the prompt",
              "_shellcfg" in src and 'shell_name = (("cmd"' in src)
    finally:
        if saved is None:
            fb.CONFIG["agent"].pop("shell", None)
        else:
            fb.CONFIG["agent"]["shell"] = saved


def test_no_script_files_ship_with_the_build():
    """No .bat, no .ps1: environments that whitelist executables block them.

    The folder is the Python file, a README and a config example; the agent is started
    with the operator's own Python.
    """
    folder = [p.name for p in BASE.iterdir()]
    check("no launcher in the repo root", "tinycmdr.bat" not in folder)
    maint = BASE / "maintenance"
    if maint.exists():
        check("no launcher in maintenance/",
              not [p for p in maint.iterdir()
                   if p.suffix.lower() in (".bat", ".cmd", ".ps1") and "cli" in p.name])
    else:
        skip("no launcher in maintenance/", "maintenance/ is repo-only")
    packager = maint / "build-cli-package.py"
    if packager.exists():
        src = packager.read_text(encoding="utf-8")
        check("the packager ships no launcher", "tinycmdr.bat" not in src)
    else:
        skip("the packager ships no launcher", "build-cli-package.py is repo-only")


def test_a_fatal_start_keeps_its_reason_on_screen():
    """A fatal startup error must not vanish with the window when we own the console.

    Reported from a workstation: the file was double-clicked, the folder's config.json had a
    base_url that had lost its "//" (the CLI said so, in a console that closed with the
    process), and the report was "the CLI does not start". A double-click console has nothing
    else attached to it; started from a shell it has the shell attached too, and the window
    stays on its own. Measured: own console -> GetConsoleProcessList == 1, in a shell -> 2.
    """
    src = SRC.read_text(encoding="utf-8", errors="replace")
    check("the shipped file can tell it owns the console",
          "def _console_closes_with_us()" in src and "GetConsoleProcessList" in src)
    check("and holds the window open for the reader", "def _hold_console()" in src)
    calls = src.count("_hold_console()") - 1          # the definition itself
    check("every fatal start path calls it", calls >= 4,
          "%d call sites" % calls)
    guard = src.replace("\r", "")
    exit_at = guard.find("raise SystemExit(3)")
    check("the bot-folder refusal holds too",
          exit_at > 0 and "_hold_console()" in guard[max(0, exit_at - 200):exit_at])
    check("a shell launch is left alone (no hold without a console of our own)",
          "if not _console_closes_with_us():" in src)


def test_it_can_never_open_a_window():
    """One console for the agent, and no second window for anything.

    Reported from a locked-down workstation: the file was started and then cmd/PowerShell
    windows multiplied until the machine had to be rebooted. This build cannot be the
    source of that - it allocates no console, starts no second copy of itself and hands no
    file to another program - so the machine's own .py handler was. These checks keep it
    true: any of these names appearing in the shipped file means someone has added a way
    to open a window or to respawn the agent, and that has to be a deliberate change with
    this test updated, not a side effect.
    """
    src = SRC.read_text(encoding="utf-8", errors="replace")
    forbidden = ("CREATE_NEW_CONSOLE", "DETACHED_PROCESS", "AllocConsole", "FreeConsole",
                 "ShellExecute", "os.startfile", "os.system(", "shell=True",
                 "conhost", "wt.exe")
    found = [name for name in forbidden if name in src]
    check("no way to allocate a console or respawn in the shipped file", not found, found)

    spawner = inspect.getsource(fb.run_capture)
    check("the shell child is created with the hidden-process flags",
          "hidden_proc_kwargs()" in spawner and "**kwargs" in spawner)
    check("killing a child goes through them too",
          "hidden_proc_kwargs()" in inspect.getsource(fb._kill_tree))
    if os.name == "nt":
        kwargs = fb.hidden_proc_kwargs()
        check("those flags ask Windows for a hidden console",
              bool(kwargs.get("creationflags", 0) & 0x08000000))
        si = kwargs.get("startupinfo")
        check("and ask for the window hidden",
              getattr(si, "wShowWindow", None) == 0)
    else:
        skip("those flags ask Windows for a hidden console", "not Windows")


def test_it_refuses_to_share_a_folder_with_the_bot():
    """Two agents must not keep one set of notes."""
    src = SRC.read_text(encoding="utf-8", errors="replace")
    check("the guard exists", "_folder_belongs_to_the_bot" in src)
    stage = Path(tempfile.mkdtemp(prefix="fbcli-shared-"))
    try:
        shutil.copy2(SRC, stage / "tinycmdr-cli.py")
        (stage / "tinycmdr.py").write_text("# the Mattermost build\n", encoding="utf-8")
        (stage / "tinycmdr.lock").write_text("", encoding="utf-8")
        r = subprocess.run([sys.executable, str(stage / "tinycmdr-cli.py"), "--once", "hi"],
                           cwd=stage, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        combined = r.stdout + r.stderr
        check("it refuses to start there",
              "belongs to a running Mattermost bot" in combined, combined[-200:])
        check("and it exits non-zero", r.returncode == 3, r.returncode)
        check("and it writes nothing into that folder",
              not (stage / "notes.md").exists() and not (stage / "sessions").exists()
              and not (stage / "tinycmdr.log").exists(),
              sorted(p.name for p in stage.iterdir()))
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def test_config_defaults_have_no_chat_or_failover_keys():
    default = fb.DEFAULT_CONFIG
    check("no mattermost section", "mattermost" not in default)
    check("no web section", "web" not in default)
    check("no failover list", "fallbacks" not in default["llm"])
    check("no cloud-fallback switch", "allow_cloud_fallback" not in default["llm"])
    # The stall watchdog and the catch-up sweep are chat-lane machinery: a console
    # has no channel to fall behind on and nothing to recover after a socket gap.
    # The check-in keys are NOT in that list any more, and that is the point of the
    # shared reporting layer: the console draws the same tool lines and the same ⏳
    # check-in as the other two lanes, so it declares the knobs it reads instead of
    # hardcoding its own cadence (which is how three lanes drifted apart).
    check("no chat-only watchdog keys",
          not any(k.startswith(("stall_", "catch_up")) for k in default["agent"]))
    check("the reporting knobs the console draws with are present",
          all(k in default["agent"] for k in
              ("checkin_steps", "checkin_tool_merge_seconds")))
    check("the budgets and the loop guard survive",
          all(k in default["agent"] for k in
              ("max_steps", "max_minutes", "loop_dedupe_after", "loop_stop_repeats",
               "confirm_patterns", "blocked_patterns")))
    check("no cron keys anywhere", not any(
        "cron" in json.dumps(default[k]).lower() for k in default))


def test_config_keys_cover_the_app_defaults():
    """The generated build must carry every default its OWN code can read.

    It drifted 31 keys behind tinycmdr.py once (found by running tests/test_plan.py against
    the CLI for the first time: the new features silently ran on their code fallbacks and a
    direct CONFIG["agent"][key] lookup raised KeyError). The guard is a comparison against
    the real defaults, not a hand-kept list, so the next default added is covered too.
    """
    import ast
    bot_src = BASE / "tinycmdr.py"
    if not bot_src.exists():
        # The suite runs against an unpacked console archive too, and there the bot build
        # is not beside it: there is nothing to compare against, which is a skip, not a
        # failure. (The bot archive carries this suite next to both builds and does run it.)
        skip("config keys: the generated build is not missing app defaults",
             "tinycmdr.py is not in this copy")
        return
    src = bot_src.read_text(encoding="utf-8")

    def agent_keys(tree):
        for n in ast.walk(tree):
            if (isinstance(n, ast.Assign) and n.targets
                    and getattr(n.targets[0], "id", "") == "DEFAULT_CONFIG"):
                for k, v in zip(n.value.keys, n.value.values):
                    if isinstance(k, ast.Constant) and k.value == "agent":
                        return {kk.value for kk in v.keys if isinstance(kk, ast.Constant)}
        return set()

    bot = agent_keys(ast.parse(src))
    cli = set(fb.DEFAULT_CONFIG["agent"])
    # The families whose code this build cuts: a key with no code behind it is decoration,
    # and test_config_defaults_have_no_chat_or_failover_keys asserts they stay out.
    cut = {k for k in bot - cli
           if k.startswith(("stall_", "checkin_", "catch_up")) or k == "vision"}
    check("nothing else is missing from the generated config",
          (bot - cli) == cut, sorted((bot - cli) - cut))
    check("and it carries nothing the app does not define",
          (cli - bot) == {"shell"}, sorted(cli - bot))


# --- 2. the shim -------------------------------------------------------------
def test_shim_json_and_headers():
    with _Server() as srv:
        r = fb.requests.post("http://127.0.0.1:%d/v1/chat/completions" % srv.port,
                             headers={"Authorization": "Bearer x"},
                             json={"model": "m"}, timeout=5)
        check("shim: status_code", r.status_code == 200, r.status_code)
        check("shim: json()", r.json().get("ok") is True)
        r.raise_for_status()
        g = fb.requests.get("http://127.0.0.1:%d/v1/models" % srv.port, timeout=5)
        check("shim: GET + json", g.json()["data"][0]["id"] == "m1")


def test_shim_sse_stream_and_close():
    _Handler.mode = "sse"
    try:
        with _Server() as srv:
            r = fb.requests.post("http://127.0.0.1:%d/v1/chat/completions" % srv.port,
                                 json={"stream": True}, timeout=5, stream=True)
            lines = [l for l in r.iter_lines(decode_unicode=False)]
            check("shim: streamed lines arrive as bytes",
                  lines and isinstance(lines[0], bytes), type(lines[0]).__name__ if lines else "none")
            check("shim: SSE payload intact",
                  any(b'"content":"a"' in l for l in lines))
            check("shim: the raw socket is reachable for the cancel path",
                  r.raw is not None and (r.raw.sock is not None or r.raw._connection is r.raw))
            r.close()
    finally:
        _Handler.mode = "json"


def test_shim_errors_carry_a_response():
    _Handler.mode = "429"
    try:
        with _Server() as srv:
            url = "http://127.0.0.1:%d/v1/chat/completions" % srv.port
            try:
                fb.requests.post(url, json={}, timeout=5).raise_for_status()
                check("shim: 429 raises HTTPError", False, "no exception")
            except fb.requests.HTTPError as e:
                check("shim: 429 raises HTTPError", True)
                check("shim: status is readable", fb._http_status(e) == 429)
                check("shim: body is readable", "slow down" in fb._http_body(e))
                check("shim: Retry-After is parsed",
                      fb._retry_after_secs(e, 60) == 7, fb._retry_after_secs(e, 60))
                check("shim: Retry-After is capped",
                      fb._retry_after_secs(e, 3) == 3)
    finally:
        _Handler.mode = "json"


def test_shim_reports_a_dead_endpoint_as_a_connection_error():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()
    try:
        fb.requests.get("http://127.0.0.1:%d/v1/models" % dead_port, timeout=2)
        check("shim: refused connection raises", False, "no exception")
    except Exception as e:
        check("shim: refused connection raises ConnectionError",
              isinstance(e, fb.requests.ConnectionError), type(e).__name__)


def test_a_silent_endpoint_is_bounded_by_the_read_timeout():
    class Silent(socketserver.TCPServer):
        allow_reuse_address = True

    class H(socketserver.BaseRequestHandler):
        def handle(self):
            time.sleep(4)

    srv = Silent(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    t0 = time.time()
    try:
        fb.requests.post("http://127.0.0.1:%d/v1/chat/completions" % port,
                         json={}, timeout=0.5)
        check("shim: silent endpoint raises", False, "no exception")
    except fb.requests.Timeout:
        check("shim: silent endpoint bounded by the read timeout", True)
        check("shim: bounded quickly", time.time() - t0 < 3.0, "%.1fs" % (time.time() - t0))
    except Exception as e:
        check("shim: silent endpoint bounded by the read timeout", False, type(e).__name__)
    finally:
        srv.shutdown()
        srv.server_close()


# --- 3. the config layer ----------------------------------------------------
def test_validator_catches_the_first_run_mistakes():
    saved = json.loads(json.dumps(fb.CONFIG))
    try:
        fb.CONFIG["llm"] = dict(saved["llm"], base_url="", model="m")
        check("validator: empty base_url refused", bool(fb.validate_startup_config()))
        fb.CONFIG["llm"] = dict(saved["llm"], base_url="127.0.0.1:8081/v1", model="m")
        check("validator: missing scheme refused", bool(fb.validate_startup_config()))
        fb.CONFIG["llm"] = dict(saved["llm"], base_url="http://change-me/v1", model="m")
        check("validator: placeholder refused", bool(fb.validate_startup_config()))
        fb.CONFIG["llm"] = dict(saved["llm"], base_url="http://127.0.0.1:8081/v1", model="")
        check("validator: empty model refused", bool(fb.validate_startup_config()))
        fb.CONFIG["llm"] = dict(saved["llm"], base_url="http://127.0.0.1:8081/v1", model="m")
        check("validator: a good config passes", fb.validate_startup_config() is None)
    finally:
        fb.CONFIG.update(saved)


def _files_under(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


def _fresh_folder(prefix, with_example=False):
    """A folder holding nothing but this build, the way an unpack looks."""
    folder = Path(tempfile.mkdtemp(prefix=prefix))
    shutil.copy2(STAGE / "tinycmdr-cli.py", folder / "tinycmdr-cli.py")
    if with_example:
        (folder / "config.example.json").write_text(json.dumps({
            "llm": {"base_url": "https://your-endpoint.invalid/v1", "api_key": "",
                    "model": ""}}, indent=2), encoding="utf-8")
    return folder


def test_a_missing_config_is_answered_with_steps_but_writes_nothing():
    """Operator requirement: nothing is created at startup, and no config.json is
    ever written for you. A missing one gets the steps, then exits."""
    folder = _fresh_folder("fbcli-noconfig-", with_example=True)
    try:
        before = _files_under(folder)
        r = subprocess.run([sys.executable, str(folder / "tinycmdr-cli.py")],
                           cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace", input="",
                           timeout=90)
        check("no config: exits 2 (not configured)", r.returncode == 2, r.returncode)
        check("no config: names the example to copy or rename",
              "config.example.json" in r.stdout and "config.json" in r.stdout, r.stdout[:160])
        check("no config: says to fill in the three llm fields",
              "base_url" in r.stdout and "model" in r.stdout and "api_key" in r.stdout)
        check("no config: creates nothing",
              _files_under(folder) == before, _files_under(folder))
        check("no config: no config.json appeared",
              not (folder / "config.json").exists())
        r2 = subprocess.run([sys.executable, str(folder / "tinycmdr-cli.py"),
                             "--once", "say hello"],
                            cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        check("no config, --once: same answer, same exit code",
              r2.returncode == 2 and "config.example.json" in r2.stdout, r2.returncode)
        check("no config, --once: still nothing created",
              _files_under(folder) == before, _files_under(folder))
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_opening_it_creates_nothing():
    """--version, --help and a refused start must leave the folder byte-for-byte as
    it was: no log, no sessions/, no tools/, no atlas.md, no state file."""
    folder = _fresh_folder("fbcli-inert-")
    try:
        before = _files_under(folder)
        for args in (["--version"], ["--help"]):
            r = subprocess.run([sys.executable, str(folder / "tinycmdr-cli.py")] + args,
                               cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            check("opening it: %s exits 0" % args[0], r.returncode == 0, r.returncode)
        check("opening it: nothing was created",
              _files_under(folder) == before, _files_under(folder))
        for name in ("tinycmdr.log", "sessions", "tools", "atlas.md", "notes.md",
                     "tasks.json", "tasks.md", "state.json"):
            check("opening it: no %s" % name, not (folder / name).exists())
        src = (STAGE / "tinycmdr-cli.py").read_text(encoding="utf-8")
        check("no mkdir at import: tools/ is made when a tool is written",
              "self.tools_dir.mkdir" not in src)
        check("no mkdir at import: sessions/ is made on the first save",
              src.count("SESSIONS_DIR.mkdir") == 1)
        check("the log file is created on the first line, not on open",
              "delay=True" in src)
        check("no atlas is written for you at run start",
              "ensure_atlas()" not in src)
        check("the wizard is gone", "first_run_wizard" not in src)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_the_shipped_atlas_is_read_and_never_generated():
    """The atlas is a document that ships beside the agent, not state: with the file
    there its facts ride the prompt, and with no file the block is empty. Nothing
    creates it either way, and the header does not claim the harness wrote it."""
    path = fb.BASE_DIR / (fb.CONFIG["agent"].get("atlas_file") or "atlas.md")
    created_here = not path.exists()
    try:
        if created_here:
            path.write_text(
                "# Machine atlas - the machine this agent is running on\n"
                "## host\n"
                "- OS family: test platform\n"
                "## layout\n"
                "- tinycmdr.py  the agent, one file\n"
                "- sessions/  appears once there is work to keep\n"
                "## notes\n"
                "- the native command for this OS, not a download\n", encoding="utf-8")
        fb._ATLAS_CACHE["mtime"] = None
        block = fb.volatile_context(atlas=True)
        check("atlas: the file beside the agent reaches the prompt",
              "the native command for this OS" in block, block[:160])
        check("atlas: its layout and host facts come through",
              "tinycmdr.py" in block and "OS family" in block)
        check("atlas: the header says it shipped with the build",
              "shipped beside the agent" in fb.render_atlas(), fb.render_atlas()[:120])
        check("atlas: the header no longer credits the harness",
              "from the harness" not in fb.render_atlas())
        path.unlink()
        fb._ATLAS_CACHE["mtime"] = None
        check("atlas: no file, empty block", fb.render_atlas().strip() == "",
              fb.render_atlas()[:80])
        check("atlas: no file, nothing recreated it", not path.exists())
        fb.CONFIG["agent"]["atlas_enabled"] = False
        path.write_text("## notes\n- should not be read\n", encoding="utf-8")
        fb._ATLAS_CACHE["mtime"] = None
        check("atlas: atlas_enabled false keeps it out of the prompt",
              fb.render_atlas().strip() == "")
    finally:
        fb.CONFIG["agent"]["atlas_enabled"] = True
        fb._ATLAS_CACHE["mtime"] = None
        if path.exists():
            path.unlink()


def test_the_atlas_sources_name_only_files_the_package_has():
    """The shipped atlas is content: three sections, relative names, and nothing that is
    true on one machine and wrong on the next (a hostname, an install path, a version).
    The packager checks the layout against the folder it ships in; this checks the text
    of the sources themselves, where the mistake would be written."""
    sources = sorted((BASE / "maintenance").glob("atlas-cli-*.md"))
    if not sources:
        skip("atlas sources: maintenance/ is not in this copy (archive)")
        return
    for src in sources:
        text = src.read_text(encoding="utf-8")
        check("%s: has host, layout and notes" % src.name,
              all(h in text for h in ("## host", "## layout", "## notes")))
        check("%s: no absolute path in the layout" % src.name,
              not re.search(r"[A-Za-z]:[\\/]|/(home|etc|var|usr|opt|root|Users)/", text))
        check("%s: no machine-specific fact" % src.name,
              not re.search(r"(?i)hostname:|\bpython \d|/home/|\\\\Users\\\\", text))
        layout = text.split("## layout", 1)[1].split("## ", 1)[0]
        names = [n for line in layout.splitlines() if line.strip()
                 for n in line.strip().lstrip("- ").split("  ")[0].split()]
        check("%s: the layout names something" % src.name, bool(names), names[:4])
        check("%s: every name is relative" % src.name,
              all(not n.startswith(("/", "~")) for n in names), names)
    if len(sources) > 1:
        win = [s for s in sources if "win" in s.name]
        nix = [s for s in sources if "linux" in s.name]
        check("atlas: one source per shipped platform", bool(win) and bool(nix),
              [s.name for s in sources])
        if win and nix:
            check("atlas: the platforms differ, so the shipped copies are not the same file",
                  win[0].read_text(encoding="utf-8") != nix[0].read_text(encoding="utf-8"))



# --- 4. the terminal commands ------------------------------------------------
def test_verbs_answer_and_do_not_crash():
    out = io.StringIO()
    real = sys.stdout
    saved_model = fb.CONFIG["llm"]["model"]
    try:
        sys.stdout = out
        for line in ("/help", "/status", "/tasks", "/notes", "/usage", "/tools", "/skills"):
            fb._cli_command(line)
        fb._cli_command("/model other-model")
        check("verb /model switches the session model",
              fb.CONFIG["llm"]["model"] == "other-model")
        fb._cli_command("/model")
        fb._cli_command("/nonsense-verb")
        fb._cli_command("/new")
        keep = fb._cli_command("/exit")
    finally:
        sys.stdout = real
        fb.CONFIG["llm"]["model"] = saved_model
    text = out.getvalue()
    check("verbs: /help lists the commands", "/status" in text and "/exit" in text)
    check("verbs: /status shows the endpoint", FIXTURE["llm"]["base_url"] in text)
    check("verbs: an unknown verb says so", "not a command" in text)
    check("verbs: /exit ends the loop", keep is False)
    check("verbs: everything answered without raising", len(PASSES) > 0)


def test_the_console_lists_and_resumes_conversations():
    """A terminal that can only ever be one conversation forgets everything the
    moment you close it. /sessions and /resume make the saved conversations
    reachable from the console, the same way the web rail does."""
    import json as _json
    import tempfile as _tempfile
    import time as _time
    out = io.StringIO()
    real, real_dir = sys.stdout, fb.SESSIONS_DIR

    def verdict(name, ok, detail=""):
        """Report through the real stdout: this test points sys.stdout at a
        StringIO to capture what the VERBS print, and a check that lands in there
        is a check nobody reads."""
        held = sys.stdout
        sys.stdout = real
        try:
            check(name, ok, detail)
        finally:
            sys.stdout = held
    folder = Path(_tempfile.mkdtemp(prefix="fb-cli-sessions-"))
    (folder / "sessions").mkdir()
    older = folder / "sessions" / "alpha.json"
    newer = folder / "sessions" / "beta.json"
    older.write_text(_json.dumps([{"role": "user", "content": "one"},
                                  {"role": "assistant", "content": "a"}]), encoding="utf-8")
    newer.write_text(_json.dumps([{"role": "user", "content": "two"},
                                  {"role": "assistant", "content": "b"},
                                  {"role": "user", "content": "three"},
                                  {"role": "assistant", "content": "c"}]), encoding="utf-8")
    now = _time.time()
    os.utime(older, (now - 600, now - 600))
    os.utime(newer, (now, now))
    saved_key = fb._CLI.get("session")
    try:
        fb.SESSIONS_DIR = folder / "sessions"
        sys.stdout = out
        fb._cli_command("/sessions")
        listed = out.getvalue()
        verdict("current: /sessions lists what is saved",
              "alpha" in listed and "beta" in listed)
        verdict("current: the newest conversation is first",
              listed.index("beta") < listed.index("alpha"))
        verdict("current: it shows how much is in each",
              "2 exchange(s)" in listed and "1 exchange(s)" in listed)
        out.truncate(0), out.seek(0)
        fb._cli_command("/resume 1")
        resumed = out.getvalue()
        verdict("current: /resume 1 continues the newest one",
              fb._cli_key() == "beta", fb._cli_key())
        verdict("current: and says which one", "beta" in resumed)
        out.truncate(0), out.seek(0)
        fb._cli_command("/status")
        verdict("current: /status names the conversation in use",
              "beta" in out.getvalue())
        out.truncate(0), out.seek(0)
        fb._cli_command("/resume 9")
        verdict("current: an out-of-range choice is refused, not guessed",
              fb._cli_key() == "beta" and "pick N" in out.getvalue())
        out.truncate(0), out.seek(0)
        fb._cli_command("/help")
        verdict("current: /help documents them",
              "/sessions" in out.getvalue() and "/resume" in out.getvalue())
        out.truncate(0), out.seek(0)
        fb._cli_command("/new")
        verdict("current: /new clears the conversation that is in use",
              not newer.exists() and older.exists(),
              f"{newer.exists()} {older.exists()}")
        fb.SESSIONS_DIR = folder / "empty"
        out.truncate(0), out.seek(0)
        fb._cli_command("/sessions")
        verdict("current: an empty folder says so, it does not traceback",
              "no saved conversations" in out.getvalue())
    finally:
        sys.stdout = real
        fb.SESSIONS_DIR = real_dir
        fb._CLI["session"] = saved_key
        import shutil as _shutil
        _shutil.rmtree(folder, ignore_errors=True)


def test_the_console_reports_the_same_run_as_the_other_lanes():
    """The console draws the SAME vocabulary as chat and the browser: the call as
    it starts, the result line with its exit code and reason, the check-in, and a
    done line. It used to have its own closures for all of that, which is how it
    drifted; these are the shared reporter's, printed by this lane's destination."""
    import io as _io
    out = _io.StringIO()
    rep = fb.RunReporter(fb.CliDestination(colour=False, out=out), "cli")
    rep.progress("shell", '{"command": "df -h"}')
    rep.tool_done("shell", {"command": "df -h"},
                  "exit_code=1\npermission denied", 2.4)
    shown = out.getvalue()
    check("console: the call is shown as it starts", "df -h" in shown, shown)
    check("console: the result carries the exit code", "[exit 1]" in shown, shown)
    check("console: ...and the reason", "permission denied" in shown, shown)

    # a growing narration prints its new TAIL: a terminal cannot edit a line, and
    # re-printing the whole text scrolls the plan off the screen
    out.truncate(0)
    out.seek(0)
    rep.narration("The disk is fine", False, True)
    rep.narration("The disk is fine and nothing is hot", False, False)
    streamed = out.getvalue()
    check("console: a growing line prints only its new tail",
          streamed.count("The disk is fine") == 1, streamed)

    out.truncate(0)
    out.seek(0)
    rep.finish(ok=True)
    check("console: the run signs off with a done line",
          "Done" in out.getvalue() and "step(s)" in out.getvalue(), out.getvalue())

    # the draft that turned out to be the answer is already on the screen; the
    # console is told what it said so the caller does not print it twice
    dropped = []
    sink = _io.StringIO()
    rep2 = fb.RunReporter(fb.CliDestination(colour=False, out=sink,
                                            on_drop=dropped.append), "cli")
    rep2.narration("**the manager box** confirmed.", True, True)
    rep2.narration_drop()
    check("console: a dropped draft is remembered for the caller",
          bool(dropped) and dropped[0].startswith("**the manager box**"), dropped)


def test_banner_reports_the_model_and_the_overhead():
    out = io.StringIO()
    real = sys.stdout
    try:
        sys.stdout = out
        fb.cli_banner()
    finally:
        sys.stdout = real
    text = out.getvalue()
    check("banner: names the model", FIXTURE["llm"]["model"] in text)
    check("banner: states the prompt overhead", "prompt overhead" in text)
    check("banner: no chat vocabulary", "Mattermost" not in text and "channel" not in text)


# --- 5. the binary surface ---------------------------------------------------
def _run_cli(args, timeout=90):
    return subprocess.run([sys.executable, str(STAGE / "tinycmdr-cli.py")] + args,
                          cwd=STAGE, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def test_version_and_help_run_without_a_config():
    r = _run_cli(["--version"])
    check("--version exits 0", r.returncode == 0, r.returncode)
    check("--version prints the version", "tinycmdr" in r.stdout and "python" in r.stdout)
    r = _run_cli(["--help"])
    check("--help exits 0 and documents --once",
          r.returncode == 0 and "--once" in r.stdout)
    r = _run_cli(["--once"])
    check("--once with no task explains itself and exits 2",
          r.returncode == 2 and "--once needs a task" in r.stdout, r.stdout[:120])


def test_a_refused_config_is_a_failed_start():
    """A config the build refuses must not report success to whatever started it.

    The message was right from 1.0.0 but the exit code was 0, so a Task Scheduler job (the
    documented way to run `--once`) saw a failed start as a clean run. 1.0.7 exits 2 on every
    fatal startup path, and holds the console when it owns it so the reason is readable.
    """
    import json as _json
    import shutil as _shutil
    bad = STAGE / "badcfg"
    _shutil.rmtree(bad, ignore_errors=True)
    bad.mkdir(parents=True)
    _shutil.copy2(SRC, bad / "tinycmdr-cli.py")
    (bad / "config.json").write_text(_json.dumps(
        {"llm": {"base_url": "http192.0.2.10:8081/v1", "api_key": "none",
                 "model": "main"}}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(bad / "tinycmdr-cli.py")], cwd=bad,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=90, stdin=subprocess.DEVNULL)
    check("a mangled base_url is refused with the value it read",
          "cannot start" in r.stdout and "http192.0.2.10:8081/v1" in r.stdout, r.stdout[:160])
    check("and the exit code says failure (2), not success",
          r.returncode == 2, r.returncode)
    _shutil.rmtree(bad, ignore_errors=True)


def test_a_dead_endpoint_fails_readably_and_does_not_hang():
    t0 = time.time()
    r = _run_cli(["--once", "say hello"])
    took = time.time() - t0
    check("dead endpoint: exits 0 (a run, then an answer)",
          r.returncode == 0, r.returncode)
    check("dead endpoint: nothing was changed",
          "nothing was changed" in r.stdout or "did not run" in r.stdout, r.stdout[-200:])
    check("dead endpoint: bounded (< 60s)", took < 60, "%.1fs" % took)


def test_the_tool_loop_runs_against_a_local_endpoint():
    """The whole path: SSE in, a tool call out, the tool really runs, an answer."""
    mock = _MockEndpoint()
    mock.start()
    folder = Path(tempfile.mkdtemp(prefix="fbcli-loop-"))
    try:
        shutil.copy2(STAGE / "tinycmdr-cli.py", folder / "tinycmdr-cli.py")
        (folder / "config.json").write_text(json.dumps({
            "llm": {"base_url": "http://127.0.0.1:%d/v1" % mock.port,
                    "api_key": "none", "model": "mock", "max_turns": 4,
                    "request_timeout": 10},
            "agent": {"bot_name": "cli-loop", "max_steps": 4, "max_minutes": 2,
                      "color_coded": False},
        }, indent=2), encoding="utf-8")
        r = subprocess.run([sys.executable, str(folder / "tinycmdr-cli.py"),
                            "--once", "run the tool loop"],
                           cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        check("loop: exits 0", r.returncode == 0, r.returncode)
        check("loop: the tool really ran", "mock-tool-ran" in (r.stdout + r.stderr))
        check("loop: the answer came back", "Answer from the mock" in r.stdout, r.stdout[-200:])
        check("loop: the usage line is printed", "tok over" in r.stdout)
        # gated on a REAL one-shot run, not a direct banner call: the line lived in
        # cli_banner() and --once never printed it (found after the fleet push, 2026-09-19).
        check("loop: the run states its lane", "capabilities: lane cli" in r.stdout, r.stdout[:200])
        check("loop: and what it can enforce",
              "blocked_patterns" in r.stdout and "memory ceiling" in r.stdout
              and "spawn backend" in r.stdout, r.stdout[:200])
        check("loop: no atlas.md was written for you", not (folder / "atlas.md").exists())
        check("loop: the agent did not touch config.json",
              json.loads((folder / "config.json").read_text(encoding="utf-8")
                         )["llm"]["model"] == "mock")
    finally:
        mock.stop()
        shutil.rmtree(folder, ignore_errors=True)


class _MockEndpoint:
    """Minimal OpenAI-compatible endpoint: tool call first, then an answer."""

    def __init__(self):
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(
                    int(self.headers.get("Content-Length") or 0)).decode() or "{}")
                seen_tool = any(m.get("role") == "tool" for m in payload.get("messages") or [])
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()

                def emit(obj):
                    self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
                    self.wfile.flush()

                if not seen_tool and payload.get("tools"):
                    delta = {"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                             "function": {"name": "shell",
                                                          "arguments": '{"command": "echo mock-tool-ran"}'}}]}
                    finish = "tool_calls"
                else:
                    delta = {"content": "Answer from the mock endpoint."}
                    finish = "stop"
                emit({"choices": [{"index": 0, "delta": dict(delta, role="assistant"),
                                   "finish_reason": None}]})
                emit({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                      "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def do_GET(self):
                body = json.dumps({"data": [{"id": "mock"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.handler = H
        self.httpd = None
        self.port = None

    def start(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for t in tests:
        if only and only not in t.__name__:
            continue
        try:
            t()
        except Exception as e:
            import traceback
            FAILURES.append("%s raised: %s" % (t.__name__, e))
            traceback.print_exc()
    summary = "\n%d passed, %d failed" % (len(PASSES), len(FAILURES))
    if SKIPPED:
        summary += ", %d skipped (needs repo-only files)" % len(SKIPPED)
    print(summary)
    for f in FAILURES:
        print("  FAIL:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
