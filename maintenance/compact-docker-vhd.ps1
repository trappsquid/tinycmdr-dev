# Compact Docker's VHDX at boot, ONLY when no interactive session and no WSL/Docker VM is running.
# background: docker image prune reclaimed ~24 GB *inside* docker_data.vhdx; the file itself
# never shrinks, so C: gains nothing until it is compacted while offline.
# Log: maintenance\docker-vhd-compact.log

$log = 'C:/Users/<user>\tinycmdr\maintenance\docker-vhd-compact.log'
$vhdx = 'C:/Users/<user>\AppData\Local\Docker\wsl\disk\docker_data.vhdx'
function Log([string]$m) { "$((Get-Date).ToString('s')) $m" | Add-Content -Path $log }

Log "=== vhdx compact run start ==="

if (-not (Test-Path $vhdx)) { Log "ABORT: $vhdx not found"; exit 0 }

# --- safety gates -------------------------------------------------------------
$explorer = @(Get-Process -Name explorer -ErrorAction SilentlyContinue)
$sessions = @(query user 2>$null | Where-Object { $_ -match '\S' })
$vmProcs  = @(Get-Process -Name vmmemWSL,vmmem,vmcompute,wslservice,dockerd -ErrorAction SilentlyContinue)

if ($explorer.Count -gt 0 -or $sessions.Count -gt 1) {
    Log "SKIP: interactive session present (explorer=$($explorer.Count), sessions=$($sessions.Count)) - not touching the disk"
    exit 0
}
if ($vmProcs.Count -gt 0) {
    Log "SKIP: WSL/Docker VM is running ($(($vmProcs | Select-Object -ExpandProperty Name) -join ',')) - refusing to compact a live disk"
    exit 0
}

$freeBefore = [math]::Round((Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'").FreeSpace / 1GB, 2)
$sizeBefore = [math]::Round((Get-Item $vhdx).Length / 1GB, 2)
Log "before: vhdx=$sizeBefore GB, C: free=$freeBefore GB"

# --- compact ------------------------------------------------------------------
$dpScript = @"
select vdisk file="$vhdx"
attach vdisk readonly
compact vdisk
detach vdisk
"@
$dpPath = 'C:/Users/<user>\tinycmdr\maintenance\compact.diskpart.txt'
$dpScript | Out-File -FilePath $dpPath -Encoding ascii
$out = & diskpart /s $dpPath 2>&1
$out | ForEach-Object { Log "  diskpart: $_" }

$freeAfter = [math]::Round((Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'").FreeSpace / 1GB, 2)
$sizeAfter = [math]::Round((Get-Item $vhdx).Length / 1GB, 2)
Log "after:  vhdx=$sizeAfter GB, C: free=$freeAfter GB  (vhdx shrank $([math]::Round($sizeBefore-$sizeAfter,2)) GB, C: gained $([math]::Round($freeAfter-$freeBefore,2)) GB)"
Log "=== run end ==="
