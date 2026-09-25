"""The Telegram lane, graded without a network.

    python tests/test_telegram.py

Everything the lane promises has to hold against a fake Bot API: the HTML escaping
that survives a model's own output, the 4096 split that never truncates, one growing
message instead of a wall of notifications, the answer as its own message, the gate
that ignores strangers, and the two ways a question gets answered (a button and a
typed line), both landing in the same slot the reporter reads.
"""
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
check("a bold tag in the model's own text cannot inject",
      fb.tg_escape("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;")

long_text = ("paragraph one " * 200) + "\n\n" + ("paragraph two " * 200)
pieces = fb.tg_split(long_text)
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
      all(len(p) <= fb.TG_MAX for p in fb.tg_split("x" * 9000)))

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
check("...and the answer lands in the slot the reporter reads", box.get("answer") == "2")

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

# ---- the token has ONE home, and both doors never fight silently -----------
# (audit, 2026-09-22: telegram.token was read from config.json, which contradicts
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
try:
    fb.CONFIG["telegram"]["token"] = "tg-token-here"
    fb.CONFIG["mattermost"]["token"] = "mm-token-here"
    note = fb.both_doors_note()
    check("both doors set is a STARTUP warning, not silence",
          "Mattermost wins" in note, note[:120])
    fb.CONFIG["mattermost"]["token"] = ""
    check("one door is not a warning", fb.both_doors_note() == "")
    fb.CONFIG["mattermost"]["token"] = "mm-token-here"
    fb.CONFIG["telegram"]["token"] = ""
    check("and neither is none", fb.both_doors_note() == "")
finally:
    fb.CONFIG["telegram"].update(_saved[0])
    fb.CONFIG["mattermost"].update(_saved[1])


print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
