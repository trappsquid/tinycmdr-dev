#!/bin/bash
# restart-tinycmdr.sh - the POSIX twin of maintenance/restart-tinycmdr.ps1.
#
# Under systemd the unit is the supervisor: never poke the process, ask systemd.
# Run it as root (systemctl restart needs it), or with sudo.
#
#   sudo bash restart-tinycmdr.sh
set -euo pipefail

SERVICE_NAME="${tinycmdr_SERVICE:-tinycmdr}"
INSTALL_DIR="${tinycmdr_DIR:-/home/${SUDO_USER:-$(id -un)}/tinycmdr}"
LOG="${tinycmdr_RESTART_LOG:-/tmp/tinycmdr-restart.log}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

log "=== restart run begin (user $(id -un)) ==="

if ! systemctl list-unit-files "$SERVICE_NAME.service" >/dev/null 2>&1; then
    log "no $SERVICE_NAME.service on this host - is tinycmdr installed here?"
    log "=== restart run end (nothing to do) ==="
    exit 1
fi

if [ "$(id -u)" != 0 ]; then
    log "need root: sudo bash $0"
    exit 1
fi

# The bot's own /restart spawns a detached copy, which systemd then reaps with
# the unit's cgroup; restarting through systemd is the clean path.
pkill -f "tinycmdr.py" 2>/dev/null || true
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
