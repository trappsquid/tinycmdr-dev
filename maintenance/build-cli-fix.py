"""Post-generation fixes: strip the last chat-era wording from tinycmdr-cli.py.

Every replacement must apply exactly once (the two shell messages twice), or the
script refuses and changes nothing.
"""
import os
import re
import sys

P = os.path.join(os.path.expanduser("~"), "tinycmdr", "tinycmdr-cli.py")
CR, LF = chr(13), chr(10)
raw = open(P, "rb").read().decode("utf-8")
NL = CR + LF if raw.count(CR + LF) == raw.count(LF) else LF
s = raw
applied = []


def sub(old, new, why, count=1):
    global s
    n = s.count(old)
    if n != count:
        print("REFUSING: %s matched %d times (expected %d)" % (why, n, count))
        sys.exit(1)
    s = s.replace(old, new)
    applied.append("%-34s %d" % (why, count))


def sub_re(pattern, new, why, count=1):
    global s
    hits = re.findall(pattern, s)
    if len(hits) != count:
        print("REFUSING: %s matched %d times (expected %d)" % (why, len(hits), count))
        sys.exit(1)
    s = re.sub(pattern, new, s, count=count)
    applied.append("%-34s %d" % (why, count))


# 1. the system prompt: who the operator is and where this runs
sub("The operator messages you via Mattermost; you do the work and report back.",
    "The operator is sitting at the terminal with you; you do the work and report back.",
    "prompt: identity")

sub('Narrate as you go: the operator watches the chat.',
    'Narrate as you go: the operator is watching the terminal.',
    "prompt: narration target")

sub("Under 15 words, no headers, no preamble — it is posted as its own message "
    "the moment you emit it, then the tools run.",
    "Under 15 words, no headers, no preamble — it is printed the moment you emit "
    "it, then the tools run.",
    "prompt: narration delivery")

sub('- Anything recurring ("check X every morning", "hourly") becomes a schedule '
    "job — it runs autonomously and reports back to the channel. Use "
    "search_sessions to recall how past issues were solved, and delegate_task to "
    "farm out self-contained subtasks in parallel.",
    '- Anything recurring ("check X every morning", "hourly") cannot be scheduled '
    "from here: this build has no cron, and nothing runs while the window is "
    "closed. Say that in the final report and give the exact one-shot command "
    'line (tinycmdr.py --once "...") so the operator can hand it to their own '
    "scheduler. Use search_sessions to recall how past issues were solved, and "
    "delegate_task to farm out self-contained subtasks.",
    "prompt: no cron")

sub("so that reads as a broken bot.", "so that reads as a broken agent.",
    "prompt: broken bot wording")

# 2. the two timeout messages the operator sees
sub("would freeze this channel). Partial output:",
    "would freeze this session). Partial output:", "timeout message", count=2)

# 3. a docstring that explained the design via the old chat incident
sub("One channel is served by ONE worker thread, so that channel"
    + NL + "    then goes permanently deaf: no error, no log line, messages "
    "queue behind" + NL + "    it silently. That is what froze the DM channel "
    "for 21 minutes on" + NL + "    2026-09-10,",
    "One conversation is served by one worker, so that session then goes"
    + NL + "    permanently deaf: no error, no log line, the next request "
    "queues behind it" + NL + "    silently. That is what froze a live session "
    "for 21 minutes on 2026-09-10,",
    "docstring: worker design")

# 4. the model catalog: no failover list to enumerate
lines = s.split(NL)
i = lines.index('    for fb in CONFIG["llm"].get("fallbacks", []):')
j = i + 1
while j < len(lines) and (lines[j].startswith("        ") or not lines[j].strip()):
    j += 1
del lines[i:j]
s = NL.join(lines)
applied.append("%-34s %d" % ("model catalog: no fallbacks", j - i))

sub("# Model catalog / switching — shared by Mattermost, the web UI and /status",
    "# Model catalog / switching — used by /model and /status",
    "model catalog: comment")

def line_sub(old, new, why):
    """Replace one whole line (None deletes it). Refuses on any ambiguity."""
    global s
    ls = s.split(NL)
    idx = [i for i, l in enumerate(ls) if l == old]
    if len(idx) != 1:
        print("REFUSING: line %s matched %d times" % (why, len(idx)))
        sys.exit(1)
    if new is None:
        del ls[idx[0]]
    else:
        ls[idx[0]] = new
    s = NL.join(ls)
    applied.append("%-34s 1" % why)

def replace_lines(first_line, n, new_text, why):
    """Replace a run of n whole lines starting at first_line. Refuses on ambiguity."""
    global s
    ls = s.split(NL)
    idx = [i for i, l in enumerate(ls) if l == first_line]
    if len(idx) != 1:
        print("REFUSING: %s matched %d times" % (why, len(idx)))
        sys.exit(1)
    i = idx[0]
    ls[i:i + n] = new_text.split(chr(10)) if new_text else []
    s = NL.join(ls)
    applied.append("%-34s %d lines" % (why, n))


line_sub('    """Every model name this bot can actually route to, resolved live.',
         '    """Every model name this endpoint can actually serve, resolved live.',
         "catalog docstring: head")
line_sub("    Names come from three places: the local server's own advertised ids, each",
         "    The names come from the endpoint's own advertised ids, so /model list shows",
         "catalog docstring: line 1")
line_sub("    fallback's configured model + optional alias, and the ids the fallback",
         "    what is really there rather than what config.json hopes is there. Each",
         "catalog docstring: line 2")
line_sub("    endpoint itself advertises (DeepSeek answers 'deepseek-flash'). Each entry",
         "    entry carries the exact send_as id, so a name is never forwarded verbatim",
         "catalog docstring: line 3")
line_sub("    carries the exact `send_as` id, so an alias — or a foreign id — is never",
         "    to a server that would not accept it.",
         "catalog docstring: line 4")
line_sub("    forwarded verbatim to a server that wouldn't accept it.", None,
         "catalog docstring: line 5")

sub("open(P, \"wb\").write", "open(P, \"wb\").write", "noop", count=0) if False else None

sub('    """The two verbs this file uses, plus the exception type it catches."""',
    '    """The two verbs this file uses, plus the exception types it catches."""',
    "shim docstring plural")

# The enterprise build carries its OWN version, and it moves when its bytes move: 1.0.0 was
# published before the config-drift fix, and re-cutting different bytes under a published
# name would make the site's version-anchored claims untrue.
sub_re(r'VERSION = "[0-9]+\.[0-9]+\.[0-9]+"', 'VERSION = "1.0.11"',
       "enterprise version number")

# The visible-core list names the web tools, which this build cuts. core_tool_names()
# filters by what exists, so behaviour is already right; this keeps the generated file
# from advertising a tool that is not there (and keeps test_cli's token scan honest).
# Regex rather than a literal: the tuple is edited whenever the core set changes, and an
# anchored literal string broke this build twice for the same reason.
sub_re(r'_DEFAULT_CORE = \([^)]*\)',
       '_DEFAULT_CORE = ("shell", "execute_code", "read_file", "write_file", "edit_file",'
       + NL + '                 "skill", "task", "remember", "list_tools", '
       '"find_tools")',
       "core list: no web tools in this build")




sub("- Prefer the OS's native mechanisms for routine maintenance — they are faster "
    "and safer than manual alternatives. On Windows: Windows Update "
    "(Microsoft.Update.Session COM or PSWindowsUpdate module), pnputil, winget, DISM, "
    "Get-ComputerInfo. On Linux: the system package manager, systemctl, journalctl, "
    "docker. Downloading installers from vendor websites or scraping download pages is "
    "the LAST resort for when native channels genuinely lack the software.",
    "- Use what the machine already has. This build sits on a closed network: no package "
    "manager reaches a repository, no update channel answers, no installer can be "
    "downloaded, and the only thing that leaves this process is a request to the model "
    "endpoint. Prefer the OS's own native mechanisms — on Windows pnputil, DISM with a "
    "local image, services and event logs; on Linux systemctl, journalctl, docker — and "
    "the software that is already installed. Never start an update or an install. If a "
    "fix genuinely needs something that is not on the machine, say so, name exactly what "
    "you would need, and stop there.",
    "prompt: closed network, no updates")

sub("- Web search is for the UNFAMILIAR: an error you don't recognize, a "
    "version-specific quirk, something that smells like a known issue — check GitHub "
    "issues, Reddit, and forums for the exact error message, early and in parallel "
    "with local checks. For routine procedures you already know (updates, service "
    "restarts, log checks), just do them — no research phase.",
    "- This build is closed: there is NO web search and NO URL fetching, and the only "
    "network destination is the model endpoint in config.json. Work from the machine's "
    "own evidence — logs, configs, package metadata, vendor documents already on disk, "
    "the source of whatever is failing — and when a question genuinely needs "
    "information from outside, say so, say exactly what you would need, and stop "
    "rather than guessing at an answer.",
    "prompt: closed network")

sub("- Time-box research: if two or three searches haven't cracked the problem, act on "
    "what you have or report back with options. Never spelunk the web for ten minutes "
    "on a task with a built-in command.",
    "- Time-box the digging: if reading the local evidence twice has not cracked it, act "
    "on what you have, or report the options and what you would need to go further.",
    "prompt: time-box digging")

# --- no script files ship: the agent is started from the operator's own Python --
# The enterprise folder is tinycmdr.py, a README and a config example. No .bat, no
# .ps1, no installer: environments that whitelist executables block those outright.

# --- and the shell can be cmd instead of PowerShell ----------------------------
# --- the shell interpreter is configurable (no PowerShell required) -----------
replace_lines('    shell_argv = (["powershell", "-NoProfile", "-Command", command] if IS_WINDOWS',
              2,
              NL.join([
                  '    # agent.shell picks the interpreter. The default is PowerShell; "cmd"',
                  '    # is for hosts where PowerShell is restricted or removed, which is common',
                  '    # where executables and scripts are whitelisted. The blocklist covers',
                  '    # cmd\'s own destructive forms either way.',
                  '    _shellcfg = str((CONFIG["agent"].get("shell") or "powershell")).strip().lower()',
                  '    if IS_WINDOWS:',
                  '        shell_argv = (["cmd", "/c", command] if _shellcfg in ("cmd", "cmd.exe")',
                  '                      else ["powershell", "-NoProfile", "-Command", command])',
                  '    else:',
                  '        shell_argv = ["bash", "-c", command]',
              ]),
              "shell interpreter from config")

replace_lines('    shell_name = "PowerShell" if IS_WINDOWS else "bash"',
              1,
              NL.join([
                  '    _shellcfg = str((cfg["agent"].get("shell") or "powershell")).strip().lower()',
                  '    shell_name = (("cmd" if _shellcfg in ("cmd", "cmd.exe") else "PowerShell")',
                  '                  if IS_WINDOWS else "bash")',
              ]),
              "prompt names the real shell")


# --- refuse to share a folder with the Mattermost bot -------------------------
# Checked at import, before the log, the sessions directory or any note file is
# created: both builds keep the same names in the folder they live in, so starting
# this one from a bot folder would merge two memories.
replace_lines('NOTES_ARCHIVE_FILE = BASE_DIR / "notes-archive.md"   # notes evicted from the prompt', 1,
              NL.join([
                  'NOTES_ARCHIVE_FILE = BASE_DIR / "notes-archive.md"   # notes evicted from the prompt',
                  '',
                  'def _folder_belongs_to_the_bot():',
                  '    """True when a Mattermost bot keeps its memory in this folder too."""',
                  '    other = BASE_DIR / "tinycmdr.py"',
                  '    try:',
                  '        mine = Path(sys.argv[0]).resolve()',
                  '    except Exception:',
                  '        mine = None',
                  '    return (other.exists() and other.resolve() != mine',
                  '            and (BASE_DIR / "tinycmdr.lock").exists())',
                  '',
                  '',
                  'def _console_closes_with_us():',
                  '    """True when this process owns its console, so the window dies with it.',
                  '',
                  '    A double-click (Explorer -> the .py association -> this script) gets a console',
                  '    with nothing else attached to it, so a fatal startup message leaves with the',
                  '    window: that is how "the CLI does not start" gets reported when it started,',
                  '    refused the config, and printed why. Started from cmd or PowerShell the shell',
                  '    is attached too and the window stays. Measured 2026-09-15: own console ->',
                  '    GetConsoleProcessList == 1, inside a shell -> 2.',
                  '    """',
                  '    if os.name != "nt":',
                  '        return False',
                  '    try:',
                  '        import ctypes',
                  '        k32 = ctypes.windll.kernel32',
                  '        if not k32.GetConsoleWindow():',
                  '            return False                 # no console at all (pythonw, a service)',
                  '        buf = (ctypes.c_uint32 * 8)()',
                  '        return k32.GetConsoleProcessList(buf, 8) == 1',
                  '    except Exception:',
                  '        return False',
                  '',
                  '',
                  'def _hold_console():',
                  '    """Keep the reason on screen when the console is ours to lose."""',
                  '    if not _console_closes_with_us():',
                  '        return',
                  '    print()',
                  '    try:',
                  '        input("press Enter to close this window")',
                  '    except (EOFError, OSError, KeyboardInterrupt):',
                  '        print()',
                  '',
                  '',
                  'if _folder_belongs_to_the_bot():',
                  '    print("tinycmdr: this folder belongs to a running Mattermost bot "',
                  '          "(a tinycmdr.py sits next to this file and tinycmdr.lock is held).")',
                  '    print("Both builds keep their notes, tasks and sessions in their own "',
                  '          "folder, so copy this file into a folder of its own and start "',
                  '          "it there.")',
                  '    _hold_console()',
                  '    raise SystemExit(3)',
              ]),
              "refuse to share the bot folder")

# --- the llama.cpp-only switch stays off a hosted provider --------------------
# A hosted provider (Gemini's OpenAI-compatible layer included) would ignore or
# reject an unknown chat_template_kwargs field, so it only goes to a local server.
replace_lines('            if CONFIG["llm"].get("no_think"):', 3,
              NL.join([
                  '            if CONFIG["llm"].get("no_think") and _is_local_url(url):',
                  '                # llama.cpp / vLLM extension: ask the template to skip the think',
                  '                # block. Hosted providers do not know this field, so it never leaves the',
                  '                # LAN; reasoning limits on a hosted model are the provider\'s own setting.',
                  '                payload["chat_template_kwargs"] = {"enable_thinking": False}',
              ]),
              "no_think stays local")
# --- no bearer header unless a key is actually configured ---------------------
replace_lines('        self.headers = {"Content-Type": "application/json",', 2,
              NL.join([
                  '        # The service may want a bearer key; an environment that authenticates',
                  '        # at the platform level does not, and then no Authorization header is sent',
                  '        # at all rather than a placeholder value.',
                  '        self.headers = {"Content-Type": "application/json"}',
                  '        _key = str(CONFIG["llm"].get("api_key") or "").strip()',
                  '        if _key and _key.lower() != "none":',
                  '            self.headers["Authorization"] = "Bearer %s" % _key',
              ]),
              "no bearer header without a key")
# --- the /models probe obeys the same rule ------------------------------------
replace_lines('    def _ids(url, key):', 6,
              NL.join([
                  '    def _ids(url, key):',
                  '        headers = {"Content-Type": "application/json"}',
                  '        if str(key or "").strip() not in ("", "none"):',
                  '            headers["Authorization"] = "Bearer %s" % key',
                  '        try:',
                  '            r = requests.get(url.rstrip("/") + "/models", headers=headers,',
                  '                             timeout=8)',
              ]),
              "no junk auth header on /models")
# --- a provider that caps max_tokens lower than we asked ---------------------
# DeepSeek-class hosted models reject a too-large output cap with a 400. Retrying
# ONCE with a smaller cap beats dying on a limit the local models do not have.
replace_lines('            escalated = False', 2,
              NL.join([
                  '            escalated = False',
                  '            waited_after_429 = False',
                  '            clamped_tokens = False',
              ]),
              "track the token clamp")

replace_lines('                    if (status in (400, 413, 422)', 1,
              NL.join([
                  '                    if (status in (400, 413, 422) and cap',
                  '                            and not clamped_tokens',
                  '                            and re.search(r"(?i)max[_ ]?(output|completion)?[_ ]?tokens"',
                  '                                          r"|output token|max output", body)',
                  '                            and re.search(r"(?i)too large|greater than|exceed|at most|"',
                  '                                          r"maximum|max.*is", body)):',
                  '                        # The provider states its own output ceiling in the',
                  '                        # rejection. Halve ours and try once more rather than',
                  '                        # failing a run over a number in our defaults.',
                  '                        smaller = max(1024, min(int(cap) // 2, 8192))',
                  '                        log.warning("LLM %s rejected max_tokens=%s (%s) - "',
                  '                                    "retrying once with %s", url, cap,',
                  '                                    body[:120], smaller)',
                  '                        _record_attempt(usage, url, "retry",',
                  '                                        f"{status} token cap: {body[:80]}", secs)',
                  '                        cap = smaller',
                  '                        clamped_tokens = True',
                  '                        continue',
                  '                    if (status in (400, 413, 422)',
              ]),
              "clamp max_tokens once")
# --- post-generation edits that change behaviour, not just wording ------------
sub('class _ShimHTTPError(Exception):' + NL
    + '    def __init__(self, message, response=None):' + NL
    + '        super().__init__(message)' + NL
    + '        self.response = response' + NL,
    'class _ShimHTTPError(Exception):' + NL
    + '    def __init__(self, message, response=None):' + NL
    + '        super().__init__(message)' + NL
    + '        self.response = response' + NL + NL + NL
    + 'class _ShimConnectionError(Exception):' + NL
    + '    """Connection-level failure: DNS, refused, TLS, no route."""' + NL + NL + NL
    + 'class _ShimTimeout(Exception):' + NL
    + '    """The socket did not answer inside the timeout (connect or read)."""' + NL,
    "shim: connection + timeout exceptions")

sub('    HTTPError = _ShimHTTPError',
    '    HTTPError = _ShimHTTPError' + NL
    + '    ConnectionError = _ShimConnectionError' + NL
    + '    Timeout = _ShimTimeout',
    "shim: exception aliases")

sub('            raise InfraError("cannot reach %s: %s" % (url, getattr(e, "reason", e)))',
    '            raise _ShimConnectionError("cannot reach %s: %s"' + NL
    + '                                       % (url, getattr(e, "reason", e)))',
    "shim: URLError -> ConnectionError")

sub('            raise InfraError("no response from %s within %ss: %s" % (url, timeout, e))',
    '            raise _ShimTimeout("no response from %s within %ss: %s"' + NL
    + '                               % (url, timeout, e))',
    "shim: socket timeout -> Timeout")

sub('# Exit code meaning "start me again on purpose", as opposed to a crash.',
    'BUILD = "cli"          # this file is the enterprise build; tinycmdr.py in the repo is the bot' + NL
    + '# Exit code meaning "start me again on purpose", as opposed to a crash.',
    "build marker constant")

sub('        print("tinycmdr %s (python %s, %s)"' + NL
    + '              % (VERSION, platform.python_version(), BASE_DIR))',
    '        print("tinycmdr %s (%s build, python %s, %s)"' + NL
    + '              % (VERSION, BUILD, platform.python_version(), BASE_DIR))',
    "--version names the build")

sub('    print("tinycmdr %s - %s at %s" % (VERSION, green(CONFIG["llm"]["model"]),' + NL
    + '                                      CONFIG["llm"]["base_url"]))',
    '    print("tinycmdr %s (%s build) - %s at %s" % (VERSION, BUILD, green(CONFIG["llm"]["model"]),' + NL
    + '                                                CONFIG["llm"]["base_url"]))',
    "banner names the build")

# --- startup is inert: opening this build creates nothing ----------------------
# Operator requirement (2026-09-14): "nothing is created at startup or checked".
# Three writers ran before the first prompt existed, and a fourth on every run:
#   * the rotating log handler opened tinycmdr.log at import
#   * ToolRegistry() created tools/ at import
#   * Agent() created sessions/ at import
#   * ensure_atlas() wrote a DRAFT atlas.md at the start of every run
# Each is now deferred to the moment there is really something to write. The atlas is
# SHIPPED with the build instead: maintenance/atlas-cli-<platform>.md is written into the
# archive as atlas.md by build-cli-package.py, and nothing ever regenerates it.
sub('        backupCount=3, encoding="utf-8"))',
    '        backupCount=3, encoding="utf-8", delay=True))  # created on the first line, not at import',
    "log file: no file until a line is written")

sub("        self.tools_dir.mkdir(exist_ok=True)",
    NL.join([
        "        # No mkdir here, and none at import: tools/ appears the first time a tool is",
        "        # written into it. Globbing a folder that is not there yields nothing, which is",
        "        # the right answer for an empty install.",
    ]),
    "tools/: no folder at import")

sub('    path = TOOLS_DIR / f"{name}.py"' + NL + "    if path.exists():",
    '    path = TOOLS_DIR / f"{name}.py"' + NL
    + '    path.parent.mkdir(parents=True, exist_ok=True)' + NL
    + "    if path.exists():",
    "tools/: created when the first tool is written")

sub("        SESSIONS_DIR.mkdir(exist_ok=True)" + NL
    + '        for f in SESSIONS_DIR.glob("*.json"):  # reload persisted sessions',
    '        for f in SESSIONS_DIR.glob("*.json"):  # reload persisted sessions',
    "sessions/: no folder at import")

sub("    def _save(self, key):" + NL
    + "        try:" + NL
    + "            atomic_write_text(self._session_path(key),",
    "    def _save(self, key):" + NL
    + "        try:" + NL
    + "            # The folder appears with the first session worth keeping, not on open -"
    + NL
    + "            # and it is made in ONE place (see _ensure_sessions_dir), so the folder"
    + NL
    + "            # is created on a write and never at import." + NL
    + "            _ensure_sessions_dir()" + NL
    + "            atomic_write_text(self._session_path(key),",
    "sessions/: created on the first save")

replace_lines("            # Generated on the host, never shipped in a package. One stat per run once the",
              7,
              NL.join([
                  "            # The atlas is shipped beside this file and never regenerated: it rides the",
                  "            # first turn (agent.atlas_enabled, agent.atlas_file), and with no file the",
                  "            # block is empty. A run start creates nothing.",
              ]),
              "atlas: no draft written at run start")

sub('    """Write a DRAFT atlas when the file is missing.',
    '    """Write a DRAFT atlas when the file is missing (this build never calls it: its'
    + NL + '    atlas ships beside the agent, and nothing regenerates the file).',
    "atlas: the generator is not automatic here")

# The header used to say the harness produced these facts. Here the atlas is a shipped,
# human-editable document, and the model should read it as exactly that.
sub('    head = ["Machine atlas (facts about this machine, from the harness - you do not have to "'
    + NL + '            "discover or remember these):"]',
    '    head = ["Machine atlas (shipped beside the agent; edit it to suit this machine - you "'
    + NL + '            "do not have to discover or remember these):"]',
    "atlas: header names where it came from")

# --- nothing here reaches for an update channel --------------------------------
sub("- Don't gold-plate. When the OS update channel offers a stable update, take it — "
    "chasing the vendor's absolute-latest version number is not the goal. Working and "
    "done beats perfect and pending.",
    "- Don't gold-plate. Working and done beats perfect and pending: do not chase version "
    "numbers, and do not start an update to close a version gap. Report the gap instead.",
    "prompt: no update chasing")

sub('write ONE short plain-text line saying what you are about to check or do '
    '("Checking what holds the file lock:", "Reading the last 50 lines of the service log:")',
    'write ONE short plain-text line saying what you are about to do ("Reading the last '
    '50 lines of the service log:", "Listing the containers:")',
    "prompt: narration example wording")

# NOTE: the "no fake tool line for the stream status" fix-up that used to sit here
# now lives in cli_blocks.py, inside run_cli's progress(): the narration buffer has
# to be flushed before the tool line prints, and the generating early-return has to
# come before that flush. Splitting one function across two files is how the
# narration callback ended up with a one-argument signature that tinycmdr.py calls
# with three, so the model's own lines were silently dropped for the whole life of
# this build.

import ast
ast.parse(s)
open(P, "wb").write(s.replace(CR + LF, LF).replace(LF, NL).encode("utf-8"))
print("\n".join(applied))
print("\nrewrote %s" % P)
print("lines: %d -> %d" % (raw.count(NL), s.count(NL)))
