"""Plan mode is a hard read-only gate; AGENTS.md travels with the checkout.

Plan mode is enforced in code, not prose (omp: tools/plan-mode-guard.ts): every
write/exec tool refuses at the dispatch point until the operator approves a plan, and a
drop-in is covered because unknown tools are tier exec. `/plan apply` (or answering the
approval question) is the approval.

Context files: AGENTS.md/CLAUDE.md from the run's cwd up to the project root are read at
session start into the cached static prefix - one per depth, nearest most prominent, a
farther file contained in a nearer one dropped, the block bounded (omp:
docs/context-files.md).

    python tests/test_plan_and_context.py
"""
import json
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
        check(str((deep / "AGENTS.md").resolve()) in block and "package rule" in block
              and "LOCAL conventions" in block,
              "the block names the paths and labels them", block[-300:])

        fb.CONFIG["agent"]["context_files_max_chars"] = 20
        block = fb.context_files_block(deep)
        check(len(block) < 400 and "package rule" not in block,
              "the block honours the char budget", block)
        fb.CONFIG["agent"]["context_files_max_chars"] = 4000

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
