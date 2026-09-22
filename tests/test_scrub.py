"""Secrets are scrubbed in BOTH builds (audit, 2026-09-22).

The review found the console build's secret sweep cut down to environment variables; the
real hole was that NEITHER build scrubbed the primary llm.api_key, which is the key a
hosted endpoint keeps in config.json and the one in use on every call. This suite grades
the sweep itself, on the code's own terms, and runs against either build:

    python tests/test_scrub.py
    TINYCMDR_SRC=tinycmdr-cli.py python tests/test_scrub.py
"""
import importlib.util
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
    # This build may have no fallback endpoints at all (the console build cuts them);
    # ask the source rather than the config, which the suite has just written.
    if 'vals.add(fb["api_key"])' in SRC.read_text(encoding="utf-8", errors="replace"):
        out = fb.scrub("failover used " + FBKEY)
        check("a fallback api_key is still redacted", FBKEY not in out, out)
    else:
        print("skip a fallback key check (this build has no fallback endpoints)")
    out = fb.scrub("token " + MM)
    check("the chat token is still redacted", MM not in out, out)
    out = fb.scrub("env token env-token-abcdefghijkl")
    check("an environment secret is redacted", "env-token-abcdefghijkl" not in out, out)

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

print()
print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
sys.exit(1 if FAILS else 0)
