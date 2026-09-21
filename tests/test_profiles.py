"""Caps follow the model, not one global guess (audit finding 8, item 2d).

A local endpoint and a 200k cloud model were paying identical tool-output, fetch and
note caps, so a capable model was fed clipping it did not need - and the campaign kept
re-deriving facts its own notes had already been clipped out of.

Run:  python tests/test_profiles.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("tinycmdr_SRC", "tinycmdr.py")
STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-profiles"
FAILS = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        FAILS.append(what)


def load(model, profiles=None):
    """Stage the harness with a config naming this model, and load it."""
    if STAGE.exists():
        shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    cfg = {"llm": {"model": model, "base_url": "http://127.0.0.1:9999/v1"}}
    if profiles:
        cfg["llm"]["profiles"] = profiles
    (STAGE / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    name = "tinycmdr_prof_%d" % (abs(hash(model)) % 100000)
    spec = importlib.util.spec_from_file_location(name, STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules[name] = fb
    spec.loader.exec_module(fb)
    return fb


def main():
    # 1. no profiles: today's values, untouched, and nothing claims a profile
    fb = load("local-gguf-model")
    check(fb.PROFILE is None, "no profiles in config means no profile is applied")
    check(fb.CONFIG["agent"].get("tool_output_max_chars") == 10000,
          "the shipped tool-output cap stays 10000 by default")
    check("active_profile" not in fb.CONFIG["agent"],
          "and no profile is recorded, so nothing can be running silently")

    # 2. a matching profile wins, and only the keys it names move
    fb = load("deepseek-v4-flash", {"deepseek": {"tool_output_max_chars": 40000,
                                                 "notes_max_note_chars": 4000}})
    check(fb.PROFILE and fb.PROFILE["profile"] == "deepseek",
          "the first profile key found in the model name wins")
    check(fb.CONFIG["agent"]["tool_output_max_chars"] == 40000,
          "its tool-output cap applies")
    check(fb.CONFIG["agent"]["notes_max_note_chars"] == 4000,
          "its note cap applies")
    check(fb.CONFIG["agent"].get("fetch_max_chars") == 12000,
          "keys the profile does not name keep the value already in config")
    check(fb.CONFIG["agent"].get("active_profile") == "deepseek",
          "the winning profile is recorded, so it cannot hide")

    # 3. a profile that does not match changes nothing (no accidental catch-all)
    fb = load("local-gguf-model", {"deepseek": {"tool_output_max_chars": 40000}})
    check(fb.PROFILE is None, "a non-matching profile is not applied")
    check(fb.CONFIG["agent"]["tool_output_max_chars"] == 10000,
          "and the cap stays where it was")

    # 4. a profile cannot invent config keys
    fb = load("cloud-model", {"cloud": {"tool_output_max_chars": 50000,
                                        "not_a_real_key": 1}})
    check("not_a_real_key" not in fb.CONFIG["agent"],
          "a profile cannot write keys the harness does not read")

    print()
    if FAILS:
        print("%d failed: %s" % (len(FAILS), FAILS))
        sys.exit(1)
    print("all model-profile checks passed")


main()
