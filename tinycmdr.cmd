@echo off
REM tinycmdr - the door on Windows. On PATH:
REM     tinycmdr                     a session in this folder (no arguments)
REM     tinycmdr status | doctor | model | logs | restart | token | help | clean | ...
REM     tinycmdr --cli               the same session, spelled out
REM     tinycmdr --web | --once "<task>" | --telegram    a bot lane in the foreground
REM A shim, not a second build: it runs tinycmdr.py from THIS folder, so the install
REM stays one folder with one config.json and one .env.
setlocal
set "HERE=%~dp0"
set "PY="
if exist "%HERE%venv\Scripts\python.exe" set "PY=%HERE%venv\Scripts\python.exe"
if not defined PY for %%I in (python.exe) do if exist "%%~$PATH:I" set "PY=%%~$PATH:I"
if not defined PY for %%I in (py.exe) do if exist "%%~$PATH:I" set "PY=%%~$PATH:I"
if not defined PY (
    echo tinycmdr: no python on PATH - install Python 3.9+ or re-run the installer. 1>&2
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
