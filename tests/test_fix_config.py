"""A config.json written by an older build neither warns nor leaks.

Two behaviours, one file, because both live in load_config's drop path:

- A key an older build READ and this one does not (`agent.notes_keep_entries`,
  `agent.tasks_max_open`, ... - the `_RETIRED_CONFIG_KEYS` table) must not raise the
  "not a key the harness reads" warning: on a long-lived box that warning fired for
  every retired key on every start and every verb, so the line that IS a typo drowned
  (A-2026-10-12-01, measured 2026-10-12).
- A genuinely unknown key still warns, and when its NAME is secret-shaped its value is
  dropped from CONFIG but kept in the file - which the agent can read - so it must join
  the secret mask (A-2026-10-10-13, measured 2026-10-10).

TINYCMDR_SRC points SRC at a reverted build so the fix can be watched going red
(tests/run_all.py clears it for a normal run).
"""
import importlib.util
import io
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

STAGE = Path(tempfile.mkdtemp(prefix="fbfixconfig-"))
HOME = STAGE / "home"
HOME.mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(HOME)
os.environ["USERPROFILE"] = str(HOME)
os.environ["TINYCMDR_NO_MATERIALIZE"] = "1"

shutil.copy2(SRC, STAGE / "tinycmdr.py")

# The config this build must tolerate: retired keys, one secret-shaped unknown key, one
# plain typo. Written BEFORE the import so load_config reads it at module level.
(STAGE / "config.json").write_text(
    '{\n'
    '  "agent": {\n'
    '    "notes_keep_entries": 60,\n'
    '    "notes_archive_days": 45,\n'
    '    "tasks_max_open": 15,\n'
    '    "ledger_stale_hours": 12,\n'
    '    "agent_max_minutes": 20\n'
    '  },\n'
    '  "mattermost": {\n'
    '    "db_password": "hunter2secret"\n'
    '  }\n'
    '}\n', encoding="utf-8")

# Capture what load_config logs before the import runs it.
_logbuf = io.StringIO()
_handler = logging.StreamHandler(_logbuf)
_handler.setLevel(logging.WARNING)
logging.getLogger("tinycmdr").addHandler(_handler)

spec = importlib.util.spec_from_file_location("tinycmdr_fix_config", STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_fix_config"] = fb
spec.loader.exec_module(fb)

_logged = _logbuf.getvalue()

PASSES = []
FAILS = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print("ok   %s" % name)
    else:
        FAILS.append(name)
        print("FAIL %s: %s" % (name, detail))


def main():
    # ---- the retired names are silent ---------------------------------------------
    check("a retired agent key raises no warning",
          "notes_keep_entries" not in _logged, _logged)
    check("  nor do the rest of the retired block",
          not any(k in _logged for k in ("notes_archive_days", "tasks_max_open",
                                         "ledger_stale_hours")), _logged)
    # ---- a real typo still warns ---------------------------------------------------
    check("a genuinely unknown key still warns with its name",
          "agent.agent_max_minutes" in _logged, _logged)
    # ---- the secret-shaped unknown value joins the mask ----------------------------
    check("the unknown secret-named value warns (a name nothing reads)",
          "mattermost.db_password" in _logged, _logged)
    check("  and its value is masked from text leaving the process",
          fb.scrub("here it is: hunter2secret") == "here it is: «redacted»",
          fb.scrub("here it is: hunter2secret"))
    check("  via the dropped-value mask specifically",
          "hunter2secret" in getattr(fb, "_DROPPED_SECRET_VALUES", ()),
          getattr(fb, "_DROPPED_SECRET_VALUES", "absent on the pre-fix build"))
    # ---- the table itself behaves --------------------------------------------------
    check("_config_key_known accepts a retired pair",
          fb._config_key_known("agent", "tasks_max_open"))
    check("  and still refuses an unknown one",
          not fb._config_key_known("agent", "agent_max_minutes"))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(STAGE, ignore_errors=True)
    print("\n%d passed, %d failed" % (len(PASSES), len(FAILS)))
    sys.exit(rc if not FAILS else 1)
