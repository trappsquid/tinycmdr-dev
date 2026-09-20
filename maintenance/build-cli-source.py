"""Generate tinycmdr-cli.py from tinycmdr.py by anchored cuts.

    python maintenance/build-cli-source.py

Every anchor must match exactly once (or within a stated region) or the script
exits without writing anything. Nothing here guesses at a line number: the cut
spans come from the AST of the file as it stands at that moment. Regenerate after
any change to tinycmdr.py, then re-run:  python tests/test_cli.py
and  tinycmdr_SRC=tinycmdr-cli.py python tests/test_ledger.py
"""
import ast
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cli_blocks import (NEW_CLI, NEW_CONFIG, NEW_HEADER, NEW_MAIN, NEW_VALIDATOR,
                        ONE_ENDPOINT, HTTP_SHIM)  # noqa: E402

SRC = os.path.join(os.path.expanduser("~"), "tinycmdr", "tinycmdr.py")
DST = os.path.join(os.path.expanduser("~"), "tinycmdr", "tinycmdr-cli.py")

raw = open(SRC, "rb").read().decode("utf-8")
CR = chr(13)
LF = chr(10)
NL = CR + LF if raw.count(CR + LF) == raw.count(LF) else LF
print("source newline: %r (%d CRLF of %d LF)" % (NL, raw.count(CR + LF), raw.count(LF)))
lines = raw.split(NL)
if lines and lines[-1] == "":
    del lines[-1]
ORIG = len(lines)
log = []


def die(msg):
    print("REFUSING:", msg)
    sys.exit(1)


def hits(sub, region=None):
    out = [i for i, l in enumerate(lines) if sub in l]
    if region:
        out = [i for i in out if region[0] <= i <= region[1]]
    return out


def one(sub, region=None):
    h = hits(sub, region)
    if len(h) != 1:
        die("anchor %r matched %d times%s" % (sub, len(h), " in region" if region else ""))
    return h[0]


def cut(a, b, why):
    if not a < b:
        die("bad cut range for %s (%d..%d)" % (why, a, b))
    log.append("cut      %-38s %5d lines" % (why, b - a))
    del lines[a:b]


def brace_block(start_sub, why):
    """Deprecated: kept only to fail loudly if still referenced."""
    die("brace_block is gone; use AST spans")


TREE = ast.parse(NL.join(lines))


def reparse():
    global TREE
    TREE = ast.parse(NL.join(lines))


def span_of_assign(name):
    """(first, last) 1-based line numbers of a module-level assignment."""
    reparse()
    for n in ast.walk(TREE):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in n.targets):
            return n.lineno, n.end_lineno
    die("no assignment to %s" % name)


def span_of_dict_key(key):
    """Line span of the VALUE stored under `key` in a module-level dict literal."""
    reparse()
    best = None
    for n in ast.walk(TREE):
        if isinstance(n, ast.Dict):
            for i, k in enumerate(n.keys):
                if isinstance(k, ast.Constant) and k.value == key:
                    v = n.values[i]
                    best = (k.lineno, v.end_lineno)
    if best is None:
        die("no dict entry for key %r" % key)
    return best


def replace_block(start_sub, end_sub, new_text, why, region=None):
    a = one(start_sub, region)
    b = one(end_sub, region)
    if not a < b:
        die("bad replace range for %s" % why)
    log.append("replace  %-38s %5d lines -> %d" % (why, b - a + 1, new_text.count("\n") + 1))
    lines[a:b + 1] = new_text.splitlines()


def insert_before(sub, new_text, why, region=None):
    a = one(sub, region)
    log.append("insert   %-38s before line %d" % (why, a + 1))
    lines[a:a] = new_text.splitlines()


def drop_line(sub, why, region=None):
    a = one(sub, region)
    log.append("drop     %-38s line %d" % (why, a + 1))
    del lines[a]


def one_line(pat, why):
    """Anchor on a whole line (regex, unanchored start means any indent)."""
    h = [i for i, l in enumerate(lines) if re.match(pat, l)]
    if len(h) != 1:
        die("line anchor %r (%s) matched %d times" % (pat, why, len(h)))
    return h[0]


def replace_line(old_sub, new_text, why, region=None):
    a = one(old_sub, region)
    log.append("reline   %-38s line %d" % (why, a + 1))
    lines[a] = new_text


# ---------------------------------------------------------------- 1. docstring
replace_block("tinycmdr.py — a tiny autonomous ops agent + Mattermost bot.",
              'One-shot task: python tinycmdr.py --once "why is plex crashing"',
              NEW_HEADER, "module docstring")

# ------------------------------------------------------- 2. imports + shim
drop_line("import requests", "third-party http import")
insert_before("import socket", "import signal\nimport ssl\nimport urllib.error\nimport urllib.request",
              "stdlib http imports")
insert_before("def _post_watchdog(", HTTP_SHIM, "stdlib requests shim")

# ---------------------------------------------------------- 3. config block
cfg_a, cfg_b = span_of_assign("DEFAULT_CONFIG")
cfg_a -= 1                                    # AST is 1-based and inclusive
log.append("replace  %-38s %5d lines -> %d" % ("DEFAULT_CONFIG", cfg_b - cfg_a,
                                               NEW_CONFIG.count("\n") + 2))
lines[cfg_a:cfg_b] = ("DEFAULT_CONFIG = " + NEW_CONFIG).splitlines()

# ------------------------------------------------- 3b. the config keys cannot drift
# NEW_CONFIG in cli_blocks.py is a CURATED copy of the real agent defaults, and a copy
# drifts: by 2.3.0 this block was 31 keys behind, so the enterprise build silently ran
# the new features on their code fallbacks and any direct CONFIG["agent"][key] lookup
# raised KeyError. (Found by running tests/test_plan.py against this build, which until
# then only ever targeted tinycmdr.py.) The curated wording stays; the KEYS are copied
# here, at the END of the build, from the source defaults — minus the ones whose code the
# cuts above removed, because a chat-only key in this build is decoration.
SRC_TREE = ast.parse(raw)


def agent_dict(tree):
    """The literal dict assigned to DEFAULT_CONFIG["agent"] in the tree given."""
    for n in ast.walk(tree):
        if (isinstance(n, ast.Assign) and n.targets
                and getattr(n.targets[0], "id", "") == "DEFAULT_CONFIG"):
            for k, v in zip(n.value.keys, n.value.values):
                if isinstance(k, ast.Constant) and k.value == "agent":
                    return v
    die("no DEFAULT_CONFIG['agent'] in the tree")


def sync_agent_keys():
    _real = agent_dict(SRC_TREE)
    real_agent = {k.value: v for k, v in zip(_real.keys, _real.values)
                  if isinstance(k, ast.Constant)}
    # Keys this build adds on purpose, so they must not read as stale. "shell" selects
    # which interpreter the shell tool uses on the host the CLI runs on; it exists only
    # in this build's config, not in the bot's defaults.
    cli_only = {"shell"}
    reparse()
    gen = agent_dict(TREE)
    have = {k.value for k in gen.keys if isinstance(k, ast.Constant)}
    stale = sorted(k for k in have if k not in real_agent and k not in cli_only)
    if stale:
        die("the config block defines keys the app does not have: %s" % ", ".join(stale))
    missing = [k for k in real_agent if k not in have]
    body = NL.join(lines)                  # everything is cut by now: this is the build
    keep = [k for k in missing if k in body]
    skipped = [k for k in missing if k not in body]
    add = ['        "%s": %s,' % (k, (ast.get_source_segment(raw, real_agent[k])
                                      or "None").replace("\n", " "))
           for k in keep]
    at = gen.end_lineno - 1                # before the dict's closing brace
    lines[at:at] = add
    log.append("inherit  %-38s %5d keys" % ("agent defaults this build reads", len(add)))
    print("config keys inherited : %s" % (", ".join(keep) or "none"))
    print("config keys left out  : %s" % (", ".join(skipped) or "none"))


drop_line('"tinycmdr_MM_TOKEN": ("mattermost", "token"),', "mattermost env override")
fa = one("    # fallback endpoints can name their own env var (api_key_env) so provider")
fb = one('            fb["api_key"] = os.environ[env_name]')
cut(fa, fb + 1, "fallback env loop in load_config")
drop_line('"ANYSEARCH_API_KEY": ("search", "anysearch_api_key"),', "search env key 1")
drop_line('"TAVILY_API_KEY": ("search", "tavily_api_key"),', "search env key 2")

# ------------------------------------------------------ 4b. no web search at all
sa = one("def _anysearch(query, max_results):")
sb = one("def tool_fetch_url(")
cut(sa, sb, "web search providers + web_search tool")
sp = span_of_dict_key("web_search")            # the tool registry entry
if '"web_search"' not in lines[sp[0] - 1]:
    die("the web_search registry entry span looks wrong: %s -> %r"
        % (sp, lines[sp[0] - 1][:60]))
cut(sp[0] - 1, sp[1], "web_search tool schema")
sa = one("def tool_fetch_url(args, ctx):")
sb = one("NOTE_LINE_RE = re.compile(")     # the notes section header follows the function
cut(sa, sb, "fetch_url tool (single network destination)")
sp = span_of_dict_key("fetch_url")             # the tool registry entry
if '"fetch_url"' not in lines[sp[0] - 1]:
    die("the fetch_url registry entry span looks wrong: %s -> %r"
        % (sp, lines[sp[0] - 1][:60]))
cut(sp[0] - 1, sp[1], "fetch_url tool schema")

# ------------------------------------------------------------ 4. secrets
# With no chat and no search there are no config sections left holding secrets, so
# the whole config-derived sweep goes; the environment sweep below it stays.
sv = one("def _secret_values():")
sa = one('    for section in ("mattermost", "search", "web"):', region=(sv, sv + 40))
sb = one('            vals.add(fb["api_key"])', region=(sv, sv + 40))
cut(sa, sb + 1, "config-derived secrets")

# -------------------------------------------------- 5. scheduler + tool cut
sa = one("# Scheduler (cron gateway equivalent)")
sb = one("def _schema(")
cut(sa, sb, "Scheduler class")
sa = one("def tool_schedule(args, ctx):")
sb = one("def tool_search_sessions(")
cut(sa, sb, "tool_schedule")
sp = span_of_dict_key("schedule")             # the tool registry entry
if '"schedule"' not in lines[sp[0] - 1]:
    die("the schedule registry entry span looks wrong: %s -> %r"
        % (sp, lines[sp[0] - 1][:60]))
cut(sp[0] - 1, sp[1], "schedule tool schema")
drop_line("SCHEDULER = Scheduler(JOBS_FILE)", "SCHEDULER instance")

# --------------------------------------------- 6. one endpoint, no failover
ch = one("    def _chat(self, messages, model=None, use_tools=True, usage=None,")
ra = one("        want = str(model).strip().lower()", region=(ch, ch + 400))
rb = one("        ordered = head + [primary] + tail", region=(ch, ch + 400))
log.append("replace  %-38s %5d lines -> %d" % ("routing gate", rb - ra + 1,
                                               ONE_ENDPOINT.count("\n") + 1))
lines[ra:rb + 1] = ONE_ENDPOINT.splitlines()

# ------------------------------------ 7. chat layer + model_command + lock
ma = one("def model_command(")
mb = one("def run_cli(")
cut(ma, mb, "model_command + Mattermost layer")

la = one_line(r"_LOCK_FH = None$", "lock sentinel")
lb = one("def validate_startup_config(")
cut(la, lb, "lock + restart + allowed_users")

# ------------------------------------------------- 8. new cli, validator, main
ca = one("def run_cli(once=None):")
cb = one("def validate_startup_config(") - 1
log.append("replace  %-38s %5d lines -> %d" % ("run_cli -> CLI", cb - ca + 1,
                                               NEW_CLI.count("\n") + 1))
lines[ca:cb + 1] = NEW_CLI.splitlines()

va = one("def validate_startup_config():")
log.append("replace  %-38s %5d lines -> %d" % ("validator + main", len(lines) - va,
                                               (NEW_VALIDATOR + NEW_MAIN).count("\n") + 2))
lines[va:] = (NEW_VALIDATOR + "\n" + NEW_MAIN).splitlines()

sync_agent_keys()          # after every cut: the keys this build can actually read
out = NL.join(lines) + NL

if "requests.post(url, headers=headers" not in out:
    die("shim lost the post call site")
for banned in ("mmpy_bot", "croniter"):
    if banned in out:
        die("third-party token still present: %s" % banned)

tree = ast.parse(out)

# name check: anything referenced but never defined/imported at module level
defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)}
imported = set()
for n in ast.walk(tree):
    if isinstance(n, ast.Import):
        imported |= {a.asname or a.name.split(".")[0] for a in n.names}
    elif isinstance(n, ast.ImportFrom):
        imported |= {a.asname or a.name for a in n.names}
builtins = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))
known = defined | assigned | imported | builtins | {"CONFIG", "AGENT", "REGISTRY", "log", "self"}
suspicious = {"head", "tail", "fallbacks", "hit", "model_command", "SCHEDULER", "Scheduler",
              "ProgressReporter", "MattermostDispatcher", "REPORTER", "run_bot", "run_webui",
              "status_text", "bar_props", "want_color", "_tool_preview", "tool_schedule",
              "user_is_allowed", "acquire_single_instance_lock", "perform_restart",
              "announce_startup", "restart_owner", "_spawn_replacement", "_note_restart",
              "_release_lock", "_LOCK_FH", "_CatchUpMessage", "_web_command", "_exit_code"}
leftovers = sorted(s for s in suspicious if re.search(r"\b%s\b" % re.escape(s), out))
open(DST, "w", encoding="utf-8", newline="").write(out)

print("\n".join(log))
print("\nwrote %s" % DST)
print("lines: %d -> %d" % (ORIG, out.count(NL)))
print("sha256: %s" % hashlib.sha256(out.encode()).hexdigest()[:16])
print("leftover references to cut subsystems: %s" % (", ".join(leftovers) or "none"))
for word in ("mattermost", "Mattermost", "channel_id", "channel"):
    n = len(re.findall(word, out))
    if n:
        print("  still mentions %-12s %d time(s)" % (word, n))
