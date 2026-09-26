#!/usr/bin/env bash
#
# tinycmdr, in one line:
#
#   curl -fsSL https://github.com/trappsquid/tinycmdr/releases/latest/download/install.sh | bash
#
# Add the installer's own switches after `bash -s --`, for example:
#
#   curl -fsSL .../install.sh | bash -s -- --mode user
#
# This is the thin door, not a second installer: it fetches the newest archive,
# unpacks it and hands over to the installer inside it, so every rule about the
# install itself stays in install/install-tinycmdr*.sh.
set -euo pipefail

BASE="${TINYCMDR_URL:-https://github.com/trappsquid/tinycmdr/releases/latest/download}"

say() { printf '\n=== %s\n' "$*"; }
die() { printf '\n*** %s\n' "$*" >&2; exit 1; }

case "$(uname -s)" in
    Linux)  asset="tinycmdr-linux.tar.gz"; installer="install/install-tinycmdr.sh" ;;
    Darwin) asset="tinycmdr-macos.zip";    installer="install/install-tinycmdr-macos.sh" ;;
    *)      die "this door covers Linux and macOS. On Windows, download tinycmdr-win.zip from the release and double-click INSTALL-WINDOWS.cmd." ;;
esac

command -v curl >/dev/null 2>&1 || die "curl is required"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/tinycmdr.unpack.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

say "tinycmdr: fetching $asset"
curl -fL --retry 3 --connect-timeout 15 -o "$tmp/$asset" "$BASE/$asset" \
    || die "could not download $BASE/$asset"

say "unpacking"
case "$asset" in
    *.tar.gz) tar -xzf "$tmp/$asset" -C "$tmp" ;;
    *.zip)    unzip -q "$tmp/$asset" -d "$tmp" ;;
esac
src="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d -name 'tinycmdr-*' | head -1)"
[ -n "$src" ] || die "the archive did not contain a tinycmdr-* folder"
[ -f "$src/tinycmdr.py" ] || die "$src has no tinycmdr.py - the download looks wrong"
[ -f "$src/$installer" ] || die "$src has no $installer - the download looks wrong"

say "handing over to $installer"
cd "$src"
# Piping into bash leaves stdin as the script itself, so the installer would see no
# terminal and skip its questions - the install mode on Linux, and on macOS the
# Mattermost server, the bot token, your user id, the model endpoint and its key.
# Borrow the terminal back when there is one - note that /dev/tty can exist and still
# refuse to open (a command run over ssh has no controlling terminal), so OPEN it
# rather than testing whether the node is readable.
set +e   # the installer's own exit code is mine to report, not to die on
if [ ! -t 0 ] && exec 3</dev/tty 2>/dev/null; then
    bash "$installer" "$@" <&3
elif [ ! -t 0 ]; then
    printf '    (no terminal here to ask questions on: the installer takes its defaults)\n'
    bash "$installer" "$@"
else
    bash "$installer" "$@"
fi
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
    printf '\n*** the installer exited %d - the unpacked copy is left in %s\n' "$rc" "$src" >&2
    trap - EXIT
    exit "$rc"
fi
# The installer prints the paths of the copy it ran from. That copy is temporary, so
# name the durable one: what it installs keeps its own installer beside the build.
rm -rf "$tmp"; trap - EXIT
printf '\n    the unpacked folder was temporary. The installed copy carries its own installer and\n'
printf '    uninstaller (~/tinycmdr by default), so later:\n'
printf '      bash ~/tinycmdr/install/install-tinycmdr.sh --verify-only\n'
printf '      bash ~/tinycmdr/install/install-tinycmdr.sh --uninstall\n'
