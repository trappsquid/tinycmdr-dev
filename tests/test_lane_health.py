"""What the surfaces say about the lanes, and about a config edit that has not applied.

Two incidents on a live install, 2026-09-28, both invisible until someone went looking:

  * the bot was up, `systemctl` said `active`, `tinycmdr health` named mattermost because a
    TOKEN existed - while the bot could not hear anybody, 510 restarts deep. Every surface
    told the truth about the wrong question: "is the process up" instead of "can it hear me";
  * the operator asked the agent, from Mattermost, to change a setting. The agent wrote
    config.json correctly and the service restarted - and nothing anywhere said that a config
    edit needs a restart to apply.

The local web UI is gone, so these facts are graded where they now live: `lanes_snapshot()`,
`tinycmdr health` (exit code and lane line), the persisted `logs/state.json`, `tinycmdr
doctor`'s lane lines, and `config_drift()`. Hermetic: the staged copy is the module, so
`logs/state.json` lands in the stage.

    python tests/test_lane_health.py
"""
import contextlib
try:
    import fcntl                      # POSIX: the folder lock is flock on a descriptor
except ImportError:                   # Windows: no fcntl module - the same target is locked
    fcntl = None                      # through msvcrt, which _hold_lock below drives
import importlib.util
import io
import json
import logging
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "tinycmdr.py"
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-lane"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(BASE / "tests" / "fixture-config.json", STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_lane", STAGE / "tinycmdr.py")
T = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_lane"] = T
spec.loader.exec_module(T)

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("ok   %s" % name)
    else:
        FAILS.append(name)
        print("FAIL %s: %s" % (name, detail))


def run_verb(fn):
    """(return_code, stdout, stderr) for a verb that prints."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn()
    return rc, out.getvalue(), err.getvalue()


def failures_on_disk():
    try:
        return json.loads(T.LANE_STATE_FILE.read_text(encoding="utf-8")).get("failures") or {}
    except Exception:                                            # noqa: BLE001
        return {}


# ---------------------------------------------------- configured is not connected
lanes = T.lanes_snapshot()
check("a configured lane with no record reads 'configured', not 'up'",
      lanes["mattermost"]["state"] == "configured"
      and lanes["mattermost"]["failed_starts"] == 0, lanes)
check("the lanes are the chat lanes, and only those (no page lane)",
      set(lanes) <= {"mattermost", "telegram"}, list(lanes))
rc, out, _err = run_verb(T._verb_health)
check("health names the configured lane and its state on one line",
      "lane mattermost=configured" in out, out)

# --------------------------------------- a failure counts ACROSS the restarts it causes
n1, same1, _first = T.lane_down("mattermost", "401 Invalid or expired session")
n2, same2, _ = T.lane_down("mattermost", "401 Invalid or expired session")
check("the first failure counts 1, its repeat counts 2", (n1, n2) == (1, 2), (n1, n2))
check("...and the repeat is recognised as the same error",
      same1 is False and same2 is True, (same1, same2))
check("the count is written to disk (a dying process cannot remember it)",
      failures_on_disk().get("mattermost", {}).get("count") == 2, failures_on_disk())

# the restart: everything in memory goes, the file stays
T.LANE_FAILS.clear()
T.LANE_STATE.clear()
T._LANE_FAILS_LOADED = False
lanes = T.lanes_snapshot()
check("after a restart the lane still reads 'failed'",
      lanes["mattermost"]["state"] == "failed", lanes)
check("...with the attempt count intact", lanes["mattermost"]["failed_starts"] == 2, lanes)
check("...and the reason", "401" in lanes["mattermost"]["detail"], lanes)
rc, out, err = run_verb(T._verb_health)
check("health reports a failed lane on its stdout line", "lane mattermost=failed" in out, out)

n3, same3, _ = T.lane_down("mattermost", "connection refused")
check("a DIFFERENT error is a fresh failure, not attempt 3",
      (n3, same3) == (1, False), (n3, same3))

# ------------------------------------------------------------- recovery, said once
T.lane_up("mattermost", "connected as @the-bot")
check("a recovered lane reads 'up'",
      T.lanes_snapshot()["mattermost"]["state"] == "up", T.lanes_snapshot())
check("...and its failure record is cleared on disk",
      "mattermost" not in failures_on_disk(), failures_on_disk())

# ------------------------------------------------ the config edit that never applied
check("a freshly loaded config shows no drift", T.config_drift() == "", T.config_drift())
_cfg = json.loads((STAGE / "config.json").read_text(encoding="utf-8"))
_cfg.setdefault("agent", {})["max_steps"] = int((_cfg.get("agent") or {}).get("max_steps") or 12) + 7
(STAGE / "config.json").write_text(json.dumps(_cfg, indent=2), encoding="utf-8")
_drift = T.config_drift()
check("a config.json edited after start IS reported", "restart to apply" in _drift, _drift)

# ------------------------------------------------------------ the two verbs, on top
def _hold_lock(kind, fh):
    """Take this folder's single-instance lock the way the product does, to pretend to be a
    running bot.

    `_lock_target()` hands back a folder DESCRIPTOR on POSIX and a FILE object on Windows
    (a Windows directory handle cannot be locked), and each needs its own primitive. Driving
    only flock meant the suite could not even import on Windows, so the lane checks below -
    its actual subject, and the incidents that created this file - went ungraded on that
    platform. Release goes through the product's own `_lock_release_fd`, so the pair matches.
    """
    if kind == "dir":
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    import msvcrt
    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    return fh


_kind, _fd = T._lock_target()          # (kind, fd): the lock target is a descriptor
_hold_lock(_kind, _fd)                 # pretend to be the running bot
try:
    # A running install whose lane is up must exit 0, so the non-zero below means the
    # lane, not the lock.
    T.lane_up("mattermost", "connected as @the-bot")
    rc0, out0, err0 = run_verb(T._verb_health)
    check("health exits 0 while the lock is held and no lane has failed",
          rc0 == 0, (rc0, out0, err0))
    check("...and names the up lane", "lane mattermost=up" in out0, out0)

    T.lane_down("mattermost", "401 Invalid or expired session")
    rc, out, err = run_verb(T._verb_health)
    check("health names the state, not just the lane", "mattermost=failed" in out, out)
    check("...in the shape scripts already grep", "lane mattermost" in out, out)
    check("...and says the lane is DOWN on stderr", "mattermost lane is DOWN" in err, err)
    check("...and exits non-zero for a monitor", rc == 1, (rc, out, err))
    check("...and reports the un-applied config edit on stderr",
          "restart to apply" in err, err)

    rc_d, out_d, err_d = run_verb(T._verb_doctor)
    check("doctor prints the lane states", "mattermost=failed" in out_d, out_d[-500:])
    check("doctor flags the pending config change", "restart to apply" in out_d + err_d,
          (out_d + err_d)[-500:])
    check("...and a down lane is listed as a problem",
          "mattermost lane is DOWN" in err_d, err_d[-300:])
finally:
    T._lock_release_fd(_fd)            # the product's own release, so both platforms match

# --------------------------------- a lane that cannot start is RETRIED, in-process
# The 2026-09-30 outage: the bot raised, main() exited by design, and launchd respawned the
# whole process every ~10s for the 4h45m the network was gone - 1654 startups of this 22k-line
# module. The lane is retried HERE now, on tinycmdr-supervise.py's growing curve, so the
# manager's fixed delay is left for a real crash. `now=` and `sleep=` are the seams, the way
# _stall_tick takes `now=`.
_sup_spec = importlib.util.spec_from_file_location("tinycmdr_supervise",
                                                   BASE / "tinycmdr-supervise.py")
SUP = importlib.util.module_from_spec(_sup_spec)
_sup_spec.loader.exec_module(SUP)
check("the in-process backoff IS the supervisor's constants (they must not drift)",
      (T.LANE_BACKOFF_START, T.LANE_BACKOFF_MAX, T.LANE_RAPID_EXIT_S)
      == (SUP.BACKOFF_START, SUP.BACKOFF_MAX, SUP.RAPID_EXIT_S),
      (T.LANE_BACKOFF_START, T.LANE_BACKOFF_MAX, T.LANE_RAPID_EXIT_S))
check("...and the same curve, step for step",
      [T.lane_backoff(n) for n in range(1, 10)]
      == [SUP.next_backoff(n) for n in range(1, 10)],
      [T.lane_backoff(n) for n in range(1, 10)])
check("a lifetime past LANE_RAPID_EXIT_S resets the count, a shorter one grows it",
      T.lane_backoff_after(7, 31) == (0, 5) and T.lane_backoff_after(7, 1) == (8, 60),
      (T.lane_backoff_after(7, 31), T.lane_backoff_after(7, 1)))


@contextlib.contextmanager
def _capturing():
    """The module's log records, for the checks about what it SAYS. Its own console handler
    keeps running - log_console_off's docstring is explicit that a suite keeps it - so the
    long CRITICALs also appear above these checks; the record count is what is graded."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    T.log.addHandler(handler)
    try:
        yield buf
    finally:
        T.log.removeHandler(handler)


def _lines(buf):
    return [l for l in buf.getvalue().splitlines() if l.strip()]


def _run_lane(lane_fn, reporter=None):
    """(attempts, slept) for one lane_with_retry run. Every attempt costs 1s of clock, well
    inside LANE_RAPID_EXIT_S, so this grades the FAILED-START path."""
    clock = [0.0]
    attempts, slept = [], []

    def now():
        return clock[0]

    def fn():
        attempts.append(1)
        clock[0] += 1
        return lane_fn(len(attempts))

    T.lane_with_retry(fn, "mattermost", reporter or T._lane_report_mattermost,
                      sleep=slept.append, now=now)
    return attempts, slept


T.LANE_FAILS.pop("mattermost", None)    # clear the record the lock section left on disk
T.LANE_STATE.clear()
T._LANE_FAILS_LOADED = False
T._lane_state_write()
_seen = []


def _reporter(e, count, same, first, delay, permanent=False):
    _seen.append((count, same, delay))
    T._lane_report_mattermost(e, count, same, first, delay, permanent)


with _capturing() as _log:
    _attempts, _slept = _run_lane(
        lambda n: (_ for _ in ()).throw(RuntimeError("no route to host")) if n <= 3
        else T.lane_up("mattermost", "connected as @the-bot"),
        reporter=_reporter)
check("a lane that fails 3 times and then connects is RETRIED, not exited",
      len(_attempts) == 4, _attempts)
check("...waiting the supervisor's growing curve: 5, 10, 20",
      _slept == [5, 10, 20], _slept)
check("...counting every failed start, so `tinycmdr health` sees attempt 3",
      [(c, s) for c, s, _d in _seen] == [(1, False), (2, True), (3, True)], _seen)
check("...and the recovered lane reads 'up' with its record cleared on disk",
      T.lanes_snapshot()["mattermost"]["state"] == "up"
      and "mattermost" not in failures_on_disk(), failures_on_disk())
check("the log gets ONE line per state change, not one per attempt (3 failures+recovery)",
      len(_lines(_log)) == 2, _lines(_log))

# A DIFFERENT error is news again, and a failed start is a failed start: the backoff keeps
# growing even though lane_down starts its count over for the new error.
T.LANE_STATE.pop("mattermost", None)
T.LANE_FAILS.clear()
T._LANE_FAILS_LOADED = False
_seen2 = []


def _reporter2(e, count, same, first, delay, permanent=False):
    _seen2.append((count, same, delay))
    T._lane_report_mattermost(e, count, same, first, delay, permanent)


_errs2 = ["connection refused", "connection refused", "401 Invalid or expired session"]
with _capturing() as _log2:
    _attempts2, _slept2 = _run_lane(
        lambda n: (_ for _ in ()).throw(RuntimeError(_errs2[n - 1])) if n <= 3
        else T.lane_up("mattermost", "connected as @the-bot"),
        reporter=_reporter2)
check("a CHANGED error is logged again (1 + 1), and recovery once",
      len(_lines(_log2)) == 3, _lines(_log2))
check("...and a REFUSED credential parks on the 10-minute retry, not the growing curve",
      _slept2 == [5, 10, 600], _slept2)
check("...and the line says what to type to fix it",
      any("token set TINYCMDR_MM_TOKEN" in l for l in _lines(_log2)), _lines(_log2))
check("...and the retry count starts over for the NEW error, as lane_down counts them",
      [(c, s) for c, s, _d in _seen2] == [(1, False), (2, True), (1, False)], _seen2)

# A PERMANENT failure is not retried: SystemExit from the lane (a missing token, a bad
# config, a refused Telegram token) leaves the loop at once, as it did before this change.
_slept3 = []


def _dead():
    raise SystemExit(2)


try:
    T.lane_with_retry(_dead, "mattermost", T._lane_report_mattermost,
                      sleep=_slept3.append, now=lambda: 0.0)
    _code3 = None
except SystemExit as _exc3:
    _code3 = _exc3.code
check("a permanent config failure still EXITS (exit 2 is not retried)",
      _code3 == 2 and _slept3 == [], (_code3, _slept3))

# The Telegram lane draws its own line before the retry ever sees it: the API being
# UNREACHABLE is retryable, a token the API REFUSED is not - it answered - and that one keeps
# the exit 2 a human notices (which is also the installer's no-token exit).
_tg_client_before, _config_before = T.TelegramClient, T.CONFIG
# a SHAPE-valid dummy: a token that cannot look like one is refused before the lane
# starts (that is graded elsewhere in this file), so the lane under test here needs
# one that passes the shape gate and then fails at the API.
T.CONFIG = {"telegram": {"token": "1234567890:" + "A" * 35, "allowed_users": ["42"]}}


def _telegram_start_raises(exc):
    class _Client:
        def me(self):
            raise exc
    T.TelegramClient = lambda token: _Client()
    try:
        T.run_telegram()
        return None
    except SystemExit as e:
        return ("exit", e.code)
    except BaseException as e:                         # noqa: BLE001 - the retry is the check
        return ("raise", e)
    finally:
        T.TelegramClient = _tg_client_before


_net = _telegram_start_raises(requests.ConnectionError("api.telegram.org unreachable"))
check("a Telegram lane that cannot REACH the API is raised for the in-process retry",
      _net is not None and _net[0] == "raise"
      and isinstance(_net[1], requests.RequestException), _net)
_refused = _telegram_start_raises(RuntimeError("getMe: Unauthorized"))
check("...but a token the API REFUSED still exits 2 (that one needs a human)",
      _refused == ("exit", 2), _refused)
T.CONFIG = _config_before


def _run_main(argv, **stubs):
    """(returned, raised) for one main() run with the lane entry points stubbed. main() is
    what decides between exit and retry, so this is where "the process does NOT exit" is as
    the operator experiences it."""
    saved = {name: getattr(T, name) for name in stubs}
    for name, fn in stubs.items():
        setattr(T, name, fn)
    argv_before = sys.argv
    sys.argv = list(argv)
    try:
        T.main()
        return True, None
    except BaseException as exc:                       # noqa: BLE001 - the exit is the check
        return False, exc
    finally:
        sys.argv = argv_before
        for name, fn in saved.items():
            setattr(T, name, fn)


def _stub_lane(limit):
    tries = []

    def fn():
        tries.append(1)
        if len(tries) <= limit:
            raise RuntimeError("no route to host")
    return tries, fn


_main_tries, _main_lane = _stub_lane(2)
_main_slept = []
_tg_tries, _tg_lane = _stub_lane(1)
_tg_slept = []
_returned, _raised = _run_main(
    ["tinycmdr.py", "--mattermost"],
    run_bot=_main_lane, validate_startup_config=lambda: "",
    acquire_single_instance_lock=lambda: True, lanes_to_serve=lambda *a, **k: ["mattermost"],
    _mm_token_configured=lambda: True, _lane_wait=_main_slept.append)
_returned_tg, _raised_tg = _run_main(
    ["tinycmdr.py", "--telegram"],
    run_telegram=_tg_lane, _lane_wait=_tg_slept.append)
check("main() retries the Mattermost lane and RETURNS - it does not exit the process",
      _returned and len(_main_tries) == 3 and _main_slept == [5, 10],
      (_returned, _raised, _main_tries, _main_slept))
check("...and the `--telegram` branch is retried the same way",
      _returned_tg and len(_tg_tries) == 2 and _tg_slept == [5],
      (_returned_tg, _raised_tg, _tg_tries, _tg_slept))

# Both tokens, no flag: ONE process serves both lanes. The old rule refused here, so a
# Telegram token added to a Mattermost install never started.
_dual_bot, _dual_bot_lane = _stub_lane(0)
_tg_started = threading.Event()


def _dual_tg():
    _tg_started.set()


_dual_ret, _dual_raised = _run_main(
    ["tinycmdr.py"],                                                  # no flag
    run_bot=_dual_bot_lane, run_telegram=_dual_tg,
    validate_startup_config=lambda: "", acquire_single_instance_lock=lambda: True,
    _mm_token_configured=lambda: True, _tg_token_configured=lambda: True,
    _lane_wait=lambda s: None)
_dual_got = _tg_started.wait(5)
check("both tokens: main() starts Mattermost AND Telegram in one process",
      _dual_ret and _dual_got and _dual_bot == [1],
      (_dual_ret, _dual_raised, _dual_got, _dual_bot))

print()
if FAILS:
    print("%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("lanes, recovery, drift: what the surfaces say is what is true")

# ---- a lane failure must carry a REASON (the fleet Windows box, 2026-10-02) -----------------------
# mattermostautodriver raises InvalidOrMissingParameters(message) where the message is the
# API's empty field: str(exc) was "", the lane stored "no detail", `doctor` printed "no
# detail", and the log got a blank ERROR line per retry - 723 failed starts with no cause
# visible to the operator.
class _Empty(Exception):
    def __str__(self):
        return ""


_bare = T._lane_reason(_Empty())
check("a lane error with an EMPTY message still yields a reason (the class name)",
      _bare.strip() == "_Empty", _bare)
check("...and a normal one keeps its text", "no route to host" in T._lane_reason(RuntimeError("no route to host")))
check("a refused credential is classified PERMANENT",
      T._lane_error_permanent(RuntimeError("HTTP 400 Bad Request at https://x/api/v4/users/me")) is True)
check("...and a network failure is not", T._lane_error_permanent(RuntimeError("no route to host")) is False)

_blank = io.StringIO()
_h = logging.StreamHandler(_blank)
_h.addFilter(T._NoBlankRecords())
_lg = logging.getLogger("mattermostautodriver.client")
_lg.addHandler(_h)
try:
    _lg.error("")
finally:
    _lg.removeHandler(_h)
check("a library logging an empty message is attributed, not left blank",
      "logged an empty message" in _blank.getvalue()
      and "mattermostautodriver" in _blank.getvalue(), _blank.getvalue()[:120])

# the lane and the credential probe agree about the server's URL
check("the Mattermost url is built once, with a non-default port",
      T._mm_base_url({"url": "chat.x.com", "port": 8065}) == "https://chat.x.com:8065"
      and T._mm_base_url({"url": "https://chat.x.com", "port": 443}) == "https://chat.x.com",
      (T._mm_base_url({"url": "chat.x.com", "port": 8065}),))

# ---- a token ALREADY on disk that cannot work is NAMED, not retried as an outage --------
# The tower's own shape (2026-10-02): one 0x16 byte in .env, 723 failed starts, "no detail".
_mm_before = T.CONFIG.get("mattermost")
T.CONFIG["mattermost"] = {"token": "x\x16y", "url": "https://chat.example.com", "port": 443}
try:
    T.run_bot()
    _said = "(no raise)"
except RuntimeError as _e:
    _said = str(_e)
except SystemExit as _e:
    _said = "SystemExit %s" % _e.code
finally:
    T.CONFIG["mattermost"] = _mm_before
check("run_bot refuses a token holding a control character, and says why",
      "unusable" in _said and "control characters" in _said, _said[:160])
check("...naming the command that fixes it",
      "token set TINYCMDR_MM_TOKEN" in _said, _said[:160])
check("...and that failure is PERMANENT, not an outage to wait out",
      T._lane_error_permanent(RuntimeError(_said)) is True, _said[:120])
