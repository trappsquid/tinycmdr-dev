"""test_host_surface - one merged suite (test_host_file_materialize, test_env_names, test_atlas).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: no member needed a namespace rewrite.
"""
import os
import sys


def _run(name, fn):
    """One member, its own snapshot: env, cwd and sys.path restored afterwards."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_path = list(sys.path)
    print("== member %s: start" % name)
    try:
        rc = fn()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
        sys.path[:] = saved_path
    rc = int(rc or 0)
    print("== member %s: exit %d" % (name, rc))
    return rc


def _suite_test_host_file_materialize():
    """Host files materialize from their shipped defaults on start.

theme.toml and soul.md are host state: a checkout or an install that lacks them gets a
copy of theme.default.toml / soul.example.md when the app starts, and an existing
(customized) file is never touched.

    python tests/test_host_surface.py
"""
    import importlib.util
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent

    FAILS = []


    def check(what, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
        if not ok:
            FAILS.append(what)


    def stage(work):
        for name in ("tinycmdr.py", "theme.default.toml", "soul.example.md"):
            shutil.copy2(BASE / name, work / name)
        shutil.copy2(BASE / "tests" / "fixture-config.json", work / "config.json")


    def load(work):
        # The runner exports TINYCMDR_NO_MATERIALIZE=1 for every suite (a suite that imports
        # the app must not create host state in the checkout). THIS suite grades exactly that
        # materialization, so it clears the guard before loading.
        os.environ.pop("TINYCMDR_NO_MATERIALIZE", None)
        spec = importlib.util.spec_from_file_location("tinycmdr_materialize",
                                                      work / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["tinycmdr_materialize"] = mod
        spec.loader.exec_module(mod)
        return mod


    def main():
        work = Path(tempfile.mkdtemp(prefix="fbmat-"))
        try:
            # fresh tree: both live files appear, byte-identical to their defaults
            stage(work)
            load(work)
            check("a fresh tree gets theme.toml", (work / "theme.toml").exists())
            check("...byte-identical to theme.default.toml",
                  (work / "theme.toml").read_bytes()
                  == (work / "theme.default.toml").read_bytes())
            check("a fresh tree gets soul.md", (work / "soul.md").exists())
            check("...byte-identical to soul.example.md",
                  (work / "soul.md").read_bytes() == (work / "soul.example.md").read_bytes())

            # the runner's guard (TINYCMDR_NO_MATERIALIZE=1): an import under the gate must
            # create neither file, which is what keeps a suite from writing host state into a
            # checkout (test_checkin's import did exactly that once both files were untracked)
            work0 = Path(tempfile.mkdtemp(prefix="fbmat0-"))
            try:
                stage(work0)
                os.environ["TINYCMDR_NO_MATERIALIZE"] = "1"
                try:
                    # A RAW import, not load(): load() clears the guard on purpose (the other
                    # cases grade the materialization the guard suppresses).
                    spec = importlib.util.spec_from_file_location(
                        "tinycmdr_mat_guard", work0 / "tinycmdr.py")
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules["tinycmdr_mat_guard"] = mod
                    spec.loader.exec_module(mod)
                finally:
                    os.environ.pop("TINYCMDR_NO_MATERIALIZE", None)
                check("with the runner's guard set, neither host file is created",
                      not (work0 / "theme.toml").exists() and not (work0 / "soul.md").exists())
            finally:
                shutil.rmtree(work0, ignore_errors=True)

            # an existing file is the host's: a start never touches it
            work2 = Path(tempfile.mkdtemp(prefix="fbmat2-"))
            try:
                stage(work2)
                (work2 / "theme.toml").write_text("# mine\n", encoding="utf-8")
                (work2 / "soul.md").write_text("custom persona\n", encoding="utf-8")
                load(work2)
                check("a customized theme.toml is untouched",
                      (work2 / "theme.toml").read_text(encoding="utf-8") == "# mine\n")
                check("a customized soul.md is untouched",
                      (work2 / "soul.md").read_text(encoding="utf-8") == "custom persona\n")
            finally:
                shutil.rmtree(work2, ignore_errors=True)

            # no shipped default: nothing is invented, and the start does not fail
            work3 = Path(tempfile.mkdtemp(prefix="fbmat3-"))
            try:
                stage(work3)
                (work3 / "theme.default.toml").unlink()
                (work3 / "soul.example.md").unlink()
                load(work3)
                check("with no shipped default, no live file is invented",
                      not (work3 / "theme.toml").exists()
                      and not (work3 / "soul.md").exists())
            finally:
                shutil.rmtree(work3, ignore_errors=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        print("\n%s" % ("all materialize checks passed" if not FAILS
                        else "FAILED: %d" % len(FAILS)))
        return 1 if FAILS else 0
    return main()


def _suite_test_env_names():
    """The names the app WRITES into .env must be the names it READS out of it.

Review H3. tinycmdr.py's setup wizard wrote `MATTERMOST_BOT_TOKEN`, `TELEGRAM_TOKEN` and
`LLM_API_KEY`; the loader's `env_map` reads none of them. A box configured through that
wizard therefore came up with a token nothing consumed - and because the loader then sees
no token at all, the symptom is the one the code already warns about for a missing token:
"the bot never connects". Measured on the author's own install 2026-09-26: `.env` carried
`MATTERMOST_BOT_TOKEN` beside the `TINYCMDR_MM_TOKEN` that was actually doing the work.

These checks are static: they read the shipped text, so they hold with no config, no
network and no interpreter band. `python tests/test_host_surface.py`
"""
    import os
    import pathlib
    import re
    import sys

    BASE = pathlib.Path(__file__).resolve().parent.parent
    # TINYCMDR_SRC lets a falsification run point this at a reverted copy (tests/run_all.py
    # clears it, so the gate always grades the real file).
    SRC_PATH = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    SRC = SRC_PATH.read_text(encoding="utf-8", errors="replace")
    EXAMPLE = (BASE / ".env.example").read_text(encoding="utf-8", errors="replace")
    FAILS = []

    # The names that were written and never read, kept here so a regression names the bug.
    LEGACY_WRITES = ("MATTERMOST_BOT_TOKEN", "TELEGRAM_TOKEN", "LLM_API_KEY")


    def check(name, cond, detail=""):
        if cond:
            print(f"ok   {name}")
        else:
            FAILS.append(name)
            print(f"FAIL {name}: {detail}")


    def env_map_names():
        """Every name the loader reads out of the environment, from its own env_map table."""
        body = re.search(r"env_map = \{(.*?)\n    \}", SRC, re.S)
        return set(re.findall(r'"([A-Z0-9_]+)":\s*\(', body.group(1))) if body else set()


    def written_names():
        """Every name the app writes into .env: _env_set(), and _env_set_safe() which reports a
    value it must refuse instead of raising at the operator's prompt."""
        return set(re.findall(r'_env_set(?:_safe)?\("([A-Z0-9_]+)"', SRC))


    def main():
        reads = env_map_names()
        writes = written_names()
        check("the loader's env_map was found", bool(reads), "no env_map in tinycmdr.py")
        check("the app writes at least one .env name", bool(writes),
              "no _env_set calls found - the wizard moved?")

        missing = sorted(w for w in writes if w not in reads)
        check("every name the app WRITES is a name it READS",
              not missing,
              f"written but never read: {missing}")

        still = sorted(n for n in LEGACY_WRITES if n in writes)
        check("the three names nothing read are gone", not still,
              f"still written: {still}")

        # A reader has to be able to find the name somewhere: env_map is the contract, the
        # example file is the documentation of it.
        undocumented = sorted(n for n in reads if n not in EXAMPLE)
        check("every name the loader READS is in .env.example", not undocumented,
              f"not documented: {undocumented}")

        # The secrets the installers write must stay in the set (each has exactly one home).
        for name in ("TINYCMDR_MM_TOKEN", "TINYCMDR_TG_TOKEN",
                     "TINYCMDR_LLM_API_KEY"):
            check(f"the loader reads {name}", name in reads, "not in env_map")

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) failed:")
            for f in FAILS:
                print(f"  - {f}")
            return 1
        print("all env-name checks passed")
        return 0
    return main()


def _suite_test_atlas():
    """Checks for the machine atlas.

Two kinds here. The unit ones pin the format, the bound and the off switch. The last two
drive a real turn against a stubbed model, because "the atlas is in the first request of a
run" and "it comes back after a wrong-path failure" are claims about the REQUEST BODY, and
only an end-to-end run can prove those.

    python tests/test_host_surface.py
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


    def check(cond, what):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}")
        else:
            print(f"ok   {what}")


    class FakeResp:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._data


    def text_reply(text):
        return {"choices": [{"message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


    def tool_call_reply(name, args, cid="c1"):
        return {"choices": [{"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": cid, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}}]},
            "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


    def install_stub(fb, seen, script):
        state = {"i": 0}

        def fake_post(url, headers, payload, timeout, grace, cancel_event=None, stream=False):
            seen.append(json.loads(json.dumps(payload)))
            i = state["i"]
            state["i"] += 1
            return FakeResp(script(i, payload))

        fb._post_watchdog = fake_post


    def payload_blob(payload):
        return "\n".join(str(m.get("content") or "") for m in payload.get("messages", []))


    SAMPLE = """# Machine atlas - testbox

## host
- host: testbox
- os: Linux 6.8 (x86_64)
- shell used by the shell tool: bash
- install: /opt/tinycmdr

## layout
- tinycmdr.py  the agent itself
- data/  (directory)
- tools/  custom tools, hot-loaded

## notes
- the model endpoint is on the LAN, not this box
"""


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbatlas-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)

            check(fb.CONFIG["agent"].get("atlas_enabled") is True,
                  "the atlas is on by default")
            check(fb.CONFIG["agent"].get("atlas_max_chars") == 2400,
                  "and bounded by atlas_max_chars")

            # ---- format ---------------------------------------------------------
            d = fb.parse_atlas(SAMPLE)
            check([k for k, _ in d["host"]] == ["host", "os", "shell used by the shell tool",
                                                "install"],
                  f"host keys parse in order ({[k for k, _ in d['host']]})")
            check(len(d["layout"]) == 3 and d["layout"][1][0] == "data/",
                  "layout entries parse, including directories")
            check(d["notes"] == ["the model endpoint is on the LAN, not this box"],
                  "notes parse as bullets")
            check(fb.parse_atlas("") == {"host": [], "layout": [], "notes": []},
                  "an empty file is not a crash")
            check(fb.parse_atlas("junk\n### also junk\n- orphan bullet")["notes"] == [],
                  "text outside a known section is ignored")
            check(fb.parse_atlas("## host\n- key with no value:\n- fine: yes")["host"]
                  == [("fine", "yes")], "a valueless line is skipped, not rendered as empty")

            # ---- render --------------------------------------------------------
            (workdir / "atlas.md").write_text(SAMPLE, encoding="utf-8")
            fb._ATLAS_CACHE["mtime"] = None
            out = fb.render_atlas()
            check(out.startswith("Machine atlas (") and "do not have to discover or remember" in out,
                  "the rendered block says what it is")
            check("install: /opt/tinycmdr" in out and "data/" in out and "note: the model" in out,
                  "host, layout and notes all reach the text")
            fb.CONFIG["agent"]["atlas_max_chars"] = 80
            small = fb.render_atlas()
            # The bound exists to keep the block small, and what it trims is the directory
            # listing. The host facts and the curated notes are why the block exists at all, so
            # they survive any cap.
            check("install: /opt/tinycmdr" in small and "note: the model endpoint" in small,
                  f"a small cap still carries the facts and the curated notes ({len(small)} chars)")
            check("more entries in atlas.md" in small,
                  "and it is the listing that gets trimmed, with the remainder named")
            fb.CONFIG["agent"]["atlas_max_chars"] = 2400
            fb.CONFIG["agent"]["atlas_enabled"] = False
            check(fb.render_atlas() == "", "atlas_enabled=false silences it entirely")
            fb.CONFIG["agent"]["atlas_enabled"] = True
            (workdir / "atlas.md").unlink()
            fb._ATLAS_CACHE["mtime"] = None
            check(fb.render_atlas() == "", "no file, nothing rendered")

            # ---- what counts as a wrong-path failure ----------------------------
            for text in ("ls: cannot access 'x': No such file or directory",
                         "Get-Content : Cannot find path 'C:\\nope' because it does not exist.",
                         "The system cannot find the file specified.",
                         "zztool: command not found"):
                check(fb.looks_like_path_failure(text), f"path failure recognised: {text[:40]!r}")
            for text in ("SyntaxError: invalid syntax", "0 rows returned", "",
                         "edit_file: wrote 12 lines"):
                check(not fb.looks_like_path_failure(text),
                      f"and not invented from: {text[:34]!r}")

            # ---- the re-ask fires on a FAILURE, not on a mention (review 2026-09-29) ---------
            # It ran on EVERY result, so a successful read of a tutorial, README or captured log
            # containing "no such file or directory" re-attached the whole machine map - around
            # 2000 characters of prompt, every turn after it - and framed a call that worked as a
            # wrong-path problem.
            doc = workdir / "tutorial.md"
            doc.write_text("If you see 'no such file or directory', the path was wrong.\n",
                           encoding="utf-8")
            fb.AGENT._exec_tool({"function": {"name": "read_file",
                                              "arguments": json.dumps({"path": str(doc)})}},
                                {"session_key": "atlas-sess"})
            check(not (fb.run_state("atlas-sess") or {}).get("atlas_reask"),
                  "a SUCCESSFUL read that merely mentions a path error does not re-ask for the map")
            fb.AGENT._exec_tool({"function": {"name": "shell",
                                              "arguments": json.dumps(
                                                  {"command": "cat /definitely/not/here"})}},
                                {"session_key": "atlas-sess"})
            check((fb.run_state("atlas-sess") or {}).get("atlas_reask") is True,
                  "  while a real missing-path FAILURE still does")

            # ---- shell rights: said up front, once ---------------------------------
            check(isinstance(fb.shell_rights_line(), str),
                  "shell_rights_line() renders on this host (elevated or not)")
            check(isinstance(fb.is_elevated(), (bool, type(None))),
                  "is_elevated() answers a bool, or None when it cannot tell")
            seen_shell = []
            install_stub(fb, seen_shell, lambda i, p: tool_call_reply("shell", {"command": "echo hi"})
                         if i == 0 else text_reply("done"))
            fb.AGENT.run("sh1", "say something short")
            first_shell = payload_blob(seen_shell[0])
            check(fb.shell_rights_line() in first_shell,
                  "the process's rights are stated in the first request of a run")
            check(fb.shell_rights_line() not in payload_blob(seen_shell[-1]),
                  "and not re-stated on every later turn")
            fb.CONFIG["agent"]["shell_facts"] = False
            check(fb.shell_rights_line() == "", "shell_facts=false silences it")
            seen_off = []
            install_stub(fb, seen_off, lambda i, p: text_reply("ok"))
            fb.AGENT.run("sh2", "hello")
            # Not "Shell:" as a substring: the system prompt has its own "- Shell: each call is a
            # fresh PowerShell" line, so that assertion would fail for the wrong reason. Assert the
            # distinctive phrasing of the rights line instead.
            marker = "elevated" if "elevated" in fb.shell_rights_line() else "not root"
            check(marker not in payload_blob(seen_off[0]),
                  f"and with it off the request carries no rights line at all ({marker!r})")
            fb.CONFIG["agent"]["shell_facts"] = True

            # ---- the draft generator -------------------------------------------
            draft_path = workdir / "atlas_draft.md"
            wrote = fb.ensure_atlas(draft_path)
            check(wrote is True, "a missing atlas gets a draft")
            body = draft_path.read_text(encoding="utf-8")
            check("DRAFT" in body and "## host" in body and "## layout" in body
                  and "## notes" in body, "the draft carries all three sections")
            check("install:" in body and "shell used by the shell tool:" in body,
                  "the draft states the box and the shell")
            check(draft_path.read_text(encoding="utf-8") == body and fb.ensure_atlas(draft_path) is False,
                  "it never overwrites an existing atlas")
            d2 = fb.parse_atlas(body)
            check(bool(d2["host"]) and bool(d2["layout"]), "and the draft parses back")

            # ---- end to end: the atlas is in the FIRST request of a run ---------
            workdir.joinpath("atlas.md").write_text(SAMPLE, encoding="utf-8")
            fb._ATLAS_CACHE["mtime"] = None
            seen = []
            install_stub(fb, seen, lambda i, p: tool_call_reply("shell", {"command": "echo one"})
                         if i == 0 else text_reply("done"))
            fb.AGENT.run("a1", "say something short")
            first, later = payload_blob(seen[0]), payload_blob(seen[-1])
            check("install: /opt/tinycmdr" in first,
                  "the atlas rides the first request of a run")
            check("install: /opt/tinycmdr" not in later,
                  "and is not re-sent on every later turn")

            # ---- and it comes back after a wrong-path failure --------------------
            seen2 = []
            # Turn order matters here: a SUCCESSFUL call first, so turn 2 proves the atlas is
            # not simply re-sent, and only then the wrong-path failure.
            script = [tool_call_reply("shell", {"command": "echo one"}),
                      tool_call_reply("shell", {"command": "cat no_such_dir/thing.txt"}, cid="c2"),
                      tool_call_reply("shell", {"command": "pwd"}, cid="c3"),
                      text_reply("done")]
            install_stub(fb, seen2, lambda i, p: script[min(i, len(script) - 1)])
            fb.AGENT.run("a2", "read that file")
            blobs = [payload_blob(p) for p in seen2]
            check(len(blobs) >= 3, f"the stub produced enough turns ({len(blobs)})")
            check("install: /opt/tinycmdr" not in blobs[1],
                  "the second turn has no atlas (unlike the first)")
            check("install: /opt/tinycmdr" in blobs[2],
                  "a wrong-path failure brings the atlas back on the next turn")

            # ---- a known file that is not in the atlas is advertised anyway --------
            # Measured reason (2026-09-17): a host-generated file can be written a fraction of a
            # second AFTER the atlas draft, and render_atlas used to trust the FILE's listing, so
            # such a file could never announce itself - not on a fresh install, and not on an
            # atlas that already exists, which is every host that has been up before. The layout
            # is therefore rebuilt from what is on disk, not only from what the file said.
            atlas_path = workdir / "atlas.md"
            atlas_path.write_text(
                "# Machine atlas - test\n\n## host\n- host: t\n\n## layout\n"
                "- tinycmdr.py  the agent itself\n- atlas.md  this file\n\n"
                "## notes\n- a curated note that must survive\n", encoding="utf-8")
            fb._ATLAS_CACHE["mtime"] = None
            merged = fb.render_atlas()
            check("config.json" in merged,
                  "a known file that exists but is missing from the atlas is listed anyway")
            check("tinycmdr.py" in merged and "a curated note that must survive" in merged,
                  "  and the atlas's own rows and notes are untouched")
            atlas_path.unlink()

            # ---- off switch end to end ------------------------------------------
            fb.CONFIG["agent"]["atlas_enabled"] = False
            seen3 = []
            install_stub(fb, seen3, lambda i, p: text_reply("ok"))
            fb.AGENT.run("a3", "hello")
            check("Machine atlas" not in payload_blob(seen3[0]),
                  "atlas_enabled=false keeps it out of the request")
            fb.CONFIG["agent"]["atlas_enabled"] = True
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all atlas checks passed'}")
        for f in FAILS:
            print("   -", f)
        return 1 if FAILS else 0
    return main()


def main():
    rc = 0
    for name, fn in (("test_host_file_materialize", _suite_test_host_file_materialize), ("test_env_names", _suite_test_env_names), ("test_atlas", _suite_test_atlas)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
