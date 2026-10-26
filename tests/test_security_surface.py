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

# ---------------------------------------------------------------- sub-agent ceilings
read_ctx = {"capabilities": "read"}
write_ctx = {"capabilities": "write"}
full_ctx = {"capabilities": "full"}
check("a read-only child may read",
      fb._capability_refusal("read_file", read_ctx) == "")
check("a read-only child may browse",
      fb._capability_refusal("fetch_url", read_ctx) == "")
check("a read-only child may not write",
      fb._capability_refusal("write_file", read_ctx).startswith("REFUSED"))
check("a read-only child may not run a command",
      fb._capability_refusal("shell", read_ctx).startswith("REFUSED"))
check("a write child may write",
      fb._capability_refusal("write_file", write_ctx) == "")
check("a write child may not run a command",
      fb._capability_refusal("shell", write_ctx).startswith("REFUSED"))
check("a full child keeps the parent's surface",
      fb._capability_refusal("shell", full_ctx) == "")
check("an unknown ceiling fails closed to read",
      fb._capability_refusal("shell", {"capabilities": "whatever"}).startswith("REFUSED"))
check("the delegation tool rejects an unknown ceiling",
      fb.tool_delegate_task({"capabilities": "bogus"}, {"depth": 0})
      .startswith("ERROR: `capabilities` must be"))

# ---------------------------------------------------------------- release signatures
sigwork = Path(tempfile.mkdtemp(prefix="tinycmdr-sig-test-"))
sums = sigwork / "SHA256SUMS"
sig = sigwork / "SHA256SUMS.sig"
sums.write_text("a" * 64 + "  tinycmdr-macos.zip\n", encoding="utf-8")
sig.write_bytes(b"not-a-real-signature")
pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY tinycmdr-test"

class _Proc:
    def __init__(self, code, out=b"", err=b""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


saved_which, saved_run = fb.shutil.which, fb.subprocess.run
saved_pub = fb.CONFIG["agent"].get("release_pubkey")
try:
    fb.shutil.which = lambda name: "/usr/bin/ssh-keygen"
    fb.subprocess.run = lambda *a, **k: _Proc(0, b"Good \"tinycmdr-release\" "
                                                 b"signature for ED25519 key\\n")
    ok, why = fb._verify_release_signature(sums, sig, pub)
    check("a good release signature passes", ok, why)
    fb.subprocess.run = lambda *a, **k: _Proc(1, b"", b"Bad signature")
    ok, why = fb._verify_release_signature(sums, sig, pub)
    check("a bad release signature is refused",
          not ok and "does not verify" in why, why)
    fb.subprocess.run = lambda *a, **k: _Proc(0, b"Good")
    ok, why = fb._verify_release_signature(sums, sigwork / "missing.sig", pub)
    check("a missing signature is refused", not ok and "no SHA256SUMS.sig" in why, why)
    ok, why = fb._verify_release_signature(sums, sig, "not a public key")
    check("a non-key config is refused", not ok and "does not look like" in why, why)
    fb.shutil.which = lambda name: None
    ok, why = fb._verify_release_signature(sums, sig, pub)
    check("a host without ssh-keygen fails closed",
          not ok and "no ssh-keygen" in why, why)

    fb.CONFIG["agent"]["release_pubkey"] = ""
    check("an empty release_pubkey is the unsigned default", fb._release_pubkey() == "")
    fb.CONFIG["agent"]["release_pubkey"] = pub
    check("a configured release key is read", fb._release_pubkey() == pub)
finally:
    fb.shutil.which, fb.subprocess.run = saved_which, saved_run
    fb.CONFIG["agent"]["release_pubkey"] = saved_pub
    shutil.rmtree(sigwork, ignore_errors=True)
check("the shipped default is unsigned",
      fb.CONFIG["agent"].get("release_pubkey") == "")
check("children are read-only by default",
      fb.CONFIG["agent"].get("subagent_capabilities") == "read")

# ---------------------------------------------------------------- MCP environment
saved_env = dict(fb.os.environ)
try:
    fb.os.environ.clear()
    fb.os.environ.update({
        "PATH": "/usr/bin", "LANG": "C.UTF-8",
        "TINYCMDR_WEB_TOKEN": "web-secret", "TINYCMDR_MM_TOKEN": "mm-secret",
        "AWS_SECRET_ACCESS_KEY": "aws-secret", "SUDO_PASSWORD": "sudo-secret",
    })
    mcp_env = fb._mcp_env({"env": {"MCP_SETTING": "yes"}})
    check("an MCP child keeps PATH and LANG", mcp_env.get("PATH") == "/usr/bin"
          and mcp_env.get("LANG") == "C.UTF-8")
    check("an MCP child gets its explicit config env",
          mcp_env.get("MCP_SETTING") == "yes")
    check("an MCP child does not inherit harness tokens",
          "TINYCMDR_WEB_TOKEN" not in mcp_env
          and "TINYCMDR_MM_TOKEN" not in mcp_env, mcp_env)
    check("an MCP child does not inherit other credentials",
          "AWS_SECRET_ACCESS_KEY" not in mcp_env
          and "SUDO_PASSWORD" not in mcp_env, mcp_env)
finally:
    fb.os.environ.clear()
    fb.os.environ.update(saved_env)

# ---------------------------------------------------------- approval trust root and skills
skills_calls = []
fb._surface_write_gate(str(fb.BASE_DIR / "skills" / "new.md"), "test",
                       {"confirm_cb": lambda q: (skills_calls.append(q), False)[1]})
check("a skills write goes through the confirm door", bool(skills_calls))

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
