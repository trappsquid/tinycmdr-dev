"""ask_user refactor: exactly ONE row and ONE wait per question.

The first shape had the door AND ask_operator each wait on their own event, so an answer
set the door's event while the run waited on its private one - every answer deadlocked
until the timeout. Now: the door OPENS the question (posts it, returns a row), the
harness WAITS, and the door keeps a copy sharing that same event.

Anchors are kept free of backslash-newline continuations: inside a Python literal a
trailing backslash eats the newline, so an anchor written that way silently matches
nothing. Blocks that do contain one are replaced by index between two markers instead.
"""
import os, py_compile, sys

FP = r"C:/Users/<user>\tinycmdr\tinycmdr.py"
raw = open(FP, "rb").read()
NL = b"\r\n" if raw.count(b"\r\n") == raw.count(b"\n") else b"\n"
t = raw.replace(b"\r\n", b"\n").decode("utf-8")
orig = len(t)


def rep(old, new, label, count=1):
    global t
    n = t.count(old)
    if n != count:
        print("REFUSING: anchor %s matched %d, expected %d" % (label, n, count))
        sys.exit(2)
    t = t.replace(old, new)
    print("  ok %s" % label)


def splice(start_marker, end_marker, new, label):
    """Replace [start_marker .. end_marker) with new - for blocks holding a backslash."""
    global t
    a = t.find(start_marker)
    if a < 0:
        print("REFUSING: start marker not found for %s" % label)
        sys.exit(2)
    b = t.find(end_marker, a)
    if b < 0:
        print("REFUSING: end marker not found for %s" % label)
        sys.exit(2)
    t = t[:a] + new + t[b:]
    print("  ok %s (spliced %d chars)" % (label, b - a))


# ---------------------------------------------------------------- 1. _ask_door
NEW_DOOR = '''def _ask_door(session_key, ctx):
    """Who, if anyone, can answer a question asked from this run.

    The run's own door is preferred; there is deliberately no global fallback door,
    because a fallback with nobody behind it is just a stalled run. `post` is the only
    mandatory member: without it the question cannot be delivered at all."""
    del session_key                      # the door carries the identity it needs
    direct = (ctx or {}).get("ask_door")
    door = {"post": None, "label": "this conversation"}
    if isinstance(direct, dict):
        door.update({k: v for k, v in direct.items() if v})
    elif direct is not None:
        # A door may be an OBJECT (the web run) as well as a dict: duck-typed, because
        # the shapes live in different corners of this file, and a dict-only check
        # silently reduced the web UI to "nobody can answer".
        for k in ("opener", "post", "post_done", "close_question", "answer", "label"):
            v = getattr(direct, k, None)
            if v is not None:
                door[k] = v
    if not door.get("post"):
        return None
    return door


'''
splice("def _ask_door(session_key, ctx):", "def ask_operator(session_key, question",
       NEW_DOOR, "_ask_door")

# ------------------------------------------------- 2. ask_operator's middle
NEW_MID = '''    door = door or _ask_door(sk, ctx)
    if not door:
        return "unreadable", ("nothing in this run can reach a human - ask nobody; "
                              "state your assumption and carry on")
    cap = _ask_wait_cap()
    wait = cap if timeout is None else min(max(float(timeout), 5.0), cap)
    opts = [str(o).strip() for o in (options or []) if str(o).strip()][:8]
    label = door.get("label") or "this conversation"
    # The door OPENS the question and the harness does the waiting, so exactly one row
    # and one event exist per question: the door keeps a copy that shares this event,
    # which is how the answer lands on the row the run is waiting on. Two rows was the
    # first shape and it deadlocked every answer until its timeout.
    opener = door.get("opener")
    if callable(opener):
        try:
            row = opener(question, opts, wait, label)
        except Exception as e:
            log.warning("ask_user: could not open the question: %s", e)
            return "unreadable", f"the question could not be posted: {e}"
        if not isinstance(row, dict) or "ev" not in row:
            return "unreadable", (str(row) if row
                                  else "this run has nobody it can ask")
        row.setdefault("answer", None)
    else:
        row = {"ev": threading.Event(), "answer": None, "question": question}
        with _ask_lock(sk):
            _ASK_PENDING[sk] = row
        try:
            door["post"](question, opts, wait, label)
        except Exception as e:
            with _ask_lock(sk):
                _ASK_PENDING.pop(sk, None)
            log.warning("ask_user: could not post the question: %s", e)
            return "unreadable", f"the question could not be delivered: {e}"
    log.info("ask_user[%s]: question open, waiting up to %.0fs", sk, wait)
    deadline = time.time() + wait
    while not row["ev"].is_set():
        left = deadline - time.time()
        if left <= 0:
            break
        row["ev"].wait(min(left, 3.0))
        _cancel = (ctx or {}).get("cancel_event")
        if _cancel is not None and _cancel.is_set():
            break
    answered = row["ev"].is_set() and row["answer"] is not None
    answer = row["answer"]
    with _ask_lock(sk):
        _ASK_PENDING.pop(sk, None)
    _ASK_ANSWERED[sk] = {"answer": answer, "at": time.time()}
    closer = door.get("close_question")
    if callable(closer):
        try:
            closer(answered)
        except Exception:
            pass
'''
splice("    door = door or _ask_door(sk, ctx)\n", "    if answered:\n", NEW_MID,
       "ask_operator wait")

# ------------------------------------------------- 3. dispatcher: open_question
rep("    def answer_question(self, session_key, question, options, timeout):\n"
    '        """Post a question and WAIT for it, on the run\'s thread.\n'
    "\n"
    "        The wait is a threading.Event set by the LISTENER thread: nothing polls, and no\n"
    '        lock is held while waiting. Returns (answered, text).\n'
    '        """\n',
    "    def open_question(self, session_key, question, options, timeout):\n"
    '        """Open a question for this session, post it, and WAIT for the answer.\n'
    "\n"
    "        Runs on the run's thread. The row published here is a copy of the one\n"
    "        ask_operator is waiting on, carrying the SAME event - which is how the\n"
    "        LISTENER thread releases that waiter without either side knowing about the\n"
    "        other. Returns (answered, text).\n"
    '        """\n',
    "dispatcher open_question header")

rep("        self.pending_asks[ch if ch else session_key] = row\n",
    "        self.pending_asks[ch if ch else session_key] = row\n"
    "        _ASK_PENDING[session_key] = dict(row)   # shares the event above\n",
    "dispatcher publishes the shared row")

rep('        return answered, row["answer"]\n\n    def reply_ask',
    '        return answered, row["answer"]\n'
    "\n"
    "    def door_post(self, channel_id, question, options, wait, label=None):\n"
    '        """The door\'s second stage. open_question already posted the question, so\n'
    "        this only touches activity: asking IS progress, and without the touch the\n"
    "        stall watchdog abandons a run that is doing exactly what it should.\"\"\"\n"
    "        self._touch(channel_id)\n"
    "        return True\n"
    "\n"
    "    def reply_ask",
    "dispatcher door_post")

rep('        return {"label": self._ask_label(channel_id),\n'
    '                "post": lambda q, opts, wait, label: self.answer_question(\n'
    '                    session_key, q, opts, wait),\n'
    '                "answer": lambda q, opts, wait, label: True}',
    '        return {"label": self._ask_label(channel_id),\n'
    '                "opener": lambda q, opts, wait, label: self.open_question(\n'
    '                    session_key, q, opts, wait),\n'
    '                "post": lambda q, opts, wait, label: self.door_post(\n'
    '                    channel_id, q, opts, wait, label)}',
    "dispatcher factory")

# --------------------------------------------------------- 4. the web door
rep('''    label = "the web page"

    def post(self, question, options, wait, label=None):
        """The ask_user door's post: the question belongs in the run's own stream, where
        the page is already rendering lines."""
        self.add("ask", "❓ " + question
                 + (" — " + " · ".join(options) if options else ""))
        return True

    def answer(self, question, options=None, wait=None, label=None):
        """The door's 'someone is here to answer' half; the wait itself is answer_question."""
        return self.answer_question(question, options, wait)

    def answer_question(self, question, options=None, timeout=None):
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
''',
'''    label = "the web page"

    def opener(self, question, options, wait, label=None):
        """The web door's row. It is published so that an /api/steer POST can be told
        apart from a plain steering line: only a live row turns that POST into an
        answer. The WAIT belongs to ask_operator, which this event shares."""
        row = {"ev": threading.Event(), "answer": None, "question": question,
               "options": list(options or [])}
        with self.lock:
            self.asked = row
        return row

    def close_question(self, answered=False):
        with self.lock:
            self.asked = None

    def post(self, question, options, wait, label=None):
        """The question belongs in the run's own stream, where the page is already
        drawing lines, and nowhere else: this door has no second surface."""
        self.add("ask", "❓ " + question
                 + (" — " + " · ".join(options) if options else ""))
        return True
''',
    "web door")

# ----------------------------------------------------- 5. the console door
NEW_CLI = '''    def cli_ask_door(label="the console"):
        """ask_user in a terminal: the person is at the keyboard, so the opener reads
        the answer itself and hands the run an already-answered row."""
        def opener(q, opts, wait, lbl):
            tail = ("\\n  options: " + " · ".join(opts)) if opts else ""
            print(f"\\n  ❓ {q}{tail}\\n  answer (Enter to let the agent decide, "
                  f"{int(wait)}s max): ", end="", flush=True)
            row = {"ev": threading.Event(), "answer": None, "question": q}
            try:
                a = input().strip()
            except (EOFError, KeyboardInterrupt):
                print()
                a = ""
            if a:
                row["answer"] = a
            row["ev"].set()
            return row
        return {"label": label, "opener": opener,
                "post": lambda q, opts, wait, lbl: True}

'''
splice('    def cli_ask_door(label="the console"):',
       '    def usage_line():', NEW_CLI, "cli door")

# --------------------------------------------------- 6. the scheduled door
rep("    def _ask_in_channel(self, channel_id, key, question, options, timeout):",
    "    def _open_in_channel(self, channel_id, key, question, options, timeout):",
    "sched opener rename")

rep("        self.pending_asks[key] = row\n",
    "        self.pending_asks[key] = row\n"
    "        _ASK_PENDING[key] = dict(row)   # shares the event ask_operator waits on\n",
    "sched publishes the shared row")

rep('''                                          "opener": lambda q, opts, w, l:''',
    '''                                          "opener": lambda q, opts, w, l:''',
    "noop") if False else None

rep('''                               ask_door=({"label": "answer in this channel",
                                          "post": lambda q, opts, w, l:
                                              SCHEDULER._ask_in_channel(
                                                  job.get("channel_id"), key,
                                                  q, opts, w),
                                          "answer": lambda q, opts, w, l: True}
                                         if rep else None))''',
    '''                               ask_door=({"label": "answer in this channel",
                                          "opener": lambda q, opts, w, l:
                                              SCHEDULER._open_in_channel(
                                                  job.get("channel_id"), key,
                                                  q, opts, w),
                                          "post": lambda q, opts, w, l: True}
                                         if rep else None))''',
    "sched door")

data = t.replace("\n", "\r\n" if NL == b"\r\n" else "\n").encode("utf-8")
open(FP + ".tmp-ask4", "wb").write(data)
os.replace(FP + ".tmp-ask4", FP)
print("tinycmdr.py %d -> %d chars" % (orig, len(t)))
py_compile.compile(FP, doraise=True)
print("compiles OK")
for s in ("def _ask_door", "def open_question", "def door_post", "def opener",
          "def close_question", "def _open_in_channel", "def cli_ask_door",
          "def answer_question", "_ASK_PENDING[session_key] = dict(row)",
          "_ASK_PENDING[key] = dict(row)"):
    print("  %-34s x%d" % (s, t.count(s)))
