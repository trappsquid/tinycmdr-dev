#!/usr/bin/env python3
"""Promote ledger items for a tag that now exists.

    python maintenance/ledger-tag.py v1.0.39            # promote what that tag carries
    python maintenance/ledger-tag.py v1.0.39 --check    # say what it would change, change nothing

WHY THIS EXISTS. An item about work that is on main but in no release cannot carry
`expect: tagged` - the tag does not exist yet - and it must not carry `expect: untagged` either,
because cutting the release falsifies that claim the moment the tag lands. Measured 2026-09-29:
the 1.0.39 release commit was pushed, `gh release create` produced the tag seconds later, and CI
failed on that commit in all three jobs with

    sglang-window says unreleased, but 5740200 is in v1.0.39

- the ledger doing its job at the one moment its claim was neither true nor false yet.

So the claim is stated only when it is checkable. release.sh runs this after the tag exists, so a
release ends with the record already correct and CI green, instead of red until someone notices.
An item with no tag expectation is an item nobody is claiming anything about; that is the state
between "merged" and "released".
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "STATUS.json"


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    tag = args.tag

    if not git("rev-parse", "--verify", "refs/tags/%s" % tag):
        print("no such tag here: %s (release.sh fetches tags before this runs)" % tag)
        return 1

    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    moved = []
    for it in data["items"]:
        a = it.get("anchor") or {}
        commit = a.get("commit")
        if not commit or a.get("expect") == "tagged":
            continue
        if tag in git("tag", "--contains", commit).splitlines():
            moved.append(it["id"])
            if not args.check:
                it["state"] = "shipped"
                a["expect"] = "tagged"
                it["anchor"] = a

    if not moved:
        print("%s carries nothing new in the ledger" % tag)
        return 0
    for name in moved:
        print("%s %s -> shipped (in %s)" % ("would promote" if args.check else "promoted",
                                            name, tag))
    if not args.check:
        LEDGER.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
