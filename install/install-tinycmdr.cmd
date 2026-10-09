@echo off
rem =====================================================================
rem  tinycmdr installer - DOUBLE-CLICK THIS FILE.
rem
rem  Why not the .ps1 directly? Windows sets the script execution policy to
rem  Restricted on many machines, so a .ps1 opened by double-click (or by
rem  "Run with PowerShell") prints an error and the window closes before you
rem  can read a word of it. This wrapper runs PowerShell with
rem  -ExecutionPolicy Bypass, keeps the window open, and logs everything.
rem
rem  NO ADMINISTRATOR RIGHTS ARE NEEDED. The install goes into your own
rem  profile (%USERPROFILE%\tinycmdr), its dependencies go into a virtual
rem  environment inside that folder, and it starts at logon through a
rem  shortcut in your Startup folder. Nothing outside your profile is
rem  touched, so Windows has nothing to ask you about.
rem
rem  The one exception is -AsService, which registers a boot-start Windows
rem  scheduled task. Windows reserves those for administrators, so that
rem  switch alone needs an elevated shell:
rem      install-tinycmdr.cmd -AsService
rem
rem  Pass installer switches straight through, e.g.
rem      install-tinycmdr.cmd -MattermostUrl chat.example.com -AllowedUser abc123
rem
rem  To remove an install again, this same wrapper -Uninstall switches to the
rem  uninstaller without a second download. -InstallDir is honoured (the uninstaller's
rem  own default is %USERPROFILE%\tinycmdr and cannot find a custom folder on its own),
rem  and so is -Force (delete without asking):
rem      install-tinycmdr.cmd -Uninstall [-InstallDir D:\tinycmdr] -Force
rem  The install folder also carries UNINSTALL-WINDOWS.cmd, the same removal as a
rem  double-click.
rem =====================================================================
setlocal
set "HERE=%~dp0"
set "PS1=%HERE%install-tinycmdr.ps1"
set "LOG=%TEMP%\tinycmdr-install.log"

if not exist "%PS1%" (
    echo.
    echo ERROR: could not find "%PS1%".
    echo Extract the whole package first, then run install\install-tinycmdr.cmd
    echo   from the extracted folder.
    echo.
    pause
    exit /b 1
)

rem Windows refuses to delete a directory that is a live process's current directory,
rem and that includes this wrapper's own shell when it was run after a cd into the
rem install folder. Step out for an uninstall only: a relative -SecretsFile keeps the
rem shell's own directory on the install path.
set "UNINSTALLING="
if not "%~1"=="" for %%A in (%*) do if /i "%%~A"=="-Uninstall" set "UNINSTALLING=1"
if defined UNINSTALLING cd /d "%TEMP%"

echo Running the tinycmdr installer. Log: %LOG%
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -NoPause %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo Installer exited with code %RC%. See %LOG%

if not "%FB_NOPAUSE%"=="1" (
    echo Press any key to close this window.
    pause >nul
)
exit /b %RC%
