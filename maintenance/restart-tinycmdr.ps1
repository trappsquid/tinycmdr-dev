# Elevated: stop the running tinycmdr (S4U-launched, so a normal shell gets
# Access denied) and start the fixed build through the same path the logon
# task uses. Writes its own log - an elevated -Wait child's stdout does not
# come back to the caller.
# Paths come from this script's own location, so it works on any host and any
# -InstallDir (it used to hardcode one machine's folder).
$here = $PSScriptRoot
$install = Split-Path -Parent $here
$log = Join-Path $here 'restart-tinycmdr.log'
function Log([string]$m) { "$((Get-Date).ToString('s')) $m" | Add-Content -Path $log }

# The bot, its SUPERVISOR and the wscript launcher, scoped to this install - the
# same three clauses install-tinycmdr.ps1's Stop-TinycmdrProcesses uses (keep the
# two in step). The old filter was Name='pythonw.exe' + '*tinycmdr.py*': it never
# matched tinycmdr-supervise.py, so the surviving supervisor held the lock and
# relaunched the OLD bot while this script's wscript relaunch exited on that
# lock - a restart never reloaded the supervisor; and unscoped, it killed a
# Second install's bot on the same box. The supervisor clause
# is deliberately not scoped by dir: in the documented no-venv fallback its
# command line is "<machine python> tinycmdr-supervise.py" with no install path.
# Caveat: an update replaces FILES, not the supervisor PROCESS. After one that changed
# tinycmdr-supervise.py, the still-running supervisor is what relaunches the bot, so it
# keeps executing the old code until a restart from outside it runs (this helper, or a
# log off/on) - this helper kills it and the logon path starts the new one.
function Get-TinycmdrProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object {
          if (-not $_.CommandLine) { return $false }
          if ($_.Name -like 'python*') {
              return ($_.CommandLine -like "*$install*") -or
                     ($_.CommandLine -like '*tinycmdr-supervise.py*')
          }
          if ($_.Name -eq 'wscript.exe') {
              return ($_.CommandLine -like '*tinycmdr-service.vbs*')
          }
          return $false
      })
}

Log "=== restart run start (pid $PID, elevated: $(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))) ==="

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

# start through the service vbs = exactly what the Tinycmdr logon/startup task does
& wscript.exe //B //Nologo (Join-Path $install 'tinycmdr-service.vbs')
Log "launched tinycmdr-service.vbs"
Start-Sleep -Seconds 15
$now = Get-TinycmdrProcesses
$sup = @($now | Where-Object { $_.CommandLine -like '*tinycmdr-supervise.py*' })
Log "after start: $($now.Count) process(es), supervisor(s) $($sup.Count): $($now.ProcessId -join ',')"
Log "=== restart run end ==="
