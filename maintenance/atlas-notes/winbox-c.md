# Curated atlas notes - the other Windows box

Appended to that host's atlas.md `## notes` section. Generated host/layout facts come
from the host itself; these are the things a probe cannot know. Never shipped in a
package.

- tinycmdr lives at C:\tinycmdr and is supervised by the scheduled task "tinycmdr"
- it carries custom tools in tools/ (media download helpers); prefer them over raw shell for
  those domains
- restart it with maintenance\tinycmdr-24x7.ps1, and do NOT trust that script's readiness
  probe: it has reported failure while the bot was already connected. Read tinycmdr.log's
  startup line and /api/health instead
- the model is not necessarily on this box: the LAN llama.cpp server is at the model box and
  ignores the model name field, so any alias reaches whatever is loaded there
- `shell` runs the OS interpreter shown in `## host`; a shell script written for the other
  platform will not run here
