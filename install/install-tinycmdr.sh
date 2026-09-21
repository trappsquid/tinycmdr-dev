#!/usr/bin/env bash
#
# install-tinycmdr.sh - install tinycmdr on a Debian/Ubuntu host, run by systemd.
#
# On a fleet host this is the whole job:
#
#   sudo bash install-tinycmdr.sh --token <mattermost-bot-token>
#
# It reads install/fleet-defaults.json (Mattermost host, model endpoint, allowed
# user), keeps the bot token out of config.json (it goes to .env), and registers
# a systemd unit that is enabled at boot.
#
#   --token <t>           Mattermost bot token (tinycmdr_MM_TOKEN)
#   --token-file <f>      read the token from a file (first token-looking line)
#   --allowed-user <id>   Mattermost user id allowed to command the bot
#   --mattermost-url <h>  Mattermost host, no scheme (default: fleet-defaults.json)
#   --install-dir <d>     default /home/<user>/tinycmdr
#   --user <u>            run the service as this user (default: the sudo caller)
#   --bot-name <n>        agent.bot_name (default: this hostname)
#   --model-base-url <u>  llm.base_url (default: install/fleet-defaults.json)
#   --model <m>           llm.model (default: install/fleet-defaults.json)
#   --web-port <p>        local web/API fallback port (default 8788, loopback only)
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
SERVICE_NAME="${tinycmdr_SERVICE:-tinycmdr}"
RUN_USER="${tinycmdr_USER:-${SUDO_USER:-$(id -un)}}"
INSTALL_DIR="${tinycmdr_DIR:-/home/$RUN_USER/tinycmdr}"
UNIT="/etc/systemd/system/$SERVICE_NAME.service"
LOG="${tinycmdr_INSTALL_LOG:-/tmp/tinycmdr-install.log}"
PY="${tinycmdr_PYTHON:-python3}"

TOKEN=""; TOKEN_FILE=""; BOT_NAME=""; MODEL_BASE_URL=""; MODEL=""; ALLOWED_ARG=""; MM_URL_ARG=""
WEB_PORT="8788"; WEB_ON=1; FORCE=0; NO_START=0; NO_DEPS=0; VERIFY_ONLY=0; UNINSTALL=0; NO_SUDOERS=0
PORT_BUSY_BEFORE=""

usage() { sed -n '3,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --token)            TOKEN="$2"; shift 2 ;;
        --token-file)       TOKEN_FILE="$2"; shift 2 ;;
        --install-dir)      INSTALL_DIR="$2"; shift 2 ;;
        --user)             RUN_USER="$2"; shift 2 ;;
        --bot-name)         BOT_NAME="$2"; shift 2 ;;
        --allowed-user)     ALLOWED_ARG="$2"; shift 2 ;;
        --mattermost-url)   MM_URL_ARG="$2"; shift 2 ;;
        --model-base-url)   MODEL_BASE_URL="$2"; shift 2 ;;
        --model)            MODEL="$2"; shift 2 ;;
        --web-port)         WEB_PORT="$2"; shift 2 ;;
        --no-web)           WEB_ON=0; shift ;;
        --force)            FORCE=1; shift ;;
        --no-start)         NO_START=1; shift ;;
        --no-deps)          NO_DEPS=1; shift ;;
        --no-sudoers)       NO_SUDOERS=1; shift ;;
        --verify-only)      VERIFY_ONLY=1; shift ;;
        --uninstall)        UNINSTALL=1; shift ;;
        -h|--help)          usage; exit 0 ;;
        *) echo "install-tinycmdr.sh: unknown switch '$1'" >&2; usage >&2; exit 2 ;;
    esac
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
    printf '  unit file    : %s\n' "$([ -f "$UNIT" ] && echo present || echo MISSING)"
    printf '  enabled      : %s\n' "$(systemctl is-enabled "$SERVICE_NAME" 2>&1 || true)"
    printf '  active       : %s\n' "$(systemctl is-active "$SERVICE_NAME" 2>&1 || true)"
    printf '  mattermost   : %s://%s:%s\n' "$(cfgval mattermost.scheme)" \
        "$(cfgval mattermost.url)" "$(cfgval mattermost.port)"
    printf '  model        : %s @ %s\n' "$(cfgval llm.model)" "$(cfgval llm.base_url)"
    printf '  bot name     : %s\n' "$(cfgval agent.bot_name)"
    printf '  allowed user : %s\n' "$(cfgval mattermost.allowed_users)"
    if [ -f "$INSTALL_DIR/.env" ] && grep -q '^tinycmdr_MM_TOKEN=.\+' "$INSTALL_DIR/.env"; then
        printf '  token in .env: yes\n'
    else
        printf '  token in .env: NO\n'
    fi
    port="$(cfgval web.port)"; port="${port:-8788}"
    if [ "$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)" = active ]; then
        printf '  web health   : %s\n' "$(curl -sf --max-time 5 "http://127.0.0.1:$port/api/health" || echo "no answer on port $port")"
    fi
    echo
    echo "--- last log lines ---"
    tail -n 12 "$INSTALL_DIR/tinycmdr.log" 2>/dev/null || echo "(no tinycmdr.log)"
    exit 0
fi

# -------------------------------------------------------------- pre-flight ---
[ "$(id -u)" = 0 ] || die "run this with sudo (it writes $UNIT)"
[ -f "$SRC/tinycmdr.py" ] || die "tinycmdr.py not found next to install/ (looked in $SRC)"
command -v systemctl >/dev/null || die "systemd not found - this installer is for Debian/Ubuntu hosts"
id -u "$RUN_USER" >/dev/null 2>&1 || die "no such user: $RUN_USER"

say "pre-flight"
info "package      : $SRC"
info "version      : $(version_of "$SRC/tinycmdr.py")"
info "install dir  : $INSTALL_DIR"
info "service user : $RUN_USER"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "$PY is $(python3 -V 2>&1); tinycmdr needs Python 3.10 or newer"
info "python       : $($PY -V 2>&1)  ($(command -v "$PY"))"

if [ "$UNINSTALL" = 1 ]; then
    say "uninstall"
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$UNIT"
    systemctl daemon-reload || true
    if [ -n "$INSTALL_DIR" ] && [ "$INSTALL_DIR" != "/" ] && [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
        info "removed $INSTALL_DIR (token, notes and history went with it)"
    fi
    info "unit $SERVICE_NAME removed"
    exit 0
fi

# ------------------------------------------------------------------ token ---
if [ -z "$TOKEN" ] && [ -n "$TOKEN_FILE" ]; then
    [ -f "$TOKEN_FILE" ] || die "no such token file: $TOKEN_FILE"
    TOKEN="$(grep -m1 -oE '[A-Za-z0-9]{20,}' "$TOKEN_FILE" || true)"
    [ -n "$TOKEN" ] || die "no token-looking string in $TOKEN_FILE"
fi
if [ -z "$TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
    TOKEN="$(grep -m1 '^tinycmdr_MM_TOKEN=' "$INSTALL_DIR/.env" | cut -d= -f2- || true)"
    if [ -n "$TOKEN" ]; then
        info "reusing the token already in $INSTALL_DIR/.env"
    fi
fi
# A chat account is OPTIONAL. The harness also runs as a session (--cli) and as a local page
# (--web, 127.0.0.1:8787). With no token there is no chat lane, so the service runs the PAGE:
# running the chat lane here would exit at once (tinycmdr.py refuses to start without a token,
# on purpose) and Restart=always would loop it forever.
APP_ARGS=""
CHAT_LANE=1
if [ -z "$TOKEN" ]; then
    CHAT_LANE=0
    APP_ARGS="--web"
    info "no Mattermost bot token: installing WITHOUT a chat account"
    info "the service will serve the local page: http://127.0.0.1:8787"
    info "a session needs no service at all:   $VENV_PY $INSTALL_DIR/tinycmdr-cli.py"
    info "add a chat account later: re-run this installer with --token-file <file>"
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
[ -n "$MM_HOST" ] || die "no Mattermost host.
Pass --mattermost-url <host> (no scheme), or put mattermost_url in
install/fleet-defaults.json for a fleet package."

say "configuration"
info "mattermost   : https://${MM_HOST}:${MM_PORT}"
info "model        : ${MODEL} @ ${MODEL_BASE_URL}"
if [ -z "$MODEL_BASE_URL" ]; then
    info "WARNING      : llm.base_url is still the template's loopback default -"
    info "               point it at your own OpenAI-compatible endpoint"
    info "               (llama.cpp, vLLM, Ollama, any OpenAI-compatible server)"
fi
if [ -n "$ALLOWED_USER" ]; then
    info "allowed user : ${ALLOWED_USER}"
else
    info "allowed user : NONE in fleet-defaults.json - the bot will ignore everybody"
fi
info "bot name     : ${BOT_NAME}"
if [ "$WEB_ON" = 1 ]; then
    info "web fallback : http://127.0.0.1:${WEB_PORT}"
    if [ -n "$WEB_TOKEN" ]; then
        info "ready link   : http://127.0.0.1:${WEB_PORT}/?token=${WEB_TOKEN}"
        info "               (token in .env: tinycmdr_WEB_TOKEN, nothing to type)"
    fi
else
    info "web fallback : disabled"
fi

if [ "$FORCE" = 1 ] && systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    say "force: stopping the running service"
    systemctl stop "$SERVICE_NAME" || true
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
# tinycmdr-cli.py goes in FLAT, beside tinycmdr.py, never in a folder of its own:
# every door then reads ONE config.json and ONE .env (it resolves both from the
# folder it sits in), and the doors are mediums rather than separate installs.
for item in tinycmdr.py tinycmdr-cli.py requirements.txt README.md \
            config.example.json .env.example \
            skills install maintenance; do
    if [ -e "$SRC/$item" ]; then
        cp -a "$SRC/$item" "$INSTALL_DIR/"
    fi
done
if [ -n "$keep" ]; then
    for f in "$keep"/*; do
        if [ -e "$f" ]; then cp -a "$f" "$INSTALL_DIR/"; fi
    done
    rm -rf "$keep"
fi
mkdir -p "$INSTALL_DIR/tools" "$INSTALL_DIR/sessions"
chmod +x "$INSTALL_DIR/tinycmdr.py" 2>/dev/null || true
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
fi
"$PY" - "$INSTALL_DIR" "$SRC/config.example.json" \
        "$BOT_NAME" "$MODEL_BASE_URL" "$MODEL" "$WEB_PORT" "$WEB_ON" "$FORCE" \
        "$MM_HOST" "$MM_PORT" "$ALLOWED_USER" <<'PY'
import json, os, secrets, sys
(inst, example, bot, base, model, webport, webon,
 force, mm_host, mm_port, allowed) = sys.argv[1:12]
cfg_path = os.path.join(inst, "config.json")
if force != "1" and os.path.exists(cfg_path):
    print("    keeping the existing config.json (use --force to rewrite it)")
else:
    cfg = json.load(open(example, encoding="utf-8-sig"))
    mm = cfg.setdefault("mattermost", {})
    if mm_host:
        mm["url"] = mm_host
    mm["port"] = int(mm_port or 443)
    mm["allowed_users"] = [allowed] if allowed else []
    # Secrets live in .env. A token left in the template or a hand-edited
    # config.json would shadow tinycmdr_MM_TOKEN, and the bot would try to
    # authenticate with a placeholder and fail.
    mm["token"] = ""
    llm = cfg.setdefault("llm", {})
    if base:
        llm["base_url"] = base
    if model:
        llm["model"] = model
    cfg.setdefault("agent", {})["bot_name"] = bot
    if webon == "1":
        # The token is a SECRET, so it goes to .env (tinycmdr_WEB_TOKEN) with the bot
        # token: one file to look in, and nothing loose in the install folder.
        cfg["web"] = {"enabled": True, "port": int(webport or 8788),
                      "token": "", "host": "127.0.0.1"}
    else:
        cfg["web"] = {"enabled": False}
    with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    print(f"    wrote config.json (bot_name={bot}, web={'on' if webon == '1' else 'off'})")
PY
chown "$RUN_USER:$RUN_USER" "$INSTALL_DIR/config.json"
chmod 644 "$INSTALL_DIR/config.json"

# --------------------------------------------------------------------- .env ---
"$PY" - "$INSTALL_DIR/.env" "$TOKEN" "$SRC/install/fleet-secrets.env" "$WEB_TOKEN" <<'PY'
import os, pathlib, re, sys
envp, tok, secrets, webtok = (pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3],
                              sys.argv[4])
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
        if key in ("tinycmdr_MM_TOKEN", "tinycmdr_WEB_TOKEN") or key in have:
            continue          # installer-managed: written below, never carried over
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
        if key not in ("tinycmdr_MM_TOKEN", "tinycmdr_WEB_TOKEN",
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
       f"tinycmdr_MM_TOKEN={tok}"] + ([f"tinycmdr_WEB_TOKEN={webtok}"] if webtok else []) \
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
    if [ "$(id -u)" != 0 ]; then
        info "not root: leaving sudo alone (re-run under sudo to grant passwordless sudo)"
    else
        SUDOERS_FILE="/etc/sudoers.d/${RUN_USER}-hermes"
        SUDOERS_LINE="${RUN_USER} ALL=(ALL) NOPASSWD: ALL"
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
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_PY $INSTALL_DIR/tinycmdr.py $APP_ARGS
Environment="HOME=/home/$RUN_USER"
Environment="USER=$RUN_USER"
Environment="LOGNAME=$RUN_USER"
Environment="PATH=$INSTALL_DIR/venv/bin:/home/$RUN_USER/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="tinycmdr_DIR=$INSTALL_DIR"
Restart=always
RestartSec=10
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

[Install]
WantedBy=multi-user.target
EOF
info "wrote $UNIT"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || die "systemctl enable failed"
info "enabled at boot: $(systemctl is-enabled "$SERVICE_NAME" 2>&1)"

if [ "$NO_START" = 1 ]; then
    info "--no-start: not starting it now"
else
    say "start"
    # If something already listens on the local web port, the health check below
    # would report THAT process, not this install. Note it before we start.
    if [ "$WEB_ON" = 1 ]; then
        pnow="$(cfgval web.port)"; pnow="${pnow:-8788}"
        if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null \
                | grep -qE "[:.]${pnow}[[:space:]]"; then
            PORT_BUSY_BEFORE="$pnow"
        fi
    fi
    systemctl restart "$SERVICE_NAME"
    active=""
    for _ in $(seq 1 25); do
        sleep 2
        if systemctl is-active --quiet "$SERVICE_NAME"; then active=1; break; fi
    done
    if [ -z "$active" ]; then
        echo "--- journal ---"
        journalctl -u "$SERVICE_NAME" -n 40 --no-pager || true
        die "the service did not stay up - see above and $INSTALL_DIR/tinycmdr.log"
    fi
    info "active: yes (pid $(systemctl show -p MainPID --value "$SERVICE_NAME"))"
fi

# -------------------------------------------------------------------- check ---
say "check"
sleep 2
tail -n 12 "$INSTALL_DIR/tinycmdr.log" 2>/dev/null | sed 's/^/    /' || true
port="$(cfgval web.port)"; port="${port:-8788}"
if [ "$WEB_ON" = 1 ]; then
    health="$(curl -sf --max-time 5 "http://127.0.0.1:$port/api/health" || true)"
    if [ -n "$health" ]; then
        info "web /api/health : $health"
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
        info "mattermost      : no login line yet - journalctl -u $SERVICE_NAME -n 50"
    fi
else
    info "chat            : none (no token given) - the page is the door"
fi

cat <<EOF

tinycmdr is installed.

  active   : systemctl status $SERVICE_NAME
  enabled  : $(systemctl is-enabled "$SERVICE_NAME" 2>&1)   (comes up at boot)
  logs     : journalctl -u $SERVICE_NAME -f    and    $INSTALL_DIR/tinycmdr.log
  restart  : sudo bash $INSTALL_DIR/maintenance/restart-tinycmdr.sh
  local    : $VENV_PY $INSTALL_DIR/tinycmdr.py --once "/status"
  session  : $VENV_PY $INSTALL_DIR/tinycmdr-cli.py
  page     : $VENV_PY $INSTALL_DIR/tinycmdr.py --web   -> http://127.0.0.1:8787
  verify   : bash $HERE/$(basename "${BASH_SOURCE[0]}") --verify-only
  remove   : sudo bash $HERE/$(basename "${BASH_SOURCE[0]}") --uninstall
EOF
