"""test_security_surface - the controls a review asks for after the boundary itself.

Three cheap, falsifiable guards on the web service and the operator's evidence:

  * a refused request leaves ONE structured line and a per-peer counter, and the
    presented token never reaches the log;
  * a peer's SendMessage takes a token bucket and a process-wide run slot, so a
    compromised mesh member cannot spend the endpoint's budget for every other lane;
  * logs/ is owner-only, and its files are tightened with it.

The tool-provenance sha256 is graded where its record lives (test_tool_load_surface).

    python tests/test_security_surface.py
"""
import importlib.util
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-security-surface"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
             STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_security_surface",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_security_surface"] = fb
spec.loader.exec_module(fb)

PASSES = []
FAILURES = []


def check(what, cond, detail=""):
    if cond:
        PASSES.append(what)
        print(f"ok   {what}")
    else:
        FAILURES.append(what)
        print(f"FAIL {what}: {detail}")


# ---------------------------------------------------------------- auth refusals
seen = []
handler = logging.Handler()
handler.emit = lambda record: seen.append(record.getMessage())
fb.log.addHandler(handler)
try:
    fb._AUTH_FAILS.clear()
    for _ in range(10):
        count = fb._auth_refused("203.0.113.5", "/api/chat?token=SECRET-VALUE",
                                 "bad token")
    lines = [m for m in seen if "refused an unauthenticated request" in m]
    check("one line per refused request", len(lines) == 10, len(lines))
    check("the peer is named", all("203.0.113.5" in m for m in lines), lines[:1])
    check("the route is logged without its query string",
          all("SECRET-VALUE" not in m for m in seen), seen[:2])
    check("the burst counter fires at ten",
          any("possible token guessing" in m for m in seen), seen[-2:])
    check("the counter returns the window count", count == 10, count)
finally:
    fb.log.removeHandler(handler)
    fb._AUTH_FAILS.clear()

# Every 401 but the page's own GET /api/login probe goes through the recorder. A new
# route that answers 401 directly is a silent refusal again, which is the state this
# control exists to end.
_src = Path(fb.__file__).read_text(encoding="utf-8", errors="replace")
check("only the login probe answers 401 without the recorder",
      _src.count('{"error": "unauthorized"}, 401') == 2,
      _src.count('{"error": "unauthorized"}, 401'))

# ---------------------------------------------------------------- A2A rate + slots
fb._A2A_BUCKETS.clear()
burst = int(fb.A2A_RATE_BURST)
check("a peer may burst", all(fb._a2a_rate_ok("198.51.100.7")
                              for _ in range(burst)))
check("...and then waits", not fb._a2a_rate_ok("198.51.100.7"))
fb._A2A_BUCKETS["198.51.100.7"] = (0.0, time.monotonic() - 3600)
check("...and refills after the window", fb._a2a_rate_ok("198.51.100.7"))
check("another peer is unaffected", fb._a2a_rate_ok("198.51.100.8"))

got = [fb._A2A_RUN_SLOTS.acquire(blocking=False)
       for _ in range(fb.A2A_MAX_CONCURRENT)]
check("every run slot can be taken", all(got), got)
check("the next run is refused, not queued",
      not fb._A2A_RUN_SLOTS.acquire(blocking=False))
for _ in got:
    fb._A2A_RUN_SLOTS.release()

# ---------------------------------------------------------------- log permissions
work = Path(tempfile.mkdtemp(prefix="tinycmdr-security-"))
try:
    logs = work / "logs"
    logs.mkdir()
    proc = logs / "proc-b1.log"
    proc.write_text("a job's output\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(logs, 0o755)
        os.chmod(proc, 0o644)
    saved_base = fb.BASE_DIR
    fb.BASE_DIR = work
    try:
        fb._tighten_log_perms()
    finally:
        fb.BASE_DIR = saved_base
    if os.name != "nt":
        check("logs/ is owner-only",
              stat.S_IMODE(logs.stat().st_mode) == 0o700,
              oct(stat.S_IMODE(logs.stat().st_mode)))
        check("a log file inside it is owner-only",
              stat.S_IMODE(proc.stat().st_mode) == 0o600,
              oct(stat.S_IMODE(proc.stat().st_mode)))
    else:
        check("the log folder still exists under Windows ACLs", logs.is_dir())

    # The handler tightens the file at the one place it is opened, including the
    # reopen after a failed write.
    hpath = work / "tinycmdr.log"
    fh = fb._LoudRotatingFileHandler(hpath, maxBytes=4096, backupCount=0)
    try:
        fh.emit(logging.makeLogRecord({"msg": "a line", "levelno": logging.INFO,
                                        "levelname": "INFO"}))
    finally:
        fh.close()
    check("the handler created its file", hpath.exists())
    if os.name != "nt":
        check("the handler's file is owner-only",
              stat.S_IMODE(hpath.stat().st_mode) == 0o600,
              oct(stat.S_IMODE(hpath.stat().st_mode)))
finally:
    shutil.rmtree(work, ignore_errors=True)

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) failed: " + ", ".join(FAILURES))
    sys.exit(1)
print(f"{len(PASSES)} security checks passed")
