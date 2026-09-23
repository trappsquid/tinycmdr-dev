#!/bin/sh
# tinycmdr uninstaller (macOS).
# Removes the launchd job, the PATH wrapper and the install folder - through the
# installer's own --uninstall path, so the removal logic has ONE home. Shipped in
# every package and copied into the install with the installer, so day-two removal
# never needs the original package.
#
#   sudo sh install/uninstall-tinycmdr-macos.sh [--install-dir ~/tinycmdr] [...]
exec bash "$(dirname "$0")/install-tinycmdr-macos.sh" --uninstall "$@"
