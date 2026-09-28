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
import fcntl
import importlib.util
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

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
_kind, _fd = T._lock_target()          # (kind, fd): the lock target is a descriptor
fcntl.flock(_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)      # pretend to be the running bot
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
    fcntl.flock(_fd, fcntl.LOCK_UN)

print()
if FAILS:
    print("%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("lanes, recovery, drift: what the surfaces say is what is true")
