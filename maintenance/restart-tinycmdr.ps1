# Elevated: stop the running tinycmdr (S4U-launched, so a normal shell gets
# Access denied) and start the fixed build through the same path the logon
# task uses. Writes its own log - an elevated -Wait child's stdout does not
# come back to the caller.
# -Stop kills everything this install runs and does NOT relaunch it: the door
# `tinycmdr quit` uses, where leaving a stopped box stopped is the whole point.
# Paths come from this script's own location, so it works on any host and any
# -InstallDir (it used to hardcode one machine's folder).
param([switch]$Stop)
$action = if ($Stop) { "stop" } else { "restart" }
$here = $PSScriptRoot
$install = Split-Path -Parent $here
$log = Join-Path $here 'restart-tinycmdr.log'
function Log([string]$m) { "$((Get-Date).ToString('s')) $m" | Add-Content -Path $log }

# The bot, its SUPERVISOR and the wscript launcher, scoped to this install - the
# same clauses install-tinycmdr.ps1's Stop-TinycmdrProcesses uses (keep the
# two in step). The old filter was Name='pythonw.exe' + '*tinycmdr.py*': it never
# matched tinycmdr-supervise.py, so the surviving supervisor held the lock and
# relaunched the OLD bot while this script's wscript relaunch exited on that
# lock - a restart never reloaded the supervisor; and unscoped, it killed a
# Second install's bot on the same box.
# Everything this install runs carries the install dir in its command line: the
# bot, the supervisor (the vbs launcher passes the full ...\tinycmdr-supervise.py;
# bare `python tinycmdr-supervise.py` (no path) is deliberately NOT killed - that
# is the cost of never touching a second install's watchdog.
# Caveat: an update replaces FILES, not the supervisor PROCESS. After one that changed
# tinycmdr-supervise.py, the still-running supervisor is what relaunches the bot, so it
# keeps executing the old code until a restart from outside it runs (this helper, or a
# log off/on) - this helper kills it and the logon path starts the new one.
function Get-TinycmdrProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object {
          if (-not $_.CommandLine) { return $false }
          if ($_.Name -like 'python*' -or $_.Name -eq 'wscript.exe') {
              # IndexOf with OrdinalIgnoreCase, never -like: -like reads [ ] * ? in the
              # PATH as WILDCARDS, so a bracketed install dir matched nothing and the old
              # bot survived the restart. OrdinalIgnoreCase is a
              # literal compare with Windows path semantics.
              # A path BOUNDARY, not a substring: `C:\x\tinycmdr` must not match
              # `C:\x\tinycmdr-dev\...` and kill a sibling install's bot
              # (A-2026-10-08-174).
              return $_.CommandLine.IndexOf(($install.TrimEnd('\') + '\'),
                  [System.StringComparison]::OrdinalIgnoreCase) -ge 0
          }
          return $false
      })
}

Log "=== $action run start (pid $PID, elevated: $(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))) ==="

$exitCode = 0
$procs = Get-TinycmdrProcesses
Log "tinycmdr processes found: $($procs.Count) -> $($procs.ProcessId -join ',')"
foreach ($b in $procs) {
    try {
        Stop-Process -Id $b.ProcessId -Force -ErrorAction Stop
        Log "  killed pid $($b.ProcessId)"
    } catch {
        Log "  FAILED to kill pid $($b.ProcessId): $($_.Exception.Message)"
    }
}
Start-Sleep -Seconds 4
$left = Get-TinycmdrProcesses
Log "after kill: $($left.Count) tinycmdr process(es) left"
if ($left.Count -gt 0) { $exitCode = 1 }

if ($Stop) {
    Log "stop requested: the launcher is NOT relaunched"
    Log "=== stop run end (exit $exitCode) ==="
    exit $exitCode
}

# start through the service vbs = exactly what the Tinycmdr logon/startup task does
& wscript.exe //B //Nologo (Join-Path $install 'tinycmdr-service.vbs')
Log "launched tinycmdr-service.vbs"
# Wait for it to appear rather than sampling once at 15s: the supervisor starts the bot,
# and on a cold box that has taken longer. A bare 0 here used to exit 0 anyway; the
# helper now returns the verdict (the macOS and POSIX twins always did).
$now = @()
for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Seconds 3
    $now = Get-TinycmdrProcesses
    if ($now.Count -gt 0) { break }
}
$sup = @($now | Where-Object { $_.CommandLine -like '*tinycmdr-supervise.py*' })
Log "after start: $($now.Count) process(es), supervisor(s) $($sup.Count): $($now.ProcessId -join ',')"
if ($now.Count -eq 0) { $exitCode = 1 }
Log "=== $action run end (exit $exitCode) ==="
exit $exitCode
