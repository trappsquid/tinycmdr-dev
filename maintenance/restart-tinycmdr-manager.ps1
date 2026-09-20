$ErrorActionPreference = 'Continue'
Write-Output "=== live tinycmdr instances (before) ==="
$procs = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
         Where-Object { $_.CommandLine -like '*tinycmdr.py*' }
foreach ($p in $procs) { Write-Output ("pid {0}  {1}" -f $p.ProcessId, $p.CommandLine) }
Write-Output ("count: " + @($procs).Count)

Write-Output "=== health before ==="
try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 http://127.0.0.1:8787/api/health).Content }
catch { Write-Output ("health probe failed: " + $_.Exception.Message) }

foreach ($p in $procs) {
  Write-Output ("stopping pid " + $p.ProcessId)
  Stop-Process -Id $p.ProcessId -Force -ErrorAction Continue
}
Start-Sleep -Seconds 3
Write-Output "=== restarting through the task ==="
schtasks /Run /TN tinycmdr
Start-Sleep -Seconds 25

Write-Output "=== live tinycmdr instances (after) ==="
$after = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
         Where-Object { $_.CommandLine -like '*tinycmdr.py*' }
foreach ($p in $after) { Write-Output ("pid {0}  {1}" -f $p.ProcessId, $p.CommandLine) }
Write-Output ("count: " + @($after).Count)

Write-Output "=== health after ==="
try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 http://127.0.0.1:8787/api/health).Content }
catch { Write-Output ("health probe failed: " + $_.Exception.Message) }
