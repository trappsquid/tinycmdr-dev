#!/bin/bash
# Double-click me to remove tinycmdr from this Mac.
#
# Why this asks for a password: the PATH wrapper it removes lives at
# /usr/local/bin/tinycmdr, which is root-owned whenever the install was run with sudo, and
# a normal user cannot unlink a file there. Asking once, up front, beats the uninstaller
# stopping part-way through the removal. If your install was user-mode there is no wrapper
# and sudo is simply not needed - it is harmless either way.
set -u
cd "$(dirname "$0")" || exit 1
if [ "$(id -u)" = "0" ]; then
    bash install/uninstall-tinycmdr-macos.sh "$@"
else
    sudo bash install/uninstall-tinycmdr-macos.sh "$@"
fi
status=$?
echo
if [ "$status" -ne 0 ]; then
    echo "The uninstaller exited $status - the lines above say why."
fi
read -n 1 -s -r -p "Press any key to close this window..."
echo
