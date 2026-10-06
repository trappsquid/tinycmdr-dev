"""The small-half integrations: text tool calls, conflicts, note policy, receipts.

Each check names the mechanism it pins, and every one of them is a shape a weak model
produces on a real box.

    python tests/test_harness_extras.py
"""
import json
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

        # ---- a timeout shorter than the auto-background window must still kill
        # A Windows install, 2026-10-03: shell timeout=5 on a 45-second command came back
        # exit_code=0 after 45s - the auto-background wait ran on its own clock (60s) and
        # never read the timeout the model asked for.
        _slow = '%s -c "import time; time.sleep(30)"' % sys.executable
        _t0 = time.time()
        _r = fb._shell_autobg(_slow, {}, 60, 2)
        _dt = time.time() - _t0
        check("a timeout shorter than the auto-background window still kills",
              isinstance(_r, str) and _r.startswith("TIMEOUT after 2s") and _dt < 20,
              (round(_dt, 1), str(_r)[:200]))

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


if __name__ == "__main__":
    sys.exit(main())
