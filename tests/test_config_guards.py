"""A wrong TYPE in config.json cannot kill the load or blank a shipped default.

`.get(key, default)` supplies the default only for a MISSING key, never for one that is
present and null - so `"agent": {"max_minutes": null}` reached `None * 60` and raised
TypeError from inside the run loop, naming neither the key nor the file (measured
2026-10-07). A null is an easy thing to ship: a config.json written by a script, a key
commented out by setting it to null, an installer template with an unfilled placeholder.
The section-level guard already stops a non-dict SECTION (`"agent": null` killed startup
on 2026-10-05); this grades the same class one level down.

Three wrong-type shapes, each graded here because each one KILLED the import before a
warning could be printed: a null for a key the run loop does arithmetic on, a non-object
`search` section read by the legacy-key sweep, and a non-dict `llm.fallbacks` entry read
by the api_key_env sweep (the last two, A-2026-10-08-135/-136, evidence: reproduced).

    python tests/test_config_guards.py

Falsification: with TINYCMDR_SRC=<pre-fix build> the merged value IS None and the keys the
run loop does arithmetic on are the ones that crash it; a pre-fix build fails the two
wrong-type cases with AttributeError at load.
"""
import importlib.util
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
FAILS = []

# The keys the run loop does ARITHMETIC on, and what the shipped default is: the value a
# null must fall back to (DEFAULT_CONFIG, not a number written here).
GUARD_KEYS = (("agent", "max_minutes"), ("agent", "max_steps"), ("agent", "stall_warn_minutes"))


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  <- %s" % (detail,)))
    if not cond:
        FAILS.append(name)


def main():
    work = Path(tempfile.mkdtemp(prefix="tc-config-guards-"))
    try:
        shutil.copy2(SRC, work / "tinycmdr.py")
        shutil.copy2(BASE / "tests" / "fixture-config.json", work / "config.json")
        cfg = json.loads((work / "config.json").read_text(encoding="utf-8"))
        for section, key in GUARD_KEYS:
            cfg.setdefault(section, {})[key] = None
        cfg["agent"]["not_a_shipped_key"] = None      # a null must not become a default
        cfg["search"] = "off"                         # a non-object section (A-135)
        cfg["llm"]["fallbacks"] = ["http://x",        # non-dict entries (A-136)...
                                   {"base_url": "http://127.0.0.1:2/v1", "model": "fb"}]
        (work / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

        spec = importlib.util.spec_from_file_location("tc_config_guards",
                                                      work / "tinycmdr.py")
        fb = importlib.util.module_from_spec(spec)
        sys.modules["tc_config_guards"] = fb
        try:
            spec.loader.exec_module(fb)
        except Exception as e:                        # noqa: BLE001
            print("FAIL the staged module loads: %s: %s" % (type(e).__name__, e))
            return 1

        said = []

        class _Grab(logging.Handler):
            def emit(self, record):
                try:
                    said.append(record.getMessage())
                except Exception:
                    pass

        grab = _Grab()
        fb.log.addHandler(grab)
        try:
            merged = fb.load_config()
        finally:
            fb.log.removeHandler(grab)

        for section, key in GUARD_KEYS:
            shipped = fb.DEFAULT_CONFIG[section].get(key)
            got = merged[section].get(key)
            check("%s.%s falls back to the shipped default" % (section, key),
                  got == shipped and got is not None, (got, shipped))
            check("...and said so, naming the key",
                  any("%s.%s is null" % (section, key) in m for m in said),
                  [m for m in said if "null" in m][:3])
        check("a null for a key nothing ships is dropped, not invented",
              "not_a_shipped_key" not in merged["agent"], merged["agent"].get("not_a_shipped_key"))
        check("a non-object search section keeps the shipped default, named",
              isinstance(merged["search"], dict)
              and any("section 'search' is str" in m for m in said),
              (type(merged["search"]).__name__, [m for m in said if "search" in m][:2]))
        check("non-dict fallback entries are dropped, the dict entry is kept",
              len(merged["llm"]["fallbacks"]) == 1
              and merged["llm"]["fallbacks"][0].get("model") == "fb",
              merged["llm"]["fallbacks"])
        check("...and the drop is named, once per entry kind",
              any("llm.fallbacks: 1 non-object entry ignored" in m for m in said),
              [m for m in said if "fallbacks" in m][:2])
        check("the load still answers a dict for every shipped section",
              all(isinstance(merged.get(s), dict) for s in fb.DEFAULT_CONFIG), sorted(merged))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("all config-guard checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
