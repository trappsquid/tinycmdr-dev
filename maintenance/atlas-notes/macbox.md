# Curated atlas notes - the macOS box

Appended to that host's atlas.md `## notes` section. Generated host/layout facts come
from the host itself; these are the things a probe cannot know. Never shipped in a
package.

- tinycmdr lives at ~/tinycmdr and runs as a per-user launchd agent
  (`launchctl kickstart -k gui/$(id -u)/com.trapp.tinycmdr`), no sudo anywhere
- the web UI port is 8788 because 8787 belongs to another service on this machine
- the NAS share is NOT reachable from an ssh session here (macOS scopes the mount to the
  GUI session): copy files in with scp/sftp instead of reading /Volumes/the file share
- this is a laptop, so it leaves the LAN: the model endpoint defaults to the cloud unless the
  LAN endpoint is chosen explicitly
- python is the Homebrew 3.12 build; 3.13+ is refused by the installer on purpose
- the model is not necessarily on this box: the LAN llama.cpp server is at the model box and
  ignores the model name field, so any alias reaches whatever is loaded there
- `shell` runs the OS interpreter shown in `## host`; a shell script written for the other
  platform will not run here
