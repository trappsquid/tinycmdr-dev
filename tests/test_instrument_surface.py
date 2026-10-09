"""test_instrument_surface - one merged suite (test_catchup, test_digest, test_experiment).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: test_catchup: globals()-> _ns.
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


def _suite_test_catchup():
    """The catch-up sweep: a message posted while the bot was DOWN.

Run:  python tests/test_instrument_surface.py            (all tests)
      python tests/test_instrument_surface.py <substring> (one test)

Why this suite exists. The sweep's own docstring names the case it is for - "a message
posted during a gap (or while the bot was restarting) is silently ignored" - but it walks
the in-memory high-water map, and every new process starts with an empty one. So it had
no channel to ask about until something arrived over the websocket, which is exactly what
a post made inside a downtime never does. Measured on Windows and macOS
2026-09-24: a restart was armed, the child was killed, an order was posted while it was
down, and no run started, no answer was posted and nothing was logged. The operator's
opinion of that is "the bot ate my message".

It runs against a fake dispatcher and a fake driver, like tests/test_interaction_surface.py: no
network, no model, no durable state.
"""
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    import threading
    import time
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-catchup"
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    FIXTURE = Path(__file__).resolve().parent / "fixture-config.json"
    if not FIXTURE.exists():
        sys.exit(f"missing test fixture: {FIXTURE}")
    shutil.copy2(FIXTURE, STAGE / "config.json")

    spec = importlib.util.spec_from_file_location("tinycmdr_under_test_catchup",
                                                  STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_under_test_catchup"] = fb
    spec.loader.exec_module(fb)

    STATE = STAGE / "state.json"
    fb.GLOBAL_STATE_FILE = STATE
    fb.CONFIG["agent"]["catch_up_seconds"] = 60
    fb.CONFIG["agent"]["catch_up_max_minutes"] = 30
    fb.CONFIG["mattermost"]["allowed_users"] = ["alice"]

    PASSES, FAILURES = [], []


    def check(name, cond, detail=""):
        if cond:
            PASSES.append(name)
            print(f"ok   {name}")
        else:
            FAILURES.append(f"{name}: {detail}")
            print(f"FAIL {name}: {detail}")


    class FakeMessage:
        def __init__(self, channel_id, text, sender="alice", user_id="u-alice",
                     create_at=None, msg_id=None):
            self.channel_id = channel_id
            self.sender_name = sender
            self.user_id = user_id
            self.create_at = int((create_at or time.time()) * 1000)
            self.message = text
            self.id = msg_id or f"post-{self.create_at}"


    class FakeThread:
        def __init__(self):
            self.calls = []
            self.posts = {"chan-A": [{"id": "p-down", "channel_id": "chan-A",
                                      "message": "an order posted while the "
                                                 "bot was down",
                                      "user_id": "alice",
                                      "create_at": int((time.time() - 30) * 1000)}]}

        def get_posts_for_channel(self, channel_id, params=None):
            self.calls.append((channel_id, dict(params or {})))
            posts = [p for p in self.posts.get(channel_id, [])
                     if p["create_at"] >= int((params or {}).get("since") or 0)]
            return {"order": [p["id"] for p in posts],
                    "posts": {p["id"]: p for p in posts}}


    class FakeDriver:
        def __init__(self):
            self.posts = FakeThread()
            self.users = self

        def get_user_by_username(self, name):
            return {"id": "u-bot", "username": name}


    class FakeDispatcher(fb.MattermostDispatcher):
        """No network, no posting: records what the sweep handed to the normal path."""

        def __init__(self):
            super().__init__()
            self.handled = []

        def _post(self, channel_id, root_id, text, color=None, draft_id=None):
            return "post"

        def _handle(self, channel_id, sender, text, msg_id, thread_root, is_dm, gen=0):
            self.handled.append((channel_id, text))


    def write_state(blob):
        STATE.write_text(json.dumps(blob), encoding="utf-8")


    def test_a_restart_restores_the_channels_the_sweep_must_ask_about():
        write_state({"last_seen": {"chan-A": 1000.0},
                     "announce_restart": {"channel_id": "chan-B", "at": 2000.0}})
        d = FakeDispatcher()
        check("restart: the carried channel comes back with its time",
              d.last_seen.get("chan-A") == 1000.0, d.last_seen)
        check("restart: the channel that ASKED for the restart is a floor too",
              d.last_seen.get("chan-B") == 2000.0, d.last_seen)


    def test_a_fresh_process_with_no_state_sweeps_nothing():
        write_state({})
        d = FakeDispatcher()
        check("fresh process: nothing remembered", d.last_seen == {}, d.last_seen)


    def test_the_high_water_mark_is_persisted_and_the_map_is_bounded():
        write_state({})
        d = FakeDispatcher()
        d.enqueue(FakeMessage("chan-A", "hello", create_at=5000.0), "hello")
        on_disk = json.loads(STATE.read_text(encoding="utf-8"))
        check("enqueue: the channel is persisted",
              on_disk.get("last_seen", {}).get("chan-A") == 5000.0, on_disk)
        for i in range(25):
            d.enqueue(FakeMessage("chan-%02d" % i, "hi", create_at=6000.0 + i), "hi")
        seen = json.loads(STATE.read_text(encoding="utf-8")).get("last_seen", {})
        check("enqueue: the map stays bounded", len(seen) <= 20, len(seen))
        newest = max(seen, key=lambda k: seen[k])
        check("enqueue: the newest channel is kept", newest == "chan-24", newest)


    def test_the_sweep_recovers_a_post_made_while_the_process_was_down():
        base = time.time() - 300          # fixed, so the millisecond assertion is exact
        write_state({"last_seen": {"chan-A": base}})
        d = FakeDispatcher()
        d.driver = FakeDriver()
        d.bot_user_id = "u-bot"
        d._is_dm = lambda channel_id: True          # no channel lookup over the network
        recovered = d._catch_up_once()
        check("recovery: the missed post is recovered", recovered == 1, recovered)
        check("recovery: it goes through the normal path",
              d.handled and d.handled[0][0] == "chan-A", d.handled)
        asked = d.driver.posts.calls[-1][1]
        check("recovery: the query starts one millisecond after the high-water mark",
              asked.get("since") == int(base * 1000) + 1, asked)


    def test_the_sweep_uses_the_shared_allowlist_check():
        """The sweep must not re-implement the allowlist: a bare-string allowlist made the
    raw membership test always true (every recovered post silently dropped) and a null
    one raised every cycle."""
        base = time.time() - 300
        for shape, want in ((["alice"], True), ("alice", True), (None, False)):
            write_state({"last_seen": {"chan-A": base}})
            fb.CONFIG["mattermost"]["allowed_users"] = shape
            try:
                d = FakeDispatcher()
                d.driver = FakeDriver()
                d.bot_user_id = "u-bot"
                d._is_dm = lambda channel_id: True
                recovered = d._catch_up_once()
            finally:
                fb.CONFIG["mattermost"]["allowed_users"] = ["alice"]
            check("the sweep recovers through the shared allowlist check (%r)" % (shape,),
                  (recovered == 1) is want, recovered)


    def test_the_first_sweep_runs_before_the_first_sleep():
        """A restart is the case this sweep exists for: waiting a full interval first
    leaves the order that arrived during the downtime unanswered for a minute more."""
        write_state({"last_seen": {"chan-A": time.time() - 120}})
        d = FakeDispatcher()
        d.driver = FakeDriver()
        d.bot_user_id = "u-bot"
        order = []

        class _Stop(Exception):
            pass

        def fake_sleep(_secs):
            order.append("sleep")
            raise _Stop()

        real_sleep, real_once, real_flag = fb.time.sleep, d._catch_up_once, d.last_seen
        fb.time.sleep = fake_sleep
        d._catch_up_once = lambda *a, **k: (order.append("sweep"), 0)[1]
        try:
            t = threading.Thread(target=lambda: _run(d, _Stop), daemon=True)
            t.start()
            t.join(5)
        finally:
            fb.time.sleep = real_sleep
            d._catch_up_once = real_once
        check("startup: the sweep runs before the loop sleeps",
              order[:2] == ["sweep", "sleep"], order)

        d.last_seen = {}
        order.clear()
        fb.time.sleep = fake_sleep
        d._catch_up_once = lambda *a, **k: (order.append("sweep"), 0)[1]
        try:
            t = threading.Thread(target=lambda: _run(d, _Stop), daemon=True)
            t.start()
            t.join(5)
        finally:
            fb.time.sleep = real_sleep
            d._catch_up_once = real_once
            d.last_seen = real_flag
        check("startup: a process with nothing restored queries nothing",
              order and order[0] == "sleep", order)



    def test_an_already_answered_post_is_not_recovered_after_a_restart():
        """The high-water mark is a CLOCK (the live message object carries no create_at),
    and this box's clock runs behind the Mattermost server's - measured 334 ms on the
    day this was found, by watching an already-answered order come back as new work
    after a restart. Ids are the exact boundary; carry them."""
        base = time.time() - 60
        write_state({"last_seen": {"chan-A": base}, "seen_ids": ["p-down", "p-older"]})
        d = FakeDispatcher()
        check("restart: the handled ids come back",
              list(d.seen)[-2:] == ["p-down", "p-older"], list(d.seen))
        d.driver = FakeDriver()
        d.bot_user_id = "u-bot"
        d._is_dm = lambda channel_id: True
        check("restart: the already-answered post is not recovered again",
              d._catch_up_once() == 0, d.handled)
        d.enqueue(FakeMessage("chan-A", "a new order", msg_id="p-new"), "a new order")
        ids = json.loads(STATE.read_text(encoding="utf-8")).get("seen_ids")
        check("enqueue: the handled id is persisted", ids and ids[-1] == "p-new", ids)
        check("enqueue: the ids stay bounded", len(ids) <= 50, len(ids))


    def test_the_dedupe_set_does_not_grow_without_bound():
        write_state({"seen_ids": ["p%d" % i for i in range(200)]})
        d = FakeDispatcher()
        check("restore: at most 50 ids are carried", len(d.seen) <= 50, len(d.seen))

    def _run(d, stop):
        try:
            d._catch_up_loop()
        except stop:
            pass
        except Exception as e:                       # noqa: BLE001 - reported by the check
            print("   (loop ended: %r)" % (e,))


    def main():
        tests = [v for k, v in sorted(_ns.items())
                 if k.startswith("test_") and callable(v)]
        only = sys.argv[1] if len(sys.argv) > 1 else ""
        for t in tests:
            if only and only not in t.__name__:
                continue
            try:
                t()
            except Exception as e:
                import traceback
                FAILURES.append(f"{t.__name__} raised: {e}")
                traceback.print_exc()
        print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
        for f in FAILURES:
            print("  FAIL:", f)
        return 1 if FAILURES else 0
    _ns = dict(locals())
    return main()


def _suite_test_digest():
    """Offline checks for tool-result digestion and field notes.

No model calls, no network. Both features are pure functions of the text a tool
returned, which is exactly why they can be pinned down here: if these pass, a change
in behaviour is a change in the regexes, not in the mood of a model.

    python tests/test_instrument_surface.py
"""
    import importlib.util
    import contextlib
    import io
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

            # ...and the SHAPE is decided by the command actually being run, not by a string that
            # happens to appear in it. Measured 2026-09-29: `grep -rn "docker ps" docs/`
            # was shaped as a CONTAINER LIST because "docker ps" sat inside the grep PATTERN, so
            # the results were head/tail-trimmed and mislabelled; `cat ipconfig-notes.txt` was
            # shaped as network output because of its FILENAME.
            def shape(cmd):
                return fb._digest_shape(fb._digest_subject("shell", {"command": cmd}))

            check(shape('grep -rn "docker ps" docs/')[0] == "search results",
                  "a command that MENTIONS another shape is still itself")
            check(shape('bash -c "apt-get update && make build"') is None,
                  "  a quoted argument is an argument, not the command")
            check(shape('echo "run ps -ef to see"') is None,
                  "  and a mention in quotes does not shape the output")
            check(shape("cat ipconfig-notes.txt") is None,
                  "  a FILENAME is not a command either")
            for cmd, want in (("docker ps -a", "container list"),
                              ("ipconfig /all", "network"),
                              ("journalctl -u backupd", "journal"),
                              ("sudo journalctl -u backupd", "journal"),
                              ("cd /srv && ps aux", "process list"),
                              ("cat build.log", "log file"),
                              ("tail -n 50 /var/log/app.log", "log file")):
                got = shape(cmd)
                check(got and got[0] == want, f"  the real cases still work: {cmd} -> {got}")

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

            # A CONTENT tool's result is DATA, not a command's output, so its own text is not
            # evidence about whether the CALL failed. Measured 2026-09-29: a successful
            # read of a file containing a traceback was called a failure, so a field note and the
            # last-good-call replay were attached to a success - the thing this module's own
            # docstring forbids - and the working call was not remembered as the good shape.
            check(fb.failed_output("/w/run.py (lines 1-40 of 400)\n"
                                   "Traceback (most recent call last):\n  File x.py") is False,
                  "a file that CONTAINS a traceback is not a failed call")
            check(fb.failed_output("/w/build.log (lines 1-10 of 90)\nexit_code=1") is False,
                  "  nor is one that contains the text exit_code=1")
            check(fb.failed_output("/w/notes.md (lines 1-3 of 9)\n--- stderr ---") is False,
                  "  nor one that contains a stderr banner")
            check(fb.failed_output("exit_code=1\nboom\nTraceback (most recent call last):") is True,
                  "while a real SHELL result with a traceback still is")
            check(fb.failed_output("exit_code=0\nok\nTraceback (most recent call last):") is True,
                  "  and a command that PRINTED a traceback is too, even at exit 0")

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

            # ---- the counters: what fired, and what fired nothing ---------------------
            # The library's rule is "if an entry fires and does not help, delete it", which needs
            # a count - and there was none: nothing counted or logged a match on a REAL run
            # (measured 2026-09-30; only the eval harness counted fires, per run, with no idea
            # WHICH entry). Without it the library can only grow by hand and can only be pruned by
            # whoever remembers every failure it ever had.
            stats_file = workdir / "field-notes-hits.json"
            stats_file.unlink(missing_ok=True)
            check(not stats_file.exists(), "no counters until a failure is seen")

            full = "exit_code=1\nwrite failed: No space left on device"
            fb.match_field_notes(full)              # an any-scoped entry the library HAS
            hits = json.loads(stats_file.read_text(encoding="utf-8"))
            fired = hits.get("entries") or {}
            check(any("filesystem" in k for k in fired),
                  f"the entry that fired is counted by title ({fired})")
            check(bool(hits.get("since")),
                  f"the window is recorded: 'never fired' is a claim about a window ({hits})")
            check(not (hits.get("unmatched") or {}),
                  f"...and a failure that matched is not also a candidate ({hits.get('unmatched')})")

            novel_a = "exit_code=1\nbackup failed for /a/b/c at 12:00 pid 4711"
            novel_b = "exit_code=1\nbackup failed for /x/y/z at 09:30 pid 90210"
            fb.match_field_notes(novel_a)
            fb.match_field_notes(novel_b)
            cand = json.loads(stats_file.read_text(encoding="utf-8")).get("unmatched") or {}
            check(len(cand) == 1 and list(cand.values())[0]["fails"] == 2,
                  f"a failure with no entry becomes ONE candidate across paths and numbers ({cand})")

            before = stats_file.read_text(encoding="utf-8")
            fb.match_field_notes("exit_code=0\nall fine here")
            check(stats_file.read_text(encoding="utf-8") == before,
                  "a successful result is never counted as a failure")

            # Bookkeeping must never be able to break the run it measures: a counter that cannot
            # be written costs one tally, not a turn.
            real_stats = fb._field_notes_stats_path
            fb._field_notes_stats_path = lambda: Path("/proc/definitely/not/writable.json")
            try:
                unwritable = None
                try:
                    unwritable = fb.match_field_notes(full)
                except Exception as e:              # noqa: BLE001 - the point of the check
                    unwritable = f"raised {type(e).__name__}: {e}"
            finally:
                fb._field_notes_stats_path = real_stats
            check(isinstance(unwritable, list) and bool(unwritable),
                  f"a counter that cannot be written never breaks the run ({unwritable})")

            # The operator's half: which entries have EVER fired here, and what keeps failing
            # with no entry - the list a new entry is written from.
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = fb._verb_failures([])
            shown = buf.getvalue()
            check(rc == 0 and "known-failure library" in shown and "counting since" in shown
                  and "never fired since counting began" in shown,
                  f"the failures verb names the entries that never fire ({shown[:170]})")
            check("backup failed for p at n:n pid n" in shown,
                  f"...and the candidates an entry is written from ({shown[-260:]})")

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
    return main()


def _suite_test_experiment():
    """The experiment ledger: what this box has already TESTED (review, 2026-09-21).

The campaign harness re-ran arms it had already measured, and one verdict ("MTP = wash")
was retracted silently because nothing recorded that an earlier line had been superseded.
These checks pin the replacement: an append-only file, a prompt index that is not the
file, and a gate that refuses to re-buy a settled question while citing its verdict.

    python tests/test_instrument_surface.py
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


    def check(name, cond, detail=""):
        if cond:
            print(f"ok   {name}")
        else:
            FAILS.append(f"{name}: {detail}")
            print(f"FAIL {name}: {detail}")


    def main():
        workdir = Path(tempfile.mkdtemp(prefix="fbtest-exp-"))
        try:
            run_scenario.stage_install(workdir, 24000)
            fb = run_scenario.load(workdir)
            path = fb.EXPERIMENTS_FILE

            def lines():
                if not path.exists():
                    return []
                return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

            # ---- an empty ledger is honest about being empty -------------------------
            out = fb.tool_experiment({"action": "index"}, {})
            check("an empty ledger says so and says how to open one",
                  "empty" in out.lower() and "action=add" in out, out[:90])

            # ---- the fields are the a bot account schema ----------------------------------
            check("the record schema is theirs, verbatim (29 names)",
                  len(fb.EXPERIMENT_FIELDS) == 29
                  and fb.EXPERIMENT_FIELDS[:6] == ("id", "date", "agent", "status",
                                                   "question", "keys")
                  and "binary+commit" in fb.EXPERIMENT_FIELDS
                  and "fill_depth" in fb.EXPERIMENT_FIELDS
                  and "superseded_by" in fb.EXPERIMENT_FIELDS,
                  str(fb.EXPERIMENT_FIELDS))

            keys = ["mtp", "ctx38k"]
            cfg = "llama-server -np 2 --ctx-size 38912 -fa"
            out = fb.tool_experiment(
                {"action": "add", "question": "Does MTP pay off at 38k?",
                 "keys": keys, "exact_config": cfg,
                 "fields": {"preregistration": "MTP on should halve prefill",
                            "fill_depth": "8K fill",
                            "engine": "llama.cpp b6000",
                            "result": "1.02x prefill, 3 reps, spread 0.4%",
                            "verdict": "MTP is a wash at this fill depth",
                            "body": "two arms, 3 reps each, interleaved"}}, {})
            check("a first experiment is recorded", out.startswith("OK: experiment #1"),
                  out[:120])
            check("as ONE appended line", len(lines()) == 1, str(len(lines())))
            rec = json.loads(lines()[0])
            check("the record carries the fields it was given",
                  rec["fill_depth"] == "8K fill" and rec["verdict"].startswith("MTP is a wash")
                  and rec["keys"] == keys, json.dumps(rec))
            check("id, date, agent and status are filled in by the harness",
                  rec["id"] == 1 and rec["date"] and rec["agent"] and rec["status"] == "open")
            check("exact_config is stored literally, not summarised",
                  rec["exact_config"] == cfg)

            # ---- the prompt gets the INDEX, never the file ---------------------------
            block = fb.render_experiment_prompt()
            check("the prompt block carries id, question, keys and verdict",
                  "#1" in block and "mtp,ctx38k" in block
                  and "verdict: MTP is a wash" in block, block[:200])
            check("... and not the whole record (preregistration stays on disk)",
                  "preregistration" not in block)
            out = fb.tool_experiment({"action": "index"}, {})
            check("action=index lists it with a date", "#1 [" in out and rec["date"] in out,
                  out[:120])

            # ---- the GATE: a settled question is not re-bought -----------------------
            out = fb.tool_experiment({"action": "add", "question": "MTP again, to be sure",
                                      "keys": keys, "exact_config": cfg}, {})
            check("a repeat arm is REFUSED", out.startswith("REFUSED"), out[:90])
            check("and the earlier verdict is cited in the refusal",
                  "MTP is a wash" in out, out[:220])
            check("and the refusal names that line", "#1" in out)
            check("and nothing was written", len(lines()) == 1, str(len(lines())))

            out = fb.tool_experiment({"action": "add", "question": "MTP at a 4K fill",
                                      "keys": keys, "exact_config": cfg + " --fill 4k"}, {})
            check("same keys but a different exact_config is a DIFFERENT arm",
                  out.startswith("OK: experiment #2"), out[:90])

            out = fb.tool_experiment({"action": "add", "question": "MTP re-run, 5 reps",
                                      "keys": keys, "exact_config": cfg, "supersedes": 1,
                                      "fields": {"preregistration": "the old run was 1 rep"}}, {})
            check("naming the line it supersedes allows the re-run",
                  out.startswith("OK: experiment #3"), out[:90])
            check("and the result says what it superseded", "#1" in out and "MTP is a wash" in out,
                  out[:220])
            recs = [json.loads(l) for l in lines()]
            one = [r for r in recs if str(r.get("id")) == "1"][-1]
            check("the superseded line is marked by APPENDING, never by rewriting",
                  one.get("status") == "superseded" and one.get("superseded_by") == 3,
                  json.dumps(one))

            # ---- an update is an append too ------------------------------------------
            before = lines()
            out = fb.tool_experiment({"action": "update", "id": 2,
                                      "fields": {"status": "done",
                                                 "verdict": "MTP pays at a 4K fill"}}, {})
            check("an update lands", out.startswith("OK: experiment #2"), out[:90])
            check("the earlier lines are byte-identical afterwards",
                  lines()[:len(before)] == before)
            check("and the newest line wins on read",
                  "MTP pays at a 4K fill" in fb.tool_experiment({"action": "show", "id": 2}, {}))

            # ---- refusals that teach -------------------------------------------------
            out = fb.tool_experiment({"action": "update", "id": 2, "fields": {"nonsense": 1}}, {})
            check("an unknown field is refused BY NAME",
                  out.startswith("ERROR") and "nonsense" in out, out[:140])
            out = fb.tool_experiment({"action": "add", "question": "no config at all",
                                      "keys": ["x"]}, {})
            check("add demands the exact config, and says so",
                  out.startswith("ERROR") and "exact_config" in out, out[:160])
            out = fb.tool_experiment({"action": "show", "id": 99}, {})
            check("an unknown id names the way to list the ids",
                  out.startswith("ERROR") and "action=index" in out, out[:120])

            # ---- Item D: on-demand by default to save ~350 tokens/turn ---------------
            check("the experiment tool is available on-demand",
                  "experiment" in fb.hidden_tools(None))
            exp_s = [s for s in fb.REGISTRY.openai_schemas()
                     if s["function"]["name"] == "experiment"][0]
            check("and its schema is inside the per-tool cap",
                  len(json.dumps(exp_s)) <= 1200, str(len(json.dumps(exp_s))))
            v = fb.volatile_context(session_key=None)
            check("the ledger index is in the prompt block", "experiment ledger" in v,
                  v[-300:])

            # ---- an arm nobody came back to stops riding the prompt -------------------
            # The index is re-sent every call, and an `open` record kept its question, its
            # keys and its body in it for ever while a finished verdict read identically
            # (measured 2026-09-30: `#1 [open]` dated 2026-09-28 rode two days, 149 est
            # tokens, with nothing in the line to say it was never closed).
            abandoned = json.loads(lines()[0])
            abandoned.update({"id": 90, "status": "open", "verdict": "", "body": "",
                              "keys": ["abandoned"], "exact_config": "",
                              "question": "an arm nobody came back to",
                              "date": fb.time.strftime(
                                  "%Y-%m-%d", fb.time.localtime(fb.time.time() - 5 * 86400))})
            path.write_text(path.read_text(encoding="utf-8") + json.dumps(abandoned) + "\n",
                            encoding="utf-8")
            block = fb.render_experiment_prompt()
            check("an abandoned open arm renders as a marker, not its text",
                  "an arm nobody came back to" not in block and "#90 [open]" in block
                  and "never closed" in block, block[-300:])
            check("...and a record that is not stale still shows its question",
                  "Does MTP pay off at 38k?" in block, block[:300])
            check("...and action=index still prints the abandoned one in full",
                  "an arm nobody came back to" in fb.tool_experiment({"action": "index"}, {}),
                  "")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) failed")
            sys.exit(1)
        print("all experiment-ledger checks passed")
    return main()


def main():
    rc = 0
    for name, fn in (("test_catchup", _suite_test_catchup), ("test_digest", _suite_test_digest), ("test_experiment", _suite_test_experiment)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
