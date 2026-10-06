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
        rules = list(ANYWHERE)
        if rel.endswith(PROSE_EXT):
            rules += list(PROSE)
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
