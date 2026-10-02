#!/usr/bin/env bash
#
# install-tinycmdr-macos.sh - install tinycmdr on a Mac, run by launchd.
#
# On a Mac this is the whole job:
#
#   bash install-tinycmdr-macos.sh
#
# With no switches it ASKS for what the bot cannot work without - the Mattermost
# server, your user id, a Telegram lane if you want one, and where the model lives:
# local (this Mac or your LAN) or cloud (a hosted provider), the key first for cloud.
# It proves the key with a bearer GET /models and offers the models that come back,
# and "Add another endpoint?" for as many fallbacks as you like - and writes nothing
# until you say yes.
# Every answer has a switch; pass them (or -y) and it asks nothing. Secrets are read
# at masked prompts, so they never have to enter your shell history.
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
#   --search-egress <b>   true|false: may web search send queries OFF this machine?
#                         default false - both built-in providers are third parties,
#                         and a provider on this LAN (a searxng entry) never needs it
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
#   --python <path>       interpreter to build the venv from (default: 3.12, else 3.11/3.10)
#   --install-python      fetch a private python 3.12 with uv when none is here
#                         (the installer also OFFERS this when it finds no 3.10-3.12)
#   --force-python        accept an interpreter NEWER than 3.12 and hope: the pinned
#                         mmpy_bot is the last release that connects on 3.13+
#   --label <l>           launchd label (default com.tinycmdr.agent)
#   --secrets-file <f>    extra KEY=VALUE lines for .env (search keys etc)
#   --no-path             do not put the `tinycmdr` verb on PATH
#   --no-launchd          install the files only; do not register the agent
#                         (also the way to dry-run this installer off macOS)
# The questions, in order: the bot token, the Mattermost server and your user id,
# a Telegram lane (optional), the model endpoint and its key, and "Add another
# endpoint?" for as many fallbacks as you want. Nothing is written until you answer
# "Install now?".
#
#   --telegram-token <t>  answer for the Telegram question without being asked
#   --allowed-user <id>   answer for your Mattermost user id
#   -y | --yes            ask nothing: take the switches above and the defaults
#                         (the questions are asked only at a terminal, and a
#                          piped or scripted run takes the defaults instead)
#   --force               reinstall in place (boots out the agent first)
#   --no-start            register the agent, do not start it now
#   --verify-only         report on an existing install, change nothing
#   --uninstall           stop the agent, remove it and the install dir
#   -h | --help           this text
#
# Everything is transcribed to $TMPDIR/tinycmdr-install.log (mode 600), so a failure
# always leaves the reason on disk. No token is ever echoed into it: the bot token goes
# to .env (mode 600) and the transcript says where to read it - a secret in a
# transcript is a secret in a file nobody thinks to delete.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
# The install belongs to the person who ran the installer, not to $HOME. `sudo` resets
# HOME to /var/root (sudoers(5) env_reset), so every $HOME-derived path below pointed at
# root's home the moment a reader followed install/README-macos.md's documented removal
# `sudo bash install/uninstall-tinycmdr-macos.sh`. Measured 2026-09-26: under sudo the
# uninstaller looked in /var/root, found nothing, printed "done." and exited 0, while the
# launchd job kept KeepAlive-ing a tinycmdr.py that was still there. So resolve the
# INVOKING user first (SUDO_USER, else the account behind this uid) and that user's home
# from the account database, and use it everywhere $HOME used to appear.
user_home() {   # user_home <name> -> that user's home directory, or empty
    local u="$1" h=""
    h="$(getent passwd "$u" 2>/dev/null | cut -d: -f6)" || true            # Linux
    [ -n "$h" ] || h="$(dscl . -read "/Users/$u" NFSHomeDirectory 2>/dev/null \
        | awk '{print $2}')" || true                                       # macOS
    printf '%s' "$h"
}
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ] && id -u "$SUDO_USER" >/dev/null 2>&1; then
    RUN_USER="$SUDO_USER"
else
    RUN_USER="$(id -un)"
fi
RUN_HOME="$(user_home "$RUN_USER")"
# Nothing in the account database answered (a container, a hand-made passwd entry): fall
# back to $HOME rather than refusing.
[ -n "$RUN_HOME" ] || RUN_HOME="$HOME"
INSTALL_DIR="${TINYCMDR_DIR:-$RUN_HOME/tinycmdr}"
LABEL="${TINYCMDR_LABEL:-com.tinycmdr.agent}"
LABEL_GIVEN=0
# Where the install records the label it registered, so --uninstall (and the shipped
# uninstaller) can remove a `--label <l>` install: nothing used to record it, and every
# shipped door only ever looked for the default plist.
LABEL_FILE_NAME=".tinycmdr-label"
LOGDIR="$INSTALL_DIR/logs"
PLIST_DIR="$RUN_HOME/Library/LaunchAgents"
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
MM_URL_ARG=""; MM_PORT_ARG=""; SECRETS_FILE=""
FORCE=0; NO_START=0; VERIFY_ONLY=0; UNINSTALL=0
NO_LAUNCHD=0; FORCE_PYTHON=0; USE_FLEET_MODEL=0; INSTALL_PYTHON=0; YES=0
# Filled by the questions (or the switches) and written into config.json.
MODEL_KEY=""; VERB_PATH=""; PATH_ADDED=""; CLOUD_FALLBACK=false
SEARCH_EGRESS=""       # --search-egress true|false ("" = leave the host's own); an
                       # off-LAN search provider is refused, not called, while false
# Extra endpoints (llm.fallbacks).
FALLBACK_SPECS=""; FB_ENV_LINES=""

usage() {
    sed -n '3,/^set -/p' "${BASH_SOURCE[0]}" | sed '/^set -/d' | sed 's/^# \{0,1\}//'
}

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
        --python)          PY_ARG="$2"; shift 2 ;;
        --use-fleet-model) USE_FLEET_MODEL=1; shift ;;
        --label)           LABEL="$2"; LABEL_GIVEN=1; PLIST="$PLIST_DIR/$LABEL.plist"; shift 2 ;;
        --secrets-file)    SECRETS_FILE="$2"; shift 2 ;;
        --no-launchd)      NO_LAUNCHD=1; shift ;;
        --force)           FORCE=1; shift ;;
        -y|--yes)          YES=1; shift ;;
        --search-egress)   SEARCH_EGRESS="$2"; shift 2 ;;
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

# ----------------------------------------------------------- asking a person ---
# Asked ONLY when a person is there to answer: a real terminal (the one-line door
# hands its own over - see install.sh) and not --yes. A scripted or headless run
# takes the defaults and prints them instead, so a fleet push can never hang on a
# question. TINYCMDR_ASK=1 forces the asks on a redirected stdin.
mm_port_only() {   # mm_port_only <what a reader typed> -> the port, or ""
    # A reader pastes what their browser shows. mattermost.url is the HOST alone (the
    # scheme and port are separate keys in config.json), so split what they gave instead
    # of writing a url no client can build a request from (measured 2026-09-26: a Mac
    # ended up with a full https:// URL in the host field).
    local h="$1"
    h="${h#http://}"; h="${h#https://}"; h="${h%%/*}"
    h="${h#*@}"
    case "$h" in
        *:*:*) printf '%s' "" ;;          # IPv6 literal, not host:port
        *:*) printf '%s' "${h##*:}" ;;
        *) printf '%s' "" ;;
    esac
}

mm_take_url() {   # mm_take_url <raw> -> sets MM_URL_ARG (host) and MM_PORT_ARG (port)
    local raw="$1" pt=""
    pt="$(mm_port_only "$raw")"
    if [ -n "$pt" ]; then MM_PORT_ARG="$pt"; fi
    raw="${raw#http://}"; raw="${raw#https://}"; raw="${raw%%/*}"; raw="${raw#*@}"
    case "$raw" in
        *:*:*) ;;                          # IPv6 literal: leave it whole
        *:*) raw="${raw%:*}" ;;
    esac
    MM_URL_ARG="$raw"
}

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
    # The installer's own reachability check, using the python it already resolved: one
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
    # from memory is how a Mac ends up configured for a model it does not serve. Nothing to
    # offer (no answer, or a server that lists nothing) keeps the plain typed answer.
    local ids="$1" dflt="${2:-}" a="" n=0 id=""
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

IS_MAC=0
[ "$(uname -s)" = "Darwin" ] && IS_MAC=1

if [ "$VERIFY_ONLY" = 1 ]; then
    LOG=/dev/null
fi
# The log must be born unreadable by others. It transcribes every line this run prints,
# and the install folder's contents are the host's own business - create it 0600 BEFORE
# tee opens it (tee -a keeps the mode of an existing file) and tighten one left behind
# by an earlier build.
if [ "$LOG" != "/dev/null" ]; then
    if [ ! -e "$LOG" ] && [ -d "$(dirname "$LOG")" ] && [ -w "$(dirname "$LOG")" ]; then
        (umask 077; : > "$LOG") 2>/dev/null || true
    elif [ -e "$LOG" ] && [ -w "$LOG" ]; then
        chmod 600 "$LOG" 2>/dev/null || true
    fi
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
    _removed=0
    # FIRST, the label: a `--label <l>` install could not be removed by any shipped door,
    # because the uninstaller only ever looked for the default plist. The installer
    # records the label in the install folder for exactly this; read it before the folder
    # goes, and only when the caller did not name one.
    if [ "$LABEL_GIVEN" != 1 ] && [ -f "$INSTALL_DIR/$LABEL_FILE_NAME" ]; then
        _recorded="$(head -n1 "$INSTALL_DIR/$LABEL_FILE_NAME" 2>/dev/null || true)"
        if [ -n "$_recorded" ]; then
            LABEL="$_recorded"
            PLIST="$PLIST_DIR/$LABEL.plist"
            info "launchd label from the install: $LABEL"
        fi
    fi
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
            _removed=1
            info "removed $PLIST"
        else
            info "kept $PLIST - it belongs to another install (not $INSTALL_DIR)"
        fi
    fi
    # the PATH wrappers the install wrote outside its folder - /usr/local/bin when it
    # was writable, else ~/.local/bin. Only the one that points at THIS install goes
    # (a probe uninstall must not take the real one's wrapper).
    for _wrap in /usr/local/bin/tinycmdr "$RUN_HOME/.local/bin/tinycmdr"; do
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
            _removed=1
            info "removed $_wrap"
        fi
    done
    # the PATH line this installer added to a shell profile, and only that line
    for _pf in "$RUN_HOME/.zshrc" "$RUN_HOME/.bash_profile"; do
        [ -f "$_pf" ] || continue
        grep -q '^# tinycmdr$' "$_pf" 2>/dev/null || continue
        _tmp="$_pf.tinycmdr-tmp"
        if grep -v -e '^# tinycmdr$' -e '^export PATH="\$HOME/\.local/bin:\$PATH"$' "$_pf" > "$_tmp" 2>/dev/null \
                && mv "$_tmp" "$_pf" 2>/dev/null; then
            _removed=1
            info "removed the ~/.local/bin PATH line from $_pf"
        else
            rm -f "$_tmp" 2>/dev/null || true
            warn "could not edit $_pf - remove the '# tinycmdr' line from it by hand"
        fi
    done
    if [ -d "$INSTALL_DIR" ]; then
        info "removing $INSTALL_DIR"
        rm -rf "$INSTALL_DIR"
        _removed=1
    fi
    if [ "$_removed" != 1 ]; then
        # Silence here is how a wrong-directory removal looked like success: under sudo the
        # uninstaller used to resolve /var/root, find nothing and print "done.".
        warn "nothing to remove - no launchd job for '$LABEL', no PATH wrapper pointing at"
        warn "$INSTALL_DIR, no '# tinycmdr' PATH line, and no folder there."
        info "checked plist   : $PLIST"
        info "checked wrapper : $RUN_HOME/.local/bin/tinycmdr, /usr/local/bin/tinycmdr"
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
    for c in "$RUN_HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
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
            3.[0-9])
                # --install-python is an explicit "give me a 3.12", so it wins over a 3.9
                # that was passed by name (or found): fetch rather than refuse (the fetch
                # used to be unreachable when --python was also passed).
                if [ "$tries" = 0 ] && [ "$INSTALL_PYTHON" = 1 ]; then
                    say "python"
                    tries=1
                    PY="$(fetch_python)"
                    continue
                fi
                # Otherwise 3.9 is the one version that is neither fixable by a flag nor
                # worth trying: the config writer calls Path.write_text(newline=...), a
                # 3.10 keyword, so the run ends in a TypeError inside the writer. Refuse
                # with the BAND named (the README said "3.10+", which 3.9 satisfies), and
                # do it even under -y: no amount of consenting makes 3.9 work.
                die "python $v is too old: tinycmdr runs on 3.10-3.12, and 3.9 fails
inside the config writer (Path.write_text(newline=...) is a 3.10 keyword).
Install one:  brew install python@3.12   then re-run with --python /opt/homebrew/bin/python3.12
- or have this installer fetch one:  --install-python" ;;
            *)
                # No interpreter at all (or one whose version cannot be read): -y means the
                # reader has already said "ask me nothing", and there is no too-old trap
                # here - a fetch is the only way the install can finish, so -y is consent.
                if [ "$tries" = 0 ] \
                   && { [ "$INSTALL_PYTHON" = 1 ] || [ "$YES" = 1 ] \
                        || tty_ask "no python 3.10-3.12 on this Mac ($v). Fetch a private python 3.12 now (uv, no password, ~66 MB)?"; }; then
                    say "python"
                    tries=1
                    PY="$(fetch_python)"
                    continue
                fi
                die "python $v is not supported (need 3.10-3.12).
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
if isinstance(v, list):
    v = ", ".join(str(x) for x in v)
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
    if [ -x /usr/local/bin/tinycmdr ] || [ -x "$RUN_HOME/.local/bin/tinycmdr" ]; then
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
    _mm=no; _tg=no
    if [ -f "$INSTALL_DIR/.env" ]; then
        grep -q '^TINYCMDR_MM_TOKEN=.\+' "$INSTALL_DIR/.env" && _mm=yes
        grep -q '^TINYCMDR_TG_TOKEN=.\+' "$INSTALL_DIR/.env" && _tg=yes
    fi
    if [ "$_mm" = yes ]; then info "lane: mattermost"
    elif [ "$_tg" = yes ]; then info "lane: telegram"
    else warn "lane: none - no chat account, so nothing remote is served"; fi
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
# fleet macOS host: no host, no endpoint, no key, and the agent exited at its first
# start because chat.example.com does not resolve).
# So ask, HERE, before a single file is written: answering is then all a reader
# has to do, and "no" leaves the disk untouched.
ASK=1
{ [ -t 0 ] && [ "$YES" != 1 ]; } || ASK=0
[ -n "${TINYCMDR_ASK:-}" ] && ASK=1

# Whatever came in on a switch goes through the splitter once (the helper is defined
# above, next to the other ask plumbing - calling it next to the arg loop was a
# "command not found" that stopped the run, measured 2026-09-26).
if [ -n "$MM_URL_ARG" ]; then mm_take_url "$MM_URL_ARG"; fi

# The bot token: --token, --token-file and the .env this install already has all
# win, and a run with none of them is a host with no chat lane (below), not an error.
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
if [ "$ASK" = 1 ]; then
    say "a few questions"
    info "press Enter with no answer to take the value in brackets"
    # ASK even when a token is already known (--token, --token-file, this install's .env):
    # the question was skipped outright then, so a reinstall could not change or add a
    # Mattermost token at all. Enter keeps what is already known.
    if [ -n "$TOKEN" ]; then
        info "a bot token is already known (a switch, --token-file or .env) - Enter keeps it"
    fi
    _tok="$(ask_secret "Mattermost bot token (input hidden, Enter to keep/skip)")"
    [ -n "$_tok" ] && TOKEN="$_tok"
fi

# Where the bot lives and who may command it: fleet-defaults.json (a fleet
# package), then this install's own config.json, then the reader.
if [ -z "$MM_URL_ARG" ]; then MM_URL_ARG="$(jget "$DEFAULTS" mattermost_url)"; fi
if [ -z "$MM_URL_ARG" ]; then MM_URL_ARG="$(cfgval_mac mattermost.url)"; fi
ALLOWED_DFLT="$(jget "$DEFAULTS" allowed_user)"
if [ -z "$ALLOWED_DFLT" ]; then ALLOWED_DFLT="$(cfgval_mac mattermost.allowed_users)"; fi
if [ "$ASK" = 1 ]; then
    if [ -n "$TOKEN" ]; then
        mm_take_url "$(ask_text "Mattermost server, no https:// (e.g. chat.example.com)" "$MM_URL_ARG")"
        ALLOWED_ARG="$(ask_text "Your Mattermost user id (optional, but without it the bot ignores your DMs)" "$ALLOWED_DFLT")"
    elif [ -z "$TG_TOKEN" ]; then
        info "no chat token: this install will have nothing remote to serve. A token"
        info "can be added later with --token-file, no reinstall of the app itself."
    fi
fi
# A token with no server is a bot that exits at its first start - the chat lane is
# fatal when Mattermost cannot be reached - so refuse HERE, with the switch to
# pass, instead of installing something that cannot run (the Linux installer has
# refused this shape all along).
if [ -n "$MM_URL_ARG" ]; then mm_take_url "$MM_URL_ARG"; fi
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
        info "both tokens set: this agent serves BOTH - Mattermost and a Telegram lane"
        info "  in the same process (no second job to start)."
    fi
fi

# Which model answers. Any OpenAI-compatible /v1 root: a llama.cpp on this Mac,
# a box on the LAN, or a hosted provider.
if [ "$USE_FLEET_MODEL" = 1 ]; then
    [ -n "$MODEL_BASE_URL" ] || MODEL_BASE_URL="$(jget "$DEFAULTS" model_base_url)"
    [ -n "$MODEL" ] || MODEL="$(jget "$DEFAULTS" model)"
fi
if [ "$ASK" = 1 ]; then
    info "local (llama.cpp, Ollama, vLLM on this Mac or your LAN) or cloud"
    info "(a hosted OpenAI-compatible provider, which needs an API key)."
    _kind_dflt=1
    case "${MODEL_BASE_URL:-$DEFAULT_MODEL_BASE}" in
        *//127.0.0.1:*|*//localhost:*|*"::1"*|*//10.*|*//192.168.*) ;;
        *) _kind_dflt=2 ;;
    esac
    _kind="$(ask_choice "Which kind of endpoint is it?" "$_kind_dflt" \
        "local / my LAN (no key)" "cloud / hosted (needs an API key)")"
    MODEL_KEY=""
    if [ "$_kind" = "2" ]; then
        MODEL_KEY="$(ask_secret "API key for the provider (hidden)")"
    fi
    # Ask, PROBE with the key in hand, and offer what it advertises: this question cannot
    # be checked until the first request fails, so a typo here used to be invisible for the
    # whole install. Three tries, then it keeps the URL and names the command that fixes it
    # later - a server that is not up yet is normal, and the installer must not become a wall.
    _url_tries=0
    while :; do
        _lbl="Model endpoint"
        [ "$_kind" = "2" ] && _lbl="Endpoint (e.g. https://api.provider.com/v1)"
        MODEL_BASE_URL="$(ask_text "$_lbl" "${MODEL_BASE_URL:-$DEFAULT_MODEL_BASE}")"
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
    MODEL="$(ask_model_id "$_ids" "${MODEL:-$DEFAULT_MODEL}")"
    # The key is written to .env below as TINYCMDR_LLM_API_KEY, NOT to config.json: the
    # primary's key used to land in llm.api_key, a file the agent reads into a prompt.
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
        # The same conversation as the primary: local or cloud, the key (cloud), then the
        # link - probed WITH the key - then the model from what it advertises.
        _fb_kind="$(ask_choice "Endpoint #$_fb_n: local or cloud?" 1 \
            "local / my LAN (no key)" "cloud / hosted (needs an API key)")"
        _fb_key=""
        [ "$_fb_kind" = "2" ] && _fb_key="$(ask_secret "API key for it (hidden)")"
        _fb_lbl="Endpoint #$_fb_n (OpenAI-compatible /v1 root)"
        [ "$_fb_kind" = "2" ] && _fb_lbl="Endpoint #$_fb_n (e.g. https://api.provider.com/v1)"
        _fb_url="$(ask_text "$_fb_lbl" "")"
        case "$_fb_url" in
            *//127.0.0.1:*|*//localhost:*|*"::1"*|*//10.*|*//192.168.*) ;;
            *) if [ "$CLOUD_FALLBACK" != "true" ]; then
                   ask_yes "Allow automatic failover to off-LAN endpoints when the local one fails?" n \
                       && CLOUD_FALLBACK=true
               fi ;;
        esac
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
if [ "$ASK" = 1 ] && [ -z "$SEARCH_EGRESS" ]; then
    if ask_yes "May the bot's web search send queries off this machine?" y; then
        SEARCH_EGRESS="true"
    else
        SEARCH_EGRESS="false"
    fi
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
        info "how you talk : nothing remote - --app (or --cli inline) and --once (no token given)"
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
        info "telegram     : on, DMs from $TG_IDS"
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

# ------------------------------------------------------------------- identity ---
# A launchd LABEL belongs to the USER, not to a folder. An install that keeps the
# default label boots out whatever is already registered under it, so a second install
# (a probe, or a run from another folder) silently takes the first one's autostart,
# and the agent it displaced stays unloaded, which looks exactly like "the bot is gone
# and nothing answers" (measured 2026-09-26 on a fleet macOS host: the real install was
# left unregistered by test installs sharing the default label).
if [ "$IS_MAC" = 1 ]; then
    _foreign=""
    if [ -f "$PLIST" ] && ! grep -qF "$INSTALL_DIR" "$PLIST" 2>/dev/null; then
        _foreign="$PLIST (it names another folder)"
    else
        # `launchctl print` EXITS NON-ZERO for a label that is not loaded, and under
        # `set -e` with pipefail that killed the whole run at the assignment (measured
        # 2026-09-26: the installer died silently right after the python check).
        _loaded="$( { launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null || true; } \
            | sed -n 's/^[[:space:]]*path = //p' | head -1)"
        if [ -n "$_loaded" ] && [ "$_loaded" != "$PLIST" ]; then
            _foreign="$_loaded"
        fi
    fi
    if [ -n "$_foreign" ]; then
        die "the launchd label $LABEL already belongs to another install:
    $_foreign
This run would boot that agent out. Give this install its own label (a probe or a
second install always should):
    bash $0 --label com.tinycmdr.$(hostname -s | tr 'A-Z' 'a-z') <your switches>
or remove the other install first:  bash $0 --uninstall"
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
# config.json and ONE .env, so a session and the bot cannot disagree about which
# config was last edited.
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
# A chat account is REQUIRED for the launchd agent: the harness also runs as a session
# (--cli) and a single task (--once), but with no Mattermost and no Telegram token there
# is nothing remote to serve, so no agent is registered - running a lane-less agent would
# exit at once (tinycmdr.py refuses to start without a token, on purpose) and KeepAlive
# would loop it forever.
HAS_LANE=1
if [ -z "$TOKEN" ] && [ -n "$SECRETS_FILE" ]; then
    # Read HERE - before the lane is chosen. It used to be read only where .env is
    # written, which is after the lane branch: a secrets file with TINYCMDR_MM_TOKEN
    # produced "installing WITHOUT a chat account", an empty `TINYCMDR_MM_TOKEN=` as the
    # FIRST line of .env with the file's real one below it - and the build keeps the
    # FIRST occurrence - so the bot answered no DMs while config.json and .env both
    # looked configured (I9).
    [ -f "$SECRETS_FILE" ] || die "--secrets-file $SECRETS_FILE does not exist"
    TOKEN="$(grep -m1 '^TINYCMDR_MM_TOKEN=' "$SECRETS_FILE" | cut -d= -f2- \
        | tr -d ' \r' || true)"
    if [ -n "$TOKEN" ]; then
        info "bot token   : TINYCMDR_MM_TOKEN from $SECRETS_FILE"
    else
        info "$SECRETS_FILE carries no TINYCMDR_MM_TOKEN line"
    fi
fi
if [ -z "$TOKEN" ] && [ -n "$TG_TOKEN" ]; then
    # The Telegram lane starts by itself with no Mattermost token, so the agent runs
    # the BOT here: this is a chat lane that answers DMs on its own.
    info "no Mattermost token, but a Telegram one: the agent runs the TELEGRAM lane"
    info "allowlist  : $TG_IDS_CLEAN"
elif [ -z "$TOKEN" ]; then
    HAS_LANE=0
    info "no Mattermost bot token and no Telegram token: a CLI-only install - a"
    info "supported way to run it. Nothing remote is served, and no launchd agent"
    info "is registered (an agent with no lane would exit at once and KeepAlive loop)."
    info "this host has two doors, both work right now:"
    info "  the app   : $VPY $INSTALL_DIR/tinycmdr.py --app"
    info "  inline    : $VPY $INSTALL_DIR/tinycmdr.py --cli"
    info "  one task  : $VPY $INSTALL_DIR/tinycmdr.py --once \"<task>\""
    info "add a chat account later, no reinstall of the app needed:"
    info "  re-run with --token-file <file>            (Mattermost)"
    info "  or with --telegram-token <t> --telegram-ids <id>   (Telegram)"
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

# Every file this installer writes under $INSTALL_DIR is ours alone - config.json can
# hold a live llm.api_key, .env holds the tokens, and the log transcribes both. Owned by
# one user, readable by one user: umask 077 from here to the end of the writes, plus an
# explicit chmod on each file, because config.json used to be opened 0644 while .env was
# written 0600 (I13).
umask 077

TOKEN="$TOKEN" MM_URL_ARG="$MM_URL_ARG" ALLOWED_ARG="$ALLOWED_ARG" BOT_NAME="$BOT_NAME" \
CLOUD_FALLBACK="$CLOUD_FALLBACK" \
TG_IDS_CLEAN="$TG_IDS_CLEAN" \
MODEL_BASE_URL="$MODEL_BASE_URL" MODEL="$MODEL" \
MODEL_BASE_GIVEN="$MODEL_BASE_GIVEN" MODEL_GIVEN="$MODEL_GIVEN" \
MODEL_KEY="$MODEL_KEY" \
FALLBACK_SPECS="$FALLBACK_SPECS" FB_ENV_LINES="$FB_ENV_LINES" \
MM_PORT_ARG="$MM_PORT_ARG" \
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
_mm_port = os.environ.get("MM_PORT_ARG", "").strip()
if _mm_port.isdigit():
    mm["port"] = int(_mm_port)
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
if os.environ.get("CLOUD_FALLBACK") == "true":
    llm["allow_cloud_fallback"] = True
# Only when the caller chose: --model-base-url/--use-fleet-model/--model. The
# defaults exist for a FIRST install, and re-applying them over a working host is
# how a LAN endpoint became a cloud one on an update.
if os.environ.get("MODEL_BASE_GIVEN") or fresh:
    llm["base_url"] = os.environ["MODEL_BASE_URL"]
if os.environ.get("MODEL_GIVEN") or fresh:
    llm["model"] = os.environ["MODEL"]
if os.environ.get("MODEL_KEY"):
    # The PRIMARY's key lives in .env as TINYCMDR_LLM_API_KEY (env_map resolves it into
    # llm.api_key), never here: config.json is a file the agent reads into a prompt.
    # Migrate any old copy away.
    llm.pop("api_key", None)
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
elif fresh:
    # config.example.json carries a placeholder fallback (api.example.com, with a key
    # variable nobody has): a fresh install must not inherit an endpoint that does not
    # exist. An update keeps whatever the host already had.
    llm["fallbacks"] = []
with open(dst, "w", encoding="utf-8", newline="\n") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print("    config.json: %s (mattermost=%s, model=%s, allowed_users=%s)"
      % ("kept this host's settings, applied what this run changed" if not fresh
         else "written", mm.get("url", "?"), llm.get("model", "?"),
         mm.get("allowed_users", [])))
if _specs:
    print("    fallbacks  : %d extra endpoint(s), keys in .env" % len(llm.get("fallbacks") or []))
PY
chmod 600 "$INSTALL_DIR/config.json"

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
MANAGED_ALT='TINYCMDR_MM_TOKEN|TINYCMDR_TG_TOKEN'
# The extra-endpoint keys are managed only when THIS run wrote them: on a scripted
# update the host's own lines must be carried over, or an answered install would
# drop the keys its own config.json still points at. The primary's key is managed the
# same way - a redo with no key keeps the host's own TINYCMDR_LLM_API_KEY line.
if [ -n "$FB_ENV_LINES" ]; then
    MANAGED_ALT="$MANAGED_ALT|TINYCMDR_ENDPOINT[0-9]+_API_KEY"
fi
if [ -n "$MODEL_KEY" ]; then
    MANAGED_ALT="$MANAGED_ALT|TINYCMDR_LLM_API_KEY"
fi
MANAGED_KEYS="^($MANAGED_ALT)="
# What the secrets file will actually supply, so nothing is written twice: a duplicate
# line for a key the install already wrote is not harmless - the build keeps the FIRST
# occurrence, so whichever copy landed first is the one the bot uses.
SECRET_SHARED=""
SECRET_SKIPPED=""
if [ -n "$SECRETS_FILE" ]; then
    [ -f "$SECRETS_FILE" ] || die "--secrets-file $SECRETS_FILE does not exist"
    # The installer-managed keys are excluded: they were written from this run's own
    # resolved values, and the file's TINYCMDR_MM_TOKEN is the one that made TOKEN
    # non-empty in the first place.
    SECRET_SHARED=$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$SECRETS_FILE" \
        | grep -E "$SHARED_KEYS" | grep -vE "$MANAGED_KEYS" | cut -d= -f1 || true)
    SECRET_SKIPPED=$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$SECRETS_FILE" \
        | grep -vE "$SHARED_KEYS" | cut -d= -f1 | tr '\n' ' ' || true)
fi
KEEP_ENV=""
if [ -f "$INSTALL_DIR/.env" ]; then
    KEEP_ENV=$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$INSTALL_DIR/.env" \
        | grep -vE "$MANAGED_KEYS" || true)
    # a key the secrets file is about to carry is dropped here: the file's copy wins
    for _k in $SECRET_SHARED; do
        KEEP_ENV=$(printf '%s\n' "$KEEP_ENV" | grep -vE "^${_k}=" || true)
    done
fi
SKIPPED_KEYS="$SECRET_SKIPPED"
{
    printf 'TINYCMDR_MM_TOKEN=%s\n' "$TOKEN"
    if [ -n "$TG_TOKEN" ]; then
        printf 'TINYCMDR_TG_TOKEN=%s\n' "$TG_TOKEN"
    fi
    # The primary's key: only when THIS run resolved one (a switch or an answer). An
    # empty value means "leave this host's own", and KEEP_ENV carries that line over.
    if [ -n "$MODEL_KEY" ]; then
        printf 'TINYCMDR_LLM_API_KEY=%s\n' "$MODEL_KEY"
    fi
    if [ -n "$FB_ENV_LINES" ]; then
        printf '%s' "$FB_ENV_LINES"
    fi
    # Only when this run set it (the switch, or the answer): an empty value is "leave
    # this host's own", and KEEP_ENV below already carries that line over. It goes
    # BEFORE the carried lines, because the loader keeps the FIRST occurrence - a stale
    # line underneath must never win over the value this run just resolved.
    if [ -n "$SEARCH_EGRESS" ]; then
        printf 'TINYCMDR_SEARCH_EGRESS=%s\n' "$SEARCH_EGRESS"
    fi
    if [ -n "$KEEP_ENV" ]; then
        printf '%s\n' "$KEEP_ENV"
    fi
    # LAST, so the fleet's copy of a shared key wins over one the host already had.
    for _k in $SECRET_SHARED; do
        grep -E "^${_k}=" "$SECRETS_FILE" | tail -n1 || true
    done
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

if [ -z "$TOKEN" ] && [ -z "$TG_TOKEN" ]; then
    # Nothing on a token-less install reads mattermost.url, so a missing host is not a
    # problem to report (the Linux installer states the same rule).
    info "no chat account: nothing remote to serve (only --app / --cli / --once here)"
elif [ -z "$TOKEN" ] && [ -z "$MM_URL_ARG" ]; then
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
    # Only a CHAT lane has anybody to ignore: a token-less install reads no allowlist.
    warn "mattermost.allowed_users is empty, and this bot is deny-by-default: it will ignore"
    warn "every DM until you add your Mattermost user id (--allowed-user <id>)."
fi

# ------------------------------------------------------- model endpoint ---
# macOS raises its Local Network permission prompt the FIRST time a process connects to a
# private address, and it raises it in whatever process dials - which, for this install, is
# the agent, from a background launchd job at boot, where nobody can answer it. Unanswered,
# that is silent and easy to misread: the log says only that the endpoint "did not answer"
# and the run carries on with an assumed 14,349-token window, while the model box is fine.
# So dial once, here, from the venv's own python - the exact binary the agent will use, so
# the approval lands on the one that matters - with the operator watching. Never fatal: a
# check, not a gate (measured on a live install, 2026-09-27).
if [ -x "$VPY" ] && [ -f "$INSTALL_DIR/config.json" ]; then
    say "model endpoint"
    PROBE_OUT="$(TINYCMDR_LLM_API_KEY="$MODEL_KEY" "$VPY" - "$INSTALL_DIR/config.json" <<'PROBEPY'
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
lan = bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|169\.254\.|100\.(6[4-9]|[7-9][0-9]|1[0-2][0-9])\.)", host)) \
    or host.endswith(".local")
print("fail|%s|%s" % (err, "lan" if lan else "other") if err
      else "ok|%s|%s" % (base, window))
PROBEPY
)" || true
    case "$PROBE_OUT" in
        ok\|*)  info "endpoint      : reachable (${PROBE_OUT#ok|})" ;;
        fail\|*lan)
                 warn "endpoint      : did not answer: $(printf '%s' "${PROBE_OUT#fail|}" | cut -d'|' -f1)"
                 warn "on macOS that is often the Local Network permission, not a dead server:"
                 warn "System Settings -> Privacy & Security -> Local Network, and allow the"
                 warn "python this install created ($VPY). The agent retries on its own."
                 ;;
        fail\|*) warn "endpoint      : did not answer: $(printf '%s' "${PROBE_OUT#fail|}" | cut -d'|' -f1) - the agent retries on its own" ;;
    esac
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
    elif mkdir -p "$RUN_HOME/.local/bin" 2>/dev/null; then
        VERB_PATH="$RUN_HOME/.local/bin"
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
    if [ "$VERB_PATH" = "$RUN_HOME/.local/bin" ]; then
        case ":$PATH:" in
            *":$RUN_HOME/.local/bin:"*) ;;
            *) if [ "$IS_MAC" = 1 ]; then
                   path_line "$RUN_HOME/.zshrc" 1                 # macOS default shell
                   [ -f "$RUN_HOME/.bash_profile" ] && path_line "$RUN_HOME/.bash_profile" 0
               fi ;;
        esac
    fi
else
    info "verbs      : not on PATH - run $INSTALL_DIR/tinycmdr (or re-run without --no-path)"
fi
if [ "$HAS_LANE" = 0 ]; then
    say "no agent"
    info "no chat account: the launchd agent is not registered - an agent with no lane"
    info "would exit at once and KeepAlive would loop it forever. The files and the verb"
    info "above are in place; the app (--app), the inline lane (--cli) and a one-shot (--once) work now."
    info "add a chat token and re-run to register the agent:"
    info "  --token-file <file>  (Mattermost)  |  --telegram-token <t> --telegram-ids <id>"
else
mkdir -p "$PLIST_DIR"
TPL="$SRC/install/com.tinycmdr.agent.plist"
[ -f "$TPL" ] || die "package is missing install/com.tinycmdr.agent.plist"
sed -e "s|__LABEL__|$LABEL|g" -e "s|__PYTHON__|$VPY|g" -e "s|__APP__|$INSTALL_DIR|g" \
    "$TPL" > "$PLIST"
plutil -lint "$PLIST" >/dev/null || die "the generated plist is not valid: $PLIST"
info "wrote $PLIST"
# Write the label down where --uninstall can find it: a `--label <l>` install used to be
# unremovable by every shipped door, because nothing recorded which job it had registered.
# Done BEFORE the load, so a failed bootstrap still leaves the label recorded.
printf '%s\n' "$LABEL" > "$INSTALL_DIR/$LABEL_FILE_NAME"
chmod 600 "$INSTALL_DIR/$LABEL_FILE_NAME" 2>/dev/null || true

if [ "$NO_START" = 1 ]; then
    info "--no-start: not loading the agent"
else
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null \
        || launchctl load -w "$PLIST" \
        || die "launchctl could not load $PLIST (see $LOGDIR/launchd.err.log)"
    info "agent loaded as $LABEL"
fi
fi

say "done"
info "install dir : $INSTALL_DIR"
if [ "$HAS_LANE" = 1 ]; then
    info "agent       : $LABEL  ($PLIST)"
    info "logs        : $INSTALL_DIR/tinycmdr.log, $LOGDIR/launchd.err.log"
    info "restart     : bash $INSTALL_DIR/maintenance/restart-tinycmdr-macos.sh"
else
    info "agent       : none - no chat account, so nothing runs in the background"
    info "session     : $VPY $INSTALL_DIR/tinycmdr.py --cli"
    info "one task    : $VPY $INSTALL_DIR/tinycmdr.py --once \"<task>\""
    info "add a lane  : re-run with --token-file <file>, or"
    info "              --telegram-token <t> --telegram-ids <id>"
fi
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
if [ "$HAS_LANE" = 1 ]; then
    info "the bot answers DMs from the users in mattermost.allowed_users only"
fi
if [ "${SEARCH_EGRESS:-false}" = "true" ]; then
    info "web search  : off-LAN allowed (search.allow_cloud_egress=true)"
else
    info "web search  : LAN only - an off-LAN provider is refused until"
    info "              search.allow_cloud_egress=true. A provider on this LAN never"
    info "              needs it: tinycmdr config set search.providers '<json>'"
fi
