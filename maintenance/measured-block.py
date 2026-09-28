#!/usr/bin/env python3
"""Regenerate the MEASURED blocks of docs/tinycmdr-what-it-is.md from the tree.

The doc that makes this project believable cannot be allowed to drift from the code: a
stale number in a document whose pitch is "measured, honest numbers" is worse than no
number. So the numbers in it are not written by hand any more - they are rendered from
the tree, and committed between markers:

    <!-- measured:surface:start -->
    ...
    <!-- measured:surface:end -->

    python maintenance/measured-block.py            # check: exit 1 when the doc is stale
    python maintenance/measured-block.py --write    # rewrite the blocks in place
    python maintenance/measured-block.py --print    # what it would write

`tests/test_measured_doc.py` is the gate: it runs the check and fails, and it falsifies
itself on a doctored copy so "the gate is green" means something.

What is NOT in a block, and cannot be: anything measured against a live endpoint (the
token figures) and anything about a specific box (its skills, its drop-in tools). Those
stay hand-written with their provenance next to them.
"""
import argparse
import difflib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DOC = BASE / "docs" / "tinycmdr-what-it-is.md"
SRC = BASE / "tinycmdr.py"


def marker(name):
    return ("<!-- measured:%s:start -->" % name, "<!-- measured:%s:end -->" % name)


# --------------------------------------------------------------------- staging
def _staged_module():
    """Import the build from a temp dir with a config beside it.

    A clean clone (and CI) has no config.json - the installer writes it - so the
    fixture the suites use is copied in as config.json, exactly the way test_stall.py
    and run_scenario.stage_install do it. Nothing is written into the checkout.
    """
    work = Path(tempfile.mkdtemp(prefix="tc-measured-"))
    shutil.copy2(SRC, work / "tinycmdr.py")
    fixture = BASE / "tests" / "fixture-config.json"
    if fixture.exists():
        shutil.copy2(fixture, work / "config.json")
    else:
        (work / "config.json").write_text(json.dumps({
            "llm": {"base_url": "http://127.0.0.1:1/v1", "model": "main"},
            "agent": {}}), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("tc_measured", work / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tc_measured"] = mod
    spec.loader.exec_module(mod)
    return work, mod


def _git_ls(pattern):
    try:
        out = subprocess.run(["git", "-C", str(BASE), "ls-files", pattern],
                             capture_output=True, text=True, timeout=20)
        if out.returncode == 0:
            return [l for l in out.stdout.splitlines() if l.strip()]
    except Exception:                                   # noqa: BLE001
        pass
    return []


def file_stats(path):
    """(lines, MB) for a file, with line endings normalized to LF first.

    The count must not depend on the checkout: GitHub's Windows runners check out with CRLF
    (core.autocrlf=true), which adds a byte per line - about 21 KB here, enough to make the
    doc look stale and fail the gate on one platform only. What the doc describes is the
    repository's content, so measure that.
    """
    text = path.read_bytes().replace(b"\r\n", b"\n")
    lines = text.count(b"\n") + (0 if text.endswith(b"\n") else 1)
    return lines, len(text) / 1048576


def facts():
    work, T = _staged_module()
    try:
        lines, mb = file_stats(SRC)
        deps = [l.split("[")[0].split("=")[0].split(">")[0].strip()
                for l in (BASE / "requirements.txt").read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.strip().startswith("#")]
        suites = sorted((BASE / "tests").glob("test_*.py"))
        tlines = sum(len(p.read_bytes().splitlines()) for p in suites)
        checks = sum(len(re.findall(r"^\s*check\(", p.read_text(encoding="utf-8",
                                                                   errors="replace"),
                                    re.M)) for p in suites)
        support = sorted(p.name for p in (BASE / "tests").glob("*.py")
                         if not p.name.startswith("test_"))
        graded = len(re.findall(r'"id": "T\d+',
                                (BASE / "tests" / "eval_tasks.py").read_text(encoding="utf-8")))
        shipped_tools = [p for p in _git_ls("tools") if p.endswith(".py")]
        shipped_skills = [p for p in _git_ls("skills") if p.endswith("SKILL.md")]
        cfg = T.DEFAULT_CONFIG
        return {
            "version": T.VERSION,
            "lines": lines,
            "mb": mb,
            "deps": deps,
            "core": len(T.CORE_TOOLS),
            "always_on": len(T.select_tool_schemas(None)),
            "shipped_tools": len(shipped_tools),
            "shipped_skills": len(shipped_skills),
            "cli_verbs": len(T.VERBS),
            "chat_verbs": len(T._CHAT_VERB_SET),
            "suites": len(suites),
            "test_lines": tlines,
            "checks": checks,
            "support": len(support),
            "graded": graded,
            "blocks": [k for k in cfg if not k.startswith("_")],
            "keys": {k: len([x for x in v if not x.startswith("_")])
                     for k, v in cfg.items() if isinstance(v, dict)},
            "agent": cfg["agent"],
            "llm": cfg["llm"],
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------- rendering
def render(name, f):
    if name == "header":
        return (
            "Working definition of v%s, the tree this document ships with. Every number in\n"
            "section 1 and section 3.2 is rendered from the code by\n"
            "`maintenance/measured-block.py` - `tests/test_measured_doc.py` fails when the\n"
            "committed numbers disagree with the tree, so they cannot rot. Section 6 is a\n"
            "comparison against projects other people maintain, and every claim about those\n"
            "projects is labelled with where it came from."
            % f["version"])
    if name == "surface":
        k = f["keys"]
        opt = {"croniter", "rich", "prompt_toolkit"}
        req = [d for d in f["deps"] if d not in opt]
        op = [d for d in f["deps"] if d in opt]
        cfg_keys = ", ".join("%s %d" % (b, k.get(b, 0)) for b in f["blocks"])
        skills = ("%d runbook folder(s) ship in ./skills/ (SKILL.md, read on demand)"
                  % f["shipped_skills"]) if f["shipped_skills"] else (
            "no runbook ships in the repo - ./skills/ is per-host and gitignored,\n"
            "                    read on demand when a box has any")
        return (
            "code                %s lines / %.2f MB in ONE file, no package, no framework\n"
            "dependencies        %d required (%s); %d optional\n"
            "                    (croniter for `schedule`; rich + prompt_toolkit for the console)\n"
            "                    - %d lines in requirements.txt, none of them a framework\n"
            "processes           one; no daemon, no gateway, no database\n"
            "interfaces          Mattermost bot (DMs + @mentions), Telegram DM, `--once \"task\"`\n"
            "                    and a terminal CLI; a host with no chat token is CLI-only\n"
            "core tools          %d, of which %d are always-on; the rest answer by name (section 2)\n"
            "custom tools        %d example tools ship in ./tools/ (native .py, register-style .py,\n"
            "                    <name>.tool.json); a working box's own drop-ins load from the same\n"
            "                    folder, and the agent writes its own with create_tool\n"
            "chat commands       %d CLI verbs, %d chat verbs (section 3.1)\n"
            "prose skills        %s\n"
            "tests               %d suites / %s lines / %s checks that need no model, plus a graded\n"
            "                    set of %d tasks against a real endpoint (%d support scripts;\n"
            "                    run_all.py is the gate)\n"
            "config              config.json, %d blocks: %s\n"
            "                    (all of section 3 is configurable)\n"
            "state on disk       sessions/*.json (per channel), notes.md, tasks.json (ledger),\n"
            "                    jobs.json (cron), uploads/, logs"
            % (f"{f['lines']:,}", f["mb"],
               len(req), ", ".join(req), len(op), len(f["deps"]),
               f["core"], f["always_on"], f["shipped_tools"],
               f["cli_verbs"], f["chat_verbs"], skills,
               f["suites"], f"{f['test_lines']:,}", f"{f['checks']:,}", f["graded"],
               f["support"], len(f["blocks"]), cfg_keys))
    if name == "budgets":
        a = f["agent"]
        return (
            "max_steps %-19s hard stop on tool calls per run\n"
            "max_minutes %-17s wall clock per run\n"
            "shell_timeout %-15s per command\n"
            "tool_output_max_chars %-5s what a tool may hand back into context\n"
            "max_context_tokens          context budget, with `context` reporting the fill\n"
            "history_exchanges           session depth kept in the prompt\n"
            "notes_max_chars / per-note / keep / archive_days   memory caps and rotation\n"
            "tasks_max_open / done_keep  ledger caps"
            % (a["max_steps"], a["max_minutes"], a["shell_timeout"],
               a["tool_output_max_chars"]))
    if name == "readability":
        # This one line is a CONSTANT, not rendered from the tree, and that is the one
        # figure in a generated block that can rot silently: a tokenizer is not in the
        # tree, so nothing here can re-measure it. It is the clean-unpack leg of section
        # 4.1's table (the public number - the doc says why), measured 2026-09-27 with
        # `maintenance/measure-prompt.py --tokenize <endpoint>`. Re-run that after any
        # prompt change and update this string; tests/test_measured_doc.py asserts the
        # token figures carry provenance, not that they are current.
        return (
            "fixed prompt overhead     ~3.5K real tokens as sent on a clean unpack, measured with\n"
            "                          the endpoint's own tokenizer - section 4.1 has both legs and\n"
            "                          the command\n"
            "readability               %s lines, one file, no dependency tree to audit\n"
            "ops runtime               stall watchdog, task ledger, periodic check-ins, live steering, and a\n"
            "                          /tinycmdr stop that reports the truth about three different states\n"
            "self-extension            a new tool is a .py file the agent writes itself, live on the next call\n"
            "prose skills              the runbooks are plain markdown an operator can read and edit mid-incident\n"
            "deployment surface        three dependencies, no daemon, no database, works offline on a LAN with a\n"
            "                          local model server, and the chat server is self-hosted\n"
            "per-host identity         one bot account per machine, so \"which box am I talking to\" is never a guess"
            % f"{f['lines']:,}")
    raise KeyError(name)


BLOCKS = ("header", "surface", "budgets", "readability")
# Blocks that live inside a code fence in the doc. The markers sit OUTSIDE the fence so
# the rendered document does not show HTML comments as code.
FENCED = {"surface", "budgets", "readability"}


def wrapped(name, body):
    if name in FENCED:
        return "```\n%s\n```" % body
    return body


# ----------------------------------------------------------------- integration
def current_block(doc, name):
    start, end = marker(name)
    i, j = doc.find(start), doc.find(end)
    if i < 0 or j < 0 or j < i:
        return None
    return doc[i + len(start):j].strip()


def rewrite(doc, name, body):
    start, end = marker(name)
    i, j = doc.find(start), doc.find(end)
    return doc[:i + len(start)] + "\n" + body + "\n" + doc[j:]


def stale_blocks(doc_path=None):
    """[(block, diff-or-why)] for every block that disagrees with the tree."""
    f = facts()
    doc = (doc_path or DOC).read_text(encoding="utf-8")
    out = []
    for name in BLOCKS:
        have = current_block(doc, name)
        if have is None:
            out.append((name, "markers missing"))
            continue
        want = wrapped(name, render(name, f))
        if have != want:
            out.append((name, "\n".join(difflib.unified_diff(
                have.splitlines(), want.splitlines(),
                fromfile="doc", tofile="tree", lineterm=""))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="rewrite the doc in place")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the blocks instead of checking")
    args = ap.parse_args()

    if args.show:
        f = facts()
        for name in BLOCKS:
            print("=== %s\n%s\n" % (name, wrapped(name, render(name, f))))
        return 0

    if args.write:
        doc = DOC.read_text(encoding="utf-8")
        f = facts()
        for name in BLOCKS:
            if current_block(doc, name) is None:
                print("no %s markers in %s" % (name, DOC), file=sys.stderr)
                return 2
            doc = rewrite(doc, name, wrapped(name, render(name, f)))
        DOC.write_text(doc, encoding="utf-8")
        print("rewrote %d measured block(s) in %s" % (len(BLOCKS), DOC))
        return 0

    stale = stale_blocks()
    if stale:
        for name, why in stale:
            print("STALE measured block: %s" % name)
            print(why if why.startswith("---") or "markers" in why
                  else "\n" + "\n".join("  " + l for l in why.splitlines()))
        print("\nrun: python maintenance/measured-block.py --write", file=sys.stderr)
        return 1
    print("measured blocks agree with the tree (%d)" % len(BLOCKS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
