# the manager box BIOS firmware update - scheduled maintenance (M2WKT63A -> M2WKT65A)
# Installs the Lenovo firmware driver offered by Windows Update, then reboots to
# apply the flash. Deliberately does NOT reboot unless the install reports success.
# Log: maintenance\bios-firmware-update.log

$ErrorActionPreference = 'Continue'
$log = 'C:/Users/<user>\tinycmdr\maintenance\bios-firmware-update.log'
function Log([string]$m) { "$((Get-Date).ToString('s')) $m" | Add-Content -Path $log }

Log "=== firmware maintenance run start (pid $PID) ==="
try {
    $bios = Get-CimInstance Win32_BIOS
    Log "BIOS before: $($bios.SMBIOSBIOSVersion) (release $($bios.ReleaseDate))"
} catch { Log "WARN could not read BIOS before: $($_.Exception.Message)" }

# ---------------------------------------------------------------- find update
try {
    $session  = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result   = $searcher.Search("IsInstalled=0")
    $fw = @($result.Updates | Where-Object { $_.Title -match 'Lenovo' -and $_.Title -match 'Firmware' })
    Log "Windows Update offered $($result.Updates.Count) update(s); Lenovo firmware matches: $($fw.Count)"
} catch {
    Log "ABORT: update search failed: $($_.Exception.Message)"; exit 1
}

if ($fw.Count -eq 0) {
    Log "ABORT: no Lenovo firmware update offered right now - leaving system alone, NO reboot."
    exit 0
}

foreach ($u in $fw) { Log "candidate: '$($u.Title)' UpdateID=$($u.Identity.UpdateID)" }

# ---------------------------------------------------------------- download
try {
    $coll = New-Object -ComObject Microsoft.Update.UpdateColl
    foreach ($u in $fw) {
        if (-not $u.EulaAccepted) { [void]$u.AcceptEula() }
        [void]$coll.Add($u)
    }
    $dl = $session.CreateUpdateDownloader(); $dl.Updates = $coll
    $dres = $dl.Download()
    Log "download: ResultCode=$($dres.ResultCode) HResult=0x$('{0:X8}' -f $dres.HResult)"
} catch {
    Log "ABORT: download failed: $($_.Exception.Message)"; exit 1
}

# ---------------------------------------------------------------- install
try {
    $inst = $session.CreateUpdateInstaller(); $inst.Updates = $coll
    $ires = $inst.Install()
    Log "install: ResultCode=$($ires.ResultCode) HResult=0x$('{0:X8}' -f $ires.HResult) RebootRequired=$($ires.RebootRequired)"
} catch {
    Log "ABORT: install threw: $($_.Exception.Message)"; exit 1
}

if ($ires.HResult -ne 0 -or ($ires.ResultCode -ne 2 -and $ires.ResultCode -ne 3)) {
    Log "ABORT: install not successful - NOT rebooting."
    exit 1
}

# ---------------------------------------------------------------- reboot
# DO NOT `docker stop` here. An explicit stop clears the container from
# restart=unless-stopped, so after the reboot NOTHING restarts the stack (this
# stranded all 11 containers, and tinycmdr with them, on 2026-09-10). Windows
# shutdown stops Docker Desktop cleanly anyway, and task the manager box-StartContainers
# starts anything still down once the engine is back.
Log "Firmware staged successfully. Leaving containers alone - the manager box-StartContainers recovers the stack after boot."

Log "Issuing restart in 120s to apply firmware flash."
& shutdown.exe /r /t 120 /f /c "Scheduled BIOS firmware update (M2WKT63A -> M2WKT65A)" /d p:4:1 2>&1 | ForEach-Object { Log "  shutdown: $_" }
Log "=== run end ==="
