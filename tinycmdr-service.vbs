' Launches the tinycmdr supervisor hidden (no console window) and WAITS for it.
' Registered as the scheduled task "tinycmdr".
'
' The wait is deliberate and load-bearing:
'   * while the supervisor runs, Task Scheduler shows the task as Running (before
'     this it exited immediately and always looked "Ready" even while the bot was
'     dead);
'   * when the supervisor exits, its exit code is propagated, so the task's
'     RestartOnFailure policy (3 attempts / 2 min) actually fires.
' The supervisor itself keeps the bot alive; this is only the outer safety net.
'
' Run by hand:  wscript //B //Nologo "C:/Users/<user>\tinycmdr\tinycmdr-service.vbs"
Option Explicit
Dim sh, rc
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:/Users/<user>\tinycmdr"
rc = sh.Run("""C:/Users/<user>\AppData\Local\Programs\Python\Python312\pythonw.exe"" ""C:/Users/<user>\tinycmdr\tinycmdr-supervise.py""", 0, True)
WScript.Quit rc
