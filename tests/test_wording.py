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
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = "tests/test_wording.py"

TEXT_EXT = (".py", ".md", ".json", ".sh", ".ps1", ".cmd", ".toml", ".txt", ".yml",
            ".yaml", ".html", ".css", ".js", ".svg")

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

# Banned in EVERY tracked text file: a reference to my private working records - the nightly
# audits, the sessions that fix their findings, those findings' ids, the ledger and the share
# they live on. The repo is public: it says what was wrong and what changed, never which run
# found it or where the write-up is kept. A dated measurement is the public form of the same
# fact ("measured 2026-10-06 on a Windows install"). Measured 2026-10-07: a release page, the
# record's newest items and several commit bodies all named a run and its finding ids.
PRIVATE = (
    (re.compile(r"night[- ]audit", re.I), "a private audit run (say what was measured, and when)"),
    (re.compile(r"bug[- ]hunt", re.I), "a private session name (say what changed)"),
    (re.compile(r"\bhunt-\d{4}-\d\d-\d\d", re.I), "a private session name"),
    (re.compile(r"\baudit-\d{4}-\d\d-\d\d", re.I), "a private report filename"),
    (re.compile(r"the work record"), "the private findings record"),
    (re.compile(r"\ban earlier pass\b"), "a private triage state"),
    (re.compile(r"\bthe private share\b", re.I), "the private share"),
)
# ...and in the narrative files the finding ids go too: a reader of the changelog or the record
# cannot look one up, and the id names the session that produced it. Source comments keep theirs
# (they are opaque labels beside the reason), so this half is scoped to what a reader reads.
FINDING_ID = (
    (re.compile(r"A-\d{4}-\d\d-\d\d-\d+"), "a private finding id"),
    (re.compile(r"\bA-\d{2,3}\b"), "a private finding id"),
)
NARRATIVE_EXT = (".md",)
NARRATIVE_FILES = ("STATUS.json",)


def tracked():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True,
                         text=True).stdout
    return [f for f in out.splitlines() if f.endswith(TEXT_EXT) and f != SELF]


def main():
    bad = []
    for rel in tracked():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rules = list(ANYWHERE) + list(PRIVATE)
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
    print("ok   the record speaks in the first person (no maintainer/we-voice stand-ins)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
