"""Offline checks for tool-result digestion (Phase 1a) and field notes (Phase 1d).

No model calls, no network. Both features are pure functions of the text a tool
returned, which is exactly why they can be pinned down here: if these pass, a change
in behaviour is a change in the regexes, not in the mood of a model.

    python tests/test_digest.py
"""
import importlib.util
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
import hermetic      # noqa: E402  (stages the field-notes fixture a clean clone lacks)

FAILS = []


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def load_staged():
    workdir = Path(tempfile.mkdtemp(prefix="fbdigest-"))
    run_scenario.stage_install(workdir, 24000)
    fb = run_scenario.load(workdir)
    return fb, workdir


LOG = "\n".join(
    [f"2026-09-13T04:{i:02d}:00Z INFO  svc=indexer msg=\"batch complete\" items={i}"
     for i in range(90)] +
    ['2026-09-13T04:50:00Z ERROR svc=backupd msg="write failed: disk quota exceeded"'] +
    [f"2026-09-13T04:{i:02d}:30Z INFO  svc=mailq msg=\"queue drained\"" for i in range(50)])

UNIT = "\n".join([
    "● backupd.service - nightly backup agent",
    "     Loaded: loaded (/etc/systemd/system/backupd.service; enabled)",
    "     Active: active (running) since Sat 2026-09-13 03:00:11 UTC; 1h ago",
    "   Main PID: 4412 (backupd)",
    "      Tasks: 4 (limit: 9430)",
    "     Memory: 118.4M (peak: 204.4M)",
    "        CPU: 22.140s",
    "     CGroup: /system.slice/backupd.service",
    "             └─4412 /usr/local/bin/backupd --config /etc/backupd.toml",
    "",
] + [f"Sep 13 0{i}:00:00 host backupd[4412]: heartbeat {i}" for i in range(1, 6)] +
    ["Sep 13 04:12:44 host backupd[4412]: write failed: disk quota exceeded"] +
    [f"Sep 13 04:0{i}:00 host systemd[1]: unrelated unit chatter {i}" for i in range(6, 20)])

APT = "\n".join(
    [f"Get:{i} http://deb.example.org/ubuntu noble/main amd64 package-number-{i} "
     f"[1,234 B]" for i in range(120)] +
    ["Err:121 http://deb.example.org/ubuntu noble/main amd64 libfoo [404 Not Found]",
     "E: Failed to fetch http://deb.example.org/ubuntu/pool/libfoo.deb 404 Not Found"])

PS = "\n".join(["  PID %CPU %MEM COMMAND"] +
               [f"{1000 + i}  {i % 9}.1  {i % 5}.0 proc-{i}" for i in range(120)])

SMALL = "exit_code=0\nhello\n"


def main():
    fb, workdir = load_staged()
    try:
        cfg = fb.CONFIG["agent"]
        check(cfg.get("digest_enabled") is True and cfg.get("digest_min_chars") == 1200,
              "digest config keys exist with the shipped defaults")

        # ---- digestion -----------------------------------------------------
        small = fb.digest_output("shell", {"command": "ls"}, SMALL)
        check(small == SMALL, "small output is left alone")

        unknown = fb.digest_output("shell", {"command": "echo " + "x" * 2000},
                                   "exit_code=0\n" + "\n".join(f"line {i}" for i in range(80)))
        check("[HARNESS: digested" not in unknown,
              "an unrecognised command shape is not digested")

        raw = fb.digest_output("shell", {"command": "journalctl -u backupd", "raw": True}, LOG)
        check(raw == LOG, "raw=true bypasses digestion")

        j = fb.digest_output("shell", {"command": "journalctl -u backupd --since -1h"}, LOG)
        check("[HARNESS: digested `journal` output" in j, "a journalctl call is recognised")
        check("disk quota exceeded" in j and "batch complete" not in j,
              "the error line survives and routine lines are dropped")
        check(len(j) < len(LOG), f"the digest is smaller ({len(j)} < {len(LOG)})")
        check("raw=true" in j, "the digest tells the model how to get the full output")

        u = fb.digest_output("shell", {"command": "systemctl status backupd"}, UNIT)
        check("[HARNESS: digested `unit status` output" in u, "systemctl status is recognised")
        check("Active: active (running)" in u, "the unit header survives")
        check("disk quota exceeded" in u, "the journal error line survives")
        check("unrelated unit chatter" not in u, "unrelated journal lines are dropped")

        a = fb.digest_output("shell", {"command": "apt-get install -y libfoo"}, APT)
        check("[HARNESS: digested `package manager` output" in a, "apt is recognised")
        check("Failed to fetch" in a and "Err:121" in a,
              "the apt failure survives even though it is the last line")
        check(a.count("Get:") <= 39, "the download chatter is dropped")
        check("failure line(s) first" in a, "the digest says failures came first")

        g = fb.digest_output("shell", {"command": "grep -rn TODO src/"}, PS)
        check("[HARNESS: digested `search results` output" in g, "grep is recognised")
        check(g.count("\n") <= 41, f"a grep result keeps at most 40 lines ({g.count(chr(10))})")

        p = fb.digest_output("shell", {"command": "ps -eo pid,args"}, PS)
        check("[HARNESS: digested `process list` output" in p, "a process list is recognised")
        check("line(s) omitted" in p, "a process list is head/tail trimmed")

        # A read_file is NOT digested, whatever the file is called. The subject used to be the
        # PATH, so a document was shaped by its FILENAME: measured 2026-09-29 by probing the
        # shape list, `.txt` chapters of a rewrite came back as "log file" (gutted to their
        # error-looking lines, and re-read every time), `docker ps logs.txt` as a container
        # list, `git diff review.md` as git output, `dir/notes.md` as a directory listing.
        # Digestion is for COMMAND output, where re-running the command is the recovery; a big
        # read is spilled whole instead, which loses nothing at all.
        TXT = "\n".join(f"line {i} of the chapter" for i in range(200))
        for path in ("/work/chapter08.txt", "/var/log/app.log", "/work/run.out",
                     "/work/docker ps logs.txt", "dir/notes.md", "/w/git diff review.md"):
            check(fb.digest_output("read_file", {"path": path}, TXT) == TXT,
                  f"a read_file is left whole ({path})")

        # ... and neither is an execute_code, whose SOURCE is not its output.
        for code in ("print('docker ps output')", "subprocess.run('ps -ef', shell=True)",
                     "print(count('grep'))"):
            check(fb.digest_output("execute_code", {"code": code}, TXT) == TXT,
                  f"an execute_code result is left whole ({code[:28]})")

        # an already-small selection is never announced
        tiny = fb.digest_output("shell", {"command": "journalctl"}, "exit_code=0\none line")
        check("[HARNESS" not in tiny, "a digest that would drop nothing is not announced")

        # ---- field notes ---------------------------------------------------
        # The library a host actually runs on is the operator's own field-notes.md, which
        # is gitignored - so reading it from the repo root was this suite's first failure
        # on a clean clone (FileNotFoundError, measured 2026-09-26) while every check
        # below grades real behaviour. Stage the fixture the repo DOES ship instead: the
        # same shape the parser reads, one entry per signature asserted here.
        lib = hermetic.field_notes_fixture()
        (workdir / "field-notes.md").write_text(lib.read_text(encoding="utf-8"),
                                                encoding="utf-8")
        fb._FIELD_NOTES_CACHE["mtime"] = None
        entries = fb.field_notes()
        check(len(entries) >= 10, f"the notes library parses ({len(entries)} entries)")
        titles = [e["title"] for e in entries]
        check(any("dpkg" in t for t in titles), "the dpkg entry is parsed")
        for e in entries:
            check(bool(e["match"]) and bool(e["note"]),
                  f"entry has both a signature and a note: {e['title'][:40]}")

        # only failures get a note
        ok_out = "exit_code=0\nCould not get lock /var/lib/dpkg/lock-frontend in a log line"
        check(fb.match_field_notes(ok_out) == [],
              "a successful result never gets a field note, even if the text matches")
        check(fb.failed_output("exit_code=0\nfine") is False, "exit_code=0 is not a failure")
        check(fb.failed_output("exit_code=1\nboom") is True, "exit_code=1 is a failure")
        check(fb.failed_output("ERROR: /x does not exist") is True, "an ERROR result is a failure")
        check(fb.failed_output("TIMEOUT after 5s") is True, "a TIMEOUT result is a failure")
        check(fb.failed_output("exit_code=0\n--- stderr ---\nwarning only") is False,
              "stderr with a zero exit code is not treated as a failure")

        # signature match, respecting scope: notes only fire on the platform they
        # were written for, and an unscoped note fires everywhere
        fail = "exit_code=100\nE: Could not get lock /var/lib/dpkg/lock-frontend"
        hits = fb.match_field_notes(fail)
        if fb._platform_tag() == "linux":
            check(len(hits) == 1 and "dpkg" in hits[0]["title"],
                  "the dpkg signature fires on the real error text")
            check("fuser" in hits[0]["note"], "the note carries the fix, not just the cause")
        else:
            check(hits == [], "a linux-scoped note does not fire on another platform")

        anyfail = "exit_code=1\ncp: no such file or directory"
        hits = fb.match_field_notes(anyfail)
        check(len(hits) == 1 and "space" in hits[0]["note"],
              "an any-scoped note fires and carries its fix")

        # scope filtering
        win = "exit_code=1\n'x' is not recognized as the name of a cmdlet, function"
        hits = fb.match_field_notes(win)
        check(all(e["scope"] in ("any", fb._platform_tag()) for e in hits),
              "only entries for this platform fire")
        if fb._platform_tag() == "windows":
            check(bool(hits) and "PATH" in hits[0]["note"],
                  "the background-PATH note fires on Windows with its fix")

        # cap
        multi = ("exit_code=1\nunknown option -- foo\n"
                 "Could not get lock /var/lib/dpkg/lock-frontend\n"
                 "cannot connect to the Docker daemon\n"
                 "dubious ownership in repository\n")
        hits = fb.match_field_notes(multi)
        check(len(hits) <= cfg.get("field_notes_max", 2),
              f"notes are capped at field_notes_max ({len(hits)})")

        # annotation shape (uses an any-scoped note so it fires on every platform)
        ann = fb.annotate_failure("shell", {}, anyfail)
        check(ann.startswith(anyfail) and "[HARNESS field note —" in ann,
              "annotate_failure appends the note below the output")
        check("source:" in ann, "the appended note names where it came from")
        # Rights denials (added 2026-09-13, from a console session that reported a Windows
        # refusal as a mystery). The narrow signatures fire; the AMBIGUOUS one must not, because
        # a bare "Access is denied" also means a locked file or an ACL, and a wrong hint costs
        # a small model more than no hint.
        win_denied = ("exit_code=1\n--- stderr ---\nNew-EventLog : The requested operation "
                      "requires elevation.\nAt line:1 char:1")
        # The note library is scope-filtered, so a Windows-scoped entry cannot fire on
        # POSIX. Assert the NOTE where it can fire, and assert the POSIX counterpart
        # there instead, rather than reporting a Windows-only expectation as a failure.
        if fb._platform_tag() == "windows":
            hits = fb.match_field_notes(win_denied)
            check(any("not elevated" in h["note"] or "administrator" in h["note"] for h in hits),
                  f"a Windows elevation refusal gets the rights note ({[h['title'] for h in hits]})")
        else:
            check(fb.match_field_notes(win_denied) == [],
                  "a Windows-scoped note does not fire on POSIX")
            sudo_hits = fb.match_field_notes(f"exit_code=1\nsudo: a password is required")
            check(any("sudo" in h["title"].lower() for h in sudo_hits),
                  f"a sudo password prompt gets its note here ({[h['title'] for h in sudo_hits]})")
        check(fb.looks_like_rights_denial(win_denied),
              "and the harness recognises it as a rights problem, so the facts are re-sent")
        ambiguous = "exit_code=1\n--- stderr ---\nGet-Content: Access is denied."
        check(not fb.looks_like_rights_denial(ambiguous),
              "a bare 'Access is denied' is NOT treated as a rights problem (it can be a lock)")
        sudo_denied = "sudo: a password is required"
        check(fb.looks_like_rights_denial(sudo_denied),
              "a sudo password prompt is recognised on POSIX")
        check(not fb.looks_like_rights_denial("exit_code=0\nwrote 12 lines"),
              "and a successful result is not")
        check(fb.annotate_failure("shell", {}, "exit_code=0\nall good")
              == "exit_code=0\nall good",
              "annotate_failure is a no-op on a successful result")

        # a broken library must not break a run
        (workdir / "field-notes.md").write_text(
            "## broken\nmatch: [unclosed\nnote: still fine\n", encoding="utf-8")
        fb._FIELD_NOTES_CACHE["mtime"] = None
        check(fb.match_field_notes("exit_code=1\n[unclosed bracket") == [],
              "an invalid regex in the library is ignored, not raised")
        (workdir / "field-notes.md").unlink()
        fb._FIELD_NOTES_CACHE["mtime"] = None
        check(fb.field_notes() == [], "a missing library returns no notes")
        check(fb.match_field_notes("exit_code=1\nanything") == [],
              "with no library, a failure is unchanged")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all digest/field-note checks passed")


if __name__ == "__main__":
    main()
