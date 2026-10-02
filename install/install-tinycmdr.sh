#!/usr/bin/env bash
#
# install-tinycmdr.sh - install tinycmdr on a Debian/Ubuntu host, run by systemd.
#
# On a fleet host this is the whole job:
#
#   sudo bash install-tinycmdr.sh --token <mattermost-bot-token>
#
# On your own machine, with no switches, it ASKS for what the bot cannot work without
# - the Mattermost server, your user id, and where the model lives: local (this machine
# or your LAN) or cloud (a hosted provider), with the key first for cloud. It proves the
# key with a bearer GET /models and offers the models that come back, then offers a
# Telegram lane and "Add another endpoint?" for as many fallbacks as you want.
# It writes nothing until you answer "Install now?". Every answer has a switch.
#
# It reads install/fleet-defaults.json (Mattermost host, model endpoint, allowed
# user), keeps the bot token out of config.json (it goes to .env), and registers
# a systemd unit.
#
# TWO WAYS TO RUN IT. Say which, or take the default for who you are:
#
#   sudo bash install-tinycmdr.sh   -> SYSTEM: a unit in /etc/systemd/system that
#                                      boots with the machine, passwordless sudo
#                                      for the agent, verb in /usr/local/bin
#   bash install-tinycmdr.sh        -> USER: a unit in ~/.config/systemd/user that
#                                      starts at login, NO root anywhere, the agent
#                                      gets no sudo, verb in ~/.local/bin
#
#   --mode system|user    which one (aliases: --system, --no-root); --yes takes
#                         the default without asking
#   --yes                 ask nothing at all: take the switches and the defaults
#                         (any run with no terminal asks nothing either)
#   --token <t>           Mattermost bot token (TINYCMDR_MM_TOKEN)
#   --token-file <f>      read the token from a file (first token-looking line)
#   --secrets-file <f>    KEY=VALUE lines for .env (search keys, and the bot token:
#                         TINYCMDR_MM_TOKEN from it chooses the chat lane), read BEFORE
#                         the lane decision - the same door the package's own
#                         install/fleet-secrets.env uses
#   --search-egress <b>   true|false: may web search send queries OFF this machine?
#                         default false - both built-in providers are third parties,
#                         and a provider on this LAN (a searxng entry) never needs it
#   --telegram-token <t>  Telegram bot token (TINYCMDR_TG_TOKEN) - the third door, DMs
#                         only; with no Mattermost token the bot runs THIS lane
#   --telegram-ids <i>    numeric Telegram id(s), comma or space separated
#   --allowed-user <id>   Mattermost user id allowed to command the bot
#   --mattermost-url <h>  Mattermost host, no scheme (default: fleet-defaults.json)
#   --install-dir <d>     default /home/<user>/tinycmdr
#   --user <u>            run the service as this user (default: the sudo caller)
#   --bot-name <n>        agent.bot_name (default: this hostname)
#   --model-base-url <u>  llm.base_url (default: install/fleet-defaults.json)
#   --model <m>           llm.model (default: install/fleet-defaults.json)
#   --force               reinstall in place (stops the running service first)
#   --no-start            install and enable, do not start it now
#   --no-deps             do not touch apt (python3-venv must already be present)
#   --no-sudoers         do not grant the service user passwordless sudo
#   --no-path            do not put the `tinycmdr` verb on PATH
#   --verify-only         report on an existing install, change nothing, no root
#   --uninstall           stop, disable, remove the unit and the install dir
#   -h | --help           this text
#
# Everything is transcribed to /tmp/tinycmdr-install.log (mode 600), so a failure always
# leaves the reason on disk. No token is ever echoed into it: the bot token goes to .env
# (mode 600) and the transcript says where to read it - a secret in a transcript is a
# secret in a file nobody thinks to delete.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
SERVICE_NAME="${TINYCMDR_SERVICE:-tinycmdr}"
RUN_USER="${TINYCMDR_USER:-${SUDO_USER:-$(id -un)}}"
# `getent` does not exist on every host, and with `set -e`+`pipefail` the bare pipeline
# above died as exit 127 with NO output, before a single argument was parsed - so this
# installer could not even print --help there, let alone its own "this installer is for
# Debian/Ubuntu hosts" message (audit I6, measured on macOS 2026-09-26: `bash
# install/install-tinycmdr.sh --help` printed nothing and exited 127). Never fatal now:
# try getent, try dscl, then $HOME.
user_home() {   # user_home <name> -> that user's home directory, or empty
    local u="$1" h=""
    h="$(getent passwd "$u" 2>/dev/null | cut -d: -f6)" || true            # Linux
    [ -n "$h" ] || h="$(dscl . -read "/Users/$u" NFSHomeDirectory 2>/dev/null \
        | awk '{print $2}')" || true                                       # macOS
    printf '%s' "$h"
}
USER_HOME="$(user_home "$RUN_USER")"
USER_HOME="${USER_HOME:-${HOME:-/home/$RUN_USER}}"
INSTALL_DIR="${TINYCMDR_DIR:-$USER_HOME/tinycmdr}"
# the default, remembered BEFORE flags parse: the box-level removals in
# --uninstall are scoped against it (2026-09-23: an un-scoped probe cleanup
# removed a running box's whole install - twice over in one day)
DEFAULT_INSTALL_DIR="$INSTALL_DIR"
# UNIT and the PATH wrapper depend on the MODE (system vs user); both are set in
# "how should it run here?" below, once the flags have been read. A system unit
# lives in /etc and needs root to write; a user unit lives in your own home.
UNIT=""
WRAPPER=""
LOG="${TINYCMDR_INSTALL_LOG:-/tmp/tinycmdr-install.log}"
PY="${TINYCMDR_PYTHON:-python3}"

TOKEN=""; TOKEN_FILE=""; BOT_NAME=""; MODEL_BASE_URL=""; MODEL=""; ALLOWED_ARG=""; MM_URL_ARG=""
# What the CALLER asked for, captured before the defaults below fill anything in:
# an update must only change what it was told to change.
MODEL_BASE_GIVEN=""; MODEL_GIVEN=""
TG_TOKEN=""; TG_IDS=""
FORCE=0; NO_START=0; NO_DEPS=0; VERIFY_ONLY=0; UNINSTALL=0; NO_SUDOERS=0
SECRETS_FILE=""          # --secrets-file: KEY=VALUE lines, read BEFORE the lane is chosen
YES=0                     # -y/--yes: ask nothing, take the switches and the defaults
MODEL_KEY=""              # the model endpoint's key, when the reader gives one
FALLBACK_SPECS=""         # extra endpoints, one "url|model|alias|env-name" per line
FB_ENV_LINES=""           # their keys, as KEY=VALUE lines for .env
SEARCH_EGRESS=""          # --search-egress true|false ("" = leave the host's own); an
                          # off-LAN search provider is refused, not called, while false
# What the questions propose when the package says nothing: the usual local
# llama.cpp shape. Any OpenAI-compatible /v1 root works.
DEFAULT_MODEL_BASE="http://127.0.0.1:8081/v1"
DEFAULT_MODEL="main"
# system | user | "" (decide from who you are). TINYCMDR_MODE is the env door, the
# --mode flag the CLI door: a fleet push sets the env one and never prompts.
INSTALL_MODE="${TINYCMDR_MODE:-}"; ASK_MODE=1; DIR_GIVEN=0; RUN_UID=""

# The whole comment header, whatever its length: a fixed range stopped mid-sentence as
# soon as the header grew past it, and every new switch restarted the drift.
usage() {
    sed -n '3,/^set -/p' "${BASH_SOURCE[0]}" | sed '/^set -/d' | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --token)            TOKEN="$2"; shift 2 ;;
        --token-file)       TOKEN_FILE="$2"; shift 2 ;;
        --secrets-file)     SECRETS_FILE="$2"; shift 2 ;;
        --telegram-token)   TG_TOKEN="$2"; shift 2 ;;
        --telegram-ids)     TG_IDS="$2"; shift 2 ;;
        --install-dir)      INSTALL_DIR="$2"; DIR_GIVEN=1; shift 2 ;;
        --user)             RUN_USER="$2"; shift 2 ;;
        --mode)             INSTALL_MODE="$2"; shift 2 ;;
        --system)           INSTALL_MODE=system; shift ;;
        --no-root)          INSTALL_MODE=user; shift ;;
        -y|--yes)           ASK_MODE=0; YES=1; shift ;;
        --search-egress)    SEARCH_EGRESS="$2"; shift 2 ;;
        --bot-name)         BOT_NAME="$2"; shift 2 ;;
        --allowed-user)     ALLOWED_ARG="$2"; shift 2 ;;
        --mattermost-url)   MM_URL_ARG="$2"; shift 2 ;;
        --model-base-url)   MODEL_BASE_URL="$2"; shift 2 ;;
        --model)            MODEL="$2"; shift 2 ;;
        --force)            FORCE=1; shift ;;
        --no-start)         NO_START=1; shift ;;
        --no-path)          NO_PATH=1; shift ;;
        --no-deps)          NO_DEPS=1; shift ;;
        --no-sudoers)       NO_SUDOERS=1; shift ;;
        --verify-only)      VERIFY_ONLY=1; shift ;;
        --uninstall)        UNINSTALL=1; shift ;;
        -h|--help)          usage; exit 0 ;;
        *) echo "install-tinycmdr.sh: unknown switch '$1'" >&2; usage >&2; exit 2 ;;
    esac
done
# Which of these a caller ACTUALLY passed, captured now that the flags are parsed
# (empty means "not given"): an update must only change what it was told to change.
MODEL_BASE_GIVEN="$MODEL_BASE_URL"
MODEL_GIVEN="$MODEL"

say()  { printf '\n=== %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    ! %s\n' "$*" >&2; }
die()  { printf '\n*** %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- logging ---
# Transcribe to a log when we can write one. A log left behind by another user
# (root ran the installer earlier, then someone runs --verify-only) must not turn
# into a "tee: Permission denied" wall, and --verify-only changes nothing anyway.
if [ "$VERIFY_ONLY" = 1 ]; then
    LOG=/dev/null
fi
# The log must be born unreadable by others: it transcribes every line this run prints,
# and the install folder's contents are the host's own business. Create it 0600 BEFORE
# tee opens it (tee -a keeps the mode of an existing file) and tighten one left by an
# older build.
if [ "$LOG" != "/dev/null" ]; then
    if [ ! -e "$LOG" ] && [ -d "$(dirname "$LOG")" ] && [ -w "$(dirname "$LOG")" ]; then
        (umask 077; : > "$LOG") 2>/dev/null || true
    elif [ -e "$LOG" ] && [ -w "$LOG" ]; then
        chmod 600 "$LOG" 2>/dev/null || true
    fi
fi
if [ "$LOG" = "/dev/null" ]; then
    :
elif [ -e "$LOG" ]; then
    if [ -w "$LOG" ]; then exec > >(tee -a "$LOG") 2>&1; fi
elif [ -d "$(dirname "$LOG")" ] && [ -w "$(dirname "$LOG")" ]; then
    exec > >(tee -a "$LOG") 2>&1
fi
printf '\n########## install-tinycmdr.sh %s  (%s)\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$(hostname)"

DEFAULTS="$SRC/install/fleet-defaults.json"
jget() {   # jget <json-file> <key> -> value or empty
    if [ ! -f "$1" ]; then return 0; fi
    "$PY" - "$1" "$2" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8-sig"))
except Exception:
    sys.exit(0)
v = d.get(sys.argv[2], "")
print("" if v is None else v)
PY
}

cfgval() {  # cfgval <dotted.key> -> value from the installed config.json
    if [ ! -f "$INSTALL_DIR/config.json" ]; then return 0; fi
    "$PY" - "$INSTALL_DIR/config.json" "$1" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8-sig"))
except Exception:
    sys.exit(0)
for part in sys.argv[2].split("."):
    d = (d or {}).get(part) if isinstance(d, dict) else None
print("" if d is None else d)
PY
}

version_of() { grep -m1 -oE 'VERSION = "[^"]+"' "$1" 2>/dev/null | cut -d'"' -f2; }

# ------------------------------------------------- how should it run here? ---
# Two supported shapes, same agent:
#
#   system  a unit in /etc/systemd/system, enabled at boot, run as $RUN_USER, and
#           (unless --no-sudoers) that user gets passwordless sudo so the agent can
#           actually administer the box. Needs root to install.
#   user    a unit in ~/.config/systemd/user, started at login through your own
#           systemd instance, verb in ~/.local/bin, no root anywhere, and NO sudo
#           for the agent - an unattended `sudo` will just sit at a password prompt.
#
# The default follows who you are: root installs system (today's behaviour, and what
# a fleet push gets), everyone else gets user. --mode overrides either way, and
# `sudo bash install-tinycmdr.sh --mode user` means "install it for me as me".
if [ -z "$INSTALL_MODE" ] && [ "$ASK_MODE" = 1 ] && [ -t 0 ] && [ -t 1 ]; then
    if [ "$(id -u)" = 0 ]; then _dflt=1; else _dflt=2; fi
    printf '\n  How should tinycmdr run on this machine?\n\n'
    printf '    1) system   boots with the machine, needs sudo now, the agent gets\n'
    printf '                passwordless sudo, and the verb lands in /usr/local/bin\n'
    printf '    2) user     starts when you log in, needs no sudo at all, the agent\n'
    printf '                gets no sudo, and the verb lands in ~/.local/bin\n\n'
    printf '  [1/2] (Enter = %s): ' "$_dflt"
    _answer=""
    read -r _answer || _answer=""          # EOF must not kill the run (see 1.0.4)
    case "$_answer" in
        1|system|s|S) INSTALL_MODE=system ;;
        2|user|u|U)   INSTALL_MODE=user ;;
        *)            : ;;                 # Enter / anything else: default below
    esac
fi
if [ -z "$INSTALL_MODE" ]; then
    if [ "$(id -u)" = 0 ]; then INSTALL_MODE=system; else INSTALL_MODE=user; fi
fi
case "$INSTALL_MODE" in
    system|user) ;;
    *) die "--mode must be system or user (got: $INSTALL_MODE)" ;;
esac
if [ "$INSTALL_MODE" = user ] && [ "$(id -u)" = 0 ]; then
    # sudo, but for the human who typed it: their home, their unit, no root kept.
    _target="${SUDO_USER:-}"
    if [ -n "$_target" ] && [ "$_target" != root ] && id -u "$_target" >/dev/null 2>&1; then
        RUN_USER="$_target"
        USER_HOME="$(getent passwd "$RUN_USER" 2>/dev/null | cut -d: -f6)"
        USER_HOME="${USER_HOME:-/home/$RUN_USER}"
        # only when --install-dir was not given: an explicit folder always wins
        if [ "$DIR_GIVEN" = 0 ]; then INSTALL_DIR="$USER_HOME/tinycmdr"; fi
    fi
fi
RUN_UID="$(id -u "$RUN_USER" 2>/dev/null || echo "")"
# The PRIMARY GROUP, resolved rather than assumed to be named after the user. `id -gn`
# answers for AD/LDAP/SSSD accounts, for `useradd -N`, wherever USERGROUPS_ENAB=no, and
# on a Mac-style `staff` group - every one of which made `chown user:user` fail with
# "illegal group name", and under `set -e` that killed the run right after config.json
# (leaving no .env and no unit) and put a Group= systemd cannot switch to in the unit.
RUN_GROUP="$(id -gn "$RUN_USER" 2>/dev/null || true)"
[ -n "$RUN_GROUP" ] || RUN_GROUP="$(id -gn 2>/dev/null || true)"
[ -n "$RUN_GROUP" ] || RUN_GROUP="$RUN_USER"
# True when the installer is already running as the account it installs for: the common
# user-mode case, where a chown is a no-op at best. A chown that cannot work (not root)
# must never kill the run and must never be silent.
CHOWN_NEEDED=1
if [ "$(id -un)" = "$RUN_USER" ]; then CHOWN_NEEDED=0; fi
if [ "$INSTALL_MODE" = user ]; then
    UNIT="$USER_HOME/.config/systemd/user/$SERVICE_NAME.service"
    WRAPPER="$USER_HOME/.local/bin/tinycmdr"
else
    UNIT="/etc/systemd/system/$SERVICE_NAME.service"
    WRAPPER="/usr/local/bin/tinycmdr"
fi
# strings the unit file and the closing summary are built from
if [ "$INSTALL_MODE" = user ]; then
    SCTL_HINT="systemctl --user"
    JCTL_SCOPE="--user "
    SUDO_IF_ROOT=""
    BOOT_TARGET="default.target"
    BOOT_DEPS=""
    # a user unit runs as you already; User=/Group= there makes systemd try to switch
    # to a group it has no permission to, and the service dies with status=216/GROUP
    UNIT_USER_LINES=""
else
    SCTL_HINT="systemctl"
    JCTL_SCOPE=""
    SUDO_IF_ROOT="sudo "
    BOOT_TARGET="multi-user.target"
    BOOT_DEPS="After=network-online.target
Wants=network-online.target"
    UNIT_USER_LINES="User=$RUN_USER
Group=$RUN_GROUP"
fi

# Hand files to the service user, and never die trying. `chown user:user` used to be
# written out at every site below: on a host where the group is not named after the user
# it failed ("illegal group name") and `set -e` aborted the install - measured on an
# AD-backed box, which came out with a config.json and no .env and no unit. A chown that
# fails now says what it could not do and the install carries on, so the worst case is a
# warning plus files owned by whoever ran the installer.
chown_to() {   # chown_to [-R] <path>...   non-fatal, names what it could not change
    if [ "$CHOWN_NEEDED" = 0 ]; then return 0; fi
    local rec="" p=""
    if [ "${1:-}" = "-R" ]; then rec="-R"; shift; fi
    for p in "$@"; do
        [ -e "$p" ] || continue
        if chown $rec "$RUN_USER:$RUN_GROUP" "$p" 2>/dev/null; then
            continue
        fi
        warn "could not chown $p to $RUN_USER:$RUN_GROUP - it stays $(id -un):$(id -gn);"
        warn "              the service may not be able to write it"
    done
}

# systemctl/journalctl in the right scope. A user unit is driven through that user's
# own systemd instance: as root we have to go through runuser to reach it, because
# `systemctl --user` as root would talk to root's bus and find nothing.
sctl() {
    if [ "$INSTALL_MODE" != user ]; then
        systemctl "$@"
        return
    fi
    if [ "$(id -u)" = 0 ]; then
        if command -v runuser >/dev/null 2>&1; then
            runuser -u "$RUN_USER" -- env XDG_RUNTIME_DIR="/run/user/$RUN_UID" systemctl --user "$@"
        else
            sudo -u "$RUN_USER" env XDG_RUNTIME_DIR="/run/user/$RUN_UID" systemctl --user "$@"
        fi
    else
        systemctl --user "$@"
    fi
}
jctl() {
    if [ "$INSTALL_MODE" = user ] && [ "$(id -u)" != 0 ]; then
        journalctl --user -u "$SERVICE_NAME" "$@"
    else
        journalctl -u "$SERVICE_NAME" "$@"
    fi
}
if [ "$INSTALL_MODE" = user ] && [ "$(id -u)" != 0 ]; then
    if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$RUN_UID" ]; then
        export XDG_RUNTIME_DIR="/run/user/$RUN_UID"
    fi
    if [ -z "${XDG_RUNTIME_DIR:-}" ] || [ ! -S "$XDG_RUNTIME_DIR/bus" ]; then
        die "a user install needs your own systemd session (no bus at \
     ${XDG_RUNTIME_DIR:-unset}/bus). Log in on the box and try again, or enable
     lingering from root:  sudo loginctl enable-linger $RUN_USER
     For the system install instead:  sudo bash $0 --mode system"
    fi
fi

# ------------------------------------------------------------- verify mode ---
if [ "$VERIFY_ONLY" = 1 ]; then
    echo "tinycmdr on $(hostname) - $INSTALL_DIR"
    if [ ! -f "$INSTALL_DIR/config.json" ]; then
        echo "  no install here (no config.json)"
        exit 1
    fi
    printf '  version      : %s\n' "$(version_of "$INSTALL_DIR/tinycmdr.py")"
    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        printf '  venv python  : %s\n' "$("$INSTALL_DIR/venv/bin/python" -V 2>&1)"
    else
        printf '  venv python  : MISSING\n'
    fi
    printf '  mode         : %s (%s)\n' "$INSTALL_MODE" "$UNIT"
    printf '  unit file    : %s\n' "$([ -f "$UNIT" ] && echo present || echo MISSING)"
    printf '  enabled      : %s\n' "$(sctl is-enabled "$SERVICE_NAME" 2>&1 || true)"
    printf '  active       : %s\n' "$(sctl is-active "$SERVICE_NAME" 2>&1 || true)"
    printf '  mattermost   : %s://%s:%s\n' "$(cfgval mattermost.scheme)" \
        "$(cfgval mattermost.url)" "$(cfgval mattermost.port)"
    printf '  model        : %s @ %s\n' "$(cfgval llm.model)" "$(cfgval llm.base_url)"
    printf '  bot name     : %s\n' "$(cfgval agent.bot_name)"
    printf '  allowed user : %s\n' "$(cfgval mattermost.allowed_users)"
    if [ -f "$INSTALL_DIR/.env" ] && grep -q '^TINYCMDR_MM_TOKEN=.\+' "$INSTALL_DIR/.env"; then
        printf '  token in .env: yes\n'
    else
        printf '  token in .env: NO\n'
    fi
    _mm=no; _tg=no
    if [ -f "$INSTALL_DIR/.env" ]; then
        grep -q '^TINYCMDR_MM_TOKEN=.\+' "$INSTALL_DIR/.env" && _mm=yes
        grep -q '^TINYCMDR_TG_TOKEN=.\+' "$INSTALL_DIR/.env" && _tg=yes
    fi
    if [ "$_mm" = yes ]; then printf '  lane         : mattermost\n'
    elif [ "$_tg" = yes ]; then printf '  lane         : telegram\n'
    else printf '  lane         : none (only --cli / --once on this host)\n'; fi
    echo
    echo "--- last log lines ---"
    tail -n 12 "$INSTALL_DIR/tinycmdr.log" 2>/dev/null || echo "(no tinycmdr.log)"
    exit 0
fi

# -------------------------------------------------------------- pre-flight ---
if [ "$INSTALL_MODE" = system ] && [ "$(id -u)" != 0 ]; then
    die "a system install writes $UNIT and needs root:
     sudo bash $0 --mode system
     ...or install it for yourself with no root at all:  bash $0 --mode user"
fi
[ "$INSTALL_MODE" = user ] || [ "$(id -u)" = 0 ] \
    || die "internal: system mode without root reached pre-flight (report this)"
[ -f "$SRC/tinycmdr.py" ] || die "tinycmdr.py not found next to install/ (looked in $SRC)"
command -v systemctl >/dev/null || die "systemd not found - this installer is for Debian/Ubuntu hosts"
id -u "$RUN_USER" >/dev/null 2>&1 || die "no such user: $RUN_USER"

# ------------------------------------------------------------------- identity ---
# A systemd unit name belongs to the HOST, not to a folder: a run that keeps the default
# name stops and re-registers whatever service already carries it, so a probe or a second
# install silently takes the first one's service away. Same class as the launchd label
# (measured there, 2026-09-26): the displaced agent was left unregistered and silent.
# `systemctl show` exits non-zero for a unit that does not exist, and with pipefail that
# is fatal at the assignment under set -e: keep the probe's failure out of the pipeline.
_unit_exec="$( { sctl show -p ExecStart --value "$SERVICE_NAME" 2>/dev/null || true; } | head -1)"
case "$_unit_exec" in
    "") : ;;
    *"$INSTALL_DIR"*) : ;;
    *) die "the service name $SERVICE_NAME already belongs to another install:
    $_unit_exec
Give this install its own unit and it will leave that one alone:
    TINYCMDR_SERVICE=tinycmdr-$(hostname -s) bash $0 <your switches>
or remove the other install first:  bash $0 --uninstall" ;;
esac

say "pre-flight"
info "package      : $SRC"
info "version      : $(version_of "$SRC/tinycmdr.py")"
info "install dir  : $INSTALL_DIR"
info "mode         : $INSTALL_MODE$( [ "$INSTALL_MODE" = user ] && echo "   (your own systemd instance, starts at login, no sudo for the agent)" || echo "   (system service, boots with the machine)" )"
info "service user : $RUN_USER"
# Named, not assumed: every chown above and the unit's Group= use the PRIMARY group,
# which is not the user name on an AD/LDAP box, a `useradd -N` account, a host with
# USERGROUPS_ENAB=no, or a Mac-style `staff` group.
info "service group: $RUN_GROUP"
"$PY" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)' \
    || die "$PY is $("$PY" -V 2>&1); tinycmdr runs on Python 3.10-3.12.
python3.13 and newer resolve a broken mmpy_bot; 3.9 predates write_text(newline=...).
Install python3.12 (apt install python3.12 python3.12-venv), or point this run at one:
TINYCMDR_PYTHON=/usr/bin/python3.12 bash $0 ..."
info "python       : $($PY -V 2>&1)  ($(command -v "$PY"))"

if [ "$UNINSTALL" = 1 ]; then
    say "uninstall"
    _removed=0
    sctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    if [ -f "$UNIT" ]; then
        rm -f "$UNIT"
        _removed=1
    fi
    sctl daemon-reload 2>/dev/null || true
    # The two things the install writes OUTSIDE its folder, both SCOPED: a probe
    # uninstall (--install-dir /tmp/...) must never take a real install's verb
    # wrapper or sudo grant with it (measured 2026-09-23: it ate a live install's wrapper and grant).
    if [ -f "$WRAPPER" ] && grep -qF "$INSTALL_DIR" "$WRAPPER" 2>/dev/null; then
        rm -f "$WRAPPER"
        _removed=1
        info "PATH wrapper removed ($WRAPPER)"
    fi
    # A user install can be removed for the user by root too; ~/.local/bin is left
    # alone when empty.
    if [ "$INSTALL_MODE" = user ] && [ -d "$USER_HOME/.local/bin" ] \
            && [ -z "$(ls -A "$USER_HOME/.local/bin" 2>/dev/null)" ]; then
        rmdir "$USER_HOME/.local/bin" 2>/dev/null || true
    fi
    # the passwordless-sudo grant is per-USER, not per-install: only the default
    # install's removal takes it. Both file names - a pre-tinycmdr one can remain.
    if [ "$(id -u)" = 0 ] && [ "$INSTALL_MODE" = system ] \
            && [ "$INSTALL_DIR" = "$DEFAULT_INSTALL_DIR" ]; then
        rm -f "/etc/sudoers.d/${RUN_USER}-tinycmdr" \
              "/etc/sudoers.d/${RUN_USER}-hermes"
        info "sudo grant removed (both file names)"
    fi
    if [ -n "$INSTALL_DIR" ] && [ "$INSTALL_DIR" != "/" ] && [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
        _removed=1
        info "removed $INSTALL_DIR (token, notes and history went with it)"
    fi
    if [ "$_removed" = 0 ]; then
        # A removal that finds nothing must SAY so. Under `sudo` the installer used to
        # resolve the invoker's home wrong on a Mac and print "done." having removed
        # nothing at all - silence is what made that look like success.
        warn "nothing to remove: no unit at $UNIT, no PATH wrapper pointing at"
        warn "$INSTALL_DIR, and no install folder there."
        info "checked unit    : $UNIT"
        info "checked wrapper : $WRAPPER"
        info "an install that lives elsewhere: --install-dir <its folder>"
    else
        info "unit $SERVICE_NAME removed"
    fi
    exit 0
fi

# ----------------------------------------------------------- asking a person ---
# Asked ONLY when a person is there to answer: a real terminal (the one-line door
# hands its own over - see install.sh) and not --yes. A scripted or headless run
# takes the defaults and prints them, so a fleet push can never hang on a question.
# TINYCMDR_ASK=1 forces the asks on a redirected stdin.
# Prompts go to STDERR: the caller reads the answer from stdout.
mm_port_only() {   # mm_port_only <what a reader typed> -> the port, or ""
    # A reader pastes what their browser shows, and mattermost.url is the HOST alone
    # (the scheme and port are separate keys in config.json): split what they gave
    # instead of writing a url no client can build a request from.
    local h="$1"
    h="${h#http://}"; h="${h#https://}"; h="${h%%/*}"; h="${h#*@}"
    case "$h" in
        *:*:*) printf '%s' "" ;;          # IPv6 literal, not host:port
        *:*) printf '%s' "${h##*:}" ;;
        *) printf '%s' "" ;;
    esac
}

mm_host_only() {   # mm_host_only <what a reader typed> -> prints the bare host
    local h="$1"
    h="${h#http://}"; h="${h#https://}"; h="${h%%/*}"; h="${h#*@}"
    case "$h" in
        *:*:*) printf '%s' "$h" ;;         # IPv6 literal: leave it whole
        *:*) printf '%s' "${h%:*}" ;;
        *) printf '%s' "$h" ;;
    esac
}

trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    printf '%s' "${s%"${s##*[![:space:]]}"}"
}

ask_text() {   # ask_text <prompt> [default] -> prints the answer
    local p="$1" dflt="${2:-}" a=""
    if [ -n "$dflt" ]; then printf '    %s [%s]: ' "$p" "$dflt" >&2
    else printf '    %s: ' "$p" >&2; fi
    read -r a || a=""
    a="$(trim "$a")"
    if [ -z "$a" ]; then printf '%s' "$dflt"; else printf '%s' "$a"; fi
}

ask_secret() {   # ask_secret <prompt> -> prints what was typed, masked with *, may be empty
    # ONE masked reader for every secret (bot tokens, provider keys). `read -s` shows
    # NOTHING, so the Mattermost token read as a dead field and a paste into it was
    # invisible; this echoes one * per character instead, whether the characters come
    # from typing or a paste. Backspace rubs one out; Enter ends the value.
    local a="" ch=""
    printf '    %s: ' "$1" >&2
    while IFS= read -rsn1 ch; do
        case "$ch" in
            ""|$'\n'|$'\r') break ;;
            $'\177'|$'\b')
                if [ -n "$a" ]; then a="${a%?}"; printf '\b \b' >&2; fi ;;
            *) a="${a}${ch}"; printf '*' >&2 ;;
        esac
    done
    printf '\n' >&2
    printf '%s' "$a"
}

probe_endpoint() {   # probe_endpoint <url> [key] -> "OK <id>.." | "AUTH <status>" | "NO <why>"
    # The installer's own reachability check, using the python it already requires: one
    # GET /models, 8s, metadata only - never a completion. The bearer key rides along when
    # there is one, so a hosted provider is checked AUTHENTICATED: without it a cloud
    # endpoint answers 401, the old probe called that "no answer", and the model list it
    # needed never came back. 401/403 returns as AUTH, so the caller re-asks the KEY and
    # not the link. An endpoint that answers with no list is still REACHABLE.
    "$PY" - "$1" "${2:-}" <<'PY'
import json, sys, urllib.error, urllib.request
url = sys.argv[1].rstrip("/")
key = sys.argv[2] if len(sys.argv) > 2 else ""
hdrs = {"Accept": "application/json", "User-Agent": "tinycmdr-install"}
if key:
    hdrs["Authorization"] = "Bearer " + key
try:
    req = urllib.request.Request(url + "/models", headers=hdrs)
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
except urllib.error.HTTPError as e:
    print("AUTH %s" % e.code if e.code in (401, 403) else "NO HTTP %s" % e.code)
    raise SystemExit(0)
except Exception as e:
    print("NO %s" % str(e)[:160])
    raise SystemExit(0)
ids = []
for item in (data.get("data") or data.get("models") or []):
    if isinstance(item, str):
        ids.append(item)
    elif isinstance(item, dict) and (item.get("id") or item.get("name")):
        ids.append(str(item.get("id") or item.get("name")))
print("OK%s" % ((" " + " ".join(ids)) if ids else ""))
PY
}

ask_model_id() {   # ask_model_id "<ids>" [default] -> the id, or its NUMBER in the list
    # The list the endpoint just advertised, offered as numbers: a reader typing the id
    # from memory is how a box ends up configured for a model it does not serve. Nothing to
    # offer (no answer, or a server that lists nothing) keeps the plain typed answer.
    local ids="$1" dflt="${2:-}" a="" n=0 pick="" id=""
    if [ -z "$ids" ]; then
        ask_text "Model id" "$dflt"
        return 0
    fi
    printf '    models it advertises:\n' >&2
    for id in $ids; do
        n=$((n + 1))
        printf '      %2d) %s\n' "$n" "$id" >&2
    done
    a="$(ask_text "Model id (number or name)" "$dflt")"
    case "$a" in
        ''|*[!0-9]*) printf '%s' "$a"; return 0 ;;
    esac
    n=0
    for id in $ids; do
        n=$((n + 1))
        if [ "$n" = "$a" ]; then printf '%s' "$id"; return 0; fi
    done
    warn "no model numbered $a - keeping what you typed"
    printf '%s' "$a"
}

ask_yes() {   # ask_yes <question> [y|n] -> 0 = yes
    local a="" dflt="${2:-y}"
    case "$dflt" in y|Y) printf '    %s [Y/n] ' "$1" >&2 ;; *) printf '    %s [y/N] ' "$1" >&2 ;; esac
    read -r a || a=""
    a="$(trim "$a")"
    case "$a" in
        "") case "$dflt" in y|Y) return 0 ;; *) return 1 ;; esac ;;
        y|Y|yes|YES|Yes) return 0 ;;
        *) return 1 ;;
    esac
}

ask_choice() {   # ask_choice <prompt> <1|2 default> <label1> <label2> -> prints 1 or 2
    # The local/cloud question every model endpoint is asked now. Local endpoints need no
    # key; cloud ones do, and the answer decides whether a key is asked for and carried on
    # the bearer probe. Prints "1" or "2" so the caller never re-parses prose.
    local p="$1" dflt="$2" l1="$3" l2="$4" a=""
    printf '    %s\n' "$p" >&2
    printf '      1) %s\n      2) %s\n' "$l1" "$l2" >&2
    while :; do
        printf '    choice [%s]: ' "$dflt" >&2
        read -r a || a=""
        a="$(trim "$a")"
        case "$a" in
            "") printf '%s' "$dflt"; return 0 ;;
            1|l|L|local|LOCAL|Local|lan|LAN) printf '1'; return 0 ;;
            2|c|C|cloud|CLOUD|Cloud|hosted|remote) printf '2'; return 0 ;;
            *) warn "answer 1 or 2" ;;
        esac
    done
}

# ------------------------------------------------------------------ token ---
if [ -z "$TOKEN" ] && [ -n "$TOKEN_FILE" ]; then
    [ -f "$TOKEN_FILE" ] || die "no such token file: $TOKEN_FILE"
    TOKEN="$(grep -m1 -oE '[A-Za-z0-9]{20,}' "$TOKEN_FILE" || true)"
    [ -n "$TOKEN" ] || die "no token-looking string in $TOKEN_FILE"
fi
if [ -z "$TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
    TOKEN="$(grep -m1 '^TINYCMDR_MM_TOKEN=' "$INSTALL_DIR/.env" | cut -d= -f2- || true)"
    if [ -n "$TOKEN" ]; then
        info "reusing the token already in $INSTALL_DIR/.env"
    fi
fi
if [ -z "$TG_TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
    TG_TOKEN="$(grep -m1 '^TINYCMDR_TG_TOKEN=' "$INSTALL_DIR/.env" | cut -d= -f2- || true)"
    [ -n "$TG_TOKEN" ] && info "reusing the Telegram token already in .env"
fi
# The package's secrets file can carry the bot token, so read it HERE - before the lane
# is chosen. It used to be read only where .env is WRITTEN, which is after the lane
# branch: a fleet secrets file with TINYCMDR_MM_TOKEN produced "installing WITHOUT a chat
# account", an empty `TINYCMDR_MM_TOKEN=` as the FIRST line of .env with the file's real
# one below it - and _load_env_file keeps the FIRST occurrence - so the bot answered no
# DMs while config.json and .env both looked configured.
if [ -n "$SECRETS_FILE" ] && [ ! -f "$SECRETS_FILE" ]; then
    die "--secrets-file $SECRETS_FILE does not exist"
fi
# One path, chosen once: --secrets-file when the caller named one, else the package copy.
# (An apostrophe inside ${VAR:-word} makes bash hunt for a closing quote - found by
# syntax-checking this line, not by reading it.)
ENV_SOURCE="${SECRETS_FILE:-$SRC/install/fleet-secrets.env}"
if [ -z "$TOKEN" ] && [ -f "$ENV_SOURCE" ]; then
    TOKEN="$(grep -m1 '^TINYCMDR_MM_TOKEN=' "$ENV_SOURCE" \
        | cut -d= -f2- | tr -d ' \r' || true)"
    if [ -n "$TOKEN" ]; then
        info "bot token   : TINYCMDR_MM_TOKEN from $ENV_SOURCE"
    fi
fi
# The lane is deny-by-default, so a token with no id is a bot that ignores every DM.
# Refuse here, with the reason, rather than at 03:00 in a log nobody is reading.
TG_IDS_CLEAN="$(printf '%s' "$TG_IDS" | tr ',;' '  ' | tr -s ' ' '\n' \
    | grep -E '^[0-9]+$' | tr '\n' ' ' | sed 's/ *$//' || true)"
if [ -n "$TG_TOKEN" ] && [ -z "$TG_IDS_CLEAN" ]; then
    die "a Telegram token with no numeric id: that lane would ignore every DM.
Message @userinfobot for your id and pass --telegram-ids 123456789"
fi
if [ -n "$TG_IDS" ] && [ -z "$TG_IDS_CLEAN" ]; then
    die "--telegram-ids needs numeric ids (message @userinfobot for yours): got '$TG_IDS'"
fi
# ---------------------------------------------------------------- questions ---
# A reader who downloaded this and ran it knows two things at most: the server their
# Mattermost lives on and a bot token. Everything else the bot cannot work without -
# where the server is, who may command it, which model answers and that model's key -
# was a switch they had to know about, and an install with none of them came out with
# the example's documentation endpoint (192.0.2.10, TEST-NET-1) as its model and no
# chat lane at all. Ask here, before anything is written: answering is then all a
# reader has to do, and a refusal leaves the disk untouched. This runs BEFORE the lane
# decision below, because the token decides which lane the service runs.
ASK_Q=1
[ -t 0 ] || ASK_Q=0
[ "$YES" = 1 ] && ASK_Q=0
[ -n "${TINYCMDR_ASK:-}" ] && ASK_Q=1
if [ "$ASK_Q" = 1 ]; then
    say "a few questions"
    info "press Enter with no answer to take the value in brackets"
    if [ -z "$TOKEN" ] && [ -z "$TG_TOKEN" ]; then
        TOKEN="$(ask_secret "Mattermost bot token (input hidden, Enter to skip)")"
    fi
fi

# Where the bot lives and who may command it: fleet-defaults.json (a fleet package),
# then this install's own config.json, then the reader.
if [ -z "$MM_URL_ARG" ]; then MM_URL_ARG="$(jget "$DEFAULTS" mattermost_url)"; fi
if [ -z "$MM_URL_ARG" ]; then MM_URL_ARG="$(cfgval mattermost.url)"; fi
case "$MM_URL_ARG" in chat.example.com|CHANGE-ME.example.com|"[]") MM_URL_ARG="" ;; esac
ALLOWED_DFLT="$ALLOWED_ARG"
[ -n "$ALLOWED_DFLT" ] || ALLOWED_DFLT="$(jget "$DEFAULTS" allowed_user)"
[ -n "$ALLOWED_DFLT" ] || ALLOWED_DFLT="$(cfgval mattermost.allowed_users)"
# The example ships a placeholder user id and an empty list prints as []: neither is
# an answer, and proposing one is how a fresh install ends up with a bot that ignores
# every DM while looking configured.
case "$ALLOWED_DFLT" in "[]"|*REPLACE_WITH*) ALLOWED_DFLT="" ;; esac
if [ "$ASK_Q" = 1 ]; then
    if [ -n "$TOKEN" ]; then
        MM_URL_ARG="$(mm_host_only "$(ask_text "Mattermost server, no https:// (e.g. chat.example.com)" "$MM_URL_ARG")")"
        ALLOWED_ARG="$(ask_text "Your Mattermost user id (optional, but without it the bot ignores your DMs)" "$ALLOWED_DFLT")"
    elif [ -z "$TG_TOKEN" ]; then
        info "no chat token: this install will have nothing remote to serve. A token"
        info "can be added later with --token-file, no reinstall of the app itself."
    fi
fi
# A token with no server is a bot that exits at its first start - the chat lane is
# fatal when Mattermost cannot be reached - so refuse HERE, with the switch to pass.
if [ -n "$TOKEN" ] && [ -z "$MM_URL_ARG" ]; then
    die "a Mattermost bot token with no server address.
Pass --mattermost-url chat.example.com (no scheme), or re-run at a terminal and
answer the questions."
fi

# ---- the third door: Telegram ----
# A Telegram bot needs nothing hosted and works from anywhere, so it is offered here
# rather than only as a switch. Its TOKEN is .env-only (TINYCMDR_TG_TOKEN) and the lane
# is deny-by-default: a token with no numeric id ignores every DM.
if [ "$ASK_Q" = 1 ] && [ -z "$TG_TOKEN" ]; then
    if ask_yes "Also install a Telegram bot lane (a token from @BotFather)?" n; then
        TG_TOKEN="$(ask_secret "Telegram bot token (input hidden)")"
    fi
fi
if [ "$ASK_Q" = 1 ] && [ -n "$TG_TOKEN" ] && [ -z "$TG_IDS" ]; then
    TG_IDS="$(ask_text "Your numeric Telegram id (message @userinfobot for it)" "")"
    TG_IDS_CLEAN="$(printf '%s' "$TG_IDS" | tr ',;' '  ' | tr -s ' ' '\n' \
        | grep -E '^[0-9]+$' | tr '\n' ' ' | sed 's/ *$//' || true)"
    if [ -z "$TG_IDS_CLEAN" ]; then
        die "a Telegram token with no numeric id: that lane would ignore every DM.
Message @userinfobot for your id and pass --telegram-ids 123456789"
    fi
    if [ -n "$TOKEN" ]; then
        info "both tokens set: this service serves BOTH - Mattermost and a Telegram"
        info "lane in the same process (no second unit to start)."
    fi
fi

# Which model answers. Any OpenAI-compatible /v1 root: a llama.cpp on this host, a box
# on the LAN, or a hosted provider.
MODEL_BASE_DFLT="$MODEL_BASE_URL"
[ -n "$MODEL_BASE_DFLT" ] || MODEL_BASE_DFLT="$(jget "$DEFAULTS" model_base_url)"
[ -n "$MODEL_BASE_DFLT" ] || MODEL_BASE_DFLT="$(cfgval llm.base_url)"
# 192.0.2.10 is TEST-NET-1: the example's placeholder is not an endpoint.
case "$MODEL_BASE_DFLT" in http://192.0.2.10:8081/v1) MODEL_BASE_DFLT="" ;; esac
[ -n "$MODEL_BASE_DFLT" ] || MODEL_BASE_DFLT="$DEFAULT_MODEL_BASE"
MODEL_DFLT="$MODEL"
[ -n "$MODEL_DFLT" ] || MODEL_DFLT="$(jget "$DEFAULTS" model)"
[ -n "$MODEL_DFLT" ] || MODEL_DFLT="$(cfgval llm.model)"
case "$MODEL_DFLT" in my-model-name) MODEL_DFLT="" ;; esac
[ -n "$MODEL_DFLT" ] || MODEL_DFLT="$DEFAULT_MODEL"
if [ "$ASK_Q" = 1 ]; then
    info "local (llama.cpp, Ollama, vLLM on this machine or your LAN) or cloud"
    info "(a hosted OpenAI-compatible provider, which needs an API key)."
    _kind_dflt=1
    case "$MODEL_BASE_DFLT" in
        *//127.0.0.1:*|*//localhost:*|*"::1"*|*//10.*|*//192.168.*) ;;
        *) _kind_dflt=2 ;;
    esac
    _kind="$(ask_choice "Which kind of endpoint is it?" "$_kind_dflt" \
        "local / my LAN (no key)" "cloud / hosted (needs an API key)")"
    MODEL_KEY=""
    if [ "$_kind" = "2" ]; then
        MODEL_KEY="$(ask_secret "API key for the provider (hidden)")"
    fi
    # Ask, PROBE with the key in hand, and offer what it advertises. Every other answer in
    # this installer can be corrected in a file; this one cannot be checked until the first
    # request fails, so a typo here used to be invisible for the whole install. Three tries,
    # then it keeps the URL and names the command that fixes it - a box whose server is not
    # up yet is normal, and the installer must not become a wall.
    _url_tries=0
    while :; do
        _lbl="Model endpoint"
        [ "$_kind" = "2" ] && _lbl="Endpoint (e.g. https://api.provider.com/v1)"
        MODEL_BASE_URL="$(ask_text "$_lbl" "$MODEL_BASE_DFLT")"
        _key_tries=0
        while :; do
            _probe="$(probe_endpoint "$MODEL_BASE_URL" "$MODEL_KEY")"
            if [ "${_probe%% *}" = "AUTH" ] && [ "$_kind" = "2" ]; then
                _key_tries=$((_key_tries + 1))
                if [ "$_key_tries" -ge 3 ]; then
                    warn "the provider still refuses the key - keeping $MODEL_BASE_URL unverified"
                    break
                fi
                warn "the provider refused that key (HTTP ${_probe#AUTH })"
                MODEL_KEY="$(ask_secret "API key (hidden, try again)")"
                continue
            fi
            break
        done
        if [ "${_probe%% *}" = "OK" ]; then
            _ids="$(printf '%s' "${_probe#OK}")"
            _ids="$(trim "$_ids")"
            info "reachable${_ids:+ - it advertises:}${_ids:+$_ids}"
            break
        fi
        warn "no answer from $MODEL_BASE_URL: ${_probe#* }"
        _url_tries=$((_url_tries + 1))
        if [ "$_url_tries" -ge 3 ]; then
            info "keeping it anyway - fix it later with: tinycmdr model endpoint <url>"
            _ids=""
            break
        fi
        info "check the host and port (the server may not be running yet)."
    done
    MODEL="$(ask_model_id "$_ids" "$MODEL_DFLT")"
    # The key is written to .env below as TINYCMDR_LLM_API_KEY, NOT to config.json: the
    # primary's key used to land in llm.api_key, a file the agent reads into a prompt.
    MODEL_BASE_GIVEN="$MODEL_BASE_URL"
    MODEL_GIVEN="$MODEL"
fi

# ---- more endpoints: llm.fallbacks, tried in order when the primary fails ----
# Each entry is base_url + model (+ an optional /model alias) and its key goes to .env
# under a generated name the entry's api_key_env points at - the shape the build
# resolves for a fallback, and it keeps the key out of config.json.
if [ "$ASK_Q" = 1 ]; then
    _fb_n=0
    while :; do
        _fb_n=$((_fb_n + 1))
        if ! ask_yes "Add another endpoint?" n; then break; fi
        # Extra endpoints get the same conversation as the primary: local or cloud, the key
        # (cloud), then the link - probed WITH the key - then the model from what it
        # advertises. The old loop asked url, then made the reader TYPE a model id, and only
        # then asked for a key it never used.
        _fb_kind="$(ask_choice "Endpoint #$_fb_n: local or cloud?" 1 \
            "local / my LAN (no key)" "cloud / hosted (needs an API key)")"
        _fb_key=""
        [ "$_fb_kind" = "2" ] && _fb_key="$(ask_secret "API key for it (hidden)")"
        _fb_lbl="Endpoint #$_fb_n (OpenAI-compatible /v1 root)"
        [ "$_fb_kind" = "2" ] && _fb_lbl="Endpoint #$_fb_n (e.g. https://api.provider.com/v1)"
        _fb_url="$(ask_text "$_fb_lbl" "")"
        if [ -z "$_fb_url" ]; then
            warn "no address given - nothing added"
            _fb_n=$((_fb_n - 1))
            continue
        fi
        _fb_key_tries=0
        while :; do
            _fb_probe="$(probe_endpoint "$_fb_url" "$_fb_key")"
            if [ "${_fb_probe%% *}" = "AUTH" ] && [ "$_fb_kind" = "2" ]; then
                _fb_key_tries=$((_fb_key_tries + 1))
                if [ "$_fb_key_tries" -ge 3 ]; then
                    warn "the provider still refuses the key - adding it with an unchecked model"
                    break
                fi
                warn "the provider refused that key (HTTP ${_fb_probe#AUTH })"
                _fb_key="$(ask_secret "API key (hidden, try again)")"
                continue
            fi
            break
        done
        _fb_ids=""
        if [ "${_fb_probe%% *}" = "OK" ]; then
            _fb_ids="$(trim "$(printf '%s' "${_fb_probe#OK}")")"
            info "reachable${_fb_ids:+ - it advertises:}${_fb_ids:+$_fb_ids}"
        else
            warn "no answer from $_fb_url: ${_fb_probe#* } - the model id is unchecked"
        fi
        _fb_model="$(ask_model_id "$_fb_ids" "")"
        _fb_alias="$(ask_text "Alias, so /model <alias> switches to it (blank = none)" "")"
        _fb_name="TINYCMDR_ENDPOINT${_fb_n}_API_KEY"
        FALLBACK_SPECS="${FALLBACK_SPECS}${_fb_url}|${_fb_model}|${_fb_alias}|${_fb_name}
"
        if [ -n "$_fb_key" ]; then
            FB_ENV_LINES="${FB_ENV_LINES}${_fb_name}=${_fb_key}
"
        fi
        info "endpoint #$_fb_n added: ${_fb_model:-?} at $_fb_url"
    done
fi

# ---- web search: may it leave this machine? ----
# Off unless asked. Both built-in providers are third parties, and the keyless anonymous
# tier used to send the model's query with nobody asked and nothing on screen saying so
# (audit, 2026-09-27). A provider ON this LAN - a searxng entry - never needs this, so
# "no" here still leaves a working search if one is configured.
if [ "$ASK_Q" = 1 ] && [ -z "$SEARCH_EGRESS" ]; then
    if ask_yes "May the bot's web search send queries off this machine?" y; then
        SEARCH_EGRESS="true"
    else
        SEARCH_EGRESS="false"
    fi
fi

if [ "$ASK_Q" = 1 ]; then
    say "about to install"
    info "folder       : $INSTALL_DIR"
    if [ -n "$TOKEN" ]; then
        info "how you talk : Mattermost at $MM_URL_ARG"
        info "allowed user : ${ALLOWED_ARG:-NONE - the bot ignores every DM until one is set}"
    elif [ -n "$TG_TOKEN" ]; then
        info "how you talk : Telegram DMs ($TG_IDS_CLEAN)"
    else
        info "how you talk : nothing remote - --cli and --once only (no token given)"
    fi
    info "model        : $MODEL at $MODEL_BASE_URL"
    if [ -n "$MODEL_KEY" ]; then
        info "model key    : given (.env, TINYCMDR_LLM_API_KEY)"
    fi
    _fb_count=0
    for _s in $FALLBACK_SPECS; do _fb_count=$((_fb_count + 1)); done
    if [ "$_fb_count" -gt 0 ]; then
        info "more endpoints: $_fb_count (tried in order when the primary fails)"
    fi
    if [ -n "$TG_TOKEN" ]; then
        info "telegram     : on, DMs from $TG_IDS_CLEAN"
    fi
    if [ "${SEARCH_EGRESS:-false}" = "true" ]; then
        info "web search   : on, and may leave this machine"
    else
        info "web search   : LAN only (an off-LAN provider is refused until allowed)"
    fi
    if ! ask_yes "Install now?"; then
        info "nothing was changed"
        exit 0
    fi
fi

# The venv python path, known before the venv exists: the hint lines below print it
# and set -u kills an unset expansion (measured 2026-09-23: every token-less and
# both-tokens install died here). Set once here, reused where the venv is made.
VENV_PY="$INSTALL_DIR/venv/bin/python"

# A chat account is REQUIRED to run as a service. The harness also runs as a session
# (--cli) and a single task (--once). With NO Mattermost and NO Telegram token there is
# nothing remote to serve: a CLI-only install is a supported way to run it, the files
# are installed and no service is registered (a lane-less service would exit at once
# and Restart=always would loop it forever). Add a token and re-run whenever you want
# a chat lane.
APP_ARGS=""
CHAT_LANE=1
TG_LANE=0
HAS_LANE=1
if [ -n "$TG_TOKEN" ]; then TG_LANE=1; fi
if [ -z "$TOKEN" ] && [ "$TG_LANE" = 1 ]; then
    # The Telegram lane starts by itself with no Mattermost token, so the service runs
    # the BOT here: this is a chat lane that answers DMs on its own.
    CHAT_LANE=0
    info "no Mattermost token, but a Telegram one: the service runs the TELEGRAM lane"
    info "allowlist    : $TG_IDS_CLEAN"
elif [ -z "$TOKEN" ]; then
    CHAT_LANE=0
    HAS_LANE=0
    info "no Mattermost bot token and no Telegram token: a CLI-only install - a"
    info "supported way to run it. Nothing remote is served, and no service is"
    info "registered or started (a lane-less service would exit at once and loop)."
    info "this host has two doors, both work right now:"
    info "  a session : $VENV_PY $INSTALL_DIR/tinycmdr.py --cli"
    info "  one task  : $VENV_PY $INSTALL_DIR/tinycmdr.py --once \"<task>\""
    info "add a chat account later, no reinstall of the app needed:"
    info "  re-run with --token-file <file>            (Mattermost)"
    info "  or with --telegram-token <t> --telegram-ids <id>   (Telegram)"
elif [ "$TG_LANE" = 1 ]; then
    info "both tokens are set: this process serves Mattermost AND Telegram, so there is"
    info "  no second unit to start - the one service answers both doors."
fi

# --------------------------------------------------------------- defaults ---
if [ -z "$MODEL_BASE_URL" ]; then MODEL_BASE_URL="$(jget "$DEFAULTS" model_base_url)"; fi
if [ -z "$MODEL" ]; then MODEL="$(jget "$DEFAULTS" model)"; fi
if [ -z "$BOT_NAME" ]; then BOT_NAME="$(hostname -s)"; fi
ALLOWED_USER="$(jget "$DEFAULTS" allowed_user)"
if [ -z "$ALLOWED_USER" ]; then ALLOWED_USER="$ALLOWED_ARG"; fi
MM_HOST="$(jget "$DEFAULTS" mattermost_url)"
MM_PORT="$(jget "$DEFAULTS" mattermost_port)"
if [ -z "$MM_PORT" ]; then MM_PORT=443; fi
if [ -z "$MM_HOST" ]; then MM_HOST="$MM_URL_ARG"; fi
if [ -z "$MM_HOST" ]; then MM_HOST="$(cfgval mattermost.url)"; fi
# Every source (a switch, the answer, fleet-defaults, this host's own config.json) goes
# through the splitter: a pasted scheme or a ":8443" must not end up inside the host.
if [ -n "$MM_HOST" ]; then
    _mm_pt="$(mm_port_only "$MM_HOST")"
    if [ -n "$_mm_pt" ]; then MM_PORT="$_mm_pt"; fi
    MM_HOST="$(mm_host_only "$MM_HOST")"
fi
if [ -z "$MM_HOST" ]; then
    if [ "$CHAT_LANE" = 0 ]; then
        # No lane here reads mattermost.url (a Telegram-only or token-less host), so
        # demanding a host would block a good install. The first fix covered the
        # Telegram lane only and the token-less path still died (probe, 2026-09-23).
        MM_HOST="CHANGE-ME.example.com"
        info "no Mattermost host: not needed without a Mattermost account"
    else
        die "no Mattermost host.
Pass --mattermost-url <host> (no scheme), or put mattermost_url in
install/fleet-defaults.json for a fleet package."
    fi
fi

say "configuration"
# The truth for an UPDATE is the file on disk, not this run's flags: with no
# fleet-defaults and no --model, MODEL is empty here while the host's config.json is
# perfectly set. Report (and warn about) the values the install will actually run
# with, so an update does not cry wolf about settings it just kept.
EFF_MODEL="$(cfgval llm.model)";   [ -n "$EFF_MODEL" ] || EFF_MODEL="$MODEL"
EFF_BASE="$(cfgval llm.base_url)"; [ -n "$EFF_BASE" ] || EFF_BASE="$MODEL_BASE_URL"
EFF_ALLOWED="$(cfgval mattermost.allowed_users)"
[ -n "$EFF_ALLOWED" ] || EFF_ALLOWED="$ALLOWED_USER"
info "mattermost   : https://${MM_HOST}:${MM_PORT}"
if [ -n "$EFF_MODEL" ]; then
    info "model        : ${EFF_MODEL} @ ${EFF_BASE}"
else
    info "model        : (none set) @ ${EFF_BASE}"
    info "WARNING      : llm.model is unset - set it in config.json (llm.model),"
    info "               or pass --model <id>"
fi
case "$EFF_BASE" in
    ""|http://192.0.2.10:8081/v1)
        info "WARNING      : llm.base_url is still a template default - point it at"
        info "               your own OpenAI-compatible endpoint (llama.cpp, vLLM,"
        info "               Ollama, any OpenAI-compatible server)" ;;
esac
if [ -n "$EFF_ALLOWED" ] && [ "$EFF_ALLOWED" != "[]" ]; then
    info "allowed user : ${EFF_ALLOWED}"
else
    info "allowed user : NONE - the bot will ignore everybody until you add one"
fi
_fb_n=0
for _s in $FALLBACK_SPECS; do _fb_n=$((_fb_n + 1)); done
if [ "$_fb_n" -gt 0 ]; then
    info "more endpoints: $_fb_n (llm.fallbacks, tried in order when the primary fails)"
fi
if [ "$TG_TOKEN" != "" ]; then
    info "telegram     : on, DMs from ${TG_IDS_CLEAN:-none - add an id}"
fi
info "bot name     : ${BOT_NAME}"
if [ "$HAS_LANE" = 0 ]; then
    info "service      : none - no chat account, so nothing runs in the background"
fi

if [ "$FORCE" = 1 ] && sctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    say "force: stopping the running service"
    sctl stop "$SERVICE_NAME" || true
fi

# ------------------------------------------------------------------- files ---
say "files"
keep=""
if [ "$FORCE" = 1 ] && [ -d "$INSTALL_DIR" ]; then
    keep="$(mktemp -d)"
    for f in .env config.json notes.md notes-archive.md \
             jobs.json state.json sessions tools snapshots; do
        if [ -e "$INSTALL_DIR/$f" ]; then
            cp -a "$INSTALL_DIR/$f" "$keep/" 2>/dev/null || true
        fi
    done
fi
mkdir -p "$INSTALL_DIR"
# The console door goes in FLAT, never in a folder of its own:
# every door then reads ONE config.json and ONE .env (it resolves both from the
# folder it sits in), and the doors are mediums rather than separate installs.
for item in tinycmdr.py tinycmdr requirements.txt README.md \
            config.example.json .env.example field-notes.md soul.md \
            skills tools install maintenance; do
    if [ -e "$SRC/$item" ]; then
        cp -a "$SRC/$item" "$INSTALL_DIR/"
    fi
done
if [ -n "$keep" ]; then
    for f in "$keep"/*; do
        # -n: a kept file must not clobber what the copy just installed -
        # that is how an old starter tool would survive an upgrade.
        if [ -e "$f" ]; then cp -an "$f" "$INSTALL_DIR/"; fi
    done
    rm -rf "$keep"
fi
mkdir -p "$INSTALL_DIR/tools" "$INSTALL_DIR/sessions"
chmod +x "$INSTALL_DIR/tinycmdr.py" 2>/dev/null || true
chmod +x "$INSTALL_DIR/tinycmdr" 2>/dev/null || true
# The verb surface on PATH (audit F12). A two-line wrapper, not a symlink: nothing
# has to resolve, it names the install dir explicitly, and removing the file IS the
# uninstall step. --no-path leaves the box untouched.
if [ "${NO_PATH:-0}" != "1" ] && [ "$INSTALL_MODE" = user ]; then
    mkdir -p "$USER_HOME/.local/bin"
    chown_to "$USER_HOME/.local" "$USER_HOME/.local/bin"
fi
if [ "${NO_PATH:-0}" != "1" ] && [ -f "$WRAPPER" ] \
        && ! grep -qF "$INSTALL_DIR" "$WRAPPER" 2>/dev/null; then
    # Same scoping rule as the macOS plist and the sudoers file: a folder OUTSIDE the
    # install dir is only touched when it is already ours. A probe install must not
    # take a working install's verb.
    info "kept the existing $WRAPPER (it belongs to another install)"
elif [ "${NO_PATH:-0}" != "1" ] && [ -d "$(dirname "$WRAPPER")" ] && { [ -w "$(dirname "$WRAPPER")" ] || [ "$(id -u)" = 0 ]; }; then
    printf '#!/bin/sh\nexec "%s/tinycmdr" "$@"\n' "$INSTALL_DIR" > "$WRAPPER"
    chmod 0755 "$WRAPPER"
    chown_to "$WRAPPER"
    info "verbs      : tinycmdr status | doctor | model | logs | restart | token"
else
    info "verbs      : not on PATH - run $INSTALL_DIR/tinycmdr (or re-run without --no-path)"
fi
if [ -f "$INSTALL_DIR/install/fleet-secrets.env" ]; then
    rm -f "$INSTALL_DIR/install/fleet-secrets.env"   # its values now live in .env
fi
info "copied $(version_of "$INSTALL_DIR/tinycmdr.py") to $INSTALL_DIR"

# --------------------------------------------------------------------- venv ---
say "python environment"
if [ ! -x "$INSTALL_DIR/venv/bin/python" ] || [ "$FORCE" = 1 ]; then
    if ! "$PY" -c 'import ensurepip' >/dev/null 2>&1; then
        if [ "$NO_DEPS" = 1 ]; then
            die "python3-venv is missing and --no-deps was given (apt install python3-venv)"
        fi
        info "installing python3-venv (apt)"
        DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-venv \
            || die "apt-get install python3-venv failed - see $LOG"
    fi
    rm -rf "$INSTALL_DIR/venv"
    "$PY" -m venv "$INSTALL_DIR/venv" || die "could not create the venv in $INSTALL_DIR"
fi
"$INSTALL_DIR/venv/bin/python" -m pip install --quiet --disable-pip-version-check \
    --upgrade pip >/dev/null 2>&1 || info "pip self-upgrade skipped (offline?)"
"$INSTALL_DIR/venv/bin/python" -m pip install --quiet --disable-pip-version-check \
    -r "$INSTALL_DIR/requirements.txt" || die "pip install failed - see $LOG"
info "python   : $("$INSTALL_DIR/venv/bin/python" -V 2>&1)"
info "mmpy_bot : $("$INSTALL_DIR/venv/bin/python" -c 'import importlib.metadata as m; print(m.version("mmpy_bot"))' 2>&1 || echo MISSING)"
info "requests : $("$INSTALL_DIR/venv/bin/python" -c 'import importlib.metadata as m; print(m.version("requests"))' 2>&1 || echo MISSING)"
"$INSTALL_DIR/venv/bin/python" -c 'import requests, mmpy_bot, croniter' \
    || die "the venv is missing a dependency (requests/mmpy_bot/croniter)"

# ------------------------------------------------------------------- config ---
say "config.json"
"$PY" - "$INSTALL_DIR" "$SRC/config.example.json" \
        "$BOT_NAME" "$MODEL_BASE_URL" "$MODEL" "$FORCE" \
        "$MM_HOST" "$MM_PORT" "$ALLOWED_USER" "$TG_IDS_CLEAN" \
        "$MODEL_BASE_GIVEN" "$MODEL_GIVEN" "$MODEL_KEY" \
        "$FALLBACK_SPECS" "$FB_ENV_LINES" <<'PY'
import json, os, sys
(inst, example, bot, base, model,
 force, mm_host, mm_port, allowed, tg_ids,
 base_given, model_given, model_key,
 fallback_specs, fb_env) = sys.argv[1:17]
cfg_path = os.path.join(inst, "config.json")
# The HOST's own config is the base whenever there is one, --force included: an
# update carries the host's settings forward and changes only what this run was
# told to change. Measured 2026-09-24 on the macOS bed: --force rebuilt the file from
# the package example, so a working install came back with a placeholder url,
# an empty allowlist and the wrong model endpoint, and its bot would not start.
fresh = not os.path.exists(cfg_path)
cfg = json.load(open(cfg_path if not fresh else example, encoding="utf-8-sig"))
mm = cfg.setdefault("mattermost", {})
if mm_host:
    mm["url"] = mm_host
if mm_port:
    mm["port"] = int(mm_port)
if allowed or fresh:
    mm["allowed_users"] = [allowed] if allowed else []
# Secrets live in .env. A token left in the template or a hand-edited config.json
# would shadow TINYCMDR_MM_TOKEN, and the bot would try to authenticate with a
# placeholder and fail.
mm["token"] = ""
llm = cfg.setdefault("llm", {})
# Only when the caller chose one: the defaults exist for a FIRST install, and
# re-applying them over a working host is how a LAN endpoint became a cloud one.
if base and (base_given or fresh):
    llm["base_url"] = base
if model and (model_given or fresh):
    llm["model"] = model
if model_key:
    # The PRIMARY's key goes to .env as TINYCMDR_LLM_API_KEY (env_map resolves it into
    # llm.api_key) - NEVER here. config.json is a file the agent reads into a prompt; a
    # key written here is a secret the model can quote. Migrate any old copy away.
    llm.pop("api_key", None)
# The third door: the TOKEN is .env-only (env_map resolves TINYCMDR_TG_TOKEN, and a
# copy in here is ignored with a warning), so only the numeric allowlist lands here.
tg = cfg.setdefault("telegram", {})
tg["token"] = ""
_ids = [i for i in (tg_ids or "").split() if i]
if _ids or fresh:
    tg["allowed_users"] = _ids
cfg.setdefault("agent", {})["bot_name"] = bot
# Extra endpoints, only when this run was told about them: a scripted update keeps the
# host's own llm.fallbacks. Each entry's key lives in .env under the name its
# api_key_env points at - the shape the build resolves for a fallback endpoint.
_specs = [s for s in (fallback_specs or "").splitlines() if s.strip()]
if _specs:
    _keys = {}
    for _line in (fb_env or "").splitlines():
        if "=" in _line:
            _k, _, _v = _line.partition("=")
            if _v.strip():
                _keys[_k.strip()] = _v.strip()
    _fbs = []
    for _spec in _specs:
        _url, _mid, _alias, _ename = [x.strip() for x in (_spec.split("|") + ["", "", "", ""])[:4]]
        if not _url:
            continue
        _entry = {"base_url": _url}
        if _mid:
            _entry["model"] = _mid
        if _alias:
            _entry["alias"] = _alias
        if _keys.get(_ename):
            _entry["api_key_env"] = _ename
        _fbs.append(_entry)
    if _fbs:
        llm["fallbacks"] = _fbs
elif fresh:
    # config.example.json carries a placeholder fallback (api.example.com, with a key
    # variable nobody has): a fresh install must not inherit an endpoint that does not
    # exist. An update keeps whatever the host already had.
    llm["fallbacks"] = []
with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
print("    config.json: %s (bot_name=%s)"
      % ("kept this host's settings" if not fresh else "written", bot))
if _specs:
    print("    fallbacks  : %d extra endpoint(s), keys in .env" % len(llm.get("fallbacks") or []))
PY
chown_to "$INSTALL_DIR/config.json"
chmod 600 "$INSTALL_DIR/config.json"

# --------------------------------------------------------------------- .env ---
"$PY" - "$INSTALL_DIR/.env" "$TOKEN" "${SECRETS_FILE:-$SRC/install/fleet-secrets.env}" "$TG_TOKEN" \
        "$FB_ENV_LINES" "${SEARCH_EGRESS:-}" "$MODEL_KEY" <<'PY'
import os, pathlib, re, sys
envp, tok, secrets, tgtok = (pathlib.Path(sys.argv[1]), sys.argv[2],
                             sys.argv[3], sys.argv[4])
fb_env = sys.argv[5] if len(sys.argv) > 5 else ""
egress = sys.argv[6] if len(sys.argv) > 6 else ""
model_key = sys.argv[7] if len(sys.argv) > 7 else ""
# The extra-endpoint keys are managed only when THIS run wrote them: a scripted update
# must carry the host's own lines over, or it drops keys its config.json points at.
# TINYCMDR_LLM_API_KEY (the primary's key) is managed the same way - a redo with no key
# keeps the host's own line; one with a key replaces it.
_managed = ["TINYCMDR_MM_TOKEN", "TINYCMDR_TG_TOKEN"]
if model_key:
    _managed.append("TINYCMDR_LLM_API_KEY")
lines, have, refused, per_bot = [], set(), [], []


def placeholder(val):
    """True for a redacted stand-in rather than a real key. A package built
    without the fleet keys carries "<redacted: TAVILY_API_KEY>"; writing that
    into .env looks like success and then 401s at every search."""
    v = val.strip()
    return (v.startswith("<") and v.endswith(">")) or "redacted" in v.lower()


if envp.exists():
    for line in envp.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key in _managed or key in have:
            continue          # installer-managed: written below, never carried over
        if fb_env.strip() and re.match(r"^TINYCMDR_ENDPOINT[0-9]+_API_KEY$", key):
            continue          # this run's own extra-endpoint key: written below
        if placeholder(line.split("=", 1)[1]):
            refused.append(key)
            continue
        have.add(key)
        lines.append(line)
if os.path.exists(secrets):
    for line in open(secrets, encoding="utf-8-sig"):
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key in ("TINYCMDR_MM_TOKEN", "TINYCMDR_TG_TOKEN"):
            # Installer-managed: written from THIS run's resolved values below. Taking
            # the file's copy too would write the same key twice, and _load_env_file
            # keeps the FIRST occurrence - so an empty managed line would win over the
            # file's real token even after the lane was chosen from it.
            continue
        if key not in ("TAVILY_API_KEY", "ANYSEARCH_API_KEY"):
            # A model key is per bot: never deploy one from a shared secrets file.
            # Hosts that took theirs from here all shared one key, and the provider's
            # dashboard showed every per-bot key as unused afterwards.
            per_bot.append(key)
            continue
        if key in have or not val.strip():
            continue
        if placeholder(val):
            refused.append(key)
            continue
        have.add(key)
        lines.append(line)
out = ["# tinycmdr secrets - per-host tokens + fleet-wide search keys.",
       "# The model key is per bot and is NOT set from any shared file: the primary's",
       "# key is TINYCMDR_LLM_API_KEY and each fallback's is whatever its api_key_env",
       "# names, and this host's own key belongs here by hand.",
       "# Never in config.json (the agent can read that file into a prompt).", "",
       f"TINYCMDR_MM_TOKEN={tok}"] \
      + ([f"TINYCMDR_TG_TOKEN={tgtok}"] if tgtok else []) \
      + ([f"TINYCMDR_LLM_API_KEY={model_key}"] if model_key else []) \
      + [l for l in (fb_env or "").splitlines() if "=" in l] \
      + ([f"TINYCMDR_SEARCH_EGRESS={egress}"] if egress else []) \
      + lines + [""]
envp.write_text("\n".join(out), encoding="utf-8")
os.chmod(envp, 0o600)
print(f"    wrote .env ({len(have) + 1} keys, mode 600)")
if per_bot:
    print("    NOTE: not copied from the secrets file: %s" % ", ".join(sorted(set(per_bot))))
    print("          a model key is per host - set this host's own in %s by hand." % envp)
if refused:
    print("    WARNING: refused %d redacted placeholder value(s): %s"
          % (len(refused), ", ".join(sorted(set(refused)))))
    print("             The package/secrets file carried no real key. Web search")
    print("             will be DEAD on this host until you put the real values in")
    print("             %s by hand, or re-run with a real install/fleet-secrets.env."
          % envp)
PY
chown_to "$INSTALL_DIR/.env"

# ------------------------------------------------------- model endpoint ---
# Ask the endpoint for its metadata once, here, with the operator watching, from the venv's
# own python - the exact binary the agent will use. Two things are learned: whether the box
# answers at all, and how many tokens it serves per request, which the agent otherwise has to
# guess from inside a run. Never fatal: a check, not a gate. (macOS has its own installer;
# there the same probe is also what raises the Local Network permission prompt in context,
# instead of from a background service where nobody can answer it.)
if [ -x "$VENV_PY" ] && [ -f "$INSTALL_DIR/config.json" ]; then
    say "model endpoint"
    PROBE_OUT="$(TINYCMDR_LLM_API_KEY="$MODEL_KEY" "$VENV_PY" - "$INSTALL_DIR/config.json" <<'PROBEPY'
import json, os, re, sys

try:
    import requests
except Exception as e:                     # --no-deps: report, never fail the install
    print("fail|could not import requests: %s" % e)
    raise SystemExit(0)

llm = (json.load(open(sys.argv[1], encoding="utf-8-sig")).get("llm") or {})
base = str(llm.get("base_url") or "").rstrip("/")
if not base:
    raise SystemExit
host = base.split("://", 1)[-1].split("/")[0].split("@")[-1].split(":")[0]
key = (str(llm.get("api_key") or "")
       or os.environ.get(str(llm.get("api_key_env") or ""), "")
       or os.environ.get("TINYCMDR_LLM_API_KEY", ""))
hdr = {"Authorization": "Bearer %s" % key} if key else {}
root = base[:-3] if base.endswith("/v1") else base
window, err = 0, ""
try:
    data = (requests.get(base + "/models", headers=hdr, timeout=4).json().get("data") or [])
    want = str(llm.get("model") or "")
    entry = next((m for m in data if m.get("id") == want), data[0] if data else {})
    window = int(entry.get("max_model_len")
                 or (entry.get("meta") or {}).get("n_ctx") or 0)
    if not window:
        props = requests.get(root + "/props", headers=hdr, timeout=4).json()
        window = int((props.get("default_generation_settings") or {}).get("n_ctx")
                     or props.get("n_ctx") or 0)
except Exception as e:
    err = " ".join(str(e).split())[:140]
print("fail|%s" % err if err else "ok|%s|%s" % (base, window))
PROBEPY
)" || true
    case "$PROBE_OUT" in
        ok\|*)  info "endpoint      : reachable (${PROBE_OUT#ok|})" ;;
        fail\|*) warn "endpoint      : did not answer: ${PROBE_OUT#fail|} - the agent retries on its own" ;;
    esac
fi

# ------------------------------------------------------------- privileges ---
# The agent administers the host it lives on, so it gets passwordless sudo by
# default. Without it the bot probes for root, hits a password prompt, and
# concludes "I have no root here" - a wrong answer it then records in notes.md
# and repeats for days. --no-sudoers skips this.
if [ "$VERIFY_ONLY" = 0 ] && [ "$UNINSTALL" = 0 ] && [ "$NO_SUDOERS" = 0 ]; then
    say "privileges"
    if [ "$INSTALL_MODE" = user ]; then
        # No grant, and none is possible without root. Say so here AND in notes.md:
        # notes.md is re-sent in every prompt, and a host that quietly lacks sudo is
        # how the bot spends a week reporting "I have no root on this box".
        info "user install: no passwordless sudo is granted (that needs root)"
        info "the agent runs as $RUN_USER and cannot sudo unattended"
        NOTES="$INSTALL_DIR/notes.md"
        if ! grep -qi 'user install' "$NOTES" 2>/dev/null; then
            printf -- '- [%s] Privileges on this host: NO passwordless sudo (user install, no root). `sudo` will prompt for a password and the shell tool has no tty. Do NOT probe with `sudo -n` or keep retrying it: a password prompt is a prompt, not proof. Root work here needs the operator, or a system install (sudo bash install-tinycmdr.sh --mode system).\n' \
                "$(date +%F)" >> "$NOTES"
            chown_to "$NOTES"
        fi
    elif [ "$(id -u)" != 0 ]; then
        info "not root: leaving sudo alone (re-run under sudo to grant passwordless sudo)"
    else
        SUDOERS_LINE="${RUN_USER} ALL=(ALL) NOPASSWD: ALL"
        SUDOERS_FILE="/etc/sudoers.d/${RUN_USER}-tinycmdr"
        # rename leftover: an upgrade left the same grant under the pre-tinycmdr
        # file name - remove it rather than keep two files for one policy
        OLD_SUDOERS_FILE="/etc/sudoers.d/${RUN_USER}-hermes"
        if [ -f "$OLD_SUDOERS_FILE" ] && grep -qxF "$SUDOERS_LINE" "$OLD_SUDOERS_FILE" 2>/dev/null; then
            rm -f "$OLD_SUDOERS_FILE"
        fi
        if [ -f "$SUDOERS_FILE" ] && grep -qxF "$SUDOERS_LINE" "$SUDOERS_FILE" 2>/dev/null; then
            info "passwordless sudo already configured: $SUDOERS_FILE"
        else
            cand="$(mktemp)"
            printf '%s\n' "$SUDOERS_LINE" > "$cand"
            # Never install an unparsed sudoers file: a syntax error can lock
            # sudo out for everyone on the host.
            if ! visudo -cf "$cand" >/dev/null 2>&1; then
                rm -f "$cand"
                die "refusing to install $SUDOERS_FILE - visudo rejected it"
            fi
            install -m 0440 -o root -g root "$cand" "$SUDOERS_FILE"
            rm -f "$cand"
            if ! visudo -c >/dev/null 2>&1; then
                rm -f "$SUDOERS_FILE"
                die "sudoers tree failed validation - removed $SUDOERS_FILE, sudo is untouched"
            fi
            info "passwordless sudo: wrote $SUDOERS_FILE (visudo clean)"
        fi
        # Record the privilege model where the agent actually reads it: notes.md
        # is re-sent to the model in every prompt.
        NOTES="$INSTALL_DIR/notes.md"
        if ! grep -qi 'nopasswd' "$NOTES" 2>/dev/null; then
            printf -- '- [%s] Privileges on this host: NOPASSWD sudo IS configured (%s). Plain `sudo <cmd>` works from the shell tool - no password, no tty. Do NOT probe with `sudo -n` on a host without it: a password prompt is a prompt, not proof of no root.\n' \
                "$(date +%F)" "$SUDOERS_FILE" >> "$NOTES"
            info "notes.md: recorded the privilege model"
        else
            info "notes.md: privilege model already recorded"
        fi
        chown_to "$NOTES"
    fi
fi

# ---------------------------------------------------------------- unit file ---
# A host with no chat account has nothing for a service to run, so it registers none: a
# lane-less service exits at once and Restart=always would loop it forever.
if [ "$HAS_LANE" = 0 ]; then
    say "no service"
    info "no chat account: no systemd unit is written, enabled or started - a service"
    info "with no lane would exit at once, and Restart=always would loop it forever."
    info "the files are installed; a session (--cli) and a one-shot (--once) work now."
    info "add a chat token and re-run to register the service:"
    info "  --token-file <file>  (Mattermost)  |  --telegram-token <t> --telegram-ids <id>"
fi
if [ "$HAS_LANE" = 1 ]; then
say "systemd unit"
# systemd does not create ~/.config/systemd/user for you (there is no tmpfiles entry for
# it), so `cat > $UNIT` failed with "No such file or directory" and `set -e` killed the
# run - AFTER the venv, config.json and .env existed and BEFORE any unit, enable or start.
# A clean Debian/Ubuntu box with no desktop session is exactly the door this install is for.
mkdir -p "$(dirname "$UNIT")"
chown_to -R "$INSTALL_DIR"      # venv and site-packages too
VENV_PY="$INSTALL_DIR/venv/bin/python"
cat > "$UNIT" <<EOF
[Unit]
Description=tinycmdr - Mattermost ops agent (${BOT_NAME})
Documentation=file://$INSTALL_DIR/README.md
StartLimitIntervalSec=0
$BOOT_DEPS

[Service]
Type=simple
$UNIT_USER_LINES
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_PY $INSTALL_DIR/tinycmdr.py $APP_ARGS
Environment="HOME=$USER_HOME"
Environment="USER=$RUN_USER"
Environment="LOGNAME=$RUN_USER"
Environment="PATH=$INSTALL_DIR/venv/bin:$USER_HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="TINYCMDR_DIR=$INSTALL_DIR"
# A FIXED delay, on purpose: systemd cannot grow one. This is the CRASH backstop - it is
# only reached once the process is gone - and it is 60s, not the 10s that respawned a bot
# 1654 times in one 4h45m outage. A lane that cannot START does not get here at all:
# tinycmdr.py retries that in-process on a growing backoff (5s to 60s).
Restart=always
RestartSec=60
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

[Install]
WantedBy=$BOOT_TARGET
EOF
info "wrote $UNIT"
sctl daemon-reload 2>/dev/null || true
sctl enable "$SERVICE_NAME" >/dev/null 2>&1 || die "systemctl enable failed"
if [ "$INSTALL_MODE" = user ]; then
    info "enabled at login: $(sctl is-enabled "$SERVICE_NAME" 2>&1)"
    # Lingering is what turns "starts when I log in" into "starts with the machine".
    # It needs root, so a no-root install may not be allowed to set it.
    if loginctl show-user "$RUN_USER" 2>/dev/null | grep -q 'Linger=yes'; then
        info "lingering      : already on, so it also starts at boot, no login needed"
    elif loginctl enable-linger "$RUN_USER" 2>/dev/null; then
        info "lingering      : enabled (starts at boot, no login needed)"
    else
        info "lingering      : not enabled (needs root) - the agent starts at your"
        info "                 next login. To start it at boot, ask for:"
        info "                 sudo loginctl enable-linger $RUN_USER"
    fi
else
    info "enabled at boot: $(sctl is-enabled "$SERVICE_NAME" 2>&1)"
fi

if [ "$NO_START" = 1 ]; then
    info "--no-start: not starting it now"
else
    say "start"
    sctl restart "$SERVICE_NAME"
    active=""
    for _ in $(seq 1 25); do
        sleep 2
        if sctl is-active --quiet "$SERVICE_NAME"; then active=1; break; fi
    done
    if [ -z "$active" ]; then
        echo "--- journal ---"
        jctl -n 40 --no-pager || true
        die "the service did not stay up - see above and $INSTALL_DIR/tinycmdr.log"
    fi
    info "active: yes (pid $(sctl show -p MainPID --value "$SERVICE_NAME"))"
fi
fi

# -------------------------------------------------------------------- check ---
if [ "$HAS_LANE" = 1 ]; then
say "check"
sleep 2
tail -n 12 "$INSTALL_DIR/tinycmdr.log" 2>/dev/null | sed 's/^/    /' || true
if [ "$CHAT_LANE" = 1 ]; then
    if grep -qE 'authenticated as|Starting bot' "$INSTALL_DIR/tinycmdr.log" 2>/dev/null; then
        info "mattermost      : connected (the log shows the bot login)"
    else
        info "mattermost      : no login line yet - journalctl ${JCTL_SCOPE}-u $SERVICE_NAME -n 50"
    fi
elif [ -n "$TG_TOKEN" ]; then
    if grep -qE 'connected as @' "$INSTALL_DIR/tinycmdr.log" 2>/dev/null; then
        info "telegram        : connected (the log shows the tg login)"
    else
        info "telegram        : no login line yet - journalctl ${JCTL_SCOPE}-u $SERVICE_NAME -n 50"
    fi
    info "                  allowlist: $TG_IDS_CLEAN"
fi
fi
if [ "${SEARCH_EGRESS:-false}" = "true" ]; then
    info "web search      : off-LAN allowed (search.allow_cloud_egress=true)"
else
    info "web search      : LAN only - an off-LAN provider is refused until"
    info "                  search.allow_cloud_egress=true. A provider on this LAN never"
    info "                  needs it: tinycmdr config set search.providers '<json>'"
fi

# spelling it out here: nesting $( ) inside a quoted echo confused bash badly enough
# that the whole summary was skipped (found by running it, not by reading it)
if [ "$HAS_LANE" = 1 ]; then
    if [ "$INSTALL_MODE" = user ]; then
        if loginctl show-user "$RUN_USER" 2>/dev/null | grep -q 'Linger=yes'; then
            MODE_LINE="user - starts at boot (lingering is on), no sudo for the agent"
        else
            MODE_LINE="user - starts at your next login, no sudo for the agent"
        fi
    else
        MODE_LINE="system - boots with the machine"
    fi

    cat <<EOF

tinycmdr is installed.

  mode     : $MODE_LINE
  active   : ${SCTL_HINT} status $SERVICE_NAME
  enabled  : $(sctl is-enabled "$SERVICE_NAME" 2>&1)
  logs     : journalctl ${JCTL_SCOPE}-u $SERVICE_NAME -f    and    $INSTALL_DIR/tinycmdr.log
  restart  : ${SCTL_HINT} restart $SERVICE_NAME
  local    : $VENV_PY $INSTALL_DIR/tinycmdr.py --once "/status"
  session  : $VENV_PY $INSTALL_DIR/tinycmdr.py --cli
  verify   : bash $INSTALL_DIR/install/install-tinycmdr.sh --verify-only --mode $INSTALL_MODE
  remove   : ${SUDO_IF_ROOT}bash $INSTALL_DIR/install/install-tinycmdr.sh --uninstall --mode $INSTALL_MODE
EOF
else
    cat <<EOF

tinycmdr is installed (files only - no chat account, so nothing runs as a service).

  lane     : none. This host has no Mattermost and no Telegram token, so there is
             nothing remote to serve and no service is registered.
  session  : $VENV_PY $INSTALL_DIR/tinycmdr.py --cli
  local    : $VENV_PY $INSTALL_DIR/tinycmdr.py --once "/status"
  add a lane later (re-run this installer; the app is not reinstalled):
    --token-file <file>                        Mattermost
    --telegram-token <t> --telegram-ids <id>   Telegram
  verify   : bash $INSTALL_DIR/install/install-tinycmdr.sh --verify-only --mode $INSTALL_MODE
  remove   : ${SUDO_IF_ROOT}bash $INSTALL_DIR/install/install-tinycmdr.sh --uninstall --mode $INSTALL_MODE
EOF
fi
