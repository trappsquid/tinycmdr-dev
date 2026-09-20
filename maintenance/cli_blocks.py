"""New code blocks for the chatless CLI build of tinycmdr."""

NEW_HEADER = r'''tinycmdr.py — a small autonomous ops agent that lives in a terminal.

One file, no installer, no service, no chat gateway. Keep the folder wherever you
like, make sure Python is installed, run it (double-click this file, or
"python tinycmdr.py"), and type what you want done. It runs shell commands, reads
and edits files, reads a URL you hand it, writes its own notes and tools, and keeps
the conversation in ./sessions. There is no web search and no third-party service
involved: the only network destination is the model endpoint in config.json.

Dependencies: none. Standard library only, so there is no pip step and nothing to
install besides Python itself.
Config: copy config.example.json to config.json and fill in the llm fields.
        Nothing is ever written for you, and opening this file creates nothing:
        the log, notes, sessions and tools appear only when there is work to keep.
Custom tools:  drop .py files into ./tools/ (it writes its own there too)
Run:           python tinycmdr.py
One-shot task: python tinycmdr.py --once "why is plex crashing"
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
        "request_timeout": 600,
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
        "history_exchanges": 10,
        "tool_output_max_chars": 6000,
        "fetch_max_chars": 12000,
        "shell_timeout": 180,
        # Which interpreter the shell tool uses on Windows: "powershell" or "cmd".
        # cmd is for hosts where PowerShell is restricted or removed.
        "shell": "powershell",
        "notes_max_chars": 4000,      # newest notes carried in the system prompt
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
            r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/(\s|$|\*)",
            r"rm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/(\s|$|\*)",
            r"\bmkfs\b", r"\bdd\s+.*of=/dev/", r":\(\)\s*\{",
            r"\bshutdown\b", r"\bpoweroff\b", r"\breboot\b",
            r">\s*/dev/sd", r"\bformat\s+[a-zA-Z]:",
            r"remove-item\b[^|;]*-recurse[^|;]*-force",
            r"\b(stop|restart)-computer\b",
            r"\bformat-volume\b", r"\bclear-disk\b", r"\binitialize-disk\b",
            r"\bcipher\s+/w\b", r"\bvssadmin\s+delete\s+shadows\b",
            r"\brd\s+/s\b", r"\brmdir\s+/s\b", r"\bdel\s+/[a-z]*[sq]",
            r"-encodedcommand\b",
        ],
    },
}'''

ONE_ENDPOINT = r'''        # One endpoint, by design. config.json names it and everything else is
        # inherited from the server, so there is no failover list to route across
        # and no path where a failure quietly re-sends the conversation elsewhere.
        want = str(model).strip().lower()
        ordered = [primary]'''

NEW_CLI = r'''_CLI = {"colour": False, "stop": None, "inbox": None, "steer": None,
        "leave": False, "stream": "", "streamed": "", "streamed_answer": ""}


def _console_utf8():
    """Windows consoles are not UTF-8 by default, and the banner is."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _ansi_enable():
    if os.name == "nt":
        try:
            import ctypes
            kern = ctypes.windll.kernel32
            kern.SetConsoleMode(kern.GetStdHandle(-11), 7)
        except Exception:
            return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _paint(text, code):
    return "\033[%sm%s\033[0m" % (code, text) if _CLI["colour"] else text


def green(text):
    """The agent talking (narration, answers, confirmations)."""
    return _paint(text, "32")


def amber(text):
    """A tool call: what it is about to do."""
    return _paint(text, "33")


def red(text):
    """A failure, and only a failure."""
    return _paint(text, "31")


def dim(text):
    return _paint(text, "2")


HELP_TEXT = ("\n"
             "  /help            this list\n"
             "  /new             forget the conversation so far and start clean\n"
             "  /model [NAME]    show the model in use, or switch to NAME\n"
             "  /sessions        the conversations saved in this folder\n"
             "  /resume N        continue one of them in this window\n"
             "  /status          version, endpoint, context use, notes, tasks, skills\n"
             "  /tasks           the task ledger for this machine\n"
             "  /notes           what it has written down about this machine\n"
             "  /skills          the runbooks it can load\n"
             "  /tools           every tool it has right now\n"
             "  /usage           tokens and time for the last run\n"
             "  /stop            cancel the run in flight (Ctrl-C does the same)\n"
             "  /exit            quit (Ctrl-D does the same)\n"
             "\n"
             "  Anything else is a request:  check why the backup job failed\n"
             "  Type at any time, including while it is working: a request is sent in\n"
             "  at the next step, /stop and the read-only verbs act immediately, and the\n"
             "  verbs that change state (/new, /model) run when the current run ends.\n"
             "  Ctrl-C stops the run in flight; a second Ctrl-C quits.\n")


def cli_banner():
    print("tinycmdr %s - %s at %s" % (VERSION, green(CONFIG["llm"]["model"]),
                                      CONFIG["llm"]["base_url"]))
    print(dim("folder %s" % BASE_DIR))
    static = est_tokens(build_system_prompt() + json.dumps(REGISTRY.openai_schemas()))
    live = est_tokens(volatile_context())
    print(dim("prompt overhead ~%s tokens (static %s: system prompt + %d tool schemas, "
              "cache-stable; live %s: notes + task ledger, sent trailing)"
              % (fmt_tokens(static + live), fmt_tokens(static),
                 len(REGISTRY.openai_schemas()), fmt_tokens(live))))
    print(dim("type /help for the commands, /exit to quit\n"))


def _cli_key():
    """Which conversation this console is in. 'cli' until /resume says otherwise."""
    return _CLI.get("session") or "cli"


def _cli_session_rows():
    """Saved conversations, newest first: key, exchanges, when it was last used.

    A session key IS a filename here (the agent writes sessions/<key>.json), so
    this reads what is on disk rather than keeping a second list that could
    disagree with it.
    """
    rows = []
    try:
        files = list(SESSIONS_DIR.glob("*.json"))
    except OSError:
        files = []
    for f in files:
        if f.name.startswith("export-"):
            continue        # /save exports, not conversations
        try:
            hist = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue        # a damaged session is not a reason to fail here
        if not isinstance(hist, list):
            continue
        rows.append({"key": f.stem, "messages": len(hist),
                     "exchanges": sum(1 for m in hist if isinstance(m, dict)
                                      and m.get("role") == "user"),
                     "mtime": f.stat().st_mtime})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows


def _cli_sessions():
    rows = _cli_session_rows()
    if not rows:
        print(dim("  no saved conversations yet"))
        return rows
    cur = _cli_key()
    for i, r in enumerate(rows, 1):
        print("  %s %2d. %-30s %3d exchange(s)  %s"
              % ("*" if r["key"] == cur else " ", i, r["key"][:30],
                 r["exchanges"],
                 time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"]))))
    print(dim("  /resume N continues one of them here; * is the one in use"))
    return rows


def _cli_resume(rest):
    rows = _cli_session_rows()
    if not rows:
        print(dim("  no saved conversations yet"))
        return
    try:
        n = int((rest or "").split()[0])
    except (IndexError, ValueError):
        n = 0
    if not 1 <= n <= len(rows):
        print(dim("  /resume N - pick N from /sessions"))
        return
    key = rows[n - 1]["key"]
    _CLI["session"] = key
    s = AGENT.stats(key)
    print(green("  now in '%s' - %d exchange(s), %s"
                % (key, s["exchanges"], fmt_tokens(s["est_tokens"]))))
    print(dim("  /new clears it; /sessions lists the others"))


def _cli_usage_line():
    u = AGENT.last_usage.get(_cli_key())
    if not u or not u.get("calls"):
        return
    s = AGENT.stats(_cli_key())
    budget = AGENT._context_budget()
    pct = 100 * s["est_tokens"] // max(1, budget)
    print(dim("  %s - %s step(s) in %ss - context ~%s/%s (%d%%)"
              % (fmt_usage(u), u["steps"], int(u["secs"]), fmt_tokens(s["est_tokens"]),
                 fmt_tokens(budget), pct)))


def _cli_notes():
    if not NOTES_FILE.exists():
        print(dim("  nothing written down yet"))
        return
    body = NOTES_FILE.read_text(encoding="utf-8", errors="replace").strip()
    if not body:
        print(dim("  nothing written down yet"))
        return
    print(dim("  %s (%d chars)" % (NOTES_FILE, len(body))))
    for line in body.splitlines()[-40:]:
        print("  " + line)


def _cli_tasks():
    t = load_tasks()
    items = t.get("items") or []
    if not items:
        print(dim("  the ledger is empty"))
        return
    for i in items:
        mark = TASK_MARKS.get(i.get("status"), " ")
        line = "  [%s] #%s %s" % (mark, i.get("id"), i.get("desc", ""))
        if i.get("note"):
            line += " - %s" % i["note"]
        print(line)


def _cli_command(text):
    """Handle one /verb. True = keep the loop, False = quit."""
    verb, _, rest = text.partition(" ")
    verb = verb.lower()
    rest = rest.strip()
    if verb == "/stop":
        print(dim("  nothing is running"))
        return True
    if verb in ("/exit", "/quit", "/bye"):
        return False
    if verb in ("/help", "/?"):
        print(HELP_TEXT)
        return True
    if verb in ("/new", "/reset"):
        AGENT.reset(_cli_key())
        print(green("  (context cleared, this machine's notes and ledger stay)"))
        return True
    if verb == "/model":
        if not rest:
            print("  model %s at %s" % (green(CONFIG["llm"]["model"]), CONFIG["llm"]["base_url"]))
            print(dim("  /model <name> switches it for this session"))
            return True
        CONFIG["llm"]["model"] = rest
        print("  model is now %s (this session)" % green(rest))
        return True
    if verb in ("/sessions", "/conversations"):
        _cli_sessions()
        return True
    if verb == "/resume":
        _cli_resume(rest)
        return True
    if verb == "/status":
        u = AGENT.last_usage.get(_cli_key()) or {}
        s = AGENT.stats(_cli_key())
        budget = AGENT._context_budget()
        t = load_tasks()
        items = t.get("items") or []
        opened = [i for i in items if str(i.get("status", "")).lower() == "open"]
        notes = 0
        if NOTES_FILE.exists():
            notes = len(NOTES_FILE.read_text(encoding="utf-8", errors="replace"))
        print("  version    %s" % VERSION)
        print("  model      %s" % CONFIG["llm"]["model"])
        print("  endpoint   %s" % CONFIG["llm"]["base_url"])
        print("  python     %s" % platform.python_version())
        print("  folder     %s" % BASE_DIR)
        print("  context    ~%s of %s (%d%%), %d exchange(s) this session"
              % (fmt_tokens(s["est_tokens"]), fmt_tokens(budget),
                 100 * s["est_tokens"] // max(1, budget), s["exchanges"]))
        print("  last run   %s" % (fmt_usage(u) if u.get("calls") else "nothing yet"))
        print("  session    %s (%d exchange(s))" % (_cli_key(), s["exchanges"]))
        print("  notes      %d chars in notes.md" % notes)
        print("  tasks      %d open of %d" % (len(opened), len(items)))
        print("  skills     %d runbooks" % len(skill_index()))
        print("  tools      %d" % len(REGISTRY.openai_schemas()))
        strays = strays_in_config()
        if strays:
            print("  ignored    %s (in config.json, never sent)" % ", ".join(strays))
        return True
    if verb == "/tasks":
        _cli_tasks()
        return True
    if verb == "/notes":
        _cli_notes()
        return True
    if verb in ("/skills", "/skill"):
        sk = skill_index()
        if not sk:
            print(dim("  no runbooks in ./skills"))
        for s in sk:
            print("  %-34s %s" % (s.get("name", "?"), (s.get("desc") or "")[:90]))
        return True
    if verb == "/tools":
        for sch in REGISTRY.openai_schemas():
            fn = sch.get("function") or {}
            print("  %-18s %s" % (fn.get("name", "?"), (fn.get("description") or "")[:88]))
        return True
    if verb == "/usage":
        _cli_usage_line()
        return True
    print(dim("  %s is not a command - /help lists them" % verb))
    return True


FAST_VERBS = ("/stop", "/help", "/?", "/usage", "/exit", "/quit", "/bye",
              "/sessions", "/conversations")


def _cli_while_running(line):
    """Handle one line that arrived WHILE the agent is mid-turn.

    True means it was dealt with here: /stop and the read-only verbs. False means
    it has to wait for the main loop, because a run reads the session history and
    the model id at its start, not during it, so /new and /model cannot mean
    anything until the run that is in flight has finished.

    This runs on the reader thread, so it only prints, sets the stop event, or
    reads state. The run owns the history, the log and the tools.
    """
    verb = line.split()[0].lower()
    if verb == "/stop":
        ev = _CLI.get("stop")
        if ev is not None:
            ev.set()
            print(red("\n  (stopping - the call in flight is being closed)"))
        else:
            print(dim("\n  (nothing is running)"))
        return True
    if verb in ("/exit", "/quit", "/bye"):
        ev = _CLI.get("stop")
        if ev is not None:
            ev.set()
        _CLI["leave"] = True
        print(red("\n  (stopping this run, then quitting)"))
        return True
    if verb in ("/help", "/?"):
        print(HELP_TEXT)
        return True
    if verb == "/usage":
        _cli_usage_line()
        return True
    if verb in ("/sessions", "/conversations"):
        _cli_sessions()
        return True
    return False


def _cli_reader():
    """The one reader for stdin. Every line lands in the inbox.

    One reader, not two: a prompt reading stdin itself while a run is in flight
    would lose whatever was typed at the wrong moment, which is the bug this
    exists to fix. A run in flight is _CLI["stop"] being set.
    """
    while True:
        raw = sys.stdin.readline()
        if raw == "":                       # EOF: Ctrl-D, /exit, or a closed pipe
            _CLI["inbox"].put(None)
            return
        line = raw.rstrip("\r\n").strip()
        if not line:
            continue
        if _CLI.get("stop") is not None:
            if _cli_while_running(line):
                continue
            if line.startswith("/"):
                _CLI["inbox"].put(line)     # a state-changing verb waits its turn
                print(dim("\n  (that verb runs when this turn ends)"))
                continue
            _CLI["steer"].put(line)         # a request, handed in at the boundary
            print(dim("\n  (sending that in when this turn ends - /stop cancels the run)"))
            continue
        _CLI["inbox"].put(line)


def run_cli(once=None):
    global CONFIG
    _console_utf8()
    _CLI["colour"] = bool(CONFIG["agent"].get("color_coded", True)) and os.environ.get("NO_COLOR") is None
    _CLI["colour"] = _CLI["colour"] and _ansi_enable()

    def narration(txt):
        """interim_cb: the prose that arrived WITH tool calls, nothing streamed."""
        line = " ".join(str(txt).split())
        if line:
            print(green("  %s" % line[:400]))

    def narration_stream(txt, final=False, first=False):
        """narration_cb: what the model is saying, as it says it.

        The callback hands over everything written so far, not the delta, up to
        twice a second, so only the new tail is printed. What it prints may turn
        out to be the final answer: narration_drop() then remembers the text and
        the answer is not printed a second time when the run returns.
        """
        text = str(txt or "")
        if first:
            _CLI["stream"] = ""
        shown = _CLI.get("stream") or ""
        if not text.startswith(shown):
            shown = ""                  # the model rewrote its line: start again
        fresh = text[len(shown):]
        if fresh:
            print(green("  " + fresh) if not shown else green(fresh),
                  end="", flush=True)
            _CLI["stream"] = text
        if final:
            print()
            _CLI["streamed"] = text

    def narration_drop():
        """That streamed text WAS the answer, which is printed once, below."""
        _CLI["streamed_answer"] = _CLI.get("streamed") or ""

    def progress(name, args):
        if name == "generating":
            return                      # the stream's own status, not a tool call
        short = args if isinstance(args, str) else json.dumps(args)
        print(amber("  -> %s %s" % (name, short.replace(chr(10), " ")[:160])))

    def progress_done(name, args, output, elapsed):
        if not output:
            return
        first = " ".join(str(output).split())[:120]
        print(dim("     %s in %.1fs: %s" % (name, elapsed or 0.0, first)))

    def say(text):
        """A line from the harness itself (not the model): a run that continued past its
        budget, for instance. The console has no chat to post into, so it prints."""
        try:
            print(amber("  %s" % text))
        except Exception:
            pass

    def confirm(command):
        try:
            ans = input(amber("  confirm: %s\n  (yes/no) " % str(command)[:200]))
            return ans.strip().lower() in ("yes", "y")
        except (EOFError, KeyboardInterrupt):
            print()
            return False

    _CLI["inbox"] = queue.Queue()
    _CLI["steer"] = queue.Queue()
    _CLI["leave"] = False
    threading.Thread(target=_cli_reader, daemon=True).start()

    def steer():
        """Requests typed while the agent is working, handed in at the boundary.

        The same contract as a mid-run correction from chat: it arrives with the
        next turn, and the agent is told it overrides what it was doing.
        """
        out = []
        while True:
            try:
                out.append(("you", _CLI["steer"].get_nowait()))
            except queue.Empty:
                return out

    if not once:
        cli_banner()
        print(dim("  type at any time: a line is sent in at the next step, /stop "
                  "cancels the run,\n  Ctrl-C does the same. Nothing you type is "
                  "lost while it works.\n"))
    print(dim(capability_line("cli")))
    # reported for BOTH entry points. It used to live in cli_banner(), which a
    # one-shot run never reaches, so `--once` - the CLI's most common entry -
    # said nothing about what it could enforce (found after the fleet push).
    if once:
        print(AGENT.run(_cli_key(), once, progress_cb=progress,
                        say_cb=say, progress_done_cb=progress_done,
                        narration_cb=narration_stream,
                        narration_drop_cb=narration_drop, interim_cb=narration,
                        confirm_cb=confirm))
        _cli_usage_line()
        return
    while True:
        if _CLI["leave"]:
            return
        print(green("you> "), end="", flush=True)
        try:
            text = _CLI["inbox"].get()
        except KeyboardInterrupt:
            print()
            return
        if text is None:                    # stdin closed: Ctrl-D
            print()
            return
        text = text.strip()
        if not text:
            continue
        if text.startswith("/"):
            if not _cli_command(text):
                return
            continue
        if text.lower() in ("reset", "exit", "quit"):
            if text.lower() == "reset":
                AGENT.reset(_cli_key())
                print(green("  (context cleared)"))
                continue
            return
        cancel = threading.Event()
        _CLI["stop"] = cancel
        _CLI["stream"] = ""
        _CLI["streamed"] = ""
        _CLI["streamed_answer"] = ""
        answer = ""
        try:
            answer = AGENT.run(_cli_key(), text, progress_cb=progress,
                               say_cb=say, progress_done_cb=progress_done,
                               narration_cb=narration_stream,
                               narration_drop_cb=narration_drop, interim_cb=narration,
                               confirm_cb=confirm, cancel_event=cancel,
                               steer_cb=steer)
        except KeyboardInterrupt:
            cancel.set()
            print(red("\n  (stopped)"))
            continue
        except OperatorStop as e:
            print(red("\n  (stopped: %s)" % e))
            continue
        except Exception as e:
            print(red("\n  run failed: %s: %s" % (type(e).__name__, e)))
        finally:
            _CLI["stop"] = None
        while not _CLI["steer"].empty():    # typed too late for that run
            _CLI["inbox"].put(_CLI["steer"].get())
        if _CLI["leave"]:
            return
        if answer:
            shown = (_CLI.pop("streamed_answer", "") or "").strip()
            body = answer
            if shown and answer.startswith(shown):
                body = answer[len(shown):]  # only what came after the streamed text
            elif shown and shown.startswith(answer.strip()):
                body = ""                   # already on screen in full
            if body.strip():
                print("\n" + body)
        _cli_usage_line()
        print()


def _cli_sigint(signum, frame):
    """First Ctrl-C asks the run to stop; a second one gets out of the way."""
    ev = _CLI.get("stop")
    if ev is not None and not ev.is_set():
        ev.set()
        print(red("\n  (stopping - the call in flight is being closed)"))
        return
    raise KeyboardInterrupt
'''

NEW_VALIDATOR = r'''def missing_config_text():
    """The steps from "no config.json" to a running agent. Writes nothing."""
    name = "config.example.json"
    me = os.path.basename(sys.argv[0]) or "tinycmdr.py"
    return "\n".join([
        "tinycmdr: there is no config.json in this folder yet.",
        "",
        "This build never writes one - nothing is created or checked at startup - so",
        "this is a one-time copy and edit by hand:",
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
