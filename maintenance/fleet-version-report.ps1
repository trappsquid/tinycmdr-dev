#Requires -Version 5
<#
    fleet-version-report.ps1 - one line per tinycmdr host: VERSION, file sha256, drift flag, watchdog.

    Read-only. Reads the Windows boxes over \\host\C$ (same local account everywhere) and the
    Unix ones over ssh (<user> / [redacted], key from ~/.ssh/fleet_ed25519; no password is
    passed anywhere). The file hash is the only thing that can tell two hosts apart while VERSION
    is the same.

    The watchdog column is the check that would have caught 2026-09-15: three hosts read "in sync"
    while two of them had no supervisor at all, because the boot path is host-local and travels in
    no archive. A host whose launcher never hands off to a supervisor (Windows) or whose service
    is not Restart=always / has no KeepAlive (Linux, macOS) now reads NO WATCHDOG instead of
    looking standardized.
#>
$ErrorActionPreference = "Continue"
$REF = "$env:USERPROFILE\tinycmdr\tinycmdr.py"

function Get-RemoteInfo([string]$path) {
    if (-not (Test-Path $path)) { return $null }
    $h = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    $v = (Select-String -Path $path -Pattern '^VERSION' | Select-Object -First 1).Line
    [pscustomobject]@{ hash = $h.Substring(0, 16); version = ($v -replace '.*"([^"]+)".*', '$1') }
}

# Windows: supervised means the launcher VBS hands off to tinycmdr-supervise.py and the file is
# actually there. The old arrangement (VBS launching tinycmdr.py directly) reads NO WATCHDOG.
function Get-WindowsWatchdog([string]$vbs, [string]$supervisor) {
    if (-not (Test-Path $vbs)) { return "NO LAUNCHER" }
    $launches = (Select-String -Path $vbs -Pattern 'tinycmdr-supervise\.py' -Quiet) -eq $true
    $present = Test-Path $supervisor
    if ($launches -and $present) { return "supervisor" }
    if ($launches) { return "supervisor MISSING FILE" }
    return "NO WATCHDOG"
}

$refInfo = Get-RemoteInfo $REF
$refWatch = Get-WindowsWatchdog "$env:USERPROFILE\tinycmdr\tinycmdr-service.vbs" `
                               "$env:USERPROFILE\tinycmdr\tinycmdr-supervise.py"
Write-Output ("reference  the manager box(local)         {0}  {1}  {2}" -f $refInfo.version, $refInfo.hash, $refWatch)

$targets = @(
    @{ name = "the other Windows box      a LAN address"; path = "\\a LAN address\C`$\tinycmdr\tinycmdr.py"
       vbs = "\\a LAN address\C`$\tinycmdr\tinycmdr-service.vbs"
       sup = "\\a LAN address\C`$\tinycmdr\tinycmdr-supervise.py" },
    @{ name = "the Windows test box    a LAN address"; path = "\\a LAN address\C`$\tinycmdr\tinycmdr.py"
       vbs = "\\a LAN address\C`$\tinycmdr\tinycmdr-service.vbs"
       sup = "\\a LAN address\C`$\tinycmdr\tinycmdr-supervise.py" }
)
foreach ($t in $targets) {
    $i = Get-RemoteInfo $t.path
    if ($i) {
        $flag = if ($i.hash -eq $refInfo.hash) { "in sync" } else { "DRIFT   " }
        Write-Output ("{0,-28} {1}  {2}  {3}  {4}" -f $t.name, $i.version, $i.hash, $flag,
                      (Get-WindowsWatchdog $t.vbs $t.sup))
    } else {
        Write-Output ("{0,-28} unreachable" -f $t.name)
    }
}

# Unix hosts: over ssh via the aliases in ~/.ssh/config. The watchdog probe is platform-specific
# and deliberately dumb: one value out, judged here, so no quoting lives inside the remote command.
$unix = @(
    @{ name = "MacBook      a LAN address"; alias = "mac-host-a"; path = "/Users/[redacted]/tinycmdr/tinycmdr.py"
       dogcmd = "plutil -p ~/Library/LaunchAgents/com.trapp.tinycmdr.plist 2>/dev/null | grep -c KeepAlive"; kind = "keepalive" },
    @{ name = "the Linux test box    a LAN address"; alias = "the Linux test box"; path = "/home/<user>/tinycmdr/tinycmdr.py"
       dogcmd = "systemctl show tinycmdr -p Restart --value 2>/dev/null"; kind = "restart" },
    @{ name = "the LAN model box        a LAN address"; alias = "the LAN model box"; path = "/home/<user>/tinycmdr/tinycmdr.py"
       dogcmd = "systemctl show tinycmdr -p Restart --value 2>/dev/null"; kind = "restart" }
)
foreach ($u in $unix) {
    $cmd = "sha256sum $($u.path) 2>/dev/null || shasum -a 256 $($u.path); grep -m1 '^VERSION' $($u.path); $($u.dogcmd)"
    $out = (ssh -o BatchMode=yes -o ConnectTimeout=8 $u.alias $cmd 2>$null)
    if (-not $out) { Write-Output ("{0,-28} ssh unavailable" -f $u.name); continue }
    # Parse PER HOST and never fall back to the previous host's values: a probe that
    # returns no hash line means "there is no build at that path", which must be said
    # out loud rather than dressed up as the last host's row (measured 2026-09-20,
    # when the LAN model box was still unmigrated and printed as "in sync").
    $h = $null; $v = $null; $dogRaw = ""
    foreach ($line in @($out)) {
        if ($line -match '^([0-9a-fA-F]{64})\s') { $h = $Matches[1].Substring(0, 16) }
        elseif ($line -match 'VERSION\s*=\s*"([^"]+)"') { $v = $Matches[1] }
        elseif ($line.Trim()) { $dogRaw = $line.Trim() }
    }
    if (-not $h) { Write-Output ("{0,-28} no build at {1}" -f $u.name, $u.path); continue }
    $dog = if ($u.kind -eq "restart") {
        if ($dogRaw -eq "always") { "systemd" } else { "NO WATCHDOG ($dogRaw)" }
    } else {
        if ($dogRaw -match '^[1-9]') { "launchd" } else { "NO WATCHDOG" }
    }
    $flag = if ($h -eq $refInfo.hash) { "in sync" } else { "DRIFT   " }
    Write-Output ("{0,-28} {1}  {2}  {3}  {4}" -f $u.name, $v, $h, $flag, $dog)
}
