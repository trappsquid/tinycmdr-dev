"""Secrets are scrubbed (audit, 2026-09-22).

The review found the secret sweep covering environment variables only; the real hole was
that it never scrubbed the primary llm.api_key, which is the key a hosted endpoint keeps in
config.json and the one in use on every call. This suite grades the sweep itself, on the
code's own terms:

    python tests/test_scrub.py
"""
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_scrub_under_test", SRC)
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_scrub_under_test"] = fb
spec.loader.exec_module(fb)

PASSES = []
FAILS = []


def check(name, cond, detail=""):
    (PASSES if cond else FAILS).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


KEY = "sk-live-0123456789abcdef"
FBKEY = "sk-backup-fedcba9876543210"
MM = "mm-bot-token-abcdefghijklmnop"
SAVED_SECRETS = fb._SECRETS
SAVED_CFG = fb.CONFIG
try:
    fb.CONFIG["llm"] = dict(fb.CONFIG["llm"])
    fb.CONFIG["llm"]["api_key"] = KEY
    fb.CONFIG["llm"]["fallbacks"] = [{"base_url": "https://example.invalid/v1",
                                      "model": "x", "api_key": FBKEY}]
    fb.CONFIG["mattermost"] = dict(fb.CONFIG["mattermost"])
    fb.CONFIG["mattermost"]["token"] = MM
    os.environ["TINYCMDR_ENV_TOKEN"] = "env-token-abcdefghijkl"
    fb._SECRETS = fb._secret_values()

    out = fb.scrub("the endpoint answered with key " + KEY)
    check("the PRIMARY llm.api_key is redacted", KEY not in out and "«redacted»" in out, out)
    # This build may have no fallback endpoints at all; ask the source rather than the
    # config, which the suite has just written.
    if 'vals.add(fb["api_key"])' in SRC.read_text(encoding="utf-8", errors="replace"):
        out = fb.scrub("failover used " + FBKEY)
        check("a fallback api_key is still redacted", FBKEY not in out, out)
    else:
        print("skip a fallback key check (this build has no fallback endpoints)")
    out = fb.scrub("token " + MM)
    check("the chat token is still redacted", MM not in out, out)
    out = fb.scrub("env token env-token-abcdefghijkl")
    check("an environment secret is redacted", "env-token-abcdefghijkl" not in out, out)

    # A name ending in PASSWORD/PASSWD is a credential at ANY length (floor 6, the rule
    # the config-side sweep already uses). The 12-char floor skipped this install's
    # 10-char SUDO_PASSWORD - a secret missing from _SECRETS is one that reaches the
    # transcript, the log and the chat.
    os.environ["TINYCMDR_TEST_SUDO_PASSWORD"] = "pwabcd1234"
    os.environ["TINYCMDR_TEST_TOO_SHORT_PASSWD"] = "abc"
    fb._SECRETS = fb._secret_values()
    out = fb.scrub("sudo said pwabcd1234 and meant it")
    check("a 10-char *_PASSWORD environment value is redacted",
          "pwabcd1234" not in out, out)
    check("and a 3-char one is still below the floor", "abc" not in fb._SECRETS)

    # the regression the sweep's own comment records: a looser name test swept PATH out
    # of ordinary log lines, so a path line must come through untouched
    probe = "PATH entry C:\\Windows\\System32 and C:\\Program Files\\Python312"
    check("an ordinary path line is left alone", fb.scrub(probe) == probe,
          fb.scrub(probe))
    check("a short value is not treated as a secret",
          fb.scrub("code 1234") == "code 1234")

    logged = fb.scrub("wrote " + KEY + " to the log")
    check("what the log filter would carry is masked too", KEY not in logged, logged)
finally:
    fb._SECRETS = SAVED_SECRETS
    fb.CONFIG = SAVED_CFG
    os.environ.pop("TINYCMDR_ENV_TOKEN", None)
    os.environ.pop("TINYCMDR_TEST_SUDO_PASSWORD", None)
    os.environ.pop("TINYCMDR_TEST_TOO_SHORT_PASSWD", None)

# ---- F-08: a key added after import is swept too -----------------------------
# _SECRETS was frozen at import, so `config set llm.api_key` (the guard skips the llm
# section, since llm keys live in config.json) took effect while the sweep still held
# the import-time set - the new key reached the transcript, the log and the chat
# (audit, 2026-09-29). Drive the real verb against a temp config.json.
import contextlib
import io
import shutil
import tempfile

NEWKEY = "sk-added-after-start-9876543210"
_saved_path = fb.CONFIG_PATH
_saved_cfg = dict(fb.CONFIG)
_saved_secrets = fb._SECRETS
_saved_source = dict(fb.CONFIG_SOURCE)
_saved_running = fb._verb_running
_stage = Path(tempfile.mkdtemp(prefix="tinycmdr-scrub-"))
try:
    fb.CONFIG_PATH = _stage / "config.json"
    fb.CONFIG_PATH.write_text("{}", encoding="utf-8")
    fb._verb_running = lambda: False        # the verb only prints its restart note
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = fb._verb_config(["set", "llm.api_key", NEWKEY])
    check("config set llm.api_key takes effect through the real verb", rc == 0, rc)
    out = fb.scrub("the new endpoint key is " + NEWKEY)
    check("...and the new key is masked without re-binding _SECRETS by hand",
          NEWKEY not in out and "«redacted»" in out, out)
finally:
    fb.CONFIG_PATH = _saved_path
    fb._verb_running = _saved_running
    fb._SECRETS = _saved_secrets
    fb.CONFIG = _saved_cfg
    fb.CONFIG_SOURCE.update(_saved_source)
    shutil.rmtree(_stage, ignore_errors=True)

# ---- BUGREPORT §M4: a 401 body that echoes the key ---------------------------
# Measured: a provider that echoes the request's Authorization header in its error body
# put the live key into the fatal notes, the run's return value, the log and the chat.
# (Earlier checks in this suite restore _SECRETS to the import-time set, which does not
# hold KEY, so seed it here - the sweep is what scrub() masks, and that is the point.)
_prev_secrets = fb._SECRETS
fb._SECRETS = set(fb._SECRETS) | {KEY}


class _Echo401:
    class response:
        status_code = 401
        text = ('{"error": {"message": "invalid api key: Bearer %s"}}' % KEY)


def _echo_exc():
    e = Exception("401")
    e.response = _Echo401.response
    return e


try:
    _body = fb._http_body(_echo_exc())
    check("an error body is scrubbed at the boundary", KEY not in _body, _body)
    check("and still says what the endpoint said", "invalid api key" in _body, _body)

    _usage = {}
    fb._record_attempt(_usage, "https://example.invalid/v1", "fatal",
                       "401: " + _Echo401.response.text, 0.1)
    check("usage['attempts'] carries no key", KEY not in json.dumps(_usage), _usage)
    check("the attempt is still reported",
          "401" in _usage["attempts"][0]["detail"], _usage)

    _seen = []
    _handler = logging.Handler()
    _handler.emit = lambda r: _seen.append(r.getMessage())
    fb.log.addHandler(_handler)
    try:
        import requests as _rq
        _real_post = fb._post_watchdog

        def _echo_post(url, headers, payload, timeout, grace, cancel_event=None,
                       stream=False):
            raise _rq.HTTPError("401", response=_Echo401.response)

        fb._post_watchdog = _echo_post
        fb.AGENT._window_cache = 32768
        fb.AGENT._window_at = 0.0
        fb.AGENT._envelope_cache = None
        _msg = ""
        try:
            fb.AGENT._chat([{"role": "system", "content": "s"},
                            {"role": "user", "content": "hi"}],
                           session_key="scrub401")
        except fb.InfraError as e:
            _msg = str(e)
        except Exception as e:                                # noqa: BLE001
            _msg = "%s: %s" % (type(e).__name__, e)
        finally:
            fb._post_watchdog = _real_post
    finally:
        fb.log.removeHandler(_handler)
    check("the run's failure message carries no key", KEY not in _msg, _msg)
    check("and it names the credential problem", "rejected the request" in _msg, _msg)
    check("the log carries no key", all(KEY not in m for m in _seen),
          [m for m in _seen if KEY in m])
finally:
    fb._SECRETS = _prev_secrets

print()
print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
sys.exit(1 if FAILS else 0)
