# tinycmdr, in one line (Windows):
#
#   irm https://github.com/trappsquid/tinycmdr/releases/latest/download/install.ps1 | iex
#
# With the installer's own switches:
#
#   iex "& { $(irm https://github.com/trappsquid/tinycmdr/releases/latest/download/install.ps1) } -InstallDir D:\tinycmdr"
#
# This is the thin door, not a second installer: it fetches the newest archive, expands
# it into a temp folder and runs INSTALL-WINDOWS.cmd in it, so every rule about the
# install itself stays in install\install-tinycmdr.ps1. `iex` runs the fetched text as a
# string, so no execution-policy change is needed the way a .ps1 FILE would need one.
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $InstallerArgs
)
$ErrorActionPreference = 'Stop'
$base = if ($env:TINYCMDR_URL) { $env:TINYCMDR_URL.TrimEnd('/') }
        else { 'https://github.com/trappsquid/tinycmdr/releases/latest/download' }
$asset = 'tinycmdr-win.zip'

function Say($m) { Write-Host "`n=== $m" }
function Die($m) { Write-Host "`n*** $m" -ForegroundColor Red; exit 1 }

$tmp = Join-Path $env:TEMP ('tinycmdr.unpack.' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
try {
    Say "tinycmdr: fetching $asset"
    $zip = Join-Path $tmp $asset
    try { Invoke-WebRequest -Uri "$base/$asset" -OutFile $zip -UseBasicParsing }
    catch { Die "could not download $base/$asset - $($_.Exception.Message)" }

    Say 'expanding'
    Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem -LiteralPath $tmp -Directory |
        Where-Object { $_.Name -like 'tinycmdr-*' } | Select-Object -First 1
    if (-not $src) { Die 'the archive did not contain a tinycmdr-* folder' }
    if (-not (Test-Path (Join-Path $src.FullName 'tinycmdr.py'))) {
        Die "$($src.FullName) has no tinycmdr.py - the download looks wrong" }
    $door = Join-Path $src.FullName 'INSTALL-WINDOWS.cmd'
    if (-not (Test-Path $door)) { Die "$($src.FullName) has no INSTALL-WINDOWS.cmd - the download looks wrong" }

    Say 'handing over to INSTALL-WINDOWS.cmd'
    Push-Location $src.FullName
    try {
        if ($InstallerArgs.Count -gt 0) { & cmd.exe /c INSTALL-WINDOWS.cmd @InstallerArgs }
        else { & cmd.exe /c INSTALL-WINDOWS.cmd }
        $rc = $LASTEXITCODE
    } finally { Pop-Location }

    # 3 is the installer's "installed, but the model endpoint did not answer" - not a failure.
    if ($rc -ne 0 -and $rc -ne 3) {
        Write-Host "`n*** the installer exited $rc - the unpacked copy is left in $src" -ForegroundColor Red
        exit $rc
    }
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "`n    the unpacked folder was temporary. The installed copy carries its own installer and"
    Write-Host "    uninstaller (%USERPROFILE%\tinycmdr by default), so later:"
    Write-Host "      powershell -File `"$env:USERPROFILE\tinycmdr\install\uninstall-tinycmdr.ps1`""
} catch {
    Write-Host "`n*** $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "    (what was unpacked is still in $tmp)" -ForegroundColor Yellow
    exit 1
}
