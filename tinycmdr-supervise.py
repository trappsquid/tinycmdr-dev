"""Supervisor: keep tinycmdr running 24/7.

Why this exists
---------------
The "tinycmdr" scheduled task starts `tinycmdr-service.vbs`, which spawns the
bot detached and exits immediately. Task Scheduler therefore believes the task
finished successfully and its RestartOnFailure policy can never fire — and,
launched through pythonw, an exception in the bot leaves no trace anywhere. The
observed result: the bot can be down for hours and the only evidence is a log
file that stops mid-line.

This supervisor is the task's action instead (via the VBS, which now waits for
it). It runs the bot as a *child*, so:

  * the task stays "Running" while the bot is up (Task Scheduler tells the truth),
  * if the bot exits — crash, network failure at startup, single-instance lock
    conflict — it is relaunched automatically with backoff, forever,
  * the child's stdout/stderr are captured to logs/bot-stdout.log, so the
    traceback that used to vanish into pythonw is on disk,
  * every start/exit/ready event is logged and mirrored into
    logs/supervisor-status.json for an external check,
  * a revival is announced in Mattermost (bot token from config.json), because a
    silently-recovered bot is indistinguishable from a dead one from the outside.

Usage
-----
    python tinycmdr-supervise.py            run forever (this is what the task does)
    python tinycmdr-supervise.py --status   print status JSON and exit
    python tinycmdr-supervise.py --once     one supervision pass, then exit (tests)

Never run two: a lock file (logs/supervisor.lock) makes the second one exit 3.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BOT = BASE_DIR / "tinycmdr.py"
CONFIG_FILE = BASE_DIR / "config.json"
LOGS = BASE_DIR / "logs"
SUPERVISOR_LOG = LOGS / "supervisor.log"
SUPERVISOR_STDOUT = LOGS / "supervisor-stdout.log"
SUPERVISOR_LOCK = LOGS / "supervisor.lock"
STATUS_FILE = LOGS / "supervisor-status.json"
BOT_STDOUT = LOGS / "bot-stdout.log"
BOT_LOCK = BASE_DIR / "tinycmdr.lock"

# The interpreter the launcher uses. A different one (a venv without mmpy_bot or
# croniter) starts a degraded bot: schedule tool disabled, Mattermost driver
# missing. Pinned deliberately — see the 20:44 run in tinycmdr.log.
PYTHON = Path(os.environ.get(
    "tinycmdr_PYTHON",
    r"C:/Users/David Trapp/AppData/Local/Programs/Python/Python312/python.exe"))

REQUIRED = ("requests", "mmpy_bot", "croniter")

READY_TIMEOUT = 90          # seconds to wait for Mattermost to answer
WAIT_SLICE = 30             # seconds per child.wait() slice
BACKOFF_START = 5
BACKOFF_MAX = 60
LOCK_CONFLICT_BACKOFF = 30   # another tinycmdr holds the lock: re-test soon. A
                             # /restart spawns an UNSUPERVISED replacement; this
                             # keeps recovery after its death inside ~30s instead
                             # of minutes. The test is one lock attempt, no network.
RAPID_EXIT_S = 30           # an exit sooner than this counts as a failed start
RESTART_EXIT_CODE = 75      # tinycmdr exiting with this wants US to start it again

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

STATUS = {
    "supervisor_pid": os.getpid(),
    "supervisor_started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "python": str(PYTHON),
    "child_pid": None,
    "child_started_at": None,
    "restarts": 0,
    "last_exit_code": None,
    "last_exit_at": None,
    "last_uptime_s": None,
    "consecutive_failures": 0,
    "last_ready_s": None,
    "mattermost_ok": None,
    "updated_at": None,
}


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------

def log(msg, level="INFO"):
    line = "%s %-7s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), level, msg)
    LOGS.mkdir(exist_ok=True)
    try:
        with open(SUPERVISOR_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def save_status():
    STATUS["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        LOGS.mkdir(exist_ok=True)
        STATUS_FILE.write_text(json.dumps(STATUS, indent=2), encoding="utf-8")
    except OSError as e:
        log("could not write status file: %s" % e, "WARN")


# --------------------------------------------------------------------------
# probes
# --------------------------------------------------------------------------

def bot_lock_free():
    """True when nothing holds tinycmdr's single-instance lock.

    tinycmdr takes an OS lock (msvcrt) on tinycmdr.lock, so the file existing
    means nothing — only a failed lock attempt does.
    """
    try:
        import msvcrt
    except ImportError:                     # non-Windows: not our platform
        return True
    try:
        fh = open(BOT_LOCK, "a+b")
    except OSError:
        return True
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return True
    except OSError:
        return False
    finally:
        try:
            fh.close()
        except OSError:
            pass


def mm_config():
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    mm = cfg.get("mattermost") or {}
    url, token = mm.get("url"), mm.get("token")
    if not url or not token:
        return None
    base = "%s://%s" % (mm.get("scheme", "https"), url)
    port = mm.get("port")
    if port and not ((mm.get("scheme", "https") == "https" and int(port) == 443)
                     or (mm.get("scheme") == "http" and int(port) == 80)):
        base += ":%s" % port
    return {"base": base.rstrip("/"), "token": token,
            "verify": bool(mm.get("ssl_verify", True)),
            "dm_user": (cfg.get("supervisor") or {}).get("dm_user")
                       or (mm.get("allowed_users") or [None])[0],
            "notify": (cfg.get("supervisor") or {}).get("notify", True)}


def mm_ok(timeout=6):
    """Is the bot's Mattermost reachable and is the token still valid?"""
    import urllib.request
    import ssl
    conf = mm_config()
    if not conf:
        return None
    ctx = None if conf["verify"] else ssl._create_unverified_context()
    req = urllib.request.Request(conf["base"] + "/api/v4/users/me",
                                 headers={"Authorization":
                                          "Bearer %s" % conf["token"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status == 200
    except Exception:
        return False


def mm_post(text):
    """Best-effort DM to the operator. Never raises."""
    import urllib.request
    import ssl
    conf = mm_config()
    if not conf or not conf.get("notify") or not conf.get("dm_user"):
        return False
    ctx = None if conf["verify"] else ssl._create_unverified_context()
    hdrs = {"Authorization": "Bearer %s" % conf["token"],
            "Content-Type": "application/json"}

    def call(path, payload=None):
        req = urllib.request.Request(
            conf["base"] + path, headers=hdrs,
            data=None if payload is None else json.dumps(payload).encode())
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            return json.loads(r.read().decode() or "{}")

    try:
        me = call("/api/v4/users/me")
        chan = call("/api/v4/channels/direct", [me["id"], conf["dm_user"]])
        call("/api/v4/posts", {"channel_id": chan["id"], "message": text})
        return True
    except Exception as e:
        log("Mattermost notify failed: %s" % e, "WARN")
        return False


def preflight():
    """Check the pinned interpreter can actually run the bot."""
    if not PYTHON.is_file():
        log("interpreter missing: %s" % PYTHON, "CRITICAL")
        return False
    code = ("import importlib,sys\n"
            "miss=[m for m in %r if importlib.util.find_spec(m) is None]\n"
            "print('MISSING:' + ','.join(miss))\n" % (list(REQUIRED),))
    try:
        out = subprocess.run([str(PYTHON), "-c", code], capture_output=True,
                             text=True, timeout=60, creationflags=NO_WINDOW)
    except Exception as e:
        log("preflight could not run %s: %s" % (PYTHON, e), "CRITICAL")
        return False
    missing = ""
    for line in (out.stdout or "").splitlines():
        if line.startswith("MISSING:"):
            missing = line.split(":", 1)[1].strip()
    if missing:
        log("interpreter %s is missing %s — the bot would start DEGRADED "
            "(schedule tool / Mattermost driver disabled). Install with: "
            '"%s" -m pip install %s' % (PYTHON, missing, PYTHON, missing),
            "CRITICAL")
        return False
    log("preflight ok: %s has %s" % (PYTHON.name, ", ".join(REQUIRED)))
    return True


# --------------------------------------------------------------------------
# child management
# --------------------------------------------------------------------------

def start_bot():
    LOGS.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"     # redirected stdout is cp1252 otherwise
    # Tell the bot it is supervised, so a /restart exits for us to relaunch
    # instead of spawning a detached copy that races our next child for the
    # lock (which is what used to fill this log with "exited 3" and 300 s waits).
    env["tinycmdr_SUPERVISED"] = "1"
    fh = open(BOT_STDOUT, "a", encoding="utf-8", errors="replace")
    fh.write("\n===== supervised start %s =====\n"
             % time.strftime("%Y-%m-%d %H:%M:%S"))
    fh.flush()
    proc = subprocess.Popen([str(PYTHON), str(BOT)], cwd=str(BASE_DIR),
                            stdout=fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env,
                            creationflags=NO_WINDOW)
    STATUS["child_pid"] = proc.pid
    STATUS["child_started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_status()
    log("started bot: pid %d (%s)" % (proc.pid, PYTHON.name))
    return proc, fh


def wait_ready(proc, timeout=READY_TIMEOUT):
    """Wait until the lock is held and Mattermost answers. Returns seconds or None."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return None                      # died while starting
        if not bot_lock_free() and mm_ok() is True:
            return int(time.time() - t0)
        time.sleep(3)
    return None


def supervise_once(first, once=False, intentional=False):
    """One bot lifetime. Returns (exit_code, uptime_seconds, was_ready)."""
    if not bot_lock_free():
        if once:
            log("another tinycmdr already holds the lock — that instance is the "
                "bot; nothing to do", "WARN")
            return 3, 0, False
        log("another tinycmdr already holds the lock — waiting %ds before "
            "trying again" % LOCK_CONFLICT_BACKOFF, "WARN")
        time.sleep(LOCK_CONFLICT_BACKOFF)
        return 3, 0, False

    proc, fh = start_bot()
    ready = wait_ready(proc)
    STATUS["last_ready_s"] = ready
    STATUS["mattermost_ok"] = mm_ok()
    save_status()
    if ready is None and proc.poll() is None:
        log("bot pid %d is up but not ready after %ds (lock held=%s, "
            "mattermost=%s)" % (proc.pid, READY_TIMEOUT, not bot_lock_free(),
                                STATUS["mattermost_ok"]), "WARN")
    elif ready is not None:
        log("bot ready in %ds (pid %d)" % (ready, proc.pid))
        if not first and not intentional and mm_post(
                "⚠️ **tinycmdr was down** — supervisor "
                "restarted it (ready in %ds)." % ready):
            log("revival announced in Mattermost")

    t0 = time.time()
    while True:
        try:
            code = proc.wait(timeout=WAIT_SLICE)
            break
        except subprocess.TimeoutExpired:
            if proc.poll() is None:
                continue
            code = proc.returncode
            break
    uptime = int(time.time() - t0)
    try:
        fh.close()
    except Exception:
        pass
    return code, uptime, ready is not None


def next_backoff(failures):
    return min(BACKOFF_MAX, BACKOFF_START * (2 ** max(0, failures - 1)))


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

def acquire_supervisor_lock():
    try:
        import msvcrt
        LOGS.mkdir(exist_ok=True)
        fh = open(SUPERVISOR_LOCK, "a+b")
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return fh
    except OSError:
        return None
    except Exception:
        return True   # lock mechanics broken: never block on that


def main(argv):
    if "--status" in argv:
        try:
            print(STATUS_FILE.read_text(encoding="utf-8"))
        except OSError:
            print(json.dumps({"error": "no status file — supervisor never ran",
                              "expected": str(STATUS_FILE)}, indent=2,
                             ensure_ascii=False))
        return 0

    lock = acquire_supervisor_lock()
    if lock is None:
        log("another supervisor is already running (lock held) — exiting", "WARN")
        return 3

    # Our own stdout/stderr: pythonw gives us none, and we want tracebacks.
    try:
        stream = open(SUPERVISOR_STDOUT, "a", encoding="utf-8", errors="replace")
        sys.stdout = sys.stderr = stream
        print("\n===== supervisor start %s ====="
              % time.strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    except OSError:
        pass

    log("supervisor up (pid %d), watching %s" % (os.getpid(), BOT.name))
    if not preflight():
        log("preflight failed — starting anyway, but expect a degraded bot",
            "WARN")
    save_status()

    once = "--once" in argv
    first = True
    failures = 0
    intentional = False          # the last exit was a deliberate /restart
    while True:
        try:
            code, uptime, ready = supervise_once(first, once, intentional)
        except KeyboardInterrupt:
            log("interrupted — exiting")
            return 0
        except Exception:
            log("supervision error:\n%s" % traceback.format_exc(), "ERROR")
            code, uptime, ready = -1, 0, False

        STATUS["last_exit_code"] = code
        STATUS["last_exit_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        STATUS["last_uptime_s"] = uptime
        STATUS["child_pid"] = None
        if code == RESTART_EXIT_CODE:
            # A /restart: the bot exited on purpose and is waiting for US. Start
            # it again at once. No failure counted, no backoff, and no "was down"
            # announcement — it was never down.
            log("intentional restart (exit %s) — relaunching now" % code)
            failures = 0
            STATUS["consecutive_failures"] = 0
            save_status()
            if once:
                return 0
            first = False
            intentional = True
            time.sleep(1)
            continue

        if code == 3:
            # Another instance owns the lock; that instance is the bot. Not a
            # failure of ours — re-check soon, don't count it as a crash.
            log("bot exited 3 (lock held elsewhere)", "WARN")
            STATUS["consecutive_failures"] = 0
            save_status()
            if once:
                return 0
            first = False
            continue

        if ready and uptime > RAPID_EXIT_S:
            failures = 0
        else:
            failures += 1
        STATUS["consecutive_failures"] = failures
        if failures:
            STATUS["restarts"] += 1
        save_status()
        log("bot exited: code=%s uptime=%ds ready=%s (consecutive failures %d)"
            % (code, uptime, ready, failures),
            "WARN" if failures else "INFO")

        if failures == 5:
            mm_post("❌ **tinycmdr keeps failing** — 5 supervised starts in a "
                    "row did not come up. Last exit code %s; see "
                    "logs\\supervisor.log and logs\\bot-stdout.log." % code)
            log("5 consecutive failures — announced in Mattermost", "ERROR")

        if once:
            return 0
        first = False
        intentional = False      # anything else is a crash or a stop, not a handover
        delay = next_backoff(failures) if failures else BACKOFF_START
        log("relaunching in %ds" % delay)
        time.sleep(delay)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:
        try:
            log("supervisor crashed:\n%s" % traceback.format_exc(), "CRITICAL")
        except Exception:
            pass
        raise
