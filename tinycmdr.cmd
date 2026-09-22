@echo off
REM tinycmdr - the management door on Windows. On PATH:
REM     tinycmdr status | doctor | model | logs | restart | token | help
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
"%PY%" "%HERE%tinycmdr.py" %*
exit /b %ERRORLEVEL%
