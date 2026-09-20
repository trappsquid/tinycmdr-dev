# Machine atlas - the machine this agent is running on

Shipped with this build as a starting map, not a live inventory: nothing regenerates it.
Edit it to match the machine in front of you and the edit stays. Add what you learn under
## notes. All three sections ride the first turn of a run, and again after a failure that
looks like a wrong path.

## host
- OS family: Linux. The shell tool runs `bash -c`, not a login shell.
- sudo only where it is passwordless (`sudo -n true` answers without a prompt). Say what
  needs root rather than working around it.
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
- services systemctl status X --no-pager | unit logs journalctl -u X -n 200 --no-pager |
  this boot journalctl -b -p err | processes ps auxf, pgrep -af | listening sockets
  ss -lntup | disks lsblk -f, df -h | packages dpkg -l or rpm -qa | network ip -4 addr,
  ip route | ports of a process ss -lntup
- prefer the mechanism already on the machine: its own package repository or media handed
  to you, its own service manager, the software already installed
- a command that prints nothing for a long time is usually waiting on a prompt, a pager or
  a lock: use --no-pager / --batch style flags, bound it, then read what it did print
- a command that works in a terminal may need its full path or an explicit environment
  here, because this is not a login shell
- the agent usually runs as a normal user: read what it can read, and report what needs
  root rather than escalating on its own
