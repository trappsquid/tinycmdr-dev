"""test_state_surface - one merged suite (test_atomic_write, test_state_damage).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: test_atomic_write: globals()-> _ns.
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


def _suite_test_atomic_write():
    """atomic_write_text / atomic_write_bytes: never truncate, always tell the caller.

Run:  python tests/test_state_surface.py, or
      python tests/run_all.py --filter atomic_write
Not pytest, deliberately: `check()` records a failure and the suite's exit code is the
verdict (0 pass / 1 fail), so pytest would report this file green whatever the checks said.

Every failure inside `atomic_write_text` - the temp write, the fsync or the
rename - fell back to `p.open("w", ...)` on the DESTINATION, so a failed save truncated the
file the function exists to protect (measured: a 20-item state file -> 0 bytes, next load died
on JSONDecodeError, no .damaged-* copy). These cases pin the replacement's promises: the
old file survives a failed rename and a failed write, the error reaches the caller, no temp
is left behind, and the temp carries the destination's own mode.
"""
    import importlib.util
    import json
    import os
    import shutil
    import stat
    import sys
    import tempfile
    import atexit
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / (os.environ.get("TINYCMDR_TEST_APP")
                  or os.environ.get("TINYCMDR_SRC") or "tinycmdr.py")
    # Import a STAGED copy, not the checkout's own file. The module writes into BASE_DIR while
    # it is being imported - it creates sessions/ and its log before a suite gets a chance to
    # rebind anything - so importing the repo's file plants those in the repo. run_all.py's leak
    # report named sessions/ for this suite (CI, ubuntu, 2026-09-27); other suites stage for
    # the same reason.
    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-atomic"
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, STAGE / "tinycmdr.py")
    spec = importlib.util.spec_from_file_location("tinycmdr_atomic_under_test",
                                                  STAGE / "tinycmdr.py")
    fb = importlib.util.module_from_spec(spec)
    sys.modules["tinycmdr_atomic_under_test"] = fb
    spec.loader.exec_module(fb)

    TMP = Path(tempfile.mkdtemp(prefix="fbatomic-"))
    atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))
    sys.path.insert(0, str(BASE / "tests"))
    import hermetic                                                          # noqa: E402
    # Importing the tree's own tinycmdr.py hands it BASE_DIR = this checkout, so its sessions
    # and state files land in the repo unless they are moved first: run_all.py's leak report
    # named sessions/ for this suite (CI, ubuntu, 2026-09-27).
    hermetic.redirect_repo_files(fb, TMP)
    FAILURES = []
    PASSES = []


    def check(name, cond, detail=""):
        if cond:
            PASSES.append(name)
        else:
            FAILURES.append(f"{name}: {detail}")
            print(f"FAIL {name}: {detail}")


    def _temps(target):
        return sorted(p.name for p in target.parent.glob(target.name + ".tmp-*"))


    def test_a_failed_rename_keeps_the_old_file_and_raises():
        target = TMP / "rename-denied.json"
        original = '{"items": [' + ", ".join('"i%d"' % i for i in range(20)) + "]}"
        target.write_text(original, encoding="utf-8")
        real_replace = fb.os.replace

        def deny(src, dst, *a, **kw):
            raise PermissionError(5, "Access is denied")

        fb.os.replace = deny
        raised = None
        try:
            fb.atomic_write_text(target, '{"items": []}')
        except Exception as e:                                       # noqa: BLE001
            raised = e
        finally:
            fb.os.replace = real_replace
        check("a denied rename raises instead of pretending to save",
              raised is not None, raised)
        check("...the old file is byte-identical (20 items survive)",
              target.read_text(encoding="utf-8") == original,
              repr(target.read_text(encoding="utf-8")[:80]))
        check("...and no temp file is left behind", not _temps(target), _temps(target))


    def test_a_failed_write_keeps_the_old_file_and_raises():
        target = TMP / "write-denied.json"
        original = '{"items": ["kept"]}'
        target.write_text(original, encoding="utf-8")
        real_fsync = fb.os.fsync
        fb.os.fsync = lambda _fd: (_ for _ in ()).throw(
            OSError(28, "No space left on device"))
        raised = None
        try:
            fb.atomic_write_text(target, '{"items": []}')
        except Exception as e:                                       # noqa: BLE001
            raised = e
        finally:
            fb.os.fsync = real_fsync
        check("a failed flush raises instead of truncating the destination",
              raised is not None, raised)
        check("...and the old file is byte-identical",
              target.read_text(encoding="utf-8") == original,
              repr(target.read_text(encoding="utf-8")[:80]))
        check("...and the temp is cleaned up", not _temps(target), _temps(target))
        # The same failure through a production caller: the file keeps what it had.
        env = TMP / ".env"
        env.write_text("KEEP=1\n", encoding="utf-8")
        fb.ENV_FILE = env
        fb.os.fsync = lambda _fd: (_ for _ in ()).throw(OSError(28, "disk gone"))
        try:
            fb._env_set("ADDED", "2")
            check("a failed env write reports the failure", False, "it returned as if it saved")
        except OSError:
            check("a failed env write reports the failure", True)
        finally:
            fb.os.fsync = real_fsync
        check("...and .env still holds exactly what it had",
              env.read_text(encoding="utf-8") == "KEEP=1\n",
              repr(env.read_text(encoding="utf-8")))


    def test_the_mode_of_the_destination_is_preserved():
        if os.name == "nt":
            print("  (mode case skipped on Windows: no POSIX mode bits)")
        else:
            for mode in (0o600, 0o644, 0o755):
                p = TMP / ("mode-%04o.txt" % mode)
                p.write_text("old", encoding="utf-8")
                os.chmod(p, mode)
                fb.atomic_write_text(p, "new")
                got = stat.S_IMODE(p.stat().st_mode)
                check("an existing %04o file is still %04o after a write" % (mode, mode),
                      got == mode, oct(got))
                check("...and holds the new text",
                      p.read_text(encoding="utf-8") == "new", p.read_text(encoding="utf-8"))
        fresh = TMP / "fresh-state.json"
        fb.atomic_write_text(fresh, "{}")
        if os.name != "nt":
            got = stat.S_IMODE(fresh.stat().st_mode)
            check("a NEW state file is 0600, not the umask's 0644",
                  got == 0o600, oct(got))


    def test_bytes_go_in_verbatim():
        p = TMP / "bytes.bin"
        data = bytes(range(256))
        fb.atomic_write_bytes(p, data)
        check("atomic_write_bytes writes the bytes exactly",
              p.read_bytes() == data, repr(p.read_bytes()[:20]))
        crlf = TMP / "crlf.txt"
        fb.atomic_write_text(crlf, "a\r\nb\r\n")
        check("a caller's newline convention is not translated",
              crlf.read_bytes() == b"a\r\nb\r\n", repr(crlf.read_bytes()))


    def test_a_symlink_is_followed_not_replaced():
        """A-2026-10-08-97: os.replace swaps the directory entry, so an edit through a
    symlink (stow/chezmoi dotfiles, /etc/alternatives-style links) replaced the LINK
    with a regular file - the real target untouched, the owner/ACL/hard links/xattrs
    lost - while write_file followed it. The two doors disagreed about one path."""
        target = TMP / "symlink-target.txt"
        link = TMP / "symlink-managed.txt"
        target.write_bytes(b"old bytes\n")
        try:
            link.unlink()
        except OSError:
            pass
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as e:
            print(f"skip symlinks are not available here: {e}")
            return
        fb.atomic_write_bytes(link, b"new bytes\n")
        check("the link is still a symlink", link.is_symlink())
        check("...and the real target received the bytes",
              target.read_bytes() == b"new bytes\n", target.read_bytes())


    def main():
        tests = [v for k, v in sorted(_ns.items()) if k.startswith("test_")]
        for t in tests:
            print(f"--- {t.__name__}")
            try:
                t()
            except Exception as e:
                FAILURES.append(f"{t.__name__} raised: {type(e).__name__}: {e}")
                print(f"FAIL {t.__name__} raised: {type(e).__name__}: {e}")
        print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
        return 1 if FAILURES else 0
    _ns = dict(locals())
    return main()


def _suite_test_state_damage():
    """Damaged state files are kept aside, not silently forgotten.

state.json, logs/state.json (the lane failure record), web-sessions.json and jobs.json
are the host's durable memory. Every reader used to turn a corrupt or truncated file into {}
in silence, and the next save overwrote the only evidence. This
suite stages the module, points the four paths into a temp dir, and grades the
quarantine: bytes kept in a `.damaged-*` copy, the document empty, and a healthy file
untouched.

    python tests/test_state_surface.py
"""
    import importlib.util
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    FAILS = []


    def check(name, cond, detail=""):
        if cond:
            print(f"ok   {name}")
        else:
            FAILS.append(name)
            print(f"FAIL {name}: {detail}")


    def main():
        work = Path(tempfile.mkdtemp(prefix="tc-state-"))
        try:
            shutil.copy2(SRC, work / "tinycmdr.py")
            shutil.copy2(BASE / "tests" / "fixture-config.json", work / "config.json")
            spec = importlib.util.spec_from_file_location("tc_state_damage",
                                                          work / "tinycmdr.py")
            fb = importlib.util.module_from_spec(spec)
            sys.modules["tc_state_damage"] = fb
            spec.loader.exec_module(fb)

            # ---- state.json ----------------------------------------------------
            st_file = work / "state.json"
            fb.GLOBAL_STATE_FILE = st_file
            st_file.write_text("{not json", encoding="utf-8")
            got = fb._state()
            copies = sorted(work.glob("state.json.damaged-*"))
            check("a corrupt state.json reads as empty", got == {}, got)
            check("...and its bytes are kept in a .damaged copy", len(copies) == 1,
                  [p.name for p in work.iterdir()])
            check("...with the original content",
                  bool(copies) and copies[0].read_text(encoding="utf-8") == "{not json")
            st_file.write_text('{"model_overrides": {"x": "m"}}', encoding="utf-8")
            got = fb._state()
            check("a healthy state.json is read, and no second copy is made",
                  got.get("model_overrides") == {"x": "m"}
                  and len(sorted(work.glob("state.json.damaged-*"))) == 1, got)

            # ---- the lane failure record ---------------------------------------
            lane = work / "lane-state.json"
            fb.LANE_STATE_FILE = lane
            lane.write_text("[1,2,3]", encoding="utf-8")      # valid JSON, wrong shape
            check("a lane record of the wrong shape reads as empty (no copy)",
                  fb._lane_state_read() == {}
                  and not list(work.glob("lane-state.json.damaged-*")))
            lane.write_text("truncated {", encoding="utf-8")
            check("a corrupt lane record reads as empty", fb._lane_state_read() == {})
            check("...and is kept aside",
                  len(sorted(work.glob("lane-state.json.damaged-*"))) == 1,
                  [p.name for p in work.iterdir()])

            # ---- the conversation registry -------------------------------------
            web = work / "web-sessions.json"
            fb.WEB_STATE_FILE = web
            web.write_text("}{", encoding="utf-8")
            got = fb._web_state()
            check("a corrupt registry reads with empty collections",
                  got.get("sessions") == [] and got.get("open") == {}, got)
            check("...and is kept aside",
                  len(sorted(work.glob("web-sessions.json.damaged-*"))) == 1)

            # ---- the schedule ----------------------------------------------------
            # The one state file this suite did not cover, because the scheduler carried its
            # own silent reader: it answered {} for anything unreadable, with no log line and
            # no copy, and the next save took the only evidence of every job (run
            # 17, A-2026-10-07-08).
            jobs = work / "jobs.json"
            fb.JOBS_FILE = jobs
            jobs.write_text("}{ not json", encoding="utf-8")
            sched = fb.Scheduler(jobs)
            sched._stop.set()
            check("a corrupt jobs.json reads as an empty schedule", sched.jobs == {},
                  sched.jobs)
            jcopies = sorted(work.glob("jobs.json.damaged-*"))
            check("...and its bytes are kept in a .damaged copy", len(jcopies) == 1,
                  [p.name for p in work.iterdir()])
            check("...with the original content",
                  bool(jcopies) and jcopies[0].read_text(encoding="utf-8") == "}{ not json")
            # ---- two corruptions inside one second do not share a copy -------------
            # The stamp has one-second resolution and copy2 overwrites, so two readers hitting
            # the same corrupt file in the same second (the service and a --once run both load
            # state.json at start) left ONE copy - whichever ran second, which is the least
            # interesting one when a restart loop keeps re-reading a wedged file.
            collide = work / "state-collide.json"
            fb.GLOBAL_STATE_FILE = collide
            collide.write_text("{first", encoding="utf-8")
            fb._state()
            collide.write_text("{second", encoding="utf-8")
            fb._state()
            kept = sorted(work.glob("state-collide.json.damaged-*"))
            held = {p.read_text(encoding="utf-8") for p in kept}
            check("a second corruption in the same second gets its own copy",
                  len(kept) == 2, [p.name for p in kept])
            check("...and neither copy overwrote the other",
                  "{first" in held and "{second" in held, held)

            # ---- retention is bounded, newest kept --------------------------------
            many = work / "state-many.json"
            fb.GLOBAL_STATE_FILE = many
            for i in range(fb._DAMAGED_KEEP + 3):
                many.write_text("{bad%d" % i, encoding="utf-8")
                fb._state()
            all_copies = sorted(work.glob("state-many.json.damaged-*"))
            check("quarantine retention is bounded (at most %d per state file)"
                  % fb._DAMAGED_KEEP, len(all_copies) == fb._DAMAGED_KEEP,
                  [p.name for p in all_copies])
            check("...and it is the newest copies that are kept",
                  [p.name for p in all_copies] == [p.name for p in
                                                   sorted(all_copies)[-fb._DAMAGED_KEEP:]],
                  [p.name for p in all_copies])
            check("...so the oldest is the one dropped",
                  not any("{bad0" in p.read_text(encoding="utf-8") for p in all_copies))
        finally:
            shutil.rmtree(work, ignore_errors=True)

        print()
        if FAILS:
            print(f"{len(FAILS)} check(s) failed")
            return 1
        print("all state damage checks passed")
        return 0
    return main()


def main():
    rc = 0
    for name, fn in (("test_atomic_write", _suite_test_atomic_write), ("test_state_damage", _suite_test_state_damage)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
