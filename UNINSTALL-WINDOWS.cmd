@echo off
rem =====================================================================
rem  tinycmdr uninstaller - DOUBLE-CLICK THIS FILE to remove it.
rem
rem  It removes the tinycmdr installed in THIS folder: stops the bot, its
rem  supervisor and the launcher, removes the autostart entry and the
rem  folder itself (config, notes, sessions and .env go with the folder -
rem  that is what removing it means). Your Mattermost bot token is not
rem  touched; revoke it in the server's profile page.
rem
rem  It asks once, so it is safe to look at. For a script, the flagless
rem  line is:
rem      install\install-tinycmdr.cmd -Uninstall -Force -InstallDir "%USERPROFILE%\tinycmdr"
rem  A custom -InstallDir install: run this door from that folder (it acts
rem  on the folder it sits in), or pass -InstallDir to the line above.
rem
rem  Why a .cmd: stock Windows refuses a double-clicked .ps1 (the script
rem  execution policy), and that window closes before the error can be
rem  read - the same reason INSTALL-WINDOWS.cmd exists.
rem =====================================================================
setlocal
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

if not exist "%HERE%\tinycmdr.py" (
    echo.
    echo ERROR: there is no tinycmdr.py in "%HERE%".
    echo This door removes the tinycmdr installed in its own folder; nothing
    echo was changed.
    echo.
    pause
    exit /b 1
)
if not exist "%HERE%\config.json" if not exist "%HERE%\venv" (
    echo.
    echo ERROR: "%HERE%" does not look like a tinycmdr install.
    echo An extracted package has no config.json and no venv\ - if this is the
    echo package, run this door from the install folder instead (a real install is
    echo %USERPROFILE%\tinycmdr unless -InstallDir put it elsewhere). Nothing was
    echo changed.
    echo.
    pause
    exit /b 1
)
if not exist "%HERE%\install\install-tinycmdr.cmd" (
    echo.
    echo ERROR: "%HERE%\install\install-tinycmdr.cmd" is missing, so the removal
    echo cannot run. If you are sure nothing runs from this folder, delete it
    echo by hand. Nothing was changed.
    echo.
    pause
    exit /b 1
)

echo This removes the tinycmdr installed in:
echo   %HERE%
echo.
echo The bot, its supervisor and its autostart entry are stopped and removed,
echo and the folder goes with them (config, notes, sessions and .env too).
echo.
set "ANS="
set /p "ANS=Remove it? (y/N) "
if /i not "%ANS%"=="y" if /i not "%ANS%"=="yes" (
    echo Nothing was removed.
    pause
    exit /b 0
)
echo.
rem -Force: the confirmation was just asked here, and the wrapper cannot ask
rem (it always passes -NoPause, so the script's own interactive branch is
rem unreachable). FB_NOPAUSE: this door pauses once, at the end.
set "FB_NOPAUSE=1"
call "%HERE%\install\install-tinycmdr.cmd" -Uninstall -Force -InstallDir "%HERE%"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
    echo The removal exited with code %RC% - the lines above say what is left.
    echo Close anything using the folder, then run this door again.
)
pause
exit /b %RC%
