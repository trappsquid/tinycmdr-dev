#!/bin/bash
# restart-tinycmdr.sh - the POSIX twin of maintenance/restart-tinycmdr.ps1.
#
# Under systemd the unit is the supervisor: never poke the process, ask systemd.
# Run it as root (systemctl restart needs it), or with sudo.
#
#   sudo bash restart-tinycmdr.sh
set -euo pipefail

SERVICE_NAME="${TINYCMDR_SERVICE:-tinycmdr}"
INSTALL_DIR="${TINYCMDR_DIR:-/home/${SUDO_USER:-$(id -un)}/tinycmdr}"
LOG="${TINYCMDR_RESTART_LOG:-/tmp/tinycmdr-restart.log}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

log "=== restart run begin (user $(id -un)) ==="

# list-unit-files exits 0 whether or not it matched, so the old guard was dead
# code: the operator got a systemctl error two lines later, AFTER the pkill had
# Already taken the bot down. LoadState is a string, not an
# exit code - only "loaded" means this host has the unit.
_unit_state="$(systemctl show -p LoadState --value "$SERVICE_NAME.service" 2>/dev/null || true)"
if [ "$_unit_state" != "loaded" ]; then
    log "no $SERVICE_NAME.service on this host (LoadState=${_unit_state:-unknown}) - is tinycmdr installed here?"
    log "=== restart run end (nothing to do) ==="
    exit 1
fi

if [ "$(id -u)" != 0 ]; then
    log "need root: sudo bash $0"
    exit 1
fi

# The bot's own /restart spawns a detached copy, which systemd then reaps with
# the unit's cgroup; restarting through systemd is the clean path. The pkill is
# scoped to THIS install and anchored with [.]: the old bare pattern was an
# unanchored regex that also hit a second install's bot and anything merely
# Naming the file, like `tail -f tinycmdr.py.log`.
pkill -f "$INSTALL_DIR/tinycmdr[.]py" 2>/dev/null || true
sleep 1
systemctl restart "$SERVICE_NAME"

for _ in $(seq 1 20); do
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log "tinycmdr is active again ($(systemctl show -p MainPID --value "$SERVICE_NAME"))"
        log "=== restart run end ==="
        exit 0
    fi
done

log "tinycmdr did NOT come back - journal follows"
journalctl -u "$SERVICE_NAME" -n 40 --no-pager >>"$LOG" 2>&1 || true
log "=== restart run end (failed) ==="
exit 1
