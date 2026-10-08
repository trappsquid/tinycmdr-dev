"""This record speaks in the first person - and the gate refuses the stand-ins.

The rule (AGENTS.md, invariants): commit messages, `STATUS.json` details and comments state
what changed and why, in plain facts. "Operator" means whoever runs a box, never me; my own
work and decisions say "I" (David). No quoted conversations, no process narration, no
first-person plural, no third-person role nouns standing in for me.

Measured 2026-10-06: the class kept returning in prose - a commit body described my own
design as "the operator's", the ledger attributed decisions to "the operator", docs called
me "the maintainer". Judgment is a rule; the mechanical half is graded here so the gate
refuses it. When a pattern below fires, rewrite the sentence in the first person rather
than editing this file.

    python tests/test_wording.py
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = "tests/test_wording.py"

# Which files are graded is decided by reading them (see tracked()), not by this kind of list:
# an extension whitelist let 22 tracked files go unread, two of them shipped to users.

# Banned in every tracked text file: a role noun that can only mean the maintainer.
ANYWHERE = (
    (re.compile(r"\bmaintainers?\b", re.I), "the maintainer (say I)"),
    (re.compile(r"\bmaintainer's\b", re.I), "the maintainer's (say my)"),
    (re.compile(r"\boperator[- ]directed\b", re.I), "operator-directed (say I decided)"),
)
# Banned in prose files: the plural team voice. Code files are exempt because `us`/`our`
# appear as substrings of identifiers and data.
PROSE = (
    (re.compile(r"\b(we|our|us)\b", re.I), "first-person plural (this project has one owner: I)"),
)
PROSE_EXT = (".md", ".json")

# A host's own working-record words - its sessions, its reports, where the write-ups are kept -
# are NOT listed here: they are that host's business and this file is public. They come from the
# host's private inventory instead (maintenance/private_rules.py, or the same source text in
# TINYCMDR_LEAK_PATTERNS); a clone with neither grades the generic half below only.
def private_words():
    """(pattern, why) pairs from this host's private inventory; () when there is none."""
    rules = ROOT / "maintenance" / "private_rules.py"
    try:
        text = rules.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = os.environ.get("TINYCMDR_LEAK_PATTERNS") or ""
    ns: dict = {}
    if text.strip():
        try:
            exec(compile(text, "private_rules", "exec"), ns)       # noqa: S102 - the host's file
        except Exception:                                          # noqa: BLE001
            return []
    out = []
    for pat in ns.get("PRIVATE_WORDS", ()):
        try:
            out.append((re.compile(pat, re.I), "a word from this host's private records"))
        except re.error:
            continue
    return out


# ...and in the narrative files the finding ids go too: a reader of the changelog or the record
# cannot look one up. Scoped to .md and STATUS.json on purpose - an id beside a reason in source
# is an opaque label, and this half must not grade it away.
FINDING_ID = (
    (re.compile(r"A-\d{4}-\d\d-\d\d-\d+"), "a private finding id"),
    (re.compile(r"\bA-\d{2,3}\b"), "a private finding id"),
)
NARRATIVE_EXT = (".md",)
NARRATIVE_FILES = ("STATUS.json",)


# What the scope rule set aside, and why. Printed by main() so the graded set has a
# denominator: the extension whitelist's sin was an unread file nobody counted, and a bare
# `continue` on an unreadable or UTF-16 file repeats it in another shape (run 21, A-56).
NOT_GRADED = {"binary": [], "unreadable": []}


def read_tracked(path):
    """("text"|"binary"|"unreadable", the text or None) for one candidate file.

    The rule is "every tracked file that decodes as UTF-8", so the DENOMINATOR is part of the
    claim. Splitting the three outcomes out is what lets the exclusion be counted: a file
    dropped by a bare `continue` (a UTF-16 file, a broken symlink, an odd permission bit)
    leaves the graded set invisible, which is the same blind spot the extension whitelist had,
    in another shape (run 21, A-2026-10-07-56). The NUL sniff is the first 4 KB: a binary
    asset is recognised by its header, and a text file long enough to carry a NUL later is not
    text anywhere a reader cares about.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return "unreadable", None
    if b"\0" in raw[:4096]:
        return "binary", None
    try:
        return "text", raw.decode("utf-8")
    except UnicodeDecodeError:
        return "binary", None


def tracked():
    """Every tracked file that decodes as UTF-8 text - not an extension whitelist.

    The whitelist left 22 tracked files ungraded, two of which ship to a user
    (install/com.tinycmdr.agent.plist and the two .command launchers) - exactly where a
    share path or a session name would be written, and the gate would not have seen it.
    Reading the bytes and keeping what decodes also covers the extension-less files
    (LICENSE, the `tinycmdr` launcher) without growing a list that goes stale.

    What it leaves out is recorded in NOT_GRADED and printed by main(): a rule that grades
    "every tracked file that decodes as UTF-8" has to be able to show its denominator.
    """
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True,
                         text=True).stdout
    keep = []
    for rel in out.splitlines():
        if rel == SELF:
            continue
        kind, _text = read_tracked(ROOT / rel)
        if kind == "text":
            keep.append(rel)
        else:
            NOT_GRADED[kind].append(rel)
    return keep


def main():
    # The graded set is "every tracked file", listed by `git ls-files`: without a .git there
    # is no tracked set at all, so this suite can only grade nothing. exit 77 declares that,
    # rather than dying mid-suite or reporting a clean sweep of zero files (run 23,
    # A-2026-10-07-66).
    probe = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        print("skip: %s is not a git work tree - there is no tracked set to grade" % ROOT)
        return 77
    bad = []
    NOT_GRADED["binary"].clear()
    NOT_GRADED["unreadable"].clear()
    graded_files = tracked()
    graded = set(graded_files)

    # ---- the scope's own arithmetic, exercised on files we control ------------------------
    # The three outcomes are the whole rule; a classifier that called an unreadable file
    # "binary" (or a UTF-16 file "text") would grade the wrong set and still print a clean
    # count. Pre-fix this was one `except OSError: continue` (run 21, A-2026-10-07-56).
    import tempfile
    work = Path(tempfile.mkdtemp(prefix="tc-wording-scope-"))
    try:
        (work / "text.md").write_text("a rule in the first person\n", encoding="utf-8")
        (work / "asset.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
        (work / "utf16.txt").write_bytes("hello".encode("utf-16"))
        hidden = work / "unreadable.txt"
        hidden.write_text("x", encoding="utf-8")
        os.chmod(hidden, 0)
        got = {"text.md": read_tracked(work / "text.md")[0],
               "asset.png": read_tracked(work / "asset.png")[0],
               "utf16.txt": read_tracked(work / "utf16.txt")[0],
               "unreadable.txt": read_tracked(hidden)[0]}
        check = {"text.md": "text", "asset.png": "binary", "utf16.txt": "binary",
                 "unreadable.txt": ("text" if (os.name == "nt" or os.geteuid() == 0)
                                    else "unreadable")}
        wrong = {k: (got[k], check[k]) for k in got if got[k] != check[k]}
        bad += ["read_tracked classified %s as %s, expected %s" % (k, v[0], v[1])
                for k, v in wrong.items()]
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)

    # The scope is "every tracked file that decodes as UTF-8", not a list of extensions:
    # these two ship to a user and the whitelist never read them.
    for rel in ("install/com.tinycmdr.agent.plist", "INSTALL-MACOS.command"):
        if rel not in graded:
            bad.append("%s  is not graded (the rule must not be an extension whitelist)"
                       % rel)
    for rel in graded_files:
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rules = list(ANYWHERE) + private_words()
        if rel.endswith(PROSE_EXT):
            rules += list(PROSE)
        if rel.endswith(NARRATIVE_EXT) or rel in NARRATIVE_FILES:
            rules += list(FINDING_ID)
        for i, line in enumerate(text.splitlines(), 1):
            for rx, why in rules:
                if rx.search(line):
                    bad.append("%s:%d  %s  <- %s" % (rel, i, line.strip()[:110], why))
    if bad:
        print("FAIL the record speaks in the first person")
        for b in bad[:40]:
            print("  " + b)
        print("\n%d line(s): rewrite in the first person ('I', David); 'operator' means "
              "whoever runs a box." % len(bad))
        return 1
    print("ok   the record speaks in the first person (%d tracked text file(s) graded; "
          "%d binary, %d unreadable not graded)"
          % (len(graded), len(NOT_GRADED["binary"]), len(NOT_GRADED["unreadable"])))
    for kind in ("binary", "unreadable"):
        for rel in NOT_GRADED[kind]:
            print("       not graded (%s): %s" % (kind, rel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
