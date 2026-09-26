"""The management verbs: they answer, they never call the model, they never print a
secret (audit F12, 2026-09-22).

`tinycmdr status|doctor|model|logs|token` exist because day-two work used to mean
hand-editing .env and config.json. What has to hold: a verb is a management operation,
so no verb starts the agent loop or spends a token; a verb that reports on secrets names
them and never prints them; and a verb that fails says why on stderr with a non-zero exit,
so a script can act on it.

    python tests/test_verbs.py
"""
import contextlib
import io
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


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILS.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def call(fb, argv, stdin=None):
    """Run one verb, with both streams captured and stdin faked when asked."""
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = fb.run_verb(list(argv))
    finally:
        sys.stdin = saved
    return rc, out.getvalue(), err.getvalue()


def main():
    workdir = Path(tempfile.mkdtemp(prefix="fbtest-verbs-"))
    try:
        run_scenario.stage_install(workdir, 24000)
        fb = run_scenario.load(workdir)
        # A page-only install: no chat lane, so a missing Mattermost client is not a
        # problem for this box (which is also what keeps this suite interpreter-agnostic).
        cfg = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        cfg["mattermost"]["token"] = ""
        (workdir / "config.json").write_text(json.dumps(cfg, indent=2),
                                            encoding="utf-8")
        fb.CONFIG["mattermost"]["token"] = ""

        # nothing below may reach the model
        def boom(*a, **kw):
            raise AssertionError("a management verb called the model")
        fb.AGENT._chat = boom

        def boom_run(*a, **kw):
            raise AssertionError("a helper ran without the rights to run it")

        # ---- help and unknown verbs ------------------------------------------
        rc, out, err = call(fb, ["help"])
        check("help exits 0", rc == 0, rc)
        check("help lists the verbs", all(v in out for v in
                                          ("status", "doctor", "model", "logs",
                                           "restart", "token")), out[:200])
        check("help says restart goes through this host's own door",
              "systemd" in out and "task" in out, out[:300])
        rc, out, err = call(fb, ["tinycmdr"])
        check("an unknown verb exits 2 and shows the help", rc == 2 and "unknown verb" in err,
              (rc, err[:120]))

        # ---- the door has to be executable (measured 2026-09-25) -------------
        # A fleet macOS host answered "/usr/local/bin/tinycmdr: line 2: ... Permission
        # denied" for the user and for sudo: the shim was right, the file it execs was
        # 0644, because git cannot carry the execute bit out of a Windows checkout and
        # the update path trusted the checkout. A reader meets this door first.
        launcher = Path(fb.BASE_DIR) / "tinycmdr"
        launcher.write_text("#!/bin/sh\nexec \"$0.py\" \"$@\"\n", encoding="utf-8")
        os.chmod(launcher, 0o644)
        fix = getattr(fb, "ensure_launcher_executable", None)
        check("the update path ships a launcher fix-up", callable(fix))
        if callable(fix):
            fix()
            if os.name == "posix":
                check("the launcher is executable after the fix-up",
                      os.access(launcher, os.X_OK), oct(launcher.stat().st_mode))
            else:
                check("the fix-up is a no-op where the execute bit does not exist",
                      launcher.read_text(encoding="utf-8").startswith("#!"))

        # ---- status: an endpoint that says nothing, then one that answers ----
        saved_detect = fb._detect_window
        fb._detect_window = lambda url, headers=None: 0
        rc, out, err = call(fb, ["status"])
        check("status with an unreachable endpoint exits 1", rc == 1, rc)
        check("...and names the reason on stderr", "did not answer" in err, err[:200])
        check("...and still reports the box and the instance", "instance" in out, out[:200])

        fb._detect_window = lambda url, headers=None: 131072
        fb.AGENT.__dict__.pop("_window_cache", None)
        fb.AGENT.__dict__.pop("_budget_cache", None)
        rc, out, err = call(fb, ["status"])
        check("status exits 0 when the endpoint answers", rc == 0, (rc, err[:200]))
        check("...and prints the model, the window and the context",
              "131.1K" in out and "main" in out and "usable" in out, out[:400])

        # ---- doctor ----------------------------------------------------------
        rc, out, err = call(fb, ["doctor"])
        check("doctor exits 0 on a healthy page-only install", rc == 0, (rc, err[:300]))
        check("doctor says so plainly", "no problems found" in out, out[-200:])

        fb._detect_window = lambda url, headers=None: 0
        rc, out, err = call(fb, ["doctor"])
        check("doctor exits 1 when the endpoint does not answer", rc == 1, rc)
        check("...and names the endpoint on stderr",
              "did not answer" in err and fb.CONFIG["llm"]["base_url"] in err, err[:300])
        check("doctor never prints a secret value",
              "fixture-token" not in out and "fixture-token" not in err, out[:200])
        fb._detect_window = saved_detect

        # ---- model: list, refuse, and set through the config writer ----------
        entries = [{"name": "main", "send_as": "main", "where": "lan"},
                   {"name": "tower", "send_as": "tower-27b", "where": "lan",
                    "alias": "big"}]
        fb.model_catalog = lambda force=False: entries
        rc, out, err = call(fb, ["model"])
        check("model lists what this install can route to", rc == 0 and "main" in out
              and "sends as tower-27b" in out, out[:300])
        rc, out, err = call(fb, ["model", "use", "nope"])
        check("model use refuses a name that is not routable", rc == 2, rc)
        check("...and says what is", "main" in err and "tower" in err, err[:200])
        rc, out, err = call(fb, ["model", "use", "tower"])
        check("model use writes the default", rc == 0, (rc, err[:200]))
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("...into config.json, as valid JSON", written["llm"]["model"] == "tower",
              written["llm"]["model"])
        check("...and prints no secret", "fixture-token" not in out, out[:200])

        # ---- model add / remove: an endpoint has a route of its own -----------
        # (operator, 2026-09-22: "your solution to wire in another endpoint is to rerun
        # the installer?" - it never was one. Hand-editing config.json was the only way
        # in, and `model use` can only pick among endpoints already written.)
        ids = ["deepseek-chat", "deepseek-reasoner"]
        fb._probe_model_ids = lambda url, key=None: (ids if "api.deepseek" in url else None)

        rc, out, err = call(fb, ["model", "add", "https://api.deepseek.com/v1",
                                 "--model", "deepseek-chat", "--alias", "cloud",
                                 "--key-env", "DEEPSEEK_API_KEY"])
        check("model add writes a fallback entry", rc == 0, (rc, err[:200]))
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        added = [f for f in written["llm"]["fallbacks"]
                 if f.get("base_url") == "https://api.deepseek.com/v1"]
        check("...with the alias and the KEY NAME, never a value",
              added and added[0].get("alias") == "cloud"
              and added[0].get("api_key_env") == "DEEPSEEK_API_KEY"
              and "api_key" not in added[0], added)
        check("...live straight away, not only after a restart",
              any((f or {}).get("alias") == "cloud" for f in fb.CONFIG["llm"]["fallbacks"]),
              fb.CONFIG["llm"]["fallbacks"])
        check("...naming the .env variable it still needs",
              "DEEPSEEK_API_KEY" in out and "token set" in out, out[:300])
        check("...saying what the flag means for failover",
              "allow_cloud_fallback" in out, out[:400])
        check("...and printing no secret", "fixture-token" not in out, out[:200])

        rc, out, err = call(fb, ["model", "add", "https://api.deepseek.com/v1"])
        check("the same endpoint twice is refused", rc == 2 and "already there" in err,
              (rc, err[:160]))
        rc, out, err = call(fb, ["model", "add", "http://a LAN host:8081/v1",
                                 "--model", "aux", "--alias", "cloud"])
        check("an alias already in use is refused", rc == 2 and "taken" in err, (rc, err[:160]))
        rc, out, err = call(fb, ["model", "add", "http://a LAN host:8081/v1", "--model", "aux"])
        check("an endpoint that does not answer is refused with the reason",
              rc == 1 and "did not answer" in err and "--force" in err, (rc, err[:200]))
        rc, out, err = call(fb, ["model", "add", "http://a LAN host:8081/v1", "--model", "aux",
                                 "--force"])
        check("--force adds it unverified, and says so",
              rc == 0 and "unverified" in out, (rc, out[:200], err[:160]))
        rc, out, err = call(fb, ["model", "add", "https://api.deepseek.com/v1/other",
                                 "--model", "gpt-nope"])
        check("a model the endpoint does not advertise is refused",
              rc == 2 and "advertises" in err, (rc, err[:200]))
        rc, out, err = call(fb, ["model", "add", "not-a-url"])
        check("a url that is not http(s) is refused", rc == 2 and "http://" in err,
              (rc, err[:160]))
        rc, out, err = call(fb, ["model", "add", "https://api.deepseek.com/v1/reasoner",
                                 "--primary", "--model", "deepseek-reasoner"])
        check("a hosted PRIMARY without a key is refused (it has no api_key_env)",
              rc == 1 and "api_key" in err, (rc, err[:200]))
        rc, out, err = call(fb, ["model", "add", "http://the LAN model box:8081/v1", "--primary",
                                 "--model", "main", "--force"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("--primary rewrites llm.base_url and its model", rc == 0
              and written["llm"]["base_url"] == "http://the LAN model box:8081/v1"
              and written["llm"]["model"] == "main",
              (rc, err[:200], written["llm"]["base_url"]))
        check("...including in the running config",
              fb.CONFIG["llm"]["base_url"] == "http://the LAN model box:8081/v1",
              fb.CONFIG["llm"]["base_url"])

        rc, out, err = call(fb, ["model", "remove", "cloud"])
        check("model remove drops the entry it names", rc == 0 and "removed" in out,
              (rc, err[:160]))
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("...from config.json too, leaving the others",
              not any((f or {}).get("alias") == "cloud" for f in written["llm"]["fallbacks"])
              and any((f or {}).get("base_url") == "http://a LAN host:8081/v1"
                      for f in written["llm"]["fallbacks"]), written["llm"]["fallbacks"])
        rc, out, err = call(fb, ["model", "remove", "cloud"])
        check("removing something that is not there is refused",
              rc == 2 and "no fallback" in err, (rc, err[:160]))
        check("add/remove is documented in the verb help",
              "model add" in fb.VERB_HELP and "key-env" in fb.VERB_HELP,
              fb.VERB_HELP[:200])

        # ---- the batch that wraps a host command ------------------------------
        # (operator, 2026-09-22: "add a bunch of terminal/cmd/powershell learned
        # invocations". Each of these replaces something typed by hand.)
        rc, out, err = call(fb, ["version"])
        check("version prints the version alone",
              rc == 0 and fb.VERSION in out and "folder" in out, out[:200])

        rc, out, err = call(fb, ["health"])
        check("health exits 1 while nothing is running",
              rc == 1 and "not running" in out, (rc, out[:200]))
        saved_running = fb._verb_running
        fb._verb_running = lambda: True
        try:
            rc, out, err = call(fb, ["health"])
            check("health exits 0 when the instance is up, and names the lane",
                  rc == 0 and "lane" in out and "up" in out, (rc, out[:200]))
        finally:
            fb._verb_running = saved_running

        rc, out, err = call(fb, ["config", "get", "agent.max_steps"])
        check("config get reads a dotted key", rc == 0 and out.strip().isdigit(), out[:80])
        rc, out, err = call(fb, ["config", "set", "agent.max_steps", "77"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("config set writes a TYPED value (a number stays a number)",
              rc == 0 and written["agent"]["max_steps"] == 77,
              (rc, written["agent"].get("max_steps")))
        check("...and reads it back", "77" in out, out[:120])
        rc, out, err = call(fb, ["config", "set", "agent.bot_name", "12"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("a numeric-looking value is JSON-parsed without --str",
              rc == 0 and written["agent"]["bot_name"] == 12,
              written["agent"].get("bot_name"))
        rc, out, err = call(fb, ["config", "set", "agent.bot_name", "12", "--str"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("--str keeps it a string", rc == 0 and written["agent"]["bot_name"] == "12",
              written["agent"].get("bot_name"))
        rc, out, err = call(fb, ["config", "set", "web.port", "nope"])
        check("an unparseable value becomes a string, not a crash", rc == 0, (rc, err[:160]))
        rc, out, err = call(fb, ["config", "set", "mattermost.token", "oops"])
        check("config refuses to put a secret in config.json",
              rc == 2 and ".env" in err, (rc, err[:160]))
        rc, out, err = call(fb, ["config", "set", "not a key", "x"])
        check("config refuses a key that is not a dotted path", rc == 2, rc)
        rc, out, err = call(fb, ["config", "get", "nope.nothing"])
        check("config get on a missing key is not an error",
              rc == 0 and "not set" in out, (rc, out[:80]))
        call(fb, ["config", "set", "agent.tmpprobe", "1"])
        rc, out, err = call(fb, ["config", "unset", "agent.tmpprobe"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("config unset removes the key",
              rc == 0 and "tmpprobe" not in written["agent"], list(written["agent"]))
        rc, out, err = call(fb, ["config", "unset", "agent.tmpprobe"])
        check("...and says so when it was not set", rc == 2, rc)

        rc, out, err = call(fb, ["proc"])
        check("proc names this install's folder and the instance",
              rc == 0 and "install :" in out and "instance:" in out, out[:200])
        # web.port holds 'nope' at this point (the check above wrote it): a verb that
        # reports on a box must name that, not traceback
        rc, out, err = call(fb, ["ports"])
        check("ports names the page, its token state, and a junk port",
              rc == 0 and "web page" in out and "token=" in out
              and "not a port" in out, out[:300])

        (workdir / "tinycmdr.py.bak-900").write_text("old bytes", encoding="utf-8")
        (workdir / ".env").write_text("TINYCMDR_TEST_KEY=keep-me" + chr(10), encoding="utf-8")
        rc, out, err = call(fb, ["clean"])
        check("clean is a DRY RUN by default",
              rc == 0 and "tinycmdr.py.bak-900" in out
              and (workdir / "tinycmdr.py.bak-900").exists(), out[:200])
        rc, out, err = call(fb, ["clean", "--yes"])
        check("clean --yes removes the junk",
              rc == 0 and not (workdir / "tinycmdr.py.bak-900").exists(), out[-200:])
        check("...and keeps the state",
              (workdir / "config.json").exists() and (workdir / ".env").exists()
              and (workdir / "tinycmdr.py").exists())

        cand = workdir / "cand" / "tinycmdr.py"
        cand.parent.mkdir()
        cand.write_text((workdir / "tinycmdr.py").read_text(encoding="utf-8")
                        .replace('VERSION = "', 'VERSION = "9.9.9-', 1), encoding="utf-8")
        rc, out, err = call(fb, ["update", str(cand)])
        check("update takes a candidate build and prints both versions",
              rc == 0 and "9.9.9" in out, (rc, out[:200], err[:200]))
        check("...leaving a .bak-update beside the file it replaced",
              any(p.name.startswith("tinycmdr.py.bak-update") for p in workdir.iterdir()),
              [p.name for p in workdir.iterdir()])
        rc, out, err = call(fb, ["update", str(cand)])
        check("updating with the same bytes is a no-op",
              rc == 0 and "nothing to do" in out, out[:120])
        bad = workdir / "bad.py"
        bad.write_text("print('not a build')\n", encoding="utf-8")
        rc, out, err = call(fb, ["update", str(bad)])
        check("update refuses a file that is not a build",
              rc == 1 and "is that a build" in err, (rc, err[:160]))
        fake = workdir / "fakebuild" / "tinycmdr.py"
        fake.parent.mkdir()
        fake.write_text("print('hello')\n", encoding="utf-8")
        rc, out, err = call(fb, ["update", str(fake)])
        check("...and a tinycmdr.py with no VERSION line",
              rc == 1 and "VERSION" in err, (rc, err[:160]))
        check("the new verbs are in the verb list",
              all(v in fb.VERBS for v in ("health", "config", "proc", "ports",
                                          "update", "clean", "version")), fb.VERBS)

        # ---- logs: bounded, and scrubbed -------------------------------------
        secret = "sk-live-ABCdef0123456789"
        fb.CONFIG["llm"]["api_key"] = secret
        fb._SECRETS = fb._secret_values()
        loglines = ["line %d" % i for i in range(1, 101)]
        loglines.append("a request went out with %s" % secret)
        (workdir / "tinycmdr.log").write_text("\n".join(loglines) + "\n", encoding="utf-8")
        rc, out, err = call(fb, ["logs", "5"])
        check("logs prints the tail", rc == 0 and "line 100" in out and "line 95" not in out,
              out[:200])
        check("...with the key redacted", secret not in out and "«redacted»" in out,
              out[-200:])
        rc, out, err = call(fb, ["logs", "banana"])
        check("logs rejects a non-number count", rc == 2, rc)
        fb.CONFIG["llm"]["api_key"] = ""
        fb._SECRETS = fb._secret_values()

        # ---- token: names, never values; and the one editing path ------------
        os.environ["TINYCMDR_MM_TOKEN"] = "mm-secret-value-1234567890"
        try:
            rc, out, err = call(fb, ["token"])
            check("token reports the key name", rc == 0 and "TINYCMDR_MM_TOKEN" in out,
                  out[:300])
            check("token never prints the value",
                  "mm-secret-value-1234567890" not in out + err, out[:300])
            check("token says where the secrets live", ".env" in out, out[:200])
            rc, out, err = call(fb, ["token", "set", "not-a-key"])
            check("token set refuses a name that is not a .env key", rc == 2, rc)
            rc, out, err = call(fb, ["token", "set", "TINYCMDR_TEST_KEY"],
                                stdin="written-from-stdin-1234\n")
            check("token set writes it", rc == 0, (rc, err[:200]))
            env_text = (workdir / ".env").read_text(encoding="utf-8")
            check("...as NAME=value in .env",
                  "TINYCMDR_TEST_KEY=written-from-stdin-1234" in env_text, env_text[:120])
            check("...without echoing the value",
                  "written-from-stdin-1234" not in out, out[:200])
            rc, out, err = call(fb, ["token", "set", "TINYCMDR_EMPTY"], stdin="\n")
            check("token set refuses an empty value", rc == 1
                  and "nothing written" in err, (rc, err[:120]))
            # a second set replaces rather than appends
            call(fb, ["token", "set", "TINYCMDR_TEST_KEY"], stdin="second-value-9876\n")
            env_text = (workdir / ".env").read_text(encoding="utf-8")
            check("a repeated set replaces the line",
                  env_text.count("TINYCMDR_TEST_KEY=") == 1
                  and "second-value-9876" in env_text, env_text[:160])
        finally:
            os.environ.pop("TINYCMDR_MM_TOKEN", None)

        check("restart and run are verbs too",
              "restart" in fb.VERBS and "run" in fb.VERBS)

        # ---- restart: it calls this host's own helper, and never re-implements it --
        # Stubbed end to end: a real restart here would restart the live bot.
        seen = {}

        def fake_run(argv, timeout=180, **kw):
            # run_capture returns FOUR values; a three-value stub would hide a real
            # ValueError (which it did, 2026-09-22)
            seen["argv"] = list(argv)
            return 0, "helper said it restarted", "", False

        # the packaged install carries the helper; the staging harness copies only the
        # app, so put one where the verb looks for it
        (workdir / "maintenance").mkdir(exist_ok=True)
        (workdir / "maintenance" / "restart-tinycmdr.ps1").write_text(
            "# a helper for the suite", encoding="utf-8")

        saved_run, saved_elev, saved_running = fb.run_capture, fb._is_elevated, fb._verb_running
        fb.run_capture = fake_run
        fb._is_elevated = lambda: True
        fb._verb_running = lambda: True
        try:
            rc, out, err = call(fb, ["restart"])
            helper = (seen.get("argv") or [""])[-1]
            check("restart calls the shipped helper for this host",
                  os.path.basename(helper) in ("restart-tinycmdr.ps1",
                                               "restart-tinycmdr.sh",
                                               "restart-tinycmdr-macos.sh"),
                  str(seen.get("argv")))
            # samefile, not a string compare: on Windows mkdtemp can hand back the 8.3
            # short form of the user's temp path while the module resolved the long one
            check("...from THIS install, not somewhere else",
                  os.path.samefile(helper,
                                   workdir / "maintenance" / "restart-tinycmdr.ps1"),
                  helper)
            check("...and says the instance is back", rc == 0 and "back up" in out,
                  (rc, out[:120], err[:160]))
            fb._is_elevated = lambda: False
            fb.run_capture = boom_run
            rc, out, err = call(fb, ["restart"])
            check("without rights it says how to get them instead of failing oddly",
                  rc == 1 and ("elevated" in err or "root" in err), (rc, err[:200]))
        finally:
            fb.run_capture, fb._is_elevated, fb._verb_running = saved_run, saved_elev, saved_running

        # --- `tinycmdr web`: the page lane in one word (2026-09-22) -----------------
        # The operator: "so what if I want to startup the tinycmdr web-ui? type tinycmdr web
        # in a cmd window?" The page was a flag (`--web`) and nothing said so; now the word
        # is translated in main(), and a port that is already served says so usefully.
        check("`web` is not a management verb", "web" not in fb.VERBS, sorted(fb.VERBS)[:6])
        script = os.path.join(os.path.dirname(fb.__file__), "tinycmdr.py")
        check("VERB_HELP names the page lane", "tinycmdr web" in fb.VERB_HELP,
              fb.VERB_HELP[-220:])
        check("VERB_HELP stopped teaching the retired prefix", "/cmdr " not in fb.VERB_HELP,
              fb.VERB_HELP[-220:])

        reached = []
        saved_mode, saved_argv = fb.run_web_mode, sys.argv
        try:
            fb.run_web_mode = lambda: reached.append("web")
            for argv in (["tinycmdr.py", "web"], ["tinycmdr.py", "webui"],
                         ["tinycmdr.py", "--web"]):
                sys.argv = list(argv)
                fb.main()
                check("`%s` starts the page lane" % " ".join(argv[1:]), reached == ["web"],
                      (reached, []))
                reached.clear()
            sys.argv = list(saved_argv)
            sys.argv = ["tinycmdr.py", "status"]
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    fb.main()
            except SystemExit:
                pass
            check("...and a management verb still does NOT start the page",
                  reached == [], reached)
        finally:
            fb.run_web_mode, sys.argv = saved_mode, list(saved_argv)

        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        held = srv.getsockname()[1]
        try:
            note = fb.web_busy_note("127.0.0.1", held)
            text = "\n".join(note)
            check("a held port is reported with its URL", ("127.0.0.1:%d" % held) in text, text)
            if fb._port_holder(held):
                check("...and names who holds it", "listening there" in text, text)
            else:
                print("skip a held port names the holder: this host cannot see the holder")

            free = socket.socket()
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
            free.close()
            text = "\n".join(fb.web_busy_note("127.0.0.1", port))
            check("a free port says nothing is listening", "Nothing is listening" in text, text)
            text = "\n".join(fb.web_busy_note("127.0.0.1", "nope"))
            check("a junk web.port does not crash the note", "Could not start" in text, text)
        finally:
            srv.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # ---- the chat surface for the management verbs ------------------------------
    # Measured 2026-09-24: the chat lane dispatched /new /stop /restart /model /status /undo
    # and nothing else, so `/tinycmdr update` - the command the fleet is updated with - went
    # to the model as ordinary text. Every verb that makes sense in a channel now runs there,
    # and the ones that need a terminal are refused by name.
    check("update is a chat verb", "update" in fb._CHAT_VERB_SET, sorted(fb._CHAT_VERB_SET))
    for v in ("run", "setup", "token", "restart"):
        check(f"{v} is NOT a chat verb (it prompts or has its own path)",
              v not in fb._CHAT_VERB_SET, sorted(fb._CHAT_VERB_SET))
    text = fb.verb_from_chat("version")
    check("a chat verb returns what it printed", "tinycmdr" in text and "(exit 0)" in text, text)
    check("a chat verb never reaches the model", True)
    text = fb.verb_from_chat("setup")
    check("a terminal-only verb is refused by name", "needs a terminal" in text, text)
    text = fb.verb_from_chat("banana")
    check("an unknown verb names the chat set", "unknown verb" in text and "update" in text, text)
    check("no arguments is the help", "tinycmdr <verb>" in fb.verb_from_chat(""), "none")

    # ---- update: no git, no checkout -------------------------------------------
    saved_git_exe = fb._git_exe
    fb._git_exe = lambda: ""
    rc, out, err = call(fb, ["update"])
    check("update with no git and no checkout says so and exits 2",
          rc == 2 and "no git binary" in err, (rc, err[:160]))
    fb._git_exe = lambda: "git-not-here"
    rc, out, err = call(fb, ["update"])
    check("update without .git adopts the git path instead of printing usage",
          "adopting" in out, (rc, out[:160]))
    fb._git_exe = saved_git_exe

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all verb checks passed")


if __name__ == "__main__":
    main()
