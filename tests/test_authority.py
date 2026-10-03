"""Per-tool authority: a deny by name, a prompt by name, and a fail-closed tier.

The regex tiers read TEXT; they cannot deny a tool by name, cannot gate a drop-in whose
arguments match no pattern, and cannot express "ask before anything that executes".
The model: a tier per tool, a mode that acts as a ceiling, a policy map that overrides
the mode, and "unknown = exec" so a new tool cannot be born auto-approved. The historical behaviour is the default
(`auto`): the regex tiers are unchanged and nothing new prompts.

    python tests/test_authority.py
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
    workdir = Path(tempfile.mkdtemp(prefix="fbauthority-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        A = fb.AGENT

        # ---- tiers
        check(fb.tool_tier("shell") == "exec", "shell is exec")
        check(fb.tool_tier("read_file") == "read", "read_file is read")
        check(fb.tool_tier("edit_file") == "write", "edit_file is write")
        check(fb.tool_tier("some_dropin_nobody_listed") == "exec",
              "an unknown tool is exec (fail closed)")

        # ---- default mode adds nothing
        fb.CONFIG["agent"]["approval_mode"] = "auto"
        fb.CONFIG["agent"]["tool_policy"] = {}
        check(fb.resolve_approval("shell", {"command": "echo hi"}, {}) is None,
              "auto mode lets a shell call through, as before")
        check(fb.resolve_approval("read_file", {"path": "x"}, {}) is None,
              "auto mode lets a read through")

        # ---- deny by name beats every mode
        fb.CONFIG["agent"]["tool_policy"] = {"shell": "deny"}
        ref = fb.resolve_approval("shell", {"command": "echo hi"}, {})
        check(ref and ref.startswith("REFUSED") and "tool_policy" in ref,
              "tool_policy deny refuses by name", ref)
        check(fb.resolve_approval("read_file", {"path": "x"}, {}) is None,
              "...and leaves other tools alone")
        fb.CONFIG["agent"]["tool_policy"] = {"read_file": "deny"}
        check((fb.resolve_approval("read_file", {"path": "x"}, {}) or "").startswith("REFUSED"),
              "deny works for a read tool too")
        fb.CONFIG["agent"]["tool_policy"] = {}

        # ---- a prompt with no door declines
        fb.CONFIG["agent"]["approval_mode"] = "write"
        ref = fb.resolve_approval("shell", {"command": "echo hi"}, {})
        check(ref and ref.startswith("DECLINED"), "a stricter mode with no door declines", ref)
        check(fb.resolve_approval("edit_file", {"path": "x"}, {}) is None,
              "'write' lets write-tier tools through")
        check(fb.resolve_approval("read_file", {"path": "x"}, {}) is None,
              "'write' lets reads through")

        # ---- ...and a door that says yes allows it
        fb.CONFIG["agent"]["approval_mode"] = "ask"
        check(fb.resolve_approval("edit_file", {"path": "x"},
                                  {"confirm_cb": lambda s: True}) is None,
              "'ask' + a yes lets a write through")
        check((fb.resolve_approval("shell", {"command": "echo hi"},
                                   {"confirm_cb": lambda s: False}) or "").startswith("DECLINED"),
              "'ask' + a no still declines")

        # ---- the dispatch actually consults it
        probe = workdir / "SHOULD-NOT-EXIST.txt"
        fb.CONFIG["agent"]["approval_mode"] = "auto"
        fb.CONFIG["agent"]["tool_policy"] = {"shell": "deny"}
        name, args, out = A._exec_tool(
            {"function": {"name": "shell",
                          "arguments": json.dumps(
                              {"command": "touch %s" % probe})}},
            {"session_key": "auth-1"})
        check(out.startswith("REFUSED") and not probe.exists(),
              "a denied tool never runs", (out[:60], probe.exists()))
        fb.CONFIG["agent"]["tool_policy"] = {}
        name, args, out = A._exec_tool(
            {"function": {"name": "read_file",
                          "arguments": json.dumps({"path": str(probe)})}},
            {"session_key": "auth-2"})
        check(not out.startswith("REFUSED"),
              "a read tool still dispatches", out[:60])

        # ---- allow_patterns: a whitelist under the confirm tier
        fb.CONFIG["agent"]["confirm_patterns"] = [r"\brm\b"]
        fb.CONFIG["agent"]["allow_patterns"] = [r"\brm\s+-i\b"]
        check(fb._confirm_hit("rm -rf build/") is not None,
              "the confirm tier still matches", fb._confirm_hit("rm -rf build/"))
        check(fb._confirm_hit("rm -i build/file") is None,
              "an allow pattern clears the confirm tier for one shape")
        check(fb.is_blocked("rm -rf /") is not None,
              "blocked still wins independently of allow")
        # the extra-merge path folds into the new lists
        fb.CONFIG["agent"]["allow_patterns"] = []
        merged = {"allow_patterns": [], "allow_patterns_extra": [r"\bdf\b"]}
        fb._merge_guard_extras(merged)
        check(merged.get("allow_patterns") == [r"\bdf\b"],
              "allow_patterns_extra folds in like every other tier", merged)
        fb.CONFIG["agent"]["confirm_patterns"] = []
        fb.CONFIG["agent"]["allow_patterns"] = []
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all authority checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
