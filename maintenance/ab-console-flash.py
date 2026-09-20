"""A/B proof: run the watchdog the way the task does, then the hidden way,
while a window probe records every console window with its session id.
Also records who the launcher process is (session + owning service).
"""
import subprocess, sys, time, datetime, os
sys.path.insert(0, r"C:/Users/<user>\tinycmdr\maintenance")
import importlib.util
spec = importlib.util.spec_from_file_location("probe", r"C:/Users/<user>\tinycmdr\maintenance\probe-console-window.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

LOG = r"C:/Users/<user>\logs\console-ab.log"
SCRIPT = r"C:/Users/<user>\AppData\Local\hermes\scripts\hermes_gateway_watchdog.ps1"

def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(m + "\n")
    print(m, flush=True)

def windows():
    return probe.scan()

def dump_windows(tag):
    hits = windows()
    if not hits:
        log(f"[{tag}] no console windows seen")
    for pid, name, sid, cls, title, vis in hits:
        log(f"[{tag}] VIS={int(vis)} pid={pid} exe={name} session={sid} class={cls} title={title!r}")

def launcher_info():
    ps = r'''
$p = Get-Process -Id 2220 -ErrorAction SilentlyContinue
if ($p) { "pid2220 name=" + $p.ProcessName + " session=" + $p.SessionId }
Get-CimInstance Win32_Service | Where-Object { $_.ProcessId -eq 2220 } | ForEach-Object { "service=" + $_.Name + " state=" + $_.State }
$t = Get-ScheduledTask -TaskName 'Hermes_Gateway_Watchdog'
"task LogonType=" + $t.Principal.LogonType + " RunLevel=" + $t.Principal.RunLevel + " Hidden=" + $t.Settings.Hidden
"task UserId=" + $t.Principal.UserId
"task Exec=" + $t.Actions[0].CommandLine + " ARGS=" + $t.Actions[0].Arguments
"current session=" + (Get-Process -Id $PID).SessionId
'''
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True, encoding="utf-8", errors="replace")
    log("--- launcher/task info ---\n" + r.stdout.strip())

def run_capture(cmd, tag, probe_secs=3.0):
    log(f"--- {tag}: launching ---")
    t0 = time.time()
    p = subprocess.Popen(cmd)
    hits = {}
    deadline = time.time() + probe_secs
    while time.time() < deadline:
        for h in windows():
            hits[h] = time.time()
        time.sleep(0.05)
    p.wait()
    log(f"--- {tag}: exited after {time.time()-t0:.2f}s; windows observed: {len(hits)}")
    for h in hits:
        log(f"    {tag} WIN VIS={int(h[5])} pid={h[0]} exe={h[1]} session={h[2]} class={h[3]} title={h[4]!r}")

launcher_info()
log(f"=== A/B start {datetime.datetime.now():%H:%M:%S} ===")
log("baseline windows:")
dump_windows("baseline")

RAW = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT]
run_capture(RAW, "A-raw-as-task-runs-it")
time.sleep(1.0)
log("baseline windows again:")
dump_windows("baseline2")

HID = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", SCRIPT]
run_capture(HID, "B-hidden")

log(f"=== A/B end {datetime.datetime.now():%H:%M:%S} ===")
