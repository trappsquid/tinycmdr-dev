@echo off
rem tinycmdr launcher — double-click to start, or drop a shortcut to this
rem file in shell:startup (Win+R -> shell:startup) to auto-start at logon.
rem
rem Uses Python 3.12 by full path on purpose: plain `python` on this machine
rem resolves to the Hermes agent venv (C:/Users/<user>\AppData\Local\hermes\hermes-agent\venv)
rem which does NOT have mmpy_bot installed, so the bot would die at startup.
rem Deps were installed into 3.12 via:  py -3.12 -m pip install requests mmpy_bot croniter
rem
rem pythonw = no console window (runs quietly in background)
rem python  = keeps a console window open so you can watch the log live

cd /d "C:/Users/<user>\tinycmdr"
start "" "C:/Users/<user>\AppData\Local\Programs\Python\Python312\python.exe" tinycmdr.py
