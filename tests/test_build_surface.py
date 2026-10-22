"""Build-in-code: the verification gate, failure-shaped test output, change review.

The failure this suite pins: a run asked to build a 3D game from a reference image
produced a buggy prototype and stopped early. Nothing defined "done" beyond the model's
word, a build's output had flooded the window, no plan was required before the first
write, and there was no way to review or undo what the run had changed.

Every feature is config-gated and default-off, so the first check is the one that matters
most: a clean unpack's fixed prompt is unchanged. The rest drive the tools directly (no
model) except the done-gate section, which uses a scripted endpoint to prove the gate
challenges an unverified answer and accepts a passing check.

    python tests/test_build_surface.py
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


def load(gates=None, files=None):
    """A fresh staged install with this test's config gates and fixture files."""
    workdir = Path(tempfile.mkdtemp(prefix="fbbuild-"))
    cfg = run_scenario.stage_install(workdir, 24000)
    cfg["agent"].update(gates or {})
    (workdir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    for rel, body in (files or {}).items():
        p = workdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return workdir, run_scenario.load(workdir)


def tool_call(name, args, cid="c1"):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class FakeResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def tool_reply(name, args, cid="c1"):
    return {"choices": [{"message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": cid, "type": "function",
                        "function": {"name": name,
                                     "arguments": json.dumps(args)}}]},
        "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def text_reply(text):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def install_stub(fb, script):
    seen = []

    def fake_post(url, headers, payload, timeout, grace, cancel_event=None,
                  stream=False):
        seen.append(json.loads(json.dumps(payload)))
        return FakeResp(script(len(seen) - 1, payload))

    fb._post_watchdog = fake_post
    fb.AGENT._post_watchdog = fake_post
    return seen


def payload_text(payload):
    return "\n".join(str(m.get("content") or "") for m in payload.get("messages", []))


def py(code):
    return '"%s" -c "%s"' % (sys.executable, code.replace('"', '\\"'))


def main():
    # ---------------------------------------------------------------- default posture
    wd, fb = load()
    try:
        names = ("submit_done", "run_tests", "changes", "build_project")
        check(not any(n in fb.CORE_TOOLS for n in names),
              "the build tools are NOT registered by default")
        check(not any(n in fb.hidden_tools(None) for n in names),
              "...and are not on the hidden shelf either (no prompt cost)")
        check("submit_done" not in fb.build_system_prompt()
              and "run_tests" not in fb.build_system_prompt(),
              "the default system prompt does not name them")
        check(not fb.build_tools_on(), "build_tools_on() is false on a clean unpack")
        out = fb.AGENT._exec_tool(tool_call("run_tests", {"command": "true"}),
                                  {"session_key": "s-off"})[2]
        check("agent.build_tools" in out and "off" in out,
              "a call to a gated-off build tool names the key that turns it on", out[:160])
        out = fb.tool_write_file({"path": str(wd / "plain.txt"),
                                  "content": "plain\n"}, {"session_key": "s-off"})
        check(out.startswith("OK: wrote"), "a normal write still works with the gate off")
        check(not (wd / "sessions" / "changes").exists()
              and not (fb.run_state("s-off") or {}).get("changes"),
              "...and it takes no change snapshot on a box that did not ask for one")
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    wd, fb = load({"build_tools": True})
    try:
        names = ("submit_done", "run_tests", "changes", "build_project")
        check(all(n in fb.CORE_TOOLS for n in names),
              "build_tools on registers all four tools")
        check(all(n in fb.hidden_tools(None) for n in names),
              "...as hidden tools (a name on a shelf, schema on reveal)")
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    # ---------------------------------------------------------------- build mode
    wd, fb = load({"build_mode": True}, {"target.txt": "original\n"})
    try:
        ctx = {"session_key": "s-buildmode"}
        target = str(wd / "target.txt")
        out = fb.AGENT._exec_tool(tool_call(
            "write_file", {"path": target, "content": "changed\n"}), ctx)[2]
        check(out.startswith("REFUSED (build mode)") and "plan first" in out,
              "build mode refuses the first write before any plan", out[:120])
        check("plan" in fb.revealed_tools("s-buildmode"),
              "...and reveals the plan schema so the next turn can act")
        out = fb.AGENT._exec_tool(tool_call(
            "read_file", {"path": target}), ctx)[2]
        check(not out.startswith("REFUSED"), "a read-only call is never blocked", out[:120])
        out = fb.tool_plan({"action": "set",
                            "steps": "inspect the target\nwrite the change\nrun the check"},
                           ctx)
        check("3 step(s) recorded" in out, "the model's own plan is recorded")
        out = fb.AGENT._exec_tool(tool_call(
            "write_file", {"path": target, "content": "changed\n"}), ctx)[2]
        check(out.startswith("OK: wrote"), "after the plan, the same write runs", out[:120])
        fb.tool_plan({"action": "clear"}, ctx)
        out = fb.AGENT._exec_tool(tool_call(
            "write_file", {"path": target, "content": "again\n"}), ctx)[2]
        check(out.startswith("REFUSED (build mode)"),
              "clearing the plan closes the gate again", out[:120])
        out = fb.AGENT._exec_tool(tool_call(
            "shell", {"command": "echo hi > redirected.txt"}), ctx)[2]
        check(out.startswith("REFUSED (build mode)"),
              "a side-effecting shell call is gated too", out[:120])
        # The harness-derived plan is not the model's own plan, by design.
        fb.set_derived_plan("s-buildmode", ["step one from the order",
                                           "step two from the order"])
        out = fb.AGENT._exec_tool(tool_call(
            "write_file", {"path": target, "content": "derived\n"}), ctx)[2]
        check(out.startswith("REFUSED (build mode)"),
              "a request-derived plan does not satisfy build mode", out[:120])
        # Read-only shell is not blocked; a redirect is a write.
        check(not fb._shell_has_side_effects("git status --short"),
              "a read-only shell command is not a build mutation")
        check(fb._shell_has_side_effects("echo hi > out.txt"),
              "a redirect is a build mutation")
        check(not fb._shell_has_side_effects("python -m pytest -q"),
              "running a test command is not blocked as a write")
        check(fb._shell_has_side_effects("python build.py"),
              "running a script can write, so it is gated")
        check(fb._shell_has_side_effects(
                  "python3 -c \"open('x.txt','w').write('x')\""),
              "a write hidden in python -c is gated")
        check(fb._shell_has_side_effects("bash -c \"echo x > x.txt\""),
              "a redirect inside bash -c is gated")
        check(not fb._shell_has_side_effects("bash -c \"git status\""),
              "...while a read-only bash -c is not")
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    # with build_mode off, the same call is untouched
    wd, fb = load({}, {"target.txt": "original\n"})
    try:
        target = str(wd / "target.txt")
        out = fb.AGENT._exec_tool(tool_call(
            "write_file", {"path": target, "content": "changed\n"}),
            {"session_key": "s-nomode"})[2]
        check(out.startswith("OK: wrote"),
              "with build_mode off the write is identical to today", out[:120])
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    # ---------------------------------------------------------------- changes
    wd, fb = load({"build_tools": True},
                  {"a.txt": "one\ntwo\n", "b.txt": "bee\n"})
    try:
        ctx = {"session_key": "s-changes"}
        a, b = wd / "a.txt", wd / "b.txt"
        out = fb.tool_edit_file({"path": str(a), "old_string": "two",
                                 "new_string": "TWO"}, ctx)
        check(out.startswith("OK:"), "edit_file writes the first fixture", out[:100])
        out = fb.tool_write_file({"path": str(b), "content": "BEE\n"}, ctx)
        check(out.startswith("OK:"), "write_file writes the second fixture", out[:100])
        listed = fb.tool_changes({"action": "list"}, ctx)
        check("Files changed by this run (2)" in listed and "a.txt" in listed
              and "b.txt" in listed,
              "changes lists both mutated files", listed[:300])
        diff = fb.tool_changes({"action": "diff", "path": "a.txt"}, ctx)
        check("-two" in diff and "+TWO" in diff and "before-this-run" in diff,
              "changes diff shows before/after lines", diff[:300])
        fb.run_state("s-changes")["done_ok"] = {"check": "prior"}
        out = fb.tool_changes({"action": "restore", "path": "a.txt"}, ctx)
        check(out.startswith("OK: restored"), "changes restores one file", out[:160])
        check(a.read_text(encoding="utf-8") == "one\ntwo\n",
              "the restored file has its pre-run content")
        check(b.read_text(encoding="utf-8") == "BEE\n",
              "the other changed file is untouched")
        check(not fb.run_state("s-changes").get("done_ok"),
              "a restore invalidates a prior verification")
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    # ---------------------------------------------------------------- run_tests
    tests_py = ["import unittest", "", "class T(unittest.TestCase):"]
    for i in range(10):
        tests_py += ["    def test_%02d(self):" % i,
                     "        self.assertEqual(%d, %d)" % (i, i + 1)]
    tests_py += ["", 'if __name__ == "__main__":',
                 "    unittest.main()", ""]
    wd, fb = load({"build_tools": True}, {"test_many.py": "\n".join(tests_py)})
    try:
        fb.AGENT._envelope_cache = {"window": 24000}
        ctx = {"session_key": "s-tests"}
        out = fb.tool_run_tests(
            {"command": '"%s" -m unittest -v test_many' % sys.executable}, ctx)
        check(out.startswith("FAIL exit="), "a failing test command is FAIL", out[:160])
        check(all(("test_%02d" % i) in out for i in range(10)),
              "every failing test name is in the injected summary", out[:400])
        cap = fb.mem_limit_chars("tool_output_max_chars", 10000)
        check(len(out) <= cap, "the injected summary is under the window-derived cap",
              len(out))
        passed = fb.tool_run_tests(
            {"command": py("print('3 passed')")}, ctx)
        check(passed.startswith("PASS exit=0") and passed.count("\n") == 0,
              "a passing run injects ONE line", passed)
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    # ---------------------------------------------------------------- submit_done
    wd, fb = load({"build_tools": True, "done_gate": True})
    try:
        ctx = {"session_key": "s-done"}
        out = fb.tool_submit_done({"check": py("raise SystemExit(1)")}, ctx)
        check(out.startswith("REFUSED") and "exited 1" in out,
              "a failing check is refused with its exit code", out[:200])
        check(not (fb.run_state("s-done") or {}).get("done_ok")
              and (fb.run_state("s-done") or {}).get("done_fail"),
              "a failure clears done_ok and records done_fail")
        out = fb.tool_submit_done({"check": py("print('ok')")}, ctx)
        check(out.startswith("DONE VERIFIED"),
              "a passing check records completion", out[:160])
        check(bool(fb.run_state("s-done").get("done_ok")),
              "done_ok is set for the answer path")
        # Windows PowerShell reports a generic 1 for a nonzero native exit, so the
        # cross-platform shape of this check is "1": the feature under test is that a
        # non-zero expectation is honoured, not that PowerShell preserves 3.
        out = fb.tool_submit_done({"check": py("raise SystemExit(1)"),
                                   "expect_exit": 1}, ctx)
        check(out.startswith("DONE VERIFIED") and "exited 1" in out,
              "expect_exit can require a non-zero code", out[:160])
        check(not fb.tool_submit_done({}, ctx).startswith("DONE"),
              "submit_done without a check is an error, not a pass")
        check(fb._done_gate_reasons("s-readonly", [], {}) == [],
              "a read-only run is never asked for a completion check")
        check(bool(fb._done_gate_reasons(
                  "s-mutated", [("write_file", "x", 1)], {})),
              "...but a run that changed a file is")
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    # ---------------------------------------------------------------- done gate e2e
    wd, fb = load({"build_tools": True, "done_gate": True})
    try:
        out_file = wd / "out.txt"

        def script(i, payload):
            if i == 0:
                return tool_reply("write_file",
                                  {"path": str(out_file), "content": "built\n"})
            if i == 1:
                return text_reply("Done: I built it.")
            if i == 2:
                return tool_reply("submit_done",
                                  {"check": py("print('ok')")}, "c2")
            return text_reply("Done: verified and complete.")

        seen = install_stub(fb, script)
        answer = fb.AGENT.run("s-done-e2e", "build the thing")
        check("verified and complete" in answer,
              "after submit_done passes, the final answer is delivered", answer[:160])
        check(len(seen) >= 4, "the run took the verification turn", len(seen))
        check(any("completion check" in payload_text(p)
                  for p in seen[2:]), "the model was asked to prove completion")
        check(bool(fb.run_state("s-done-e2e").get("done_ok")),
              "the pass is recorded on the run")
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    wd, fb = load({"build_tools": True, "done_gate": True})
    try:
        out_file = wd / "out.txt"

        def script(i, payload):
            if i == 0:
                return tool_reply("write_file",
                                  {"path": str(out_file), "content": "built\n"})
            return text_reply("Everything is finished.")

        install_stub(fb, script)
        answer = fb.AGENT.run("s-done-unverified", "build the thing")
        check("completion not verified" in answer,
              "an answer that never proves completion carries the warning", answer[:240])
        check(not fb.run_state("s-done-unverified").get("done_ok"),
              "...and no pass is recorded")
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    wd, fb = load({"build_tools": True})
    try:
        out_file = wd / "out.txt"

        def script(i, payload):
            if i == 0:
                return tool_reply("write_file",
                                  {"path": str(out_file), "content": "built\n"})
            return text_reply("Done, no gate.")

        seen = install_stub(fb, script)
        answer = fb.AGENT.run("s-no-done-gate", "build the thing")
        check(len(seen) == 2
              and not any("completion check" in payload_text(p) for p in seen)
              and "Done, no gate." in answer,
              "with done_gate off the answer is delivered with no extra turn",
              f"{len(seen)} call(s): {answer[:120]}")
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    # ---------------------------------------------------------------- build_project
    wd, fb = load({"build_tools": True})
    try:
        proj = wd / "proj"
        (proj / "dist").mkdir(parents=True)
        sent = []
        ctx = {"session_key": "s-project",
               "send_file": lambda path, note: sent.append((path, note)) or "sent"}
        (proj / "fake_build.py").write_text(
            "import pathlib\n"
            "pathlib.Path('dist').mkdir(exist_ok=True)\n"
            "pathlib.Path('dist/app.whl').write_text('x')\n", encoding="utf-8")
        command = '"%s" fake_build.py' % sys.executable
        out = fb.tool_build_project({"path": str(proj), "command": command,
                                     "artifact": "dist/*", "send": True}, ctx)
        check("BUILD PASSED" in out and "app.whl" in out,
              "build_project names the artifact", out[:200])
        check(sent and sent[0][0].endswith("app.whl"),
              "build_project hands the artifact to the lane's send_file")
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all build-surface checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
