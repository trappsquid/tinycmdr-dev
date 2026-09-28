$ErrorActionPreference = 'Continue'

# The truth surface that survived the built-in web UI's removal: `tinycmdr health`, one line and
# an exit code, run under this install's own interpreter. This used to poll the served page.
# Paths come from this script's own location, so it works on any host.
$install = Split-Path -Parent $PSScriptRoot
$venvPy  = Join-Path $install 'venv\Scripts\python.exe'

function Get-Health {
    if (-not (Test-Path $venvPy)) { return "no venv at $venvPy - run the installer first" }
    Push-Location $install
    try {
        $out  = & $venvPy tinycmdr.py health 2>&1
        $code = $LASTEXITCODE
        return ("{0} (exit {1})" -f (($out | Out-String).Trim()), $code)
    } finally { Pop-Location }
}

Write-Output "=== live tinycmdr instances (before) ==="
$procs = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
         Where-Object { $_.CommandLine -like '*tinycmdr.py*' }
foreach ($p in $procs) { Write-Output ("pid {0}  {1}" -f $p.ProcessId, $p.CommandLine) }
Write-Output ("count: " + @($procs).Count)

Write-Output "=== health before ==="
Write-Output (Get-Health)

foreach ($p in $procs) {
  Write-Output ("stopping pid " + $p.ProcessId)
  Stop-Process -Id $p.ProcessId -Force -ErrorAction Continue
}
Start-Sleep -Seconds 3
Write-Output "=== restarting through the task ==="
schtasks /Run /TN Tinycmdr
Start-Sleep -Seconds 25

Write-Output "=== live tinycmdr instances (after) ==="
$after = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
         Where-Object { $_.CommandLine -like '*tinycmdr.py*' }
foreach ($p in $after) { Write-Output ("pid {0}  {1}" -f $p.ProcessId, $p.CommandLine) }
Write-Output ("count: " + @($after).Count)

Write-Output "=== health after ==="
Write-Output (Get-Health)
