"""The safety tiers in config.example.json must equal DEFAULT_CONFIG's.

Every installer writes a new host's config.json FROM config.example.json. Measured
2026-09-25: the example was missing the `robocopy /MOVE` confirm pattern that
DEFAULT_CONFIG and the same release's own changelog both carry (8 vs 9), so a fresh
install shipped without the gate the changelog announced. Nothing caught it: the suites
run against tests/fixture-config.json, which holds ZERO confirm and content patterns, so
"all suites green" said nothing at all about the file the installers copy.

The check below runs against the real tree and then FALSIFIES itself on a copy with the
pattern deleted - a gate that cannot fail grades nothing.

    python tests/test_config_example.py
"""
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FAILS = []


def check(what, cond, detail=""):
    if cond:
        print(f"ok   {what}")
    else:
        FAILS.append(what)
        print(f"FAIL {what}: {detail}")


sys.path.insert(0, str(BASE / "maintenance"))   # build-package.py imports private_rules
spec = importlib.util.spec_from_file_location(
    "build_package_under_test", BASE / "maintenance" / "build-package.py")
bp = importlib.util.module_from_spec(spec)
sys.modules["build_package_under_test"] = bp
spec.loader.exec_module(bp)

probs = bp.tier_drift()
check("the shipped config.example.json matches DEFAULT_CONFIG's tiers", probs == [], probs)

work = Path(tempfile.mkdtemp(prefix="fbcfg-"))
try:
    shutil.copy2(BASE / "tinycmdr.py", work / "tinycmdr.py")
    cfg = json.loads((BASE / "config.example.json").read_text(encoding="utf-8"))
    cfg["agent"]["confirm_patterns"] = [x for x in cfg["agent"]["confirm_patterns"]
                                        if "robocopy" not in x]
    (work / "config.example.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    saved = bp.ROOT
    bp.ROOT = work
    try:
        broken = bp.tier_drift()
    finally:
        bp.ROOT = saved
    check("a tier pattern deleted from the example is REFUSED",
          any("robocopy" in p for p in broken), broken)

    cfg["agent"]["confirm_patterns"].append(r"\bzzz-extra-pattern-not-in-code\b")
    (work / "config.example.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    bp.ROOT = work
    try:
        extra = bp.tier_drift()
    finally:
        bp.ROOT = saved
    check("a pattern the CODE does not have is refused too",
          any("zzz-extra-pattern" in p for p in extra), extra)
finally:
    shutil.rmtree(work, ignore_errors=True)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("config.example.json and DEFAULT_CONFIG agree on every safety tier")
