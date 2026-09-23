# tinycmdr uninstaller (Windows).
# Removes the scheduled task, the running processes, the user-Path entry and the
# install folder - through the installer's own -Uninstall path, so the removal
# logic has ONE home. Shipped in every package and copied into the install with
# the installer, so day-two removal never needs the original package.
#
#   install\uninstall-tinycmdr.ps1 [-InstallDir C:\tinycmdr] [-TaskName Tinycmdr]
#                                  [-Force] [-NoPause]
#
# -Force deletes the folder without asking; without it you are asked first.
param(
    [string] $InstallDir = "C:\tinycmdr",
    [string] $TaskName = "Tinycmdr",
    [switch] $Force,
    [switch] $NoPause
)
& (Join-Path $PSScriptRoot "install-tinycmdr.ps1") -Uninstall `
    -InstallDir $InstallDir -TaskName $TaskName -Force:$Force -NoPause:$NoPause @args
exit $LASTEXITCODE
