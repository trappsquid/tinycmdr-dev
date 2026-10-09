"""test_log_config_surface - one merged suite (test_file_log, test_config_guards).

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


def _suite_test_file_log():
    """A file log that cannot write a record says so - and keeps the record when a rollover is
blocked.

on a Windows install: a refused secret verb's line
appeared on the console and never in the file, three calls in a row, with nothing saying
why. Two Windows facts make that shape: a rollover RENAMES the log, and an open handle
(the running bot holds this very file) makes the rename fail - and the exception died in
the QueueListener's thread, which reports nothing. The handler answers both: a blocked
rollover falls back to a plain append (losing the rotation is fine, losing the record is
not), and one stderr line names the file and the error. A WRITE failure reports through
the same hook - StreamHandler.emit swallows its own exceptions and calls handleError(),
so an except around super().emit() would be dead code (A-2026-10-08-141).

    python tests/test_log_config_surface.py        [TINYCMDR_SRC=/path/to/old/tinycmdr.py]
"""
    import importlib.util
    import io
    import logging
    import logging.handlers
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-filelog"

    FAILS = []


    def check(what, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
        if not ok:
            FAILS.append(what)


    def main():
        if STAGE.exists():
            shutil.rmtree(STAGE, ignore_errors=True)
        STAGE.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC, STAGE / "tinycmdr.py")
        shutil.copy2(BASE / "tests" / "fixture-config.json", STAGE / "config.json")
        spec = importlib.util.spec_from_file_location("tinycmdr_filelog", STAGE / "tinycmdr.py")
        fb = importlib.util.module_from_spec(spec)
        sys.modules["tinycmdr_filelog"] = fb
        try:
            spec.loader.exec_module(fb)
        except Exception as e:                                    # noqa: BLE001
            print("FAIL the staged module loads: %s" % e)
            return 1

        work = Path(tempfile.mkdtemp(prefix="fbflog-"))
        try:
            log = work / "tinycmdr.log"
            handler = fb._LoudRotatingFileHandler(str(log), maxBytes=300, backupCount=1,
                                                  encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            rec = logging.LogRecord("t", logging.INFO, __file__, 1, "first line", (), None)
            handler.handle(rec)
            check("a normal record lands", "first line" in log.read_text(encoding="utf-8"))

            # A rollover is what Windows refuses while the running bot holds the file.
            saved = logging.handlers.RotatingFileHandler.doRollover

            def blocked(self):
                raise PermissionError("the file is open elsewhere")

            logging.handlers.RotatingFileHandler.doRollover = blocked
            captured = io.StringIO()
            saved_stderr, sys.stderr = sys.stderr, captured
            try:
                big = logging.LogRecord("t", logging.INFO, __file__, 1, "R" * 400, (), None)
                handler.handle(big)
            finally:
                sys.stderr = saved_stderr
                logging.handlers.RotatingFileHandler.doRollover = saved
            text = log.read_text(encoding="utf-8")
            check("a blocked rollover still keeps the record (append fallback)",
                  ("R" * 400) in text, text[-80:])
            check("...and says why, once, on stderr",
                  "could not roll over" in captured.getvalue()
                  and "the file is open elsewhere" in captured.getvalue(),
                  captured.getvalue()[:200])
            handler.handle(logging.LogRecord("t", logging.INFO, __file__, 1, "second", (), None))
            check("...and only once (a per-record warning would be noise)",
                  captured.getvalue().count("WARNING") == 1, captured.getvalue())

            # A WRITE failure - the handle died under the running bot - must reach the same
            # fallback. StreamHandler.emit catches its own write errors and calls handleError,
            # so an except around super().emit() never ran: pre-fix this record is LOST and
            # stderr gets the stdlib's per-record traceback instead of the one line
            # (A-2026-10-08-141). A fresh handler, because the warn-once flag is per instance.
            log2 = work / "tinycmdr2.log"
            handler2 = fb._LoudRotatingFileHandler(str(log2), maxBytes=300, backupCount=1,
                                                   encoding="utf-8")
            handler2.setFormatter(logging.Formatter("%(message)s"))
            handler2.handle(logging.LogRecord("t", logging.INFO, __file__, 1, "alive", (), None))
            handler2.stream.close()                       # the handle dies mid-flight
            captured2 = io.StringIO()
            saved_stderr, sys.stderr = sys.stderr, captured2
            try:
                handler2.handle(logging.LogRecord("t", logging.INFO, __file__, 1,
                                                  "after the handle died", (), None))
            finally:
                sys.stderr = saved_stderr
            check("a write failure keeps the record (fresh handle, append fallback)",
                  "after the handle died" in log2.read_text(encoding="utf-8"),
                  log2.read_text(encoding="utf-8")[-80:])
            check("...and says why once, as the one line - not the stdlib traceback",
                  "could not be written" in captured2.getvalue()
                  and "--- Logging error ---" not in captured2.getvalue(),
                  captured2.getvalue()[:200])

            if FAILS:
                print("\n%d FAILED: %s" % (len(FAILS), "; ".join(FAILS)))
                return 1
            print("\nall file-log checks passed")
            return 0
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return main()


def _suite_test_config_guards():
    """A wrong TYPE in config.json cannot kill the load or blank a shipped default.

`.get(key, default)` supplies the default only for a MISSING key, never for one that is
present and null - so `"agent": {"max_minutes": null}` reached `None * 60` and raised
TypeError from inside the run loop, naming neither the key nor the file (measured
2026-10-07). A null is an easy thing to ship: a config.json written by a script, a key
commented out by setting it to null, an installer template with an unfilled placeholder.
The section-level guard already stops a non-dict SECTION (`"agent": null` killed startup
on 2026-10-05); this grades the same class one level down.

Three wrong-type shapes, each graded here because each one KILLED the import before a
warning could be printed: a null for a key the run loop does arithmetic on, a non-object
`search` section read by the legacy-key sweep, and a non-dict `llm.fallbacks` entry read
by the api_key_env sweep (the last two, A-2026-10-08-135/-136, evidence: reproduced).

    python tests/test_log_config_surface.py

Falsification: with TINYCMDR_SRC=<pre-fix build> the merged value IS None and the keys the
run loop does arithmetic on are the ones that crash it; a pre-fix build fails the two
wrong-type cases with AttributeError at load.
"""
    import importlib.util
    import json
    import logging
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")
    FAILS = []

    # The keys the run loop does ARITHMETIC on, and what the shipped default is: the value a
    # null must fall back to (DEFAULT_CONFIG, not a number written here).
    GUARD_KEYS = (("agent", "max_minutes"), ("agent", "max_steps"), ("agent", "stall_warn_minutes"))


    def check(name, cond, detail=""):
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  <- %s" % (detail,)))
        if not cond:
            FAILS.append(name)


    def main():
        work = Path(tempfile.mkdtemp(prefix="tc-config-guards-"))
        try:
            shutil.copy2(SRC, work / "tinycmdr.py")
            shutil.copy2(BASE / "tests" / "fixture-config.json", work / "config.json")
            cfg = json.loads((work / "config.json").read_text(encoding="utf-8"))
            for section, key in GUARD_KEYS:
                cfg.setdefault(section, {})[key] = None
            cfg["agent"]["not_a_shipped_key"] = None      # a null must not become a default
            cfg["search"] = "off"                         # a non-object section (A-135)
            cfg["llm"]["fallbacks"] = ["http://x",        # non-dict entries (A-136)...
                                       {"base_url": "http://127.0.0.1:2/v1", "model": "fb"}]
            (work / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

            spec = importlib.util.spec_from_file_location("tc_config_guards",
                                                          work / "tinycmdr.py")
            fb = importlib.util.module_from_spec(spec)
            sys.modules["tc_config_guards"] = fb
            try:
                spec.loader.exec_module(fb)
            except Exception as e:                        # noqa: BLE001
                print("FAIL the staged module loads: %s: %s" % (type(e).__name__, e))
                return 1

            said = []

            class _Grab(logging.Handler):
                def emit(self, record):
                    try:
                        said.append(record.getMessage())
                    except Exception:
                        pass

            grab = _Grab()
            fb.log.addHandler(grab)
            try:
                merged = fb.load_config()
            finally:
                fb.log.removeHandler(grab)

            for section, key in GUARD_KEYS:
                shipped = fb.DEFAULT_CONFIG[section].get(key)
                got = merged[section].get(key)
                check("%s.%s falls back to the shipped default" % (section, key),
                      got == shipped and got is not None, (got, shipped))
                check("...and said so, naming the key",
                      any("%s.%s is null" % (section, key) in m for m in said),
                      [m for m in said if "null" in m][:3])
            check("a null for a key nothing ships is dropped, not invented",
                  "not_a_shipped_key" not in merged["agent"], merged["agent"].get("not_a_shipped_key"))
            check("a non-object search section keeps the shipped default, named",
                  isinstance(merged["search"], dict)
                  and any("section 'search' is str" in m for m in said),
                  (type(merged["search"]).__name__, [m for m in said if "search" in m][:2]))
            check("non-dict fallback entries are dropped, the dict entry is kept",
                  len(merged["llm"]["fallbacks"]) == 1
                  and merged["llm"]["fallbacks"][0].get("model") == "fb",
                  merged["llm"]["fallbacks"])
            check("...and the drop is named, once per entry kind",
                  any("llm.fallbacks: 1 non-object entry ignored" in m for m in said),
                  [m for m in said if "fallbacks" in m][:2])
            check("the load still answers a dict for every shipped section",
                  all(isinstance(merged.get(s), dict) for s in fb.DEFAULT_CONFIG), sorted(merged))
        finally:
            shutil.rmtree(work, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed: %s" % (len(FAILS), ", ".join(FAILS)))
            return 1
        print("all config-guard checks passed")
        return 0
    return main()


def main():
    rc = 0
    for name, fn in (("test_file_log", _suite_test_file_log), ("test_config_guards", _suite_test_config_guards)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
