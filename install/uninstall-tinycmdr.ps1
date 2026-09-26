# tinycmdr uninstaller (Windows).
# Removes the scheduled task, the running processes, the user-Path entry and the
# install folder - through the installer's own -Uninstall path, so the removal
# logic has ONE home. Shipped in every package and copied into the install with
# the installer, so day-two removal never needs the original package.
#
# The default folder is the installer's own default (%USERPROFILE%\tinycmdr). An install
# that was given -InstallDir - or steered there by the package's fleet-defaults.json - lives
# somewhere else, so pass the same -InstallDir here; the PATH entry, the Startup shortcut
# and the task all follow the folder, not this default.
#
# Stock Windows blocks .ps1 files outright (execution policy Restricted), so the
# documented form is a wrapper. Both of these take -InstallDir/-Force as well:
#
#   install\install-tinycmdr.cmd -Uninstall -Force [-InstallDir <folder>]
#   powershell -ExecutionPolicy Bypass -File install\uninstall-tinycmdr.ps1 -Force
#
#   install\uninstall-tinycmdr.ps1 [-InstallDir <folder>] [-TaskName Tinycmdr]
#                                  [-Force] [-NoPause]
#
# -Force deletes the folder without asking; without it you are asked first.
param(
    [string] $InstallDir = (Join-Path $env:USERPROFILE "tinycmdr"),
    [string] $TaskName = "Tinycmdr",
    [switch] $Force,
    [switch] $NoPause
)
& (Join-Path $PSScriptRoot "install-tinycmdr.ps1") -Uninstall `
    -InstallDir $InstallDir -TaskName $TaskName -Force:$Force -NoPause:$NoPause @args
exit $LASTEXITCODE
