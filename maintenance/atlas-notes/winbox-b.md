# Curated atlas notes - the Windows test box

Appended to that host's atlas.md `## notes` section. Generated host/layout facts come
from the host itself; these are the things a probe cannot know. Never shipped in a
package.

- tinycmdr lives at C:\tinycmdr and runs detached (pythonw.exe), with NO supervisor: the
  scheduled task shows Ready while the bot runs
- restart it with `schtasks /Run /TN tinycmdr`, but STOP the old process first (it holds
  tinycmdr.lock and will make the replacement abort)
- its maintenance\restart-tinycmdr.ps1 is broken (its $log points at a path that does not
  exist); do not use it
- the web UI port is opened on the LAN by a firewall rule; a new port needs a matching rule
  or the bind times out silently
- 445 is open for the admin share, which is how another box pushes files here
- the model is not necessarily on this box: the LAN llama.cpp server is at the model box and
  ignores the model name field, so any alias reaches whatever is loaded there
- `shell` runs the OS interpreter shown in `## host`; a shell script written for the other
  platform will not run here
