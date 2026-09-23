#!/bin/sh
# tinycmdr uninstaller (Linux).
# Removes the systemd unit, the PATH wrapper, the passwordless-sudo grant and the
# install folder - through the installer's own --uninstall path, so the removal
# logic has ONE home. Shipped in every package and copied into the install with
# the installer, so day-two removal never needs the original package.
#
#   sudo sh install/uninstall-tinycmdr.sh [--install-dir ~/tinycmdr] [...]
exec bash "$(dirname "$0")/install-tinycmdr.sh" --uninstall "$@"
