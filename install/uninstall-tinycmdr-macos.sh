#!/bin/sh
# tinycmdr uninstaller (macOS).
# Removes the launchd job, the PATH wrapper and the install folder - through the
# installer's own --uninstall path, so the removal logic has ONE home. Shipped in
# every package and copied into the install with the installer, so day-two removal
# never needs the original package.
#
#   sudo sh install/uninstall-tinycmdr-macos.sh [--install-dir ~/tinycmdr] [...]
#
# Works under sudo: the installer resolves the INVOKING user (SUDO_USER) instead of
# $HOME, which sudo resets to /var/root - so the documented sudo line no longer looks in
# the wrong home, finds nothing and reports "done." It also reads the launchd label out
# of the install folder, so an install made with --label <name> needs no --label here.
exec bash "$(dirname "$0")/install-tinycmdr-macos.sh" --uninstall "$@"
