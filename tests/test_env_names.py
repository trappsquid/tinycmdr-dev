"""The names the app WRITES into .env must be the names it READS out of it.

Review H3. tinycmdr.py's setup wizard wrote `MATTERMOST_BOT_TOKEN`, `TELEGRAM_TOKEN` and
`LLM_API_KEY`; the loader's `env_map` reads none of them. A box configured through that
wizard therefore came up with a token nothing consumed - and because the loader then sees
no token at all, the symptom is the one the code already warns about for a missing token:
"the bot never connects". Measured on the author's own install 2026-09-26: `.env` carried
`MATTERMOST_BOT_TOKEN` beside the `TINYCMDR_MM_TOKEN` that was actually doing the work.

These checks are static: they read the shipped text, so they hold with no config, no
network and no interpreter band. `python tests/test_env_names.py`
"""
import os
import pathlib
import re
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
# TINYCMDR_SRC lets a falsification run point this at a reverted copy (tests/run_all.py
# clears it, so the gate always grades the real file).
SRC_PATH = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
SRC = SRC_PATH.read_text(encoding="utf-8", errors="replace")
EXAMPLE = (BASE / ".env.example").read_text(encoding="utf-8", errors="replace")
FAILS = []

# The names that were written and never read, kept here so a regression names the bug.
LEGACY_WRITES = ("MATTERMOST_BOT_TOKEN", "TELEGRAM_TOKEN", "LLM_API_KEY")


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILS.append(name)
        print(f"FAIL {name}: {detail}")


def env_map_names():
    """Every name the loader reads out of the environment, from its own env_map table."""
    body = re.search(r"env_map = \{(.*?)\n    \}", SRC, re.S)
    return set(re.findall(r'"([A-Z0-9_]+)":\s*\(', body.group(1))) if body else set()


def written_names():
    """Every name the app writes into .env: _env_set(), and _env_set_safe() which reports a
    value it must refuse instead of raising at the operator's prompt."""
    return set(re.findall(r'_env_set(?:_safe)?\("([A-Z0-9_]+)"', SRC))


def main():
    reads = env_map_names()
    writes = written_names()
    check("the loader's env_map was found", bool(reads), "no env_map in tinycmdr.py")
    check("the app writes at least one .env name", bool(writes),
          "no _env_set calls found - the wizard moved?")

    missing = sorted(w for w in writes if w not in reads)
    check("every name the app WRITES is a name it READS",
          not missing,
          f"written but never read: {missing}")

    still = sorted(n for n in LEGACY_WRITES if n in writes)
    check("the three names nothing read are gone", not still,
          f"still written: {still}")

    # A reader has to be able to find the name somewhere: env_map is the contract, the
    # example file is the documentation of it.
    undocumented = sorted(n for n in reads if n not in EXAMPLE)
    check("every name the loader READS is in .env.example", not undocumented,
          f"not documented: {undocumented}")

    # The secrets the installers write must stay in the set (each has exactly one home).
    for name in ("TINYCMDR_MM_TOKEN", "TINYCMDR_TG_TOKEN",
                 "TINYCMDR_LLM_API_KEY"):
        check(f"the loader reads {name}", name in reads, "not in env_map")

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed:")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("all env-name checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
