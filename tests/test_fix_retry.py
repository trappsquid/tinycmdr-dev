"""A 429's Retry-After is honoured in BOTH spellings RFC 9110 allows.

The delay-seconds form (`15`) was read; the HTTP-date form
(`Wed, 21 Oct 2026 07:28:00 GMT`) was discarded as "no hint" and the capped backoff
decided instead (A-2026-10-12-05, measured 2026-10-12). Both now clamp to the same cap.

TINYCMDR_SRC points SRC at a reverted build so the fix can be watched going red
(tests/run_all.py clears it for a normal run).
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

STAGE = Path(tempfile.mkdtemp(prefix="fbfixretry-"))
HOME = STAGE / "home"
HOME.mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(HOME)
os.environ["USERPROFILE"] = str(HOME)
os.environ["TINYCMDR_NO_MATERIALIZE"] = "1"

shutil.copy2(SRC, STAGE / "tinycmdr.py")
spec = importlib.util.spec_from_file_location("tinycmdr_fix_retry", STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_fix_retry"] = fb
spec.loader.exec_module(fb)

PASSES = []
FAILS = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print("ok   %s" % name)
    else:
        FAILS.append(name)
        print("FAIL %s: %s" % (name, detail))


class _Resp:
    def __init__(self, headers):
        self.headers = headers


class _Exc(Exception):
    def __init__(self, retry_after):
        super().__init__("429")
        self.response = _Resp({"Retry-After": retry_after} if retry_after is not None else {})


def main():
    f = fb._retry_after_secs
    check("delay-seconds is honoured", f(_Exc("15"), 60) == 15.0, f(_Exc("15"), 60))
    check("delay-seconds clamps at the cap", f(_Exc("900"), 60) == 60.0, f(_Exc("900"), 60))
    check("a missing header is no hint", f(_Exc(None), 60) == 0.0)
    # The date form: ten seconds in the future, within a tolerance window.
    soon = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10))
    got = f(_Exc(soon), 60)
    check("an HTTP-date is honoured (10s ahead reads ~10)",
          9.0 <= got <= 11.0, got)
    check("an HTTP-date clamps at the cap too",
          f(_Exc(format_datetime(datetime.now(timezone.utc) + timedelta(hours=2))), 60) == 60.0)
    old = format_datetime(datetime.now(timezone.utc) - timedelta(hours=1))
    check("a date already in the past is no hint", f(_Exc(old), 60) == 0.0,
          f(_Exc(old), 60))
    check("an unparsable value is no hint", f(_Exc("soon-ish"), 60) == 0.0)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(STAGE, ignore_errors=True)
    print("\n%d passed, %d failed" % (len(PASSES), len(FAILS)))
    sys.exit(rc if not FAILS else 1)
