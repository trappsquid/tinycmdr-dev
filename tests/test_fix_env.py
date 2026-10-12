""".env follows the de-facto dotenv rule for inline comments.

`TOKEN=value # note` used to store `value # note` - a silent wrong-token failure: every
lane authenticated with a corrupted value and the remote answered 401 with no detail
(A-2026-10-12-06, measured 2026-10-12). The fix strips an UNQUOTED comment only: a `#`
inside quotes, or with no space before it, is part of the value.

TINYCMDR_SRC points SRC at a reverted build so the fix can be watched going red
(tests/run_all.py clears it for a normal run).
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

STAGE = Path(tempfile.mkdtemp(prefix="fbfixenv-"))
HOME = STAGE / "home"
HOME.mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(HOME)
os.environ["USERPROFILE"] = str(HOME)
os.environ["TINYCMDR_NO_MATERIALIZE"] = "1"

shutil.copy2(SRC, STAGE / "tinycmdr.py")

# The probe names are cleared first so an inherited environment cannot shadow the file.
for name in ("TCM_PROBE_PLAIN", "TCM_PROBE_QUOTED", "TCM_PROBE_QUOTED_SINGLE",
             "TCM_PROBE_NOSPACE", "TCM_PROBE_EMPTY"):
    os.environ.pop(name, None)

(STAGE / ".env").write_text(
    "TCM_PROBE_PLAIN=x # a trailing note\n"
    'TCM_PROBE_QUOTED="quoted # not a comment"\n'
    "TCM_PROBE_QUOTED_SINGLE='also # kept'\n"
    "TCM_PROBE_NOSPACE=ab#cd\n"
    "TCM_PROBE_EMPTY=\n",
    encoding="utf-8")

spec = importlib.util.spec_from_file_location("tinycmdr_fix_env", STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_fix_env"] = fb
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


def main():
    g = os.environ.get
    check("an unquoted inline comment is stripped",
          g("TCM_PROBE_PLAIN") == "x", repr(g("TCM_PROBE_PLAIN")))
    check("a double-quoted value keeps its # and loses the quotes",
          g("TCM_PROBE_QUOTED") == "quoted # not a comment",
          repr(g("TCM_PROBE_QUOTED")))
    check("a single-quoted value keeps its # too",
          g("TCM_PROBE_QUOTED_SINGLE") == "also # kept",
          repr(g("TCM_PROBE_QUOTED_SINGLE")))
    check("a # with no space before it is not a comment",
          g("TCM_PROBE_NOSPACE") == "ab#cd", repr(g("TCM_PROBE_NOSPACE")))
    check("an empty value stays empty",
          g("TCM_PROBE_EMPTY") == "", repr(g("TCM_PROBE_EMPTY")))
    # The real environment still wins over the file.
    os.environ["TCM_PROBE_PLAIN"] = "from-env"
    fb._load_env_file()
    check("a real environment variable still wins",
          g("TCM_PROBE_PLAIN") == "from-env", repr(g("TCM_PROBE_PLAIN")))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(STAGE, ignore_errors=True)
    print("\n%d passed, %d failed" % (len(PASSES), len(FAILS)))
    sys.exit(rc if not FAILS else 1)
