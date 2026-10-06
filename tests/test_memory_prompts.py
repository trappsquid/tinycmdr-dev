"""Memory promotion: event-driven, dismissible, and never a nag.

was made at WRITE TIME - the moment a fact is freshest, which correlates with the effort
just spent, not with future value - and only for an order whose wording reads as a
"where is X" question. So the publishable got saved and the load-bearing did not, and
nothing anywhere ever said so. The evidence a fact IS durable is that it cost a lookup
AGAIN, so the offer is event-driven instead: a run that spent hand-driven calls and saved
nothing is asked once per session, the ask is SAVE OR DISMISS, and a dismissal is
remembered for that shape (the order's own words, the census's overlap rule) so it is
never repeated. A bare "dismiss" is a control order: the harness records it and answers
it without a model call.

    python tests/test_memory_prompts.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / (os.environ.get("TINYCMDR_TEST_APP")
              or os.environ.get("TINYCMDR_SRC") or "tinycmdr.py")
# A STAGED copy: the module writes into BASE_DIR while it is being imported.
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-memprompts"
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_memprompts_under_test",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_memprompts_under_test"] = fb
spec.loader.exec_module(fb)

TMP = Path(tempfile.mkdtemp(prefix="fbmemprompts-"))
sys.path.insert(0, str(BASE / "tests"))
import hermetic                                                          # noqa: E402
hermetic.redirect_repo_files(fb, TMP)
fb._CENSUS_FORCE = True            # this suite is the census's test, not the running bot

PASSES, FAILS = [], []


def check(name, cond, detail=""):
    (PASSES if cond else FAILS).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


class _Rep:
    def __init__(self):
        self.lines = []

    def say(self, text):
        self.lines.append(text)


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def text_reply(text):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def install_stub(fb, seen):
    def fake_post(url, headers, payload, timeout, grace, cancel_event=None, stream=False):
        seen.append(json.loads(json.dumps(payload)))
        return _FakeResp(text_reply("(the model answered)"))
    fb._post_watchdog = fake_post


def _seed(session, words, calls=5, **extra):
    st = fb.run_state(session, create=True)
    st["calls_by"] = {"shell": calls}
    st["hand_at_start"] = 0
    st["order_words"] = words
    st["order_sample"] = " ".join(words)
    st.update(extra)
    return st


SHAPE = ["burn", "the", "dvd", "and", "verify", "it"]
OTHER = ["drain", "the", "mail", "queue", "and", "report"]
RUN_SHAPE = ["encode", "the", "dvd", "title", "set", "now"]

# getattr so a build WITHOUT the promotion path fails these checks instead of crashing the
# suite: falsification is a FAIL summary, not a traceback (test_route_hint's rule).
remember_offer = getattr(fb, "remember_offer", lambda *a, **k: "")
remember_dismiss_order = getattr(fb, "remember_dismiss_order", lambda *a, **k: "")
offer_after_run = getattr(fb, "offer_after_run", lambda *a, **k: "")

# ---- the offer is EVENT-driven ---------------------------------------------------------
_rep = _Rep()
_seed("mp-1", SHAPE)
line = remember_offer("mp-1", _rep, source="main", trigger="event")
check("a run that spent hand calls and saved nothing is asked to save or dismiss",
      "save it" in line and "dismiss" in line, line[:200])
check("...and the operator saw exactly one line", _rep.lines == [line], _rep.lines)
check("...once per session", remember_offer("mp-1", _rep, source="main", trigger="event") == "")
_seed("mp-2", SHAPE, calls=9, remembered=1)
check("nothing is asked of a run that saved something",
      remember_offer("mp-2", None, source="main", trigger="event") == "")
_seed("mp-3", SHAPE, calls=9)
check("a sub-agent's run never gets the offer",
      remember_offer("mp-3", None, source="child") == "")
_seed("mp-4", SHAPE, calls=1)
check("a run under the threshold is not asked",
      remember_offer("mp-4", None, source="main", trigger="event") == "")
_seed("mp-5", ["which", "port", "the", "web", "ui", "listens"], calls=2, order_is_lookup=1)
check("...but a small run that ANSWERED a lookup still is (the old trigger kept)",
      "save it" in remember_offer("mp-5", None, source="main"))
check("the offer is a config lever",
      fb.DEFAULT_CONFIG["agent"].get("remember_offer") is True
      and int(fb.DEFAULT_CONFIG["agent"].get("remember_offer_steps")) == 4)

# ---- the dismissal sticks -------------------------------------------------------------
ans = remember_dismiss_order("mp-1", "dismiss")
check("a bare dismiss is recorded and answered", "won't offer" in ans, ans[:120])
check("...and only the bare word is a dismissal",
      remember_dismiss_order("mp-1", "dismiss the janitor at noon") == "")
_seed("mp-6", SHAPE, calls=9)
check("the dismissed shape is never offered again, in a later session",
      remember_offer("mp-6", None, source="main", trigger="event") == "")
_seed("mp-7", OTHER, calls=9)
check("a different shape is still offered",
      "save it" in remember_offer("mp-7", None, source="main", trigger="event"))

# ---- the run answers a dismissal without a model call ---------------------------------
seen = []
install_stub(fb, seen)
_seed("mp-run", RUN_SHAPE)
check("the offer is pending for the run's session",
      bool(remember_offer("mp-run", _Rep(), source="main", trigger="event")))
got = fb.AGENT.run("mp-run", "dismiss")
check("AGENT.run answers a pending dismiss with the harness line",
      "won't offer" in got, got[:160])
check("...and made NO model call for it", seen == [], len(seen))
got2 = fb.AGENT.run("mp-nobody", "dismiss")
check("with no pending offer the word is an ordinary order (the model is asked)",
      seen != [], (got2[:80], len(seen)))

# ---- one offer per run, in priority order ---------------------------------------------
_seed("mp-pri", ["burn", "the", "dvd", "and", "verify", "it"], calls=9, order_repeats=3)
_pri = offer_after_run("mp-pri", _Rep(), source="main")
check("a repeated ORDER still gets the mint offer, never the memory ask",
      "mint it" in _pri and "save it" not in _pri, _pri[:180])
_seed("mp-pri2", OTHER, calls=9)
_pri2 = offer_after_run("mp-pri2", _Rep(), source="main")
check("...while an ordinary long run gets the save-or-dismiss ask",
      "save it" in _pri2 and "dismiss" in _pri2, _pri2[:180])

print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
