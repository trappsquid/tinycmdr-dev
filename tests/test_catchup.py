"""The catch-up sweep: a message posted while the bot was DOWN.

Run:  python tests/test_catchup.py            (all tests)
      python tests/test_catchup.py <substring> (one test)

Why this suite exists. The sweep's own docstring names the case it is for - "a message
posted during a gap (or while the bot was restarting) is silently ignored" - but it walks
the in-memory high-water map, and every new process starts with an empty one. So it had
no channel to ask about until something arrived over the websocket, which is exactly what
a post made inside a downtime never does. Measured on the Windows test box and the MacBook
2026-09-24: a restart was armed, the child was killed, an order was posted while it was
down, and no run started, no answer was posted and nothing was logged. The operator's
opinion of that is "the bot ate my message".

It runs against a fake dispatcher and a fake driver, like tests/test_ask_user.py: no
network, no model, no ledger.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-catchup"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
FIXTURE = Path(__file__).resolve().parent / "fixture-config.json"
if not FIXTURE.exists():
    sys.exit(f"missing test fixture: {FIXTURE}")
shutil.copy2(FIXTURE, STAGE / "config.json")

spec = importlib.util.spec_from_file_location("tinycmdr_under_test_catchup",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_under_test_catchup"] = fb
spec.loader.exec_module(fb)

STATE = STAGE / "state.json"
fb.GLOBAL_STATE_FILE = STATE
fb.CONFIG["agent"]["catch_up_seconds"] = 60
fb.CONFIG["agent"]["catch_up_max_minutes"] = 30
fb.CONFIG["mattermost"]["allowed_users"] = ["david"]

PASSES, FAILURES = [], []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"ok   {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


class FakeMessage:
    def __init__(self, channel_id, text, sender="david", user_id="u-david",
                 create_at=None, msg_id=None):
        self.channel_id = channel_id
        self.sender_name = sender
        self.user_id = user_id
        self.create_at = int((create_at or time.time()) * 1000)
        self.message = text
        self.id = msg_id or f"post-{self.create_at}"


class FakeThread:
    def __init__(self):
        self.calls = []
        self.posts = {"chan-A": [{"id": "p-down", "channel_id": "chan-A",
                                  "message": "an order posted while the "
                                             "bot was down",
                                  "user_id": "david",
                                  "create_at": int((time.time() - 30) * 1000)}]}

    def get_posts_for_channel(self, channel_id, params=None):
        self.calls.append((channel_id, dict(params or {})))
        posts = [p for p in self.posts.get(channel_id, [])
                 if p["create_at"] >= int((params or {}).get("since") or 0)]
        return {"order": [p["id"] for p in posts],
                "posts": {p["id"]: p for p in posts}}


class FakeDriver:
    def __init__(self):
        self.posts = FakeThread()
        self.users = self

    def get_user_by_username(self, name):
        return {"id": "u-bot", "username": name}


class FakeDispatcher(fb.MattermostDispatcher):
    """No network, no posting: records what the sweep handed to the normal path."""

    def __init__(self):
        super().__init__()
        self.handled = []

    def _post(self, channel_id, root_id, text, color=None):
        return "post"

    def _handle(self, channel_id, sender, text, msg_id, thread_root, is_dm, gen=0):
        self.handled.append((channel_id, text))


def write_state(blob):
    STATE.write_text(json.dumps(blob), encoding="utf-8")


def test_a_restart_restores_the_channels_the_sweep_must_ask_about():
    write_state({"last_seen": {"chan-A": 1000.0},
                 "announce_restart": {"channel_id": "chan-B", "at": 2000.0}})
    d = FakeDispatcher()
    check("restart: the carried channel comes back with its time",
          d.last_seen.get("chan-A") == 1000.0, d.last_seen)
    check("restart: the channel that ASKED for the restart is a floor too",
          d.last_seen.get("chan-B") == 2000.0, d.last_seen)


def test_a_fresh_process_with_no_state_sweeps_nothing():
    write_state({})
    d = FakeDispatcher()
    check("fresh process: nothing remembered", d.last_seen == {}, d.last_seen)


def test_the_high_water_mark_is_persisted_and_the_map_is_bounded():
    write_state({})
    d = FakeDispatcher()
    d.enqueue(FakeMessage("chan-A", "hello", create_at=5000.0), "hello")
    on_disk = json.loads(STATE.read_text(encoding="utf-8"))
    check("enqueue: the channel is persisted",
          on_disk.get("last_seen", {}).get("chan-A") == 5000.0, on_disk)
    for i in range(25):
        d.enqueue(FakeMessage("chan-%02d" % i, "hi", create_at=6000.0 + i), "hi")
    seen = json.loads(STATE.read_text(encoding="utf-8")).get("last_seen", {})
    check("enqueue: the map stays bounded", len(seen) <= 20, len(seen))
    newest = max(seen, key=lambda k: seen[k])
    check("enqueue: the newest channel is kept", newest == "chan-24", newest)


def test_the_sweep_recovers_a_post_made_while_the_process_was_down():
    base = time.time() - 300          # fixed, so the millisecond assertion is exact
    write_state({"last_seen": {"chan-A": base}})
    d = FakeDispatcher()
    d.driver = FakeDriver()
    d.bot_user_id = "u-bot"
    d._is_dm = lambda channel_id: True          # no channel lookup over the network
    recovered = d._catch_up_once()
    check("recovery: the missed post is recovered", recovered == 1, recovered)
    check("recovery: it goes through the normal path",
          d.handled and d.handled[0][0] == "chan-A", d.handled)
    asked = d.driver.posts.calls[-1][1]
    check("recovery: the query starts one millisecond after the high-water mark",
          asked.get("since") == int(base * 1000) + 1, asked)


def test_the_first_sweep_runs_before_the_first_sleep():
    """A restart is the case this sweep exists for: waiting a full interval first
    leaves the order that arrived during the downtime unanswered for a minute more."""
    write_state({"last_seen": {"chan-A": time.time() - 120}})
    d = FakeDispatcher()
    d.driver = FakeDriver()
    d.bot_user_id = "u-bot"
    order = []

    class _Stop(Exception):
        pass

    def fake_sleep(_secs):
        order.append("sleep")
        raise _Stop()

    real_sleep, real_once, real_flag = fb.time.sleep, d._catch_up_once, d.last_seen
    fb.time.sleep = fake_sleep
    d._catch_up_once = lambda *a, **k: (order.append("sweep"), 0)[1]
    try:
        t = threading.Thread(target=lambda: _run(d, _Stop), daemon=True)
        t.start()
        t.join(5)
    finally:
        fb.time.sleep = real_sleep
        d._catch_up_once = real_once
    check("startup: the sweep runs before the loop sleeps",
          order[:2] == ["sweep", "sleep"], order)

    d.last_seen = {}
    order.clear()
    fb.time.sleep = fake_sleep
    d._catch_up_once = lambda *a, **k: (order.append("sweep"), 0)[1]
    try:
        t = threading.Thread(target=lambda: _run(d, _Stop), daemon=True)
        t.start()
        t.join(5)
    finally:
        fb.time.sleep = real_sleep
        d._catch_up_once = real_once
        d.last_seen = real_flag
    check("startup: a process with nothing restored queries nothing",
          order and order[0] == "sleep", order)



def test_an_already_answered_post_is_not_recovered_after_a_restart():
    """The high-water mark is a CLOCK (the live message object carries no create_at),
    and this box's clock runs behind the Mattermost server's - measured 334 ms on the
    day this was found, by watching an already-answered order come back as new work
    after a restart. Ids are the exact boundary; carry them."""
    base = time.time() - 60
    write_state({"last_seen": {"chan-A": base}, "seen_ids": ["p-down", "p-older"]})
    d = FakeDispatcher()
    check("restart: the handled ids come back",
          list(d.seen)[-2:] == ["p-down", "p-older"], list(d.seen))
    d.driver = FakeDriver()
    d.bot_user_id = "u-bot"
    d._is_dm = lambda channel_id: True
    check("restart: the already-answered post is not recovered again",
          d._catch_up_once() == 0, d.handled)
    d.enqueue(FakeMessage("chan-A", "a new order", msg_id="p-new"), "a new order")
    ids = json.loads(STATE.read_text(encoding="utf-8")).get("seen_ids")
    check("enqueue: the handled id is persisted", ids and ids[-1] == "p-new", ids)
    check("enqueue: the ids stay bounded", len(ids) <= 50, len(ids))


def test_the_dedupe_set_does_not_grow_without_bound():
    write_state({"seen_ids": ["p%d" % i for i in range(200)]})
    d = FakeDispatcher()
    check("restore: at most 50 ids are carried", len(d.seen) <= 50, len(d.seen))

def _run(d, stop):
    try:
        d._catch_up_loop()
    except stop:
        pass
    except Exception as e:                       # noqa: BLE001 - reported by the check
        print("   (loop ended: %r)" % (e,))


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
            FAILURES.append(f"{t.__name__} raised: {e}")
            traceback.print_exc()
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print("  FAIL:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
