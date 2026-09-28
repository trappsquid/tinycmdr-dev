#!/usr/bin/env bash
#
# restart-tinycmdr-macos.sh - control the tinycmdr launchd agent on a Mac.
#
#   bash restart-tinycmdr-macos.sh            restart (kill + let launchd start it)
#   bash restart-tinycmdr-macos.sh status     is the agent loaded, and is it answering
#   bash restart-tinycmdr-macos.sh start
#   bash restart-tinycmdr-macos.sh stop
#   bash restart-tinycmdr-macos.sh logs       tail the launchd error log
#
# Why not just use /restart in Mattermost: the bot's own /restart spawns a detached
# replacement and exits 0, and the agent is set to restart only on a NON-zero exit, so
# the two do not fight - but a hung process (a thread stuck in a syscall) never reaches
# that path at all, and this is how you clear it.
#
set -euo pipefail

INSTALL_DIR="${TINYCMDR_DIR:-$HOME/tinycmdr}"
LABEL="${TINYCMDR_LABEL:-com.tinycmdr.agent}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"
TARGET="gui/$UID_NUM/$LABEL"
ACTION="${1:-restart}"

say()  { printf '\n=== %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n*** %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "launchd is macOS-only (this is $(uname -s))"

# The truth surface that survived the built-in web UI's removal: `tinycmdr health`, one line and
# an exit code, run under this install's own interpreter. This used to probe the served page
# on whatever port config.json named.
VPY="$INSTALL_DIR/venv/bin/python"

health() {
    if [ ! -x "$VPY" ] || [ ! -f "$INSTALL_DIR/tinycmdr.py" ]; then
        info "no venv or agent under $INSTALL_DIR - run install/install-tinycmdr-macos.sh first"
        return 1
    fi
    local out rc=0
    out="$(cd "$INSTALL_DIR" && "$VPY" tinycmdr.py health 2>&1)" || rc=$?
    printf '    health (exit %s): %s\n' "$rc" "$out"
    return "$rc"
}

case "$ACTION" in
    status)
        say "status: $LABEL"
        if launchctl print "$TARGET" >/dev/null 2>&1; then
            info "loaded"
            launchctl print "$TARGET" | grep -E '^\s+(state|pid|last exit code|runs) =' || true
        else
            info "NOT loaded (agent file present: $([ -f "$PLIST" ] && echo yes || echo no))"
        fi
        health || true
        ;;
    start)
        [ -f "$PLIST" ] || die "no agent at $PLIST - run install/install-tinycmdr-macos.sh first"
        launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"
        say "started $LABEL"
        ;;
    stop)
        launchctl bootout "$TARGET" 2>/dev/null || launchctl unload -w "$PLIST" 2>/dev/null || true
        say "stopped $LABEL"
        ;;
    restart)
        if [ "$(uname -s)" = "Darwin" ] && [ -f "$PLIST" ]; then
            say "restarting $LABEL"
            launchctl kickstart -k "$TARGET" 2>/dev/null \
                || { launchctl bootout "$TARGET" 2>/dev/null || true; sleep 1;
                     launchctl bootstrap "gui/$UID_NUM" "$PLIST"; }
            info "asked launchd to restart it"
            sleep 4
            health || info "not up yet - check $INSTALL_DIR/logs/launchd.err.log"
        else
            die "no agent at $PLIST - run install/install-tinycmdr-macos.sh first"
        fi
        ;;
    logs)
        tail -n 60 "$INSTALL_DIR/logs/launchd.err.log" 2>/dev/null \
            || tail -n 60 "$INSTALL_DIR/tinycmdr.log"
        ;;
    *) die "usage: restart-tinycmdr-macos.sh [restart|status|start|stop|logs]" ;;
esac
