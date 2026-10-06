"""The Telegram lane, graded without a network.

    python tests/test_telegram.py

Everything the lane promises has to hold against a fake Bot API: the HTML escaping
that survives a model's own output, the 4096 split that never truncates, one growing
message instead of a wall of notifications, the answer as its own message, the gate
that ignores strangers, and the two ways a question gets answered (a button and a
typed line), both landing in the same slot the reporter reads.
"""
import atexit
import tempfile
import importlib.util
import os
import sys
import shutil
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_tg_under_test", SRC)
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_tg_under_test"] = fb
spec.loader.exec_module(fb)

# The lane persists state beside the module (GLOBAL_STATE_FILE, the lane record and the
# event log), and this suite imports the TREE's build - so without this every run wrote
# state.json and sessions/*.events.jsonl into the checkout (the gate's leak report named
# them). hermetic redirects the module's own path constants into a temp dir.
_TMP = Path(tempfile.mkdtemp(prefix="fbtg-"))
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
sys.path.insert(0, str(BASE / "tests"))
import hermetic                                                          # noqa: E402

hermetic.redirect_repo_files(fb, _TMP)

PASSES = []
FAILS = []

if not hasattr(fb, "tg_escape"):
    # No Telegram lane in this build: it is a bot lane and this build has none.
    print("no Telegram lane in this build - nothing to grade here")
    sys.exit(0)


def check(name, cond, detail=""):
    (PASSES if cond else FAILS).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))


class FakeClient:
    """The Bot API, in memory: it records what a real one would have been asked."""

    def __init__(self):
        self.messages = []
        self.edits = []
        self.typing_calls = 0
        self.callbacks = []
        self._id = 100

    def send(self, chat_id, text, buttons=None, reply_to=None):
        self._id += 1
        self.messages.append({"chat": chat_id, "text": text, "buttons": buttons,
                              "reply_to": reply_to, "id": self._id})
        return [{"message_id": self._id}]

    def edit(self, chat_id, message_id, text, buttons=None):
        self.edits.append({"id": message_id, "text": text})
        return {"message_id": message_id}

    def typing(self, chat_id):
        self.typing_calls += 1

    def answer_callback(self, callback_id, text=""):
        self.callbacks.append(callback_id)

    def me(self):
        return {"username": "tinycmdr_test"}


check("escaping is the three characters HTML needs",
      fb.tg_escape("a <b> & c") == "a &lt;b&gt; &amp; c")
check("backticks become the code they always meant",
      fb.tg_html("run `ls -la` now") == "run <code>ls -la</code> now")
check("bold and italic become Telegram's own tags",
      fb.tg_html("**bold** and *it*") == "<b>bold</b> and <i>it</i>")
check("a markdown link becomes an anchor",
      fb.tg_html("[docs](https://example.com/x)")
      == '<a href="https://example.com/x">docs</a>')
check("a bare URL becomes a link",
      fb.tg_html("see https://example.com/x now")
      == 'see <a href="https://example.com/x">https://example.com/x</a> now')
_tbl = fb.tg_html("| a | b |\n| --- | --- |\n| 1 | 2 |")
check("a pipe table rides in <pre> (Telegram's HTML has no table element)",
      _tbl.startswith("<pre>") and _tbl.endswith("</pre>") and "| 1 | 2 |" in _tbl, _tbl)
check("code spans are protected from the emphasis rules",
      "<b>" not in fb.tg_html("`**not bold**`"))
check("the escaping still comes first (no tag injection)",
      fb.tg_html("<script>alert(1)</script>").startswith("&lt;script&gt;"))
check("a bold tag in the model's own text cannot inject",
      fb.tg_escape("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;")

long_text = ("paragraph one " * 200) + "\n\n" + ("paragraph two " * 200)
# tg_render is the live splitter (split raw, then render); a pre-fix build only has
# the old raw tg_split, so the shared checks below run against whichever is here.
_split = getattr(fb, "tg_render", None) or fb.tg_split
pieces = _split(long_text)
check("a long body becomes several messages",
      len(pieces) > 1, f"{len(pieces)} piece(s)")
check("...none of them over Telegram's ceiling",
      all(len(p) <= fb.TG_MAX for p in pieces),
      str([len(p) for p in pieces]))
joined = "".join(pieces)
check("...and no word is dropped on the way",
      set(long_text.split()) <= set(joined.split()),
      str(sorted(set(long_text.split()) - set(joined.split()))[:5]))
check("...and nothing was truncated either",
      len(joined) >= int(len(long_text) * 0.95),
      f"{len(joined)} of {len(long_text)} chars kept")
check("a single oversized word is hard-cut rather than refused",
      all(len(p) <= fb.TG_MAX for p in _split("x" * 9000)))

client = FakeClient()
dest = fb.TelegramDestination(client, chat_id=42, session_key="telegram-42", reply_to=7)
dest.line("tool", "`shell` ls -la")
check("the first line opens ONE message", len(client.messages) == 1)
check("...with the tool name as code", "<code>shell</code>" in client.messages[0]["text"])
dest.line("tool_done", "shell ls -la · 0.4s")
check("a second line inside the same second does not touch Telegram again",
      len(client.messages) == 1 and not client.edits,
      f"{len(client.messages)} messages, {len(client.edits)} edits")
dest.update(None, "status", "working · 3 steps · 12s")
dest._flush(force=True)
live = client.edits[-1]["text"] if client.edits else ""
check("...and one edit carries every line collected since the last one",
      len(client.edits) == 1 and "ls -la" in live and "working" in live,
      live[:80])
for i in range(20):
    dest.line("tool", "step %d" % i)
dest._flush(force=True)
check("a long run keeps the message bounded (the last lines, not all of them)",
      len(client.messages[0]["text"]) < fb.TG_MAX)
check("...and says how many steps it folded away",
      "earlier step(s)" in (client.edits[-1]["text"] if client.edits else ""))
dest.answer("all done")
check("the answer is posted on its own", client.messages[-1]["text"].startswith("✅"))
check("...as a reply to the operator's own message",
      client.messages[-1]["reply_to"] == 7)
check("...and not buried in the live message", "all done" not in client.messages[0]["text"])

# the question: a button press answers it
client2 = FakeClient()
d2 = fb.TelegramDestination(client2, chat_id=1, session_key="t1")
box = {}


def asker():
    box["answer"] = d2.ask("Restart the task now?", ["yes", "no"], wait=10)


t = threading.Thread(target=asker, daemon=True)
t.start()
time.sleep(0.4)
buttons = client2.messages[-1]["buttons"]
check("the question carries buttons, one per option",
      buttons and buttons[0][0]["callback_data"] == "opt:1" and len(buttons) == 2,
      str(buttons))
check("...and the options are in the text too, for a client without buttons",
      "1) yes" in client2.messages[-1]["text"])
check("...and the typing indicator is on", client2.typing_calls >= 1)
check("a button press answers it", d2.button_ask("opt:2") is True)
t.join(3)
check("...and the answer lands in the slot the reporter reads, as the option's WORDS",
      box.get("answer") == "no", repr(box.get("answer")))
check("...not as the bare number the keyboard carried (A-142)",
      box.get("answer") != "2", repr(box.get("answer")))

# the question: a typed line answers it too
box2 = {}
d3 = fb.TelegramDestination(FakeClient(), chat_id=2, session_key="t2")


def asker2():
    box2["answer"] = d3.ask("Which disk?", ["sda", "sdb"], wait=10)


t2 = threading.Thread(target=asker2, daemon=True)
t2.start()
time.sleep(0.4)
check("a typed line answers it", d3.reply_ask("sdb") is True)
t2.join(3)
check("...with the operator's own words", box2.get("answer") == "sdb")
check("a stray typed line with no question open is not swallowed",
      fb.TelegramDestination(FakeClient(), chat_id=3).reply_ask("hello") is False)


# ---- run 12: the ask path, the splitter, the 429, the secret sweep ----------
# Each was measured against 1.0.84 before the fix; the numbers are in the night
# audit's Telegram pass. These grade the LOCAL half (the question the lane posts,
# the pieces the client builds) - no network anywhere.

import re as _re


def _open_tags(html):
    """The tags `html` leaves open - empty means a balanced document."""
    stack = []
    for m in _re.finditer(r"</?([a-zA-Z][a-zA-Z0-9]*)[^>]*>", html):
        name = m.group(1).lower()
        if m.group(0).startswith("</"):
            if stack and stack[-1] == name:
                stack.pop()
        elif not m.group(0).endswith("/>"):
            stack.append(name)
    return stack


# A-139: the lane always had an ask() - has_human gated it off, so a run never asked.
check("A-139: the Telegram destination can reach a human (has_human)",
      fb.TelegramDestination(FakeClient(), chat_id=90).has_human is True,
      repr(getattr(fb.TelegramDestination(FakeClient(), chat_id=90), "has_human",
                   "<missing>")))

# A-140: the confirm question actually posts, and a no-answer is not silence.
_cf = FakeClient()
_cfdest = fb.TelegramDestination(_cf, chat_id=91, session_key="t-confirm")
_crep = fb.RunReporter(_cfdest, "t-confirm")
_cfok = _crep.confirm("rm -rf /tmp/run12", wait=1)
check("A-140: a confirm-tier command posts its question on Telegram",
      any("confirm-pattern" in m["text"] for m in _cf.messages),
      [m["text"][:50] for m in _cf.messages])
check("A-140: ...and the command is declined when nobody answers",
      _cfok is False)
check("A-140: ...and the operator is told it was skipped, not left guessing",
      any("skipped" in m["text"] or "no answer" in m["text"]
          for m in _cf.messages + _cf.edits),
      [m["text"][:60] for m in _cf.messages + _cf.edits][:3])
check("A-187: the confirm question's own fence renders as <pre>, not backticks",
      any("<pre>rm -rf /tmp/run12" in m["text"] for m in _cf.messages),
      [m["text"][:90] for m in _cf.messages])

# A-141: the destination IS an ask_user door, so the tool can reach this chat.
_cd = fb.TelegramDestination(FakeClient(), chat_id=92, session_key="t-door")
_door = fb._ask_door("t-door", {"ask_door": _cd})
check("A-141: the Telegram destination is a complete ask_user door",
      _door is not None and callable(_door.get("opener"))
      and callable(_door.get("post")) and callable(_door.get("post_done"))
      and callable(_door.get("close_question")), repr(_door))
_dbox = {}
_dc = FakeClient()
_dd = fb.TelegramDestination(_dc, chat_id=93, session_key="t-door2")
_dth = threading.Thread(
    target=lambda: _dbox.__setitem__("r", fb.ask_operator(
        "t-door2", "Ship it?", ctx={"ask_door": _dd}, options=["yes", "no"],
        timeout=5)), daemon=True)
_dth.start()
time.sleep(0.4)
_dd.button_ask("opt:1")
_dth.join(3)
check("A-141: ask_user through the real destination comes back answered",
      _dbox.get("r", ("", ""))[0] == "answered" and _dbox["r"][1] == "yes",
      repr(_dbox.get("r")))

# A-144: one question at a time; a second used to steal the first waiter's answer.
_q = fb.TelegramDestination(FakeClient(), chat_id=94, session_key="t-one")
_first = _q._open_question("one", ["a", "b"]) if hasattr(_q, "_open_question") else None
_second = (_q._open_question("two", ["a", "b"])
           if hasattr(_q, "_open_question") else object())
check("A-144: a second question while one is open is refused, not queued",
      hasattr(_q, "_open_question") and _first is not None and _second is None,
      "first=%s second-refused=%s" % (_first is not None, _second is None))
if hasattr(_q, "_open_question"):
    _q.close_question()


# A-145: a question that never reached the screen must not burn the whole wait.
class _BlockedClient(FakeClient):
    def send(self, *a, **k):
        raise RuntimeError("bot blocked")


_bt0 = time.time()
_bout = fb.TelegramDestination(_BlockedClient(), chat_id=95,
                               session_key="t-block").ask("q?", ["yes", "no"],
                                                          wait=30)
_bdt = time.time() - _bt0
check("A-145: a failed post returns at once instead of waiting out the timeout",
      _bout is None and _bdt < 1.0, "%.3fs" % _bdt)

# A-146: a direct ask() is clamped by _ask_wait_cap, not the caller's number.
_wsaved = fb.CONFIG["agent"].get("ask_user_wait_seconds")
_tbox = {}


def _cap_ask():
    _tbox["v"] = fb.TelegramDestination(FakeClient(), chat_id=96,
                                        session_key="t-cap").ask(
        "q?", ["yes", "no"], wait=999999)


fb.CONFIG["agent"]["ask_user_wait_seconds"] = 1
_cth = threading.Thread(target=_cap_ask, daemon=True)
_cth.start()
_cth.join(8)   # the cap floor is 5s; 8 is enough, 999999 is not
check("A-146: ask() honours _ask_wait_cap() instead of the caller's wait",
      not _cth.is_alive() and _tbox.get("v") is None,
      "still parked" if _cth.is_alive() else repr(_tbox.get("v")))
if _wsaved is None:
    fb.CONFIG["agent"].pop("ask_user_wait_seconds", None)
else:
    fb.CONFIG["agent"]["ask_user_wait_seconds"] = _wsaved

# A-143: a verb typed while a question is open is an order, not the answer.
_ac = FakeClient()
_ad = fb.TelegramDestination(_ac, chat_id=97, session_key="telegram-97")
_ap = fb.TelegramPoller(_ac, {"123456789"})
_ap.live[97] = _ad
_acancel = threading.Event()
_ap.cancel[97] = _acancel
_abox = {}
_ath = threading.Thread(target=lambda: _abox.__setitem__(
    "v", _ad.ask("Question?", ["yes", "no"], wait=5)), daemon=True)
_ath.start()
time.sleep(0.3)
_ahandled = _ap.handle({"update_id": 1, "message": {
    "message_id": 1, "text": "/stop", "chat": {"id": 97, "type": "private"},
    "from": {"id": 123456789, "username": "someone"}}})
_row = _ad._asking.get("q")
if _row is not None:
    _row.get("ev", _row.get("event")).set()
_ath.join(3)
check("A-143: /stop with a question open stops the run, it is not the answer",
      _ahandled and _acancel.is_set(), repr(_abox.get("v")))

# A-150: the answer is split as RAW markdown, then rendered per message.
LONG = (("**bold** and [link](https://example.com/x) and `code` words " * 25)
        + "\n\n" + "```\n" + "print('hi')\n" * 40 + "```\n\n"
        + ("see https://example.com/y now\n" * 25))
check("A-150: tg_render splits raw markdown, every piece within the ceiling",
      hasattr(fb, "tg_render") and len(fb.tg_render(LONG)) >= 2
      and all(len(p) <= fb.TG_MAX for p in fb.tg_render(LONG)),
      "%d piece(s)" % (len(fb.tg_render(LONG)) if hasattr(fb, "tg_render") else -1))
check("A-150: ...each piece is a balanced document (no tag left open)",
      hasattr(fb, "tg_render")
      and all(not _open_tags(p) for p in fb.tg_render(LONG)),
      [str(_open_tags(p)) for p in fb.tg_render(LONG)][:3]
      if hasattr(fb, "tg_render") else "no tg_render")
check("A-150: ...and the fenced block arrives whole in one message",
      hasattr(fb, "tg_render")
      and sum(1 for p in fb.tg_render(LONG) if "<pre>print('hi')" in p) == 1,
      "no tg_render" if not hasattr(fb, "tg_render") else "")
check("A-150: tg_html_split closes a tag a cut would leave open",
      hasattr(fb, "tg_html_split")
      and all(len(p) <= fb.TG_MAX and not _open_tags(p)
              for p in fb.tg_html_split("<b>" + "x" * 5000 + "</b>")),
      "no tg_html_split" if not hasattr(fb, "tg_html_split") else "")
_ansc = FakeClient()
_ansd = fb.TelegramDestination(_ansc, chat_id=98, session_key="t-ans")
_ansd.answer(LONG)
_ans_texts = [m["text"] for m in _ansc.messages]
check("A-150: dest.answer() sends several messages, none over the ceiling",
      len(_ans_texts) >= 2 and all(len(t) <= fb.TG_MAX for t in _ans_texts),
      "lens=%s" % [len(t) for t in _ans_texts])
check("A-150: ...and no message carries a tag its neighbours opened",
      all(not _open_tags(t) for t in _ans_texts),
      [str(_open_tags(t)) for t in _ans_texts][:3])


# A-151: the live message is truncated outside every tag, not mid-tag.
class _RecClient:
    def __init__(self):
        self.payload = {}

    def call(self, method, **payload):
        self.payload = payload
        return {"message_id": 1}


_rc = _RecClient()
_rcl = fb.TelegramClient("1:x")
_rcl.call = _rc.call
_rcl.edit(1, 2, "<b>" + "x" * 5000)
_etxt = _rc.payload.get("text", "")
check("A-151: edit() truncates outside every tag and closes what is open",
      len(_etxt) <= fb.TG_MAX and not _open_tags(_etxt),
      "len=%d open=%s" % (len(_etxt), _open_tags(_etxt)))


# A-149: a 429's own retry_after is honoured once, bounded by config.
class _Resp:
    def __init__(self, payload, code=429):
        self._p = payload
        self.status_code = code

    def json(self):
        return self._p


class _FakeRequests:
    def __init__(self):
        self.n = 0

    def post(self, url, json=None, timeout=None):
        self.n += 1
        if self.n == 1:
            return _Resp({"ok": False, "error_code": 429,
                          "description": "Too Many Requests: retry after 30",
                          "parameters": {"retry_after": 30}})
        return _Resp({"ok": True, "result": [{"message_id": 1}]}, code=200)


_saved_req = fb.requests
_saved_cap = fb.CONFIG["telegram"].get("retry_after_max")
try:
    _fr = _FakeRequests()
    fb.requests = _fr
    fb.CONFIG["telegram"]["retry_after_max"] = 0.05
    _rcl2 = fb.TelegramClient("1:x")
    try:
        _got = _rcl2.send(1, "hello")
    except Exception as _e:                     # pre-fix: raised, answer lost
        _got = ("raised", _e)
    check("A-149: a 429 alone does not lose the message (one bounded retry)",
          _fr.n == 2 and isinstance(_got, list), "%d post(s), %r" % (_fr.n, _got))
    _fr2 = _FakeRequests()
    fb.requests = _fr2
    fb.CONFIG["telegram"]["retry_after_max"] = 0
    _raised = False
    try:
        _rcl2.send(1, "hello")
    except RuntimeError:
        _raised = True
    check("A-149: retry_after_max=0 turns the wait off without spinning",
          _raised and _fr2.n == 1, "%d post(s)" % _fr2.n)
finally:
    fb.requests = _saved_req
    if _saved_cap is None:
        fb.CONFIG["telegram"].pop("retry_after_max", None)
    else:
        fb.CONFIG["telegram"]["retry_after_max"] = _saved_cap

# A-156: a token in config.json is swept like every other secret section.
_tsave = dict(fb.CONFIG.get("telegram") or {})
_ssave = fb._SECRETS
_tok = "12345:AAVerySecretTokenValueXYZ"
try:
    fb.CONFIG.setdefault("telegram", {})["token"] = _tok
    _vals = fb._secret_values()
    fb._SECRETS = _vals
    check("A-156: a telegram token in config.json is in _secret_values",
          _tok in _vals)
    check("A-156: ...so scrub masks it before it can reach chat",
          _tok not in fb.scrub("poll failed: " + _tok),
          repr(fb.scrub("poll failed: " + _tok)))
finally:
    fb.CONFIG["telegram"].clear()
    fb.CONFIG["telegram"].update(_tsave)
    fb._SECRETS = _ssave

# A-187/188/190: fences are protected before the inline-code rule.
check("A-187: a fenced block is one <pre>, not a broken <code> span",
      fb.tg_html("```\nfoo\n```") == "<pre>foo</pre>",
      repr(fb.tg_html("```\nfoo\n```")))
check("A-187: ...with the language line dropped from the body",
      fb.tg_html("```py\nx = 1\n```") == "<pre>x = 1</pre>",
      repr(fb.tg_html("```py\nx = 1\n```")))
check("A-188: a triple-backtick fence reaches <pre> (the branch is live)",
      "<pre>" in fb.tg_html("```\nfoo\n```"))
check("A-190: emphasis cannot reach inside a fence",
      fb.tg_html("```\n**bold** https://e.com\n```")
      == "<pre>**bold** https://e.com</pre>",
      repr(fb.tg_html("```\n**bold** https://e.com\n```")))
check("A-190: ...even when the fence is never closed",
      "<b>" not in fb.tg_html("```\nnot closed **bold**")
      and fb.tg_html("```\nnot closed **bold**").startswith("<pre>"),
      repr(fb.tg_html("```\nnot closed **bold**")))

# the gate
client3 = FakeClient()
poller = fb.TelegramPoller(client3, {"123456789"})


def msg(chat_id, text, user_id, kind="private", mid=1):
    return {"update_id": mid, "message": {
        "message_id": mid, "text": text, "chat": {"id": chat_id, "type": kind},
        "from": {"id": user_id, "username": "someone"}}}


check("an allowed id is allowed", poller.allowed_user({"id": 123456789}) is True)
check("anyone else is not", poller.allowed_user({"id": 5}) is False)
check("a stranger's message is ignored, not answered",
      poller.handle(msg(1, "rm -rf /", 5)) is False and not client3.messages)
check("a group message is ignored even from an allowed user",
      poller.handle(msg(1, "hi", 123456789, kind="group")) is False
      and not client3.messages)
check("/help answers", poller.handle(msg(1, "/help", 123456789, mid=2)) is True
      and "tinycmdr" in client3.messages[-1]["text"])
check("/stop with nothing running says so",
      poller.handle(msg(1, "/stop", 123456789, mid=3)) is True
      and "nothing is running" in client3.messages[-1]["text"])
check("a repeated update id is not handled twice",
      poller.handle(msg(1, "/help", 123456789, mid=3)) is True
      and len(client3.messages) == 2)
submitted = []
poller.submit = lambda chat_id, text, mid: submitted.append((chat_id, text, mid))
check("a task is handed to the chat's worker",
      poller.handle(msg(1, "check the backups", 123456789, mid=4)) is True
      and submitted == [(1, "check the backups", 4)], str(submitted))
check("each chat gets its own conversation name",
      fb.tg_session_key(42) == "telegram-42")

# ---- a duplicate id in ANOTHER chat is not the same message ----------------
# (review, 2026-09-29: seen held the bare message_id, but Telegram numbers messages
# per chat, so with two allowed users the second chat's id-1 was swallowed as a dup
# of the first chat's and never answered - silently.)
cross = FakeClient()
xp = fb.TelegramPoller(cross, {"111", "222"})
handed = []
xp.submit = lambda chat_id, text, mid: handed.append((chat_id, text, mid))
check("the same message id in two chats is two messages",
      xp.handle(msg(11, "one", 111, mid=1)) is True
      and xp.handle(msg(22, "two", 222, mid=1)) is True
      and handed == [(11, "one", 1), (22, "two", 1)], str(handed))
check("...and a true repeat inside one chat is still dropped once",
      xp.handle(msg(11, "one", 111, mid=1)) is True and len(handed) == 2,
      str(handed))

# ---- a /stop that lands before the worker picks the task up still bites -----
# (review, 2026-09-29: submit() registered the chat's cancel event and /stop set it,
# but the worker minted its OWN event and overwrote the slot, so a stop in the gap
# between the message arriving and the run starting was discarded: the run began
# un-stoppable. Drive the real run_telegram with the bot API and the run faked.)

_hold = threading.Event()               # holds the worker until the test has stopped it
_parked = threading.Event()
_poller_box = {}
_result = {}


class _ParkedDestination(fb.TelegramDestination):
    """The real reporter, held at construction until the test has pressed /stop."""

    def __init__(self, *a, **kw):
        _parked.set()
        _hold.wait(3)
        super().__init__(*a, **kw)


class _RecordingPoller(fb.TelegramPoller):
    """The real poller, kept so the test can reach the channel's cancel slot."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _poller_box["poller"] = self


class _HeadlessReporter:
    def __init__(self, dest, key):
        pass

    def finish(self, ok=True):
        pass


class _LoopOnceClient(FakeClient):
    """The bot API, but the second poll presses /stop and then ends the lane."""

    def __init__(self, token):
        super().__init__()
        self.polls = 0

    def updates(self, offset):
        self.polls += 1
        if self.polls == 1:
            return [msg(7, "do the thing", 123456789, mid=1)]
        if not _parked.wait(3):
            raise SystemExit
        ev = _poller_box["poller"].cancel.get(7)
        _result["stop_event"] = ev
        if ev is not None:
            ev.set()
        _hold.set()
        raise SystemExit                        # not an Exception: leaves the poll loop


def _run_once(key, text, reporter, *, cancel_event=None, **kw):
    _result["cancel"] = cancel_event
    _result["sees_stop"] = cancel_event is not None and cancel_event.is_set()
    return None


_keep = (fb.CONFIG["telegram"], fb.TelegramClient, fb.TelegramPoller,
         fb.TelegramDestination, fb.RunReporter, fb.drive_run, fb.lane_up)
fb.CONFIG["telegram"] = {"token": "12345:" + "A" * 35, "allowed_users": ["123456789"]}
fb.TelegramClient = _LoopOnceClient
fb.TelegramPoller = _RecordingPoller
fb.TelegramDestination = _ParkedDestination
fb.RunReporter = _HeadlessReporter
fb.drive_run = _run_once
fb.lane_up = lambda *a, **k: None
try:
    try:
        fb.run_telegram()
    except SystemExit:
        pass
    for _ in range(40):
        if "sees_stop" in _result:
            break
        time.sleep(0.05)
    check("a /stop before the worker picks the task up is not discarded",
          _result.get("sees_stop") is True,
          str({k: v for k, v in _result.items() if k != "cancel"}))
finally:
    (fb.CONFIG["telegram"], fb.TelegramClient, fb.TelegramPoller,
     fb.TelegramDestination, fb.RunReporter, fb.drive_run,
     fb.lane_up) = _keep

# ---- a message during a LIVE run steers it, and the worker still leaves -----
# (2026-09-29. The Telegram lane handed drive_run a cancel event but no
# steering and no watchdog record, so a plain message sent mid-run queued behind the
# run it was meant to redirect - while README 110/137 promise a mid-run message steers,
# which was already true of Mattermost and the CLI. And its workers blocked on the
# queue with no timeout, so one thread lived per chat this process ever heard from.)
#
# Driven end to end: the real run_telegram, the real drive_run (so the run's steering
# comes from the registry drive_run opens), and only the model call replaced.

lane = {}
run_started = threading.Event()
release = threading.Event()
got = {}


class _LivePoller(fb.TelegramPoller):
    """The real poller, kept so the test can feed it a message mid-run."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        lane["poller"] = self


class _FeedThenLeaveClient(FakeClient):
    """Poll 1 the task; poll 2 a mid-run message through the real handler; poll 3 leave
    the loop (SystemExit is not an Exception, so it leaves run_telegram) while the
    worker is held inside the run, so the idle exit can be observed afterwards."""

    def __init__(self, token):
        super().__init__()
        lane["client"] = self
        self.polls = 0

    def updates(self, offset):
        self.polls += 1
        if self.polls == 1:
            return [msg(21, "start the check", 123456789, mid=1)]
        if self.polls == 2:
            if not run_started.wait(5):
                raise SystemExit
            lane["poller"].handle(msg(21, "hold on, skip step 3",
                                      123456789, mid=2))
            lane["poller"].handle(msg(21, "/stop", 123456789, mid=3))
            return []
        raise SystemExit


def _live_model_run(session_key, text, **kw):
    got["steer_cb"] = kw.get("steer_cb")
    got["runs"] = got.get("runs", 0) + 1
    run_started.set()
    release.wait(10)
    return "done"


_keep2 = (fb.CONFIG["telegram"], fb.TelegramClient, fb.TelegramPoller,
          fb.TelegramDestination, fb.RunReporter, fb.AGENT.run, fb.lane_up,
          fb.TG_IDLE_SECONDS)
fb.CONFIG["telegram"] = {"token": "12345:" + "A" * 35, "allowed_users": ["123456789"]}
fb.TelegramClient = _FeedThenLeaveClient
fb.TelegramPoller = _LivePoller
fb.AGENT.run = _live_model_run
fb.lane_up = lambda *a, **k: None
fb.TG_IDLE_SECONDS = 1.0
try:
    try:
        fb.run_telegram()
    except SystemExit:
        pass
    check("the run started (the lane reached the model)", run_started.is_set(),
          str(got))
    check("a message during the run is steered into it, not queued",
          got.get("steer_cb") is not None
          and got["steer_cb"]() == [("123456789", "hold on, skip step 3")],
          str(got.get("steer_cb") and got["steer_cb"]()))
    check("...so no second run starts behind it", got.get("runs") == 1, got)
    check("...and the operator is told it went into the run",
          any("Passing that into the run" in m["text"]
              for m in lane["client"].messages),
          [m["text"][:60] for m in lane["client"].messages])
    check("a /stop mid-run is still out-of-band, never steered",
          got["steer_cb"]() == [], "a command was steered into the run")
    release.set()
    deadline = time.time() + 6
    while time.time() < deadline and any(
            t.name == "tg-21" for t in threading.enumerate()):
        time.sleep(0.05)
    check("the chat's worker exits once the chat goes idle",
          not any(t.name == "tg-21" for t in threading.enumerate()),
          [t.name for t in threading.enumerate() if t.name.startswith("tg-")])
finally:
    release.set()
    (fb.CONFIG["telegram"], fb.TelegramClient, fb.TelegramPoller,
     fb.TelegramDestination, fb.RunReporter, fb.AGENT.run, fb.lane_up,
     fb.TG_IDLE_SECONDS) = _keep2

# ---- A-199: the lane can SEND a file. Grade the LOCAL half: the ONE multipart POST the
# transport builds, and the door's cap/answer. No network anywhere. -------------------
import tempfile as _tempfile

_file_dir = Path(_tempfile.mkdtemp(prefix="tg-file-"))
_doc = _file_dir / "report.txt"
_doc.write_text("hello, operator", encoding="utf-8")
_pic = _file_dir / "shot.png"
_pic.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)


class _RecPost:
    """The requests.post the transport makes, recorded instead of sent."""

    def __init__(self):
        self.calls = []

    def post(self, url, data=None, files=None, timeout=None, **kw):
        self.calls.append({"url": url, "data": data, "files": files, "timeout": timeout})

        class _R:
            status_code = 200

            def json(self):
                return {"ok": True, "result": {"message_id": 7}}

        return _R()


_saved_requests = fb.requests
_rec = _RecPost()
fb.requests = _rec
try:
    try:
        _sent = fb.TelegramClient("1:x").send_file(99, str(_doc), note="the report")
    except Exception as _e:                     # pre-fix: no such method at all
        _sent = ("raised", _e)
finally:
    fb.requests = _saved_requests
_call = _rec.calls[0] if _rec.calls else {}
check("A-199: send_file is ONE multipart POST to sendDocument",
      hasattr(fb.TelegramClient, "send_file") and len(_rec.calls) == 1
      and str(_call.get("url", "")).endswith("/sendDocument"),
      [c.get("url") for c in _rec.calls] or "no send_file")
check("A-199: ...carrying the chat, the caption and the file itself",
      str(_call.get("data", {}).get("chat_id")) == "99"
      and _call.get("data", {}).get("caption") == "the report"
      and (_call.get("files") or {}).get("document", (None,))[0] == "report.txt"
      and _sent == {"message_id": 7},
      (_call.get("data"), sorted((_call.get("files") or {})), _sent))

_rec2 = _RecPost()
fb.requests = _rec2
try:
    fb.TelegramClient("1:x").send_file(99, str(_pic))
except Exception:                               # pre-fix: no such method at all
    pass
finally:
    fb.requests = _saved_requests
check("A-199: ...an image goes as sendPhoto (rendered inline, not as a document)",
      bool(_rec2.calls) and str(_rec2.calls[0]["url"]).endswith("/sendPhoto")
      and "photo" in (_rec2.calls[0].get("files") or {}),
      _rec2.calls[0]["url"] if _rec2.calls else "no call")


class _FileClient(FakeClient):
    """A bot API that records the file door's call instead of uploading."""

    def __init__(self):
        super().__init__()
        self.files = []

    def send_file(self, chat_id, path, note="", reply_to=None):
        self.files.append((chat_id, Path(str(path)).name, note, reply_to))
        return {"message_id": 5}


_fc = _FileClient()
_line = fb.TelegramDestination(_fc, chat_id=99, reply_to=3).attach(str(_doc), "here it is")
check("A-199: the destination's file door uploads and answers honestly",
      _line.startswith("sent:") and _fc.files == [(99, "report.txt", "here it is", 3)],
      (_line, _fc.files))
_fc2 = _FileClient()
check("A-199: the door reaches the transport through the reporter the tool is handed",
      fb.RunReporter(fb.TelegramDestination(_fc2, chat_id=1), "s").attach(
          str(_doc), "n").startswith("sent:") and bool(_fc2.files),
      _fc2.files)

_saved_cap = fb.CONFIG["agent"].get("send_file_max_bytes")
_big = _file_dir / "big.bin"
_big.write_bytes(b"x" * 4096)
fb.CONFIG["agent"]["send_file_max_bytes"] = 1024
try:
    _cap_line = fb.TelegramDestination(_FileClient(), chat_id=99).attach(str(_big))
finally:
    if _saved_cap is None:
        fb.CONFIG["agent"].pop("send_file_max_bytes", None)
    else:
        fb.CONFIG["agent"]["send_file_max_bytes"] = _saved_cap
check("A-199: ...over the cap is refused with the path, not uploaded",
      "over this box's 1,024-byte send limit" in _cap_line
      and "NOT SENT" not in _cap_line, _cap_line)
check("A-199: ...and a path that is not there is an honest error too",
      fb.TelegramDestination(_FileClient(), chat_id=99).attach(
          str(_file_dir / "nope.bin")).startswith("ERROR: no such file"))
shutil.rmtree(_file_dir, ignore_errors=True)

# ---- A-200/A-201: run_telegram wires the Scheduler and the report door, and honours the
# restart announce - exactly as run_bot does. -------------------------------------------
_wire = {}


class _WireClient(FakeClient):
    """The bot API, but the first poll ends the lane (SystemExit leaves the loop)."""

    def __init__(self, token):
        super().__init__()
        _wire["client"] = self

    def updates(self, offset):
        raise SystemExit


_keep3 = (fb.CONFIG["telegram"], fb.TelegramClient, fb.REPORTER,
          fb.SCHEDULER.dispatcher, fb.announce_startup, fb.lane_up,
          fb.CONFIG["agent"].get("announce_restart"))
_announced = []
fb.CONFIG["telegram"] = {"token": "12345:" + "A" * 35, "allowed_users": ["1"]}
fb.TelegramClient = _WireClient
fb.REPORTER = None
fb.SCHEDULER.dispatcher = None
fb.announce_startup = lambda d: _announced.append(d)
fb.lane_up = lambda *a, **k: None


def _drive_wire():
    try:
        fb.run_telegram()
    except SystemExit:
        pass


try:
    fb.CONFIG["agent"]["announce_restart"] = True
    _drive_wire()
    _report = fb.REPORTER
    check("A-200: run_telegram wires REPORTER, so a scheduled job can report",
          callable(_report), repr(_report))
    if callable(_report):
        _report(4242, "scheduled: the backup finished")
    check("A-200: ...and the post lands in the chat it is given",
          any(m["chat"] == 4242 and "the backup finished" in m["text"]
              for m in _wire["client"].messages),
          [m["text"][:40] for m in _wire["client"].messages])
    check("A-200: run_telegram wires SCHEDULER.dispatcher the same way run_bot does",
          fb.SCHEDULER.dispatcher is not None
          and all(callable(getattr(fb.SCHEDULER.dispatcher, m, None))
                  for m in ("_post", "_edit", "send_file")),
          repr(fb.SCHEDULER.dispatcher))
    check("A-201: run_telegram honours announce_restart (run_bot calls it)",
          len(_announced) == 1, len(_announced))
    fb.CONFIG["agent"]["announce_restart"] = False
    _drive_wire()
    check("A-201: ...and the config switch still turns it off",
          len(_announced) == 1, len(_announced))
finally:
    (fb.CONFIG["telegram"], fb.TelegramClient, fb.REPORTER,
     fb.SCHEDULER.dispatcher, fb.announce_startup, fb.lane_up,
     _ann_saved) = _keep3
    if _ann_saved is None:
        fb.CONFIG["agent"].pop("announce_restart", None)
    else:
        fb.CONFIG["agent"]["announce_restart"] = _ann_saved

# A-200: a run started from a chat carries that chat as its channel, which is what a
# `schedule` job made in that chat reports back to.
class _MsgWireClient(FakeClient):
    """Poll 1 hands the lane one task (from an allowed id); poll 2 leaves."""

    def __init__(self, token):
        super().__init__()
        self.polls = 0

    def updates(self, offset):
        self.polls += 1
        if self.polls == 1:
            return [msg(777, "schedule the backup", 123456789, mid=1)]
        raise SystemExit


_keep4 = (fb.CONFIG["telegram"], fb.TelegramClient, fb.AGENT.run, fb.lane_up,
          fb.TG_IDLE_SECONDS, fb.REPORTER, fb.SCHEDULER.dispatcher)
_seen_channel = {}
fb.CONFIG["telegram"] = {"token": "12345:" + "A" * 35, "allowed_users": ["123456789"]}
fb.TelegramClient = _MsgWireClient
fb.AGENT.run = lambda session_key, text, **kw: _seen_channel.setdefault(
    "channel_id", kw.get("channel_id"))
fb.lane_up = lambda *a, **k: None
fb.TG_IDLE_SECONDS = 1.0
try:
    try:
        fb.run_telegram()
    except SystemExit:
        pass
    _deadline = time.time() + 6
    while "channel_id" not in _seen_channel and time.time() < _deadline:
        time.sleep(0.02)
finally:
    (fb.CONFIG["telegram"], fb.TelegramClient, fb.AGENT.run, fb.lane_up,
     fb.TG_IDLE_SECONDS, fb.REPORTER, fb.SCHEDULER.dispatcher) = _keep4
check("A-200: a run started from a chat carries that chat as its channel",
      _seen_channel.get("channel_id") == 777, _seen_channel)

# ---- the token has ONE home, and both doors never fight silently -----------
# (review, 2026-09-22: telegram.token was read from config.json, which contradicts
# the package's own rule that secrets live only in .env; and with both tokens set
# the Telegram lane simply never started, with nothing said.)


def staged_token(config_token, env_token):
    """Import the app from a throwaway folder and report what it made of the token."""
    import json
    import subprocess
    import tempfile
    wd = Path(tempfile.mkdtemp(prefix="fbtg-"))
    try:
        shutil.copy2(SRC, wd / "tinycmdr.py")
        (wd / "config.json").write_text(json.dumps({
            "telegram": {"token": config_token, "allowed_users": ["123"]},
            "mattermost": {"url": "", "token": ""},
            "llm": {"base_url": "http://127.0.0.1:1/v1", "model": "none"},
        }), encoding="utf-8")
        # Strip EVERY TINYCMDR_* var: importing tinycmdr.py at the top of this
        # file loads the repo's own .env into os.environ, so the suite's
        # environment is not a clean one to measure a token rule in.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("TINYCMDR_")}
        if env_token:
            env["TINYCMDR_TG_TOKEN"] = env_token
        code = (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('staged', r'%s')\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "sys.modules['staged'] = m\n"
            "spec.loader.exec_module(m)\n"
            "print('TOKEN', repr(m.CONFIG['telegram'].get('token') or ''))\n"
            "print('TG_ONLY', m.tg_token_only())\n" % (wd / "tinycmdr.py"))
        r = subprocess.run([sys.executable, "-c", code], cwd=str(wd), env=env,
                           capture_output=True, text=True, timeout=180)
        said = r.stdout + r.stderr
        logf = wd / "tinycmdr.log"
        if logf.exists():
            said += logf.read_text(encoding="utf-8", errors="replace")
        return said
    finally:
        shutil.rmtree(wd, ignore_errors=True)


said = staged_token("123456:SECRET-FROM-CONFIG", "")
check("a token in config.json is IGNORED", "TOKEN ''" in said, said[-300:])
check("and the log says why", "IGNORED" in said, said[-300:])
check("a config token alone does not open the lane", "TG_ONLY False" in said,
      said[-300:])

said = staged_token("", "123456:SECRET-FROM-ENV")
check("the .env token IS honoured", "TOKEN '123456:SECRET-FROM-ENV'" in said,
      said[-300:])
check("and it opens the lane on its own", "TG_ONLY True" in said, said[-300:])

_saved = (dict(fb.CONFIG.get("telegram") or {}), dict(fb.CONFIG["mattermost"]))
# The rule under test is CONFIG precedence, so the environment must not decide it.
# _mm_token_configured() falls back to os.environ["TINYCMDR_MM_TOKEN"], which a real
# install's .env supplies and this block cannot clear through CONFIG - so on any
# configured box "one door is not a warning" failed while a clean clone was green
# (measured 2026-09-29 on macOS: 41 passed, 1 failed; the same tree archived to a
# clean checkout: 42 passed). That is the suite grading the box instead of the rule, which
# is the class tests/hermetic.py exists for. Neutralise the two names for this block only.
_env_saved = {k: os.environ.pop(k) for k in ("TINYCMDR_MM_TOKEN", "TINYCMDR_TG_TOKEN")
              if k in os.environ}
try:
    fb.CONFIG["telegram"]["token"] = "tg-token-here"
    fb.CONFIG["mattermost"]["token"] = "mm-token-here"
    check("both doors set serves BOTH (the old refusal left the new lane dark)",
          fb.lanes_to_serve([]) == ["mattermost", "telegram"], fb.lanes_to_serve([]))
    check("...and a flag still forces one lane",
          fb.lanes_to_serve(["--telegram"]) == ["telegram"]
          and fb.lanes_to_serve(["--mattermost"]) == ["mattermost"], None)
    fb.CONFIG["mattermost"]["token"] = ""
    check("one door serves that door", fb.lanes_to_serve([]) == ["telegram"], None)
    fb.CONFIG["mattermost"]["token"] = "mm-token-here"
    fb.CONFIG["telegram"]["token"] = ""
    check("...and the other", fb.lanes_to_serve([]) == ["mattermost"], None)
    fb.CONFIG["mattermost"]["token"] = ""
    check("no token serves none", fb.lanes_to_serve([]) == [], None)
finally:
    fb.CONFIG["telegram"].update(_saved[0])
    fb.CONFIG["mattermost"].update(_saved[1])
    os.environ.update(_env_saved)


# ---- run 12, pass 3: the remainder of the Telegram finder ---------------------------
# Measured on the 1.0.84 line before each fix; every check below is RED on the
# pre-fix snapshot. The LOCAL half only - payloads the client builds, the poller's
# dispatch/dedupe/gating, the renderer's output. No network anywhere.

import logging as _logging


class _CaptureLog(_logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:               # noqa: BLE001 - a capture must not break a test
            self.lines.append("")


# A-191/A-192/A-193/A-196: renderer edges.
check("A-191: a quote in a URL cannot escape the href attribute (no tag injection)",
      'a"b' not in fb.tg_html('[x](https://e.com/a"b)'),
      repr(fb.tg_html('[x](https://e.com/a"b)')))
check("A-191: ...and an ordinary link still renders",
      fb.tg_html("[docs](https://example.com/x)")
      == '<a href="https://example.com/x">docs</a>')
check("A-192: None renders as nothing, not the word None", fb.tg_escape(None) == "",
      repr(fb.tg_escape(None)))
try:
    _nul = fb.tg_html("a\x000\x00b")
except Exception as _e:                 # noqa: BLE001 - the crash IS the finding
    _nul = "RAISED %s" % type(_e).__name__
check("A-193: a stray NUL in model output cannot break the render",
      _nul == "a0b" and "<code>" not in _nul, repr(_nul))
_tbl2 = fb.tg_html("| a | b |\n| 1 | 2 |")
check("A-196: a pipe table with no separator row still rides in <pre>",
      _tbl2.startswith("<pre>") and "</pre>" in _tbl2, repr(_tbl2))

# A-194/A-195: a long answer carrying a URL longer than half a message (a signed link,
# a data URI) must still split into WHOLE, non-empty messages. The cut used to collapse
# to one character when the atom started a piece, so the URL came out as an empty
# message, then "h", then a broken "ttps://..." link.
_long_url = "https://signed.example.com/report?" + "k=ABCDEFGH" * 300
_lu_txt = ("filler word " * 500) + "see " + _long_url + "\n\n" + ("tail " * 500)
_lu_pieces = fb.tg_render(_lu_txt)
check("A-194: no message is empty or whitespace-only",
      all(p.strip() for p in _lu_pieces), [len(p) for p in _lu_pieces])
check("A-195: ...and every message is within the ceiling",
      all(len(p) <= fb.TG_MAX for p in _lu_pieces), [len(p) for p in _lu_pieces])
check("A-195: ...and nothing is lost on the way",
      len("".join(_lu_pieces)) >= len(_lu_txt) * 0.95,
      (len("".join(_lu_pieces)), len(_lu_txt)))
_fit_url = "https://example.com/report/" + "abcDEF123" * 55
_fu_pieces = fb.tg_render(("filler word " * 500) + " see " + _fit_url + "\n\n"
                          + ("tail " * 400))
check("A-195: a URL that fits is never divided",
      any(_fit_url in p for p in _fu_pieces), [len(p) for p in _fu_pieces])


# A-152: a failed edit must re-open the live message, not redraw a dead id for ever.
class _DeadEditClient(FakeClient):
    def edit(self, chat_id, message_id, text, buttons=None):
        raise RuntimeError("Bad Request: message to edit not found")


_dec = _DeadEditClient()
_ded = fb.TelegramDestination(_dec, chat_id=51, session_key="t-dead")
_ded.line("tool", "step one")
_ded._flush(force=True)                     # opens the live message
_ded.line("tool", "step two")
_ded._flush(force=True)                     # the edit fails: recovery is armed
_ded._flush(force=True)                     # and the next flush must RE-OPEN, not re-edit
check("A-152: a failed edit re-opens the live message instead of freezing it",
      len(_dec.messages) >= 2, (_ded.live_id, len(_dec.messages)))


# A-153/A-198: the run's close forces the last live write and unpins the status.
class _FinClient(FakeClient):
    pass


_fc9 = _FinClient()
_fd9 = fb.TelegramDestination(_fc9, chat_id=52, session_key="t-fin")
_fd9.line("tool", "step one")
_fd9._flush(force=True)
_fd9.update(None, "status", "working · 3 steps")
_fd9.line("tool", "step two")                     # inside the throttle window
_fd9.update(None, "final", "✅ Done — 2 step(s)")
check("A-153: the closing line forces the last live write past the throttle",
      any("step two" in e["text"] for e in _fc9.edits), [e["text"] for e in _fc9.edits])
check("A-198: ...and the pinned status does not outlive the run", _fd9.status == "",
      repr(_fd9.status))


# A-148: the question is part of the run's own story.
_ac9 = FakeClient()
_ad9 = fb.TelegramDestination(_ac9, chat_id=53, session_key="t-askline")
_box9 = {}


def _ask9():
    _box9["v"] = _ad9.ask("Restart the task now?", ["yes", "no"], wait=5)


_th9 = threading.Thread(target=_ask9, daemon=True)
_th9.start()
time.sleep(0.3)
_ad9.button_ask("opt:1")
_th9.join(3)
_asklines = [t for k, t in _ad9.lines if k == "ask"]
check("A-148: the question is recorded in the run's live message",
      bool(_asklines) and "Restart the task now?" in _asklines[0],
      [k for k, _ in _ad9.lines])
check("A-148: ...and the line says what came back",
      bool(_asklines) and "yes" in _asklines[0], _asklines)


# A-159/A-160: verb parsing.
_pp = fb.TelegramPoller(FakeClient(), {"1"})
try:
    _empty_verb = _pp._verb(1, "")
except Exception as _e:                 # noqa: BLE001 - the crash IS the finding
    _empty_verb = "RAISED %s" % type(_e).__name__
check("A-159: an empty line is not a verb, and does not IndexError",
      _empty_verb is False, repr(_empty_verb))
_pc = FakeClient()
_pp2 = fb.TelegramPoller(_pc, {"1"})
check("A-160: /help@MyBot is the help command it looks like",
      _pp2._verb(1, "/help@MyBot") is True and "tinycmdr" in _pc.messages[-1]["text"],
      [m["text"][:20] for m in _pc.messages])


# A-161: the "no text here" notice is once per chat, not once per photo.
_pn = FakeClient()
_pn2 = fb.TelegramPoller(_pn, {"7"})


def _media(mid):
    return {"update_id": mid, "message": {
        "message_id": mid, "chat": {"id": 61, "type": "private"},
        "from": {"id": 7, "username": "u"}}}


for _i in range(3):
    _pn2.handle(_media(_i + 1))
check("A-161: three media messages draw ONE notice, not three",
      len([m for m in _pn.messages if "read text" in m["text"]]) == 1,
      [m["text"][:30] for m in _pn.messages])


# A-162: a non-string text must not kill the update.
_pm = FakeClient()
_pm2 = fb.TelegramPoller(_pm, {"7"})
_pm_got = []
_pm2.submit = lambda cid, text, mid: _pm_got.append((cid, text, mid))
try:
    _pm_handled = _pm2.handle({"update_id": 1, "message": {
        "message_id": 1, "text": 123, "chat": {"id": 62, "type": "private"},
        "from": {"id": 7, "username": "u"}}})
except Exception as _e:                 # noqa: BLE001 - the crash IS the finding
    _pm_handled = "RAISED %s" % type(_e).__name__
check("A-162: a malformed non-string text is handled, not swallowed",
      _pm_handled is True and _pm_got == [(62, "123", 1)], (_pm_handled, _pm_got))


# A-163/A-164: the log lines an operator actually reads.
_cap = _CaptureLog()
fb.log.addHandler(_cap)
fb.log.setLevel(_logging.DEBUG)
try:
    _pl = FakeClient()
    _pl2 = fb.TelegramPoller(_pl, {"7"})
    _pl2.handle({"update_id": 1, "message": {
        "message_id": 1, "text": "hi", "chat": {"id": 63}, "from": {"id": 7}}})
    _pl2.handle({"update_id": 2, "callback_query": {
        "id": "cb1", "from": {"id": 7, "username": "u"}, "data": "opt:1"}})
finally:
    fb.log.removeHandler(_cap)
_typelog = [ln for ln in _cap.lines if "DM only" in ln]
_btnlog = [ln for ln in _cap.lines if "button press" in ln]
check("A-163: a message with no chat.type is logged as such, not 'a None chat'",
      bool(_typelog) and "chat.type" in _typelog[-1], _typelog[-1:])
check("A-164: a press with no chat is not blamed on the (allowed) user",
      bool(_btnlog) and "no chat" in _btnlog[-1], _btnlog[-1:])


# A-166: the dedupe window is per chat.
_p166 = fb.TelegramPoller(FakeClient(), {"11", "22"})
_handed166 = []
_p166.submit = lambda cid, text, mid: _handed166.append((cid, mid))
for _i in range(1, 4002):
    _p166.handle(msg(11, "x", 11, mid=_i))
_p166.handle(msg(22, "hello", 22, mid=1))
for _i in range(4002, 8003):
    _p166.handle(msg(11, "x", 11, mid=_i))
_after_b = len(_handed166)
_p166.handle(msg(22, "hello", 22, mid=1))          # redelivery after 4000 chat-A ids
check("A-166: a busy chat cannot evict another chat's dedupe history",
      len(_handed166) == _after_b, (len(_handed166), _after_b))


# A-171: a redelivered /stop must bite again.
_p171 = fb.TelegramPoller(FakeClient(), {"7"})
_p171.submit = lambda *a: None
_ev171 = threading.Event()
_p171.cancel[7] = _ev171
_p171.handle(msg(7, "/stop", 7, mid=1))
_first171 = _ev171.is_set()
_ev171.clear()
_p171.handle(msg(7, "/stop", 7, mid=1))
check("A-171: a redelivered /stop still stops the run",
      _first171 and _ev171.is_set(), (_first171, _ev171.is_set()))


# A-172: /new resets the visible progress message too.
_p172 = fb.TelegramPoller(FakeClient(), {"7"})
_d172 = fb.TelegramDestination(FakeClient(), chat_id=7, session_key="telegram-7")
_d172.live_id = 42
_d172.lines = [("tool", "old step")]
_p172.live[7] = _d172
_p172.handle(msg(7, "/new", 7, mid=1))
check("A-172: /new clears the previous conversation's steps from the live message",
      _d172.lines == [], list(_d172.lines))


# A-173: the help screen lists every verb, and the prefix.
check("A-173: the help screen names /start and the /tinycmdr prefix",
      "/start" in fb.TG_HELP and "/tinycmdr" in fb.TG_HELP, fb.TG_HELP[:60])


# A-170: a failed steer receipt must not lose the update.
class _NoReceiptClient(FakeClient):
    def send(self, *a, **k):
        raise RuntimeError("Too Many Requests")


class _Ctrl:
    def __init__(self):
        self.got = None

    def steer(self, sender, text):
        self.got = (sender, text)


_p170 = fb.TelegramPoller(_NoReceiptClient(), {"7"})
_ctrl170 = _Ctrl()
_saved_ctrl = fb.RUNS.control
fb.RUNS.control = lambda key: _ctrl170
try:
    try:
        _steer170 = _p170.steer(7, "please hurry", "7")
    except Exception as _e:             # noqa: BLE001 - the crash IS the finding
        _steer170 = "RAISED %s" % type(_e).__name__
finally:
    fb.RUNS.control = _saved_ctrl
check("A-170: a steer whose receipt fails is still steered",
      _steer170 is True and _ctrl170.got == ("7", "please hurry"),
      (_steer170, _ctrl170.got))


# A-158: telegram.http_timeout is a knob, not a code edit.
_saved_ht = fb.CONFIG["telegram"].get("http_timeout")
try:
    fb.CONFIG["telegram"]["http_timeout"] = 7
    check("A-158: telegram.http_timeout bounds a non-poll call",
          fb.TelegramClient("1:x").http_timeout == 7,
          fb.TelegramClient("1:x").http_timeout)
finally:
    if _saved_ht is None:
        fb.CONFIG["telegram"].pop("http_timeout", None)
    else:
        fb.CONFIG["telegram"]["http_timeout"] = _saved_ht


# ---- run_telegram-level checks: the lane's own gating, state and lifecycle ----------
_TG_TOK = "12345:" + "A" * 35


class _OnePollClient(FakeClient):
    """Poll 1 yields `batch`; poll 2 leaves the lane (SystemExit is not an Exception)."""

    batch = []                              # a class attribute: an instance would shadow it

    def __init__(self, token):
        super().__init__()
        self.polls = 0
        self.offsets = []

    def updates(self, offset):
        self.offsets.append(offset)
        self.polls += 1
        if self.polls == 1:
            return list(self.batch)
        raise SystemExit


class _NoPollClient(FakeClient):
    """The API never answers a poll: the first call leaves the lane."""

    def __init__(self, token):
        super().__init__()

    def updates(self, offset):
        raise SystemExit


def _tg_setup(client_cls, config, poller_cls=None, **stubs):
    """Patch the lane for a run_telegram() drive. Returns (client_holder, restore).

    `restore` is explicit because a worker thread keeps calling drive_run AFTER
    run_telegram() has returned (the poll loop leaves the lane while a run is still
    held), and the fake must outlive the lane for that.
    """
    _names = ("TelegramClient", "TelegramPoller", "lane_up", "REPORTER",
              "announce_startup", "drive_run", "TG_IDLE_SECONDS")
    _saved = {n: getattr(fb, n, None) for n in _names}
    _sched = fb.SCHEDULER.dispatcher
    _cfg = fb.CONFIG["telegram"]
    _holder = {}

    class _Cap(client_cls):
        def __init__(self, token):
            super().__init__(token)
            _holder["client"] = self

    fb.CONFIG["telegram"] = dict(config)
    fb.TelegramClient = _Cap
    fb.lane_up = lambda lane, detail="": None
    fb.SCHEDULER.dispatcher = None
    fb.REPORTER = None
    fb.announce_startup = lambda d: None
    if poller_cls is not None:
        fb.TelegramPoller = poller_cls
    for _k, _v in stubs.items():
        setattr(fb, _k, _v)

    def _restore():
        for _n in _names:
            setattr(fb, _n, _saved[_n])
        fb.SCHEDULER.dispatcher = _sched
        fb.CONFIG["telegram"] = _cfg

    return _holder, _restore


def _run_tg(client_cls, config, poller_cls=None, **stubs):
    """(outcome, client, lane_up_details) for one run_telegram() with the API faked."""
    _holder, _restore = _tg_setup(client_cls, config, poller_cls, **stubs)
    _ups = []
    fb.lane_up = lambda lane, detail="": _ups.append(detail)
    try:
        try:
            fb.run_telegram()
            _out = ("returned", None)
        except SystemExit as _e:
            _out = ("exit", _e.code)
        except Exception as _e:                     # noqa: BLE001 - the class IS the check
            _out = ("raised", type(_e).__name__)
    finally:
        _restore()
    return _out, _holder.get("client"), _ups


# A-154: getMe answering `result: null` must not name the lane "@None".
class _NullMeClient(_OnePollClient):
    def me(self):
        return {}


_out154, _cl154, _ups154 = _run_tg(_NullMeClient, {"token": _TG_TOK, "allowed_users": ["7"]})
check("A-154: a null getMe reports @unknown, not @None",
      bool(_ups154) and all("@None" not in d for d in _ups154)
      and any("unknown" in d for d in _ups154), _ups154)

# A-178: lane_up waits for the API to answer a poll.
_out178, _cl178, _ups178 = _run_tg(_NoPollClient, {"token": _TG_TOK, "allowed_users": ["7"]})
check("A-178: a lane that dies on poll 1 is not reported 'up'",
      _ups178 == [], _ups178)

# A-176: a captive portal's getMe is a network problem, not a refused token.
class _PortalClient(_NoPollClient):
    def me(self):
        raise RuntimeError("getMe: HTTP 204 and no JSON")


_out176, _cl176, _ups176 = _run_tg(_PortalClient, {"token": _TG_TOK, "allowed_users": ["7"]})
check("A-176: a captive-portal getMe is raised for the retry, not exit 2",
      _out176 == ("raised", "RuntimeError"), _out176)

# A-177: a token revoked mid-run stops the lane instead of polling for ever.
class _RevokedClient(_OnePollClient):
    def updates(self, offset):
        self.offsets.append(offset)
        self.polls += 1
        if self.polls >= 2:
            raise SystemExit(9)
        raise RuntimeError("getUpdates: Unauthorized (HTTP 401)")


_out177, _cl177, _ups177 = _run_tg(_RevokedClient, {"token": _TG_TOK, "allowed_users": ["7"]})
check("A-177: a mid-run refusal ends the lane at once",
      _out177 == ("exit", 2) and _cl177.polls == 1, (_out177, _cl177.polls))

# A-174: a non-numeric allowed id refuses at startup.
_out174, _cl174, _ups174 = _run_tg(_OnePollClient, {"token": _TG_TOK,
                                                    "allowed_users": ["@david"]})
check("A-174: a non-numeric allowed_users id refuses the lane at startup",
      _out174 == ("exit", 2), _out174)

_saved_tg174 = dict(fb.CONFIG.get("telegram") or {})
_saved_mm174 = dict(fb.CONFIG["mattermost"])
_saved_cp174 = fb.CONFIG_PATH
_cfgdir174 = Path(_tempfile.mkdtemp(prefix="tg-doctor-"))
_env174 = {k: os.environ.pop(k) for k in ("TINYCMDR_MM_TOKEN", "TINYCMDR_TG_TOKEN")
           if k in os.environ}
try:
    # validate_startup_config refuses before it reads anything else when there is no
    # config.json, so the rule is graded against a staged one - the same reason
    # tests/hermetic.py exists. A clean clone has no config.json in the tree.
    _staged174 = _cfgdir174 / "config.json"
    _staged174.write_text("{}", encoding="utf-8")
    fb.CONFIG_PATH = _staged174
    fb.CONFIG["telegram"] = {"token": _TG_TOK, "allowed_users": ["@david"]}
    fb.CONFIG["mattermost"]["token"] = ""
    _msg174 = fb.validate_startup_config()
    check("A-174: ...and doctor catches it before the lane even starts",
          bool(_msg174) and "NUMERIC" in _msg174, _msg174)
finally:
    fb.CONFIG_PATH = _saved_cp174
    shutil.rmtree(_cfgdir174, ignore_errors=True)
    fb.CONFIG["telegram"].clear()
    fb.CONFIG["telegram"].update(_saved_tg174)
    fb.CONFIG["mattermost"].clear()
    fb.CONFIG["mattermost"].update(_saved_mm174)
    os.environ.update(_env174)

# A-165: the update offset survives a restart, so the backlog is not replayed.
_saved_off = fb._state().get("telegram_offset")


def _clear_offset():
    fb._state(lambda st: st.pop("telegram_offset", None))


class _HelpOnceClient(_OnePollClient):
    batch = [msg(7, "/help", 7, mid=5)]


try:
    _clear_offset()
    _out_a, _cla, _ = _run_tg(_HelpOnceClient, {"token": _TG_TOK, "allowed_users": ["7"]})
    _out_b, _clb, _ = _run_tg(_HelpOnceClient, {"token": _TG_TOK, "allowed_users": ["7"]})
    check("A-165: a restart resumes at the last acknowledged update",
          _out_a == ("exit", None) and _clb is not None and _clb.offsets[:1] == [6],
          (None if _cla is None else _cla.offsets, None if _clb is None else _clb.offsets))
finally:
    if _saved_off is None:
        _clear_offset()
    else:
        fb._state(lambda st: st.__setitem__("telegram_offset", _saved_off))


# A-167/A-168: a run queued behind a stopped one must not inherit its stop.
_qbox = {}


class _CapPoller(fb.TelegramPoller):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _qbox["poller"] = self


class _TwoMsgsClient(_OnePollClient):
    batch = [msg(7, "first", 7, mid=1), msg(7, "second", 7, mid=2)]


_runs167 = []
_stop_seen = threading.Event()


def _hold_drive(key, text, reporter, **kw):
    _runs167.append((text, kw.get("cancel_event")))
    if len(_runs167) == 1:
        _stop_seen.wait(5)
    return "ok"


try:
    _h167, _restore167 = _tg_setup(_TwoMsgsClient, {"token": _TG_TOK, "allowed_users": ["7"]},
                                   poller_cls=_CapPoller, drive_run=_hold_drive,
                                   TG_IDLE_SECONDS=5.0)
    try:
        fb.run_telegram()
    except SystemExit:
        pass
    _dl = time.time() + 5
    while len(_runs167) < 1 and time.time() < _dl:
        time.sleep(0.02)
    _p167 = _qbox.get("poller")
    _ev1 = _p167.cancel.get(7) if _p167 else None
    if _ev1 is not None:
        _ev1.set()
    _stop_seen.set()
    _dl = time.time() + 5
    while len(_runs167) < 2 and time.time() < _dl:
        time.sleep(0.02)
    check("A-167/168: a queued run does not start already stopped",
          len(_runs167) == 2 and _runs167[1][1] is not None
          and not _runs167[1][1].is_set(),
          [(t, None if e is None else e.is_set()) for t, e in _runs167])
finally:
    _stop_seen.set()
    _restore167()


# A-169: a burst of DMs in one chat is capped, with a notice.
_qbox2 = {}


class _CapPoller2(fb.TelegramPoller):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _qbox2["poller"] = self


_runs169 = []
_gate169 = threading.Event()


def _gate_drive(key, text, reporter, **kw):
    _runs169.append(text)
    _gate169.wait(5)
    return "ok"


try:
    _h169, _restore169 = _tg_setup(_NoPollClient, {"token": _TG_TOK, "allowed_users": ["7"]},
                                   poller_cls=_CapPoller2, drive_run=_gate_drive,
                                   TG_IDLE_SECONDS=5.0)
    try:
        fb.run_telegram()
    except SystemExit:
        pass
    _cl169 = _h169.get("client")
    _p169 = _qbox2.get("poller")
    _cap169 = getattr(fb, "TG_QUEUE_MAX", 5)
    _p169.submit(7, "m0", 0)
    _dl = time.time() + 5
    while not _runs169 and time.time() < _dl:
        time.sleep(0.02)
    for _i in range(1, _cap169 + 2):
        _p169.submit(7, "m%d" % _i, _i)
    check("A-169: a burst of DMs is capped with a notice in the chat",
          any("already working through" in m["text"] for m in _cl169.messages),
          [m["text"][:40] for m in _cl169.messages])
finally:
    _gate169.set()
    _restore169()


print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
