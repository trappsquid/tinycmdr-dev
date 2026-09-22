$ErrorActionPreference = 'Stop'
$base = 'C:/Users/<user>\tinycmdr\maintenance'
$rlog = 'C:/Users/<user>\tinycmdr\maintenance\boot-recovery-register.log'
function Say($m) { "$((Get-Date).ToString('s')) $m" | Add-Content -Path $rlog }
"=== registration run $(Get-Date -Format s) ===" | Set-Content -Path $rlog

# ---- 1. boot-time container watchdog (SYSTEM, AtStartup +1m) ----
$act = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}\start-containers.ps1"' -f $base)
$trg = New-ScheduledTaskTrigger -AtStartup
$trg.Delay = 'PT1M'
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$prn = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'the manager box-StartContainers' -Action $act -Trigger $trg -Settings $set `
    -Principal $prn -Description 'After every boot: wait for the Docker engine, then start any example container that is not running. Safety net for the docker-stop-before-reboot trap. Created 2026-09-10.' -Force | Out-Null
Say 'registered the manager box-StartContainers'

# ---- 2. Tinycmdr: add an AtStartup trigger so it returns with no logon ----
$t = Get-ScheduledTask -TaskName 'Tinycmdr'
$existing = @($t.Triggers)
$hasStartup = $existing | Where-Object { $_.CimClass.CimClassName -match 'MSFT_TaskBootTrigger' }
if ($hasStartup) {
    Say 'Tinycmdr already has a boot trigger - leaving triggers alone'
} else {
    $boot = New-ScheduledTaskTrigger -AtStartup
    $boot.Delay = 'PT4M'
    Set-ScheduledTask -TaskName 'Tinycmdr' -Trigger ($existing + $boot) | Out-Null
    Say 'added AtStartup +4m trigger to Tinycmdr'
}

# ---- 3. verify ----
Say '--- VERIFY ---'
foreach ($n in 'the manager box-StartContainers','Tinycmdr') {
    $task = Get-ScheduledTask -TaskName $n
    Say "$n state=$($task.State) user=$($task.Principal.UserId) runlevel=$($task.Principal.RunLevel)"
    foreach ($tr in $task.Triggers) { Say "   trigger=$($tr.CimClass.CimClassName) delay=$($tr.Delay)" }
}
foreach ($f in 'start-containers.ps1') {
    $p = Join-Path $base $f; $errs = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$errs)
    if ($errs.Count -eq 0) { Say "OK   $f parses clean" } else { Say "FAIL $f : $(($errs | ForEach-Object { $_.Message }) -join '; ')" }
}
