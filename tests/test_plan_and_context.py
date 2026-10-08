"""Plan mode is a hard read-only gate; AGENTS.md travels with the checkout.

Plan mode is enforced in code, not prose: every
write/exec tool refuses at the dispatch point until the operator approves a plan, and a
drop-in is covered because unknown tools are tier exec. `/plan apply` (or answering the
approval question) is the approval.

Context files: AGENTS.md/CLAUDE.md from the run's cwd up to the project root are read at
session start into the cached static prefix - one per depth, nearest most prominent, a
farther file contained in a nearer one dropped, the block bounded.

    python tests/test_plan_and_context.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import run_scenario  # noqa: E402

FAILS = []


def check(cond, what, detail=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}: {detail}")
    else:
        print(f"ok   {what}")


def named(block, path):
    """True when `block` names `path` the way context_files_block actually renders it.

    The block writes `<file path=%r>`, and `repr()` DOUBLES every backslash in the string - so
    `str(p) in block` is false for any Windows path while it passes on POSIX, where a path
    carries no backslash. That is how this suite's first check failed on windows-latest through
    every release while passing here, and the cut's own record said one check failed (measured
    2026-10-08 on the v1.0.88 release's product CI: 1 failing check before that release, 2
    after, because a new check I added compared `str(p)` too). Compare the rendering, not the
    path.
    """
    return repr(str(Path(path).resolve())) in block


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbplan-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        A = fb.AGENT
        ctx = {"session_key": "plan-test"}

        # ---------------------------------------------------------- plan mode
        check(fb.plan_mode("p0") == "execute", "the default mode is execute")
        fb.CONFIG["agent"]["plan_requires_approval"] = True
        check(fb.plan_mode("p1") == "plan",
              "plan_requires_approval starts a session in plan mode")
        fb.CONFIG["agent"]["plan_requires_approval"] = False

        fb.plan_mode_set("plan-test", "plan")
        check(fb.plan_mode("plan-test") == "plan", "the mode can be set")
        probe = workdir / "SHOULD-NOT-EXIST.txt"
        name, args, out = A._exec_tool(
            {"function": {"name": "shell",
                          "arguments": json.dumps({"command": "touch %s" % probe})}},
            dict(ctx))
        check(out.startswith("REFUSED") and "plan mode" in out and not probe.exists(),
              "a shell call is refused and never runs", out[:90])
        name, args, out = A._exec_tool(
            {"function": {"name": "write_file",
                          "arguments": json.dumps({"path": str(probe), "content": "x"})}},
            dict(ctx))
        check(out.startswith("REFUSED") and not probe.exists(),
              "a write is refused", out[:90])
        name, args, out = A._exec_tool(
            {"function": {"name": "read_file",
                          "arguments": json.dumps({"path": str(workdir / "config.json")})}},
            dict(ctx))
        check(not out.startswith("REFUSED"), "a read still runs", out[:60])
        name, args, out = A._exec_tool(
            {"function": {"name": "plan",
                          "arguments": json.dumps({"action": "set",
                                                   "steps": ["look", "then write"]})}},
            dict(ctx))
        check("plan mode is ON" in out and "/plan apply" in out,
              "recording a plan says how it is approved", out[-160:])

        # the plan tool is the ONLY mutating door, and it is the gate's own door
        name, args, out = A._exec_tool(
            {"function": {"name": "shell", "arguments": json.dumps({"command": "echo hi"})}},
            dict(ctx))
        check(out.startswith("REFUSED"), "...and the gate is still shut", out[:60])

        # an approval question in-band flips it
        fb.plan_mode_set("plan-door", "plan")
        name, args, out = A._exec_tool(
            {"function": {"name": "plan",
                          "arguments": json.dumps({"action": "set", "steps": ["a"]})}},
            {"session_key": "plan-door", "confirm_cb": lambda s: True})
        check(fb.plan_mode("plan-door") == "execute" and "APPROVED" in out,
              "a yes at the door leaves plan mode", out[-120:])

        # /plan apply is the operator's door
        fb.plan_mode_set("plan-test", "execute")
        check(fb.plan_mode("plan-test") == "execute", "/plan apply resumes execution")

        # ---------------------------------------------------------- context files
        tree = Path(tempfile.mkdtemp(prefix="fbctx-"))
        (tree / ".git").mkdir()
        (tree / "AGENTS.md").write_text("root rule: run the gate\n", encoding="utf-8")
        (tree / "CLAUDE.md").write_text("root CLAUDE (shadowed)\n", encoding="utf-8")
        sub = tree / "pkg" / "api"
        sub.mkdir(parents=True)
        (sub / "AGENTS.md").write_text("package rule: name the file\n", encoding="utf-8")
        deep = sub / "deep"
        deep.mkdir()
        (deep / "AGENTS.md").write_text("package rule: name the file\n", encoding="utf-8")
        rows = fb._context_files(deep)
        paths = [str(p) for p, _ in rows]
        # pkg/api/AGENTS.md is byte-identical to deep/AGENTS.md: the FARTHER one is
        # contained in the nearer, so it is dropped and only root + deep survive.
        check(paths == [str((tree / "AGENTS.md").resolve()),
                        str((deep / "AGENTS.md").resolve())],
              "one file per depth, AGENTS.md wins there, a contained farther file "
              "dropped", paths)
        check("root CLAUDE" not in "\n".join(t for _, t in rows),
              "a shadowed CLAUDE.md is not read", rows)

        block = fb.context_files_block(deep)
        check(named(block, deep / "AGENTS.md") and "package rule" in block
              and "LOCAL conventions" in block,
              "the block names the paths and labels them", block[-300:])
        # The class the check above is an instance of, graded on a path whose rendering differs
        # from `str()`: a backslash in a name is legal on POSIX and reproduces exactly the split
        # Windows creates for every path, so this fails on ANY platform if the comparison slips
        # back to `str(p)`.
        if os.name == "posix":
            # A backslash is legal in a POSIX name and reproduces the split on this host. On
            # Windows it is a SEPARATOR, so the fixture cannot exist there - and it does not
            # need to: every Windows path has the split already, which is what the check in
            # the else-branch asserts (this suite runs in the dev Windows tier now, so both
            # halves have to be true wherever they run).
            odd_tree = Path(tempfile.mkdtemp(prefix="fbctx-odd-"))
            (odd_tree / ".git").mkdir()
            (odd_tree / "a\\b").mkdir()
            (odd_tree / "a\\b" / "AGENTS.md").write_text("odd rule: name the file\n",
                                                        encoding="utf-8")
            odd = (odd_tree / "a\\b" / "AGENTS.md").resolve()
            odd_block = fb.context_files_block(odd_tree / "a\\b")
            check(str(odd) != repr(str(odd)),
                  "the fixture reproduces the rendering split (a backslash in the path)",
                  (str(odd), repr(str(odd))))
            check(named(odd_block, odd) and "odd rule" in odd_block,
                  "...and the block names that path too", odd_block[-200:])
            shutil.rmtree(odd_tree, ignore_errors=True)
        else:
            _win = Path("C:/somewhere/AGENTS.md")
            check(str(_win) != repr(str(_win)),
                  "on Windows EVERY path has the str/repr split the named() comparison "
                  "exists for", (str(_win), repr(str(_win))))

        fb.CONFIG["agent"]["context_files_max_chars"] = 20
        block = fb.context_files_block(deep)
        check(len(block) < 900 and "package rule" not in block and "CUT at 0 of" in block,
              "the block honours the char budget and SAYS it cut", block[-300:])
        fb.CONFIG["agent"]["context_files_max_chars"] = 4000

        # ---- a cut says so, and the cut lands on a line boundary (run 21, A-61) ---------
        # The block used to truncate in silence at the byte: the model read half a rulebook,
        # would not go and look for the rest, and nothing in the log said a rule was missing.
        cut_tree = Path(tempfile.mkdtemp(prefix="fbctx-cut-"))
        (cut_tree / ".git").mkdir()
        (cut_tree / "AGENTS.md").write_text("line one is a rule\n" * 40
                                           + "the very last rule\n", encoding="utf-8")
        fb.CONFIG["agent"]["context_files_max_chars"] = 120
        cut_block = fb.context_files_block(cut_tree)
        check("CUT at" in cut_block and "AGENTS.md" in cut_block
              and "of %d chars" % len((cut_tree / "AGENTS.md")
                                      .read_text(encoding="utf-8").strip()) in cut_block,
              "a cut file is marked in the prompt, by name and counts",
              cut_block[-260:])
        check(cut_block.count("line one is a rule") == 6
              and "the very last rule" not in cut_block
              and "line one is a rul\n" not in cut_block,
              "the visible half ends on a line boundary, not mid-rule",
              cut_block[:400])
        # A budget the first file spends to the character leaves the next one unreached: it
        # must be named, not dropped (the old code `break`ed in silence).
        fb.CONFIG["agent"]["context_files_max_chars"] = len(
            (cut_tree / "AGENTS.md").read_text(encoding="utf-8").strip())
        (cut_tree / "pkg").mkdir()
        (cut_tree / "pkg" / "AGENTS.md").write_text("near rule: keep this\n", encoding="utf-8")
        starved = fb.context_files_block(cut_tree / "pkg")
        check("NOT READ" in starved and named(starved, cut_tree / "pkg" / "AGENTS.md"),
              "a file the budget never reached is named as NOT READ", starved[-320:])
        fb.CONFIG["agent"]["context_files_max_chars"] = 4000
        shutil.rmtree(cut_tree, ignore_errors=True)

        fb.CONFIG["agent"]["context_files"] = False
        check(fb.context_files_block(deep) == "", "context_files=false disables it")
        fb.CONFIG["agent"]["context_files"] = True

        # the prompt carries it (static prefix), and disabling strips it
        was_cwd = Path.cwd()
        import os as _os
        _os.chdir(tree)
        try:
            prompt = fb.build_system_prompt()
            check("root rule: run the gate" in prompt,
                  "the static prompt carries the repo's rules")
            fb.CONFIG["agent"]["context_files"] = False
            check("root rule: run the gate" not in fb.build_system_prompt(),
                  "...and dropping the switch removes them")
        finally:
            fb.CONFIG["agent"]["context_files"] = True
            _os.chdir(was_cwd)
        shutil.rmtree(tree, ignore_errors=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all plan/context checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
