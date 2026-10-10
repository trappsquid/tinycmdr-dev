"""test_lane_surface - one merged suite (test_lane_health, test_lane_choice).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: no member needed a namespace rewrite.
"""
import os
import sys


def _run(name, fn):
    """One member, its own snapshot: env, cwd and sys.path restored afterwards."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_path = list(sys.path)
    print("== member %s: start" % name)
    try:
        rc = fn()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
        sys.path[:] = saved_path
    rc = int(rc or 0)
    print("== member %s: exit %d" % (name, rc))
    return rc


def _suite_test_lane_health():
    """What the surfaces say about the lanes, and about a config edit that has not applied.

Two incidents on a live install, 2026-09-28, both invisible until someone went looking:

  * the bot was up, `systemctl` said `active`, `tinycmdr health` named mattermost because a
    TOKEN existed - while the bot could not hear anybody, 510 restarts deep. Every surface
    told the truth about the wrong question: "is the process up" instead of "can it hear me";
  * the operator asked the agent, from Mattermost, to change a setting. The agent wrote
    config.json correctly and the service restarted - and nothing anywhere said that a config
    edit needs a restart to apply.

The web UI's own surface is graded by tests/test_webui.py (`/api/health` carries the lane
state) and by the page suite's dead-lane banner check; these facts are graded where the
other lanes live: `lanes_snapshot()`, `tinycmdr health` (exit code and lane line), the
persisted `logs/state.json`, `tinycmdr doctor`'s lane lines, and `config_drift()`.
Hermetic: the staged copy is the module, so `logs/state.json` lands in the stage.

    python tests/test_lane_surface.py
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
    import os
    import shutil
    import subprocess
    import sys
    import tempfile
    import threading
    from pathlib import Path

    import requests

    BASE = Path(__file__).resolve().parent.parent
    # TINYCMDR_SRC names the module to grade, the same seam tests/test_telegram.py uses: the
    # suite is run against a PRE-FIX copy to prove a check is red before a fix, and against
    # the working tree otherwise.
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
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
    check("no lane appears that this build does not know",
          set(lanes) <= {"mattermost", "telegram", "web"}, list(lanes))
    rc, out, _err = run_verb(T._verb_health)
    check("health names the configured lane and its state on one line",
          "lane mattermost=configured" in out, out)

    # ---------------------------------------------------- the web lane is REPORTED
    # It was written by run_webui and read by nobody (measured 2026-10-05: the live
    # state.json held a current `web` record while /api/health, `tinycmdr health` and doctor
    # showed two lanes - so "the page never started" was invisible to the operator's one
    # "is it alive?" command).
    check("an enabled page with no record is NOT a lane (no news, not a placeholder)",
          "web" not in T.lanes_snapshot(), list(T.lanes_snapshot()))
    T.lane_up("web", "port 8790")
    check("a bound page reads 'up' with the port it bound",
          T.lanes_snapshot().get("web", {}).get("state") == "up"
          and T.lanes_snapshot().get("web", {}).get("detail") == "port 8790",
          T.lanes_snapshot().get("web"))
    T.lane_down("web", "no token configured")
    check("...and a page that cannot start reads 'failed' with the reason",
          T.lanes_snapshot().get("web", {}).get("state") == "failed"
          and T.lanes_snapshot().get("web", {}).get("detail") == "no token configured",
          T.lanes_snapshot().get("web"))
    _web_cfg = T.CONFIG.setdefault("web", {})
    _had_enabled = "enabled" in _web_cfg
    _web_cfg["enabled"] = False
    check("a page the config turns off is not reported at all",
          "web" not in T.lanes_snapshot(), list(T.lanes_snapshot()))

    # A lane that STARTED and then went deaf never raises, so nothing calls lane_down: the
    # Telegram poll loop retries internally for ever. The grace decision is graded here.
    _due = getattr(T, "lane_poll_failure_due", None)
    check("a lane whose polls keep failing is reported down after the grace window",
          callable(_due) and _due(None, 5000.0) is False
          and _due(1000.0, 1000.0 + 299, grace=300.0) is False
          and _due(1000.0, 1000.0 + 300, grace=300.0) is True,
          "grace window")
    if _had_enabled:
        _web_cfg["enabled"] = True
    else:
        _web_cfg.pop("enabled", None)
    T.lane_up("web", "port 8790")            # leave the record clean for the checks below

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

    # ---- a lane failure must carry a REASON (a Windows install, 2026-10-02) -----------------------
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

    # =============== run 12, section D: lane plumbing (A-179..A-186) ===============
    # A blank disk, so a record left by the checks above cannot decide these counts.
    T.atomic_write_text(T.LANE_STATE_FILE, json.dumps({}))
    T.LANE_FAILS.clear()
    T.LANE_STATE.clear()
    getattr(T, "_LANE_TOUCHED", set()).clear()     # a pre-fix build has no such set
    T._LANE_FAILS_LOADED = False

    # A-179/A-180: the repeat count keys on the lane and the reason CLASS, not the literal
    # text. The poll loop's own message carries a minute count and the transport's carries a
    # different errno each attempt, so a lane failing stubbornly reset its count every time.
    _a1 = T.lane_down("mattermost", "poll failing for 5 min: Connection reset by peer")
    _a2 = T.lane_down("mattermost", "poll failing for 5 min: Temporary failure in name resolution")
    check("A-179: a repeat whose message varies still counts as the SAME failure",
          (_a1[0], _a1[1], _a2[0], _a2[1]) == (1, False, 2, True), (_a1, _a2))
    _b1 = T.lane_down("mattermost", "401 Invalid or expired session")
    _b2 = T.lane_down("mattermost", "connection reset by peer")
    check("A-179: ...but a different CLASS is still a fresh failure, not attempt 3",
          (_b1[0], _b1[1], _b2[0], _b2[1]) == (1, False, 1, False), (_b1, _b2))
    _rc = getattr(T, "_lane_reason_class", None)
    check("A-180: the poll loop's varying message normalises to one class",
          callable(_rc)
          and _rc("poll failing for 5 min: ConnectionError(1, 'reset')")
          == _rc("poll failing for 10 min: ConnectionError(2, 'reset')"),
          "no _lane_reason_class" if not callable(_rc) else "")

    # A-181: another process owns a lane this one has never written; this process's next
    # write must carry that lane's record over instead of replacing the whole map.
    _other = T._lane_state_read()
    _other.setdefault("lanes", {})["mattermost"] = {"ok": True, "detail": "someone else's",
                                                    "since": 1.0}
    T.atomic_write_text(T.LANE_STATE_FILE, json.dumps(_other))
    getattr(T, "_LANE_TOUCHED", set()).discard("mattermost")   # not ours
    T.LANE_STATE.pop("mattermost", None)
    T.LANE_FAILS.pop("mattermost", None)
    T._lane_state_write()
    check("A-181: a lane this process does not own survives its next write",
          (T._lane_state_read().get("lanes") or {}).get(
              "mattermost", {}).get("detail") == "someone else's",
          T._lane_state_read().get("lanes"))
    T.lane_up("mattermost", "connected as @me")    # now it IS ours, and ours wins
    check("A-181: ...while a lane this process DOES own is still authoritative",
          (T._lane_state_read().get("lanes") or {}).get(
              "mattermost", {}).get("detail") == "connected as @me",
          T._lane_state_read().get("lanes"))

    # A-182: a wall clock that steps backwards must not silence the poll-failure report.
    check("A-182: a clock that stepped backwards reports the lane, not silence",
          T.lane_poll_failure_due(1000.0, 10.0) is True
          and T.lane_poll_failure_due(0.0, 299.0) is False
          and T.lane_poll_failure_due(0.0, 300.0) is True,
          (T.lane_poll_failure_due(1000.0, 10.0), T.lane_poll_failure_due(0.0, 299.0)))

    # A-183: a bare 401 in an id or a port is NOT a refused credential.
    check("A-183: an id or port that merely contains 401 is not a refused credential",
          T._lane_error_permanent(RuntimeError("update id 401 could not be delivered")) is False
          and T._lane_error_permanent(RuntimeError("connect to 10.0.0.9:401 failed")) is False,
          (T._lane_error_permanent(RuntimeError("update id 401 could not be delivered")),
           T._lane_error_permanent(RuntimeError("connect to 10.0.0.9:401 failed"))))
    check("A-183: ...but the real status shape still is",
          T._lane_error_permanent(RuntimeError("HTTP 401 Unauthorized")) is True
          and T._lane_error_permanent(RuntimeError("status: 403 Forbidden")) is True,
          (T._lane_error_permanent(RuntimeError("HTTP 401 Unauthorized")),
           T._lane_error_permanent(RuntimeError("status: 403 Forbidden"))))

    # A-184: a NEGATIVE uptime (a backwards clock) is a failed start, never a healthy reset -
    # and a negative failure count is never handed to the curve.
    check("A-184: a negative uptime reads as a failed start, not a reset",
          T.lane_backoff_after(7, -31) == (8, 60) and T.lane_backoff_after(7, 31) == (0, 5),
          (T.lane_backoff_after(7, -31), T.lane_backoff_after(7, 31)))
    check("A-184: a negative failure count is clamped before the curve sees it",
          T.lane_backoff_after(-5, -1) == (1, 5), T.lane_backoff_after(-5, -1))

    # A-185: a lane that exits 2 records WHY before it goes, so `health` does not keep
    # reporting the exit path's last "up" (the pair-lane process keeps running for the other).
    T.lane_up("telegram", "connected as @stale")   # an earlier healthy run's record
    _tg_before = T.CONFIG.get("telegram")
    T.CONFIG["telegram"] = {}
    try:
        T.run_telegram()
        _tg_code = None
    except SystemExit as _e:
        _tg_code = _e.code
    finally:
        T.CONFIG["telegram"] = _tg_before
    _tg_rec = (T._lane_state_read().get("lanes") or {}).get("telegram", {})
    check("A-185: a lane that exits 2 writes its true state, the stale 'up' is gone",
          _tg_code == 2 and _tg_rec.get("ok") is False
          and "no Telegram token" in str(_tg_rec.get("detail")), (_tg_code, _tg_rec))

    # A-186: a record whose writing process is DEAD is the last known state, not "now".
    _dead_proc = subprocess.Popen([sys.executable, "-c", "pass"])
    _dead_proc.wait()
    _dead_pid = _dead_proc.pid
    _alive = getattr(T, "_lane_pid_alive", None)
    check("A-186: the liveness helper knows a live pid and a reaped one",
          callable(_alive) and _alive(os.getpid()) is True and _alive(_dead_pid) is False,
          "no _lane_pid_alive" if not callable(_alive)
          else (_alive(os.getpid()), _alive(_dead_pid)))
    T.atomic_write_text(T.LANE_STATE_FILE, json.dumps({
        "pid": _dead_pid, "updated": 1.0,
        "lanes": {"mattermost": {"ok": True, "detail": "up once", "since": 1.0}}}))
    T.LANE_STATE.clear()
    T._LANE_FAILS_LOADED = False
    _snap_dead = T.lanes_snapshot().get("mattermost", {})
    check("A-186: a dead writer's record is marked stale, not presented as current",
          _snap_dead.get("stale") is True and _snap_dead.get("pid_alive") is False
          and _snap_dead.get("from_pid") == _dead_pid, _snap_dead)
    T.atomic_write_text(T.LANE_STATE_FILE, json.dumps({
        "pid": os.getpid(), "updated": 1.0, "lanes": {}}))
    T.LANE_STATE.clear()
    T._LANE_FAILS_LOADED = False
    check("A-186: ...and a live writer's record is not stale",
          T.lanes_snapshot().get("mattermost", {}).get("stale") is False,
          T.lanes_snapshot().get("mattermost"))

    # ...and the PRINTERS have to surface it. A-186 computed the flag and no surface read it, so
    # health and /status handed a dead lane's last "up" to the operator as current state - the
    # monitoring line a supervisor polls every minute (run 24, A-2026-10-07-69). The state WORD
    # carries it, because every consumer renders `state`; the "was up" detail belongs in prose.
    T.atomic_write_text(T.LANE_STATE_FILE, json.dumps({
        "pid": _dead_pid, "updated": 1.0,
        "lanes": {"mattermost": {"ok": True, "detail": "up once", "since": 1.0}}}))
    T.LANE_STATE.clear()
    T._LANE_FAILS_LOADED = False
    _h_rc, _h_out, _h_err = run_verb(T._verb_health)
    check("A-69: health's lane word is 'stale', never a dead record's 'up'",
          "lane mattermost=stale" in _h_out and "mattermost=up" not in _h_out, _h_out)
    check("A-69: ...and stderr says it WAS up, whose record it is, and when",
          "last known state was UP" in _h_err and str(_dead_pid) in _h_err, _h_err)
    _s_rc, _s_out, _s_err = run_verb(T._verb_status)
    check("A-69: /status prints the same word (both read the one derived state)",
          "mattermost=stale" in _s_out, _s_out[-400:])
    check("A-69: a stale record is not a FAILURE - it does not change health's exit code",
          _h_rc == (0 if T._verb_running() is True else 1), _h_rc)
    T.atomic_write_text(T.LANE_STATE_FILE, json.dumps({
        "pid": os.getpid(), "updated": 1.0, "lanes": {}}))
    T.LANE_STATE.clear()
    T._LANE_FAILS_LOADED = False

    # ---- the deaf-listener probe sees the state it hunts --------------------------------
    # `_last_msg` is 0 on every FRESH websocket (the driver builds one per connect) and the
    # socket object is None whenever there is no connection at all, so the old
    # `last_msg > 0` test skipped exactly the deafness the watchdog exists for - a reconnect
    # that never completes, an expired token, a server refusing the upgrade (measured
    # 2026-10-07). The probe's three states, graded with an injected clock.
    _D = T.MattermostDispatcher()


    class _Ws:
        """A stand-in for the vendor's websocket object (it is not this module's class)."""

        def __init__(self, last):
            if last is not None:
                self._last_msg = last


    _now = 1000.0
    _D._ws_attach_time = _now - 600
    _D._ws_missing_since = None
    check("websocket liveness: a socket that just spoke is not deaf",
          _D._listener_deaf(_Ws(_now - 5), _now - 5, now=_now) == "")
    check("...a socket quiet for 15+ minutes is",
          "no message" in _D._listener_deaf(_Ws(_now - 901), _now - 901, now=_now),
          _D._listener_deaf(_Ws(_now - 901), _now - 901, now=_now))
    _D._ws_missing_since = None
    check("...and a socket object that is GONE counts as deaf, not as unknown",
          _D._listener_deaf(None, None, now=_now) == ""
          and _D._listener_deaf(None, None, now=_now + 200) != "",
          (_D._ws_missing_since, _D._listener_deaf(None, None, now=_now + 200)))
    _warned_before = _D._ws_probe_warned
    check("a driver with no `_last_msg` to read is named once, not passed off as healthy",
          _D._listener_deaf(_Ws(None), None, now=_now) == "" and _D._ws_probe_warned
          and not _warned_before, _D._ws_probe_warned)

    # ---- the deaf restart frees the folder lock BEFORE spawning the replacement --------
    # The replacement takes the single-instance lock at startup (non-blocking): spawned
    # while this process still held it, it aborted "another tinycmdr is already running"
    # inside the 0.5s window, and the old process then exited - a dead bot with nothing
    # left to relaunch it.
    _R = T.MattermostDispatcher()
    _order = []
    _saved_deaf = (T.restart_owner, T._release_lock, T._spawn_replacement, T.os._exit)
    _real_release = T._release_lock

    def _watched_release():
        _order.append("release")
        _real_release()

    def _probe_spawn():
        _order.append("spawn")

    def _probe_exit(code):
        _order.append("exit")
        raise SystemExit(code)

    T.restart_owner = lambda: "self"
    T._release_lock = _watched_release
    T._spawn_replacement = _probe_spawn
    T.os._exit = _probe_exit
    try:
        try:
            _R._restart_deaf("graded")
        except SystemExit:
            pass
    finally:
        (T.restart_owner, T._release_lock, T._spawn_replacement,
         T.os._exit) = _saved_deaf
    check("the deaf restart frees the lock BEFORE spawning the replacement",
          _order[:2] == ["release", "spawn"] and "exit" in _order, _order)

    print()
    if FAILS:
        print("%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
        sys.exit(1)
    print("lane plumbing: reason classes, per-lane merge, clock safety, liveness")


def _suite_test_lane_choice():
    """Which lane a bare start serves - and the three states that are all valid.

The lane rules this grades, all of them visible only by RUNNING the file:

  * no token and the page ON (the default) is a serving install: the first start mints
    the token the page requires and holds the process open - the shape the autostart
    agent runs on. The CLI-only end state is the page OFF (`--no-web` or
    web.enabled: false), which explains itself and stops cleanly (exit 0);
  * the shipped placeholders ("PASTE_BOT_TOKEN_HERE", "your-mattermost-user-id") are
    NOT a lane - counting them made a fresh install look like a Mattermost host with a
    broken URL, and `tinycmdr health` claimed a `mattermost` lane on a CLI-only box;
  * BOTH tokens set serves BOTH lanes in one process (Mattermost on the main thread,
    Telegram on a daemon one), so adding a Telegram token never leaves that lane dark;
  * `--mattermost` with no token says so instead of guessing.

    python tests/test_lane_surface.py
"""
    import importlib.util
    import json
    import os
    import shutil
    import socket
    import subprocess
    import sys
    import tempfile
    import time
    import urllib.request
    from pathlib import Path

    SRC = Path(__file__).resolve().parent.parent / "tinycmdr.py"
    LLM = {"base_url": "http://127.0.0.1:9/v1", "model": "dead"}

    PASSES, FAILS = [], []
    # The last child's streams, kept apart: `said` is stdout + stderr + the log, and a log
    # tail then hides what a check is actually about (a card, an answer, on STDOUT - which is
    # what the automation reads). Written by run(), read by the checks that need the split.
    LAST_IO = ["", ""]


    def check(cond, what, extra=""):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}\n     {extra}")
        else:
            PASSES.append(what)
            print(f"ok   {what}")


    def stub_llm(answer="stub answer"):
        """A live endpoint that answers one chat completion. Returns (server, base_url).

    The one-shot door's exit code has TWO sides - a failed run must not read as success
    and a delivered answer must not read as failure - and only a live stub can grade the
    second half (run 25, A-2026-10-07-68). Shut it down with server.shutdown().
    """
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class _Stub(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._send({"data": [{"id": "main", "object": "model",
                                      "max_model_len": 32768}]})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                self._send({"id": "c1", "object": "chat.completion", "model": "main",
                            "choices": [{"index": 0, "finish_reason": "stop",
                                         "message": {"role": "assistant", "content": answer}}],
                            "usage": {"prompt_tokens": 8, "completion_tokens": 3,
                                      "total_tokens": 11}})

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, "http://127.0.0.1:%d/v1" % srv.server_address[1]


    def run(dirpath, args=(), tokens=(), with_mm=False, timeout=60, llm=None, config=True):
        """Run the harness in `dirpath` and return (exit_code, stdout+stderr+log).

    A child that SERVES (the page holds the process open) never exits on its own, so
    the deadline is the caller's: a timeout comes back as the string "serving" with
    everything the child had printed, instead of an exception that grades the test.
    For a serving child, prefer serve_and_probe(): text from a killed process is
    unreliable, the page answering is not. `config` is True (the minimal config below),
    False (no config at all), or a DICT the caller owns - staged as the config.json.
    """
        cfg = {"llm": llm or LLM}
        if with_mm:
            cfg["mattermost"] = {"url": "chat.invalid", "scheme": "https", "port": 443,
                                 "token": "", "allowed_users": ["u1"]}
            cfg["telegram"] = {"token": "", "allowed_users": ["4242"]}
        if isinstance(config, dict):
            (dirpath / "config.json").write_text(json.dumps(config), encoding="utf-8")
        elif not config:
            (dirpath / "config.json").unlink(missing_ok=True)
        else:
            (dirpath / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        if tokens:
            (dirpath / ".env").write_text("".join(f"{k}={v}\n" for k, v in tokens),
                                          encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("TINYCMDR_")}
        env["HOME"] = str(dirpath)
        env["TINYCMDR_NO_BROWSER"] = "1"          # a test never opens a browser tab
        # FILES, not pipes: a piped child's stdout came back EMPTY on windows-latest while its
        # stderr carried the whole run (measured 2026-10-08, with fd 1 confirmed a FIFO and the
        # raw sentinel write reporting success) - so the pipe itself was the broken half. Files
        # also cannot deadlock on a full pipe buffer, which a run that prints a card can fill.
        out_f, err_f = dirpath / "child.out", dirpath / "child.err"
        try:
            with open(out_f, "wb") as _o, open(err_f, "wb") as _e:
                r = subprocess.run([sys.executable, str(dirpath / "tinycmdr.py"), *args],
                                   cwd=str(dirpath), env=env, stdout=_o, stderr=_e,
                                   timeout=timeout, stdin=subprocess.DEVNULL)
            r.stdout = out_f.read_text(encoding="utf-8", errors="replace")
            r.stderr = err_f.read_text(encoding="utf-8", errors="replace")
            # `or ""`: seen on windows-latest, where one of the two came back None
            # (measured 2026-10-08: TypeError: ... +: 'NoneType' and 'str'), and the suite
            # dying hides every check after it. The suite's _text() guards the timeout
            # path the same way.
            code, said = r.returncode, (r.stdout or "") + (r.stderr or "")
            LAST_IO[0], LAST_IO[1] = r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired as e:
            code = "serving"
            # The FILES are this run's output; a TimeoutExpired only carries bytes when the
            # stream was a pipe (measured: ubuntu CI) and None when it was one.
            out_t = out_f.read_text(encoding="utf-8", errors="replace") if out_f.exists() \
                else _text(e.stdout)
            err_t = err_f.read_text(encoding="utf-8", errors="replace") if err_f.exists() \
                else _text(e.stderr)
            said = out_t + err_t
            LAST_IO[0], LAST_IO[1] = out_t, err_t
        logf = dirpath / "tinycmdr.log"
        if logf.exists():
            said += logf.read_text(encoding="utf-8", errors="replace")
        return code, said


    def _text(chunk):
        if chunk is None:
            return ""
        return chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")


    def load(dirpath):
        """Import the staged file in-process, for the rules that are not about the process."""
        spec = importlib.util.spec_from_file_location("lane_" + dirpath.name,
                                                     dirpath / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod


    def free_port():
        """A port nothing holds right now (the child binds exactly it and we poll it)."""
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port


    def serve_and_probe(dirpath, port, deadline=45, config=None):
        """Start the file with the page on `port`, wait for the PAGE to answer, kill it.

    The page is the evidence, not the child's words: a process killed at a deadline has
    unflushed prints in its pipe AND an unflushed log tail (the log rides a background
    listener), so text greps flake by platform - measured, and the reason this helper
    exists. Returns (served, authed, said): served is /api/health answering 200, authed
    is a gated route answering 200 with the token the child itself minted into .env.

    `config` runs the same probe against a config the CALLER owns (the example-as-shipped
    case); the default is the minimal page config.
    """
        if config is None:
            config = {"llm": LLM, "web": {"enabled": True, "host": "127.0.0.1", "port": port}}
        (dirpath / "config.json").write_text(json.dumps(config), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("TINYCMDR_")}
        env["HOME"] = str(dirpath)
        # a test must not put tabs in somebody's browser: every staged child that serves
        # would otherwise auto-open the page (macOS says a browser is available)
        env["TINYCMDR_NO_BROWSER"] = "1"
        proc = subprocess.Popen([sys.executable, str(dirpath / "tinycmdr.py")],
                                cwd=str(dirpath), env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        served = authed = False
        t0 = time.time()
        while time.time() - t0 < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port,
                                            timeout=1) as r:
                    if r.status == 200:
                        served = True
                        break
            except Exception:                                        # noqa: BLE001
                time.sleep(0.5)
        tok = ""
        envf = dirpath / ".env"
        if envf.exists():
            for ln in envf.read_text(encoding="utf-8").splitlines():
                if ln.startswith("TINYCMDR_WEB_TOKEN="):
                    tok = ln.split("=", 1)[1].strip()
        if served and tok:
            try:
                req = urllib.request.Request("http://127.0.0.1:%d/api/tasks" % port,
                                             headers={"X-Tinycmdr-Token": tok})
                with urllib.request.urlopen(req, timeout=5) as r:
                    authed = r.status == 200
            except Exception:                                        # noqa: BLE001
                authed = False
        if proc.poll() is None:
            proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:                                            # noqa: BLE001
            out, err = "", ""
        said = (out or "") + (err or "")
        logf = dirpath / "tinycmdr.log"
        if logf.exists():
            said += logf.read_text(encoding="utf-8", errors="replace")
        return served, authed, said


    def main():
        work = Path(tempfile.mkdtemp(prefix="fblane-"))
        try:
            for name in ("cli_only", "both", "mm_only", "tg_only", "health", "fresh", "once",
                         "example"):
                d = work / name
                d.mkdir(parents=True, exist_ok=True)
                shutil.copy2(SRC, d / "tinycmdr.py")

            # -- no token and the page on: the page is the door ------------------------
            # The page's token is MINTED at first start now, so a bare run on a lane-less
            # box does not stop: it mints, serves the page and holds the process open -
            # which is what the installer's autostart agent relies on. The CLI-only end
            # state is the page OFF (next case).
            port = free_port()
            served, authed, said = serve_and_probe(work / "cli_only", port)
            check(served, "no token, page on: the page answers on the configured port")
            check(authed, "and the token it minted gates it (a gated route: 200)")
            check("cannot start" not in said, "it is NOT a startup abort")
            _env = (work / "cli_only" / ".env").read_text(encoding="utf-8")
            check("TINYCMDR_WEB_TOKEN=" in _env, "the minted token lands in .env", _env[-120:])

            # -- a config from the SHIPPED example is a page install, not a broken MM box ----
            # config.example.json's allowlist holds its own placeholder
            # (REPLACE_WITH_YOUR_MATTERMOST_USER_ID) and the installers wrote the example into
            # config.json (the Windows and Linux ones copied it verbatim), so a fresh,
            # page-only install carried a Mattermost intent nobody chose: the harness demanded
            # a token, bare `tinycmdr` aborted "no Mattermost bot token", and the page never
            # came up (measured 2026-10-08, a fresh Windows install of v1.0.93). The example's
            # keys are the schema's outer edge too, so a start from it must draw no
            # unknown-key warnings.
            d = work / "example"
            shutil.copy2(SRC.parent / "config.example.json", d / "config.example.json")
            example = json.loads((SRC.parent / "config.example.json").read_text(encoding="utf-8"))
            port = free_port()
            example.setdefault("web", {})["enabled"] = True
            example["web"]["host"] = "127.0.0.1"
            example["web"]["port"] = port
            served, authed, said = serve_and_probe(d, port, config=example)
            check(served, "a config from the shipped example serves the page")
            check("cannot start" not in said and "no Mattermost bot token" not in said,
                  "...and no Mattermost token is demanded for it", said[-400:])
            check("is not a key the harness reads" not in said,
                  "...and the example's own keys draw no unknown-key warning", said[-400:])

            # -- every door's line must reach STDOUT -----------------------------
            # stdout is what cron, ssh and CI parse; a run that prints only to stderr is
            # invisible to all three. Measured 2026-10-08 on windows-latest: a piped child's
            # stdout was EMPTY for the whole once-path while its stderr carried the turn logs
            # and the exit-1 line - so a delivered answer looked exactly like a silent failure.
            # The bare-python figure in the evidence is the control: it says whether the
            # platform captured stdout at all.
            code, _ = run(work / "cli_only", args=("--version",))
            _bare = subprocess.run([sys.executable, "-c", "print('x')"],
                                   capture_output=True, text=True).stdout
            check(LAST_IO[0].strip() != "",
                  "a verb's line reaches stdout, not just stderr",
                  "STDOUT=%r STDERR=%r bare-python-stdout=%r"
                  % (LAST_IO[0][-200:], LAST_IO[1][-200:], _bare))

            # -- the CLI-only end state: no chat lane and the page OFF ----------------
            code, said = run(work / "cli_only", args=("--no-web",))
            check(code == 0, f"no token and --no-web stops cleanly, exit 0 ({code})")
            check("no chat lane is configured" in said and "--cli" in said,
                  "and says an install with no chat account is CLI-only", said[-400:])
            check("cannot start" not in said, "it is NOT a startup abort")
            check("Serving the page" not in said, "and it does not serve a page it was told to skip",
                  said[-300:])

            # -- the one-shot door's EXIT CODE carries what its card says ---------------
            # `--once` is the door cron, ssh and CI use. A run whose endpoint never answered
            # printed "the task did not run" and returned 0, so a pipeline advanced on a run
            # that did nothing (run 24 filed it, run 25 re-verified it on v1.0.88,
            # A-2026-10-07-68). Both sides are graded, because a code that is always 1 would
            # break the other half just as quietly.
            code, said = run(work / "cli_only", args=("--no-web", "--once", "say hi"),
                             timeout=120)
            check(code == 1, f"--once against a dead endpoint exits 1, not 0 ({code})",
                  said[-300:])
            # -1500, not -300: the card is what this check is about, and a Windows-only
            # failure's cause sits further back than the stderr/log tail (measured
            # 2026-10-08: -300 showed only the last log line of a run whose card was
            # missing, which is not enough to tell a skipped card from a broken run).
            check("did not run" in LAST_IO[0],
                  "...and the card still says what happened",
                  "STDOUT head=%r tail=%r STDERR head=%r tail=%r"
                  % (LAST_IO[0][:120], LAST_IO[0][-120:],
                     LAST_IO[1][:160], LAST_IO[1][-200:]))
            check("exit 1: the run did not reach the model" in said,
                  "...and stderr names the verb that explains it", said[-300:])
            _srv, _url = stub_llm("stub answer")
            try:
                code, said = run(work / "cli_only", args=("--no-web", "--once", "say hi"),
                                 timeout=120, llm={"base_url": _url, "model": "main"})
            finally:
                _srv.shutdown()
                _srv.server_close()
            check(code == 0, f"...and a delivered answer still exits 0 ({code})", said[-300:])
            check("stub answer" in LAST_IO[0], "with the answer on stdout",
                  "STDOUT head=%r tail=%r STDERR head=%r tail=%r"
                  % (LAST_IO[0][:120], LAST_IO[0][-120:],
                     LAST_IO[1][:160], LAST_IO[1][-200:]))

            # -- a box with NO config.json is told so at this door ----------------------
            # The CLI/`--once` door deliberately runs without a config (it is the door that works
            # on one) and it used to say nothing: a fresh install's first command drew a confident
            # banner naming the shipped placeholder endpoint, and the only mention of config.json
            # in the whole run was an unrelated warning (run 24, A-2026-10-07-70).
            code, said = run(work / "fresh", args=("--no-web", "--once", "hi"),
                             config=False, timeout=120)
            check("config.example.json" in said and "no config.json" in said,
                  "a run with no config.json says so, and names the example to copy",
                  said[-400:])
            check(code == 1,
                  "...and it still exits 1 (the shipped default endpoint answers nobody)", code)
            code, said = run(work / "cli_only", args=("--no-web", "--once", "say hi"), timeout=120)
            check("no config.json" not in said,
                  "a box WITH a config.json gets no such note", said[-300:])

            # -- the STARTUP ORDER on a box that cannot start ---------------------------
            # The page used to be raised BEFORE the validation, so a new operator got a browser
            # tab at a port that died a moment later and a tokenized link pointing at nobody
            # (run 24, A-2026-10-07-73) - and the failed start then slept 30 seconds "for
            # pythonw" on a box with no console at all, stretching every crash-restart cycle
            # (A-2026-10-07-72). Both are visible at the process boundary.
            _port = free_port()
            _t0 = time.time()
            code, said = run(work / "fresh",
                             args=("--mattermost", "--no-browser", "--web-port", str(_port)),
                             config=False, timeout=90)
            _dt = time.time() - _t0
            check(code == 2 and "config.example.json" in said,
                  "a cold --mattermost refuses with the config sentence", (code, said[-200:]))
            check("page:" not in said and "web UI listening" not in said,
                  "A-73: ...and NO page is raised before it refuses", said[-300:])
            check(_dt < 15,
                  "A-72: ...and it does not sit 30s on the way out", "took %.1fs" % _dt)

            # -- --mattermost with a VALIDATING config: still no page before the refusal --
            # The page was raised before the token check, so a lane-less host with web on
            # (this one) opened a browser at a server that died the moment the check refused
            # - the same curse A-73 fixed for the config error, one branch over
            # (A-2026-10-08-138).
            d = work / "mm_page"
            d.mkdir(exist_ok=True)
            shutil.copy2(SRC, d / "tinycmdr.py")
            _port = free_port()
            code, said = run(d, args=("--mattermost",),
                             config={"llm": LLM,
                                     "web": {"enabled": True, "host": "127.0.0.1",
                                             "port": _port}},
                             tokens=(("TINYCMDR_WEB_TOKEN", "t" * 32),), timeout=60)
            check(code == 2 and "--mattermost was given" in said,
                  "a valid, token-less --mattermost refuses (rc 2)", (code, said[-300:]))
            check("page:" not in said and "web UI listening" not in said,
                  "A-138: ...and NO page is raised before it refuses", said[-400:])

            # -- a second start on the same folder names the LOOK-THROUGH door ------------
            # POSIX locks the install FOLDER itself, so there is no lock file to delete;
            # advising one hands the next start a fresh lock and a second bot on the same
            # token (A-2026-10-08-131). Hold the lock IN-PROCESS, exactly as a running bot
            # does: a serving child as the holder raced under the CI's --jobs (measured
            # 2026-10-09, macos-latest - the holder never answered), and the message is what
            # this check grades.
            d = work / "lockhold"
            d.mkdir(exist_ok=True)
            shutil.copy2(SRC, d / "tinycmdr.py")
            (d / "config.json").write_text(json.dumps({"llm": LLM}), encoding="utf-8")
            m = load(d)
            _held = m.acquire_single_instance_lock()
            check(_held is True, "the test process holds the folder's lock like a bot", _held)
            try:
                code, said = run(d, timeout=30)
                check(code == 3 and "already running from this folder" in said,
                      "a second start is refused (rc 3)", (code, said[-300:]))
                check("tinycmdr status" in said and "delete tinycmdr.lock" not in said,
                      "A-131: ...naming the look-through door, never the lock file",
                      said[-500:])
            finally:
                m._release_lock()

            # -- `--once` takes the TASK, and only the task ----------------------------
            # A separate flag after `--once` used to be swallowed into the prompt - the model was
            # asked "--cli hello" (read back from the session the run wrote: run 24,
            # A-2026-10-07-74) - and `--once` with nothing after it drew a banner and opened a
            # full interactive session that then ate the script's stdin (A-2026-10-07-75).
            code, said = run(work / "once", args=("--no-web", "--once", "--cli", "hello"),
                             timeout=120)
            check("read as flags" in said and "--cli" in said,
                  "A-74: a flag after --once is named, not swallowed into the prompt", said[-300:])
            _stored = []
            for _f in sorted((work / "once" / "sessions").glob("*.json")):
                try:
                    _msgs = json.loads(_f.read_text(encoding="utf-8"))
                except Exception:                                    # noqa: BLE001
                    continue
                for _m in (_msgs if isinstance(_msgs, list) else []):
                    if isinstance(_m, dict) and _m.get("role") == "user":
                        _stored.append(str(_m.get("content")))
            check(_stored and all(s == "hello" for s in _stored),
                  "A-74: ...and the prompt the model received is exactly the text", _stored[:3])
            code, said = run(work / "once", args=("--no-web", "--once"), timeout=60)
            check(code == 2 and "needs the task" in said,
                  "A-75: --once with no task is a usage error, not a session", (code, said[-200:]))
            check("type at any time" not in said and "Type /tinycmdr" not in said,
                  "A-75: ...and it does not fall through to the interactive banner", said[-300:])

            # -- the shipped placeholders are not a lane -----------------------------
            # Asserted in-process. Grepping the child's LOG for "CLI-only install" was a race -
            # the log listener need not have flushed its last lines when a fast child exits -
            # which macOS won and ubuntu lost, so it graded the platform, not the rule. The run
            # here is only what stages this case's config.json; the rule is read off the module.
            # `--no-web`: this run exists only to stage the case's config.json (the rule is
            # read off the module, below). Without it the page is the door now, and the child
            # mints a token, serves 8790 and holds the port until the deadline.
            run(work / "health", args=("--no-web",), timeout=30)
            saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("TINYCMDR_")}
            try:
                m = load(work / "health")
                check(m._chat_lane_configured() is False,
                      "the shipped placeholder token is not treated as a Mattermost lane",
                      repr(m.CONFIG["mattermost"].get("token")))
                err = m.validate_startup_config()
                check(err is None,
                      "so a token-less install is a CLI-only one, not a startup error", err)
            finally:
                os.environ.update(saved)
            env = {k: v for k, v in os.environ.items() if not k.startswith("TINYCMDR_")}
            env["HOME"] = str(work / "health")
            r = subprocess.run([sys.executable, str(work / "health" / "tinycmdr.py"), "health"],
                               cwd=str(work / "health"), env=env, capture_output=True,
                               text=True, timeout=60)
            _hsaid = (r.stdout or "") + (r.stderr or "")
            check("lane none" in _hsaid,
                  "and `health` reports no lane, not a placeholder one",
                  _hsaid[-200:])

            # -- both tokens: BOTH lanes are served --------------------------------
            # Graded in-process: actually RUNNING this would open a Mattermost connection and
            # long-poll Telegram, so the config is staged as files and the rule read off the
            # module. The old rule refused to guess (exit 2) and left a box that added a
            # Telegram token serving NOTHING - the lane was never started.
            (work / "both" / "config.json").write_text(json.dumps({
                "llm": LLM,
                "mattermost": {"url": "chat.invalid", "scheme": "https", "port": 443,
                               "token": "", "allowed_users": ["u1"]},
                "telegram": {"token": "", "allowed_users": ["4242"]},
            }), encoding="utf-8")
            (work / "both" / ".env").write_text(
                "TINYCMDR_MM_TOKEN=mm-tok\nTINYCMDR_TG_TOKEN=tg-tok\n", encoding="utf-8")
            saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("TINYCMDR_")}
            try:
                m = load(work / "both")
                os.environ["TINYCMDR_MM_TOKEN"] = "mm-tok"
                os.environ["TINYCMDR_TG_TOKEN"] = "tg-tok"
                check(m.lanes_to_serve([]) == ["mattermost", "telegram"],
                      "both tokens serve BOTH lanes in one process", m.lanes_to_serve([]))
                check(m.lanes_to_serve(["--telegram"]) == ["telegram"]
                      and m.lanes_to_serve(["--mattermost"]) == ["mattermost"],
                      "a flag still forces a single lane", None)
            finally:
                os.environ.update(saved)

            # -- --mattermost with no token ------------------------------------------
            code, said = run(work / "mm_only", args=["--mattermost"])
            check(code == 2 and "--mattermost was given" in said,
                  f"--mattermost without a token says so ({code})", said[-300:])
        finally:
            shutil.rmtree(work, ignore_errors=True)

        print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
        return 1 if FAILS else 0
    return main()


def main():
    rc = 0
    for name, fn in (("test_lane_health", _suite_test_lane_health), ("test_lane_choice", _suite_test_lane_choice)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
