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
rem
rem  Why it re-runs itself from %TEMP%: this door LIVES in the folder the
rem  removal deletes, and cmd.exe reads a batch file while it runs - a
rem  deletion under it kills the tail, so the exit code, the error line and
rem  the closing pause never appear (measured 2026-10-09: a double-clicked
rem  door closed its window instantly with no message at all). The %TEMP%
rem  copy cannot be deleted by the removal, so its tail always runs.
rem =====================================================================
setlocal
rem Step out of the folder first: a live shell whose current directory is
rem the install folder blocks the delete on Windows, and the double-clicked
rem shell's cwd is exactly that.
cd /d "%TEMP%" 2>nul || goto :nocd
if /i "%~1"=="--removing" goto :removing
set "SELF=%TEMP%\tinycmdr-uninstall-%RANDOM%%RANDOM%.cmd"
rem The folder travels WITHOUT its trailing backslash: an argument ending in \" is the
rem classic CALL re-parse footgun, where the closing quote is read as escaped and the
rem copy gets a wrong folder or none.
set "TARGET=%~dp0"
if "%TARGET:~-1%"=="\" set "TARGET=%TARGET:~0,-1%"
rem TRANSFER, not CALL: the removal deletes this very file, and a script that waits to
rem return needs it again to do so - the tail dies with it (measured 2026-10-09,
rem windows-latest: exit 1, no barrier). Handing control over means this file's remaining
rem lines are never read again; the temp copy's exit code is the process's. `exit /b 1`
rem is unreachable unless the copy could not be executed at all.
copy /y "%~f0" "%SELF%" >nul 2>&1
if not exist "%SELF%" goto :inplace
"%SELF%" --removing "%TARGET%" %*
exit /b 1

:inplace
rem Could not copy to %TEMP% (a full or read-only temp). Run in place; the
rem removal may take this file with it and this tail may not survive, which
rem is the shape this door exists to avoid on a normal machine.
"%~f0" --removing "%TARGET%" %*
exit /b 1

:nocd
echo.
echo ERROR: could not step out of the install folder (is %%TEMP%% writable?).
echo Close this window and remove through the wrapper instead:
echo   "%USERPROFILE%\tinycmdr\install\install-tinycmdr.cmd" -Uninstall -Force
echo.
echo Press any key to close this window.
pause >nul
exit /b 2

:removing
set "RC=2"
set "HERE=%~2"
if "%HERE%"=="" goto :nofolder
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

if not exist "%HERE%\tinycmdr.py" goto :noapp
if not exist "%HERE%\config.json" if not exist "%HERE%\venv" goto :notinstall
if not exist "%HERE%\install\install-tinycmdr.cmd" goto :nowrapper

echo This removes the tinycmdr installed in:
echo   %HERE%
echo.
echo The bot, its supervisor and its autostart entry are stopped and removed,
echo and the folder goes with them (config, notes, sessions and .env too).
echo.
set "ANS="
set /p "ANS=Remove it? (y/N) "
if /i "%ANS%"=="y" goto :yes
if /i "%ANS%"=="yes" goto :yes
echo Nothing was removed.
set "RC=0"
goto :end

:yes
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
goto :end

:noapp
echo.
echo ERROR: there is no tinycmdr.py in "%HERE%".
echo This door removes the tinycmdr installed in its own folder; nothing
echo was changed.
goto :end

:notinstall
echo.
echo ERROR: "%HERE%" does not look like a tinycmdr install.
echo An extracted package has no config.json and no venv\ - if this is the
echo package, run this door from the install folder instead (a real install is
echo %USERPROFILE%\tinycmdr unless -InstallDir put it elsewhere). Nothing was
echo changed.
goto :end

:nowrapper
echo.
echo ERROR: "%HERE%\install\install-tinycmdr.cmd" is missing, so the removal
echo cannot run. If you are sure nothing runs from this folder, delete it
echo by hand. Nothing was changed.
goto :end

:nofolder
echo.
echo ERROR: no install folder was given.
goto :end

:end
echo.
echo Press any key to close this window.
pause >nul
exit /b %RC%
