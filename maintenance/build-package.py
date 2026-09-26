"""Build the shippable tinycmdr package for a new host (Windows or Linux).

    python maintenance/build-package.py            # -> dist/tinycmdr-<v>-win.zip
                                                  #    dist/tinycmdr-<v>-linux.tar.gz
    python maintenance/build-package.py --list     # just show what would ship

Design rules, in order of importance:

1. NOTHING host-specific ships. No .env, no config.json, no logs, no session
   history, no notes/ledger, no web token, no Mattermost ids. The build FAILS if
   it finds any of them — a leak here is a leak onto every host you install on.
2. Everything needed to reach a working install ships: the app, the config and
   env templates, the skills, the tests, and the installer.
3. The manifest records what shipped, so an installed host can be compared
   against the package it came from.
"""
import argparse
import ast
import hashlib
import json
import pathlib
import plistlib
import re
import shutil
import sys
import tarfile
import tempfile
import time
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# Which files in a package must carry the execute bit on Unix. ONE list, because three
# places set modes (the fleet zip, the tarball, the macOS zip) and a predicate written out
# three times is how `tinycmdr` shipped non-executable in the published macOS package
# (measured 2026-09-25 on the v1.0.16 release asset: the entry is mode 0644, and a reader
# who installs it gets "Permission denied" from the door - for the user AND for sudo,
# because execve wants one execute bit for every user). The launcher has no extension, so
# a `.sh` test can never catch it; and it is the ONE file the /usr/local/bin shim execs.
EXEC_NAMES = ("tinycmdr", "tinycmdr.py")


def wants_exec_bit(path):
    """True for the launcher, the app and every shell script in a package.

    Callers hand this three shapes: a Windows Path (the staging tree), a POSIX
    archive name, and a str with either separator. Normalise first - with
    backslashes left in, "C:\\...\\tinycmdr" is ONE component to PurePosixPath and
    the launcher silently kept its 0644 (measured 2026-09-25: the tarball was right
    and the zips were not, from the same predicate).
    """
    p = pathlib.PurePosixPath(str(path).replace("\\", "/"))
    return p.name in EXEC_NAMES or p.suffix == ".sh"


# files/dirs that ship, in package-relative form
SHIP = [
    "tinycmdr.py",
    # The watchdog the Windows scheduled task runs. It belongs to THIS package; without
    # it the installer's task points
    # at a file that is not there, which is how boxes ended up with no respawn at all.
    "tinycmdr-supervise.py",
    "field-notes.md",
    # The persona: who the agent is plus three judgment hints a local model
    # loses without help. Shipped so an operator re-personas by editing one file
    # instead of patching the build (the fallback in the code is the same text).
    "soul.md",
    "requirements.txt",
    "config.example.json",
    ".env.example",
    "README.md",
    # The door a reader actually finds: it sits in the package root, while the installer
    # itself is one level down and the .ps1 beside it is refused by stock Windows (the
    # script execution policy is Restricted there, so that window closes before it can be
    # read). A root-level .cmd is what a person double-clicks.
    "INSTALL-WINDOWS.cmd",
    "install/install-tinycmdr.ps1",
    "install/install-tinycmdr.cmd",
    "install/install-tinycmdr.sh",
    "install/install-tinycmdr-macos.sh",
    "install/uninstall-tinycmdr.ps1",
    "install/uninstall-tinycmdr.sh",
    "install/uninstall-tinycmdr-macos.sh",
    "install/com.tinycmdr.agent.plist",
    "install/README-macos.md",
    # The management door: two ~20-line shims that run tinycmdr.py from the folder
    # they sit in, so `tinycmdr status` works from any prompt without a second copy of
    # anything (audit F12).
    "tinycmdr.cmd",
    "tinycmdr",
    "maintenance/restart-tinycmdr.ps1",
    "maintenance/restart-tinycmdr.sh",
    "skills",
    # The starter drop-in tools, the shapes doc and the toolsmith. tools/ is
    # otherwise per-host payload and stays banned from directory walks below
    # (FORBIDDEN_DIRS); these four files are shipped source, like soul.md.
    "tools/patch.py",
    "tools/process.py",
    "tools/toolsmith.py",
    "tools/README.md",
]

# A backup/file that must never be staged, whatever it is called: ".bak" anywhere
# (x.py.bak, x.py.bak-pre256-20260914), "pre" immediately followed by a version
# digit (x.pre-1.9.30, x.bak-pre256-...), and the usual editor leftovers.
BACKUP_RE = re.compile(r"\.bak|\.pre-|(?<![a-z])pre[-_]?\d|\.orig$|\.rej$|~$", re.I)

# things that must never be inside the zip, even by accident
FORBIDDEN_NAMES = {
    ".env", "config.json", "state.json", "jobs.json", "tasks.json", "tasks.md",
    "notes.md", "notes-archive.md", "tinycmdr.log", "tinycmdr.lock",
    "web-token.txt",
    # The machine atlas is generated ON the host it describes (atlas.md: os, paths, ports,
    # and where things live). Shipping this box's map to another box is worse than shipping
    # none: it is wrong in a way that reads as authoritative.
    "atlas.md",
}
FORBIDDEN_DIRS = {"sessions", "snapshots", "tools", "tmp", "__pycache__",
                  # Run output, not source: the graded-eval runner writes a jsonl per run
                  # plus a per-task artifact folder, and shipping those put ~400 KB of this
                  # box's own scoring runs inside the public archive (173 of its 191 entries
                  # were test output). The leak gate passed them; a stranger-facing package
                  # still has no business carrying them.
                  "eval-runs",
                  "dist", ".archive"}
# Makes a host install zero-argument: this fleet's Mattermost host, model
# endpoint and allowed user. Deliberately fleet-specific (that is its point) and
# carries no secrets - the bot token is written to .env at install time.
FLEET_FILE = "install/fleet-defaults.json"
# Fleet-wide keys (search): the same on every host, so they ship in the package and
# the installer writes them into .env. Per-bot keys (Mattermost token, DeepSeek) are
# NOT here - those come from -MattermostToken/-SecretsFile/an existing .env/prompt.
SECRETS_FILE = "install/fleet-secrets.env"
FLEET_WIDE_KEYS = ("TAVILY_API_KEY", "ANYSEARCH_API_KEY")
FLEET_MAY_CARRY = ("mattermost url", "allowed user id", "llm base url")

# What in maintenance/ is generic enough to ship: the restart helpers an install needs.
# Everything else in this folder is the manager box-specific - fleet pushes,
# migrations, probes, backups - and stays out.
#
# This is the SAME list as SHIP's maintenance/ entries, written twice, and the two drifting
# apart refuses the whole build. When you ship a new file from this folder, add it to BOTH,
# and cut the package in the same batch.
ALLOWED_MAINTENANCE = {"restart-tinycmdr.ps1", "restart-tinycmdr.sh",
                       "restart-tinycmdr-macos.sh"}

# Values that must not appear ANYWHERE (they are secrets, or this box's identity)

ENV_PREFIX = "env "
# Ships-as-code files must be neutral too: a host value here would be baked into
# every install, which is exactly how this box's endpoint ended up in the code.
APP_FILES = ("INSTALL-WINDOWS.cmd", "tinycmdr.py", "tinycmdr-supervise.py", "config.example.json",
             "soul.md",
             ".env.example", "README.md",
             "CHANGELOG.md", "install/install-tinycmdr.ps1",
             "install/uninstall-tinycmdr.ps1",
             "install/uninstall-tinycmdr.sh",
             "install/uninstall-tinycmdr-macos.sh",
             "install/install-tinycmdr.sh", "maintenance/restart-tinycmdr.ps1",
             "maintenance/restart-tinycmdr.sh", "launch-tinycmdr.sh",
             "install/install-tinycmdr-macos.sh", "install/com.tinycmdr.agent.plist",
             "maintenance/restart-tinycmdr-macos.sh",
             "tools/patch.py", "tools/process.py", "tools/toolsmith.py",
             "tools/README.md")

# --------------------------------------------------------------- public build ---
# Skills that belong to ONE box and must not ride along in a fleet build. They document a
# specific host's key path and a public server address, so installing them on other hosts would
# hand those hosts instructions that cannot work there (and leak that address into every copy).
# Host-local by design, added 2026-09-11.
HOST_SKILLS_SKIP = ("devops/mail-vps-admin", "web/example-blog", "devops/the file share",
                    "web/post-humanizer")

# `--public` produces the package that can be handed to strangers (a blog
# download): no fleet defaults, no keys, no private runbooks, and a scan that
# FAILS if a single personal string survives.

# The public package ships NO skills. The bot is the product; the skills are
# whatever the operator brings. The format is documented for drag-and-drop, and
# a reader has no use for this fleet's runbooks. Shipping a library would also
# mean shipping other people's copyrights (some bundled skills are MIT by other
# authors), which a standalone download has no reason to do.
PUBLIC_PRUNE = ("skills",)

# Written to skills/README.md in a public build, so whoever opens that folder
# knows the contract without reading the whole README.
PUBLIC_SKILLS_README = """# Skills go here

tinycmdr ships no skills of its own, and it does not need any. Anything you put
in this folder that follows the SKILL.md skill convention is picked up
automatically:

    skills/<category>/<name>/SKILL.md      or just   skills/<name>/SKILL.md

SKILL.md must begin with YAML frontmatter carrying a name and a description:

    ---
    name: mail-server-admin
    description: "Use when administering a self-hosted mail server."
    ---

    # Mail server admin

    The runbook: commands, paths, gotchas, in the order you would do them.

How tinycmdr uses it:

  - the model sees one line per skill ("name: description") in its prompt;
  - it reads a skill in full only when the task looks relevant (first 4000
    characters), or searches one for a topic;
  - every other .md in the skill folder is searchable too;
  - a new folder is live on the NEXT message. No restart, no config, no index.

Two things worth knowing:

  - COPY the folder in. A symlinked skill folder is invisible to the scanner
    (pathlib does not descend into symlinked directories).
  - The format is the one agent skill libraries use, so folders from those load
    as they are. What does not come with them is their tools: a runbook whose
    steps call a tool this build does not have (a desktop, browser or subagent
    tool, say) is an instruction for a program that is not here. Every skill read
    ends with the list of tools this install does have, and a capability you need
    is a file in tools/ - or one the agent writes with create_tool.

This file is documentation, not a skill. Delete it whenever you like.
"""

# Prose that a public build MAY rewrite (the rest of APP_FILES is code: a hit
# there is a bug to fix by hand, not something to silently rewrite).
PUBLIC_REDACT_DOCS = ("README.md", "CHANGELOG.md")

# Redaction for everything else. ORDER MATTERS: the specific phrases first, then
# the generic shapes, or a broad rule eats the context a narrow one needed.

try:
    from private_rules import PUBLIC_RULES, PUBLIC_FORBIDDEN, SECRET_LABELS
except ImportError as _e:
    raise SystemExit(
        "maintenance/private_rules.py is missing or unreadable (%s).\n" % _e +
        "It holds the fleet's private inventory (host names, ids, addresses, secret\n"
        "labels) that must never ship in a public package. Copy private_rules.example.py\n"
        "to private_rules.py and fill it in. Refusing to build without it.")



# Anything matching one of these in a --public package is a build failure, not a
# warning. The 26-char rule catches Mattermost ids; the key shapes catch leaked
# credentials in any doc that quotes one.



def version():
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', (ROOT / "tinycmdr.py").read_text(
        encoding="utf-8", errors="replace"), re.M)
    return m.group(1) if m else "unknown"


def host_values():
    """Values that exist only on THIS host. A package containing any of them is
    leaking this machine's identity, channels or secrets — refuse to build."""
    vals = {}
    cfg_path = ROOT / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        for section, key, label in (
                ("mattermost", "url", "mattermost url"),
                ("mattermost", "token", "mattermost token"),
                ("web", "token", "web ui token"),
                ("llm", "base_url", "llm base url")):
            v = ((cfg.get(section) or {}).get(key) or "")
            if isinstance(v, str) and len(v) >= 6:
                vals[label] = v
        for u in ((cfg.get("mattermost") or {}).get("allowed_users") or []):
            if isinstance(u, str) and len(u) >= 6:
                vals["allowed user id"] = u
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                if v.strip() and len(v.strip()) >= 8:
                    vals[f"env {k.strip()}"] = v.strip()
    return vals


def write_fleet_secrets(target):
    """install/fleet-secrets.env with the fleet-wide keys, taken from this box's .env
    so they are never typed again. Read by install-tinycmdr.ps1 automatically."""
    env_path = ROOT / ".env"
    vals = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            s = line.strip()
            if s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            if k.strip() in FLEET_WIDE_KEYS and v.strip():
                vals[k.strip()] = v.strip()
    if not vals:
        return {}
    lines = ["# fleet-wide keys (same on every host). install-tinycmdr.ps1 reads this",
             "# file automatically and writes these into the install's .env.",
             "# Per-bot keys are NOT here: Mattermost token and DeepSeek key are per host."]
    lines += [f"{k}={vals[k]}" for k in FLEET_WIDE_KEYS if k in vals]
    p = target / SECRETS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return vals


def write_fleet_defaults(target):
    cfg_path = ROOT / "config.json"
    cfg = {}
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    mm, llm = cfg.get("mattermost") or {}, cfg.get("llm") or {}
    users = [u for u in (mm.get("allowed_users") or []) if isinstance(u, str) and u]
    out = {
        "_readme": [
            "Fleet defaults for install-tinycmdr.ps1: with this file present a host",
            "installs with no arguments and nothing hand-edited. It carries no",
            "secrets - the Mattermost bot token is written to .env at install time.",
            "Command-line switches always win over these values.",
        ],
        "mattermost_url": mm.get("url") or "",
        "mattermost_port": mm.get("port") or 443,
        "allowed_user": users[0] if users else "",
        "model_base_url": llm.get("base_url") or "",
        "model": llm.get("model") or "main",
        # A fleet host keeps the layout and the boot-start task it has always run.
        # The PUBLIC package ships no fleet-defaults.json at all, so a reader gets
        # the profile default (%USERPROFILE%\tinycmdr) and the logon autostart
        # that needs no administrator rights.
        "install_dir": "C:\\tinycmdr",
        "as_service": True,
    }
    p = target / FLEET_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def lf_only(path):
    """A .sh with CRLF endings is not runnable: bash reads the shebang as
    "#!/usr/bin/env bash\r" and dies with "syntax error near '$\'in\r\''".
    The Windows working tree can carry CRLF even though the shipped kit must be
    LF, so normalise here instead of trusting the checkout."""
    if path.suffix != ".sh":
        return False
    raw = path.read_bytes()
    if b"\r\n" not in raw:
        return False
    path.write_bytes(raw.replace(b"\r\n", b"\n"))
    return True


def stage(target):
    """Copy the shipping set into target/, applying the exclusion rules."""
    target.mkdir(parents=True, exist_ok=True)
    for rel in SHIP:
        src = ROOT / rel
        if not src.exists():
            print(f"  ! missing {rel} — skipped")
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            for item in src.rglob("*"):
                if item.is_dir():
                    continue
                if any(part in FORBIDDEN_DIRS or part in FORBIDDEN_NAMES
                       for part in item.relative_to(src).parts):
                    continue
                if item.suffix in (".pyc", ".log", ".bak"):
                    continue
                # Rollback copies live beside the file they replace until a version is
                # proven; they must never ship (a stale test copy in the package is worse
                # than no test at all, because it passes). The suffix rules above missed
                # a backup named "x.py.bak-pre256-20260914-234923": its suffix is
                # ".bak-pre256-..." and the old name test looked for ".pre-" while the
                # real name had "-pre256-". One release shipped that file. So: match the
                # shape, not one spelling of it - ".bak" anywhere, "pre" followed by a
                # version digit, and the usual editor leftovers.
                if BACKUP_RE.search(item.name):
                    continue
                # Same rule for run output that is not a .log: jsonl run records and the
                # per-task artifact folders the eval runner leaves behind.
                if item.suffix == ".jsonl" or ".jsonl" in item.name:
                    continue
                if any(part.endswith("-artifacts") for part in item.relative_to(src).parts):
                    continue
                rel_from_src = item.relative_to(src).as_posix()
                if any(rel_from_src.startswith(host + "/") for host in HOST_SKILLS_SKIP):
                    continue
                out = dst / item.relative_to(src)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, out)
                lf_only(out)
        else:
            shutil.copy2(src, dst)
            lf_only(dst)

    return target


# Secrets and personal ids get replaced rather than dropped: the docs stay
# useful on the new host, without carrying this box's credentials or ids.
PLACEHOLDER = {
    "web ui token": "<web-ui-token>",
    "mattermost token": "<mattermost-bot-token>",
    "allowed user id": "<your-mattermost-user-id>",
}


def sanitize(target, host_vals):
    """Replace secret values in the shipped files (never in APP_FILES, which must
    be clean by construction). Returns the list of redacted files."""
    changed = []
    for f in target.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(target).as_posix()
        if rel in APP_FILES:
            continue
        if rel == SECRETS_FILE:
            # This file exists to carry the fleet-wide keys, so it must not go
            # through env-value redaction: doing so replaced every key with the
            # literal "<redacted: TAVILY_API_KEY>" and both installers wrote that
            # into the new host's .env as if it were a key (seen 2026-09-11 on
            # the LAN model box and the Linux test box: search dead, HTTP 401 from the provider).
            # audit() and the zip verifier already exempt it the same way.
            continue
        # Bytes in, bytes out. A text-mode read applies universal newlines, so a CRLF file
        # comes back LF and the generator's stray `\r\r\n` insertions come back as an extra
        # blank line each (measured 2026-09-22: the shipped public build carried 84
        # extra blank lines and no longer matched the file the suites grade). A scrub must
        # change the strings it names and nothing else, newlines included.
        text = f.read_bytes().decode("utf-8", "surrogateescape")
        new = text
        for label, val in host_vals.items():
            if not val or val not in new:
                continue
            if rel == FLEET_FILE and label in FLEET_MAY_CARRY:
                # fleet-defaults.json exists to carry the fleet's own values; a
                # redacted allowed_user would leave the bot ignoring every DM
                continue
            if label in SECRET_LABELS:
                new = new.replace(val, PLACEHOLDER[label])
            elif label.startswith(ENV_PREFIX):
                new = new.replace(val, f"<redacted: {label[4:]}>")
        if new != text:
            f.write_bytes(new.encode("utf-8", "surrogateescape"))
            changed.append(rel)
    return changed


def audit(target, host_vals, allow_secrets=False):
    """Fail on secrets and on any host value inside shippable code; merely warn
    about this fleet's hostnames appearing in skills, which are documentation
    about the fleet and are meant to be useful as-is."""
    problems, warnings, scanned = [], [], 0
    for f in target.rglob("*"):
        if not f.is_file():
            continue
        scanned += 1
        rel = f.relative_to(target).as_posix()
        parts = f.relative_to(target).parts
        if f.name in FORBIDDEN_NAMES or any(p in FORBIDDEN_DIRS for p in parts):
            # tools/ is per-host payload - except the starter files SHIP
            # names explicitly, which are shipped source like soul.md.
            if rel not in {r for r in SHIP if r.startswith("tools/")}:
                problems.append(f"forbidden file: {rel}")
        if parts[0] == "maintenance" and f.name not in ALLOWED_MAINTENANCE:
            problems.append(f"host-specific maintenance script: {rel}")
        # Windows PowerShell 5.1 decodes a BOM-less file as ANSI, so a UTF-8 em
        # dash inside a string turns into a smart quote and breaks the parse.
        # Shipped scripts must be pure ASCII.
        # A BOM breaks JSON parsing and HTTP headers (an installer-written
        # web token with a BOM is a 401 with no visible cause). Notepad and
        # PowerShell 5.1 both add one, so gate it.
        if f.suffix.lower() in (".json", ".example") or f.name == ".env.example":
            if f.read_bytes()[:3] == b"\xef\xbb\xbf":
                problems.append(f"UTF-8 BOM in a shipped data file: {rel}")
        # The .sh half of this gate covers the scripts THIS package runs on a new
        # host (installer, launcher, restart helper) - the ones that must parse in
        # any locale. Skill scripts are the agent's own toolkit and ship as-is,
        # like the .py and .md beside them.
        runs_here = (rel.startswith("install/") or rel.startswith("maintenance/")
                     or rel == "launch-tinycmdr.sh")
        if f.suffix.lower() in (".ps1", ".bat", ".vbs", ".cmd") or (
                f.suffix.lower() == ".sh" and runs_here):
            raw = f.read_bytes()
            nonascii = [b for b_ in [raw] for b in b_ if b > 127]
            if nonascii:
                problems.append(
                    f"non-ASCII bytes in a script (PS 5.1 will misparse it): {rel}")
        text = f.read_text(encoding="utf-8", errors="replace")
        for label, val in host_vals.items():
            if not val or val not in text:
                continue
            if label.startswith(ENV_PREFIX):
                if rel == SECRETS_FILE and allow_secrets:
                    continue          # this file exists to carry the fleet keys
                problems.append(f"secret {label} in {rel}")
                continue
            if rel == FLEET_FILE and label in FLEET_MAY_CARRY:
                continue          # fleet-defaults.json exists to carry these
            hard = (label in SECRET_LABELS or rel in APP_FILES)
            (problems if hard else warnings).append(f"{label} in {rel}")
    return problems, warnings, scanned


def prune(stage_dir, prefixes):
    """Drop whole subtrees (skills this fleet's docs live in). Returns the count."""
    dropped = 0
    for pre in prefixes:
        target = stage_dir / pre
        if target.is_dir():
            dropped += len([f for f in target.rglob("*") if f.is_file()])
            shutil.rmtree(target)
    return dropped


def redact_public(stage_dir):
    """Scrub personal strings out of every non-code file. Returns {file: n_hits}."""
    changed = {}
    for f in sorted(stage_dir.rglob("*")):
        if not f.is_file() or f.suffix.lower() in (".png", ".gz", ".zip"):
            continue
        rel = f.relative_to(stage_dir).as_posix()
        if rel in APP_FILES and rel not in PUBLIC_REDACT_DOCS:
            # the app itself must be byte-identical to the live file, so a hit
            # here is a bug in tinycmdr.py, not something to silently rewrite
            continue
        text = f.read_bytes().decode("utf-8", "surrogateescape")   # bytes in, bytes out
        new, n = text, 0
        for pat, repl in PUBLIC_RULES:
            new, k = re.subn(pat, repl, new)
            n += k
        if n:
            f.write_bytes(new.encode("utf-8", "surrogateescape"))
            changed[rel] = n
    return changed


def audit_public(stage_dir):
    """Any personal string left anywhere (code included) fails the build."""
    problems = []
    for f in sorted(stage_dir.rglob("*")):
        if not f.is_file() or f.suffix.lower() in (".png", ".gz", ".zip"):
            continue
        rel = f.relative_to(stage_dir).as_posix()
        text = f.read_text(encoding="utf-8", errors="replace")
        for pat in PUBLIC_FORBIDDEN:
            for m in set(re.findall(pat, text)):
                problems.append(f"private string {m!r} ({pat}) in {rel}")
    return problems


# The package states Python 3.10 as its floor in the README and requirements, so a shipped
# .py that only PARSES on a newer interpreter is a broken promise: 2.3.0 went out with two
# suites using a nested same-quote f-string (3.12-only, PEP 701), found by unpacking the
# published archive on a 3.10 host. Parse every shipped .py against the floor, not the
# interpreter this build happens to run on.
PY_FLOOR = (3, 10)


def tier_drift():
    """Problems where config.example.json disagrees with DEFAULT_CONFIG's safety tiers.

    Measured 2026-09-25: every installer writes a new host's config.json FROM
    config.example.json, and the example was missing the `robocopy /MOVE` confirm pattern
    that DEFAULT_CONFIG and that release's own changelog both carry (8 vs 9) - so a fresh
    install shipped without the gate the changelog announced. Nothing caught it: the suites
    run against tests/fixture-config.json, which holds ZERO confirm and content patterns, so
    "all suites green" says nothing about the file the installers copy. The safety tiers must
    match the code exactly; the other keys are the reader-facing template and may differ.
    """
    import socket as _socket
    problems = []
    src = (ROOT / "tinycmdr.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    node = next((n for n in tree.body if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", None) == "DEFAULT_CONFIG" for t in n.targets)), None)
    if node is None:
        return ["tinycmdr.py: DEFAULT_CONFIG not found, cannot check config.example.json"]
    ns = {"socket": _socket, "os": __import__("os"), "platform": __import__("platform")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<default-config>", "exec"), ns)
    code = ns["DEFAULT_CONFIG"]["agent"]
    example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))["agent"]
    for key in ("blocked_patterns", "confirm_patterns", "confirm_content_patterns"):
        want = [str(x) for x in (code.get(key) or [])]
        have = [str(x) for x in (example.get(key) or [])]
        for pat in want:
            if pat not in have:
                problems.append(
                    f"config.example.json: {key} is missing {pat!r} (DEFAULT_CONFIG has it, "
                    f"and every installer copies this file)")
        for pat in have:
            if pat not in want:
                problems.append(
                    f"config.example.json: {key} has {pat!r}, which DEFAULT_CONFIG does NOT "
                    f"(the two lists must agree)")
    return problems


def syntax_floor(folder):
    """Problems for any shipped .py that does not parse on the stated minimum."""
    problems, checked = [], 0
    for f in sorted(folder.rglob("*.py")):
        rel = f.relative_to(folder).as_posix()
        try:
            ast.parse(f.read_text(encoding="utf-8", errors="replace"),
                      filename=rel, feature_version=PY_FLOOR)
            checked += 1
        except SyntaxError as e:
            problems.append(f"{rel}:{e.lineno} does not parse on Python "
                            f"{PY_FLOOR[0]}.{PY_FLOOR[1]} ({e.msg})")
    print(f"syntax floor: {checked} .py file(s) parse on Python "
          f"{PY_FLOOR[0]}.{PY_FLOOR[1]}")
    return problems


def payload_floor():
    """What ONE turn of this build rents before any history: the system prompt
    plus the visible tool schemas, measured with the A/A staging (the shipped
    fixture config, no skills, no custom tools) so the number compares between
    builds and boxes. Item C of docs/plan-efficiency-2026-09-24.md: payload
    growth is visible per cut."""
    import hashlib
    import importlib.util
    with tempfile.TemporaryDirectory(prefix="tinycmdr-probe-") as tmp:
        tmp = pathlib.Path(tmp)
        shutil.copy2(ROOT / "tinycmdr.py", tmp / "tinycmdr.py")
        shutil.copy2(ROOT / "tests" / "fixture-config.json", tmp / "config.json")
        spec = importlib.util.spec_from_file_location("tinycmdr_probe",
                                                  tmp / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["tinycmdr_probe"] = mod
        spec.loader.exec_module(mod)
        prompt = mod.build_system_prompt()
        schemas = json.dumps(mod.select_tool_schemas(None))
        tok = mod.est_tokens(prompt + schemas)
        # the build's RotatingFileHandler holds tinycmdr.log open in the probe
        # dir; release it or Windows refuses to delete the directory after
        mod._log_listener.stop()
        for _h in mod._log_listener.handlers:
            _h.close()
    digest = hashlib.sha256((ROOT / "tinycmdr.py").read_bytes()).hexdigest()[:16]
    return len(prompt), len(schemas), tok, digest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="show what would ship")
    ap.add_argument("--public", action="store_true",
                    help="blog/release build: no fleet defaults, no keys, private "
                         "runbooks dropped, everything else redacted, and a scan that "
                         "REFUSES the build on any surviving personal string")
    ap.add_argument("--no-fleet", action="store_true",
                    help="omit fleet-defaults.json (a generic, argument-driven package)")
    ap.add_argument("--macos", action="store_true",
                    help="also build dist/tinycmdr-<v>-macos.zip (macOS kit: the same\n                         staged tree plus the launchd installer)")
    ap.add_argument("--no-secrets", action="store_true",
                    help="omit install/fleet-secrets.env (no keys in the package; hosts "
                         "then need -SecretsFile)")
    args = ap.parse_args()

    public = args.public
    if public:
        args.no_fleet = True
        args.no_secrets = True

    ver = version()
    host_vals = host_values()
    print(f"tinycmdr package builder — version {ver}"
          + ("  [PUBLIC RELEASE]" if public else ""))
    print(f"host-specific values that must NOT appear: {len(host_vals)}")
    for label in host_vals:
        print(f"  will check: {label}")

    try:
        p_ch, s_ch, tok, digest = payload_floor()
        print(f"payload floor: prompt {p_ch:,} ch + schemas {s_ch:,} ch "
              f"= {p_ch + s_ch:,} (~{tok:,} est-tok) · build {digest}")
    except Exception as exc:
        print(f"payload floor: PROBE FAILED ({exc})")

    with tempfile.TemporaryDirectory(prefix="tinycmdr-pkg-") as tmp:
        stage_dir = pathlib.Path(tmp) / f"tinycmdr-{ver}"
        stage(stage_dir)
        if public:
            n = prune(stage_dir, PUBLIC_PRUNE)
            print(f"\npublic: dropped {n} file(s) under "
                  f"{', '.join(PUBLIC_PRUNE)} (skills are the operator's to bring)")
            sk = stage_dir / "skills"
            sk.mkdir(parents=True, exist_ok=True)
            (sk / "README.md").write_text(PUBLIC_SKILLS_README, encoding="utf-8",
                                          newline="\n")
            print("public: wrote skills/README.md (the drag-and-drop contract)")
        if not args.no_fleet:
            fleet = write_fleet_defaults(stage_dir)
            print(f"fleet defaults: {fleet['mattermost_url']} | {fleet['model_base_url']} "
                  f"| allowed_user={'set' if fleet['allowed_user'] else 'unset'}")
        secrets_written = {}
        if not args.no_secrets:
            secrets_written = write_fleet_secrets(stage_dir)
            if secrets_written:
                print("fleet secrets : " + ", ".join(sorted(secrets_written))
                      + "  <-- the package now carries these keys; keep the zip to "
                        "your own hosts")

        if args.list:
            print("\nwould ship:")
            for f in sorted(stage_dir.rglob("*")):
                if f.is_file():
                    print(f"  {f.relative_to(stage_dir)}  ({f.stat().st_size} B)")
            return 0

        redacted = sanitize(stage_dir, host_vals)
        if redacted:
            print(f"\nredacted {len(redacted)} file(s) that carried secrets/ids:")
            for r_ in redacted:
                print(f"  ~ {r_}")
        if public:
            scrubbed = redact_public(stage_dir)
            if scrubbed:
                print(f"\npublic: scrubbed personal strings out of {len(scrubbed)} "
                      f"file(s) ({sum(scrubbed.values())} replacements):")
                for r_ in sorted(scrubbed):
                    print(f"  ~ {r_}  ({scrubbed[r_]})")
        problems, warnings, scanned = audit(stage_dir, host_vals,
                                            allow_secrets=not args.no_secrets)
        if public:
            problems += audit_public(stage_dir)
        problems += syntax_floor(stage_dir)
        problems += tier_drift()
        if problems:
            print("\nBUILD REFUSED — the package would carry secrets or host data:")
            for p in problems:
                print(f"  ! {p}")
            return 2
        print(f"\naudit: {scanned} files, no forbidden names, no secrets, "
              f"no host values in shipped code")
        if public:
            print("public scan: no personal strings anywhere (domains, hosts, "
                  "ids, names, paths, keys)")
        if warnings and not public:
            files = sorted({w.split(" in ", 1)[1] for w in warnings})
            print(f"note: {len(files)} skill file(s) mention this fleet's "
                  f"hostnames (documentation, shipped as-is):")
            for f in files[:8]:
                print(f"  - {f}")
            if len(files) > 8:
                print(f"  ... and {len(files) - 8} more")

        # The hashes are PRINTED, not shipped. They are how a build gets verified here; a
        # manifest file in the download is one more document a reader has to decide about,
        # and nothing in the code reads it.
        files = sorted(f for f in stage_dir.rglob("*") if f.is_file())
        print("  sha256 of the files that matter:")
        for rel in ("tinycmdr.py", "config.example.json", ".env.example",
                    "INSTALL-WINDOWS.cmd",
                    "install/install-tinycmdr.ps1", "install/install-tinycmdr.cmd",
                    "install/install-tinycmdr.sh",
                    "install/install-tinycmdr-macos.sh",
                    "install/com.tinycmdr.agent.plist"):
            p = stage_dir / rel
            if p.exists():
                print("    %s  %s" % (hashlib.sha256(p.read_bytes()).hexdigest()[:16], rel))
        print("  %d files in the package" % len(files))

        DIST.mkdir(exist_ok=True)
        suffix = "" if public else "-fleet"
        zip_path = DIST / f"tinycmdr-{ver}-win{suffix}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(stage_dir.rglob("*")):
                if not f.is_file():
                    continue
                arc = f.relative_to(stage_dir.parent).as_posix()
                mode = 0o755 if wants_exec_bit(f) else 0o644
                zi = zipfile.ZipInfo(arc, date_time=time.localtime()[:6])
                zi.external_attr = mode << 16
                zi.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(zi, f.read_bytes())

        # verify the zip itself, not just the staging dir
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            bad = [n for n in names
                   if pathlib.PurePosixPath(n).name in FORBIDDEN_NAMES]
            leak, soft = [], []
            for n in names:
                if n.endswith("/"):
                    continue
                rel = n.split("/", 1)[1] if "/" in n else n
                blob = z.read(n).decode("utf-8", errors="replace")
                for label, val in host_vals.items():
                    if not val or val not in blob:
                        continue
                    if label.startswith(ENV_PREFIX):
                        if rel == SECRETS_FILE and not args.no_secrets:
                            continue
                        leak.append(f"secret {label} in {rel}")
                        continue
                    if rel == FLEET_FILE and label in FLEET_MAY_CARRY:
                        continue
                    hard = (label in SECRET_LABELS or rel in APP_FILES)
                    (leak if hard else soft).append(f"{label} in {rel}")
            inner = hashlib.sha256(z.read(f"tinycmdr-{ver}/tinycmdr.py")).hexdigest()
            # The installer's scheduled task runs the supervisor. A package without it
            # installs a host whose task points at a missing file, so the package is
            # refused here rather than shipping a dead watchdog.
            guarded = [n for n in names if n.endswith("/tinycmdr-supervise.py")]
            zlaunch = next(((z.getinfo(n).external_attr >> 16) & 0o777 for n in names
                            if n.endswith("/tinycmdr")), 0)

        live_inner = hashlib.sha256((ROOT / "tinycmdr.py").read_bytes()).hexdigest()
        ok = (not bad and not leak and inner == live_inner and bool(guarded)
              and bool(zlaunch & 0o111))
        print(f"\nzip: {zip_path}")
        print(f"  {len(names)} entries, {zip_path.stat().st_size / 1024:.0f} KB")
        print(f"  tinycmdr.py in zip matches the live file: {inner == live_inner}")
        print(f"  the watchdog rides in the package: {bool(guarded)}")
        print(f"  the launcher itself (what the shim execs): {oct(zlaunch)} "
              f"(executable: {bool(zlaunch & 0o111)})")
        print(f"  no forbidden filenames: {not bad}")
        print(f"  no secrets/ids anywhere: {not leak}")
        print(f"  fleet hostnames in skills (informational): {len(soft)}")
        if leak:
            for l in leak:
                print(f"    ! {l}")

        # The same staged tree as a tarball: the .sh half of the kit needs its
        # exec bits, which a zip does not carry dependably - and on Windows the
        # filesystem has no exec bit at all, so set the modes explicitly rather
        # than trusting st_mode.
        tar_path = DIST / f"tinycmdr-{ver}-linux{suffix}.tar.gz"

        def _modes(ti):
            if ti.isdir():
                ti.mode = 0o755
            elif wants_exec_bit(ti.name):
                ti.mode = 0o755
            else:
                ti.mode = 0o644
            return ti

        with tarfile.open(tar_path, "w:gz") as t:
            t.add(stage_dir, arcname=stage_dir.name, filter=_modes)
        with tarfile.open(tar_path) as t:
            tfiles = [m for m in t.getmembers() if m.isfile()]
            tbad = [m.name for m in tfiles
                    if pathlib.PurePosixPath(m.name).name in FORBIDDEN_NAMES
                    or BACKUP_RE.search(pathlib.PurePosixPath(m.name).name)]
            tinner = hashlib.sha256(
                t.extractfile(f"{stage_dir.name}/tinycmdr.py").read()).hexdigest()
            tmode = next((m.mode for m in tfiles
                          if m.name.endswith("install/install-tinycmdr.sh")), 0)
            tlaunch = next((m.mode for m in tfiles
                            if m.name.endswith("/tinycmdr")), 0)
        tar_ok = (not tbad and tinner == live_inner and tmode & 0o111
                  and tlaunch & 0o111)
        print(f"\ntarball: {tar_path}")
        print(f"  {len(tfiles)} files, {tar_path.stat().st_size / 1024:.0f} KB")
        print(f"  tinycmdr.py matches the live file: {tinner == live_inner}")
        print(f"  install-tinycmdr.sh mode: {oct(tmode)} (executable: {bool(tmode & 0o111)})")
        print(f"  the launcher itself (what the shim execs): {oct(tlaunch)} "
              f"(executable: {bool(tlaunch & 0o111)})")
        print(f"  no forbidden filenames: {not tbad}")
        # macOS: the same staged tree as a zip (Finder extracts it), with the exec bits
        # written explicitly because the build host has no exec bit to copy. The launchd
        # Every file the INSTALLER requires must be in the package. Dropping `tests` from the
        # download made every install die with "package is missing tests (run the installer from
        # the extracted zip)": the copy lists tolerate a missing file, this list does not, and
        # -VerifyOnly never reaches it. Parse the list and fail the build instead.
        inst = stage_dir / "install" / "install-tinycmdr.ps1"
        if inst.exists():
            m = re.search(r"\$required\s*=\s*@\(([^)]*)\)",
                          inst.read_text(encoding="utf-8", errors="replace"))
            if not m:
                raise SystemExit("cannot read the installer's $required list: it guards every "
                                 "install, so a build without it proves nothing")
            need = re.findall(r'"([^"]+)"', m.group(1))
            miss = [n for n in need if not (stage_dir / n).exists()]
            if miss:
                raise SystemExit("the installer requires file(s) the package does not carry: %s"
                                 % ", ".join(miss))
            print("  installer's required files all present: %d" % len(need))

        # plist is parsed here rather than trusted: launchd refusing a plist is the kind of
        # failure nobody sees until the install is already "done".
        mac_ok = True
        if args.macos:
            mac_path = DIST / f"tinycmdr-{ver}-macos{suffix}.zip"
            with zipfile.ZipFile(mac_path, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(stage_dir.rglob("*")):
                    if not f.is_file():
                        continue
                    arc = f.relative_to(stage_dir.parent).as_posix()
                    mode = 0o755 if wants_exec_bit(f) else 0o644
                    zi = zipfile.ZipInfo(arc, date_time=time.localtime()[:6])
                    zi.external_attr = mode << 16
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    z.writestr(zi, f.read_bytes())
            with zipfile.ZipFile(mac_path) as z:
                mac_names = z.namelist()
                macbad = [n for n in mac_names
                          if pathlib.PurePosixPath(n).name in FORBIDDEN_NAMES]
                macinner = hashlib.sha256(z.read(f"tinycmdr-{ver}/tinycmdr.py")).hexdigest()
                rendered = (z.read(f"tinycmdr-{ver}/install/com.tinycmdr.agent.plist")
                            .decode("utf-8")
                            .replace("__LABEL__", "com.example.test")
                            .replace("__PYTHON__", "/tmp/venv/bin/python")
                            .replace("__APP__", "/tmp/tinycmdr"))
                try:
                    pl = plistlib.loads(rendered.encode("utf-8"))
                    plist_ok = (pl.get("Label") == "com.example.test"
                                and pl.get("RunAtLoad") is True
                                and isinstance(pl.get("KeepAlive"), dict)
                                and pl["KeepAlive"].get("SuccessfulExit") is False
                                and len(pl.get("ProgramArguments", [])) == 2)
                except Exception as e:                      # noqa: BLE001
                    plist_ok = False
                    print(f"    ! plist did not parse: {e}")
                inst = f"tinycmdr-{ver}/install/install-tinycmdr-macos.sh"
                macmode = (z.getinfo(inst).external_attr >> 16) & 0o777
                maclaunch = (z.getinfo(f"tinycmdr-{ver}/tinycmdr")
                             .external_attr >> 16) & 0o777
                mleak, msoft = [], []
                for n in mac_names:
                    if n.endswith("/"):
                        continue
                    rel = n.split("/", 1)[1] if "/" in n else n
                    blob = z.read(n).decode("utf-8", errors="replace")
                    for label, val in host_vals.items():
                        if not val or val not in blob:
                            continue
                        if label.startswith(ENV_PREFIX) and rel == SECRETS_FILE:
                            continue
                        if rel == FLEET_FILE and label in FLEET_MAY_CARRY:
                            continue
                        # Same rule as the Windows zip: a host value in shipped CODE (or
                        # a secret anywhere) fails the build; a host value in skills is
                        # documentation about this fleet and ships as-is.
                        hard = (label in SECRET_LABELS or rel in APP_FILES)
                        (mleak if hard else msoft).append(f"{label} in {rel}")
            mac_ok = (not macbad and not mleak and macinner == live_inner
                      and plist_ok and bool(macmode & 0o111)
                      and bool(maclaunch & 0o111))
            print(f"\nmacos zip: {mac_path}")
            print(f"  {len(mac_names)} entries, {mac_path.stat().st_size / 1024:.0f} KB")
            print(f"  tinycmdr.py matches the live file: {macinner == live_inner}")
            print(f"  install-tinycmdr-macos.sh mode: {oct(macmode)} "
                  f"(executable: {bool(macmode & 0o111)})")
            print(f"  the launcher itself (what the shim execs): {oct(maclaunch)} "
                  f"(executable: {bool(maclaunch & 0o111)})")
            print(f"  launchd plist parses and is complete: {plist_ok}")
            print(f"  no forbidden filenames: {not macbad}")
            print(f"  no secrets/ids anywhere: {not mleak}")
            print(f"  fleet hostnames in skills (informational): {len(msoft)}")
            for l in mleak:
                print(f"    ! {l}")

        return 0 if (ok and tar_ok and mac_ok) else 2


if __name__ == "__main__":
    sys.exit(main())
