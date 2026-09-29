"""Every work item's state is anchored to something the repository can check.

Why this exists. A 2026-09-28 review of this project's own notes found three items describing
their work as "BUILT, unshipped (commit a4722fb, not pushed; no release cut)" for a change that
had shipped in 1.0.37, one item headed as an outage while its own body recorded the fix, two
headed as closed inside a section called "Still open", and two cross-references pointing at the
wrong item. None of that was lying. The record was prose, and prose has no way to disagree with
the repository - so a reader had to re-derive every claim by hand, and a reader who did not
bothered was misled.

STATUS.json is the machine-readable half. This suite is what makes it checkable:

  1. the ledger parses and every item is well formed (id, title, a state from the allowed set)
  2. ids are unique, so an item can be referred to by name
  3. every commit anchor exists
  4. an item marked expect=tagged IS reachable from a tag - the work shipped
  5. an item marked expect=untagged is reachable from NO tag - it has not
  6. every file anchor exists

On CI. This needs tag history and actions/checkout defaults to fetch-depth: 1, which brings no
tags; the workflow therefore checks out with fetch-depth: 0. A clone with no tags fails check 4
loudly rather than passing quietly, because `git tag` is how "shipped" is decided here and a
green run that graded nothing is the failure mode this project keeps catching.

    python tests/test_status.py
"""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LEDGER = BASE / "STATUS.json"
STATES = ("open", "blocked", "wish", "shipped", "closed")

PASSES, FAILS = [], []


def check(name, cond, detail=""):
    (PASSES if cond else FAILS).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "   " + str(detail)))


def git(*args):
    # A host with no git at all - the Windows fleet box - raises FileNotFoundError from exec,
    # not a non-zero exit. That crashed this suite with WinError 2 before it graded anything.
    try:
        p = subprocess.run(["git", "-C", str(BASE), *args], capture_output=True, text=True)
    except OSError:
        return 127, ""
    return p.returncode, p.stdout.strip()


def main():
    check("the ledger is in the tree", LEDGER.exists(), LEDGER)
    if not LEDGER.exists():
        print()
        print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
        return 1
    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    items = data.get("items") or []
    check("it holds items", bool(items), len(items))

    ids = [it.get("id") for it in items]
    check("every item has an id and a title",
          all(it.get("id") and it.get("title") for it in items),
          [it.get("id") for it in items if not (it.get("id") and it.get("title"))])
    check("ids are unique", len(ids) == len(set(ids)),
          sorted({i for i in ids if ids.count(i) > 1}))
    check("every state is one this file knows", all(it.get("state") in STATES for it in items),
          [(it.get("id"), it.get("state")) for it in items if it.get("state") not in STATES])

    # An EXPORT has no history at all - a package, or `git archive` - and that is a normal way to
    # deploy: the fleet gate runs from one because the Windows host has no git. There the ledger's
    # shape and its file anchors are still graded, and the commit anchors cannot be; the run says
    # which and how many. A CHECKOUT with no tags is a different thing - a shallow clone or a
    # missing fetch - and that fails, because git tag is how "shipped" is decided here.
    is_checkout = (BASE / ".git").exists()
    _, taglist = git("tag", "-l") if is_checkout else (0, "")
    tags = [t for t in taglist.splitlines() if t.strip()]
    if is_checkout:
        check("this checkout carries tag history, so 'shipped' can be decided", bool(tags),
              "no tags: actions/checkout needs fetch-depth: 0, or run `git fetch --tags`")
    else:
        print("note  no .git in this tree: it is an export, not a checkout - file anchors are "
              "graded below, commit anchors cannot be")

    bad_file, bad_commit, wrong_expect, unverified = [], [], [], 0
    for it in items:
        a = it.get("anchor") or {}
        if "file" in a:
            if not (BASE / a["file"]).exists():
                bad_file.append("%s -> %s" % (it["id"], a["file"]))
            continue
        commit = a.get("commit")
        if not commit:
            continue
        if not is_checkout:
            unverified += 1
            continue
        rc, _ = git("cat-file", "-e", commit + "^{commit}")
        if rc != 0:
            bad_commit.append("%s -> %s" % (it["id"], commit))
            continue
        _, containing = git("tag", "--contains", commit)
        in_tag = bool(containing.strip())
        if a.get("expect") == "tagged" and not in_tag:
            wrong_expect.append("%s says shipped, but no tag contains %s" % (it["id"], commit))
        if a.get("expect") == "untagged" and in_tag:
            wrong_expect.append("%s says unreleased, but %s is in %s"
                                % (it["id"], commit, containing.split()[0]))

    check("every file anchor exists", not bad_file, bad_file)
    check("every commit anchor exists", not bad_commit, bad_commit)
    check("every 'shipped'/'unreleased' claim agrees with the tags", not wrong_expect, wrong_expect)
    if unverified:
        print("note  %d commit anchor(s) NOT verified: this tree carries no git history" % unverified)

    print()
    print("%d passed, %d failed" % (len(PASSES), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
