@echo off
REM tinycmdr - the door on Windows. On PATH:
REM     tinycmdr                     the PAGE (no arguments; opens in your browser), and
REM                                  the chat lanes when this box has any
REM     tinycmdr cli                 the console alone: the session, no page
REM     tinycmdr --app               the full-screen session alone, no page
REM     tinycmdr status | doctor | model | logs | restart | token | help | clean | ...
REM     tinycmdr --once "<task>" | --telegram          a bot lane in the foreground
REM A shim, not a second build: it runs tinycmdr.py from THIS folder, so the install
REM stays one folder with one config.json and one .env.
setlocal
set "HERE=%~dp0"
set "PY="
if exist "%HERE%venv\Scripts\python.exe" set "PY=%HERE%venv\Scripts\python.exe"
REM PATH is a trap on Windows: %LOCALAPPDATA%\Microsoft\WindowsApps sits early on it and
REM holds the Microsoft Store's python.exe STUB, which opens the Store instead of running
REM anything. The installer's own Python discovery excludes that path; this shim
REM did not, so after a failed venv build or a deleted venv\ `tinycmdr status` opened the
REM Store. The venv is preferred above; both PATH fallbacks below skip the stub.
if not defined PY call :findpy python.exe
if not defined PY call :findpy py.exe
if not defined PY (
    echo tinycmdr: no Python found - install Python 3.10-3.12, or re-run the installer. 1>&2
    REM Brackets, not parentheses: an unescaped close-paren in a line inside an if-block
    REM is read as the END of the block, so the line after it ran unconditionally and
    REM this shim exited 127 without printing anything. Measured on Windows 2026-09-29.
    echo           [the Microsoft Store stub on PATH is not a usable interpreter.] 1>&2
    exit /b 127
)
REM THE RULE: every user, on every released version, types `tinycmdr update` and it works.
REM An old install's own updater may predate the release package, so when the local code
REM cannot do the job this shim fetches the published updater and lets IT do the whole
REM thing (the probe is a marker in tinycmdr.py, not a version compare).
if /i "%~1"=="update" (
    REM The CAPABILITY marker, not a verb name: 1.0.44 HAS an update verb - git pull
    REM based, which dead-ends on the dirty checkout its installer leaves - and no
    REM releases/latest/download anywhere - see the unix shim for the measurement.
    findstr /c:"releases/latest/download" "%HERE%tinycmdr.py" >nul 2>&1
    if errorlevel 1 (
        echo tinycmdr: this install predates the packaged updater - using the published one
        powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing 'https://github.com/trappsquid/tinycmdr/releases/latest/download/update.ps1' -OutFile (Join-Path $env:TEMP 'tc-update.ps1'); } catch { Write-Error 'could not fetch the updater'; exit 1 }; & (Join-Path $env:TEMP 'tc-update.ps1') -Dir '%HERE%'; exit $LASTEXITCODE"
        exit /b %ERRORLEVEL%
    )
)

REM No arguments means the PAGE (the build's no-argument start), so nothing is added; a
REM terminal session is the deliberate choice (`tinycmdr cli`). The bot keeps starting the
REM way it always has: the scheduled task runs tinycmdr.py itself.
if "%~1"=="" (
    "%PY%" "%HERE%tinycmdr.py"
) else (
    "%PY%" "%HERE%tinycmdr.py" %*
)
exit /b %ERRORLEVEL%

:findpy
for /f "delims=" %%I in ('where %~1 2^>nul') do (
    echo(%%I| findstr /i /c:"WindowsApps" >nul
    if errorlevel 1 if not defined PY set "PY=%%I"
)
exit /b 0
