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
rem  Needs administrator rights to register the scheduled task, and asks for
rem  them itself (UAC). For a files-only install with no admin:
rem      powershell -ExecutionPolicy Bypass -File install-tinycmdr.ps1 -SkipTask
rem
rem  Pass installer switches straight through, e.g.
rem      install-tinycmdr.cmd -MattermostUrl chat.example.com -AllowedUser abc123
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

rem -VerifyOnly and -SkipTask never touch the scheduled task, so no UAC for them
set "NOELEV="
echo %* | findstr /I /C:"-VerifyOnly" /C:"-SkipTask" >nul 2>&1 && set "NOELEV=1"

rem already elevated? "net session" fails without admin rights
net session >nul 2>&1
if errorlevel 1 (
    if "%NOELEV%"=="1" goto run
    if not "%FB_NOELEV%"=="1" if not "%NOELEV%"=="1" (
        rem Switches typed here have to survive the elevation: the first version forwarded
        rem -File <ps1> only, so -Force / -MattermostTokenFile went nowhere and the elevated
        rem window did the default install instead. An empty %* must not become an empty
        rem argument, so it is only appended when there is something to append.
        set "FB_ARGS=%*"
        echo Asking for administrator rights ^(needed for the scheduled task^)...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$arg = '-NoProfile -ExecutionPolicy Bypass -File ' + [char]34 + $env:PS1 + [char]34 + ' ' + $env:FB_ARGS; Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arg.Trim()"
        echo.
        echo The installer is running in the elevated window that just opened.
        echo This window can be closed.
        echo.
        echo If that window closes without a word, read %LOG%
        timeout /t 10 >nul
        exit /b 0
    )
)

:run
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
