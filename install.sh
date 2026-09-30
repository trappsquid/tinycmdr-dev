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

say "verifying the download"
# SHA256SUMS covers all eight published files (checked against the v1.0.40 release: the
# three versioned archives, the three stable alias names, install.sh and install.ps1), so
# this checks the ONE file this run fetched rather than trusting the transfer. The README
# documents the same check by hand, but the pipe-to-bash path - the one the README leads
# with - is where a truncated download does the most damage, and it had none at all
# (measured 2026-09-29: neither this script nor install/install-tinycmdr.sh mentioned
# SHA256SUMS, sha256 or shasum anywhere).
sums="$tmp/SHA256SUMS"
if curl -fL --retry 3 --connect-timeout 15 -o "$sums" "$BASE/SHA256SUMS"; then
    # Pick this asset's own line: "$NF == a" covers a plain name, "*" a binary-mode one.
    awk -v a="$asset" '$NF == a || $NF == "*" a' "$sums" > "$tmp/one.sum"
    [ -s "$tmp/one.sum" ] || die "SHA256SUMS does not cover $asset - the release is broken, and nothing was unpacked"
    if command -v sha256sum >/dev/null 2>&1; then
        ( cd "$tmp" && sha256sum -c one.sum ) \
            || die "the download does not match SHA256SUMS - a corrupted or truncated transfer. Nothing was unpacked."
    elif command -v shasum >/dev/null 2>&1; then
        ( cd "$tmp" && shasum -a 256 -c one.sum ) \
            || die "the download does not match SHA256SUMS - a corrupted or truncated transfer. Nothing was unpacked."
    else
        printf '    (no sha256sum or shasum on this host: the download is unchecked)\n'
    fi
    printf '    the download matches SHA256SUMS. Releases are unsigned, so this catches a\n'
    printf '    corrupted or truncated transfer, not a release that was replaced.\n'
elif [ "${TINYCMDR_NO_SUMS:-}" = "1" ]; then
    printf '    (SHA256SUMS could not be fetched, and TINYCMDR_NO_SUMS=1 says carry on)\n'
else
    die "could not fetch $BASE/SHA256SUMS, so this download cannot be checked. Re-run it, or set TINYCMDR_NO_SUMS=1 to skip the check deliberately."
fi

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
# rather than testing whether the node is readable, and do it inside a group whose
# stderr is already /dev/null: in `exec 3</dev/tty 2>/dev/null` the failed open is
# reported to the CURRENT stderr, because redirections are applied left to right - every
# headless run printed "bash: line 55: /dev/tty: Device not configured" (I16).
set +e   # the installer's own exit code is mine to report, not to die on
if [ ! -t 0 ] && { exec 3</dev/tty; } 2>/dev/null; then
    bash "$installer" "$@" <&3
    exec 3<&-
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
# The PLATFORM's installer, not this script's name: the footer used to print the Linux
# one on macOS too, and on a Mac that path exists (it is in the package) but dies at its
# first `getent` under `set -e`+`pipefail` - exit 127, no output, "so later:" followed by
# nothing that works (I6). $installer is the same name this run handed over to.
printf '    uninstaller (~/tinycmdr by default), so later:\n'
printf '      bash ~/tinycmdr/%s --verify-only\n' "$installer"
printf '      bash ~/tinycmdr/%s --uninstall\n' "$installer"
case "$installer" in
    *macos*)
        printf '      (or double-click ~/tinycmdr/UNINSTALL-MACOS.command)\n' ;;
    *)
        printf '      (a system install needs it as root: sudo bash ~/tinycmdr/%s --uninstall)\n' "$installer" ;;
esac
