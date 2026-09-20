# Curated atlas notes - the manager box

Appended to that host's atlas.md `## notes` section. Generated host/layout facts come
from the host itself; these are the things a probe cannot know. Never shipped in a
package.

- this box is the FLEET MANAGER and the chat host, not the model host
- Docker Desktop runs the example stack; compose files live under F:\Docker (mattermost,
  npm, open-webui, stirling-pdf, search-agg). `docker` is NOT on PATH in a background shell:
  prefix "/c/Program Files/Docker/Docker/resources/bin"
- chat is Mattermost (Mostlymatter fork) at example.com, container published on
  127.0.0.1:8065 only, fronted by nginx-proxy-manager and Cloudflare
- the NAS share is \\a LAN address\the file share (Z: in an interactive session). An S4U logon has no
  network credentials, so from a bot shell use `bin\nas-connect.cmd <command>`, which reads
  %USERPROFILE%\.nas-cred and connects for the length of that command
- `bash` on this box is a WSL relay that cannot exec a script; write PowerShell for anything
  the shell tool must run as a script
- another service on this box also wants 8787, so check the `web ui` line above against the live config before assuming that port is free
- scratch space: %TEMP%\tinycmdr-runs and %TEMP%\tinycmdr-test
- example.com is published from the Security Onion box, not from here; do not edit a
  local copy of the site and expect it to appear
- the the manager box bot runs elevated (Task Scheduler, highest privileges), so a non-elevated shell
  cannot kill it - restart it through itself (`/restart`)
- the model is not necessarily on this box: the LAN llama.cpp server is at the model box and
  ignores the model name field, so any alias reaches whatever is loaded there
- `shell` runs the OS interpreter shown in `## host`; a shell script written for the other
  platform will not run here
