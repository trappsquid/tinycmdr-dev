# Curated atlas notes - the LAN model box

Appended to that host's atlas.md `## notes` section. Generated host/layout facts come
from the host itself; these are the things a probe cannot know. Never shipped in a
package.

- tinycmdr lives at ~/tinycmdr and is a systemd service (sudo is NOPASSWD here)
- THIS BOX ALSO RUNS THE LAN MODEL SERVER: llama-server on port 8081, one model in three
  slots, alias `main`. Other consumers (another agent and a web UI) share it, so restarting
  llama-server or changing what is loaded affects them immediately - do not do it casually
- the local endpoint is http://127.0.0.1:8081/v1 on this box
- `/stop` style restarts of tinycmdr are fine; a restart of the model server is not
- the model is not necessarily on this box: the LAN llama.cpp server is at the model box and
  ignores the model name field, so any alias reaches whatever is loaded there
- `shell` runs the OS interpreter shown in `## host`; a shell script written for the other
  platform will not run here
