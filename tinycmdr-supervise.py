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
other exit is a crash and is retried with a growing backoff. An exit 75 the bot made on
its own carries no marker and is counted, so a self-restart loop stops after a few
rounds instead of cycling for ever.
"""
from __future__ import annotations

import json
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
FAILED_STARTS_BEFORE_STOP = 10  # rapid failed starts in a row: stop, make a human look
STOPPED_EXIT_CODE = 4           # supervisor gave up; nothing will restart the bot

RESTART_MARKER_FILE = LOGS / "restart-requested.json"  # the bot's /restart handover marker
RESTART_MARKER_FRESH_S = 120    # an older marker is a past cycle, not this exit
SELF_RESTARTS_BEFORE_STOP = 5   # unrequested 75s in the window below: stop the loop
SELF_RESTART_WINDOW_S = 30 * 60  # the bound on those: 5 self-restarts per 30 min
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


def consume_handover_marker(now=None):
    """Was this exit 75 ASKED FOR? Reads and deletes logs/restart-requested.json.

    The bot writes that file (with the epoch it left at) only on a handover it was
    told to make - a /restart inside the bot - and never on the watchdog path that
    restarts a deaf listener. So a fresh marker is the difference between "a human
    is watching, relaunch at once for ever" and "the bot restarted itself", which
    the stop-the-loop ladder below MUST count. The marker is consumed either way so
    a later cycle cannot read the same request twice; one older than
    RESTART_MARKER_FRESH_S is a leftover from a past cycle and does not exempt this
    exit. Never raises: an unreadable or malformed marker just means "not asked".
    """
    now = time.time() if now is None else now
    try:
        data = json.loads(RESTART_MARKER_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    try:
        RESTART_MARKER_FILE.unlink()
    except OSError:
        pass
    try:
        at = int(data["at"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= now - at <= RESTART_MARKER_FRESH_S


def _bump_self_restart(count, window_start, now=None):
    """Count a self-restart, or start a fresh window when the last one has run out."""
    now = time.time() if now is None else now
    if not window_start or now - window_start > SELF_RESTART_WINDOW_S:
        return 1, now
    return count + 1, window_start


def respond_to_exit(code, uptime, failures, self_restarts=0, window_start=0.0, now=None):
    """What one lifetime's exit means: (failures, self_restarts, window_start, delay).

    A handover (/restart) is not a failure and is relaunched at once; it is also the
    only exit 75 taken on trust, and only while its marker is fresh - it resets both
    ladders, because a human is watching. An exit 75 with no marker (or a stale one)
    is the bot restarting itself, and that IS counted, on its own much shorter
    ladder: SELF_RESTARTS_BEFORE_STOP restarts inside SELF_RESTART_WINDOW_S and the
    loop STOPS (delay 0) instead of cycling for ever. Another instance holding the
    bot's lock is not our failure either - wait and look again. A lifetime that ran
    longer than RAPID_EXIT_S was healthy, so the failure count resets; a shorter one
    is a failed start and grows the backoff, up to FAILED_STARTS_BEFORE_STOP in a row
    - after that the ladder STOPS (delay 0) rather than retrying a broken install or
    an ambiguous config every 60 s for ever.
    """
    if code == RESTART_EXIT_CODE:
        if consume_handover_marker(now):
            return 0, 0, 0.0, 1
        self_restarts, window_start = _bump_self_restart(self_restarts, window_start, now)
        if self_restarts >= SELF_RESTARTS_BEFORE_STOP:
            return failures, self_restarts, window_start, 0
        return failures, self_restarts, window_start, 1
    if code == LOCKED_EXIT_CODE:
        return failures, self_restarts, window_start, 30
    if uptime > RAPID_EXIT_S:
        # ANY exit after a long healthy run resets the count: requiring code == 0 let a
        # crash after days (exit 1, an endpoint blip, a killed child) count as a failed
        # START, so crashes spread over weeks added up and one quick exit at boot then
        # tripped FAILED_STARTS_BEFORE_STOP - the Windows bot stopped for good
        # (A-2026-10-08-170). The count is about rapid failed STARTS; a long run was not
        # one.
        return 0, self_restarts, window_start, BACKOFF_START
    failures += 1
    if uptime <= RAPID_EXIT_S and failures >= FAILED_STARTS_BEFORE_STOP:
        # A start that dies at once, over and over, is not a crash to retry: it is a
        # broken install or an ambiguous config (exit 2 is the config class), and its
        # reason is printed once in bot-stdout.log and never acted on. The old ladder
        # retried it every 60 s for ever - 5,368 times over four days on a live host
        #. delay 0 means stop; main says why and exits.
        return failures, self_restarts, window_start, 0
    return failures, self_restarts, window_start, next_backoff(failures)


def _last_start_words(limit=6):
    """The last lines the bot wrote before it died, for the give-up log."""
    lines = []
    try:
        with BOT_STDOUT.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip()
                if line and not line.startswith("===== supervised start"):
                    lines.append(line)
                    if len(lines) > limit:
                        lines.pop(0)
    except OSError:
        return []
    return lines


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
    self_restarts = 0
    self_window = 0.0
    while True:
        try:
            code, uptime = run_once()
        except KeyboardInterrupt:
            log("interrupted - exiting")
            return 0
        except Exception as e:                                    # noqa: BLE001
            code, uptime = -1, 0
            log("could not run the bot: %s" % e)
        failures, self_restarts, self_window, delay = respond_to_exit(
            code, uptime, failures, self_restarts, self_window)
        if once:
            return code if isinstance(code, int) and code >= 0 else 1
        if delay <= 0:
            if code == RESTART_EXIT_CODE:
                # Measured 2026-10-07: a deaf bot restarted itself and went deaf again,
                # one restart every ~15 min, with the ladder blind to it because every
                # 75 was treated as a human handover. A self-restart loop is not a
                # failed start to retry slowly; it is the bot saying it cannot run.
                log("bot restarted itself without being asked %d times in %d min - "
                    "a human must look before it runs again. Last words from the bot:"
                    % (self_restarts, SELF_RESTART_WINDOW_S // 60))
            else:
                log("bot failed to start %d times in a row (last code=%s, uptime=%ds) - "
                    "NOT retrying until a human looks. Last words from the bot:"
                    % (failures, code, uptime))
            for line in _last_start_words():
                log("  | %s" % line)
            return STOPPED_EXIT_CODE
        log("bot exited: code=%s uptime=%ds (consecutive failures %d) - relaunching "
            "in %ds" % (code, uptime, failures, delay))
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
