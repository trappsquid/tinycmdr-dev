"""Update output says what changed and what to do - not the updater's internal policy.

Three notes earned this suite: the `.git` notice narrated a 1.0.46-1.0.48 failure mode to
everyone with a checkout, the dev-kit keep named a where-roles.json a clone may not even
have, and the host-default note claimed "this release's default differs" for a theme.toml
the release never touched. The update prints what it wrote and what the operator must do;
the keep-it-silent paths stay silent.

    python tests/test_update_notes.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

FAILS = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
    if not ok:
        FAILS.append(what)


def stage(work):
    for name in ("theme.default.toml", "soul.example.md"):
        shutil.copy2(BASE / name, work / name)
    shutil.copy2(SRC, work / "tinycmdr.py")
    shutil.copy2(BASE / "tests" / "fixture-config.json", work / "config.json")


def load(work):
    spec = importlib.util.spec_from_file_location("tinycmdr_notes", work / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_notes"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    work = Path(tempfile.mkdtemp(prefix="fbnotes-"))
    try:
        stage(work)
        mod = load(work)

        # a bare install: no declaration, no checkout evidence, nothing to keep for
        check("a bare install has no dev-tree reason", mod._dev_tree_reason() is None,
              repr(mod._dev_tree_reason()))

        # checkout evidence alone - the shape a contributor's clone has
        (work / ".git").mkdir()
        check("a .git alone reads as a checkout", mod._dev_tree_reason() == "checkout",
              repr(mod._dev_tree_reason()))
        (work / "docs").mkdir(exist_ok=True)
        (work / "docs" / "development.md").write_text("x", encoding="utf-8")
        (work / "STATUS.json").write_text("{}", encoding="utf-8")
        note = mod._prune_dev_kit()
        check("a checkout keeps its kit",
              (work / "docs" / "development.md").exists()
              and (work / "STATUS.json").exists())
        check("...and says nothing about it", note == "", repr(note))

        # a declaration pointing at THIS tree is reason enough too - and stays quiet
        shutil.rmtree(work / ".git")
        (work / "maintenance").mkdir(exist_ok=True)
        roles = work / "maintenance" / "where-roles.json"
        roles.write_text(json.dumps([{"role": "dev", "path": str(work)}]), encoding="utf-8")
        check("a dev declaration reads as declared", mod._dev_tree_reason() == "declared",
              repr(mod._dev_tree_reason()))
        check("...and the prune stays quiet too", mod._prune_dev_kit() == "",
              repr(mod._prune_dev_kit()))
        roles.write_text("{ not json", encoding="utf-8")
        check("an unreadable declaration fails safe as dev",
              mod._dev_tree_reason() == "declared", repr(mod._dev_tree_reason()))
        roles.unlink()

        # host-default notes: only a default THIS package wrote produces one
        (work / "theme.toml").write_text("# mine\n", encoding="utf-8")
        mod._HOST_DEFAULT_GAPS["checked"] = False     # host_file_gaps caches its answer
        mod._HOST_DEFAULT_GAPS["gaps"] = []
        check("a differing theme.toml with an untouched default prints no note",
              mod._host_gap_notes([]) == [], repr(mod._host_gap_notes([])))
        lines = mod._host_gap_notes(["theme.default.toml"])
        check("a moved default prints one note naming both files",
              len(lines) == 1 and "theme.toml" in lines[0]
              and "theme.default.toml" in lines[0], repr(lines))

        if FAILS:
            print("\n%d FAILED: %s" % (len(FAILS), "; ".join(FAILS)))
            return 1
        print("\nall good")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
