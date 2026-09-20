"""Apply the ask_user feature to tinycmdr.py. Idempotent-checked; nothing without anchors."""
import os, re, py_compile, sys

BASE = r"C:/Users/<user>\tinycmdr"
FP = os.path.join(BASE, "tinycmdr.py")
BP = os.path.join(BASE, "maintenance", "_askuser_block.txt")

blk = open(BP, encoding="utf-8").read()
parts = re.split(r"^# ===SPLICE:(MAIN|CORE|END)===\s*$", blk, flags=re.M)
assert parts[1] == "MAIN" and parts[3] == "CORE" and parts[5] == "END", "block markers"
MAIN, CORE = parts[2], parts[4]

def load(path):
    raw = open(path, "rb").read()
    nl = b"\r\n" if raw.count(b"\r\n") == raw.count(b"\n") else b"\n"
    return raw.replace(b"\r\n", b"\n").decode("utf-8"), nl, raw

def save(path, text, nl):
    data = text.replace("\n", "\r\n" if nl == b"\r\n" else "\n").encode("utf-8")
    tmp = path + ".tmp-askuser"
    open(tmp, "wb").write(data)
    os.replace(tmp, path)

t, nl, raw = load(FP)
orig = len(t)
assert "ask_operator" not in t, "already applied - refusing to double-apply"

EDITS = []
def edit(label, anchor, new, count=1):
    EDITS.append((label, anchor, new, count))

edit("MAIN block",
     "\n# --------------------------------------------------------------------------\n# The agent loop\n",
     "\n" + MAIN.rstrip("\n") + "\n# --------------------------------------------------------------------------\n# The agent loop\n")

edit("ask_user schema",
     '    "skill": {\n        "fn": tool_skill,',
     CORE.rstrip("\n") + '\n    "skill": {\n        "fn": tool_skill,')

edit("late answer delivery",
     '                        late = steer_cb() if steer_cb else []\n                        if late:',
     r'''                        late = steer_cb() if steer_cb else []
                        # A question answered while a run was parked on it is delivered
                        # here too: the answer IS the operator's next instruction. The
                        # name can be absent when the enterprise cut drops the ask_user
                        # block, so this is a guard rather than a top-level import.
                        try:
                            _ask_ans = _ASK_ANSWERED.pop(session_key or "", None)
                        except NameError:
                            _ask_ans = None
                        if _ask_ans and _ask_ans.get("answer"):
                            late = list(late) + [
                                ("operator (answer to your question)",
                                 str(_ask_ans["answer"]))]
                        if late:''')

edit("run() signature",
     '            interim_cb=None, progress_done_cb=None, steer_cb=None,\n            narration_cb=None, narration_drop_cb=None, say_cb=None):',
     '''            interim_cb=None, progress_done_cb=None, steer_cb=None,
            narration_cb=None, narration_drop_cb=None, say_cb=None,
            ask_door=None):''')

edit("ctx ask_door",
     '                   # Tools are revealed per session, so the context has to carry it.\n                   "session_key": session_key}',
     '''                   # Tools are revealed per session, so the context has to carry it.
                   "session_key": session_key,
                   # ask_user's door: how this run reaches a human, and how it waits.
                   # Only a door that owns a blocking wait sets it (the Mattermost
                   # dispatcher, the web run, the CLI prompt).
                   "ask_door": ask_door}''')

edit("web answer_question",
     '    def take_steer(self):\n        with self.lock:\n            out, self.steer = self.steer, []\n        return [("web", m) for m in out]',
     r'''    def answer_question(self, question, options=None, timeout=None):
        """The web run's ask_user door: the answer arrives as an /api/steer POST.

        The row is published rather than queued, because that POST has to be told apart
        from a plain steering line: only a live row turns it into an answer.
        """
        row = {"ev": threading.Event(), "answer": None, "question": question}
        with self.lock:
            self.asked = row
        try:
            return row["ev"].wait(float(timeout or 300))
        finally:
            with self.lock:
                self.asked = None

    def take_steer(self):
        with self.lock:
            out, self.steer = self.steer, []
        return [("web", m) for m in out]''')

edit("web run door",
     '        answer = AGENT.run(run.session_key, text,\n                           say_cb=lambda t: run.add("say", t),',
     '''        answer = AGENT.run(run.session_key, text,
                           ask_door=run,
                           say_cb=lambda t: run.add("say", t),''')

edit("web steer answers",
     '                if run.done:\n                    self._json({"error": "run finished"}, 409)\n                    return\n                with run.lock:\n                    run.steer.append(text)',
     '''                if run.done:
                    self._json({"error": "run finished"}, 409)
                    return
                # A question is open on this run: this POST is its ANSWER, not a steering
                # line. It is read here and handed to the parked run through the row,
                # because only the run's own thread can unblock itself.
                with run.lock:
                    _row = getattr(run, "asked", None)
                if _row is not None:
                    _row["answer"] = text
                    _row["ev"].set()
                    run.add("you", text + "   (answer)")
                    log.info("web run %s: question answered", run.id)
                    self._json({"answered": True})
                    return
                with run.lock:
                    run.steer.append(text)''')

edit("pending_asks",
     '        self.queued_notice = {}    # channel_id -> post id of the queued notice',
     '''        self.queued_notice = {}    # channel_id -> post id of the queued notice
        # channel_id (or session key) -> the ask_user row a run is parked on. It lives
        # here, not in the run, because the ANSWER arrives on the listener thread and
        # has to be readable without touching the run.
        self.pending_asks = {}''')

edit("answer_question/reply_ask",
     '    def _touch(self, channel_id):\n        """Mark output in a channel as progress for the stall watchdog."""\n        a = self.active.get(channel_id)\n        if a:\n            a["last"] = time.time()',
     r'''    def _touch(self, channel_id):
        """Mark output in a channel as progress for the stall watchdog."""
        a = self.active.get(channel_id)
        if a:
            a["last"] = time.time()

    def _ask_label(self, channel_id):
        """How to answer: a DM has no @mention, a channel needs one."""
        if channel_id and (self._is_dm(channel_id) or not self.bot_username):
            return "reply here"
        return ("reply here or @mention me (in a channel Mattermost only hands me "
                "messages addressed to me)")

    def answer_question(self, session_key, question, options, timeout):
        """Post a question and WAIT for it, on the run's thread.

        The wait is a threading.Event set by the LISTENER thread: nothing polls, and no
        lock is held while waiting. Returns (answered, text).
        """
        row = {"ev": threading.Event(), "answer": None, "question": question,
               "options": list(options or [])}
        ch = self._session_channel.get(session_key)
        self.pending_asks[ch if ch else session_key] = row
        opts = ""
        if row["options"]:
            opts = "\nOptions: " + " · ".join(f"`{o}`" for o in row["options"])
        try:
            self._post(ch, None,
                       f"❓ **{question}**{opts}\n_({self._ask_label(ch)} — or "
                       f"`/stop` to cancel this run. Waiting up to "
                       f"{max(1, int((timeout or 300) / 60))} min; if nothing arrives "
                       f"I carry on with my own judgment.)_")
        except Exception as e:
            log.warning("ask_user: could not post the question: %s", e)
        try:
            answered = row["ev"].wait(float(timeout or 300))
        finally:
            self.pending_asks.pop(ch if ch else session_key, None)
        return answered, row["answer"]

    def reply_ask(self, session_key, text, sender=""):
        """Hand a message to the run parked on a question, and say so.

        True means it was claimed as the answer (so the caller stops). The row is
        CLEARED here, which is what makes a second message land as ordinary steering
        instead of overwriting the answer the run is already acting on.
        """
        ch = self._session_channel.get(session_key)
        row = self.pending_asks.pop(ch if ch else session_key, None)
        if row is None or row["ev"].is_set():
            return False
        row["answer"] = text
        row["ev"].set()
        log.info("ask_user: answer handed to the parked run (%s)", sender or "?")
        self._post(ch, None, "✅ Got it — carrying on.")
        return True''')

edit("ask_door_factory",
     '        return ask\n\n    # -- attachments ---',
     r'''        return ask

    def ask_door_factory(self, channel_id, session_key):
        """A door bound to the RUN's session, not the channel.

        Every run has its own session key while the channel is the operator's door, and
        /stop flags the channel's cancel events - so the door records which session this
        channel is running, in order to release the right waiter.
        """
        if session_key:
            self._session_channel[session_key] = channel_id
        return {"label": self._ask_label(channel_id),
                "post": lambda q, opts, wait, label: self.answer_question(
                    session_key, q, opts, wait),
                "answer": lambda q, opts, wait, label: True}

    # -- attachments ---''')

edit("main ask_door",
     '                               cancel_event=cancel,\n                               steer_cb=lambda: self._take_steering(channel_id),\n                               channel_id=channel_id)',
     '''                               cancel_event=cancel,
                               steer_cb=lambda: self._take_steering(channel_id),
                               ask_door=self.ask_door_factory(channel_id,
                                                              session_key),
                               channel_id=channel_id)''')

edit("bg ask_door",
     '                                       cancel_event=cancel,\n                                       channel_id=channel_id)',
     '''                                       ask_door=self.ask_door_factory(
                                           channel_id, bg_key),
                                       cancel_event=cancel,
                                       channel_id=channel_id)''')

edit("enqueue claim",
     '        low = text.strip().lower()\n        if low == "/stop":',
     '''        low = text.strip().lower()
        # A run parked on an ask_user question takes the next message in its channel as
        # the ANSWER, on the listener thread, while that run stays blocked. Ahead of
        # /stop so an answer that reads like a command is still the answer; `/stop`
        # itself cancels instead, so a parked run is never unreleasable.
        if low != "/stop" and not low.startswith("/"):
            for _sk in [s for s, r in list(self.pending_asks.items())
                        if not r["ev"].is_set()]:
                _ch = self._session_channel.get(_sk)
                if (_ch and _ch == channel_id) or (not _ch and _sk == channel_id):
                    if self.reply_ask(_sk, text, sender):
                        return
        if low == "/stop":''')

edit("stop releases ask",
     '        events = self.cancel_events.get(channel_id, set())\n        live = [e for e in events if not e.is_set()]\n        busy = self._channel_busy(channel_id)\n        self._drain(channel_id)',
     '''        events = self.cancel_events.get(channel_id, set())
        live = [e for e in events if not e.is_set()]
        busy = self._channel_busy(channel_id)
        # A run parked on an ask_user question must be RELEASED, not merely flagged: its
        # cancel check runs only once the wait returns, so without this a /stop sat out
        # the whole window - the "my stop was ignored" complaint.
        for _sk in list(self.pending_asks):
            _r = self.pending_asks.get(_sk)
            if _r and not _r["ev"].is_set():
                _r["answer"] = None
                _r["ev"].set()
        self._drain(channel_id)''')

edit("status line",
     '        f"· dedupe {a.get(\'loop_dedupe_after\')} · loop-stop "\n        f"{a.get(\'loop_stop_repeats\')}")',
     '''        f"· dedupe {a.get('loop_dedupe_after')} · loop-stop "
        f"{a.get('loop_stop_repeats')}")
    _ask_lim = (f"on, wait {int(float(a.get('ask_user_wait_seconds') or 300))}s"
                if a.get("ask_user") else "off (agent.ask_user)")
    try:
        _p = CONFIG.get("_dispatcher")
        _pending = _p(_ask_user_pending(key)) if _p else None
    except Exception:
        _pending = None
    lines.append(f"ask_user: {_ask_lim}"
                 + (f" · WAITING on: {_pending['question'][:90]}" if _pending else ""))''')

edit("scheduler field",
     "    dispatcher = None   # set by run_bot so scheduled jobs can report progress",
     '''    dispatcher = None   # set by run_bot so scheduled jobs can report progress
    # sched-<name> -> the row a job is parked on. Only a job WITH a reporting channel
    # gets a door: a job fired with none has nobody to ask and must not wait.
    pending_asks = {}''')

edit("sched ask_door",
     "                               say_cb=rep.say if rep else None)",
     '''                               say_cb=rep.say if rep else None,
                               ask_door=({"label": "answer in this channel",
                                          "post": lambda q, opts, w, l:
                                              SCHEDULER._ask_in_channel(
                                                  job.get("channel_id"), key,
                                                  q, opts, w),
                                          "answer": lambda q, opts, w, l: True}
                                         if rep else None))''')

edit("sched _ask_in_channel",
     "    def _fire(self, name, job):",
     r'''    def _ask_in_channel(self, channel_id, key, question, options, timeout):
        """A scheduled run's question: posted to its channel, answered by any message in
        it. Returns after the wait either way - a job never waits for ever."""
        row = {"ev": threading.Event(), "answer": None, "question": question,
               "options": list(options or [])}
        self.pending_asks[key] = row
        opts = ("\nOptions: " + " · ".join(f"`{o}`" for o in options)
                if options else "")
        try:
            report(channel_id, f"❓ **{question}**{opts}\n_(Answer here — or it waits "
                               f"{max(1, int((timeout or 300) / 60))} min and carries "
                               f"on with its own judgment.)_")
        except Exception as e:
            log.warning("scheduled ask_user: could not post the question: %s", e)
        try:
            row["ev"].wait(float(timeout or 300))
        finally:
            self.pending_asks.pop(key, None)
        return True

    def _fire(self, name, job):''')

edit("cli_ask_door",
     '    def confirm(command):\n        try:\n            return input(f"⚠️ confirm: {command[:200]}\\n(yes/no) "\n                         ).strip().lower() in ("yes", "y")\n        except (EOFError, KeyboardInterrupt):\n            return False',
     r'''    def confirm(command):
        try:
            return input(f"⚠️ confirm: {command[:200]}\n(yes/no) "
                         ).strip().lower() in ("yes", "y")
        except (EOFError, KeyboardInterrupt):
            return False

    def cli_ask_door(label="the console"):
        """ask_user in a terminal: the prompt IS the question, so post says nothing."""
        def ask(q, opts, wait, lbl):
            tail = ("\n  options: " + " · ".join(opts)) if opts else ""
            print(f"\n  ❓ {q}{tail}\n  answer (Enter to skip, "
                  f"{int(wait)}s max): ", end="", flush=True)
            return True

        def answer(q, opts, wait, lbl):
            try:
                return input().strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return ""
        return {"label": label, "post": ask, "answer": answer}''')

edit("cli run sites", "confirm_cb=confirm)",
     'confirm_cb=confirm, ask_door=cli_ask_door("cli"))', count=2)

edit("prompt rule",
     "- Never ask for confirmation. If something is ambiguous, pick the most reasonable option, state your assumption, and proceed. Only stop if you are truly blocked.",
     "- You are autonomous, but not omniscient: when a decision is genuinely the operator's — an irreversible change, two paths their preference settles, a target or credential you cannot choose between — use `ask_user` and wait for the answer. Everything else: pick the most reasonable option, state the assumption in one line, and proceed. Never use `ask_user` to ask permission to do the job you were given, and never for something you can find out with a tool. If it is off, times out, or nobody is there, you get that in the result: apply your judgment, say what you assumed, and carry on.")

bad = [(l, t.count(a), c) for l, a, n, c in EDITS if t.count(a) != c]
if bad:
    print("REFUSING - anchors did not match:")
    for l, n, c in bad:
        print("  %-24s got %d want %d" % (l, n, c))
    sys.exit(2)
for label, anchor, new, count in EDITS:
    t = t.replace(anchor, new)
    print("  applied %-24s" % label)

save(FP, t, nl)
print("tinycmdr.py: %d -> %d chars (+%d), newline %s" % (orig, len(t), len(t) - orig, nl))
py_compile.compile(FP, doraise=True)
print("compiles OK")
for sym in ("def tool_ask_user", "def ask_operator", "def ask_door_factory",
            "def answer_question", "def reply_ask", "def _ask_in_channel",
            "def cli_ask_door", '"ask_user": {', "ask_door=self.ask_door_factory",
            "_ASK_ANSWERED", "ask_user: {_ask_lim}"):
    print("  %-34s x%d" % (sym, t.count(sym)))
