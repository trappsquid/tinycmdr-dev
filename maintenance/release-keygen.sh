#!/usr/bin/env bash
# release-keygen.sh - create the operator's release-signing key, outside the tree.
#
#   bash maintenance/release-keygen.sh
#
# The private key is a CREDENTIAL: it signs the SHA256SUMS of every release, so a
# compromised GitHub account cannot replace a download. Keep it off the installs and
# back it up somewhere you control; this script writes it under ~/.dsh/tinycmdr/ at
# mode 600. The public half is printed; paste it into each host's config with
#   tinycmdr config set agent.release_pubkey "ssh-ed25519 AAAA..."
# and a release without a valid signature is refused before it is unpacked.
set -euo pipefail
cd "$(dirname "$0")/.."

KEY="${TINYCMDR_RELEASE_KEY:-$HOME/.dsh/tinycmdr/release-signing}"
if [ -e "$KEY" ]; then
    echo "refusing: $KEY already exists - move it aside or set TINYCMDR_RELEASE_KEY" >&2
    exit 2
fi
command -v ssh-keygen >/dev/null 2>&1 || {
    echo "ssh-keygen is not on PATH; it is the signer and the verifier" >&2
    exit 2
}
mkdir -p "$(dirname "$KEY")"
chmod 700 "$(dirname "$KEY")" 2>/dev/null || true
ssh-keygen -q -t ed25519 -f "$KEY" -N "" -C "tinycmdr release signing"
chmod 600 "$KEY"
chmod 644 "$KEY.pub"

echo
echo "private key: $KEY (mode 600 - back it up, never commit it)"
echo "public key:"
cat "$KEY.pub"
echo
echo "activate on a host that should verify releases:"
printf '  tinycmdr config set agent.release_pubkey "%s"\n' "$(cat "$KEY.pub")"
echo
echo "cut a signed release with the normal release.sh; it signs SHA256SUMS when this"
echo "key exists (or TINYCMDR_RELEASE_KEY names another file)."
