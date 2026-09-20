$ErrorActionPreference = 'Stop'
$base = 'C:/Users/<user>\tinycmdr\maintenance'

$act = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}\bios-firmware-update.ps1"' -f $base)
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date '2026-09-11T04:00:00')
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$prn = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'the manager box-BIOS-Firmware' -Action $act -Trigger $trg -Settings $set -Principal $prn `
    -Description 'Install Lenovo firmware driver M2WKT63A to M2WKT65A from Windows Update, then restart to apply flash. Created 2026-09-10.' -Force | Out-Null
'registered the manager box-BIOS-Firmware'

$act2 = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}\postflash-check.ps1"' -f $base)
$trg2 = New-ScheduledTaskTrigger -AtStartup
$trg2.Delay = 'PT1M'
Register-ScheduledTask -TaskName 'the manager box-PostFlash-Log' -Action $act2 -Trigger $trg2 -Settings $set -Principal $prn `
    -Description 'Append BIOS version + logical CPU count to bios-postflash.log at every boot.' -Force | Out-Null
'registered the manager box-PostFlash-Log'

'---VERIFY---'
Get-ScheduledTask -TaskName 'the manager box-BIOS-Firmware','the manager box-PostFlash-Log' | ForEach-Object {
    "$($_.TaskName) state=$($_.State) as=$($_.Principal.UserId) runlevel=$($_.Principal.RunLevel)"
    $_.Triggers | ForEach-Object { "   trigger=$($_.CimClass.CimClassName) start=$($_.StartBoundary) delay=$($_.Delay)" }
    "   action=$($_.Actions[0].Execute) $($_.Actions[0].Arguments)"
}
'---NEXT-RUN---'
Get-ScheduledTaskInfo -TaskName 'the manager box-BIOS-Firmware' | Select-Object TaskName,NextRunTime,LastRunTime,LastTaskResult | Format-List

'---SYNTAX-CHECK---'
foreach ($f in 'bios-firmware-update.ps1','postflash-check.ps1') {
    $p = Join-Path $base $f; $errs = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$errs)
    if ($errs.Count -eq 0) { "OK   $f parses clean" } else { "FAIL $f : $(($errs | ForEach-Object { $_.Message }) -join '; ')" }
}

'---DOCKERDESKTOPSTART-TRIGGER---'
$d = Get-ScheduledTask -TaskName 'DockerDesktopStart'
$d.Triggers | Format-List CimClass,StartBoundary,Enabled,Repetition
"principal: $($d.Principal.UserId) / LogonType=$($d.Principal.LogonType)"

'---HT-SETTING-NOW---'
(Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosSetting | Where-Object { $_.CurrentSetting -match 'HyperThreadingTechnology' }).CurrentSetting
