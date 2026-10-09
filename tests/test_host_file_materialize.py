"""Host files materialize from their shipped defaults on start.

theme.toml and soul.md are host state: a checkout or an install that lacks them gets a
copy of theme.default.toml / soul.example.md when the app starts, and an existing
(customized) file is never touched.

    python tests/test_host_file_materialize.py
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

FAILS = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
    if not ok:
        FAILS.append(what)


def stage(work):
    for name in ("tinycmdr.py", "theme.default.toml", "soul.example.md"):
        shutil.copy2(BASE / name, work / name)
    shutil.copy2(BASE / "tests" / "fixture-config.json", work / "config.json")


def load(work):
    # The runner exports TINYCMDR_NO_MATERIALIZE=1 for every suite (a suite that imports
    # the app must not create host state in the checkout). THIS suite grades exactly that
    # materialization, so it clears the guard before loading.
    os.environ.pop("TINYCMDR_NO_MATERIALIZE", None)
    spec = importlib.util.spec_from_file_location("tinycmdr_materialize",
                                                  work / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_materialize"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    work = Path(tempfile.mkdtemp(prefix="fbmat-"))
    try:
        # fresh tree: both live files appear, byte-identical to their defaults
        stage(work)
        load(work)
        check("a fresh tree gets theme.toml", (work / "theme.toml").exists())
        check("...byte-identical to theme.default.toml",
              (work / "theme.toml").read_bytes()
              == (work / "theme.default.toml").read_bytes())
        check("a fresh tree gets soul.md", (work / "soul.md").exists())
        check("...byte-identical to soul.example.md",
              (work / "soul.md").read_bytes() == (work / "soul.example.md").read_bytes())

        # the runner's guard (TINYCMDR_NO_MATERIALIZE=1): an import under the gate must
        # create neither file, which is what keeps a suite from writing host state into a
        # checkout (test_checkin's import did exactly that once both files were untracked)
        work0 = Path(tempfile.mkdtemp(prefix="fbmat0-"))
        try:
            stage(work0)
            os.environ["TINYCMDR_NO_MATERIALIZE"] = "1"
            try:
                # A RAW import, not load(): load() clears the guard on purpose (the other
                # cases grade the materialization the guard suppresses).
                spec = importlib.util.spec_from_file_location(
                    "tinycmdr_mat_guard", work0 / "tinycmdr.py")
                mod = importlib.util.module_from_spec(spec)
                sys.modules["tinycmdr_mat_guard"] = mod
                spec.loader.exec_module(mod)
            finally:
                os.environ.pop("TINYCMDR_NO_MATERIALIZE", None)
            check("with the runner's guard set, neither host file is created",
                  not (work0 / "theme.toml").exists() and not (work0 / "soul.md").exists())
        finally:
            shutil.rmtree(work0, ignore_errors=True)

        # an existing file is the host's: a start never touches it
        work2 = Path(tempfile.mkdtemp(prefix="fbmat2-"))
        try:
            stage(work2)
            (work2 / "theme.toml").write_text("# mine\n", encoding="utf-8")
            (work2 / "soul.md").write_text("custom persona\n", encoding="utf-8")
            load(work2)
            check("a customized theme.toml is untouched",
                  (work2 / "theme.toml").read_text(encoding="utf-8") == "# mine\n")
            check("a customized soul.md is untouched",
                  (work2 / "soul.md").read_text(encoding="utf-8") == "custom persona\n")
        finally:
            shutil.rmtree(work2, ignore_errors=True)

        # no shipped default: nothing is invented, and the start does not fail
        work3 = Path(tempfile.mkdtemp(prefix="fbmat3-"))
        try:
            stage(work3)
            (work3 / "theme.default.toml").unlink()
            (work3 / "soul.example.md").unlink()
            load(work3)
            check("with no shipped default, no live file is invented",
                  not (work3 / "theme.toml").exists()
                  and not (work3 / "soul.md").exists())
        finally:
            shutil.rmtree(work3, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("\n%s" % ("all materialize checks passed" if not FAILS
                    else "FAILED: %d" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
