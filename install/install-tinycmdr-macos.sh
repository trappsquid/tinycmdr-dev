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
#   --token <t>           Mattermost bot token (tinycmdr_MM_TOKEN)
#   --token-file <f>      read the token from a file (first non-empty line)
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
INSTALL_DIR="${tinycmdr_DIR:-$HOME/tinycmdr}"
LABEL="${tinycmdr_LABEL:-com.tinycmdr.agent}"
LOGDIR="$INSTALL_DIR/logs"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/$LABEL.plist"
LOG="${tinycmdr_INSTALL_LOG:-${TMPDIR:-/tmp}/tinycmdr-install.log}"
PY_ARG=""
DEFAULTS="$SRC/install/fleet-defaults.json"
DEFAULT_MODEL_BASE="https://api.deepseek.com/v1"
DEFAULT_MODEL="deepseek-v4-flash"

TOKEN=""; TOKEN_FILE=""; BOT_NAME=""; MODEL_BASE_URL=""; MODEL=""; ALLOWED_ARG=""
MM_URL_ARG=""; SECRETS_FILE=""
WEB_PORT="8788"; WEB_ON=1; FORCE=0; NO_START=0; VERIFY_ONLY=0; UNINSTALL=0
NO_LAUNCHD=0; FORCE_PYTHON=0; USE_FLEET_MODEL=0

usage() { sed -n '3,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --token)           TOKEN="$2"; shift 2 ;;
        --token-file)      TOKEN_FILE="$2"; shift 2 ;;
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
        --verify-only)     VERIFY_ONLY=1; shift ;;
        --uninstall)       UNINSTALL=1; shift ;;
        --force-python)    FORCE_PYTHON=1; shift ;;
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
    die "no python3 found. Install one with:  brew install python@3.12  (or python.org)"
}
check_python() {
    local py="$1" v
    v="$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" \
        || die "$py could not run"
    info "python: $py ($v)"
    case "$v" in
        3.10|3.11|3.12) : ;;
        3.13|3.14|3.15|3.16|3.17|3.18|3.19|3.2[0-9])
            if [ "$FORCE_PYTHON" = 1 ]; then
                warn "python $v is newer than anything this was tested on (--force-python)"
            else
                die "python $v resolves an ancient, broken mmpy_bot (the bot starts and never
connects). Use 3.12:  brew install python@3.12   then re-run with --python
/opt/homebrew/bin/python3.12   (or pass --force-python to try anyway)"
            fi ;;
        *) die "python $v is not supported (need 3.10, 3.11 or 3.12)" ;;
    esac
}

if [ "$VERIFY_ONLY" != 1 ]; then
    PY="$(pick_python)"
    check_python "$PY"
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
        grep -q '^tinycmdr_MM_TOKEN=..' "$INSTALL_DIR/.env" && info ".env: bot token is set" \
            || warn ".env: tinycmdr_MM_TOKEN is missing or empty"
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

for f in tinycmdr.py requirements.txt config.example.json README.md; do
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
    TOKEN="$(grep -m1 '^tinycmdr_MM_TOKEN=' "$INSTALL_DIR/.env" | cut -d= -f2- || true)"
    [ -n "$TOKEN" ] && info "reusing the token already in .env"
fi
if [ -z "$TOKEN" ]; then
    printf '    Mattermost bot token (input hidden): '
    read -rs TOKEN
    echo
fi
[ -n "$TOKEN" ] || die "no Mattermost bot token given. Create a bot + token in Mattermost, then
pass it with --token or --token-file."

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

TOKEN="$TOKEN" MM_URL_ARG="$MM_URL_ARG" ALLOWED_ARG="$ALLOWED_ARG" BOT_NAME="$BOT_NAME" \
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
cfg.setdefault("agent", {})["bot_name"] = os.environ["BOT_NAME"]
llm = cfg.setdefault("llm", {})
llm["base_url"] = os.environ["MODEL_BASE_URL"]
llm["model"] = os.environ["MODEL"]
web = cfg.setdefault("web", {})
web["enabled"] = os.environ["WEB_ON"] == "1"
web["port"] = int(os.environ["WEB_PORT"])
with open(dst, "w", encoding="utf-8", newline="\n") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print("    wrote config.json (mattermost=%s, model=%s, allowed_users=%s)"
      % (mm.get("url", "?"), llm["model"], mm.get("allowed_users", [])))
PY

umask 077
# A model key is per bot: keep the one this host already has, and never take one
# from a shared secrets file (that is how several hosts ended up sharing one key).
OWN_KEY=""
if [ -f "$INSTALL_DIR/.env" ]; then
    OWN_KEY=$(sed -n 's/^DEEPSEEK_API_KEY=//p' "$INSTALL_DIR/.env" | tail -1)
fi
SKIPPED_MODEL_KEY=0
{
    printf 'tinycmdr_MM_TOKEN=%s\n' "$TOKEN"
    if [ -n "$SECRETS_FILE" ]; then
        [ -f "$SECRETS_FILE" ] || die "--secrets-file $SECRETS_FILE does not exist"
        if grep -qE '^[[:space:]]*DEEPSEEK_API_KEY=' "$SECRETS_FILE"; then
            SKIPPED_MODEL_KEY=1
        fi
        grep -E '^[A-Z_]+=' "$SECRETS_FILE" | grep -vE '^[[:space:]]*DEEPSEEK_API_KEY=' || true
    fi
    if [ -n "$OWN_KEY" ]; then
        printf 'DEEPSEEK_API_KEY=%s\n' "$OWN_KEY"
    fi
} > "$INSTALL_DIR/.env"
chmod 600 "$INSTALL_DIR/.env"
info ".env written (mode 600, token not in config.json)"
if [ -n "$OWN_KEY" ]; then
    info "kept this host's own DEEPSEEK_API_KEY (the model key is per bot)"
fi
if [ "$SKIPPED_MODEL_KEY" = 1 ]; then
    warn "DEEPSEEK_API_KEY in the secrets file IGNORED - model keys are per bot"
    warn "put this host's own key in $INSTALL_DIR/.env by hand"
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
mkdir -p "$PLIST_DIR"
TPL="$SRC/install/com.tinycmdr.agent.plist"
[ -f "$TPL" ] || die "package is missing install/com.tinycmdr.agent.plist"
sed -e "s|__LABEL__|$LABEL|g" -e "s|__PYTHON__|$VPY|g" -e "s|__APP__|$INSTALL_DIR|g" \
    "$TPL" > "$PLIST"
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
