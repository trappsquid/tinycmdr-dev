"""New code blocks for the chatless CLI build of tinycmdr."""

NEW_HEADER = r'''tinycmdr-cli.py — the same agent as tinycmdr.py, built for a terminal.

One file, no installer, no service, no chat gateway. Keep the folder wherever you
like, make sure Python is installed, run it (double-click this file, or
"python tinycmdr-cli.py"), and type what you want done. It runs shell commands, reads
and edits files, reads a URL you hand it, writes its own notes and tools, and keeps
the conversation in ./sessions. There is no web search and no third-party service
involved: the only network destination is the model endpoint in config.json.
A card per call, result and answer, with a boxed banner: that is the screen when a
terminal is there to draw on.

Installed the harness? The installer puts this file beside tinycmdr.py, and this
build then reads that config.json, that .env and the same sessions and notes as the
bot and the page: one folder, one set of files, whichever door you use.

Dependencies: none required. This build runs on the standard library alone, so there
is no pip step and nothing to install besides Python. Two optional libraries turn the
console into the card UI (rich + prompt_toolkit): the installer brings them in, and
without them every line prints plainly, exactly as it does when the output is a pipe
or you set tinycmdr_PLAIN=1.
Config: config.json next to this file (config.example.json is the reference, and the
        installer's own copy is already filled in). Nothing is ever written for you,
        and opening this file creates nothing: the log, notes, sessions and tools
        appear only when there is work to keep.
Custom tools:  drop .py files into ./tools/ (it writes its own there too)
Run:           python tinycmdr-cli.py
One-shot task: python tinycmdr-cli.py --once "why is plex crashing"
'''

HTTP_SHIM = r'''# --------------------------------------------------------------------------
# HTTP: the standard library only
# --------------------------------------------------------------------------
# The name `requests` is kept deliberately. Every call site and both hermetic
# suites reference `fb.requests.post` and `fb.requests.HTTPError`, so this shim
# presents exactly that surface (post, get, HTTPError carrying .response, and a
# streamed response that supports iter_lines() and a real close()) on top of
# urllib. That is what removes the last third-party dependency: the file runs on
# a stock Python install with no pip step at all.


def _ssl_context():
    """The TLS context for the model endpoint: the system trust store.

    Authentication and certificates are the environment's business here, so there is
    nothing to configure: if the endpoint's issuer is in the system store, it works.
    """
    return ssl.create_default_context()


class _CIDict(dict):
    """Case-insensitive header lookup (Retry-After arrives in any casing)."""

    def __init__(self, items=()):
        super().__init__((str(k).lower(), v) for k, v in items)

    def get(self, key, default=None):
        return super().get(str(key).lower(), default)

    def __getitem__(self, key):
        return super().__getitem__(str(key).lower())

    def __contains__(self, key):
        return super().__contains__(str(key).lower())


class _ShimHTTPError(Exception):
    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


def _sock_of(raw):
    """The socket behind an http.client response, however this Python nests it."""
    for getter in (lambda: raw.fp.raw._sock,
                   lambda: raw.fp.raw,
                   lambda: raw._sock):
        try:
            cand = getter()
        except Exception:
            continue
        if cand is not None and hasattr(cand, "shutdown"):
            return cand
    return None


class _ShimRaw:
    """Enough of urllib3's response object for the cancel path in _stream_chat.

    That code shuts the socket down, which is what makes a local llama.cpp stop
    generating, and it reaches the socket through resp.raw. Exposing the same
    shape keeps that behaviour exactly as it was.
    """

    def __init__(self, raw):
        self._raw = raw
        self.sock = _sock_of(raw) if raw is not None else None
        self._connection = self

    def close(self):
        try:
            if self._raw is not None:
                self._raw.close()
        except Exception:
            pass


class _ShimResponse:
    def __init__(self, raw, url, status, headers, body=b"", stream=False):
        self._raw_obj = raw
        self.url = url
        self.status_code = status
        self.headers = _CIDict(headers)
        self.raw = _ShimRaw(raw) if stream else None
        self._body = body

    @property
    def text(self):
        return (self._body or b"").decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text or "{}")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _ShimHTTPError("HTTP %s from %s" % (self.status_code, self.url), self)

    def iter_lines(self, decode_unicode=False):
        if self._raw_obj is None:
            return
        try:
            for raw in self._raw_obj:
                if decode_unicode:
                    yield raw.decode("utf-8", "replace").rstrip("\r\n")
                else:
                    yield raw.rstrip(b"\r\n")
        except Exception:
            return

    def close(self):
        try:
            if self._raw_obj is not None:
                self._raw_obj.close()
        except Exception:
            pass


class _RequestsShim:
    """The two verbs this file uses, plus the exception type it catches."""

    HTTPError = _ShimHTTPError

    @staticmethod
    def _go(method, url, headers=None, payload=None, timeout=None, stream=False):
        hdrs = {"User-Agent": "tinycmdr", "Accept-Encoding": "identity"}
        for k, v in (headers or {}).items():
            hdrs[k] = v
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout or None,
                                          context=_ssl_context())
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = e.read()
            except Exception:
                pass
            raise _ShimHTTPError(
                "HTTP %s from %s" % (e.code, url),
                _ShimResponse(None, url, e.code,
                              e.headers.items() if e.headers else [], body))
        except urllib.error.URLError as e:
            raise InfraError("cannot reach %s: %s" % (url, getattr(e, "reason", e)))
        except (socket.timeout, TimeoutError) as e:
            raise InfraError("no response from %s within %ss: %s" % (url, timeout, e))
        status = getattr(resp, "status", None) or resp.getcode()
        head = resp.headers.items() if resp.headers else []
        if stream:
            return _ShimResponse(resp, url, status, head, b"", stream=True)
        try:
            body = resp.read()
        finally:
            try:
                resp.close()
            except Exception:
                pass
        return _ShimResponse(None, url, status, head, body)

    @staticmethod
    def post(url, headers=None, json=None, timeout=None, stream=False):  # noqa: A002
        return _RequestsShim._go("POST", url, headers, json, timeout, stream)

    @staticmethod
    def get(url, headers=None, timeout=None):
        return _RequestsShim._go("GET", url, headers, None, timeout, False)


requests = _RequestsShim


'''

NEW_CONFIG = r'''{
    "llm": {
        # One endpoint. Point it at anything OpenAI-compatible: a local llama.cpp
        # or vLLM server, or a hosted provider. There is no failover list and no
        # cloud-fallback gate in this build, so a request either reaches this
        # endpoint or fails loudly. Sampling is deliberately NOT configured here:
        # it is inherited from the server, and apply_sampling() strips any stray
        # sampling key so nothing can quietly send one.
        # THE only network destination this agent talks to. Everything else it
        # needs is on the machine it runs on: no search provider, no telemetry, no
        # update check, no package install.
        "base_url": "https://your-endpoint.invalid/v1",
        # The key the endpoint authenticates with. Required for a remote endpoint;
        # a local or loopback one needs none, so this may stay empty there.
        "api_key": "",
        "model": "",
        # Model-call turns in one task, before the harness forces a report. Kept above
        # max_steps so the tool budget is what binds, not the turn counter.
        "max_turns": 100,
        # The enterprise model carries a 1M-token window, so the prompt budget is set
        # high and compaction almost never fires. It is a budget rather than the window
        # itself: a runaway session is capped here instead of being sent, and if the
        # server's own limit is hit anyway the build shrinks the context and retries.
        # Point this at the window the endpoint in use actually has (a 128K provider
        # wants ~100000; a local model keeps the small original value).
        "max_context_tokens": 500000,
        # Cap on generated tokens per call. Local servers default to unlimited,
        # so one call can generate for many minutes on a slow model. Thinking
        # models spend this budget on reasoning BEFORE the answer, so it has to
        # leave room for both.
        "max_tokens": 16384,
        "final_max_tokens": 8192,     # forced wrap-up call at the budget limit
        "max_tokens_ceiling": 65536,  # one-shot retry cap when cut off mid-think
        "request_timeout": 1200,
        # Hard wall-clock bound = request_timeout + request_grace. The timeout
        # bounds INACTIVITY, not total time: an endpoint that trickles a byte
        # every few seconds keeps a connection alive forever.
        "request_grace": 30,
        "retry_after_max": 60,  # honour a 429 Retry-After header, capped here
        "stream": True,         # SSE: the first token is visible while it
                                # generates, and a cancel really closes the socket
        "stream_idle_seconds": 120,  # a stream this quiet is wedged
        "no_think": False,      # True for qwen3-style models that answer empty
    },
    "agent": {
        "bot_name": socket.gethostname(),
        "history_exchanges": 20,
        "tool_output_max_chars": 10000,
        "fetch_max_chars": 12000,
        "shell_timeout": 300,
        # Which interpreter the shell tool uses on Windows: "powershell" or "cmd".
        # cmd is for hosts where PowerShell is restricted or removed.
        "shell": "powershell",
        "notes_max_chars": 8000,      # newest notes carried in the system prompt
        "notes_max_note_chars": 1200,  # cap on ONE remember call, at write time
        "notes_keep_entries": 60,     # entries kept in notes.md before ageing out
        "notes_archive_days": 45,     # older than this -> notes-archive.md
        "tasks_max_open": 15,         # refuse new tasks past this many open ones
        "tasks_done_keep": 3,         # finished tasks still shown in the prompt
        # Loop guard hard stop: the same tool call with the same result this many
        # times is a spin, not work. A weak local model will happily re-run the
        # same command while you watch it happen.
        "loop_stop_repeats": 6,
        # An exact repeat (same tool, same args, same output) is refused after
        # this many real executions: it cannot produce new information, and for a
        # mutating command re-running it is harmful. 0 disables the refusal.
        "loop_dedupe_after": 2,
        # Hard cap on tool calls per task. 40 was arbitrary and cut real troubleshooting
        # sessions short; the fleet bots run 100. Raise it for debugging work.
        "max_steps": 250,
        # Wall-clock cap per task; a summary is forced at it. 10 minutes is fine for a
        # question and wrong for an investigation, which is what users bring to this build.
        "max_minutes": 75,
        "progress_updates": True,
        "color_coded": True,    # green narration, amber tools, red failures
        "subagent_model": "",   # model for delegate_task sub-agents; empty = inherit
        "show_usage": True,     # token/time footer after each run
        "confirm_patterns": [],  # commands matching these need a 'yes' reply
        "blocked_patterns": [
            "rm\\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\\s+/(?![A-Za-z0-9_./~-])",
            "rm\\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\\s+/(?![A-Za-z0-9_./~-])",
            "\\bmkfs\\b",
            "\\bdd\\s+.*of=/dev/",
            ":\\(\\)\\s*\\{",
            "\\bshutdown\\b",
            "\\bpoweroff\\b",
            "\\breboot\\b",
            ">\\s*/dev/sd",
            "\\bformat\\s+[a-zA-Z]:",
            "remove-item\\b[^|;]*-recurse[^|;]*-force",
            "\\b(stop|restart)-computer\\b",
            "\\bformat-volume\\b",
            "\\bclear-disk\\b",
            "\\binitialize-disk\\b",
            "\\bcipher\\s+/w\\b",
            "\\bvssadmin\\s+delete\\s+shadows\\b",
            "\\brd\\s+/s\\b",
            "\\brmdir\\s+/s\\b",
            "\\bdel\\s+/[a-z]*[sq]",
            "-encodedcommand\\b",
        ],
    },
}'''

ONE_ENDPOINT = r'''        # One endpoint, by design. config.json names it and everything else is
        # inherited from the server, so there is no failover list to route across
        # and no path where a failure quietly re-sends the conversation elsewhere.
        want = str(model).strip().lower()
        ordered = [primary]'''


NEW_VALIDATOR = r'''def missing_config_text():
    """The steps from "no config.json" to a running agent. Writes nothing."""
    name = "config.example.json"
    me = os.path.basename(sys.argv[0]) or "tinycmdr.py"
    return "\n".join([
        "tinycmdr: there is no config.json in this folder yet.",
        "",
        "Installed already? Run the copy the installer put beside tinycmdr.py - this",
        "build reads that config.json, that .env and the same sessions and notes as",
        "the bot and the page, so every door sees one set of files.",
        "",
        "On its own instead? This build never writes a config.json (nothing is created",
        "or checked at startup), so it is a one-time copy and edit by hand:",
        "",
        "  1. copy the example that sits beside this file, or just rename it:",
        "",
        "       Windows         copy %s config.json" % name,
        "       Linux / macOS   cp %s config.json" % name,
        "",
        "  2. open config.json and fill in the three fields under \"llm\":",
        "",
        "       base_url   the endpoint you are approved to reach (OpenAI-compatible,",
        "                  usually ends in /v1)",
        "       model      the model id that endpoint serves",
        "       api_key    the key that service issued you (leave empty for a local one)",
        "",
        "  3. start it again:  python %s" % me,
        "",
        "%s lists the settings worth knowing; every other one already has a" % name,
        "default inside this file. llm.base_url is the only destination it ever talks to.",
    ])


def missing_config_note():
    """One extra line when the example is not beside this file either."""
    name = "config.example.json"
    if (BASE_DIR / name).exists():
        return ""
    return ("\n(%s is not beside this file, so write config.json by hand: it is just\n"
            " {\"llm\": {\"base_url\": \"...\", \"model\": \"...\", \"api_key\": \"...\"}}.)"
            % name)


def validate_startup_config():
    """Catch the first-run mistakes before they surface as a traceback.

    Returns an error string, or None. This build has one thing to get right: the
    endpoint. Everything else has a working default.
    """
    if CONFIG_ERROR:
        return CONFIG_ERROR
    llm = CONFIG.get("llm") or {}
    url = str(llm.get("base_url") or "").strip()
    if not url:
        return "llm.base_url is empty in config.json."
    if not re.match(r"^https?://", url, re.I):
        return ("llm.base_url must start with http:// or https:// (it reads %r).\n"
                "Example: http://127.0.0.1:8081/v1" % url)
    if any(t in url.lower() for t in ("example.com", ".invalid", "your-endpoint", "change-me")):
        return ("llm.base_url still reads %r, which is a placeholder.\n"
                "Point it at your endpoint, for example http://127.0.0.1:8081/v1" % url)
    if not str(llm.get("model") or "").strip():
        return "llm.model is empty in config.json."
    key = str(llm.get("api_key") or "").strip()
    if not _is_local_url(url) and (not key or key.lower() == "none"):
        return ("llm.api_key is empty, and %s is a remote endpoint: it will refuse an "
                "unauthenticated request.\nPut the key this model service issued you in "
                "config.json." % url)
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    if url.lower().startswith("http://") and host not in ("127.0.0.1", "localhost", "::1"):
        # Not fatal: a locked-down network may serve plain http internally. But an
        # unencrypted endpoint in a DoD environment is worth saying out loud. Printed,
        # not logged: a startup line must not be the reason a log file appears.
        print("note: llm.base_url is plain http to %s - the conversation and the tool "
              "output it carries are not encrypted in transit" % host)
    return None

'''

NEW_MAIN = r'''def main():
    global CONFIG
    _console_utf8()
    try:
        signal.signal(signal.SIGINT, _cli_sigint)
    except Exception:
        pass
    if "--version" in sys.argv or "-V" in sys.argv:
        print("tinycmdr %s (python %s, %s)"
              % (VERSION, platform.python_version(), BASE_DIR))
        return
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print("  --once \"<task>\"   run one task, print the answer, exit")
        print("  --version        print the version and where it is running from")
        return
    # Startup is inert by design: nothing is created, nothing is checked, and no
    # config.json is ever written for you. A missing one is answered with the steps
    # to make it, and nothing in this folder changes.
    if not CONFIG_PATH.exists():
        print(missing_config_text() + missing_config_note())
        _hold_console()
        raise SystemExit(2)
    if "--once" in sys.argv:
        idx = sys.argv.index("--once")
        task = " ".join(sys.argv[idx + 1:]).strip()
        if not task:
            print("--once needs a task, e.g. --once \"why is plex crashing\"")
            _hold_console()
            raise SystemExit(2)
        err = validate_startup_config()
        if err:
            print("cannot start: %s" % err)
            _hold_console()
            raise SystemExit(2)
        run_cli(once=task)
        return
    err = validate_startup_config()
    if err:
        print("cannot start: %s" % err)
        print("(edit %s and start again)" % CONFIG_PATH)
        _hold_console()
        raise SystemExit(2)
    if not is_elevated():
        print()
        print("  Note: this console is not elevated. Commands that need administrator rights")
        print("  (some event logs, root WMI classes, service and driver changes) will fail with")
        print("  Access is denied. Relaunch from a terminal started with 'Run as administrator'")
        print("  if you need them. Everything else works as it is.")
    run_cli()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
    except Exception as exc:                     # noqa: BLE001 - reported, not swallowed
        import traceback
        traceback.print_exc()
        print("\n*** tinycmdr stopped: %s: %s ***" % (type(exc).__name__, exc))
        print("the details are also in %s" % (BASE_DIR / "tinycmdr.log"))
        try:
            input("press Enter to close this window...")
        except (EOFError, OSError):
            pass
'''
