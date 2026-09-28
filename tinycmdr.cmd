@echo off
REM tinycmdr - the door on Windows. On PATH:
REM     tinycmdr                     a session in this folder (no arguments)
REM     tinycmdr status | doctor | model | logs | restart | token | help | clean | ...
REM     tinycmdr --cli               the same session, spelled out
REM     tinycmdr --once "<task>" | --telegram          a bot lane in the foreground
REM A shim, not a second build: it runs tinycmdr.py from THIS folder, so the install
REM stays one folder with one config.json and one .env.
setlocal
set "HERE=%~dp0"
set "PY="
if exist "%HERE%venv\Scripts\python.exe" set "PY=%HERE%venv\Scripts\python.exe"
REM PATH is a trap on Windows: %LOCALAPPDATA%\Microsoft\WindowsApps sits early on it and
REM holds the Microsoft Store's python.exe STUB, which opens the Store instead of running
REM anything. The installer's own Python discovery excludes that path (audit W8); this shim
REM did not, so after a failed venv build or a deleted venv\ `tinycmdr status` opened the
REM Store. The venv is preferred above; both PATH fallbacks below skip the stub.
if not defined PY call :findpy python.exe
if not defined PY call :findpy py.exe
if not defined PY (
    echo tinycmdr: no Python found - install Python 3.10-3.12, or re-run the installer. 1>&2
    echo           (the Microsoft Store stub on PATH is not a usable interpreter.) 1>&2
    exit /b 127
)
REM No arguments means a human at a keyboard, so give them a session. The bot keeps
REM starting the way it always has: the scheduled task runs tinycmdr.py itself.
if "%~1"=="" (
    "%PY%" "%HERE%tinycmdr.py" --cli
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
