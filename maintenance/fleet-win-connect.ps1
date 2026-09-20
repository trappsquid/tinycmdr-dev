#Requires -Version 5
<#
    fleet-win-connect.ps1 - reach the Windows tinycmdr boxes from a bot shell.

    A bot shell is an S4U logon with no network credentials, so \\host\C$ is
    "Access is denied" until a session is established. This reads the shared
    password from %USERPROFILE%\.nas-cred (never inline it in a command line)
    and opens the admin-share session on .9 / .20.

    usage:
      powershell -ExecutionPolicy Bypass -File fleet-win-connect.ps1 -Report
      powershell -ExecutionPolicy Bypass -File fleet-win-connect.ps1 -Push
          -Source C:/Users/<user>\tinycmdr\tinycmdr.py
      powershell -ExecutionPolicy Bypass -File fleet-win-connect.ps1 -Restart

    -Report  version + sha256 + web config of each host (read-only)
    -Push    copy the source file over the host's tinycmdr.py (keeps a .bak once)
    -Restart re-run the scheduled task "tinycmdr" on each host
#>
param(
    [switch]$Report,
    [switch]$Push,
    [switch]$Restart,
    [string]$Source = "$env:USERPROFILE\tinycmdr\tinycmdr.py"
)
$ErrorActionPreference = "Continue"
if (-not ($Report -or $Push -or $Restart)) { $Report = $true }

$credFile = "$env:USERPROFILE\.nas-cred"
if (-not (Test-Path $credFile)) { Write-Output "no $credFile - cannot authenticate"; exit 2 }
$pw = (Get-Content $credFile -Raw).Trim()

$hosts = @(
    @{ name = "the other Windows box   a LAN address";  ip = "a LAN address";  user = "David Trapp"; task = "tinycmdr" },
    @{ name = "the Windows test box a LAN address"; ip = "a LAN address"; user = "David Trapp"; task = "tinycmdr" }
)

foreach ($h in $hosts) {
    $unc = "\\$($h.ip)\C`$"
    # Drop any stale session so a wrong cached credential cannot fake success.
    cmd /c "net use `"$unc`" /delete /y" 2>&1 | Out-Null
    $out = cmd /c "net use `"$unc`" /user:`"$($h.user)`" `"$pw`" 2>&1"
    $ok = $LASTEXITCODE -eq 0
    if ($ok) { Write-Output ("== {0}  share open" -f $h.name) }
    else { Write-Output ("== {0}  share FAILED: {1}" -f $h.name, ($out -join " ")); continue }
    $dir = "$unc\tinycmdr"
    Remove-Variable pw -ErrorAction SilentlyContinue; $pw = (Get-Content $credFile -Raw).Trim()

    if ($Report -or $Push -or $Restart) {
        $remote = "$dir\tinycmdr.py"
        if (Test-Path $remote) {
            $rh = (Get-FileHash $remote -Algorithm SHA256).Hash.ToLower().Substring(0, 16)
            $rv = ((Select-String -Path $remote -Pattern '^VERSION' | Select-Object -First 1).Line -replace '.*"([^"]+)".*', '$1')
            $lh = (Get-FileHash $Source -Algorithm SHA256).Hash.ToLower().Substring(0, 16)
            Write-Output ("   VERSION {0}  sha {1}   local sha {2}   {3}" -f $rv, $rh, $lh, $(if ($rh -eq $lh) { "in sync" } else { "DRIFT" }))
            try {
                $cfg = Get-Content "$dir\config.json" -Raw | ConvertFrom-Json
                Write-Output ("   web: enabled={0} port={1} token={2} host={3}" -f $cfg.web.enabled, $cfg.web.port, $(if ($cfg.web.token) { "set" } else { "none" }), $cfg.web.host)
            } catch { Write-Output "   web: config.json unreadable ($($_.Exception.Message))" }
        } else { Write-Output "   no $remote" }
    }

    if ($Push) {
        $remote = "$dir\tinycmdr.py"
        if (Test-Path $remote) {
            $bak = "$dir\maintenance\tinycmdr.py.bak-push-$(Get-Date -Format yyyyMMdd-HHmmss)"
            Copy-Item $remote $bak -Force
            Write-Output "   backup -> $bak"
        }
        Copy-Item $Source $remote -Force
        if ($?) {
            $nh = (Get-FileHash $remote -Algorithm SHA256).Hash.ToLower().Substring(0, 16)
            Write-Output "   copied, remote sha now $nh"
        } else { Write-Output "   COPY FAILED" }
    }

    if ($Restart) {
        $r = cmd /c "schtasks /run /tn `"$($h.task)`" /s $($h.ip) /u `"$($h.user)`" /p `"$pw`" 2>&1"
        Write-Output ("   schtasks /run -> {0} {1}" -f $LASTEXITCODE, ($r -join " "))
        Remove-Variable pw -ErrorAction SilentlyContinue; $pw = (Get-Content $credFile -Raw).Trim()
    }
}
