#!/bin/bash
# Manual launch of tinycmdr, in the foreground, from its own folder.
#
# systemd is the normal path on Linux:
#   sudo systemctl restart tinycmdr
# This script is the "just run it and watch" path (Ctrl-C to stop), the twin of
# launch_tinycmdr.bat on Windows. Do not leave it running under a session that
# logs out - the lock is per install folder, so a second copy refuses to start.
cd "$(dirname "$0")" || exit 1
exec ./venv/bin/python tinycmdr.py "$@"
