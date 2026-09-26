#!/bin/bash
# Double-click me to remove tinycmdr from this Mac.
#
# This asks for a password only when it has to. The PATH wrapper at /usr/local/bin/tinycmdr
# is root-owned whenever the install was run with sudo, and no normal user can unlink a file
# there - so a system install needs sudo once, while a user-mode install needs no password at
# all. Asking unconditionally made a harmless removal look dangerous, and asking on the way
# is why an uninstall could stop half-way through.
set -u
cd "$(dirname "$0")" || exit 1

SUDO=""
if [ -e /usr/local/bin/tinycmdr ] && [ ! -w /usr/local/bin ]; then
    SUDO="sudo"
fi

# --install-dir "$(pwd)" is load-bearing: without it the uninstaller falls back to its
# default (~/tinycmdr) and removes NOTHING when the install lives anywhere else - measured
# 2026-09-26, where a door in an --install-dir folder reported success and left the folder
# standing. The door removes the folder it sits in.
if [ -z "$SUDO" ]; then
    bash install/uninstall-tinycmdr-macos.sh --install-dir "$(pwd)" "$@"
else
    echo "A tinycmdr launcher in /usr/local/bin is owned by root, so this needs sudo once."
    $SUDO bash install/uninstall-tinycmdr-macos.sh --install-dir "$(pwd)" "$@"
fi
status=$?
echo
if [ "$status" -ne 0 ]; then
    echo "The uninstaller exited $status - the lines above say why."
fi
read -n 1 -s -r -p "Press any key to close this window..."
echo
