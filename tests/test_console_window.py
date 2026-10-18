"""test_console_window - no shell or tool helper may flash a console window.

The bot runs under pythonw.exe, which has no console attached. Windows gives every
console program started from a console-less process a NEW, VISIBLE console window
unless the spawn asks for none, and powershell.exe is every Windows shell command -
so a bare Popen there pops a blue window on top of everything for as long as the
command lives. CREATE_NO_WINDOW (0x08000000) plus STARTF_USESHOWWINDOW/SW_HIDE is the
ask; the harness's run_capture has carried it for a while, and the job wrapper that
every auto-background probe and every process start runs through did not.

Three checks, all run against the artifact under test:
  * the wrapper (tools/process.py) hides its child when the parent says to, and
    passes neither flag when it does not (the POSIX parent);
  * tools/computer_use.py::_spawn_group() keeps CREATE_NO_WINDOW alongside its
    process group, so the PowerShell helper cannot flash either;
  * on Windows, end to end: pythonw runs the wrapper, the wrapper spawns a console
    child, and the child's GetConsoleWindow() is 0 - no console was allocated.

Run: python tests/test_console_window.py     [TINYCMDR_SRC=<pre-fix tools/process.py>]
"""
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
# The job wrapper under test. TINYCMDR_SRC points the suite at a snapshot (the
# pre-fix tools/process.py) so the wrapper check can be watched going red.
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tools/process.py")
FAILS = []


def check(cond, what, detail=""):
    if not cond:
        FAILS.append(what)
        print("FAIL %s: %s" % (what, detail))
    else:
        print("ok   %s" % what)


def wrapper_source():
    """The literal _WRAPPER the file under test carries. A string by design, so it is
    read as data and executed - never graded as text."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "_WRAPPER" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("_WRAPPER is not a literal in %s" % SRC)


# The wrapper's own subprocess, shadowed: it records the kwargs Popen was given and
# exits, so the check reads what the wrapper actually built instead of what its source
# says. Imported in preference to the stdlib because `-c` puts the cwd first on sys.path.
_SHADOW = '''
import json, os
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0
STDOUT = -2
PIPE = -1
DEVNULL = -3
class TimeoutExpired(Exception):
    pass
class STARTUPINFO:
    def __init__(self):
        self.dwFlags = 0
        self.wShowWindow = None
def Popen(*a, **kw):
    rec = os.environ.get("TINYCMDR_CONSOLE_RECORD")
    if rec:
        si = kw.get("startupinfo")
        with open(rec, "w", encoding="utf-8") as fh:
            json.dump({"creationflags": int(kw.get("creationflags") or 0),
                       "dwFlags": int(getattr(si, "dwFlags", 0)),
                       "wShowWindow": getattr(si, "wShowWindow", None),
                       "has_startupinfo": si is not None}, fh)
    raise SystemExit(0)
'''


def wrapper_kwargs(wrapper, hide):
    """Run the wrapper with the shadow subprocess and return the Popen kwargs it built."""
    work = Path(tempfile.mkdtemp(prefix="fbconsole-"))
    try:
        (work / "subprocess.py").write_text(_SHADOW, encoding="utf-8")
        rec = work / "record.json"
        env = dict(os.environ, TINYCMDR_CONSOLE_RECORD=str(rec))
        proc = subprocess.run(
            [sys.executable, "-c", wrapper, json.dumps(["unused"]), "0", "", hide],
            cwd=str(work), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        data = json.loads(rec.read_text(encoding="utf-8")) if rec.exists() else {}
        return proc.returncode, data, (proc.stderr or b"")[-300:]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def spawn_group_kwargs():
    """computer_use's _spawn_group() as Windows returns it, graded on any platform."""
    spec = importlib.util.spec_from_file_location(
        "fb_console_cu", BASE / "tools" / "computer_use.py")
    mod = importlib.util.module_from_spec(spec)
    saved_dwb = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = saved_dwb
    names = ("CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP",
             "STARTUPINFO", "STARTF_USESHOWWINDOW", "SW_HIDE")
    sentinel = object()
    saved = {n: getattr(subprocess, n, sentinel) for n in names}
    saved_win = mod.IS_WIN

    class _StartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    mod.IS_WIN = True
    subprocess.CREATE_NO_WINDOW = 0x08000000
    subprocess.CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.STARTUPINFO = _StartupInfo
    subprocess.STARTF_USESHOWWINDOW = 0x00000001
    subprocess.SW_HIDE = 0
    try:
        return mod._spawn_group()
    finally:
        mod.IS_WIN = saved_win
        for name, value in saved.items():
            if value is sentinel:
                delattr(subprocess, name)
            else:
                setattr(subprocess, name, value)


def windows_child_probe(wrapper):
    """The reported symptom, measured: pythonw -> wrapper -> console child, and the
    child's own GetConsoleWindow() must be 0 (no console window was allocated)."""
    if os.name != "nt":
        print("note: POSIX allocates no console window; the kwargs checks are the verdict")
        return
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    check(pythonw.exists(), "pythonw.exe sits beside the interpreter", str(pythonw))
    if not pythonw.exists():
        return
    work = Path(tempfile.mkdtemp(prefix="fbconsole-win-"))
    try:
        out = work / "hwnd.txt"
        child = ("import ctypes, sys\n"
                 "open(sys.argv[1], 'w').write("
                 "str(ctypes.windll.kernel32.GetConsoleWindow()))\n")
        argv = [sys.executable, "-c", child, str(out)]
        # The wrapper itself runs under pythonw, exactly like the bot: console-free.
        proc = subprocess.run(
            [str(pythonw), "-c", wrapper, json.dumps(argv), "0", "", "1"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=120)
        hwnd = out.read_text(encoding="utf-8").strip() if out.exists() else "(no output)"
        check(hwnd == "0",
              "a console child spawned through the wrapper has no console window",
              "rc=%s hwnd=%s stderr=%r"
              % (proc.returncode, hwnd, (proc.stderr or b"")[-300:]))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    wrapper = wrapper_source()

    rc, hidden, err = wrapper_kwargs(wrapper, "1")
    check(rc == 0 and hidden, "the wrapper runs with the hide flag", "rc=%s err=%r"
          % (rc, err))
    check(hidden.get("creationflags", 0) & 0x08000000,
          "the wrapper spawns its child with CREATE_NO_WINDOW", hidden)
    check(hidden.get("has_startupinfo") and hidden.get("dwFlags", 0) & 0x00000001
          and hidden.get("wShowWindow") == 0,
          "the wrapper also asks for a hidden show state", hidden)

    rc, plain, err = wrapper_kwargs(wrapper, "0")
    check(rc == 0 and plain, "the wrapper runs without the hide flag", "rc=%s err=%r"
          % (rc, err))
    check(not plain.get("creationflags") and not plain.get("has_startupinfo"),
          "a POSIX parent passes no Windows spawn flags", plain)

    kw = spawn_group_kwargs()
    check(kw.get("creationflags", 0) & 0x08000000,
          "computer_use helpers carry CREATE_NO_WINDOW", kw)
    check(kw.get("creationflags", 0) & 0x00000200,
          "...alongside their own process group", kw)
    check(kw.get("startupinfo") is not None
          and kw["startupinfo"].wShowWindow == 0,
          "...and the hidden show state", kw.get("startupinfo"))

    windows_child_probe(wrapper)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all console-window checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
