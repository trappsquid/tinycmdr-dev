#!/bin/bash
# Double-click me. macOS runs a .command in Terminal; a .sh opens in TextEdit, which is
# where a reader who is not a terminal user gets stuck. The real logic lives in
# install/install-tinycmdr-macos.sh so there is ONE copy of it - this file only makes it
# reachable with a double-click.
set -u
cd "$(dirname "$0")" || exit 1
bash install/install-tinycmdr-macos.sh "$@"
status=$?
echo
if [ "$status" -ne 0 ]; then
    echo "The installer exited $status - the lines above say why."
fi
read -n 1 -s -r -p "Press any key to close this window..."
echo
