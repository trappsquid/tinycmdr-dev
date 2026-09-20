# Machine atlas - the machine this agent is running on

Shipped with this build as a starting map, not a live inventory: nothing regenerates it.
Edit it to match the machine in front of you and the edit stays. Add what you learn under
## notes. All three sections ride the first turn of a run, and again after a failure that
looks like a wrong path.

## host
- OS family: Windows. The shell tool runs PowerShell (`powershell -NoProfile -Command`)
  unless agent.shell says "cmd" in config.json.
- Administrator work (some event logs, root WMI classes, service and driver changes) fails
  with "Access is denied" when the console is not elevated. Name the command that needs
  elevation instead of working around it.
- Nothing is fetched from outside: no update channel, no package repository, no installer
  download, no web search. The only network destination is llm.base_url.

## layout
- tinycmdr.py  the agent, one file, standard library only
- config.example.json  the template; copy or rename it to config.json and fill in llm
- README.txt  how to run it, and everything it keeps
- atlas.md  this file
- skills/  runbooks it reads on demand (a folder containing a SKILL.md)
- tools/  custom tools; it writes its own here and hot-loads them
- sessions/ notes.md tasks.json tinycmdr.log  appear once there is work to keep

## notes
- services Get-Service, Get-Service -Name X -RequiredServices | event logs
  Get-WinEvent -LogName System -MaxEvents 50 | processes Get-Process,
  Get-CimInstance Win32_Process | listening sockets Get-NetTCPConnection -State Listen |
  disks Get-Volume, Get-Disk | devices and drivers pnputil /enum-drivers, Get-PnpDevice |
  file contents Select-String -Path X -Pattern Y (never cat a binary)
- prefer the mechanism already on the machine: DISM with a local image, pnputil with an
  .inf you were handed, the environment's own update server, the software already installed
- a command that prints nothing for a long time is usually waiting on a prompt or a lock,
  not slow work: bound it with a timeout, then read what it did print
- quote any path containing a space; in cmd use double quotes, in PowerShell single quotes
  keep a literal path literal
- the console is switched to UTF-8 at startup, but a redirected pipe may not render the
  glyphs: keep what you print plain
