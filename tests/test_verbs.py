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
        rc, out, err = call(fb, ["model", "add", "http://a LAN address:8081/v1",
                                 "--model", "aux", "--alias", "cloud"])
        check("an alias already in use is refused", rc == 2 and "taken" in err, (rc, err[:160]))
        rc, out, err = call(fb, ["model", "add", "http://a LAN address:8081/v1", "--model", "aux"])
        check("an endpoint that does not answer is refused with the reason",
              rc == 1 and "did not answer" in err and "--force" in err, (rc, err[:200]))
        rc, out, err = call(fb, ["model", "add", "http://a LAN address:8081/v1", "--model", "aux",
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
        rc, out, err = call(fb, ["model", "add", "http://a LAN address:8081/v1", "--primary",
                                 "--model", "main", "--force"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("--primary rewrites llm.base_url and its model", rc == 0
              and written["llm"]["base_url"] == "http://a LAN address:8081/v1"
              and written["llm"]["model"] == "main",
              (rc, err[:200], written["llm"]["base_url"]))
        check("...including in the running config",
              fb.CONFIG["llm"]["base_url"] == "http://a LAN address:8081/v1",
              fb.CONFIG["llm"]["base_url"])

        rc, out, err = call(fb, ["model", "remove", "cloud"])
        check("model remove drops the entry it names", rc == 0 and "removed" in out,
              (rc, err[:160]))
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("...from config.json too, leaving the others",
              not any((f or {}).get("alias") == "cloud" for f in written["llm"]["fallbacks"])
              and any((f or {}).get("base_url") == "http://a LAN address:8081/v1"
                      for f in written["llm"]["fallbacks"]), written["llm"]["fallbacks"])
        rc, out, err = call(fb, ["model", "remove", "cloud"])
        check("removing something that is not there is refused",
              rc == 2 and "no fallback" in err, (rc, err[:160]))
        check("add/remove is documented in the verb help",
              "model add" in fb.VERB_HELP and "key-env" in fb.VERB_HELP,
              fb.VERB_HELP[:200])

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
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        sys.exit(1)
    print("all verb checks passed")


if __name__ == "__main__":
    main()
