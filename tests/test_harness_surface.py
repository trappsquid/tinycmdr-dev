"""test_harness_surface - one merged suite (test_harness_extras, test_harness_refinements, test_verify, test_run_all).

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


def _suite_test_harness_extras():
    """The small-half integrations: text tool calls, conflicts, note policy, receipts.

Each check names the mechanism it pins, and every one of them is a shape a weak model
produces on a real box.

    python tests/test_harness_surface.py
"""
    import json
    import os
    import shutil
    import sys
    import tempfile
    import time
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
        workdir = Path(tempfile.mkdtemp(prefix="fbextras-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            A = fb.AGENT
            ctx = {"session_key": "extras"}

            # ---------------------------------------------------------- text tool calls
            check(bool(fb._TEXT_CALL_RX.search('```json\n{"command": "ls"}\n```')),
                  "a fenced json call is detected")
            check(bool(fb._TEXT_CALL_RX.search("print(default_api.shell(command='ls'))")),
                  "the default_api transcription is detected")
            check(not fb._TEXT_CALL_RX.search("I will check the logs now."),
                  "ordinary prose is not")
            check("# In this prompt: NEVER = do not" in fb.build_system_prompt()
                  or "NEVER = do not" in fb.build_system_prompt(),
                  "the prompt defines its own vocabulary")

            # ---------------------------------------------------------- tool paths are install paths
            # A-2026-10-08-105: the shell runs in BASE_DIR, so a RELATIVE tool path has to
            # mean the install's file. It used to resolve against the PROCESS cwd - wherever
            # the launcher was exec'd from - so `write_file report.md` and the next
            # `shell` call disagreed about the same spelling. Pre-fix these land in cwd;
            # both spots are cleaned up below either way.
            relname = "relcheck-extras.txt"
            inst_copy = Path(fb.BASE_DIR) / relname
            cwd_copy = Path.cwd() / relname

            def _text(p):
                try:
                    return p.read_text(encoding="utf-8")
                except OSError:
                    return ""
            out = fb.tool_write_file({"path": relname, "content": "installed here\n"},
                                     dict(ctx))
            check(out.startswith("OK") and _text(inst_copy) == "installed here\n",
                  "a relative write_file path lands in the install dir", out[:140])
            check(not cwd_copy.exists(), "...not beside the process cwd", str(cwd_copy))
            back = fb.tool_read_file({"path": relname}, dict(ctx))
            check("installed here" in back,
                  "a relative read_file path reads it back", back[:140])
            out = fb.tool_edit_file({"path": relname, "old_string": "installed",
                                     "new_string": "INSTALLED"}, dict(ctx))
            check(out.startswith("OK") and "INSTALLED" in _text(inst_copy),
                  "a relative edit_file path edits the install's file", out[:140])
            for leftover in (inst_copy, cwd_copy):
                try:
                    if leftover.exists():
                        leftover.unlink()
                except OSError:
                    pass

            # ---------------------------------------------------------- write_file: atomic, backs off
            # A-2026-10-08-107: the old write read the whole file into RAM for the backup,
            # replaced whatever sat at <name>.bak (an operator's copy included), and truncated
            # the destination in place.
            tf = Path(fb.BASE_DIR) / "wfile-target.txt"
            tf.write_text("version one\n", encoding="utf-8")
            tf_bak = Path(str(tf) + ".bak")
            tf_bak.write_text("the operator's own copy\n", encoding="utf-8")
            holder = open(tf, "rb") if os.name != "nt" else None
            out = fb.tool_write_file({"path": str(tf), "content": "version two\n"},
                                     dict(ctx))
            check(_text(tf_bak) == "the operator's own copy\n",
                  "write_file keeps a .bak the operator owns", out[:160])
            tf_bak1 = Path(str(tf) + ".bak.1")
            check(_text(tf_bak1) == "version one\n" and ".bak.1" in out,
                  "...the previous content goes to .bak.1, named in the result", out[:220])
            check(_text(tf) == "version two\n", "the destination carries the new content")
            if holder is not None:
                seen = holder.read()
                holder.close()
                check(seen == b"version one\n",
                      "the replace is atomic: a reader that opened the old file still sees it",
                      seen[:60])
            else:
                print("note  no pre-write reader check on Windows")
            for leftover in (tf, tf_bak, tf_bak1):
                try:
                    leftover.unlink()
                except OSError:
                    pass

            import socket as _socket
            if os.name != "nt" and hasattr(_socket, "AF_UNIX"):
                sock = Path(fb.BASE_DIR) / "wfile-sock"
                _s = _socket.socket(_socket.AF_UNIX)
                _s.bind(str(sock))
                out = fb.tool_write_file({"path": str(sock), "content": "x"}, dict(ctx))
                check(out.startswith("ERROR:") and "not a regular file" in out,
                      "write_file refuses a non-regular target", out[:160])
                sock.unlink()
            else:
                print("note  no non-regular-target check on this platform")

            # ---------------------------------------------------------- read miss with a sibling
            # Operator report (2026-10-03): `read_file .../notes` got the tool lecture while
            # `notes.md` sat next to it — the model wanted the FILE and was sent elsewhere.
            sib = workdir / "notes.md"
            sib.write_text("durable fact\n", encoding="utf-8")
            miss = fb.tool_read_file({"path": str(workdir / "notes")}, dict(ctx))
            check(miss.startswith("ERROR:") and str(sib) in miss and "Did you mean" in miss,
                  "a missing file names the sibling that exists", miss[:220])
            check("TOOL on this box" not in miss,
                  "...and does not lecture about a tool: on this build the name is a file",
                  miss[:220])
            plain = fb.tool_read_file({"path": str(workdir / "absent_xyz")}, dict(ctx))
            check(plain == "ERROR: %s does not exist." % (workdir / "absent_xyz"),
                  "a plain miss stays plain", plain[:140])

            # ---------------------------------------------------------- merge conflicts
            conflict = ["a", "<<<<<<< HEAD", "ours", "=======", "theirs",
                        ">>>>>>> branch", "b"]
            check(fb._merge_conflict_span(conflict) == (2, 6),
                  "a well-formed conflict block is found",
                  fb._merge_conflict_span(conflict))
            check(fb._merge_conflict_span(["a", "<<<<<<< HEAD", "ours", "======="]) is None,
                  "an unterminated block is not")
            check(fb._merge_conflict_span([" <<<<<<< HEAD", "x", "=======", ">>>>>>> y"]) is None,
                  "an indented marker is not")
            cfile = workdir / "conflicted.conf"
            cfile.write_text("a = 1\n<<<<<<< HEAD\nx = 2\n=======\nx = 3\n>>>>>>> other\n",
                             encoding="utf-8")
            out = fb.tool_read_file({"path": str(cfile)}, dict(ctx))
            check("unresolved merge conflict" in out and "do NOT run it" in out,
                  "a read names the conflict", out[-220:])

            # ---------------------------------------------------------- field-note policy
            notes = workdir / "field-notes.md"
            notes.write_text("## flaky DNS\nmatch: no such host, name resolution\n"
                             "scope: any\nnote: the LAN resolver blipped; retry once.\n"
                             "repeat: once\n", encoding="utf-8")
            fb.CONFIG["agent"]["field_notes_file"] = str(notes)
            fb._FIELD_NOTES_CACHE["mtime"] = None
            hits = fb._field_notes_stats_path()
            hits.parent.mkdir(parents=True, exist_ok=True)

            def stats(entries=None, unmatched=None):
                hits.write_text(json.dumps({"entries": entries or {},
                                            "unmatched": unmatched or {}}),
                                encoding="utf-8")

            stats()
            got = fb.match_field_notes("ERROR: no such host: dns.lan")
            check(len(got) == 1 and got[0]["title"] == "flaky DNS",
                  "a matching note fires the first time", got)
            stats({"flaky DNS": {"fired": 3, "last": "2026-10-01 10:00:00"}})
            check(fb.match_field_notes("ERROR: no such host: dns.lan") == [],
                  "`repeat: once` stays quiet after it has fired")

            # A renamed or deleted entry must not leave a ghost tally - the
            # counters are keyed by title, and doctor's "never fired" list is a claim about the
            # LIVE library.
            notes.write_text("## renamed DNS\nmatch: no such host, name resolution\n"
                             "scope: any\nnote: x.\nrepeat: once\n", encoding="utf-8")
            fb._FIELD_NOTES_CACHE["mtime"] = None
            stats({"flaky DNS": {"fired": 7, "last": "2026-10-01 10:00:00"},
                   "renamed DNS": {"fired": 1, "last": "2026-10-02 10:00:00"}})
            fb._field_notes_record([{"title": "renamed DNS"}], "")
            data = json.loads(hits.read_text(encoding="utf-8"))
            check("flaky DNS" not in (data.get("entries") or {}),
                  "a renamed entry's ghost tally is retired", data.get("entries"))
            check((data.get("entries") or {}).get("renamed DNS", {}).get("fired") == 2,
                  "...while the live entry's tally survives and counts", data.get("entries"))
            # ...and an absent library (temporarily unmounted, path typo) must never wipe it.
            fb.CONFIG["agent"]["field_notes_file"] = str(workdir / "not-there.md")
            fb._FIELD_NOTES_CACHE["mtime"] = None
            stats({"renamed DNS": {"fired": 5, "last": "x"}})
            fb._field_notes_record([], "")
            data = json.loads(hits.read_text(encoding="utf-8"))
            check("renamed DNS" in (data.get("entries") or {}),
                  "a missing library never wipes the tallies", data.get("entries"))
            # ...and leave the suite's library where the next checks expect it (the stats path
            # is derived from it: a config left pointing at a missing file moves the counters).
            notes.write_text("## flaky DNS\nmatch: no such host, name resolution\n"
                             "scope: any\nnote: the LAN resolver blipped; retry once.\n"
                             "repeat: once\n", encoding="utf-8")
            fb.CONFIG["agent"]["field_notes_file"] = str(notes)
            fb._FIELD_NOTES_CACHE["mtime"] = None

            stats(unmatched={"ValueError: unknown field frobnicate": {
                       "fails": 9, "first": "2026-10-01", "last": "2026-10-02",
                       "sample": "unknown field frobnicate"}})
            cands = fb.field_note_candidates()
            check(len(cands) == 1 and "9x" in cands[0] and "frobnicate" in cands[0],
                  "unmatched signatures are ranked for the operator", cands)
            check("draft ->" in cands[0], "with a matcher draft", cands)

            # A stopword is not a matcher: a live install's doctor printed `match: everything`
            # for a timeout signature, 2026-10-03.
            stats(unmatched={"timeout after 5s - the command and everything it started "
                             "were killed": {"fails": 3, "sample": "TIMEOUT after 5s"}})
            cands = fb.field_note_candidates()
            check("match: everything" not in cands[0],
                  "the draft never picks a stopword", cands)
            check("match: timeout" in cands[0],
                  "...and falls back to a distinctive word", cands)

            # The signature comes from the line that LOOKS like the failure, not the first
            # line: banners/headers were recorded as "the failure" of any multi-command
            # Result whose last command failed.
            sig = fb._failure_signature("=== tinycmdr status ===\nname source\n"
                                        "git : fatal: not a git repository\n")
            check("a banner is not the signature when a failing line exists",
                  "fatal" in sig and "tinycmdr status" not in sig, sig)
            sig = fb._failure_signature("checking...\n0 errors\n")
            check("...and a benign '0 errors' line does not win either",
                  sig.startswith("checking"), sig)
            sig = fb._failure_signature("the system cannot find the file specified.")
            check("a single failing line is still the signature",
                  "cannot find the file" in sig, sig)

            # Windows paths are squeezed like POSIX ones, while a
            # domain\user token stays readable.
            a = fb._failure_signature(r"error: C:\Users\One\proj\venv\lib missing")
            b = fb._failure_signature(r"error: C:\Other\place\venv\lib missing")
            check("a Windows drive path is squeezed out of the key",
                  a == b and "users" not in a, (a, b))
            u1 = fb._failure_signature(r"get-item : could not find \\10.1.1.1\share\a.tmp")
            check("a UNC path is squeezed too", "share" not in u1 and "p" in u1, u1)
            d = fb._failure_signature(r"whoami: acme\jdoe")
            check("a domain\\user token is not a path and survives", "acme" in d, d)

            # ---- a CLI run with nobody who can type (piped stdin) declares no human
            # A Windows install, 2026-10-03: a `--once` run driven over ssh with the script piped in
            # mounted the console door anyway, so ask_user parked the full 120s and then
            # stopped the run. The caller now says whether a human is reachable.
            class _TTY:
                def __init__(self, tty):
                    self._tty = tty

                def isatty(self):
                    return self._tty

            dst_none = fb.CliDestination(colour=False, out=_TTY(False), has_human=False)
            check(dst_none.has_human is False,
                  "has_human=False survives construction", dst_none.has_human)
            check(fb.RunReporter(dst_none, "k").ask("q") is None,
                  "confirm/ask on it takes the doorless path, not a stdin wait")
            dst_yes = fb.CliDestination(colour=False, out=_TTY(False))
            check(dst_yes.has_human is True,
                  "the default keeps a fake console human (tests and interactive lanes)")
            check(fb._cli_new_reporter(has_human=False).dest.has_human is False,
                  "the --once caller can declare it", None)

            # ---- an older host-owned drop-in must be REPORTED, not silently absorbed
            # A Windows install, 2026-10-03: tree 1.0.54, tools/process.py pre-1.0.54 → a 75s command
            # blocked the turn and nothing anywhere said auto-background was off.
            fb._DROPIN_GAPS.update({"checked": False, "gaps": []})
            fb._AUTOBG_GAP_WARNED = False
            _real_proc = fb.REGISTRY.custom.get("process")
            fb.REGISTRY.custom["process"] = {"fn": (lambda a, c: ""), "schema": {}}
            try:
                gaps = fb.dropin_gaps()
                check(any(g[0] == "process" and "spawn_probe" in g[1] for g in gaps),
                      "an older process drop-in is reported, not ignored", gaps)
                check(fb._shell_autobg("echo hi", {}, 60) is None,
                      "auto-background degrades cleanly on an old drop-in", None)
            finally:
                if _real_proc is None:
                    fb.REGISTRY.custom.pop("process", None)
                else:
                    fb.REGISTRY.custom["process"] = _real_proc
                fb._DROPIN_GAPS.update({"checked": False, "gaps": []})

            # ---- a host-owned file whose SHIPPED DEFAULT moved must be reported
            # update never overwrites a host-owned file by design, so a fix inside the default
            # reaches only new installs unless the host is told. Measured 2026-10-03: the
            # 16-colour fix lived in theme.toml, existing installs kept the old copy, and a
            # Windows console stayed all-yellow - the drop-in gap's shape, one release later.
            _work = Path(tempfile.mkdtemp(prefix="fbtest-hostgap-"))
            try:
                _saved_base = fb.BASE_DIR
                fb.BASE_DIR = _work
                (_work / "theme.toml").write_text("# mine\n", encoding="utf-8")
                (_work / "theme.default.toml").write_text("# mine\n", encoding="utf-8")
                fb._HOST_DEFAULT_GAPS.update({"checked": False, "gaps": []})
                check("a host file identical to the shipped default is not reported",
                      fb.host_file_gaps() == [], fb.host_file_gaps())
                (_work / "theme.toml").write_text("# mine, edited\n", encoding="utf-8")
                fb._HOST_DEFAULT_GAPS.update({"checked": False, "gaps": []})
                check("...and one that differs IS reported, with its default named",
                      fb.host_file_gaps() == [("theme.toml", "theme.default.toml")],
                      fb.host_file_gaps())
                (_work / "theme.toml").unlink()
                fb._HOST_DEFAULT_GAPS.update({"checked": False, "gaps": []})
                check("...and an absent file is not a gap", fb.host_file_gaps() == [],
                      fb.host_file_gaps())
            finally:
                fb.BASE_DIR = _saved_base
                fb._HOST_DEFAULT_GAPS.update({"checked": False, "gaps": []})
                shutil.rmtree(_work, ignore_errors=True)

            # ---------------------------------------------------------- memory scrubbing
            fb._SECRETS.add("hunter2secret")
            try:
                fb.tool_memory({"action": "add", "title": "wifi password",
                                "body": "the wifi password is hunter2secret here"},
                               dict(ctx))
            finally:
                fb._SECRETS.discard("hunter2secret")
            concept = fb.MEMORY_DIR / "wifi-password.md"
            body = concept.read_text(encoding="utf-8") if concept.exists() else ""
            check("hunter2secret" not in body and "redacted" in body,
                  "a memory write passes the secret scrubber", body[-200:])

            # ---------------------------------------------------------- hint persistence
            fb._HINTS_SHOWN.clear()
            fb.SESSIONS_DIR.mkdir(exist_ok=True)   # a real run has saved its session by now
            fb._hints_path("hp").unlink(missing_ok=True)
            first = fb.result_hint("fetch_url", {}, "page", session_key="hp")
            check("DATA" in first, "a hint attaches once", first)
            fb._HINTS_SHOWN.clear()             # simulate a restart
            check(fb.result_hint("fetch_url", {}, "page", session_key="hp") == "",
                  "...and does not come back after a restart")
            check("DATA" in fb.result_hint("fetch_url", {}, "page", session_key="hp2"),
                  "a different session still gets it")

            # ---------------------------------------------------------- read receipts
            target = workdir / "receipts.txt"
            target.write_text("alpha\nbeta\n", encoding="utf-8")
            fb.tool_read_file({"path": str(target)}, dict(ctx))
            target.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
            out = fb.tool_edit_file({"path": str(target), "old_string": "epsilon",
                                     "new_string": "zeta"}, dict(ctx))
            check("changed since your last read" in out,
                  "an edit against a moved file says so", out[:220])

            # ---- CRLF files: a correct edit must NOT be told the file changed
            # Found on a Windows install, 2026-10-03, by its own agent: the read side recorded
            # the raw CRLF bytes and the edit side compared its LF-normalized copy, so every
            # correct edit to a CRLF file carried "changed since your last read (was 3 lines,
            # now 3)". Both sides now hash the same LF view and count lines the same way.
            crlf = workdir / "crlf.txt"
            crlf.write_bytes(b"alpha\nbeta\r\n")
            fb.tool_read_file({"path": str(crlf)}, dict(ctx))
            out = fb.tool_edit_file({"path": str(crlf), "old_string": "alpha",
                                     "new_string": "ALPHA"}, dict(ctx))
            check("changed since your last read" not in out,
                  "a correct edit to a CRLF file carries no stale-view note", out[-200:])
            crlf.write_bytes(b"alpha\nbeta\ngamma\r\n")
            out = fb.tool_edit_file({"path": str(crlf), "old_string": "nope",
                                     "new_string": "x"}, dict(ctx))
            check("changed since your last read" in out and "was 2 lines, now 3" in out,
                  "...and an externally changed CRLF file does, with true line counts",
                  out[-240:])

            # ---------------------------------------------------------- verify region
            pyfile = workdir / "broken.py"
            pyfile.write_text("def f():\n    return 1\n", encoding="utf-8")
            out = fb.tool_edit_file({"path": str(pyfile),
                                     "old_string": "def f():\n    return 1",
                                     "new_string": "def f(:"}, dict(ctx))
            check("verify FAILED" in out and "before this edit (from the .bak)" in out
                  and "fix the REGION" in out,
                  "a broken edit shows the region and the pre-image", out[-300:])

            # ---- A-2026-10-08-96: an edit round-trips the file's own encoding
            # The write was always UTF-8, so every cp1252/Latin-1/Shift-JIS byte in the file
            # became U+FFFD on the first edit, with a clean diff.
            latin = workdir / "latin1.txt"
            latin.write_bytes(b"caf\xe9 grounds\nsecond line\n")
            out = fb.tool_edit_file({"path": str(latin), "old_string": "second line",
                                     "new_string": "SECOND LINE"}, dict(ctx))
            check(out.startswith("OK"), "a cp1252/Latin-1 file edits cleanly", out[:160])
            check(latin.read_bytes() == b"caf\xe9 grounds\nSECOND LINE\n",
                  "...and its non-UTF-8 bytes survive the round-trip", latin.read_bytes())

            # ---- A-2026-10-08-99: a PowerShell 5.1 `>` redirect is UTF-16
            # Decoded as UTF-8 with errors="replace" it read as NUL-interleaved text and
            # never matched a content search.
            u16 = workdir / "ps-redirect.txt"
            u16.write_bytes("alpha kumquat beta\n".encode("utf-16"))     # BOM + LE
            read_back = fb.tool_read_file({"path": str(u16)}, dict(ctx))
            check("needle" not in read_back and "kumquat" in read_back
                  and "\x00" not in read_back,
                  "a UTF-16 file reads as text, not NULs", read_back[:120])
            searched = fb.tool_search_files({"path": str(workdir), "content": "kumquat"},
                                            dict(ctx))
            check("ps-redirect.txt" in searched,
                  "...and a content search finds inside it", searched[:200])

            # ---------------------------------------------------------- skills metadata
            sk = workdir / "skills" / "net"
            sk.mkdir(parents=True)
            (sk / "SKILL.md").write_text(
                "---\nname: net\ndescription: LAN checks\nglobs: *.conf, /etc/**\n"
                "always: true\n---\n\nAlways check the resolver first.\n", encoding="utf-8")
            trg = workdir / "skills" / "trig"
            trg.mkdir(parents=True)
            (trg / "SKILL.md").write_text(
                "---\nname: trig\ndescription: triggered runbook\n"
                "globs: *.conf, /etc/**\n---\n\nBody.\n", encoding="utf-8")
            hid = workdir / "skills" / "secret"
            hid.mkdir(parents=True)
            (hid / "SKILL.md").write_text(
                "---\nname: secret\ndescription: hidden runbook\nhide: true\n---\n\nX\n",
                encoding="utf-8")
            opo = workdir / "skills" / "oponly"
            opo.mkdir(parents=True)
            (opo / "SKILL.md").write_text(
                "---\nname: oponly\ndescription: operator-only runbook\n"
                "disable-model-invocation: true\n---\n\nY\n", encoding="utf-8")
            idx = {s["name"]: s for s in fb.skill_index()}
            check(idx["net"]["globs"] == ["*.conf", "/etc/**"] and idx["net"]["always"],
                  "globs and always parse", idx["net"])
            check(idx["secret"]["hide"], "hide parses", idx["secret"])
            check(idx["oponly"]["hide"],
                  "omp's disable-model-invocation spelling parses as the same switch",
                  idx["oponly"])
            prompt = fb.build_system_prompt()
            check("secret" not in prompt,
                  "a hidden runbook is not in the prompt index")
            check("oponly" not in prompt,
                  "an operator-only runbook stays out of the prompt index too")
            check("trig (globs: *.conf, /etc/**): triggered runbook" in prompt,
                  "a trigger is rendered in the index",
                  [ln for ln in prompt.splitlines() if "trig (" in ln][:2])
            check("net (globs:" not in prompt,
                  "an always-on skill stays out of the index", "")
            fb._ALWAYS_SKILLS_CACHE["at"] = 0.0
            vol = fb.volatile_context(session_key="extras")
            check("Always check the resolver first." in vol,
                  "an always-on runbook body rides the trailing block", vol[-400:])
            card_ids = [s.get("id") for s in fb.a2a_card().get("skills", [])]
            check("net" in card_ids and "oponly" not in card_ids,
                  "the public A2A card carries normal runbooks and not operator-only ones",
                  card_ids)

            # ---------------------------------------------------------- session state + fork
            check(fb._session_tail_state([{"role": "user", "content": "hi"}]) == "unfinished",
                  "a dangling operator message is unfinished")
            check(fb._session_tail_state(
                [{"role": "user", "content": "hi"},
                 {"role": "assistant", "content": "done"}]) == "ok",
                "an answered conversation is ok")
            check(fb._session_tail_state(
                [{"role": "user", "content": "hi"},
                 {"role": "assistant", "content": "⛔ Stopped."}]) == "unfinished",
                "an abnormal end marker is unfinished")
            src_key = "fork-src"
            A._save(src_key)
            src = A._session_path(src_key)
            made = A.fork(src_key, "fork-copy")
            check(made == "fork-copy" and A._session_path(made).exists(),
                  "fork copies the conversation", made)
            check(A._session_path(made).read_bytes() == src.read_bytes(),
                  "...byte for byte")
            again = A.fork(src_key, "fork-copy")
            check(again == "fork-copy-2", "a second fork of the same name is numbered", again)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print("all harness-extra checks passed")
        return 0
    return main()


def _suite_test_harness_refinements():
    import unittest
    import tempfile
    import pathlib
    import sys
    import os
    import atexit
    import shutil

    # Ensure repo root is on sys.path
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

    import tinycmdr as _fb                                                   # noqa: E402
    from tinycmdr import (
        AGENT,
        _carry_reset,
        _CARRY,
        _CARRY_LOCK,
        _carry_path,
        tool_edit_file,
        tool_execute_code
    )

    # --- this suite owns the files it grades ----------------------------------------------
    # Importing the app sets BASE_DIR to the checkout, so a write through the app landed in the
    # repo and the session carry file created sessions/ beside it (both named by run_all.py's
    # leak report). Point every repo-root data file at a temp dir this suite removes on the way out.
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    import hermetic                                                          # noqa: E402

    _SANDBOX = pathlib.Path(tempfile.mkdtemp(prefix="fbharness-"))
    atexit.register(lambda: shutil.rmtree(_SANDBOX, ignore_errors=True))
    hermetic.redirect_repo_files(_fb, _SANDBOX)

    class TestHarnessRefinements(unittest.TestCase):

        def test_carry_reset_on_session_reset(self):
            """AGENT.reset must clear in-memory carry and remove .carry.json on disk."""
            key = "test-reset-carry-key"
            with _CARRY_LOCK:
                _CARRY[key] = {"run": 1, "entries": [{"tool": "read_file", "args": "foo", "out": "bar"}]}
        
            carry_file = _carry_path(key)
            carry_file.write_text('{"run": 1, "entries": []}', encoding="utf-8")
            self.assertTrue(carry_file.exists())
            self.assertIn(key, _CARRY)

            AGENT.reset(key)

            self.assertNotIn(key, _CARRY)
            self.assertFalse(carry_file.exists())

        def test_session_loader_skips_carry_sidecars(self):
            """A sessions/ sidecar must not load as a conversation of its own.

        Three files live in that folder beside a conversation - the carry, the hint list
        and the parked question - and Path.stem turns each into a key of its own. The
        exclusion list named only the carry, and
        the hints file is a JSON LIST, so it also listed as a conversation in the
        `sessions` verb.
        """
            real = _fb.SESSIONS_DIR / "reload-probe.json"
            real.write_text('[{"role": "user", "content": "hi"}]', encoding="utf-8")
            sidecars = {suffix: _fb.SESSIONS_DIR / ("reload-probe" + suffix)
                        for suffix in (".carry.json", ".hints.json", ".question.json")}
            sidecars[".carry.json"].write_text('{"run": 1, "entries": []}', encoding="utf-8")
            sidecars[".hints.json"].write_text('["hint-a", "hint-b"]', encoding="utf-8")
            sidecars[".question.json"].write_text('{"question": "restart?", "options": []}',
                                                  encoding="utf-8")
            try:
                fresh = _fb.Agent()
                self.assertIn("reload-probe", fresh.histories)      # the conversation loads
                for suffix in sidecars:
                    self.assertNotIn("reload-probe" + suffix.split(".")[1],
                                     fresh.histories)
                rows = [r["key"] for r in _fb._cli_session_rows()]
                self.assertIn("reload-probe", rows)
                for phantom in ("reload-probe.carry", "reload-probe.hints",
                                "reload-probe.question"):
                    self.assertNotIn(phantom, rows)
            finally:
                real.unlink(missing_ok=True)
                for p in sidecars.values():
                    p.unlink(missing_ok=True)

        def test_every_session_file_goes_on_forget_and_reset(self):
            """Forgetting a conversation (delete or prune) and /new drop ALL of its files.

        _web_forget_files kept a hand-written suffix list that missed the hint list and
        the parked question, and AGENT.reset dropped only the transcript and the page
        log - so a deleted conversation left sidecars behind for ever and /new carried
        the old carry, hints, question and events into the fresh one
        (A-2026-10-08-118). One list now (_CONVERSATION_FILE_SUFFIXES)."""
            key = "forget-probe"
            suffixes = (".json", ".web.jsonl", ".carry.json", ".hints.json",
                        ".question.json", ".transcript.jsonl", ".events.jsonl")

            def make():
                _fb.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
                for s in suffixes:
                    (_fb.SESSIONS_DIR / (key + s)).write_text("x", encoding="utf-8")

            try:
                make()
                _fb._web_forget_files(key)
                self.assertEqual([s for s in suffixes
                                  if (_fb.SESSIONS_DIR / (key + s)).exists()], [],
                                 "the web door left sidecars behind")
                make()
                AGENT.reset(key)
                self.assertEqual([s for s in suffixes
                                  if (_fb.SESSIONS_DIR / (key + s)).exists()], [],
                                 "/new left sidecars behind")
            finally:
                for s in suffixes:
                    (_fb.SESSIONS_DIR / (key + s)).unlink(missing_ok=True)

        def test_fork_copies_the_state_but_not_the_parked_question(self):
            """A fork carries the conversation's sidecars - and not a run's question.

        The fork's suffix list was hand-written too; it now walks the ONE list, so a
        sidecar added later cannot be silently left out, and the two exclusions (the
        history, copied above, and the parked question, which belongs to the run that
        asked it) are stated in the code."""
            src, dst = "fork-src-118", "fork-dst-118"
            _fb.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            try:
                (_fb.SESSIONS_DIR / (src + ".json")).write_text("[]", encoding="utf-8")
                for s in (".carry.json", ".hints.json", ".question.json",
                          ".transcript.jsonl", ".events.jsonl"):
                    (_fb.SESSIONS_DIR / (src + s)).write_text("x", encoding="utf-8")
                made = AGENT.fork(src, dst)
                self.assertTrue(made)
                for s in (".json", ".carry.json", ".hints.json", ".transcript.jsonl",
                          ".events.jsonl"):
                    self.assertTrue((_fb.SESSIONS_DIR / (made + s)).exists(),
                                    "fork left out %s" % s)
                self.assertFalse((_fb.SESSIONS_DIR / (made + ".question.json")).exists(),
                                 "a fork copied a RUN's parked question")
            finally:
                for s in (".json", ".web.jsonl", ".carry.json", ".hints.json",
                          ".question.json", ".transcript.jsonl", ".events.jsonl"):
                    (_fb.SESSIONS_DIR / (src + s)).unlink(missing_ok=True)
                    (_fb.SESSIONS_DIR / (dst + s)).unlink(missing_ok=True)

        def test_reset_reclaims_but_never_steals_session_locks(self):
            """AGENT.reset drops the session's lock, but leaves one a worker still holds."""
            key = "test-reset-lock-key"
            AGENT._lock(key)                       # mint it, as a run would
            self.assertIn(key, AGENT.locks)
            AGENT.reset(key)
            self.assertNotIn(key, AGENT.locks)

            held_key = "test-reset-held-lock-key"
            held = AGENT._lock(held_key)
            held.acquire()
            try:
                AGENT.reset(held_key)
                self.assertIn(held_key, AGENT.locks)
            finally:
                held.release()

        def test_tool_edit_file_full_line_deletion_exact(self):
            """Deleting a full line via edit_file exact match must not leave an empty line."""
            with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".conf") as f:
                f.write("[section]\nkey1 = value1\ndelete_me = true\nkey2 = value2\n")
                f_path = pathlib.Path(f.name)

            try:
                res = tool_edit_file({
                    "path": str(f_path),
                    "old_string": "delete_me = true",
                    "new_string": ""
                }, {})
                self.assertIn("OK: replaced 1 occurrence", res)
                content = f_path.read_text(encoding="utf-8")
                self.assertEqual(content, "[section]\nkey1 = value1\nkey2 = value2\n")
            finally:
                f_path.unlink(missing_ok=True)
                pathlib.Path(str(f_path) + ".bak").unlink(missing_ok=True)

        def test_tool_edit_file_deletion_fuzzy(self):
            """Deleting lines via edit_file fuzzy match must not leave an empty line."""
            with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".conf") as f:
                f.write("[section]\n    key1 = value1\n    delete_me = true\n    key2 = value2\n")
                f_path = pathlib.Path(f.name)

            try:
                # old_string has different indentation to force fuzzy match
                res = tool_edit_file({
                    "path": str(f_path),
                    "old_string": "delete_me = true",
                    "new_string": ""
                }, {})
                self.assertIn("OK: replaced 1 occurrence", res)
                content = f_path.read_text(encoding="utf-8")
                self.assertEqual(content, "[section]\n    key1 = value1\n    key2 = value2\n")
            finally:
                f_path.unlink(missing_ok=True)
                pathlib.Path(str(f_path) + ".bak").unlink(missing_ok=True)

        def test_tool_execute_code_name_error_hint(self):
            """execute_code should provide a process isolation hint when NameError occurs."""
            res = tool_execute_code({"code": "print(unknown_var_xyz)"}, {})
            self.assertIn("exit_code=1", res)
            self.assertIn("NameError: name 'unknown_var_xyz' is not defined", res)
            self.assertIn("[HINT: execute_code runs each snippet in an isolated Python process", res)
    _loader = unittest.TestLoader()
    _cases = unittest.TestSuite()
    for _n, _o in sorted(locals().items()):
        if isinstance(_o, type) and issubclass(_o, unittest.TestCase):
            _cases.addTests(_loader.loadTestsFromTestCase(_o))
    _result = unittest.TextTestRunner(verbosity=2).run(_cases)
    sys.exit(0 if _result.wasSuccessful() else 1)


def _suite_test_verify():
    """Offline checks for post-write verification.

No model calls. The verifier is a pure function of the file on disk, so it can be
pinned exactly: a broken file must be reported broken, a good one reported good, and
an unverifiable type must stay silent rather than inventing a verdict.

    python tests/test_harness_surface.py
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

            # ...but a file in SOMEBODY ELSE'S ./tools/ is not a drop-in tool. Measured
            # 2026-09-29: the gate was the DIRECTORY NAME, so any .py under any directory called
            # tools ran the tool loader, which correctly said "no tool here" - and the model was
            # handed "[HARNESS verify FAILED ... The file on disk is broken]" about a perfectly
            # good module, then rewrote a correct file. tools_dir_verdict already used the
            # resolved-path test; _verify_python does now too.
            other = workdir / "userproject" / "tools"
            other.mkdir(parents=True, exist_ok=True)
            plain = other / "helpers.py"
            plain.write_text("def load(p):\n    return open(p).read()\n", encoding="utf-8")
            st, why = fb.verify_written_file(plain)
            check((st, why) == ("ok", "python syntax OK"),
                  f"a module in somebody else's ./tools/ still verifies -> {st}/{why}")

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
            # outright, and a successful edit said nothing about WHAT changed. On a host that manages other installs, that reads as a stupid agent; both are capabilities, not intelligence.
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
            # The paths are QUOTED, and that is load-bearing rather than tidiness: on a host whose
            # temporary directory contains a space (any Windows profile for a two-word account
            # name, e.g. "C:\Users\Example User\AppData\Local\Temp\...") an unquoted path is two
            # arguments to the shell, so the command is not one anybody would run and the first
            # token is the only thing any reader could recover. Quoted, it is the shape the shell
            # actually receives - and the shape the detector has to read (fixed the same day).
            _p1, _p2, _p3 = workdir / "s1.json", workdir / "s2.json", workdir / "s3.txt"
            for cmd, want in (
                    (f"Set-Content -Path '{_p1}' -Value '{{\"a\": 1'", "s1.json"),
                    (f"echo '{{\"b\": 2}}' > '{_p2}'", "s2.json"),
                    (f"printf 'x' | tee '{_p3}'", "s3.txt"),
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

            # A-2026-10-08-105: the shell runs in BASE_DIR, so a RELATIVE redirect target is
            # the install's file - Path.cwd() here verified the wrong file or none at all.
            rel = workdir / "rel-verify-target.json"
            rel.write_text('{"a": 1', encoding="utf-8")
            out = fb.verify_shell_writes(write_cmd(Path("rel-verify-target.json"), '{"a": 1'))
            check("[HARNESS verify FAILED:" in out,
                  "a relative redirect target is verified where the shell ran")
            rel.unlink()

            # ---- off switch ----------------------------------------------------
            fb.CONFIG["agent"]["verify_after_write"] = False
            check(fb.verify_note(badj) == "", "verify_after_write=false silences it")
            check(fb.verify_shell_writes(
                f"Set-Content -Path '{shell_bad}' -Value x") == "",
                "the shell path honours the same off switch")
            fb.CONFIG["agent"]["verify_after_write"] = True
            check(fb.verify_note(badj) != "", "turning it back on restores the verdict")

            # A redirection INSIDE a quote is text, not a write (review 2026-09-29): the guesser
            # produced a candidate path from inside `'x>y'`, and when a file of that name happened
            # to exist the result carried a verify verdict about a file the command never touched.
            check(fb.shell_written_files("grep 'x>y' notes.md") == [],
                  fb.shell_written_files("grep 'x>y' notes.md"))
            check(fb.shell_written_files('grep "a > b" notes.md') == [],
                  fb.shell_written_files('grep "a > b" notes.md'))
            check(fb.shell_written_files("echo hi > out.txt") == ["out.txt"],
                  fb.shell_written_files("echo hi > out.txt"))
            check(fb.shell_written_files('echo hi > "my file.txt"') == ["my file.txt"],
                  fb.shell_written_files('echo hi > "my file.txt"'))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) failed")
            sys.exit(1)
        print("all post-write verification checks passed")
    return main()


def _suite_test_run_all():
    """The gate's own controls: what it grades, and that the tree cannot shrink the sweep.

Three properties of tests/run_all.py that no suite graded, each one measured wrong once:

  * WHICH BUILD the suites grade. Every suite picks its file with
    `BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")`, and the runner used to pop that
    variable out of the child environment as a stale shell pick - so
    `TINYCMDR_SRC=<pre-fix build> python tests/run_all.py` answered green for the
    checkout's own tinycmdr.py. The falsification step read as "the test does not
    reproduce the bug".
  * THE TREE KILL. run_one spawns each suite in its own process group and promises a
    helper dies with it; on Windows proc.kill() reached the direct child only.
  * THE ENVELOPE. A missing tests/test_envelope.py was a note, not a red gate.

    python tests/test_harness_surface.py

Falsification: against the pre-fix tests/run_all.py, graded_source() does not exist (so
the import-and-call checks fail), _windows_kill_argv does not exist, and a run with
TINYCMDR_SRC pointing at a missing file goes on to grade the tree's own build instead of
exiting 2.
"""
    import importlib.util
    import os
    import subprocess
    import sys
    import tempfile
    import time
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    RUN_ALL = BASE / "tests" / "run_all.py"
    ENVELOPE = BASE / "tests" / "test_envelope.py"
    FAILS = []


    def check(what, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
        if not ok:
            FAILS.append(what)


    def load_runner():
        """tests/run_all.py as a module. Its module level is imports and constants only."""
        spec = importlib.util.spec_from_file_location("tinycmdr_run_all", RUN_ALL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


    def sha12(path):
        import hashlib
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


    def gone(pid, seconds=10.0):
        """True once `pid` is no longer signalable (killed and reaped by init)."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                return True
            time.sleep(0.1)
        return False


    def main():
        mod = load_runner()

        # (d) --exclude drops a suite and SAYS so. The install surface's Windows job uses it for the
        # suites that are red there (named in STATUS.json and kept in step by tests/test_contracts.py),
        # so the mechanism itself is graded: it must drop exactly what it is told, keep everything
        # else, and print the exclusion instead of shrinking the sweep quietly.
        def listing(*args):
            proc = subprocess.run([sys.executable, str(RUN_ALL), "--list", *args],
                                  capture_output=True, text=True, cwd=str(BASE))
            rows = proc.stdout.splitlines()
            return (proc.returncode, [r for r in rows if r.startswith("tests/")],
                    [r[len("excluded: "):] for r in rows if r.startswith("excluded: ")])

        rc0, whole, _ = listing()
        # The expectation is the TREE's own suite files, never a fixed count: the
        # source tree carries 53 and the generated install surface ships 43 (its
        # dev-only graders stay behind, per maintenance/product-manifest.json), so
        # the old "> 50" graded the source tree only and the surface's own run
        # failed this check with its 43 suites (measured 2026-10-10).
        on_disk = sorted("tests/" + p.name
                         for p in (BASE / "tests").glob("test_*.py"))
        check("--list names every suite and nothing is excluded by default",
              rc0 == 0 and not listing()[2] and sorted(whole) == on_disk,
              (len(whole), len(on_disk)))
        rc1, reduced, dropped = listing("--exclude", "tests/test_tui.py")
        check("--exclude drops exactly the named suite, and says which",
              rc1 == 0 and dropped == ["tests/test_tui.py"]
              and "tests/test_tui.py" not in reduced and len(reduced) == len(whole) - 1,
              (dropped, len(reduced), len(whole)))
        rc2, globbed, dropped2 = listing("--exclude", "tests/test_st*py")
        check("a glob drops every match, each one named",
              rc2 == 0 and len(dropped2) > 1 and all(d.startswith("tests/test_st") for d in dropped2)
              and len(globbed) == len(whole) - len(dropped2),
              (dropped2, len(globbed), len(whole)))

        # (a) no env pick: the tree's own build, with its digest.
        saved = os.environ.pop("TINYCMDR_SRC", None)
        try:
            path, digest = mod.graded_source()
            check("unset TINYCMDR_SRC grades the tree's own tinycmdr.py",
                  path == BASE / "tinycmdr.py" and digest == sha12(BASE / "tinycmdr.py"),
                  "%s (%s)" % (path, digest))

            # (b) an explicit pick is the file that gets graded, and its digest is that file's.
            with tempfile.TemporaryDirectory(prefix="tinycmdr-src-") as tmp:
                pick = Path(tmp) / "pre-fix.py"
                pick.write_text("# a pre-fix build\n", encoding="utf-8")
                os.environ["TINYCMDR_SRC"] = str(pick)
                got_path, got_digest = mod.graded_source()
                check("an explicit TINYCMDR_SRC is the graded source",
                      got_path == pick and got_digest == sha12(pick),
                      "%s (%s)" % (got_path, got_digest))
                check("its digest is not the tree's",
                      got_digest != sha12(BASE / "tinycmdr.py"))
                env = mod.child_env(BASE / "tests" / "test_safety_surface.py", Path(tmp))
                check("child_env passes TINYCMDR_SRC through",
                      env.get("TINYCMDR_SRC") == str(pick), repr(env.get("TINYCMDR_SRC")))
        finally:
            os.environ.pop("TINYCMDR_SRC", None)
            if saved is not None:
                os.environ["TINYCMDR_SRC"] = saved

        # (c) end to end: a pick that names no file is a red run, not a quiet grading of the
        # tree's own build (which is what the popped variable produced).
        env = dict(os.environ)
        env["TINYCMDR_SRC"] = str(BASE / "tests" / "no-such-build.py")
        done = subprocess.run([sys.executable, str(RUN_ALL), "--select",
                               "tests/test_envelope.py"], cwd=str(BASE), env=env,
                              capture_output=True, text=True, timeout=120)
        check("a TINYCMDR_SRC that names no file is a red run", done.returncode != 0,
              "exit %d: %s" % (done.returncode, (done.stdout + done.stderr).strip()[-200:]))
        check("it says nothing could be graded",
              "nothing to grade" in (done.stdout + done.stderr))

        # (d) the Windows kill is a tree kill.
        argv = mod._windows_kill_argv(4321)
        check("the Windows kill names the process tree",
              "/T" in argv and argv[-1] == "4321", " ".join(argv))

        # (e) POSIX: a helper the suite spawned dies with the suite.
        if os.name == "posix":
            grandchild_code = ("import subprocess, sys, time\n"
                               "p = subprocess.Popen([sys.executable, '-c', "
                               "'import time; time.sleep(120)'])\n"
                               "print(p.pid, flush=True)\n"
                               "time.sleep(120)\n")
            proc = subprocess.Popen([sys.executable, "-c", grandchild_code],
                                    stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                    text=True, start_new_session=True)
            try:
                line = proc.stdout.readline().strip()
                grandchild = int(line)
            except (ValueError, AttributeError):
                grandchild = 0
            check("the probe suite spawned a helper", grandchild > 0, line)
            if grandchild:
                mod._kill_tree(proc)
                check("_kill_tree kills the helper too", gone(grandchild))

        # (f) the envelope assertions cannot be dropped from the tree.
        check("tests/test_envelope.py is in the tree", ENVELOPE.is_file())
        done = subprocess.run([sys.executable, str(RUN_ALL), "--require-envelope"],
                              cwd=str(BASE), capture_output=True, text=True, timeout=60)
        check("the retired --require-envelope switch is gone",
              done.returncode == 2 and "unrecognized arguments" in done.stderr,
              "exit %d: %s" % (done.returncode, done.stderr.strip()[-160:]))

        if FAILS:
            print("\n%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
            return 1
        print("\nall runner checks passed")
        return 0
    return main()


def main():
    rc = 0
    for name, fn in (("test_harness_extras", _suite_test_harness_extras), ("test_harness_refinements", _suite_test_harness_refinements), ("test_verify", _suite_test_verify), ("test_run_all", _suite_test_run_all)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
