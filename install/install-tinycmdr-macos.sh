#!/usr/bin/env bash
#
# install-tinycmdr-macos.sh - install tinycmdr on a Mac, run by launchd.
#
# On a Mac this is the whole job:
#
#   bash install-tinycmdr-macos.sh
#
# With no switches it ASKS for what the bot cannot work without - the Mattermost
# server, your user id, a Telegram lane if you want one, the model endpoint and its
# key, "Add another endpoint?" for as many fallbacks as you like, and whether the
# page should be reachable from your network - and writes nothing until you say yes.
# Every answer has a switch; pass them (or -y) and it asks nothing. Tokens are read
# at hidden prompts, so they never have to enter your shell history.
#
# It builds a venv, writes config.json from config.example.json (plus
# install/fleet-defaults.json when it is present), keeps the bot token out of
# config.json (it goes to .env, mode 600), and registers a per-user launchd
# agent that starts at login and comes back if it dies.
#
# Nothing here needs root: the agent lives in ~/Library/LaunchAgents and the bot
# runs as you.
#
#   --token <t>           Mattermost bot token (TINYCMDR_MM_TOKEN)
#   --token-file <f>      read the token from a file (first non-empty line)
#   --telegram-token <t>  Telegram bot token (TINYCMDR_TG_TOKEN) - the third door, DMs
#                         only; with no Mattermost token the agent runs THIS lane
#   --telegram-ids <i>    numeric Telegram id(s), comma or space separated
#   --allowed-user <id>   Mattermost user id allowed to command the bot
#   --mattermost-url <h>  Mattermost host, no scheme (default: fleet-defaults.json)
#   --install-dir <d>     default ~/tinycmdr
#   --bot-name <n>        agent.bot_name (default: this Mac's hostname)
#   --model-base-url <u>  llm.base_url (default: a llama.cpp on this machine, or
#                         answered at the prompt; --use-fleet-model for the LAN box)
#   --model <m>           llm.model (default: main)
#   --use-fleet-model     take llm.base_url/model from fleet-defaults.json instead
#                         (i.e. the LAN model endpoint)
#   --web-port <p>        local web/API port (default 8787, loopback only)
#   --no-web              leave the local web port closed
#   --python <path>       interpreter to build the venv from (default: 3.12, else 3.11/3.10)
#   --install-python      fetch a private python 3.12 with uv when none is here
#                         (the installer also OFFERS this when it finds no 3.10-3.12)
#   --label <l>           launchd label (default com.tinycmdr.agent)
#   --secrets-file <f>    extra KEY=VALUE lines for .env (search keys etc)
#   --no-launchd          install the files only; do not register the agent
#                         (also the way to dry-run this installer off macOS)
# The questions, in order: the bot token, the Mattermost server and your user id,
# a Telegram lane (optional), the model endpoint and its key, "Add another
# endpoint?" for as many fallbacks as you want, and whether the page should be
# reachable from your network. Nothing is written until you answer "Install now?".
#
#   --telegram-token <t>  answer for the Telegram question without being asked
#   --allowed-user <id>   answer for your Mattermost user id
#   --web-host <addr>     the page's bind address: 0.0.0.0 (your network) or
#                         127.0.0.1 (this machine only); "" keeps this host's own
#   -y | --yes            ask nothing: take the switches above and the defaults
#                         (the questions are asked only at a terminal, and a
#                          piped or scripted run takes the defaults instead)
#   --force               reinstall in place (boots out the agent first)
#   --no-start            register the agent, do not start it now
#   --verify-only         report on an existing install, change nothing
#   --uninstall           stop the agent, remove it and the install dir
#   -h | --help           this text
#
# Everything is transcribed to $TMPDIR/tinycmdr-install.log, so a failure always
# leaves the reason on disk.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
INSTALL_DIR="${TINYCMDR_DIR:-$HOME/tinycmdr}"
LABEL="${TINYCMDR_LABEL:-com.tinycmdr.agent}"
LOGDIR="$INSTALL_DIR/logs"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/$LABEL.plist"
LOG="${TINYCMDR_INSTALL_LOG:-${TMPDIR:-/tmp}/tinycmdr-install.log}"
PY_ARG=""
DEFAULTS="$SRC/install/fleet-defaults.json"
# No provider is named here on purpose: this is the usual local llama.cpp shape,
# and any OpenAI-compatible endpoint works.
DEFAULT_MODEL_BASE="http://127.0.0.1:8081/v1"
DEFAULT_MODEL="main"

TOKEN=""; TOKEN_FILE=""; BOT_NAME=""; MODEL_BASE_URL=""; MODEL=""; ALLOWED_ARG=""
TG_TOKEN=""; TG_IDS=""
MM_URL_ARG=""; SECRETS_FILE=""
WEB_PORT="8787"; WEB_ON=1; FORCE=0; NO_START=0; VERIFY_ONLY=0; UNINSTALL=0
# Generated later (in the config section), but READ earlier by the no-token branch: under
# `set -u` an unset name there is a crash.
WEB_TOKEN=""
NO_LAUNCHD=0; FORCE_PYTHON=0; USE_FLEET_MODEL=0; INSTALL_PYTHON=0; YES=0
# Filled by the questions (or the switches) and written into config.json.
MODEL_KEY=""; VERB_PATH=""; PATH_ADDED=""
WEB_HOST_ARG=""        # --web-host: the page's bind address, without being asked
# Extra endpoints (llm.fallbacks) and where the page may be reached from.
FALLBACK_SPECS=""; FB_ENV_LINES=""; PAGE_HOST=""; LAN_IP=""

usage() { sed -n '3,65p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --token)           TOKEN="$2"; shift 2 ;;
        --token-file)      TOKEN_FILE="$2"; shift 2 ;;
        --telegram-token)  TG_TOKEN="$2"; shift 2 ;;
        --telegram-ids)    TG_IDS="$2"; shift 2 ;;
        --allowed-user)    ALLOWED_ARG="$2"; shift 2 ;;
        --mattermost-url)  MM_URL_ARG="$2"; shift 2 ;;
        --install-dir)     INSTALL_DIR="$2"; LOGDIR="$INSTALL_DIR/logs"; shift 2 ;;
        --bot-name)        BOT_NAME="$2"; shift 2 ;;
        --model-base-url)  MODEL_BASE_URL="$2"; shift 2 ;;
        --model)           MODEL="$2"; shift 2 ;;
        --web-port)        WEB_PORT="$2"; shift 2 ;;
        --no-web)          WEB_ON=0; shift ;;
        --python)          PY_ARG="$2"; shift 2 ;;
        --use-fleet-model) USE_FLEET_MODEL=1; shift ;;
        --label)           LABEL="$2"; PLIST="$PLIST_DIR/$LABEL.plist"; shift 2 ;;
        --secrets-file)    SECRETS_FILE="$2"; shift 2 ;;
        --no-launchd)      NO_LAUNCHD=1; shift ;;
        --force)           FORCE=1; shift ;;
        -y|--yes)          YES=1; shift ;;
        --web-host)        WEB_HOST_ARG="$2"; shift 2 ;;
        --no-start)        NO_START=1; shift ;;
        --no-path)         NO_PATH=1; shift ;;
        --verify-only)     VERIFY_ONLY=1; shift ;;
        --uninstall)       UNINSTALL=1; shift ;;
        --force-python)    FORCE_PYTHON=1; shift ;;
        --install-python)  INSTALL_PYTHON=1; shift ;;
        -h|--help)         usage; exit 0 ;;
        *) echo "install-tinycmdr-macos.sh: unknown switch '$1'" >&2; usage >&2; exit 2 ;;
    esac
done

say()  { printf '\n=== %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    ! %s\n' "$*" >&2; }
die()  { printf '\n*** %s\n' "$*" >&2; exit 1; }

file_mode() {   # permission bits: BSD stat on macOS, GNU stat elsewhere
    if stat -f '%Lp' "$1" >/dev/null 2>&1; then stat -f '%Lp' "$1"; else stat -c '%a' "$1"; fi
}

app_version() {   # the version in the app file, for telling OUR bot from another one
    [ -f "$1" ] || return 0
    grep -m1 '^VERSION = ' "$1" 2>/dev/null | cut -d'"' -f2
}

# ----------------------------------------------------------- asking a person ---
# Asked ONLY when a person is there to answer: a real terminal (the one-line door
# hands its own over - see install.sh) and not --yes. A scripted or headless run
# takes the defaults and prints them instead, so a fleet push can never hang on a
# question. TINYCMDR_ASK=1 forces the asks on a redirected stdin.
trim() {   # trim() <string> -> the same string without leading/trailing spaces
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    printf '%s' "${s%"${s##*[![:space:]]}"}"
}

# EVERY question goes to STDERR. The caller reads the answer from stdout
# (X="$(ask_text ...)"), so a prompt printed to stdout is captured as the answer
# itself - measured on the first probe: mattermost.url came back as the literal
# string "    Mattermost server, no https:// (e.g. chat.example.com): mm.probe.local".
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

IS_MAC=0
[ "$(uname -s)" = "Darwin" ] && IS_MAC=1

if [ "$VERIFY_ONLY" = 1 ]; then
    LOG=/dev/null
fi
if [ "$LOG" = "/dev/null" ]; then
    :                                  # --verify-only changes nothing, so it writes no log
elif [ -e "$LOG" ]; then
    # A log left behind by another user (root ran this earlier, then someone else
    # ran it again) must not turn into a "tee: Permission denied" wall.
    [ -w "$LOG" ] && exec > >(tee -a "$LOG") 2>&1
elif [ -d "$(dirname "$LOG")" ] && [ -w "$(dirname "$LOG")" ]; then
    exec > >(tee -a "$LOG") 2>&1
fi
printf '\n########## install-tinycmdr-macos.sh %s  (%s, %s)\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$(hostname)" "$(uname -sr)"

if [ "$UNINSTALL" = 1 ]; then
    say "uninstall"
    # SCOPED, like the wrapper below and like the Linux installer's cleanup. A probe
    # install (--install-dir /tmp/..., --no-launchd) shares the DEFAULT label with a
    # real install, so an unscoped `bootout` + `rm` here stopped a live agent and
    # deleted its plist: measured 2026-09-24 on the macOS bed, where a probe uninstall
    # took the running bot down with it. The plist is removed only when it names
    # THIS install directory.
    if [ "$IS_MAC" = 1 ] && [ -f "$PLIST" ]; then
        if grep -qF "$INSTALL_DIR" "$PLIST" 2>/dev/null; then
            launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null \
                || launchctl unload -w "$PLIST" 2>/dev/null || true
            rm -f "$PLIST"
            info "removed $PLIST"
        else
            info "kept $PLIST - it belongs to another install (not $INSTALL_DIR)"
        fi
    fi
    # the PATH wrappers the install wrote outside its folder - /usr/local/bin when it
    # was writable, else ~/.local/bin. Only the one that points at THIS install goes
    # (a probe uninstall must not take the real one's wrapper).
    for _wrap in /usr/local/bin/tinycmdr "$HOME/.local/bin/tinycmdr"; do
        [ -f "$_wrap" ] || continue
        grep -qF "$INSTALL_DIR" "$_wrap" 2>/dev/null || continue
        # A wrapper written by a sudo install is root-owned inside a root-owned directory,
        # so a user-mode uninstall cannot unlink it. Under `set -e` the bare `rm -f` that
        # used to sit here aborted the WHOLE script at this line - measured 2026-09-25 on a
        # fleet macOS host: the rm printed "Permission denied", the shell exited 1, and the
        # `rm -rf $INSTALL_DIR` below never ran, so the uninstall left the install folder
        # behind and told the reader nothing. Never fatal now: try, then say what is left.
        rm -f "$_wrap" 2>/dev/null || true
        if [ -f "$_wrap" ]; then
            warn "$_wrap is owned by root and could not be removed here."
            warn "finish that one line by hand:  sudo rm -f $_wrap"
        else
            info "removed $_wrap"
        fi
    done
    # the PATH line this installer added to a shell profile, and only that line
    for _pf in "$HOME/.zshrc" "$HOME/.bash_profile"; do
        [ -f "$_pf" ] || continue
        grep -q '^# tinycmdr$' "$_pf" 2>/dev/null || continue
        _tmp="$_pf.tinycmdr-tmp"
        if grep -v -e '^# tinycmdr$' -e '^export PATH="\$HOME/\.local/bin:\$PATH"$' "$_pf" > "$_tmp" 2>/dev/null \
                && mv "$_tmp" "$_pf" 2>/dev/null; then
            info "removed the ~/.local/bin PATH line from $_pf"
        else
            rm -f "$_tmp" 2>/dev/null || true
            warn "could not edit $_pf - remove the '# tinycmdr' line from it by hand"
        fi
    done
    if [ -d "$INSTALL_DIR" ]; then
        info "removing $INSTALL_DIR"
        rm -rf "$INSTALL_DIR"
    fi
    info "done. The Mattermost bot account still exists; the token in it is unused"
    info "now - revoke it in Profile > Security > Personal Access Tokens if you want it gone."
    exit 0
fi

if [ "$IS_MAC" != 1 ] && [ "$NO_LAUNCHD" != 1 ] && [ "$VERIFY_ONLY" != 1 ]; then
    die "this installer is for macOS (launchd). Off macOS, pass --no-launchd to install the
files and venv only, or use install/install-tinycmdr.sh on Linux. --verify-only is allowed
anywhere because it inspects an existing install and changes nothing."
fi

# ---------------------------------------------------------------- python ---
# The interpreter matters: mmpy_bot resolves to 2.2.1 on 3.10-3.12, and to an
# ancient broken release on 3.13+, where the bot starts and never connects.
tty_ask() {   # a real terminal, and the answer is yes: nothing is ever fetched from a pipe
    [ -t 0 ] || return 1
    printf '    %s [Y/n] ' "$1"
    local a=""
    read -r a || return 1
    case "$a" in ""|y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

fetch_python() {
    # A python for THIS install, fetched with uv INTO THE INSTALL FOLDER, so
    # uninstalling the folder takes it away again and the interpreter is never
    # shared with anything else. uv's own download cache goes to ~/.cache/uv
    # (reusable, unlike the interpreter); nothing else touches $HOME, no
    # password, no Homebrew, no system change. Measured on an M-series Mac:
    # about a second for the uv bootstrap, 943 ms for the interpreter, 71 MB
    # on disk, and a venv built on it pip-installs mmpy_bot 2.2.1 and runs the
    # shipped build (rc=0).
    # STDOUT IS THE RESULT: every progress line goes to stderr, or the caller's
    # PY="$(fetch_python)" captures chatter and then cannot run it.
    local tools="$INSTALL_DIR/.tools" pydir="$INSTALL_DIR/.python" uv="" boot=""
    mkdir -p "$tools" "$pydir" || die "could not create the python folders in $INSTALL_DIR"
    uv="$(command -v uv 2>/dev/null || true)"
    local c
    for c in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
        [ -n "$uv" ] && break
        [ -x "$c" ] && uv="$c"
    done
    if [ ! -x "$uv" ]; then
        printf '    %s\n' "uv is not here either: fetching it first (a copy inside $tools)" >&2
        boot="$(mktemp -t tinycmdr-uv)"
        curl -fsSL https://astral.sh/uv/install.sh -o "$boot" \
            || die "could not download uv. Install a python yourself (brew install python@3.12,
or python.org), then re-run with --python <its path>."
        UV_INSTALL_DIR="$tools" UV_NO_MODIFY_PATH=1 sh "$boot" >/dev/null 2>&1 \
            || die "the uv bootstrap failed - its output is in $LOG"
        rm -f "$boot"
        uv="$tools/uv"
    fi
    [ -x "$uv" ] || die "uv did not install"
    printf '    %s\n' "fetching python 3.12 with $uv" >&2
    UV_PYTHON_INSTALL_DIR="$pydir" "$uv" python install --no-bin 3.12 \
        || die "uv could not fetch python 3.12 - its output is in $LOG"
    UV_PYTHON_INSTALL_DIR="$pydir" "$uv" python find 3.12 \
        || die "uv installed nothing findable under $pydir"
}

pick_python() {
    if [ -n "$PY_ARG" ]; then
        [ -x "$PY_ARG" ] || die "--python $PY_ARG is not executable"
        echo "$PY_ARG"; return 0
    fi
    local cand
    for cand in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 python3.12 \
                /opt/homebrew/bin/python3.11 python3.11 \
                /opt/homebrew/bin/python3.10 python3.10 python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            echo "$(command -v "$cand")"; return 0
        fi
    done
    echo ""                      # nothing at all: check_python can fetch one
}

# Validates $PY and REPLACES it when this machine has no usable interpreter and a
# download is allowed (--install-python, or a yes at the prompt).
check_python() {
    local v tries=0
    while :; do
        if [ -z "$PY" ]; then
            v="none found"
        else
            v="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" \
                || die "$PY could not run"
        fi
        case "$v" in
            3.10|3.11|3.12)
                info "python: $PY ($v)"
                return 0 ;;
            3.13|3.14|3.15|3.16|3.17|3.18|3.19|3.2[0-9])
                if [ "$FORCE_PYTHON" = 1 ]; then
                    warn "python $v is newer than anything this was tested on (--force-python)"
                    info "python: $PY ($v)"
                    return 0
                fi
                die "python $v resolves an ancient, broken mmpy_bot (the bot starts and never
connects). Use 3.12:  brew install python@3.12   then re-run with --python
/opt/homebrew/bin/python3.12   (or pass --force-python to try anyway)" ;;
            *)
                if [ "$tries" = 0 ] && [ -z "$PY_ARG" ] \
                   && { [ "$INSTALL_PYTHON" = 1 ] || tty_ask "no python 3.10-3.12 on this Mac ($v). Fetch a private python 3.12 now (uv, no password, ~66 MB)?"; }; then
                    say "python"
                    tries=1
                    PY="$(fetch_python)"
                    continue
                fi
                die "python $v is not supported (need 3.10, 3.11 or 3.12).
Install one with:  brew install python@3.12   (or python.org), then re-run with --python <its path>,
or let this installer fetch one for you:  --install-python" ;;
        esac
    done
}

if [ "$VERIFY_ONLY" != 1 ]; then
    PY="$(pick_python)"
    check_python                 # may replace $PY with a freshly fetched one
fi

# ---------------------------------------------------------------- json helpers ---
jget() {   # jget <json-file> <key> -> value or empty
    if [ ! -f "$1" ]; then return 0; fi
    "$PY" - "$1" "$2" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8-sig"))
except Exception:
    sys.exit(0)
v = d.get(sys.argv[2])
print("" if v is None else v)
PY
}

cfgval_mac() {   # cfgval_mac <section>.<key> -> the value in THIS install's config.json
    # The DEFAULTS the questions propose on an update are the host's own values, so
    # an update that is answered with Enter changes nothing. The example's
    # placeholders are never proposed, and a placeholder user id is nobody.
    [ -f "$INSTALL_DIR/config.json" ] || return 0
    "$PY" - "$INSTALL_DIR/config.json" "$1" <<'PY'
import json, sys
sec, key = sys.argv[2].split(".", 1)
try:
    d = json.load(open(sys.argv[1], encoding="utf-8-sig"))
except Exception:
    sys.exit(0)
v = (d.get(sec) or {}).get(key)
if isinstance(v, list):
    v = [x for x in v if x and not str(x).startswith("REPLACE_WITH")]
    v = ", ".join(str(x) for x in v)
if v in (None, "", "chat.example.com", "CHANGE-ME.example.com"):
    sys.exit(0)
print(v)
PY
}

# ---------------------------------------------------------------- verify only ---
if [ "$VERIFY_ONLY" = 1 ]; then
    say "verify-only: $INSTALL_DIR"
    [ -f "$INSTALL_DIR/tinycmdr.py" ] && info "app: present" || die "no tinycmdr.py in $INSTALL_DIR"
    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        info "venv: $("$INSTALL_DIR/venv/bin/python" -c 'import sys; print(sys.version.split()[0])')"
        "$INSTALL_DIR/venv/bin/python" -c 'import requests, mmpy_bot, croniter' \
            && info "deps: requests, mmpy_bot, croniter import cleanly" \
            || warn "a dependency does not import - run pip install -r requirements.txt"
    else
        warn "no venv at $INSTALL_DIR/venv"
    fi
    [ -f "$INSTALL_DIR/config.json" ] && info "config.json: present" || warn "no config.json"
    if [ -x /usr/local/bin/tinycmdr ] || [ -x "$HOME/.local/bin/tinycmdr" ]; then
        info "verb: tinycmdr is on PATH"
    else
        warn "no tinycmdr command on PATH (use $INSTALL_DIR/tinycmdr, or re-install without --no-path)"
    fi
    if [ -f "$INSTALL_DIR/.env" ]; then
        info ".env: present ($(file_mode "$INSTALL_DIR/.env") permissions)"
        grep -q '^TINYCMDR_MM_TOKEN=..' "$INSTALL_DIR/.env" && info ".env: bot token is set" \
            || warn ".env: TINYCMDR_MM_TOKEN is missing or empty"
    else
        warn "no .env (the bot cannot authenticate without it)"
    fi
    if [ "$IS_MAC" = 1 ] && [ -f "$PLIST" ]; then
        if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
            info "launchd: $LABEL is loaded"
        else
            warn "launchd: $PLIST exists but $LABEL is not loaded"
        fi
    fi
    if command -v curl >/dev/null 2>&1; then
        want="$(app_version "$INSTALL_DIR/tinycmdr.py")"
        for p in "$WEB_PORT" 8788; do
            body="$(curl -fsS --max-time 3 "http://127.0.0.1:$p/api/health" 2>/dev/null || true)"
            [ -n "$body" ] || continue
            case "$body" in
                *"$want"*)
                    info "health: http://127.0.0.1:$p/api/health is this build ($want)";;
                *)
                    warn "port $p answers, but not with this build (expected version $want):"
                    warn "something else is listening there - check with: lsof -nP -iTCP:$p -sTCP:LISTEN";;
            esac
            info "$body" | head -c 400
            echo
            break
        done
    fi
    info "verify-only changed nothing"
    exit 0
fi

# ---------------------------------------------------------------- install ---
if [ -e "$INSTALL_DIR/tinycmdr.py" ] && [ "$FORCE" != 1 ]; then
    die "$INSTALL_DIR already holds a tinycmdr. Re-run with --force to reinstall in place."
fi

# ---------------------------------------------------------------- questions ---
# A reader who downloaded this and ran it knows two things at most: the server
# their Mattermost lives on and a bot token. Everything else the bot cannot work
# without - where the server is, who may command it, which model answers and that
# model's key - used to be a switch they had to know about, and the installs that
# came out of the one-line door were dead on arrival (measured 2026-09-26 on a
# fleet macOS host: the page token printed, no host, no endpoint, no key, and the
# agent exited at its first start because chat.example.com does not resolve).
# So ask, HERE, before a single file is written: answering is then all a reader
# has to do, and "no" leaves the disk untouched.
ASK=1
{ [ -t 0 ] && [ "$YES" != 1 ]; } || ASK=0
[ -n "${TINYCMDR_ASK:-}" ] && ASK=1

# The bot token: --token, --token-file and the .env this install already has all
# win, and a run with none of them is the local-page lane (below), not an error.
if [ -z "$TOKEN" ] && [ -n "$TOKEN_FILE" ]; then
    [ -f "$TOKEN_FILE" ] || die "--token-file $TOKEN_FILE does not exist"
    TOKEN="$(grep -m1 -E '[A-Za-z0-9]{20,}' "$TOKEN_FILE" | tr -d ' \r\n' || true)"
fi
if [ -z "$TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
    TOKEN="$(grep -m1 '^TINYCMDR_MM_TOKEN=' "$INSTALL_DIR/.env" | cut -d= -f2- || true)"
    if [ -n "$TOKEN" ]; then info "reusing the bot token already in .env"; fi
fi
if [ -z "$TG_TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
    TG_TOKEN="$(grep -m1 '^TINYCMDR_TG_TOKEN=' "$INSTALL_DIR/.env" | cut -d= -f2- || true)"
    if [ -n "$TG_TOKEN" ]; then info "reusing the Telegram token already in .env"; fi
fi
if [ "$ASK" = 1 ]; then say "a few questions"; fi
if [ "$ASK" = 1 ] && [ -z "$TOKEN" ] && [ -z "$TG_TOKEN" ]; then
    info "press Enter with no answer to take the value in brackets"
    TOKEN="$(ask_secret "Mattermost bot token (input hidden, Enter to skip for the local page)")"
fi

# Where the bot lives and who may command it: fleet-defaults.json (a fleet
# package), then this install's own config.json, then the reader.
if [ -z "$MM_URL_ARG" ]; then MM_URL_ARG="$(jget "$DEFAULTS" mattermost_url)"; fi
if [ -z "$MM_URL_ARG" ]; then MM_URL_ARG="$(cfgval_mac mattermost.url)"; fi
ALLOWED_DFLT="$(jget "$DEFAULTS" allowed_user)"
if [ -z "$ALLOWED_DFLT" ]; then ALLOWED_DFLT="$(cfgval_mac mattermost.allowed_users)"; fi
if [ "$ASK" = 1 ]; then
    if [ -n "$TOKEN" ]; then
        MM_URL_ARG="$(ask_text "Mattermost server, no https:// (e.g. chat.example.com)" "$MM_URL_ARG")"
        ALLOWED_ARG="$(ask_text "Your Mattermost user id (optional, but without it the bot ignores your DMs)" "$ALLOWED_DFLT")"
    elif [ -z "$TG_TOKEN" ]; then
        info "no chat token: this install serves the local page only (a token can be"
        info "added later with --token-file, no reinstall of the app itself)"
    fi
fi
# A token with no server is a bot that exits at its first start - the chat lane is
# fatal when Mattermost cannot be reached - so refuse HERE, with the switch to
# pass, instead of installing something that cannot run (the Linux installer has
# refused this shape all along).
if [ -n "$TOKEN" ] && [ -z "$MM_URL_ARG" ]; then
    die "a Mattermost bot token with no server address.
Pass --mattermost-url chat.example.com (no scheme), or re-run at a terminal and
answer the questions."
fi

# ---- the third door: Telegram ----
# A Telegram bot needs nothing hosted and works from anywhere, so it is offered
# here rather than only as a switch. Its TOKEN is .env-only (TINYCMDR_TG_TOKEN) and
# the lane is deny-by-default: a token with no numeric id ignores every DM.
if [ "$ASK" = 1 ] && [ -z "$TG_TOKEN" ]; then
    if ask_yes "Also install a Telegram bot lane (a token from @BotFather)?" n; then
        TG_TOKEN="$(ask_secret "Telegram bot token (input hidden)")"
    fi
fi
if [ "$ASK" = 1 ] && [ -n "$TG_TOKEN" ]; then
    if [ -z "$TG_IDS" ]; then
        TG_IDS="$(ask_text "Your numeric Telegram id (message @userinfobot for it)" "")"
    fi
    if [ -z "$TG_IDS" ]; then
        die "a Telegram token with no numeric id: that lane would ignore every DM.
Message @userinfobot for your id and pass --telegram-ids 123456789"
    fi
    if [ -n "$TOKEN" ]; then
        info "with both tokens set, Mattermost wins in the agent this installer starts:"
        info "  the Telegram lane is a second process - $INSTALL_DIR/tinycmdr --telegram"
    fi
fi

# Which model answers. Any OpenAI-compatible /v1 root: a llama.cpp on this Mac,
# a box on the LAN, or a hosted provider.
if [ "$USE_FLEET_MODEL" = 1 ]; then
    [ -n "$MODEL_BASE_URL" ] || MODEL_BASE_URL="$(jget "$DEFAULTS" model_base_url)"
    [ -n "$MODEL" ] || MODEL="$(jget "$DEFAULTS" model)"
fi
if [ "$ASK" = 1 ]; then
    info "the endpoint is any OpenAI-compatible /v1 root: llama.cpp, Ollama, vLLM,"
    info "or a hosted provider. Enter takes a llama.cpp on this machine."
    MODEL_BASE_URL="$(ask_text "Model endpoint" "${MODEL_BASE_URL:-$DEFAULT_MODEL_BASE}")"
    MODEL="$(ask_text "Model id" "${MODEL:-$DEFAULT_MODEL}")"
    # A hosted endpoint wants a key. It has no env var of its own (only fallback
    # entries have api_key_env), so it lives in llm.api_key - the same home the
    # Windows installer gives it, and the one the build looks at for a hosted
    # PRIMARY. Loopback and untrusted names are never asked about.
    case "$MODEL_BASE_URL" in
        *//127.0.0.1:*|*//localhost:*|*"::1"*) ;;
        *) MODEL_KEY="$(ask_secret "API key for it (blank if it needs none)")" ;;
    esac
fi

# ---- more endpoints: llm.fallbacks, tried in order when the primary fails ----
# Each one is a plain entry (base_url, model, an optional /model alias) and its key
# goes to .env under a generated name the entry's api_key_env points at, which is
# what the build looks at for a fallback endpoint - the fleet's own shape - and it
# keeps the key out of config.json.
if [ "$ASK" = 1 ]; then
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
# web.host decides it: the code binds 0.0.0.0 for an empty value and 127.0.0.1 for
# that address. The page always needs its token (in .env), so "other machines" is
# only about who can REACH it - and a page nobody can reach reads as a broken install.
if [ "$ASK" = 1 ]; then
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

if [ "$ASK" = 1 ]; then
    say "about to install"
    info "folder       : $INSTALL_DIR"
    if [ -n "$TOKEN" ]; then
        info "how you talk : Mattermost at $MM_URL_ARG, as this Mac's own bot"
        info "allowed user : ${ALLOWED_ARG:-NONE - the bot ignores every DM until one is set}"
    elif [ -n "$TG_TOKEN" ]; then
        info "how you talk : Telegram DMs"
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
        info "telegram     : on, DMs from $TG_IDS"
    fi
    if [ "$WEB_ON" = 1 ]; then
        info "local page   : port $WEB_PORT, ${PAGE_HOST:-all interfaces}"
    fi
    if ! ask_yes "Install now?"; then
        info "nothing was changed"
        exit 0
    fi
fi

say "install tinycmdr into $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$LOGDIR"

if [ "$FORCE" = 1 ]; then
    if [ "$IS_MAC" = 1 ] && [ -f "$PLIST" ]; then
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        sleep 1
        info "booted out the running agent"
    fi
    for victim in tinycmdr.py; do
        [ -f "$INSTALL_DIR/$victim" ] && cp -p "$INSTALL_DIR/$victim" \
            "$INSTALL_DIR/$victim.pre-reinstall" 2>/dev/null || true
    done
fi

# Everything is installed FLAT in one folder: every door then reads ONE
# config.json and ONE .env, so a session, the page and the bot cannot disagree
# about which config was last edited.
for f in tinycmdr.py tinycmdr requirements.txt config.example.json README.md field-notes.md soul.md; do
    if [ -f "$SRC/$f" ]; then
        cp -f "$SRC/$f" "$INSTALL_DIR/$f"
    elif [ -f "$INSTALL_DIR/$f" ]; then
        info "keeping the existing $f"
    else
        warn "package has no $f"
    fi
done
if [ -d "$SRC/skills" ]; then
    mkdir -p "$INSTALL_DIR/skills"
    cp -R "$SRC/skills/." "$INSTALL_DIR/skills/" 2>/dev/null || true
    n=$(find "$INSTALL_DIR/skills" -name SKILL.md | wc -l | tr -d ' ')
    info "skills: $n"
fi
# the starter drop-in tools (package bytes win; the operator's own tool
# files in this folder are not named by the package and are left alone)
if [ -d "$SRC/tools" ]; then
    mkdir -p "$INSTALL_DIR/tools"
    cp -f "$SRC/tools/"* "$INSTALL_DIR/tools/" 2>/dev/null || true
fi
# the installer family lands with the install - day-two removal must not need
# the original package (the uninstaller is uninstall-tinycmdr-macos.sh)
if [ -d "$SRC/install" ]; then
    mkdir -p "$INSTALL_DIR/install"
    cp -f "$SRC/install/"* "$INSTALL_DIR/install/" 2>/dev/null || true
# The two double-clickable doors ride in the install dir as well, so someone who wants
# tinycmdr GONE later is looking at a folder that shows them how, without the original
# package (measured 2026-09-26: the install dir carried no door at all).
cp -f "$SRC/INSTALL-MACOS.command" "$INSTALL_DIR/" 2>/dev/null || true
cp -f "$SRC/UNINSTALL-MACOS.command" "$INSTALL_DIR/" 2>/dev/null || true
fi
mkdir -p "$INSTALL_DIR/maintenance"
for f in restart-tinycmdr-macos.sh restart-tinycmdr.sh; do
    [ -f "$SRC/maintenance/$f" ] && cp -f "$SRC/maintenance/$f" "$INSTALL_DIR/maintenance/$f"
done
info "files copied"
# The launcher needs its execute bit: the shim in /usr/local/bin execs THAT file, and
# the package carries it as 0644 (git cannot hold the bit out of a Windows checkout),
# so the `cp -f` above lands a door that answers "Permission denied" for the user and
# for sudo alike - measured 2026-09-25 on a fleet macOS host. The Linux installer
# already chmods it; this one did not.
chmod +x "$INSTALL_DIR/tinycmdr" 2>/dev/null || true

say "python environment"
if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
    "$PY" -m venv "$INSTALL_DIR/venv" \
        || die "venv creation failed. On Homebrew python this means the interpreter is fine but
the venv module is missing; reinstall it:  brew reinstall python@3.12"
fi
VPY="$INSTALL_DIR/venv/bin/python"
"$VPY" -m pip install --quiet --disable-pip-version-check --upgrade pip >/dev/null 2>&1 \
    || info "pip self-upgrade skipped (offline?)"
"$VPY" -m pip install --quiet --disable-pip-version-check -r "$INSTALL_DIR/requirements.txt" \
    || die "pip install failed - see $LOG"
"$VPY" -c 'import requests, mmpy_bot, croniter' \
    || die "the venv is missing a dependency (requests/mmpy_bot/croniter)"
info "mmpy_bot : $("$VPY" -c 'import importlib.metadata as m; print(m.version("mmpy_bot"))')"
info "requests : $("$VPY" -c 'import importlib.metadata as m; print(m.version("requests"))')"

# ---------------------------------------------------------------- token ---
say "credentials"
# The token itself was resolved (or asked for) in the questions above; what is
# left here is the validation that must not run on an unanswered prompt.
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
# (The token is asked for in the questions above, where the answer can still change
# what gets installed. This prompt used to live here, after the files and the venv:
# a second `read` that blocked forever on a terminal with nothing left to type, and
# an answer that arrived too late to keep the run from writing a dead config.)
# A chat account is OPTIONAL: the harness also runs as a session (--cli) and as a local page
# (--web, 127.0.0.1:8787). No token means no chat lane, so the agent runs the PAGE instead -
# running the chat lane would exit at once (tinycmdr.py refuses to start without a token, on
# purpose) and KeepAlive would loop it forever.
APP_ARGS=""
if [ -z "$TOKEN" ] && [ -n "$TG_TOKEN" ]; then
    # The Telegram lane starts by itself with no Mattermost token, so the agent runs
    # the BOT here (no --web): this is a chat lane, and calling it "without a chat
    # account" put a local page where a DM should have been answered.
    info "no Mattermost token, but a Telegram one: the agent runs the TELEGRAM lane"
    info "allowlist  : $TG_IDS_CLEAN"
elif [ -z "$TOKEN" ]; then
    APP_ARGS="--web"
    WEB_ON=1
    info "no Mattermost bot token: installing WITHOUT a chat account"
    info "the agent will serve the local page: http://127.0.0.1:$WEB_PORT"
    info "a session needs no service:         $VPY $INSTALL_DIR/tinycmdr.py --cli"
    info "the page will ask for its token (printed below, and in .env)"
    info "add a chat account later: re-run with --token-file <file>"
fi
# ---------------------------------------------------------------- config ---
say "config"
# What the CALLER asked for, captured before any default is filled in. An update
# (--force, in place) must only change what it was told to change: measured
# 2026-09-24, this writer used the PACKAGE's config.example.json as its base every
# time, so re-running the installer replaced a working host's config with the
# example's placeholders - mattermost.url, allowed_users, the model endpoint and
# the Telegram allowlist all went back to defaults and the bot would not start.
MODEL_BASE_GIVEN="$MODEL_BASE_URL"
MODEL_GIVEN="$MODEL"
WEB_CLI_GIVEN=0
for a in "$@"; do
    case "$a" in
        --web-port|--no-web) WEB_CLI_GIVEN=1 ;;
    esac
done
[ -n "$MM_URL_ARG" ] || MM_URL_ARG="$(jget "$DEFAULTS" mattermost_url)"
[ -n "$ALLOWED_ARG" ] || ALLOWED_ARG="$(jget "$DEFAULTS" allowed_user)"
if [ "$USE_FLEET_MODEL" = 1 ]; then
    [ -n "$MODEL_BASE_URL" ] || MODEL_BASE_URL="$(jget "$DEFAULTS" model_base_url)"
    [ -n "$MODEL" ] || MODEL="$(jget "$DEFAULTS" model)"
fi
if [ -z "$MODEL_BASE_URL" ]; then
    # A laptop leaves the LAN, so default to the cloud endpoint rather than a
    # LAN address that only resolves at home. --use-fleet-model or
    # --model-base-url overrides.
    MODEL_BASE_URL="$DEFAULT_MODEL_BASE"
fi
[ -n "$MODEL" ] || MODEL="$DEFAULT_MODEL"
[ -n "$BOT_NAME" ] || BOT_NAME="$(hostname -s)"

# The page token is a secret like the bot token, so it is written to .env (one secrets
# file per install) rather than into config.json or a loose .txt in the folder.
WEB_TOKEN=""
if [ "$WEB_ON" = "1" ]; then
    WEB_TOKEN="$("$VPY" -c 'import secrets;print(secrets.token_hex(24))')"
    # no ?token= link (security review 2026-09-23): the token in a URL lands in
    # the request line, browser history and any proxy log, and it is shell and
    # code execution on this box. The page prompts for it; print it for paste.
    # Here and not in the earlier summary: that block runs before this mints the
    # token, so its old ready-link line never printed at all (probe, 2026-09-23).
    info "page token   : $WEB_TOKEN   (paste it when the page asks)"
    info "               (also in .env: TINYCMDR_WEB_TOKEN)"
fi

TOKEN="$TOKEN" MM_URL_ARG="$MM_URL_ARG" ALLOWED_ARG="$ALLOWED_ARG" BOT_NAME="$BOT_NAME" \
TG_IDS_CLEAN="$TG_IDS_CLEAN" \
MODEL_BASE_URL="$MODEL_BASE_URL" MODEL="$MODEL" WEB_ON="$WEB_ON" WEB_PORT="$WEB_PORT" \
MODEL_BASE_GIVEN="$MODEL_BASE_GIVEN" MODEL_GIVEN="$MODEL_GIVEN" \
WEB_CLI_GIVEN="$WEB_CLI_GIVEN" MODEL_KEY="$MODEL_KEY" \
FALLBACK_SPECS="$FALLBACK_SPECS" FB_ENV_LINES="$FB_ENV_LINES" PAGE_HOST="$PAGE_HOST" \
"$VPY" - "$SRC/config.example.json" "$INSTALL_DIR/config.json" <<'PY'
import json, os, sys
src, dst = sys.argv[1], sys.argv[2]
# The HOST's own config is the base whenever there is one: an update carries the
# host's settings forward and changes only what this run was told to change.
fresh = not os.path.exists(dst)
cfg = json.load(open(dst if not fresh else src, encoding="utf-8-sig"))
cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
mm = cfg.setdefault("mattermost", {})
mm["token"] = ""                       # the token belongs in .env, never here
if os.environ.get("MM_URL_ARG"):
    mm["url"] = os.environ["MM_URL_ARG"]
au = os.environ.get("ALLOWED_ARG", "").strip()
if au:
    mm["allowed_users"] = [u.strip() for u in au.split(",") if u.strip()]
elif fresh:
    # The example carries REPLACE_WITH_YOUR_MATTERMOST_USER_ID. Leaving it in place
    # made a fresh install look configured while the bot ignored every DM, and the
    # warning printed beside it claimed the list was empty (measured 2026-09-26, a
# fleet macOS host, from the reader's own terminal log).
    mm["allowed_users"] = []
# The third door: the TOKEN is .env-only (env_map resolves TINYCMDR_TG_TOKEN, and a
# copy in here is ignored with a warning), so only the numeric allowlist lands here.
tg = cfg.setdefault("telegram", {})
tg["token"] = ""
_tg_ids = os.environ.get("TG_IDS_CLEAN", "").split()
if _tg_ids or fresh:
    tg["allowed_users"] = _tg_ids
cfg.setdefault("agent", {})["bot_name"] = os.environ["BOT_NAME"]
llm = cfg.setdefault("llm", {})
# Only when the caller chose: --model-base-url/--use-fleet-model/--model. The
# defaults exist for a FIRST install, and re-applying them over a working host is
# how a LAN endpoint became a cloud one on an update.
if os.environ.get("MODEL_BASE_GIVEN") or fresh:
    llm["base_url"] = os.environ["MODEL_BASE_URL"]
if os.environ.get("MODEL_GIVEN") or fresh:
    llm["model"] = os.environ["MODEL"]
_key = os.environ.get("MODEL_KEY", "")
if _key:
    # Only when one was given: an update with no switch keeps whatever this host
    # already has (a model key is per host, and it is the only way to reach a
    # hosted endpoint whose key config.json has to carry).
    llm["api_key"] = _key
# Extra endpoints, only when this run was told about them: a scripted update keeps
# the host's own llm.fallbacks. Each entry's key lives in .env, named by the entry's
# api_key_env - the shape the build resolves for a fallback (never config.json).
_specs = [s for s in os.environ.get("FALLBACK_SPECS", "").splitlines() if s.strip()]
if _specs:
    _keys = {}
    for _line in os.environ.get("FB_ENV_LINES", "").splitlines():
        if "=" in _line:
            _k, _, _v = _line.partition("=")
            if _v.strip():
                _keys[_k.strip()] = _v.strip()
    _fbs = []
    for _spec in _specs:
        _parts = [_p.strip() for _p in (_spec.split("|") + ["", "", "", ""])[:4]]
        _url, _mid, _alias, _ename = _parts
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
if os.environ.get("WEB_CLI_GIVEN") == "1" or fresh:
    web["enabled"] = os.environ["WEB_ON"] == "1"
    web["port"] = int(os.environ["WEB_PORT"])
# The page's bind address, only when the reader chose one: an empty value is the
# code default (0.0.0.0, the token gating it), and an update must not move a host's
# page onto the network because this run said nothing about it.
if os.environ.get("PAGE_HOST"):
    web["host"] = os.environ["PAGE_HOST"]
web["token"] = ""                       # it lives in .env (TINYCMDR_WEB_TOKEN)
with open(dst, "w", encoding="utf-8", newline="\n") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print("    config.json: %s (mattermost=%s, model=%s, allowed_users=%s)"
      % ("kept this host's settings, applied what this run changed" if not fresh
         else "written", mm.get("url", "?"), llm.get("model", "?"),
         mm.get("allowed_users", [])))
if _specs:
    print("    fallbacks  : %d extra endpoint(s), keys in .env" % len(llm.get("fallbacks") or []))
if os.environ.get("PAGE_HOST"):
    print("    web page   : bound to %s:%s" % (os.environ["PAGE_HOST"], web.get("port")))
PY

umask 077
# A model key is per bot: keep whatever this host already has, and never take one
# from a shared secrets file (that is how several hosts ended up sharing one key).
# NO PROVIDER IS NAMED HERE on purpose - the key is whatever the endpoint issued,
# and its variable name is the fallback entry's "api_key_env".
SHARED_KEYS='^(TINYCMDR_MM_TOKEN|TAVILY_API_KEY|ANYSEARCH_API_KEY)='
# Only the keys THIS INSTALL owns are withheld from the carry-over. The search keys
# are the host's own too: they were in this list, so an update without a
# --secrets-file dropped a working host's TAVILY/ANYSEARCH keys - the same loss the
# config writer had, one file over. A secrets file still supplies them when the host
# has none, and wins when it does (it is the fleet's canonical copy).
MANAGED_KEYS='^(TINYCMDR_MM_TOKEN|TINYCMDR_TG_TOKEN|TINYCMDR_WEB_TOKEN)='
# The extra-endpoint keys are managed only when THIS run wrote them: on a scripted
# update the host's own lines must be carried over, or an answered install would
# drop the keys its own config.json still points at.
if [ -n "$FB_ENV_LINES" ]; then
    MANAGED_KEYS='^(TINYCMDR_MM_TOKEN|TINYCMDR_TG_TOKEN|TINYCMDR_WEB_TOKEN|TINYCMDR_ENDPOINT[0-9]+_API_KEY)='
fi
KEEP_ENV=""
if [ -f "$INSTALL_DIR/.env" ]; then
    KEEP_ENV=$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$INSTALL_DIR/.env" \
        | grep -vE "$MANAGED_KEYS" || true)
fi
SKIPPED_KEYS=""
{
    printf 'TINYCMDR_MM_TOKEN=%s\n' "$TOKEN"
    if [ -n "$TG_TOKEN" ]; then
        printf 'TINYCMDR_TG_TOKEN=%s\n' "$TG_TOKEN"
    fi
    if [ -n "$WEB_TOKEN" ]; then
        printf 'TINYCMDR_WEB_TOKEN=%s\n' "$WEB_TOKEN"
    fi
    if [ -n "$FB_ENV_LINES" ]; then
        printf '%s' "$FB_ENV_LINES"
    fi
    if [ -n "$KEEP_ENV" ]; then
        printf '%s\n' "$KEEP_ENV"
    fi
    # LAST, so the fleet's copy of a shared key wins over one the host already had
    # (a duplicate line would otherwise be written for the same key).
    if [ -n "$SECRETS_FILE" ]; then
        [ -f "$SECRETS_FILE" ] || die "--secrets-file $SECRETS_FILE does not exist"
        grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$SECRETS_FILE" | grep -E "$SHARED_KEYS" || true
        SKIPPED_KEYS=$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$SECRETS_FILE" \
            | grep -vE "$SHARED_KEYS" | cut -d= -f1 | tr '\n' ' ' || true)
    fi
} > "$INSTALL_DIR/.env"
chmod 600 "$INSTALL_DIR/.env"
info ".env written (mode 600, token not in config.json)"
if [ -n "$KEEP_ENV" ]; then
    info "kept this host's own keys (a model key is per bot)"
fi
if [ -n "$SKIPPED_KEYS" ]; then
    warn "not copied from the secrets file: $SKIPPED_KEYS"
    warn "a model key is per host - put this host's own in $INSTALL_DIR/.env by hand"
fi

if [ "$WEB_ON" != 1 ]; then
    info "web UI disabled (--no-web)"
elif [ -z "$TOKEN" ]; then
    # Nothing on a page-only or Telegram-only install reads mattermost.url, so a
    # missing host is not a problem to report (the Linux installer states the
    # same rule).
    info "no Mattermost account: the local page is this install's only door"
elif [ -z "$MM_URL_ARG" ]; then
    # Read the file that was just written: on an UPDATE the host already has its own
    # mattermost.url, and warning about a missing one there is a false alarm on the
    # line a reader is most likely to act on.
    _u=$("$VPY" - "$INSTALL_DIR/config.json" <<'PY' 2>/dev/null || true
import json, sys
print((json.load(open(sys.argv[1], encoding="utf-8-sig")).get("mattermost") or {}).get("url") or "")
PY
)
    case "$_u" in
        ""|chat.example.com|CHANGE-ME.example.com)
            warn "no Mattermost host is set: put mattermost.url in"
            warn "$INSTALL_DIR/config.json (or re-run with --mattermost-url)" ;;
    esac
fi
# Read the allowlist back OUT OF THE FILE: on an update this run's switches are
# empty while the host's list is fine, and a warning about a value the install
# just kept is the line a reader is most likely to act on.
EFF_ALLOWED="$("$VPY" - "$INSTALL_DIR/config.json" <<'PY' 2>/dev/null || true
import json, sys
au = (json.load(open(sys.argv[1], encoding="utf-8-sig")).get("mattermost") or {}).get("allowed_users") or []
print(", ".join(str(u) for u in au))
PY
)"
if [ -n "$EFF_ALLOWED" ]; then
    info "allowed user : $EFF_ALLOWED"
elif [ -n "$TOKEN" ]; then
    # Only a CHAT lane has anybody to ignore: on the page lane this warning named a
    # list no process reads.
    warn "mattermost.allowed_users is empty, and this bot is deny-by-default: it will ignore"
    warn "every DM until you add your Mattermost user id (--allowed-user <id>)."
fi

# ---------------------------------------------------------------- launchd ---
if [ "$NO_LAUNCHD" = 1 ]; then
    say "no-launchd: files installed, agent not registered"
    info "start it by hand:  cd $INSTALL_DIR && ./venv/bin/python tinycmdr.py"
    info "or re-run without --no-launchd to register the agent."
    exit 0
fi

say "launchd agent"
# The verb surface on PATH (audit F12): a two-line wrapper with the install dir
# written into it, so nothing has to resolve and uninstalling is one file.
# /usr/local/bin belongs to the reader only where something (Homebrew, a previous
# sudo install) made it writable. On a stock Mac it exists and is root-owned, so the
# old check silently did nothing and the install ended with NO `tinycmdr` command at
# all - the reader is told to run `tinycmdr status` and their shell has never heard
# of it (measured 2026-09-26 on a fleet macOS host). Fall back to ~/.local/bin, and
# a new terminal can see the folder.
VERB_PATH=""
if [ "${NO_PATH:-0}" != "1" ]; then
    if [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then
        VERB_PATH=/usr/local/bin
    elif mkdir -p "$HOME/.local/bin" 2>/dev/null; then
        VERB_PATH="$HOME/.local/bin"
    fi
fi
path_line() {   # path_line <profile-file> <create?> - one marked line, added once
    local pf="$1" create="$2"
    if [ ! -f "$pf" ] && [ "$create" != 1 ]; then return 0; fi
    if grep -q 'tinycmdr' "$pf" 2>/dev/null && grep -q '\.local/bin' "$pf" 2>/dev/null; then
        return 0
    fi
    if printf '\n# tinycmdr\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$pf" 2>/dev/null; then
        info "PATH       : added ~/.local/bin to $pf (open a NEW terminal for it)"
        PATH_ADDED=1
    else
        warn "could not write $pf - add this line to it by hand:"
        warn '    export PATH="$HOME/.local/bin:$PATH"'
    fi
}
if [ -n "$VERB_PATH" ]; then
    printf '#!/bin/sh\nexec "%s/tinycmdr" "$@"\n' "$INSTALL_DIR" > "$VERB_PATH/tinycmdr"
    chmod 0755 "$VERB_PATH/tinycmdr"
    info "verbs      : $VERB_PATH/tinycmdr"
    info "             tinycmdr status | doctor | model | config | logs | restart | token"
    if [ "$VERB_PATH" = "$HOME/.local/bin" ]; then
        case ":$PATH:" in
            *":$HOME/.local/bin:"*) ;;
            *) if [ "$IS_MAC" = 1 ]; then
                   path_line "$HOME/.zshrc" 1                 # macOS default shell
                   [ -f "$HOME/.bash_profile" ] && path_line "$HOME/.bash_profile" 0
               fi ;;
        esac
    fi
else
    info "verbs      : not on PATH - run $INSTALL_DIR/tinycmdr (or re-run without --no-path)"
fi
mkdir -p "$PLIST_DIR"
TPL="$SRC/install/com.tinycmdr.agent.plist"
[ -f "$TPL" ] || die "package is missing install/com.tinycmdr.agent.plist"
sed -e "s|__LABEL__|$LABEL|g" -e "s|__PYTHON__|$VPY|g" -e "s|__APP__|$INSTALL_DIR|g" \
    "$TPL" > "$PLIST"
if [ -n "$APP_ARGS" ]; then
    # run the local page instead of the chat lane: one more ProgramArguments entry, inserted
    # after the script itself so launchd passes it to the agent.
    python3 - "$PLIST" "$INSTALL_DIR/tinycmdr.py" "$APP_ARGS" <<'PY'
import pathlib
import sys
plist, app, args = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(plist)
text = p.read_text()
needle = "<string>%s</string>" % app
if needle in text and args not in text:
    p.write_text(text.replace(needle, needle + "\n\t\t<string>%s</string>" % args, 1))
PY
fi
plutil -lint "$PLIST" >/dev/null || die "the generated plist is not valid: $PLIST"
info "wrote $PLIST"

if [ "$NO_START" = 1 ]; then
    info "--no-start: not loading the agent"
else
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null \
        || launchctl load -w "$PLIST" \
        || die "launchctl could not load $PLIST (see $LOGDIR/launchd.err.log)"
    info "agent loaded as $LABEL"
fi

# ---------------------------------------------------------------- health ---
if [ "$NO_START" != 1 ] && [ "$WEB_ON" = 1 ]; then
    say "health check (http://127.0.0.1:$WEB_PORT/api/health)"
    ok=0
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        if curl -fsS --max-time 3 "http://127.0.0.1:$WEB_PORT/api/health" >/dev/null 2>&1; then
            ok=1
            break
        fi
        sleep 2
    done
    if [ "$ok" = 1 ]; then
        body="$(curl -fsS --max-time 3 "http://127.0.0.1:$WEB_PORT/api/health" || true)"
        want="$(app_version "$INSTALL_DIR/tinycmdr.py")"
        case "$body" in
            *"$want"*)
                info "the bot is answering as this build ($want)";;
            *)
                warn "port $WEB_PORT answers, but not with this build (expected $want):"
                warn "another process was already listening there. Move the bot's web port"
                warn "(--web-port) or stop the other process, then restart the agent.";;
        esac
        printf '    %s\n' "$body"
        # Loopback answering says nothing about the address a reader will actually
        # type. Two different things look identical from there - a page bound to
        # 127.0.0.1, and one on 0.0.0.0 that the macOS application firewall blocks -
        # so ask the machine for its own LAN address and try THAT (measured
        # 2026-09-26: "nothing reachable at <lan-ip>:8787" was both at once).
        LAN_IP=""
        for _if in en0 en1 en2; do
            LAN_IP="$(ipconfig getifaddr "$_if" 2>/dev/null || true)"
            [ -n "$LAN_IP" ] && break
        done
        if [ -z "$LAN_IP" ]; then
            warn "no LAN address on this Mac (wifi off?), so the page is reachable here only"
            info "when it is on a network, the page answers on http://<this-mac's-address>:$WEB_PORT"
        elif curl -fsS --max-time 3 "http://$LAN_IP:$WEB_PORT/api/health" >/dev/null 2>&1; then
            info "page         : http://$LAN_IP:$WEB_PORT (paste the token from .env)"
        else
            warn "the page answers on 127.0.0.1 but NOT on http://$LAN_IP:$WEB_PORT"
            case "$(cfgval_mac web.host)" in
                127.0.0.1)
                    warn "web.host is 127.0.0.1 (loopback only): the page is not on your"
                    warn "network by design. To open it, set web.host to 0.0.0.0 in"
                    warn "$INSTALL_DIR/config.json and restart the agent." ;; 
                *)
                    fw="$(/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null || true)"
                    case "$fw" in
                        *"enabled"*|*"State = 1"*|*"State = 2"*)
                            warn "the macOS application firewall is ON and is blocking the"
                            warn "connection. Let this agent's python in (asks for your password):"
                            warn "  sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add \"$VPY\""
                            warn "  sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp \"$VPY\"" ;;
                        *)
                            warn "check System Settings > Network > Firewall, and any router" 
                            warn "or VPN between this Mac and the machine you are testing from." ;;
                    esac ;;
            esac
        fi
    else
        warn "no answer on port $WEB_PORT yet. First start can take a few seconds, and the bot"
        warn "exits if Mattermost is unreachable. Check $LOGDIR/launchd.err.log and"
        warn "$INSTALL_DIR/tinycmdr.log, then:  ./maintenance/restart-tinycmdr-macos.sh status"
    fi
fi

say "done"
info "install dir : $INSTALL_DIR"
info "agent       : $LABEL  ($PLIST)"
info "logs        : $INSTALL_DIR/tinycmdr.log, $LOGDIR/launchd.err.log"
info "restart     : bash $INSTALL_DIR/maintenance/restart-tinycmdr-macos.sh"
info "uninstall   : double-click $INSTALL_DIR/UNINSTALL-MACOS.command"
    info "              (or: bash $INSTALL_DIR/install/install-tinycmdr-macos.sh --uninstall)"
if [ -n "$VERB_PATH" ]; then
    info "verb        : $VERB_PATH/tinycmdr"
else
    info "verb        : not on PATH - run $INSTALL_DIR/tinycmdr"
fi
if [ -n "$PATH_ADDED" ]; then
    info "              (a new terminal is needed before plain \`tinycmdr\` resolves)"
fi
if [ "$WEB_ON" = 1 ]; then
    _page_host="${LAN_IP:-127.0.0.1}"
    info "local page  : http://$_page_host:$WEB_PORT   (its token is in .env:"
    info "              TINYCMDR_WEB_TOKEN - the page asks for it once)"
fi
info "the bot answers DMs from the users in mattermost.allowed_users only"
