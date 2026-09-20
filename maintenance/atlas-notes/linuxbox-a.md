# Curated atlas notes - the Linux test box

Appended to that host's atlas.md `## notes` section. Generated host/layout facts come
from the host itself; these are the things a probe cannot know. Never shipped in a
package.

- tinycmdr lives at ~/tinycmdr and is a systemd service (`sudo -n systemctl restart tinycmdr`)
- this box publishes example.com: the site source is ~/tech-site, the deploy script is
  ~/bin/deploy-tech-site.sh, and a publish must pass the deploy guards plus blog_lint and the
  humanizer pass before it goes out
- the NAS share is mounted at /mnt/the file share (also how build archives arrive from the
  fleet manager box)
- Ubuntu 22.04 with python3.10; the tinycmdr venv's python3 shadows the system one inside the
  project, so scripts that need the system interpreter must call /usr/bin/python3
- this box also runs Security Onion, so ports and services are busier than a plain host
- a wedged run is diagnosed with `sudo -n py-spy dump --pid <pid>` (ptrace_scope=1 needs sudo)
- the model is not necessarily on this box: the LAN llama.cpp server is at the model box and
  ignores the model name field, so any alias reaches whatever is loaded there
- `shell` runs the OS interpreter shown in `## host`; a shell script written for the other
  platform will not run here
