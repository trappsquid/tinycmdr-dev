#!/usr/bin/env bash
#
# install-tinycmdr-macos.sh - install tinycmdr on a Mac, run by launchd.
#
# On a Mac this is the whole job:
#
#   bash install-tinycmdr-macos.sh --token <mattermost-bot-token>
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
#   --model-base-url <u>  llm.base_url (default: the cloud endpoint - a laptop roams)
#   --model <m>           llm.model (default: the cloud model)
#   --use-fleet-model     take llm.base_url/model from fleet-defaults.json instead
#                         (i.e. the LAN model endpoint)
#   --web-port <p>        local web/API port (default 8788, loopback only)
#   --no-web              leave the local web port closed
#   --python <path>       interpreter to build the venv from (default: 3.12, else 3.11/3.10)
#   --install-python      fetch a private python 3.12 with uv when none is here
#                         (the installer also OFFERS this when it finds no 3.10-3.12)
#   --label <l>           launchd label (default com.tinycmdr.agent)
#   --secrets-file <f>    extra KEY=VALUE lines for .env (search keys etc)
#   --no-launchd          install the files only; do not register the agent
#                         (also the way to dry-run this installer off macOS)
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
WEB_PORT="8788"; WEB_ON=1; FORCE=0; NO_START=0; VERIFY_ONLY=0; UNINSTALL=0
# Generated later (in the config section), but READ earlier by the no-token branch: under
# `set -u` an unset name there is a crash.
WEB_TOKEN=""
NO_LAUNCHD=0; FORCE_PYTHON=0; USE_FLEET_MODEL=0; INSTALL_PYTHON=0

usage() { sed -n '3,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

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
    if [ "$IS_MAC" = 1 ] && [ -f "$PLIST" ]; then
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null \
            || launchctl unload -w "$PLIST" 2>/dev/null || true
        rm -f "$PLIST"
        info "removed $PLIST"
    fi
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
    # shipped console build (rc=0).
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
        for p in 8788 "$WEB_PORT"; do
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

# tinycmdr-cli.py is installed FLAT, beside tinycmdr.py: every door then reads ONE
# config.json and ONE .env, so a session, the page and the bot cannot disagree
# about which config was last edited.
for f in tinycmdr.py tinycmdr-cli.py tinycmdr requirements.txt config.example.json README.md field-notes.md soul.md; do
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
mkdir -p "$INSTALL_DIR/maintenance"
for f in restart-tinycmdr-macos.sh restart-tinycmdr.sh; do
    [ -f "$SRC/maintenance/$f" ] && cp -f "$SRC/maintenance/$f" "$INSTALL_DIR/maintenance/$f"
done
info "files copied"

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
if [ -z "$TOKEN" ] && [ -n "$TOKEN_FILE" ]; then
    [ -f "$TOKEN_FILE" ] || die "--token-file $TOKEN_FILE does not exist"
    TOKEN="$(grep -m1 -E '[A-Za-z0-9]{20,}' "$TOKEN_FILE" | tr -d ' \r\n' || true)"
fi
if [ -z "$TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
    TOKEN="$(grep -m1 '^TINYCMDR_MM_TOKEN=' "$INSTALL_DIR/.env" | cut -d= -f2- || true)"
    [ -n "$TOKEN" ] && info "reusing the token already in .env"
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
if [ -z "$TOKEN" ]; then
    printf '    Mattermost bot token (input hidden): '
    read -rs TOKEN
    echo
fi
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
    info "a session needs no service:         $VPY $INSTALL_DIR/tinycmdr-cli.py"
    if [ -n "$WEB_TOKEN" ]; then
        info "the page needs its token:           in .env as TINYCMDR_WEB_TOKEN"
        info "ready link (carries it, nothing to type):"
        info "  http://127.0.0.1:$WEB_PORT/?token=$WEB_TOKEN"
    fi
    info "add a chat account later: re-run with --token-file <file>"
fi
# ---------------------------------------------------------------- config ---
say "config"
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
fi

TOKEN="$TOKEN" MM_URL_ARG="$MM_URL_ARG" ALLOWED_ARG="$ALLOWED_ARG" BOT_NAME="$BOT_NAME" \
TG_IDS_CLEAN="$TG_IDS_CLEAN" \
MODEL_BASE_URL="$MODEL_BASE_URL" MODEL="$MODEL" WEB_ON="$WEB_ON" WEB_PORT="$WEB_PORT" \
"$VPY" - "$SRC/config.example.json" "$INSTALL_DIR/config.json" <<'PY'
import json, os, sys
src, dst = sys.argv[1], sys.argv[2]
cfg = json.load(open(src, encoding="utf-8-sig"))
cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
mm = cfg.setdefault("mattermost", {})
mm["token"] = ""                       # the token belongs in .env, never here
if os.environ.get("MM_URL_ARG"):
    mm["url"] = os.environ["MM_URL_ARG"]
au = os.environ.get("ALLOWED_ARG", "").strip()
if au:
    mm["allowed_users"] = [u.strip() for u in au.split(",") if u.strip()]
# The third door: the TOKEN is .env-only (env_map resolves TINYCMDR_TG_TOKEN, and a
# copy in here is ignored with a warning), so only the numeric allowlist lands here.
tg = cfg.setdefault("telegram", {})
tg["token"] = ""
tg["allowed_users"] = os.environ.get("TG_IDS_CLEAN", "").split()
cfg.setdefault("agent", {})["bot_name"] = os.environ["BOT_NAME"]
llm = cfg.setdefault("llm", {})
llm["base_url"] = os.environ["MODEL_BASE_URL"]
llm["model"] = os.environ["MODEL"]
web = cfg.setdefault("web", {})
web["enabled"] = os.environ["WEB_ON"] == "1"
web["port"] = int(os.environ["WEB_PORT"])
web["token"] = ""                       # it lives in .env (TINYCMDR_WEB_TOKEN)
with open(dst, "w", encoding="utf-8", newline="\n") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print("    wrote config.json (mattermost=%s, model=%s, allowed_users=%s)"
      % (mm.get("url", "?"), llm["model"], mm.get("allowed_users", [])))
PY

umask 077
# A model key is per bot: keep whatever this host already has, and never take one
# from a shared secrets file (that is how several hosts ended up sharing one key).
# NO PROVIDER IS NAMED HERE on purpose - the key is whatever the endpoint issued,
# and its variable name is the fallback entry's "api_key_env".
SHARED_KEYS='^(TINYCMDR_MM_TOKEN|TAVILY_API_KEY|ANYSEARCH_API_KEY)='
MANAGED_KEYS='^(TINYCMDR_MM_TOKEN|TINYCMDR_TG_TOKEN|TINYCMDR_WEB_TOKEN|TAVILY_API_KEY|ANYSEARCH_API_KEY)='
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
    if [ -n "$SECRETS_FILE" ]; then
        [ -f "$SECRETS_FILE" ] || die "--secrets-file $SECRETS_FILE does not exist"
        grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$SECRETS_FILE" | grep -E "$SHARED_KEYS" || true
        SKIPPED_KEYS=$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$SECRETS_FILE" \
            | grep -vE "$SHARED_KEYS" | cut -d= -f1 | tr '\n' ' ' || true)
    fi
    if [ -n "$KEEP_ENV" ]; then
        printf '%s\n' "$KEEP_ENV"
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
elif [ -z "$MM_URL_ARG" ]; then
    warn "no Mattermost host given and no fleet-defaults.json: set mattermost.url in"
    warn "$INSTALL_DIR/config.json before starting, or re-run with --mattermost-url"
fi
if [ -z "$ALLOWED_ARG" ]; then
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
if [ "${NO_PATH:-0}" != "1" ] && [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then
    printf '#!/bin/sh\nexec "%s/tinycmdr" "$@"\n' "$INSTALL_DIR" > /usr/local/bin/tinycmdr
    chmod 0755 /usr/local/bin/tinycmdr
    info "verbs      : tinycmdr status | doctor | model | logs | restart | token"
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
info "uninstall   : bash $SRC/install/install-tinycmdr-macos.sh --uninstall"
info "the bot answers DMs from the users in mattermost.allowed_users only"
