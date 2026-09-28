#!/usr/bin/env python3
"""Windows launch helper: keep tinycmdr running when nothing else will.

WHY THIS FILE EXISTS. Linux and macOS already own this: the systemd unit ships
`Restart=always` and the launchd agent ships `KeepAlive`, so on those platforms the
OS IS the watchdog and this file is not installed at all (the Linux installer stopped
copying it).

Windows has no per-user service manager. A plain install is a logon shortcut and an
`-AsService` install is a scheduled task, and neither brings back a crashed process on
its own: the task's restart policy never fires, because the launcher wakes it and the
launcher exits 0 whatever the bot does. So on Windows this file is the mechanism - it
starts the bot hidden, waits for it, and starts it again.

It does exactly that and nothing else. No readiness probes, no status JSON, no
notifications: `tinycmdr health` and `doctor` are the surfaces that answer "can it hear
me", and the bot's own log is where a failure is read.

    pythonw tinycmdr-supervise.py [--telegram|--cli|<verb>]   (the VBS launcher)
    python  tinycmdr-supervise.py --once                       one lifetime, for tests

A /restart inside the bot exits 75 (RESTART_EXIT_CODE) and is relaunched at once; any
other exit is a crash and is retried with a growing backoff.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BOT = BASE_DIR / "tinycmdr.py"
LOGS = BASE_DIR / "logs"
LOG_FILE = LOGS / "supervisor.log"
BOT_STDOUT = LOGS / "bot-stdout.log"
LOCK_FILE = LOGS / "supervisor.lock"

RESTART_EXIT_CODE = 75          # the bot exits with this to ask US to start it again
LOCKED_EXIT_CODE = 3            # the bot exits with this when another instance holds its lock
BACKOFF_START = 5
BACKOFF_MAX = 60
RAPID_EXIT_S = 30               # an exit sooner than this counts as a failed start
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CHILD_ARGS = []


def log(msg):
    """One line, timestamped, to logs/supervisor.log. Never raises."""
    line = "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        LOGS.mkdir(exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(line)
    except OSError:
        pass


def supervisor_lock():
    """Hold a lock for this supervisor, or None when one is already running.

    Two supervisors would fight over one bot (each relaunching what the other's child
    lost the lock to). Windows only, like the rest of this file; everywhere else the
    lock is skipped, which is what lets the test drive it on POSIX.
    """
    try:
        import msvcrt
    except ImportError:
        return True
    try:
        LOGS.mkdir(exist_ok=True)
        fh = open(LOCK_FILE, "a+b")
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return fh
    except OSError:
        return None
    except Exception:
        return True   # lock mechanics broken: never block on that


def start_bot():
    """Launch the bot hidden, its stdout appended to logs/bot-stdout.log."""
    LOGS.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # Tells the bot that exiting is a handover, so /restart does not spawn a second
    # copy that would race us for the instance lock.
    env["TINYCMDR_SUPERVISED"] = "1"
    fh = open(BOT_STDOUT, "a", encoding="utf-8", errors="replace")
    fh.write("\n===== supervised start {} =====\n".format(
        time.strftime("%Y-%m-%d %H:%M:%S")))
    fh.flush()
    proc = subprocess.Popen([sys.executable, str(BOT)] + list(CHILD_ARGS),
                            cwd=str(BASE_DIR), stdout=fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env,
                            creationflags=NO_WINDOW)
    log("started bot: pid %d%s" % (proc.pid, (" args=%s" % " ".join(CHILD_ARGS))
                                   if CHILD_ARGS else ""))
    return proc, fh


def run_once():
    """One bot lifetime: start it, wait for it, return (exit_code, uptime_seconds)."""
    proc, fh = start_bot()
    t0 = time.time()
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        raise
    finally:
        try:
            fh.close()
        except OSError:
            pass
    return code, int(time.time() - t0)


def next_backoff(failures):
    return min(BACKOFF_MAX, BACKOFF_START * (2 ** max(0, failures - 1)))


def respond_to_exit(code, uptime, failures):
    """What one lifetime's exit means: (failures, delay_seconds).

    A handover (/restart) is not a failure and is relaunched at once. Another instance
    holding the bot's lock is not our failure either - wait and look again. A lifetime
    that ran longer than RAPID_EXIT_S was healthy, so the failure count resets; a
    shorter one is a failed start and grows the backoff.
    """
    if code == RESTART_EXIT_CODE:
        return 0, 1
    if code == LOCKED_EXIT_CODE:
        return failures, 30
    if uptime > RAPID_EXIT_S and code == 0:
        return 0, BACKOFF_START
    failures += 1
    return failures, next_backoff(failures)


def main(argv):
    global CHILD_ARGS
    CHILD_ARGS = list(argv)
    lock = supervisor_lock()
    if lock is None:
        log("another supervisor is already running (lock held) - exiting")
        return 3
    log("supervisor up (pid %d), watching %s" % (os.getpid(), BOT.name))
    once = "--once" in CHILD_ARGS
    if once:
        CHILD_ARGS.remove("--once")
    failures = 0
    while True:
        try:
            code, uptime = run_once()
        except KeyboardInterrupt:
            log("interrupted - exiting")
            return 0
        except Exception as e:                                    # noqa: BLE001
            code, uptime = -1, 0
            log("could not run the bot: %s" % e)
        failures, delay = respond_to_exit(code, uptime, failures)
        log("bot exited: code=%s uptime=%ds (consecutive failures %d) - relaunching "
            "in %ds" % (code, uptime, failures, delay))
        if once:
            return code if isinstance(code, int) and code >= 0 else 1
        time.sleep(delay)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:
        import traceback
        log("supervisor crashed:\n%s" % traceback.format_exc())
        raise
