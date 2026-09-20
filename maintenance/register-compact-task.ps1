$ErrorActionPreference = 'Stop'
$base = 'C:/Users/<user>\tinycmdr\maintenance'

$act = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}\compact-docker-vhd.ps1"' -f $base)
$trg = New-ScheduledTaskTrigger -AtStartup
$trg.Delay = 'PT3M'
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$prn = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName 'the manager box-CompactDockerVhd' -Action $act -Trigger $trg -Settings $set -Principal $prn `
    -Description 'Compact docker_data.vhdx while offline. Skips itself if any interactive session or WSL/Docker VM is running. Created 2026-09-10.' -Force | Out-Null
'registered the manager box-CompactDockerVhd'

'---VERIFY---'
Get-ScheduledTask -TaskName 'the manager box-CompactDockerVhd' | ForEach-Object {
    "$($_.TaskName) state=$($_.State) as=$($_.Principal.UserId) runlevel=$($_.Principal.RunLevel)"
    $_.Triggers | ForEach-Object { "   trigger=$($_.CimClass.CimClassName) delay=$($_.Delay)" }
    "   action=$($_.Actions[0].Execute) $($_.Actions[0].Arguments)"
}

'---SYNTAX---'
$p = Join-Path $base 'compact-docker-vhd.ps1'; $errs = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$errs)
if ($errs.Count -eq 0) { 'OK   compact-docker-vhd.ps1 parses clean' } else { "FAIL: $(($errs | ForEach-Object { $_.Message }) -join '; ')" }

'---DRY-RUN-GATES-NOW (expected: SKIP, we are logged in)---'
$explorer = @(Get-Process -Name explorer -ErrorAction SilentlyContinue)
$vmProcs  = @(Get-Process -Name vmmemWSL,vmmem,vmcompute,wslservice,dockerd -ErrorAction SilentlyContinue)
"explorer sessions=$($explorer.Count)  wsl/docker procs=$($vmProcs.Count)"

'---ALL-MAINTENANCE-TASKS---'
Get-ScheduledTask | Where-Object { $_.TaskName -like 'the manager box-*' } | Select-Object TaskName,State | Format-Table -AutoSize
