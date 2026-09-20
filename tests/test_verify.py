"""Offline checks for post-write verification (Phase 1c).

No model calls. The verifier is a pure function of the file on disk, so it can be
pinned exactly: a broken file must be reported broken, a good one reported good, and
an unverifiable type must stay silent rather than inventing a verdict.

    python tests/test_verify.py
"""
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


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbverify-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)

        check(fb.CONFIG["agent"].get("verify_after_write") is True,
              "verification is on by default")

        # ---- python --------------------------------------------------------
        good = workdir / "good.py"
        good.write_text("import os\n\ndef f():\n    return os.getcwd()\n",
                        encoding="utf-8")
        st, why = fb.verify_written_file(good)
        check((st, why) == ("ok", "python syntax OK"), f"valid python -> {st}/{why}")

        bad = workdir / "bad.py"
        bad.write_text("def f(:\n    pass\n", encoding="utf-8")
        st, why = fb.verify_written_file(bad)
        check(st == "fail" and "syntax error" in why, f"broken python -> {st}/{why}")
        check("line 1" in why, "the verdict names the line")

        # a custom tool must survive the loader, not merely look like one: 1e runs
        # the registry's own import over the file, because a static scan of the
        # attribute names disagreed with reality (a NAME that does not match the
        # file name loads under the wrong name and the file name finds nothing).
        tools = workdir / "tools"
        tools.mkdir(exist_ok=True)
        incomplete = tools / "half_tool.py"
        incomplete.write_text("NAME = 'half_tool'\n\ndef run(args, ctx):\n    return 'x'\n",
                              encoding="utf-8")
        st, why = fb.verify_written_file(incomplete)
        check(st == "fail" and "DESCRIPTION" in why and "loader rejects" in why,
              f"a tool the loader rejects is caught -> {why}")

        complete = tools / "full_tool.py"
        complete.write_text(
            "NAME = 'full_tool'\nDESCRIPTION = 'x'\nSCHEMA = {}\n"
            "def run(args, ctx):\n    return 'x'\n", encoding="utf-8")
        st, why = fb.verify_written_file(complete)
        check(st == "ok" and "tool loads as 'full_tool'" in why,
              f"a tool that loads passes -> {why}")

        explodes = tools / "boom_tool.py"
        explodes.write_text("NAME = 'boom_tool'\nDESCRIPTION = 'x'\nSCHEMA = {}\n"
                            "raise RuntimeError('boom at import')\n"
                            "def run(args, ctx):\n    return 'x'\n",
                            encoding="utf-8")
        st, why = fb.verify_written_file(explodes)
        check(st == "fail" and "RuntimeError" in why,
              f"a tool that raises on import is caught -> {why}")

        misnamed = tools / "other_name.py"
        misnamed.write_text("NAME = 'registered_as_this'\nDESCRIPTION = 'x'\n"
                            "SCHEMA = {}\ndef run(args, ctx):\n    return 'x'\n",
                            encoding="utf-8")
        st, why = fb.verify_written_file(misnamed)
        check(st == "fail" and "registers it as 'registered_as_this'" in why,
              f"a NAME that disagrees with the file name is caught -> {why}")

        badschema = tools / "bad_schema.py"
        badschema.write_text("NAME = 'bad_schema'\nDESCRIPTION = 'x'\n"
                             "SCHEMA = 'not an object'\n"
                             "def run(args, ctx):\n    return 'x'\n",
                             encoding="utf-8")
        st, why = fb.verify_written_file(badschema)
        check(st == "fail" and "SCHEMA is not a JSON object" in why,
              f"a schema that is not an object is caught -> {why}")

        # An interpreter that never ran is a skip, never a broken tool.
        real_exe = fb.sys.executable
        try:
            fb.sys.executable = str(workdir / "no-such-python")
            st, why = fb.verify_written_file(complete)
        finally:
            fb.sys.executable = real_exe
        check(st == "skip", f"a probe that cannot run is a skip -> {st}/{why}")

        # ---- json ----------------------------------------------------------
        okj = workdir / "ok.json"
        okj.write_text('{"a": [1, 2]}', encoding="utf-8")
        check(fb.verify_written_file(okj)[0] == "ok", "valid JSON passes")
        badj = workdir / "bad.json"
        badj.write_text('{"a": 1', encoding="utf-8")
        st, why = fb.verify_written_file(badj)
        check(st == "fail" and "invalid JSON" in why, f"unclosed JSON fails -> {why}")

        # 1e: config.json gets the verdict the startup path would give it, from the
        # two refusals that are pure data in the file.
        cfg = workdir / "config.json"
        cfg.write_text('{"mattermost": {"allowed_users": ["abc"]}, "llm": {"model": "main"}}',
                       encoding="utf-8")
        st, why = fb.verify_written_file(cfg)
        check(st == "ok" and "starts on" in why, f"a usable config passes -> {why}")
        cfg.write_text('{"mattermost": {"allowed_users": []}}', encoding="utf-8")
        st, why = fb.verify_written_file(cfg)
        check(st == "fail" and "allowed_users" in why,
              f"an empty allowlist is caught -> {why}")
        cfg.write_text('{"mattermost": {"url": "change-me"}}', encoding="utf-8")
        st, why = fb.verify_written_file(cfg)
        check(st == "fail" and "placeholder" in why,
              f"a placeholder url is caught -> {why}")
        # Absence is not a problem: this file is an overlay on the shipped defaults.
        cfg.write_text('{"llm": {"model": "main"}}', encoding="utf-8")
        st, why = fb.verify_written_file(cfg)
        check(st == "ok", f"a partial overlay passes -> {st}/{why}")

        # ---- toml / yaml ---------------------------------------------------
        okt = workdir / "ok.toml"
        okt.write_text('name = "x"\n[svc]\nport = 8082\n', encoding="utf-8")
        st, why = fb.verify_written_file(okt)
        check(st in ("ok", "skip"), f"toml is handled or skipped honestly -> {st}/{why}")
        badt = workdir / "bad.toml"
        badt.write_text('name = "x\n', encoding="utf-8")
        st, why = fb.verify_written_file(badt)
        check(st in ("fail", "skip"), f"broken toml -> {st}/{why}")

        oky = workdir / "ok.yaml"
        oky.write_text("a: 1\nb:\n  - 2\n", encoding="utf-8")
        st, why = fb.verify_written_file(oky)
        check(st in ("ok", "skip"), f"yaml is handled or skipped honestly -> {st}/{why}")

        # ---- shells --------------------------------------------------------
        oks = workdir / "ok.sh"
        oks.write_text("#!/bin/bash\nset -e\necho hi\n", encoding="utf-8")
        st, why = fb.verify_written_file(oks)
        check(st in ("ok", "skip"),
              f"a good shell script is never reported broken -> {st}/{why}")
        bads = workdir / "bad.sh"
        bads.write_text("if true; then\n  echo hi\n", encoding="utf-8")
        st, why = fb.verify_written_file(bads)
        check(st in ("fail", "skip"),
              f"an unclosed shell script is never reported ok by accident -> {st}/{why}")

        # ---- silence where there is nothing to say -------------------------
        md = workdir / "notes-something.md"
        md.write_text("# hello\n", encoding="utf-8")
        check(fb.verify_note(md) == "", "an unverifiable type adds nothing to the result")
        check("verify" not in fb.verify_note(md), "no empty verdict line is emitted")

        # ---- the note that reaches the model -------------------------------
        note = fb.verify_note(badj)
        check(note.startswith("\n[HARNESS verify FAILED:"), "a failure is announced")
        check("fix it before reporting" in note, "the failure says what to do next")
        check(fb.verify_note(okj).startswith("\n[HARNESS verify: valid JSON"),
              "a pass is announced briefly")

        # ---- never raises --------------------------------------------------
        missing = workdir / "nope.json"
        check(fb.verify_written_file(missing)[0] == "skip",
              "a path that does not exist skips instead of raising")
        weird = workdir / "weird.json"
        weird.write_bytes(b"\x00\x01\x02binary")
        check(fb.verify_written_file(weird)[0] == "skip",
              "a binary file skips instead of raising")

        # ---- the hook is wired into the tools ------------------------------
        out = fb.tool_write_file({"path": str(workdir / "w.py"),
                                  "content": "x = 1\n"}, {})
        check("[HARNESS verify: python syntax OK]" in out,
              "write_file appends the verdict")
        out = fb.tool_write_file({"path": str(workdir / "w2.json"),
                                  "content": '{"a": 1'}, {})
        check("[HARNESS verify FAILED:" in out,
              "write_file reports a broken file in the same result")
        replaced = workdir / "w3.py"
        replaced.write_text("a = 1\n", encoding="utf-8")
        out = fb.tool_edit_file({"path": str(replaced), "old_string": "a = 1",
                                 "new_string": "a = 1\nb = ("}, {})
        check("[HARNESS verify FAILED:" in out,
              "edit_file reports a file it just broke")

        # ---- edit recovery and feedback (2026-09-19) ------------------------
        # A near-miss anchor (LF where the file is CRLF, indentation drift) used to fail
        # outright, and a successful edit said nothing about WHAT changed. On the fleet
        # manager that reads as a stupid agent; both are capabilities, not intelligence.
        CRLF = chr(13) + chr(10)
        LF = chr(10)
        crlf_file = workdir / "crlf_edit.py"
        body = CRLF.join(["def a():", "    return 1", "", "def b():", "    return 2", ""])
        crlf_file.write_bytes(body.encode())
        out = fb.tool_edit_file({"path": str(crlf_file),
                                 "old_string": "def b():" + LF + "    return 2",
                                 "new_string": "def b():" + LF + "    return 42"}, {})
        check("edit_file applies an LF anchor to a CRLF file",
              out.startswith("OK:") and "return 42" in crlf_file.read_text())
        check("edit_file names the strategy that matched", "[strategy:" in out)
        check("edit_file returns a diff of what it changed",
              "--- diff ---" in out and "+    return 42" in out)
        raw_now = crlf_file.read_bytes()
        CR = chr(13).encode()
        LFb = chr(10).encode()
        check("the file's CRLF convention survives the edit",
              (CR + LFb) in raw_now and LFb not in raw_now.replace(CR + LFb, b""))
        amb = workdir / "amb.py"
        amb.write_text("def b():" + LF + "    return 1" + LF + LF
                       + "def b():" + LF + "    return 2" + LF, encoding="utf-8")
        out = fb.tool_edit_file({"path": str(amb), "old_string": "def b():",
                                 "new_string": "def c"}, {})
        check("an ambiguous anchor is refused, with the candidate count",
              out.startswith("ERROR") and "ambiguous" in out)
        out = fb.tool_edit_file({"path": str(amb), "old_string": "def zzz():",
                                 "new_string": "x"}, {})
        check("a missing anchor is refused, with guidance",
              out.startswith("ERROR") and "not found" in out)

        # ---- shell writes are verified too ---------------------------------
        # Measured in the eval: asked to create a JSON file, this model used shell
        # redirection rather than write_file, so a verifier wired only to the write
        # tools never ran. That is the common path, not the exception.
        for cmd, want in (
                (f"Set-Content -Path {workdir / 's1.json'} -Value '{{\"a\": 1'", "s1.json"),
                (f"echo '{{\"b\": 2}}' > {workdir / 's2.json'}", "s2.json"),
                (f"printf 'x' | tee {workdir / 's3.txt'}", "s3.txt"),
        ):
            got = fb.shell_written_files(cmd)
            check(any(want in g for g in got),
                  f"shell write detected in {cmd.split()[0]!r} -> {got}")
        check(fb.shell_written_files("ps -ef | grep x") == [],
              "a read-only command yields no write candidates")
        check(fb.shell_written_files("echo hi 2>&1") == [],
              "a redirection of a descriptor is not treated as a file write")

        # The write has to be a command the HOST UNDER TEST actually has: Set-Content is
        # PowerShell, and a Linux host runs bash, so the older form wrote nothing there and
        # both checks failed for the wrong reason (found by running this suite from the
        # shipped archive on a Linux box). POSIX redirection is the same category of write,
        # so whichever form exists on the host is exercised, and the POSIX extraction path
        # gains coverage it never had.
        POSIX = os.name != "nt"

        def write_cmd(path, body):
            if POSIX:
                return "printf '%s' '" + body + "' > '" + str(path) + "'"
            return "Set-Content -Path '" + str(path) + "' -Value '" + body + "'"

        shell_bad = workdir / "shell_bad.json"
        out = fb.tool_shell({"command": write_cmd(shell_bad, '{"a": 1')}, {})
        check("[HARNESS verify FAILED:" in out,
              "a broken file written via shell is reported broken")
        shell_ok = workdir / "shell_ok.json"
        out = fb.tool_shell({"command": write_cmd(shell_ok, '{"a": 1}')}, {})
        check("[HARNESS verify: valid JSON]" in out,
              "a good file written via shell gets its verdict too")

        # ---- off switch ----------------------------------------------------
        fb.CONFIG["agent"]["verify_after_write"] = False
        check(fb.verify_note(badj) == "", "verify_after_write=false silences it")
        check(fb.verify_shell_writes(
            f"Set-Content -Path '{shell_bad}' -Value x") == "",
            "the shell path honours the same off switch")
        fb.CONFIG["agent"]["verify_after_write"] = True
        check(fb.verify_note(badj) != "", "turning it back on restores the verdict")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all post-write verification checks passed")


if __name__ == "__main__":
    main()
