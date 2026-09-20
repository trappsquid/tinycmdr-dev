"""Detect a *visible* console window owned by powershell/conhost/cmd/wscript.
Polls top-level windows fast; logs any window whose owning PID is a console host,
with its session id, class, title, and visibility. Silent when none appear.
"""
import ctypes, ctypes.wintypes as wt, sys, time, datetime, os

TARGETS = {"powershell.exe", "conhost.exe", "cmd.exe", "wscript.exe", "cscript.exe", "pwsh.exe"}
OUT = r"C:/Users/<user>\logs\console-window-probe.log"
MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 7.0

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
GetWindowTextLength = user32.GetWindowTextLengthW
GetWindowText = user32.GetWindowTextW
GetClassName = user32.GetClassNameW
IsWindowVisible = user32.IsWindowVisible
GetWindowThreadProcessId = user32.GetWindowThreadProcessId

QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
OpenProcess = kernel32.OpenProcess
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def exe_name(pid: int) -> str:
    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def session_id(pid: int) -> str:
    # ProcessIdToSessionId
    try:
        pid_to_sid = kernel32.ProcessIdToSessionId
        sid = wt.DWORD(0)
        if pid_to_sid(pid, ctypes.byref(sid)):
            return str(sid.value)
    except Exception:
        pass
    return "?"


def scan() -> list[tuple]:
    hits = []
    def cb(hwnd, lparam):
        pid = wt.DWORD(0)
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True
        name = exe_name(pid.value)
        if name not in TARGETS:
            return True
        n = GetWindowTextLength(hwnd)
        title = ""
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            GetWindowText(hwnd, buf, n + 1)
            title = buf.value
        cbuf = ctypes.create_unicode_buffer(256)
        GetClassName(hwnd, cbuf, 256)
        hits.append((pid.value, name, session_id(pid.value), cbuf.value, title, bool(IsWindowVisible(hwnd))))
        return True
    EnumWindows(EnumWindowsProc(cb), 0)
    return hits


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    def log(msg):
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    log(f"=== window probe start {datetime.datetime.now():%H:%M:%S} for {MINUTES} min ===")
    seen = set()
    end = time.time() + MINUTES * 60
    while time.time() < end:
        for pid, name, sid, cls, title, visible in scan():
            key = (pid, name, cls, title, visible)
            if key in seen:
                continue
            seen.add(key)
            log(f"[{datetime.datetime.now():%H:%M:%S.%f}] VIS={int(visible)} pid={pid} exe={name} session={sid} class={cls} title={title!r}")
        time.sleep(0.2)
    log(f"=== window probe end {datetime.datetime.now():%H:%M:%S} ===")


if __name__ == "__main__":
    main()
