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

Log "=== restart run start (pid $PID, elevated: $(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))) ==="

$bots = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
          Where-Object { $_.CommandLine -like '*tinycmdr.py*' })
Log "tinycmdr processes found: $($bots.Count) -> $($bots.ProcessId -join ',')"
foreach ($b in $bots) {
    try {
        Stop-Process -Id $b.ProcessId -Force -ErrorAction Stop
        Log "  killed pid $($b.ProcessId)"
    } catch {
        Log "  FAILED to kill pid $($b.ProcessId): $($_.Exception.Message)"
    }
}
Start-Sleep -Seconds 4
$left = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
          Where-Object { $_.CommandLine -like '*tinycmdr.py*' })
Log "after kill: $($left.Count) tinycmdr process(es) left"

# start through the service vbs = exactly what the Tinycmdr logon/startup task does
& wscript.exe //B //Nologo (Join-Path $install 'tinycmdr-service.vbs')
Log "launched tinycmdr-service.vbs"
Start-Sleep -Seconds 15
$now = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
         Where-Object { $_.CommandLine -like '*tinycmdr.py*' })
Log "after start: $($now.Count) process(es): $($now.ProcessId -join ',')"
Log "=== restart run end ==="
