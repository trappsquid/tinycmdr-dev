@echo off
rem =====================================================================
rem  tinycmdr - START HERE on Windows. Double-click THIS file.
rem
rem  It sits in the package root so you do not have to go looking. It runs
rem  install\install-tinycmdr.cmd, which keeps its window open and writes
rem  everything to %TEMP%\tinycmdr-install.log.
rem
rem  No administrator rights are needed: the agent is installed into your own
rem  profile, its dependencies are fetched into a virtual environment inside
rem  that folder, and it starts at logon. If Python is missing, the installer
rem  downloads and installs it.
rem
rem  Do NOT double-click install\install-tinycmdr.ps1: stock Windows blocks .ps1
rem  files (execution policy Restricted) and that window closes before you can read
rem  the error. If you prefer a shell, the line is in README.md, under
rem  "Install on a new Windows host".
rem
rem  Switches pass straight through, e.g.
rem      INSTALL-WINDOWS.cmd -InstallDir D:\tinycmdr
rem      INSTALL-WINDOWS.cmd -AsService         (boot-start task; needs admin)
rem =====================================================================
setlocal
set "HERE=%~dp0"
if not exist "%HERE%install\install-tinycmdr.cmd" (
    echo.
    echo ERROR: install\install-tinycmdr.cmd is not next to this file.
    echo Extract the WHOLE archive first, then run INSTALL-WINDOWS.cmd from inside it.
    echo.
    pause
    exit /b 1
)
call "%HERE%install\install-tinycmdr.cmd" %*
exit /b %ERRORLEVEL%
