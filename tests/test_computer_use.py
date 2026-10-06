"""computer_use: the hermetic half of the GUI tool, graded on every OS.

`tools/computer_use.py` carries its own checks (`python tools/computer_use.py`):
key canonicalisation, both blocklists, the screenshot dedup, the AppleScript
wire format, element numbering and the platform gate. This suite stages a
byte-copy of the tool, runs that self-test, and adds what the harness side needs
from a shipped starter tool. Nothing here touches a screen, a permission, a
network or an app.

    python tests/test_computer_use.py
"""
import contextlib
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tools/computer_use.py")
FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILS.append(name)
        print(f"FAIL {name}: {detail}")


def load():
    """Import a staged byte-copy, the way every suite treats the code under test."""
    work = Path(tempfile.mkdtemp(prefix="tc-cu-"))
    shutil.copy2(SRC, work / "computer_use.py")
    spec = importlib.util.spec_from_file_location("tc_computer_use",
                                                  work / "computer_use.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tc_computer_use"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    check("the tool is in the tree", SRC.exists(), SRC)
    if not SRC.exists():
        return 1
    mod = load()

    # ---- the tool grades itself, hermetically -----------------------------
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod._selftest()
    failed = [ln for ln in buf.getvalue().splitlines() if ln.startswith("FAIL")]
    check("the tool's own checks pass on this platform", rc == 0 and not failed,
          (rc, failed[:3]))

    # ---- the shipped shape -------------------------------------------------
    check("it is a native drop-in (NAME/SCHEMA/run)",
          getattr(mod, "NAME", "") == "computer_use"
          and callable(getattr(mod, "run", None)) and isinstance(mod.SCHEMA, dict),
          getattr(mod, "NAME", ""))
    actions = (mod.SCHEMA.get("properties") or {}).get("action", {}).get("enum") or []
    for verb in ("capture", "click", "right_click", "double_click", "drag",
                 "scroll", "type", "key", "wait", "doctor"):
        check("the schema offers %r" % verb, verb in actions, actions)
    check("it declares itself mutating (the loader backs up files for those)",
          getattr(mod, "MUTATES", False) is True)

    # ---- the blocklist regressions this batch fixed -----------------------
    key, mods, err = mod.canon_combo("ctrl-opt-del")
    check("opt folds to option, so the force-logout spelling parses",
          bool(key) and err == "", (key, mods, err))
    check("...and the canonicalised table refuses it",
          bool(mod.blocked_combo(key, mods)), (key, mods))
    key, mods, _ = mod.canon_combo("cmd+shift+delete")
    check("delete folds to backspace, so empty-trash is refused",
          bool(mod.blocked_combo(key, mods)), (key, mods))
    key, mods, _ = mod.canon_combo("win+l")
    check("win+L is blocked where cmd IS the Windows key",
          bool(mod.blocked_combo(key, mods)) == mod.IS_WIN, (key, mods, mod.IS_WIN))

    # A-113 (an earlier review run 11): Alt is canonicalised to `option` on the Python side, so
    # BOTH spellings must hit the table, and the Windows-only entries must not depend on
    # the macOS keycode table (that gate made ctrl+alt+delete unblockable on Windows).
    for spelling in ("alt", "option"):
        check("the block sees %r on f4 (force-quit dialog)" % spelling,
              bool(mod.blocked_combo("f4", [spelling])),
              mod.blocked_combo("f4", [spelling]))
    _was_win = mod.IS_WIN
    try:
        mod.IS_WIN = True
        check("ctrl+alt+delete is blocked on Windows (secure attention)",
              bool(mod.blocked_combo("delete", ["ctrl", "alt"])),
              mod.blocked_combo("delete", ["ctrl", "alt"]))
    finally:
        mod.IS_WIN = _was_win

    # The two halves must agree on the modifier vocabulary: every word the Python
    # canonicaliser can emit (plus `shift`, armed directly) is an arm in the embedded
    # PowerShell helper - the bug was `option` missing from all three switches, which
    # sent every Alt combo with no Alt held.
    wanted = set(mod._KEY_ALIASES.values()) | {"shift"}
    arms = set(re.findall(r'^\s+"([a-z]+)"\s+\{', mod._PS_HELPER, re.M))
    check("the PowerShell helper arms every modifier the Python half can emit",
          wanted <= arms, (sorted(wanted), sorted(arms)))

    # ---- the screenshot dedup ---------------------------------------------
    mod._SHOT_DEDUP.clear()
    check("dedup: the first frame is delivered",
          not mod.dedup_should_omit("s", "d", ("A", "")))
    check("dedup: an identical frame is omitted",
          mod.dedup_should_omit("s", "d", ("A", "")))
    check("dedup: the second identical frame is omitted too",
          mod.dedup_should_omit("s", "d", ("A", "")))
    check("dedup: pixels return once the streak is spent",
          not mod.dedup_should_omit("s", "d", ("A", "")))
    check("dedup: changed bytes are delivered",
          not mod.dedup_should_omit("s", "d2", ("A", "")))

    # ---- a shipped starter may not pull a dependency tree in --------------
    bad = [ln for ln in SRC.read_text(encoding="utf-8").splitlines()
           if ln.startswith(("import ", "from ")) and "tools." in ln]
    check("it imports nothing from the harness itself", not bad, bad)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        return 1
    print("all computer_use checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
