#!/usr/bin/env bash
#
# install-tinycmdr.sh - install tinycmdr on a Debian/Ubuntu host, run by systemd.
#
# On a fleet host this is the whole job:
#
#   sudo bash install-tinycmdr.sh --token <mattermost-bot-token>
#
# On your own machine, with no switches, it ASKS for what the bot cannot work without
# - the Mattermost server, your user id, the model endpoint, the model id and that
# endpoint's key - then offers a Telegram lane, "Add another endpoint?" for as many
# fallbacks as you want, and whether the page should be reachable from your network.
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
#   --web-host <addr>     the page's bind address: 0.0.0.0 (your network) or
#                         127.0.0.1 (this host only); "" keeps this host's own
#   --token <t>           Mattermost bot token (TINYCMDR_MM_TOKEN)
#   --token-file <f>      read the token from a file (first token-looking line)
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
#   --web-port <p>        local web/API fallback port (default 8787, loopback only)
#   --no-web              leave the local web port closed
#   --force               reinstall in place (stops the running service first)
#   --no-start            install and enable, do not start it now
#   --no-deps             do not touch apt (python3-venv must already be present)
#   --no-sudoers         do not grant the service user passwordless sudo
#   --verify-only         report on an existing install, change nothing, no root
#   --uninstall           stop, disable, remove the unit and the install dir
#   -h | --help           this text
#
# Everything is transcribed to /tmp/tinycmdr-install.log, so a failure always
# leaves the reason on disk.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
SERVICE_NAME="${TINYCMDR_SERVICE:-tinycmdr}"
RUN_USER="${TINYCMDR_USER:-${SUDO_USER:-$(id -un)}}"
USER_HOME="$(getent passwd "$RUN_USER" 2>/dev/null | cut -d: -f6)"
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
MODEL_BASE_GIVEN=""; MODEL_GIVEN=""; WEB_CLI_GIVEN=0
TG_TOKEN=""; TG_IDS=""
WEB_PORT="8787"; WEB_ON=1; FORCE=0; NO_START=0; NO_DEPS=0; VERIFY_ONLY=0; UNINSTALL=0; NO_SUDOERS=0
YES=0                     # -y/--yes: ask nothing, take the switches and the defaults
MODEL_KEY=""              # the model endpoint's key, when the reader gives one
FALLBACK_SPECS=""         # extra endpoints, one "url|model|alias|env-name" per line
FB_ENV_LINES=""           # their keys, as KEY=VALUE lines for .env
PAGE_HOST=""              # web.host the reader chose ("" = leave the host's own)
WEB_HOST_ARG=""           # --web-host: set it without being asked
LAN_IP=""                 # this host's first LAN address, for the reachability check
# What the questions propose when the package says nothing: the usual local
# llama.cpp shape. Any OpenAI-compatible /v1 root works.
DEFAULT_MODEL_BASE="http://127.0.0.1:8081/v1"
DEFAULT_MODEL="main"
# system | user | "" (decide from who you are). TINYCMDR_MODE is the env door, the
# --mode flag the CLI door: a fleet push sets the env one and never prompts.
INSTALL_MODE="${TINYCMDR_MODE:-}"; ASK_MODE=1; DIR_GIVEN=0; RUN_UID=""
# Generated later (in the config section), but READ earlier by the summary: under `set -u`
# an unset name there is a crash, and the token-less + Telegram-only paths both fell into it.
WEB_TOKEN=""
PORT_BUSY_BEFORE=""

usage() { sed -n '3,56p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --token)            TOKEN="$2"; shift 2 ;;
        --token-file)       TOKEN_FILE="$2"; shift 2 ;;
        --telegram-token)   TG_TOKEN="$2"; shift 2 ;;
        --telegram-ids)     TG_IDS="$2"; shift 2 ;;
        --install-dir)      INSTALL_DIR="$2"; DIR_GIVEN=1; shift 2 ;;
        --user)             RUN_USER="$2"; shift 2 ;;
        --mode)             INSTALL_MODE="$2"; shift 2 ;;
        --system)           INSTALL_MODE=system; shift ;;
        --no-root)          INSTALL_MODE=user; shift ;;
        -y|--yes)           ASK_MODE=0; YES=1; shift ;;
        --web-host)         WEB_HOST_ARG="$2"; shift 2 ;;
        --bot-name)         BOT_NAME="$2"; shift 2 ;;
        --allowed-user)     ALLOWED_ARG="$2"; shift 2 ;;
        --mattermost-url)   MM_URL_ARG="$2"; shift 2 ;;
        --model-base-url)   MODEL_BASE_URL="$2"; shift 2 ;;
        --model)            MODEL="$2"; shift 2 ;;
        --web-port)         WEB_PORT="$2"; shift 2 ;;
        --no-web)           WEB_ON=0; shift ;;
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
WEB_CLI_GIVEN=0
for _a in "$@"; do
    case "$_a" in --web-port|--no-web) WEB_CLI_GIVEN=1 ;; esac
done

say()  { printf '\n=== %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n*** %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- logging ---
# Transcribe to a log when we can write one. A log left behind by another user
# (root ran the installer earlier, then someone runs --verify-only) must not turn
# into a "tee: Permission denied" wall, and --verify-only changes nothing anyway.
if [ "$VERIFY_ONLY" = 1 ]; then
    LOG=/dev/null
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
Group=$RUN_USER"
fi

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
    port="$(cfgval web.port)"; port="${port:-8787}"
    if [ "$(sctl is-active "$SERVICE_NAME" 2>/dev/null || true)" = active ]; then
        printf '  web health   : %s\n' "$(curl -sf --max-time 5 "http://127.0.0.1:$port/api/health" || echo "no answer on port $port")"
    fi
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

say "pre-flight"
info "package      : $SRC"
info "version      : $(version_of "$SRC/tinycmdr.py")"
info "install dir  : $INSTALL_DIR"
info "mode         : $INSTALL_MODE$( [ "$INSTALL_MODE" = user ] && echo "   (your own systemd instance, starts at login, no sudo for the agent)" || echo "   (system service, boots with the machine)" )"
info "service user : $RUN_USER"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "$PY is $(python3 -V 2>&1); tinycmdr needs Python 3.10 or newer"
info "python       : $($PY -V 2>&1)  ($(command -v "$PY"))"

if [ "$UNINSTALL" = 1 ]; then
    say "uninstall"
    sctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$UNIT"
    sctl daemon-reload 2>/dev/null || true
    # The two things the install writes OUTSIDE its folder, both SCOPED: a probe
    # uninstall (--install-dir /tmp/...) must never take a real install's verb
    # wrapper or sudo grant with it (measured 2026-09-23: it ate a live install's wrapper and grant).
    if [ -f "$WRAPPER" ] && grep -qF "$INSTALL_DIR" "$WRAPPER" 2>/dev/null; then
        rm -f "$WRAPPER"
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
        info "removed $INSTALL_DIR (token, notes and history went with it)"
    fi
    info "unit $SERVICE_NAME removed"
    exit 0
fi

# ----------------------------------------------------------- asking a person ---
# Asked ONLY when a person is there to answer: a real terminal (the one-line door
# hands its own over - see install.sh) and not --yes. A scripted or headless run
# takes the defaults and prints them, so a fleet push can never hang on a question.
# TINYCMDR_ASK=1 forces the asks on a redirected stdin.
# Prompts go to STDERR: the caller reads the answer from stdout.
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

ask_secret() {   # ask_secret <prompt> -> prints what was typed, hidden, may be empty
    local a=""
    printf '    %s: ' "$1" >&2
    read -rs a || a=""
    echo >&2
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
        TOKEN="$(ask_secret "Mattermost bot token (input hidden, Enter to skip for the local page)")"
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
        MM_URL_ARG="$(ask_text "Mattermost server, no https:// (e.g. chat.example.com)" "$MM_URL_ARG")"
        ALLOWED_ARG="$(ask_text "Your Mattermost user id (optional, but without it the bot ignores your DMs)" "$ALLOWED_DFLT")"
    elif [ -z "$TG_TOKEN" ]; then
        info "no chat token: this install serves the local page only (a token can be"
        info "added later with --token-file, no reinstall of the app itself)"
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
        info "with both tokens set, Mattermost wins in this service: the Telegram lane"
        info "is a second process - $INSTALL_DIR/venv/bin/python $INSTALL_DIR/tinycmdr.py --telegram"
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
    info "the endpoint is any OpenAI-compatible /v1 root: llama.cpp, Ollama, vLLM,"
    info "or a hosted provider. Enter takes a llama.cpp on this machine."
    MODEL_BASE_URL="$(ask_text "Model endpoint" "$MODEL_BASE_DFLT")"
    MODEL="$(ask_text "Model id" "$MODEL_DFLT")"
    # A hosted endpoint wants a key. The primary's key is not env-resolved (only
    # fallback entries have api_key_env), so it lives in llm.api_key - the same home
    # the Windows installer gives it. Loopback is never asked about.
    case "$MODEL_BASE_URL" in
        *//127.0.0.1:*|*//localhost:*|*"::1"*) ;;
        *) MODEL_KEY="$(ask_secret "API key for it (blank if it needs none)")" ;;
    esac
    # The answer is THIS run's choice, so it must be written even over an existing
    # config.json (an empty "was it given" marker means "keep what the host has").
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
        _fb_url="$(ask_text "Endpoint #$_fb_n (OpenAI-compatible /v1 root)" "")"
        if [ -z "$_fb_url" ]; then
            warn "no address given - nothing added"
            _fb_n=$((_fb_n - 1))
            continue
        fi
        _fb_model="$(ask_text "Model id for it" "")"
        _fb_alias="$(ask_text "Alias, so /model <alias> switches to it (blank = none)" "")"
        _fb_name="TINYCMDR_ENDPOINT${_fb_n}_API_KEY"
        _fb_key="$(ask_secret "API key for it (blank if it needs none)")"
        FALLBACK_SPECS="${FALLBACK_SPECS}${_fb_url}|${_fb_model}|${_fb_alias}|${_fb_name}
"
        if [ -n "$_fb_key" ]; then
            FB_ENV_LINES="${FB_ENV_LINES}${_fb_name}=${_fb_key}
"
        fi
        info "endpoint #$_fb_n added: ${_fb_model:-?} at $_fb_url"
    done
fi

# ---- the local page: loopback, or reachable from your network? ----
# web.host decides it; an empty value binds every interface, and the page always needs
# its token (in .env). A page nobody can reach reads as a broken install.
if [ "$ASK_Q" = 1 ]; then
    if ask_yes "Should the page be reachable from other machines on your network?" y; then
        PAGE_HOST="0.0.0.0"
    else
        PAGE_HOST="127.0.0.1"
    fi
fi
# The switch wins over the question, and an empty value is "leave this host's own".
if [ -n "$WEB_HOST_ARG" ]; then
    PAGE_HOST="$WEB_HOST_ARG"
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
        info "how you talk : the local page on http://127.0.0.1:$WEB_PORT only"
    fi
    info "model        : $MODEL at $MODEL_BASE_URL"
    if [ -n "$MODEL_KEY" ]; then
        info "model key    : given (config.json, llm.api_key)"
    fi
    _fb_count=0
    for _s in $FALLBACK_SPECS; do _fb_count=$((_fb_count + 1)); done
    if [ "$_fb_count" -gt 0 ]; then
        info "more endpoints: $_fb_count (tried in order when the primary fails)"
    fi
    if [ -n "$TG_TOKEN" ]; then
        info "telegram     : on, DMs from $TG_IDS_CLEAN"
    fi
    if [ "$WEB_ON" = 1 ]; then
        info "local page   : port $WEB_PORT, ${PAGE_HOST:-all interfaces}"
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

# A chat account is OPTIONAL. The harness also runs as a session (--cli) and as a local page
# (--web, 127.0.0.1:8787). With no token there is no chat lane, so the service runs the PAGE:
# running the chat lane here would exit at once (tinycmdr.py refuses to start without a token,
# on purpose) and Restart=always would loop it forever.
APP_ARGS=""
CHAT_LANE=1
TG_LANE=0
if [ -n "$TG_TOKEN" ]; then TG_LANE=1; fi
if [ -z "$TOKEN" ] && [ "$TG_LANE" = 1 ]; then
    # The Telegram lane starts by itself with no Mattermost token, so the service runs
    # the BOT here (no --web): this is a chat lane, and calling it "without a chat
    # account" put a local page where a DM should have been answered.
    CHAT_LANE=0
    info "no Mattermost token, but a Telegram one: the service runs the TELEGRAM lane"
    info "allowlist    : $TG_IDS_CLEAN"
elif [ -z "$TOKEN" ]; then
    CHAT_LANE=0
    APP_ARGS="--web"
    info "no Mattermost bot token: installing WITHOUT a chat account"
    info "the service will serve the local page: http://127.0.0.1:${WEB_PORT}"
    info "a session needs no service at all:   $VENV_PY $INSTALL_DIR/tinycmdr.py --cli"
    info "add a chat account later: re-run this installer with --token-file <file>"
elif [ "$TG_LANE" = 1 ]; then
    info "both tokens are set: Mattermost wins in this process, so Telegram needs"
    info "  $VENV_PY $INSTALL_DIR/tinycmdr.py --telegram   (its own unit, not this one)"
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
if [ -z "$MM_HOST" ]; then
    if [ "$CHAT_LANE" = 0 ]; then
        # No lane here reads mattermost.url (Telegram-only or the local page), so
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
if [ "$WEB_ON" = 1 ]; then
    info "local page   : port ${WEB_PORT} (web.host: ${PAGE_HOST:-as this host has it})"
fi
info "bot name     : ${BOT_NAME}"
if [ "$WEB_ON" = 1 ]; then
    info "web fallback : http://127.0.0.1:${WEB_PORT}"
else
    info "web fallback : disabled"
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
    for f in .env config.json notes.md notes-archive.md tasks.json tasks.md \
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
for item in tinycmdr.py tinycmdr-supervise.py tinycmdr requirements.txt README.md \
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
    chown "$RUN_USER:$RUN_USER" "$USER_HOME/.local" "$USER_HOME/.local/bin" 2>/dev/null || true
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
    chown "$RUN_USER:$RUN_USER" "$WRAPPER" 2>/dev/null || true
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
# The page token is a secret like the bot token, so it is written to .env (one secrets
# file per install) instead of into config.json or a loose .txt in the folder.
WEB_TOKEN=""
if [ "$WEB_ON" = "1" ]; then
    WEB_TOKEN="$("$PY" -c 'import secrets;print(secrets.token_hex(24))')"
    # no ?token= link (security review 2026-09-23): the token in a URL lands in
    # the request line, browser history and any proxy log, and it is shell and
    # code execution on this box. The page prompts for it; print it for paste.
    # Here and not in the earlier summary: that block runs before this mints the
    # token, so its old ready-link line never printed at all (probe, 2026-09-23).
    info "page token   : ${WEB_TOKEN}   (paste it when the page asks)"
    info "               (also in .env: TINYCMDR_WEB_TOKEN)"
fi
"$PY" - "$INSTALL_DIR" "$SRC/config.example.json" \
        "$BOT_NAME" "$MODEL_BASE_URL" "$MODEL" "$WEB_PORT" "$WEB_ON" "$FORCE" \
        "$MM_HOST" "$MM_PORT" "$ALLOWED_USER" "$TG_IDS_CLEAN" \
        "$MODEL_BASE_GIVEN" "$MODEL_GIVEN" "$WEB_CLI_GIVEN" "$MODEL_KEY" \
        "$FALLBACK_SPECS" "$FB_ENV_LINES" "$PAGE_HOST" <<'PY'
import json, os, sys
(inst, example, bot, base, model, webport, webon,
 force, mm_host, mm_port, allowed, tg_ids,
 base_given, model_given, web_given, model_key,
 fallback_specs, fb_env, page_host) = sys.argv[1:20]
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
    # Only when one was given: an update with no switch keeps whatever this host
    # already has, and a model key is per host. A hosted PRIMARY has no api_key_env
    # of its own, so config.json is where the build looks for it.
    llm["api_key"] = model_key
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
web = cfg.setdefault("web", {})
if web_given or fresh:
    # The token is a SECRET, so it goes to .env (TINYCMDR_WEB_TOKEN) with the bot
    # token: one file to look in, and nothing loose in the install folder.
    web["enabled"] = webon == "1"
    if webon == "1":
        web["port"] = int(webport or 8787)
        # The bind address, WRITTEN OUT: the build reads an empty value as 0.0.0.0
        # ("every interface"), and a reader opening config.json should not have to
        # know that. The reader's answer wins when there was one, else the host's own
        # value stands (an update must not move a working page onto the network).
        if page_host:
            web["host"] = page_host
        elif not str(web.get("host") or "").strip():
            web["host"] = "0.0.0.0"
web["token"] = ""
with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
print("    config.json: %s (bot_name=%s, web=%s)"
      % ("kept this host's settings" if not fresh else "written",
         bot, "on" if webon == "1" else "off"))
if _specs:
    print("    fallbacks  : %d extra endpoint(s), keys in .env" % len(llm.get("fallbacks") or []))
if page_host:
    print("    web page   : bound to %s:%s" % (page_host, web.get("port")))
PY
chown "$RUN_USER:$RUN_USER" "$INSTALL_DIR/config.json"
chmod 644 "$INSTALL_DIR/config.json"

# --------------------------------------------------------------------- .env ---
"$PY" - "$INSTALL_DIR/.env" "$TOKEN" "$SRC/install/fleet-secrets.env" "$WEB_TOKEN" "$TG_TOKEN" \
        "$FB_ENV_LINES" <<'PY'
import os, pathlib, re, sys
envp, tok, secrets, webtok, tgtok = (pathlib.Path(sys.argv[1]), sys.argv[2],
                                     sys.argv[3], sys.argv[4], sys.argv[5])
fb_env = sys.argv[6] if len(sys.argv) > 6 else ""
# The extra-endpoint keys are managed only when THIS run wrote them: a scripted update
# must carry the host's own lines over, or it drops keys its config.json points at.
_managed = ("TINYCMDR_MM_TOKEN", "TINYCMDR_TG_TOKEN", "TINYCMDR_WEB_TOKEN")
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
        if key not in ("TINYCMDR_MM_TOKEN", "TINYCMDR_WEB_TOKEN",
                       "TAVILY_API_KEY", "ANYSEARCH_API_KEY"):
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
       "# The model key is per bot and is NOT set from any shared file: the variable",
       "# is whatever your llm.fallbacks entry names in api_key_env, and this host's",
       "# own key belongs here by hand.",
       "# Never in config.json (the agent can read that file into a prompt).", "",
       f"TINYCMDR_MM_TOKEN={tok}"] \
      + ([f"TINYCMDR_TG_TOKEN={tgtok}"] if tgtok else []) \
      + ([f"TINYCMDR_WEB_TOKEN={webtok}"] if webtok else []) \
      + [l for l in (fb_env or "").splitlines() if "=" in l] \
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
chown "$RUN_USER:$RUN_USER" "$INSTALL_DIR/.env"

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
            chown "$RUN_USER:$RUN_USER" "$NOTES" 2>/dev/null || true
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
        chown "$RUN_USER:$RUN_USER" "$NOTES" 2>/dev/null || true
    fi
fi

# ---------------------------------------------------------------- unit file ---
say "systemd unit"
chown -R "$RUN_USER:$RUN_USER" "$INSTALL_DIR"      # venv and site-packages too
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
Restart=always
RestartSec=10
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
    # If something already listens on the local web port, the health check below
    # would report THAT process, not this install. Note it before we start.
    if [ "$WEB_ON" = 1 ]; then
        pnow="$(cfgval web.port)"; pnow="${pnow:-8787}"
        if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null \
                | grep -qE "[:.]${pnow}[[:space:]]"; then
            PORT_BUSY_BEFORE="$pnow"
        fi
    fi
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

# -------------------------------------------------------------------- check ---
say "check"
sleep 2
tail -n 12 "$INSTALL_DIR/tinycmdr.log" 2>/dev/null | sed 's/^/    /' || true
port="$(cfgval web.port)"; port="${port:-8787}"
if [ "$WEB_ON" = 1 ]; then
    health="$(curl -sf --max-time 5 "http://127.0.0.1:$port/api/health" || true)"
    if [ -n "$health" ]; then
        info "web /api/health : $health"
        # Loopback answering says nothing about the address a reader will actually
        # type: a page bound to 127.0.0.1 and one behind a host firewall look the
        # same from here, and both read as "the installer did not set up the page"
        # (measured 2026-09-26 on a fleet macOS host: "nothing reachable at <lan-ip>:8787").
        LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        LAN_IP="${LAN_IP:-}"
        if [ -z "$LAN_IP" ]; then
            info "no LAN address on this host, so the page is reachable here only"
        elif curl -sf --max-time 5 "http://$LAN_IP:$port/api/health" >/dev/null 2>&1; then
            info "page            : http://$LAN_IP:$port  (paste the token from .env)"
        else
            info "page            : answers on 127.0.0.1 but NOT on http://$LAN_IP:$port"
            if [ "$(cfgval web.host)" = "127.0.0.1" ]; then
                info "                  web.host is 127.0.0.1 (loopback only) by design:"
                info "                  set it to 0.0.0.0 in $INSTALL_DIR/config.json and restart"
                info "                  the service to open the page to your network."
            elif command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet ufw; then
                info "                  ufw is active:  sudo ufw allow $port/tcp"
            elif command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
                info "                  firewalld is active:  sudo firewall-cmd --add-port=$port/tcp --permanent"
                info "                                        sudo firewall-cmd --reload"
            else
                info "                  check this host's firewall and any router between you."
            fi
        fi
    else
        info "web port $port did not answer yet (the bot runs anyway; the page is a fallback)"
    fi
    if [ -n "$PORT_BUSY_BEFORE" ]; then
        info "NOTE            : port $PORT_BUSY_BEFORE was already in use BEFORE this"
        info "                  install, so that answer (and the local page) may belong"
        info "                  to another process. Re-run with --web-port <free port>"
        info "                  if you want the page on this host."
    fi
fi
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
else
    info "chat            : none (no token given) - the page is the door"
fi

# spelling it out here: nesting $( ) inside a quoted echo confused bash badly enough
# that the whole summary was skipped (found by running it, not by reading it)
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
  page     : http://${LAN_IP:-127.0.0.1}:$WEB_PORT   (token in .env: TINYCMDR_WEB_TOKEN)
             by hand: $VENV_PY $INSTALL_DIR/tinycmdr.py --web
  verify   : bash $INSTALL_DIR/install/install-tinycmdr.sh --verify-only --mode $INSTALL_MODE
  remove   : ${SUDO_IF_ROOT}bash $INSTALL_DIR/install/install-tinycmdr.sh --uninstall --mode $INSTALL_MODE
EOF
