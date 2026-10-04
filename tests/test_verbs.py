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
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import run_scenario  # noqa: E402

PASSES = []
FAILS = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"ok   {name}")
    else:
        FAILS.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


class FakeTTY(io.StringIO):
    """A stdin that isatty()s - for the verbs that ask a question only a person answers."""

    def isatty(self):
        return True


def call(fb, argv, stdin=None, tty=False):
    """Run one verb, with both streams captured and stdin faked when asked."""
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdin
    if stdin is not None:
        sys.stdin = FakeTTY(stdin) if tty else io.StringIO(stdin)
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
        # A chat install whose only lane is Telegram: no Mattermost token, so a missing
        # Mattermost client is not a problem for this box (which is also what keeps this
        # suite interpreter-agnostic). A host with NO chat lane at all is no longer a lane
        # of any kind - validate_startup_config refuses it - so Telegram is the stand-in.
        cfg = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        cfg["mattermost"]["token"] = ""
        cfg["telegram"] = {"token": "fixture-tg-token-not-a-secret",
                           "allowed_users": ["12345"]}
        (workdir / "config.json").write_text(json.dumps(cfg, indent=2),
                                            encoding="utf-8")
        fb.CONFIG["mattermost"]["token"] = ""
        fb.CONFIG["telegram"] = dict(cfg["telegram"])

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

        # ---- update prunes the project's kit (and this list DELETES) ------------------
        # `update` used to git-pull the whole repo, so user machines accumulated the test
        # suites, the CI workflow, the docs and the maintainer kit. It now deletes those BY
        # NAME. Grade the two ways that can go wrong: the kit must go, and a host's own
        # files sitting in the same folders (private_rules.py, where-roles.json) must not.
        # Run in a temp tree so the suite's own install is never touched.
        _saved_base = fb.BASE_DIR
        _ptmp = Path(tempfile.mkdtemp(prefix="fbtest-prune-"))
        try:
            fb.BASE_DIR = _ptmp
            for rel in ("tests", "docs", ".github"):
                (_ptmp / rel).mkdir(parents=True)
                (_ptmp / rel / "x.txt").write_text("x", encoding="utf-8")
            (_ptmp / "CHANGELOG.md").write_text("x", encoding="utf-8")
            (_ptmp / "maintenance").mkdir()
            for _name in ("release.sh", "where.py", "leak-gate.py",
                          "private_rules.py"):
                (_ptmp / "maintenance" / _name).write_text("x", encoding="utf-8")
            (_ptmp / "maintenance" / "where-roles.json").write_text(
                '[{"role":"live","path":"/a"},{"role":"dev","path":"/b"}]', encoding="utf-8")
            _note = fb._prune_dev_kit()
            check("pruning removes the project's kit (dirs, files, maintainer scripts)",
                  not (_ptmp / "tests").exists() and not (_ptmp / "docs").exists()
                  and not (_ptmp / ".github").exists()
                  and not (_ptmp / "CHANGELOG.md").exists()
                  and not (_ptmp / "maintenance" / "release.sh").exists()
                  and not (_ptmp / "maintenance" / "where.py").exists(), _note)
            check("...and never a host's own file in the same folder",
                  (_ptmp / "maintenance" / "private_rules.py").exists()
                  and (_ptmp / "maintenance" / "where-roles.json").exists(), _note)
            (_ptmp / "tests").mkdir()
            (_ptmp / "maintenance" / "where-roles.json").write_text(
                '[{"role":"dev","path":"%s"}]' % _ptmp, encoding="utf-8")
            _note2 = fb._prune_dev_kit()
            check("a tree the box declares as its DEV tree is never pruned",
                  (_ptmp / "tests").exists() and "DEVELOPMENT tree" in _note2, _note2)
        finally:
            fb.BASE_DIR = _saved_base
            shutil.rmtree(_ptmp, ignore_errors=True)

        # _apply_package() writes the package over the install. It must SEED a host-owned
        # path and never overwrite one: 1.0.49 protected only soul.md, so an edited
        # tools/patch.py or skills/README.md - tools/ being exactly where the agent is told
        # to write its own tools - was silently replaced (measured 2026-10-03).
        _ab = fb.BASE_DIR
        _atmp = Path(tempfile.mkdtemp(prefix="fbtest-apply-"))
        _pkg = Path(tempfile.mkdtemp(prefix="fbtest-pkg-"))
        try:
            fb.BASE_DIR = _atmp
            for d, f, text in (("tools", "patch.py", "MINE"),
                               ("skills", "README.md", "MINE")):
                (_atmp / d).mkdir(); (_atmp / d / f).write_text(text, encoding="utf-8")
                (_pkg / d).mkdir(); (_pkg / d / f).write_text("SHIPPED", encoding="utf-8")
            (_atmp / "soul.md").write_text("my persona", encoding="utf-8")
            (_pkg / "soul.md").write_text("seed persona", encoding="utf-8")
            (_atmp / "tinycmdr.py").write_text("old", encoding="utf-8")
            (_pkg / "tinycmdr.py").write_text("new", encoding="utf-8")
            (_pkg / "install").mkdir()
            (_pkg / "install" / "x.sh").write_text("new", encoding="utf-8")
            _wrote, _skipped = fb._apply_package(_pkg)
            check("apply writes what the package owns",
                  (_atmp / "tinycmdr.py").read_text(encoding="utf-8") == "new"
                  and (_atmp / "install" / "x.sh").exists()
                  and "tinycmdr.py" in _wrote, str(_wrote))
            check("...and never overwrites a host-owned file (tools/, skills/, soul.md)",
                  (_atmp / "tools" / "patch.py").read_text(encoding="utf-8") == "MINE"
                  and (_atmp / "skills" / "README.md").read_text(encoding="utf-8") == "MINE"
                  and (_atmp / "soul.md").read_text(encoding="utf-8") == "my persona",
                  str(_skipped))
            (_pkg / "skills" / "RULES.md").write_text("seed", encoding="utf-8")
            fb._apply_package(_pkg)
            check("...while a MISSING host-owned file IS seeded",
                  (_atmp / "skills" / "RULES.md").read_text(encoding="utf-8") == "seed")
            check("...and nothing is copied aside (no .bak-update pile)",
                  not list(_atmp.glob("*.bak*")),
                  [p.name for p in _atmp.rglob("*") if p.is_file()])
        finally:
            fb.BASE_DIR = _ab
            shutil.rmtree(_atmp, ignore_errors=True)
            shutil.rmtree(_pkg, ignore_errors=True)

        # _declared_dev_tree() decides whether pruning is SAFE here, so grade both
        # directions: a two-tree box declares dev elsewhere and its live tree is prunable,
        # while a "same_as live" (or unreadable) declaration must hold it off - the
        # operator's own box sat in the one-tree shape and could never be cleaned.
        _wr = Path(fb.BASE_DIR) / "maintenance" / "where-roles.json"
        _wr.parent.mkdir(parents=True, exist_ok=True)
        _saved = _wr.read_text(encoding="utf-8") if _wr.exists() else None
        try:
            _wr.write_text('[{"role":"live","path":"~/tinycmdr"},'
                           '{"role":"dev","path":"/somewhere/else"}]', encoding="utf-8")
            check("a dev tree declared elsewhere does not protect THIS tree",
                  fb._declared_dev_tree() is False)
            _wr.write_text('[{"role":"dev","same_as":"live"}]', encoding="utf-8")
            check("...but 'dev: same_as live' does", fb._declared_dev_tree() is True)
            _wr.write_text('[{"role":"dev","path":"%s"}]' % fb.BASE_DIR, encoding="utf-8")
            check("...and an explicit dev path pointing here does",
                  fb._declared_dev_tree() is True)
            _wr.write_text("{ not json", encoding="utf-8")
            check("an unreadable declaration is treated as dev (never prune on doubt)",
                  fb._declared_dev_tree() is True)
        finally:
            if _saved is None:
                _wr.unlink(missing_ok=True)
            else:
                _wr.write_text(_saved, encoding="utf-8")

        # ---- status: an endpoint that says nothing, then one that answers ----
        saved_detect = fb._detect_window

        def forget_probes():
            """Drop the probe caches the agent holds, whatever this build calls them.

            Measured 2026-09-26: this block cleared _window_cache and _budget_cache, but
            the envelope built from the probe had been renamed to _envelope_cache, so the
            "endpoint answers" checks below graded the PREVIOUS probe's verdict ("assumed",
            i.e. no answer) and went red on a box where the stub answered 131072. The names
            drift; the intent - forget what the last probe said - does not.
            """
            for _n in [n for n in fb.AGENT.__dict__ if n.endswith("_cache")]:
                fb.AGENT.__dict__.pop(_n, None)

        fb._detect_window = lambda url, headers=None: 0
        forget_probes()
        rc, out, err = call(fb, ["status"])
        check("status with an unreachable endpoint exits 1", rc == 1, rc)
        check("...and names the reason on stderr", "did not answer" in err, err[:200])
        check("...and still reports the box and the instance", "instance" in out, out[:200])

        fb._detect_window = lambda url, headers=None: 131072
        forget_probes()
        rc, out, err = call(fb, ["status"])
        check("status exits 0 when the endpoint answers", rc == 0, (rc, err[:200]))
        # The line must carry the model, the window the server reported and the arithmetic
        # built from it. Asserted against the build's OWN formatter, not one spelling of it:
        # this check used to require the word "usable", which the status line stopped
        # printing when the envelope line took over (the banner still says it), so it graded
        # prose instead of the numbers the operator reads.
        env = fb.AGENT._envelope()
        check("...and prints the model, the window and the context",
              env.get("source") == "server" and fb.fmt_tokens(env["window"]) in out
              and "main" in out and fb.envelope_line(env) in out, out[:400])

        # ---- doctor ----------------------------------------------------------
        rc, out, err = call(fb, ["doctor"])
        check("doctor exits 0 on a healthy chat install", rc == 0, (rc, err[:300]))
        check("doctor says so plainly", "no problems found" in out, out[-200:])

        fb._detect_window = lambda url, headers=None: 0
        forget_probes()
        rc, out, err = call(fb, ["doctor"])
        check("doctor exits 1 when the endpoint does not answer", rc == 1, rc)
        check("...and names the endpoint on stderr",
              "did not answer" in err and fb.CONFIG["llm"]["base_url"] in err, err[:300])
        check("doctor never prints a secret value",
              "fixture-token" not in out and "fixture-token" not in err, out[:200])

        # ---- the persona: which soul this agent is actually running ----------------
        # soul.md is the one TRACKED file an operator is invited to edit, so a persona is an
        # uncommitted modification to a tracked file: nothing used to say whether a box ran
        # the shipped identity or somebody's edit, and `update` (a git pull) either refuses
        # over that edit or has it discarded by the next `git reset --hard`.
        soul = workdir / "soul.md"
        saved_soul = fb.SOUL_FILE
        fb.SOUL_FILE = soul
        try:
            soul.unlink(missing_ok=True)
            rc, out, err = call(fb, ["doctor"])
            check("doctor names the built-in default when there is no soul.md",
                  "persona" in out and "built-in default" in out, out[:400])

            soul.write_text(fb.DEFAULT_SOUL, encoding="utf-8")
            rc, out, err = call(fb, ["doctor"])
            check("...the shipped seed when soul.md is untouched",
                  "the shipped seed" in out, out[:400])

            soul.write_text("You are a laconic mainframe operator.", encoding="utf-8")
            rc, out, err = call(fb, ["doctor"])
            check("...and an edit when somebody re-persona'd this box",
                  "edited on this host" in out, out[:400])
            check("...with a note that update protects the edit and a hard reset would not",
                  "uncommitted edit" in out and "reset --hard" in out, out[-300:])

            backup = fb.preserve_edited_soul("20260930-000000")
            check("an edited persona is copied aside before an update",
                  bool(backup) and Path(backup).read_text(encoding="utf-8")
                  == "You are a laconic mainframe operator.", backup)
            check("...the backup lands beside it, named for the update",
                  bool(backup) and Path(backup).name == "soul.md.bak-update-20260930-000000",
                  backup)
            # `update` is routine, so the copies must be bounded by DISTINCT personas, not
            # by the number of times it ran: a second run with nothing edited adds nothing.
            check("...and an update that changed nothing adds no second copy",
                  fb.preserve_edited_soul("20260930-000001") == ""
                  and len(list(workdir.glob("soul.md.bak-update-*"))) == 1,
                  sorted(p.name for p in workdir.glob("soul.md.bak-update-*")))
            for n in range(2, 6):                      # four more distinct personas
                soul.write_text("persona v%d" % n, encoding="utf-8")
                fb.preserve_edited_soul("20260930-00000%d" % n)
            kept = sorted(p.name for p in workdir.glob("soul.md.bak-update-*"))
            check("...and older copies are pruned, so the set stays bounded by the cap",
                  len(kept) == fb._SOUL_BACKUPS_KEEP, kept)
            check("...keeping the NEWEST persona, not the oldest",
                  Path(workdir / kept[-1]).read_text(encoding="utf-8") == "persona v5", kept)
            soul.write_text(fb.DEFAULT_SOUL, encoding="utf-8")
            check("...and the shipped seed is not backed up: nothing to lose",
                  fb.preserve_edited_soul("x") == "", "")
            soul.unlink()
            check("...nor is a missing soul.md", fb.preserve_edited_soul("x") == "", "")
        finally:
            fb.SOUL_FILE = saved_soul

        # ---- F-19: a cloud endpoint on an ASSUMED window is told the lever --------
        # /v1/models on a hosted API carries no max_model_len and there is no /props,
        # /api/ps or /get_server_info, so _detect_window returns 0 and the envelope assumes
        # its 8000-token window with replies clipped at 2048 - a 128k model driven at 8k.
        # Doctor used to call that "did not answer" and name no lever; the fix is one key,
        # and only for an off-LAN endpoint. A server that DID report a window needs no
        # advice, and an on-LAN box is asked again once it is restarted with a bigger slot.
        saved_url = fb.CONFIG["llm"]["base_url"]
        saved_ceiling = fb.CONFIG["llm"].get("max_context_tokens")
        fb.CONFIG["llm"]["base_url"] = "http://192.0.2.10:8081/v1"   # off-LAN, no DNS
        fb.CONFIG["llm"]["max_context_tokens"] = "auto"              # nothing to believe
        fb._detect_window = lambda url, headers=None: 0
        forget_probes()
        rc, out, err = call(fb, ["doctor"])
        check("doctor names the window lever for a cloud endpoint that reports no window",
              rc == 1 and "llm.max_context_tokens" in err, (rc, err[-400:]))
        check("...and says the window is assumed, not that the endpoint is down",
              "no window known" in out and "did not answer" not in err,
              (out[-300:], err[-300:]))
        fb.CONFIG["llm"]["base_url"] = "http://192.0.2.10:8081/v1"
        fb._detect_window = lambda url, headers=None: 131072         # the server answered
        forget_probes()
        rc, out, err = call(fb, ["doctor"])
        check("...and stays silent when the endpoint does report a window",
              rc == 0 and "llm.max_context_tokens" not in err, (rc, err[-300:]))
        fb.CONFIG["llm"]["base_url"] = saved_url                     # back on the LAN
        fb._detect_window = lambda url, headers=None: 0
        forget_probes()
        rc, out, err = call(fb, ["doctor"])
        check("...and stays silent for a LAN endpoint with no window",
              "llm.max_context_tokens" not in err, err[-300:])
        fb.CONFIG["llm"]["max_context_tokens"] = saved_ceiling
        forget_probes()

        # A run parked on a legitimate question must not be abandoned as a stall first.
        # Nothing compared the pair: with the ask cap at 900 s and abandon at 10 min,
        # `_stall_tick` set the cancel event and ask_operator returned "stopped" instead
        # of the answer. Doctor has to name the pair, and stay silent when the watchdog
        # is off (0) - there is nothing to compare against.
        fb._detect_window = lambda url, headers=None: 131072
        forget_probes()
        agent_cfg = fb.CONFIG["agent"]
        saved_ask = agent_cfg.get("ask_user_wait_seconds")
        saved_abandon = agent_cfg.get("stall_abandon_minutes")
        agent_cfg["ask_user_wait_seconds"] = 900
        agent_cfg["stall_abandon_minutes"] = 10
        rc, out, err = call(fb, ["doctor"])
        check("doctor flags an ask cap longer than the abandon window",
              rc == 1 and "stall_abandon_minutes" in err, (rc, err[-300:]))
        agent_cfg["stall_abandon_minutes"] = 0     # the watchdog is off
        rc, out, err = call(fb, ["doctor"])
        check("...and stays silent with the watchdog disabled",
              rc == 0 and "stall_abandon_minutes" not in err, (rc, err[-300:]))
        agent_cfg["ask_user_wait_seconds"] = saved_ask
        agent_cfg["stall_abandon_minutes"] = saved_abandon
        rc, out, err = call(fb, ["doctor"])
        check("...and stays silent on the default pair",
              rc == 0 and "stall_abandon_minutes" not in err, (rc, err[-300:]))
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

        # ---- the model picker: the list you MOVE through ------------------------
        # Operator, 2026-09-30: "the /tinycmdr model 'wizard' ... is fucking terrible".
        # It was a Commands box: to switch you retyped the whole command with an exact name
        # you had to already know. hermes opens a list you move through; this is that list.
        pick_rows = fb.model_pick_rows(
            [{"name": "main", "send_as": "main", "url": "http://127.0.0.1:8081/v1",
              "local": True},
             {"name": "tower", "send_as": "tower-27b", "alias": True,
              "url": "http://10.0.0.5:8081/v1"},
             {"name": "qwen3-14b", "send_as": "qwen3-14b",
              "url": "http://10.0.0.5:8081/v1"}],
            "tower")
        p = fb.ModelPick(pick_rows, current="tower", title="Select model",
                         scope="ENTER switches this session")
        check("the picker opens ON the model in use", p.picked() == "tower", p.picked())
        check("...and a row names the endpoint and the id an alias really sends",
              pick_rows[1][1] == "sends as tower-27b · http://10.0.0.5:8081/v1",
              pick_rows[1][1])
        check("down moves the cursor", p.move(1).picked() == "qwen3-14b", p.picked())
        check("...and it stops at the first row", p.move(-9).picked() == "main", p.picked())
        check("typing narrows the list to the match",
              p.typed("qwen").picked() == "qwen3-14b" and len(p.visible()) == 1, p.visible())
        drawn = "".join(text for _style, text in p.render(70))
        check("...and the filter line counts what is left",
              "Filter: qwen" in drawn and "(1/3 match" in drawn, drawn[:200])
        check("backspace widens it again",
              p.backspace().backspace().backspace().backspace().picked() == "main"
              and not p.filter, (p.filter, p.visible()))
        p.typed("nothingmatchesthis")
        check("no match is said out loud, not drawn blank",
              any("nothing matches" in t for _s, t in p.render(70)), p.render(70))
        check("...and ENTER then picks nothing at all", p.picked() is None, p.picked())
        fresh = fb.ModelPick(pick_rows, current="tower")
        lines = [t for _s, t in fresh.render(70)]
        check("the cursor row is marked and the model in use is named",
              any(t.strip().startswith("\u2192 (\u25cf) tower") and "\u2190 current" in t
                  for t in lines), lines)
        check("the list scrolls rather than overflowing, and says how much is left",
              len(fb.ModelPick([(str(i), "", False) for i in range(40)]).render(70))
              <= fb.MODEL_PICK_VIEW + 6,
              len(fb.ModelPick([(str(i), "", False) for i in range(40)]).render(70)))

        # bare `model` on a pipe (a script, a cron job, CI) is still the plain list
        rc, out, err = call(fb, ["model"])
        check("bare model on a pipe still prints the list a script greps",
              rc == 0 and "models this install can route to" in out, out[:200])

        # a door that cannot host the picker keeps the status box it always had
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            fb._cli_model("", pick=True)
        check("chat and the inline console keep the Model Status box",
              "Model Status" in out_buf.getvalue(), out_buf.getvalue()[:200])

        # `--app`'s bare /model opens the picker instead (the app draws it; the console
        # worker only hands over the state)
        class _FakeScreen:
            def __init__(self):
                self.opened = []

            def open_model_pick(self, state, on_pick):
                self.opened.append((state, on_pick))

        fb.AGENT.model_overrides.pop(fb._cli_key(), None)
        fb._CLI["app"] = _FakeScreen()
        try:
            out_buf = io.StringIO()
            with contextlib.redirect_stdout(out_buf):
                fb._cli_model("", pick=True)
            state, on_pick = fb._CLI["app"].opened[0]
            check("--app's bare model opens the picker, printing no command list",
                  not out_buf.getvalue().strip() and isinstance(state, fb.ModelPick),
                  out_buf.getvalue()[:120])
            check("...on the model in use, saying what ENTER does",
                  state.picked() == written["llm"]["model"] and "ENTER" in state.scope,
                  (state.picked(), state.scope))
            on_pick("tower")                      # what the app calls when ENTER lands
        finally:
            fb._CLI.pop("app", None)
        check("...and ENTER takes the same path a typed name takes",
              fb.AGENT.model_overrides.get(fb._cli_key()) == "tower",
              fb.AGENT.model_overrides)
        fb.AGENT.model_overrides.pop(fb._cli_key(), None)

        # the setup wizard refuses the door where its raw input() would fight the app
        fb._CLI["app"] = _FakeScreen()
        try:
            rc, out, err = call(fb, ["setup"])
        finally:
            fb._CLI.pop("app", None)
        check("setup names the app as the wrong door instead of scribbling",
              rc == 1 and "not from inside --app" in err, (rc, err[:200]))

        # ---- model endpoint: read it, and CORRECT it ---------------------------
        # Operator, 2026-09-30: the picker "should also allow you to edit your incorrectly
        # entered endpoint if that happened to a user when they set it up". The probe is the
        # real `probe_endpoint`, stubbed per URL, so a dead URL is refused and a live one
        # is written - and a wrong endpoint stops being a hand-edit of config.json.
        live = {"http://10.0.0.9:8081/v1": ["qwen3-14b", "glm-4.6"]}

        def _fake_probe(url, key=None):
            got = live.get(str(url).rstrip("/"))
            return ({"ok": True, "ids": list(got), "status": 200, "error": ""}
                    if got is not None
                    else {"ok": False, "ids": [], "status": None,
                          "error": "connection refused"})

        fb.probe_endpoint = _fake_probe

        rc, out, err = call(fb, ["model", "endpoint"])
        check("model endpoint reads the endpoint and whether it answers",
              rc == 1 and "primary endpoint:" in out and "did NOT answer" in out, out[:300])
        check("...and names the command that fixes it",
              "model endpoint <url>" in out, out[:300])

        rc, out, err = call(fb, ["model", "endpoint", "http://10.0.0.9:8081/v1"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("model endpoint writes a URL that answers, and says what it advertises",
              rc == 0 and written["llm"]["base_url"] == "http://10.0.0.9:8081/v1"
              and "qwen3-14b" in out, (rc, written["llm"]["base_url"], out[:200]))

        rc, out, err = call(fb, ["model", "endpoint", "http://10.0.0.9:9999/v1"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("a URL that does not answer is REFUSED, not written",
              rc == 1 and written["llm"]["base_url"] == "http://10.0.0.9:8081/v1"
              and "did not answer" in err, (rc, written["llm"]["base_url"], err[:200]))
        check("...and the refusal names --force for a server that is not up yet",
              "--force" in err, err[:200])

        rc, out, err = call(fb, ["model", "endpoint", "http://10.0.0.9:9999/v1", "--force"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("--force writes it and says it is unverified",
              rc == 0 and written["llm"]["base_url"] == "http://10.0.0.9:9999/v1"
              and "unverified" in out, (rc, out[:200]))

        rc, out, err = call(fb, ["model", "endpoint", "not-a-url"])
        check("a URL without a scheme is refused before anything is probed",
              rc == 2 and "http://" in err, (rc, err[:200]))

        # the console door: the same command, and the app is handed the picker over the new
        # endpoint's ids rather than printing a list to retype
        fb._CLI["app"] = _FakeScreen()
        try:
            out_buf = io.StringIO()
            with contextlib.redirect_stdout(out_buf):
                fb._cli_model("endpoint http://10.0.0.9:8081/v1", pick=True)
            opened = fb._CLI["app"].opened
            check("--app: `model endpoint <url>` opens the picker on the new endpoint's models",
                  opened and isinstance(opened[0][0], fb.ModelPick)
                  and opened[0][0].picked() == "qwen3-14b", out_buf.getvalue()[:200])
        finally:
            fb._CLI.pop("app", None)

        # ---- the fix door: a 401 on the primary asks for the KEY ------------------
        # Operator: "the user shouldn't be able to black hole themselves with a mistype."
        # A cloud primary has two faults that look like one - the link and the key - so the
        # door that fixes a link must be able to fix the key too, and write it to .env.
        def _401_probe(url, key=None, timeout=20):
            if key == "sk-fixed":
                return {"ok": True, "ids": ["recovered-1"], "status": 200, "error": ""}
            return {"ok": False, "ids": [], "status": 401, "error": "HTTP 401"}

        fb.probe_endpoint = _401_probe
        fb._ask_secret = lambda prompt: "sk-fixed"
        os.environ.pop("TINYCMDR_LLM_API_KEY", None)
        fb.CONFIG["llm"]["api_key"] = "none"
        rc, out, err = call(fb, ["model", "endpoint", "https://api.fixed.example/v1"],
                            stdin="", tty=True)
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        env_text = (workdir / ".env").read_text(encoding="utf-8")
        check("a 401 at the fix door asks for the key and writes it to .env",
              rc == 0 and "TINYCMDR_LLM_API_KEY=sk-fixed" in env_text
              and written["llm"]["base_url"] == "https://api.fixed.example/v1"
              and "api_key" not in written["llm"],
              (rc, env_text[-160:], written["llm"]))
        check("...and the re-probe reaches the provider's model list",
              "recovered-1" in out, out[:300])
        # a key the door cannot fix is NOT written over a working one
        fb.probe_endpoint = lambda url, key=None, timeout=20: {
            "ok": False, "ids": [], "status": 401, "error": "HTTP 401"}
        fb._ask_secret = lambda prompt: "sk-still-bad"
        rc, out, err = call(fb, ["model", "endpoint", "https://api.broken.example/v1"],
                            stdin="", tty=True)
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("...and a key that is still refused writes nothing",
              rc == 1 and written["llm"]["base_url"] == "https://api.fixed.example/v1",
              (rc, written["llm"]["base_url"], err[:200]))
        os.environ.pop("TINYCMDR_LLM_API_KEY", None)
        fb.CONFIG["llm"]["api_key"] = ""

        # ---- `model setup` is the wizard, and bare `model` offers it -----------------
        # Operator: "A user has to type all that just to get the model change wizard to pop
        # up?" The wizard is reachable three ways now: the verb, the picker's first row, and
        # bare `model` when the endpoint is not usable.
        fb.probe_endpoint = lambda url, key=None, timeout=20: {
            "ok": True, "ids": ["w1", "w2"], "status": 200, "error": ""}
        rc, out, err = call(fb, ["model", "setup"], stdin="local\nhttps://api.wiz.example/v1\n1\n",
                            tty=True)
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("`model setup` runs the wizard and sets the primary",
              rc == 0 and written["llm"]["base_url"] == "https://api.wiz.example/v1"
              and written["llm"]["model"] == "w1",
              (rc, written["llm"].get("base_url"), written["llm"].get("model")))
        rc, out, err = call(fb, ["model", "setup"])
        check("...and without a terminal it names the shell instead of hanging",
              rc == 2 and "needs a terminal" in err, (rc, err[:200]))

        # the picker's first row is the same wizard - one ENTER, no command to remember
        saved_ready, saved_pick = fb.pick_terminal_ready, fb.run_model_pick
        calls = []

        def _capture_rows(state):
            calls.append([r[0] for r in state.rows])
            # The FIRST pick is the model list (choose the add-endpoint row); the wizard's
            # own model picker is the second, and cancelling it keeps the wizard's default.
            return fb.MODEL_PICK_ADD if len(calls) == 1 else None

        fb.pick_terminal_ready = lambda: True
        fb.run_model_pick = _capture_rows
        fb.CONFIG["llm"]["base_url"] = "http://127.0.0.1:9/v1"   # dead, so catalog is clean
        rc, out, err = call(fb, ["model"], stdin="local\nhttps://api.pick.example/v1\n1\n",
                            tty=True)
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("the picker leads with an add/change-endpoint row",
              (calls[0] if calls else [None])[0] == fb.MODEL_PICK_ADD,
              calls[:1])
        check("...and picking it runs the wizard",
              rc == 0 and written["llm"]["base_url"] == "https://api.pick.example/v1",
              (rc, written["llm"].get("base_url")))
        fb.pick_terminal_ready, fb.run_model_pick = saved_ready, saved_pick

        # ---- model add / remove: an endpoint has a route of its own -----------
        # (operator, 2026-09-22: "your solution to wire in another endpoint is to rerun
        # the installer?" - it never was one. Hand-editing config.json was the only way
        # in, and `model use` can only pick among endpoints already written.)
        ids = ["deepseek-chat", "deepseek-reasoner"]
        fb.probe_endpoint = lambda url, key=None: (
            {"ok": True, "ids": ids, "status": 200, "error": ""} if "api.deepseek" in url
            else {"ok": False, "ids": [], "status": None, "error": "connection refused"})

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
        check("a hosted PRIMARY without a key is refused, and names the .env fix",
              rc == 1 and ("API key" in err or "TINYCMDR_LLM_API_KEY" in err),
              (rc, err[:200]))
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

        # ---- model failover: the door that keeps operators out of config.json ------
        rc, out, err = call(fb, ["model", "failover"])
        check("model failover reports the flag", rc == 0 and "cloud failover:" in out, out[:160])
        rc, out, err = call(fb, ["model", "failover", "on"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("model failover on writes llm.allow_cloud_fallback",
              rc == 0 and written["llm"].get("allow_cloud_fallback") is True, (rc, err[:160]))
        rc, out, err = call(fb, ["model", "failover", "off"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("...and off turns it back off",
              rc == 0 and written["llm"].get("allow_cloud_fallback") is False, (rc, err[:160]))
        rc, out, err = call(fb, ["model", "failover", "maybe"])
        check("a bad value is refused", rc == 2, (rc, err[:120]))
        # ---- approvals: the confirm-gate allowlist has a verb ----------------
        rc, out, err = call(fb, ["approvals"])
        check("approvals reports the gate allowlist", rc == 0 and "allowlist" in out, out[:160])
        fb.confirm_allow("always")
        rc, out, err = call(fb, ["approvals"])
        check("...and shows a permanent approval", "for ever : yes" in out, out[:240])
        rc, out, err = call(fb, ["approvals", "clear"])
        check("approvals clear resets it",
              rc == 0 and fb.confirm_preapproved("x")[0] is False, (rc, err[:120]))

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

        rc, out, err = call(fb, ["config", "set", "agent.vision", "treu"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("a boolean key refuses a bare word (a string is always truthy)",
              rc == 2 and "true or false" in err and "vision" not in written["agent"],
              (rc, err[:160]))
        rc, out, err = call(fb, ["config", "set", "agent.vision", "true"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("...and takes the JSON boolean", rc == 0 and written["agent"]["vision"] is True,
              written["agent"].get("vision"))
        rc, out, err = call(fb, ["config", "set", "agent.vision", "false", "--str"])
        check("--str cannot hide a truthy string under a boolean key",
              rc == 2 and "true or false" in err, (rc, err[:160]))
        call(fb, ["config", "unset", "agent.vision"])
        rc, out, err = call(fb, ["config", "set", "agent.probe_port", "nope"])
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
        check("...and no .bak copy is left beside it",
              not any(p.name.startswith("tinycmdr.py.bak-update") for p in workdir.iterdir()),
              [p.name for p in workdir.iterdir()])
        rc, out, err = call(fb, ["update", str(cand)])
        check("updating with the same bytes is a no-op",
              rc == 0 and "nothing to do" in out, out[:120])
        bad = workdir / "bad.py"
        bad.write_text("print('not a build')\n", encoding="utf-8")
        rc, out, err = call(fb, ["update", str(bad)])
        check("update refuses a file that is not named tinycmdr.py, and says the rule",
              rc == 1 and "must be named tinycmdr.py" in err and "bad.py" in err,
              (rc, err[:200]))
        wrongly = workdir / "cand2" / "tinycmdr-cand.py"
        wrongly.parent.mkdir()
        wrongly.write_text((workdir / "tinycmdr.py").read_text(encoding="utf-8"),
                           encoding="utf-8")
        rc, out, err = call(fb, ["update", str(wrongly)])
        check("...even when the bytes ARE a good build",
              rc == 1 and "must be named tinycmdr.py" in err and "tinycmdr-cand.py" in err,
              (rc, err[:200]))
        fake = workdir / "fakebuild" / "tinycmdr.py"
        fake.parent.mkdir()
        fake.write_text("print('hello')\n", encoding="utf-8")
        rc, out, err = call(fb, ["update", str(fake)])
        check("...and a tinycmdr.py with no VERSION line",
              rc == 1 and "VERSION" in err, (rc, err[:160]))

        # ---- the rescue must not need git, or a clean tree ---------------------
        # The stranded installs are the ones 1.0.46-1.0.48 left behind: a git checkout
        # their own kit-prune made dirty, or no git at all. `update <package>` is the one
        # path they have, so pin that it lands with git absent and the tree looking like a
        # broken checkout.
        (workdir / ".git").mkdir(exist_ok=True)
        (workdir / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        pkg = workdir / "pkg" / "tinycmdr.py"
        pkg.parent.mkdir()
        pkg.write_text((workdir / "tinycmdr.py").read_text(encoding="utf-8")
                       .replace('VERSION = "', 'VERSION = "9.9.10-', 1), encoding="utf-8")
        _path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(workdir)      # nothing named git anywhere on it
        try:
            rc, out, err = call(fb, ["update", str(pkg)])
        finally:
            os.environ["PATH"] = _path
        check("update lands on a legacy git checkout with no git on PATH",
              rc == 0 and "9.9.10" in out, (rc, out[:200], err[:200]))
        check("...and the install now runs the new build",
              'VERSION = "9.9.10-' in (workdir / "tinycmdr.py").read_text(encoding="utf-8"))

        # ---- the primary path: bare `update`, from the release URL, no git ----------
        # The whole stranded-install incident was bare `update` needing git. Pin the real
        # path hermetically: serve a package and its SHA256SUMS over loopback, point update
        # at it, and prove it lands with no git on PATH and refuses a corrupted transfer.
        import hashlib
        import http.server
        import socketserver
        import threading
        import zipfile
        rel = workdir / "release"
        (rel / "pkg" / "tinycmdr-9.9.11").mkdir(parents=True)
        (rel / "pkg" / "tinycmdr-9.9.11" / "tinycmdr.py").write_text(
            (workdir / "tinycmdr.py").read_text(encoding="utf-8")
            .replace('VERSION = "', 'VERSION = "9.9.11-', 1), encoding="utf-8")
        asset = "tc-update-test.zip"
        with zipfile.ZipFile(rel / asset, "w") as z:
            z.write(rel / "pkg" / "tinycmdr-9.9.11" / "tinycmdr.py",
                    "tinycmdr-9.9.11/tinycmdr.py")
        digest = hashlib.sha256((rel / asset).read_bytes()).hexdigest()
        (rel / "SHA256SUMS").write_text("%s  %s\n" % (digest, asset), encoding="utf-8")

        class _Rel(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                f = rel / self.path.rsplit("/", 1)[-1]
                if not f.exists():
                    self.send_error(404)
                    return
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        srv = socketserver.TCPServer(("127.0.0.1", 0), _Rel)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        fb._update_asset = lambda: asset
        fb.CONFIG["agent"]["update_url"] = "http://127.0.0.1:%d" % srv.server_address[1]
        _p = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = str(workdir)          # nothing named git on it
            rc, out, err = call(fb, ["update"])
        finally:
            os.environ["PATH"] = _p
        check("bare update lands from the release URL with no git on PATH",
              rc == 0 and "9.9.11" in out, (rc, out[:200], err[:200]))
        check("...and the install runs the new build",
              'VERSION = "9.9.11-' in (workdir / "tinycmdr.py").read_text(encoding="utf-8"))

        (rel / "SHA256SUMS").write_text("%s  %s\n" % ("0" * 64, asset), encoding="utf-8")
        _before = (workdir / "tinycmdr.py").read_bytes()
        rc, out, err = call(fb, ["update"])
        check("a download that fails SHA256SUMS is refused",
              rc == 1 and "SHA256SUMS" in (out + err), (rc, (out + err)[:200]))
        check("...and nothing is written over the install",
              (workdir / "tinycmdr.py").read_bytes() == _before)
        srv.shutdown()
        srv.server_close()

        check("the new verbs are in the verb list",
              all(v in fb.VERBS for v in ("health", "config", "proc",
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
            # a value that can never work is REFUSED before it is written (the fleet Windows box,
            # 2026-10-02: a token of one 0x16 byte sat in .env while `token` said "set")
            rc, out, err = call(fb, ["token", "set", "TINYCMDR_TEST_KEY"], stdin="bad\x16value\n")
            check("token set refuses a value with control characters", rc == 1
                  and "control characters" in err, (rc, err[:160]))
            check("...and writes nothing", "bad" not in (workdir / ".env").read_text(encoding="utf-8"))
            rc, out, err = call(fb, ["token", "set", "TINYCMDR_TEST_KEY"], stdin="\ufeffbom-prefixed-1234\n")
            check("a UTF-8 BOM (what a PowerShell pipe adds) is stripped, and said so",
                  rc == 0 and "BOM" in out
                  and "TINYCMDR_TEST_KEY=bom-prefixed-1234" in (workdir / ".env").read_text(encoding="utf-8"),
                  (rc, out[:160]))
            rc, out, err = call(fb, ["token", "set", "TINYCMDR_MM_TOKEN"], stdin="short\n")
            check("a Mattermost token of the wrong shape is refused, with the shape named",
                  rc == 1 and "26 letters/digits" in err, (rc, err[:200]))
            # the provider is ASKED, and its answer is the operator's, at the prompt
            _probe_before = fb._token_probe
            try:
                fb._token_probe = lambda n, v: (True, "accepted as @the-bot")
                rc, out, err = call(fb, ["token", "set", "TINYCMDR_MM_TOKEN"], stdin="a" * 26 + "\n")
                check("a good token is probed and reported accepted",
                      rc == 0 and "accepted as @the-bot" in out, (rc, out[:200]))
                fb._token_probe = lambda n, v: (False, "HTTP 400 Bad Request at https://chat/api/v4/users/me")
                rc, out, err = call(fb, ["token", "set", "TINYCMDR_MM_TOKEN"], stdin="b" * 26 + "\n")
                check("a token the provider REFUSES is reported at the prompt, rc 1",
                      rc == 1 and "REFUSED" in err and "HTTP 400" in err, (rc, err[:200]))
                fb._token_probe = lambda n, v: (None, "could not reach https://chat: timed out")
                rc, out, err = call(fb, ["token", "set", "TINYCMDR_MM_TOKEN"], stdin="c" * 26 + "\n")
                check("an unreachable provider says so instead of claiming success",
                      rc == 0 and "unchecked" in out and "timed out" in out, (rc, out[:200]))
            finally:
                fb._token_probe = _probe_before
            # a second set replaces rather than appends
            call(fb, ["token", "set", "TINYCMDR_TEST_KEY"], stdin="second-value-9876\n")
            env_text = (workdir / ".env").read_text(encoding="utf-8")
            check("a repeated set replaces the line",
                  env_text.count("TINYCMDR_TEST_KEY=") == 1
                  and "second-value-9876" in env_text, env_text[:160])
        finally:
            os.environ.pop("TINYCMDR_MM_TOKEN", None)

        # ---- the update RULE: one command, any version ---------------------------
        # Every user, on every released version, types `tinycmdr update` (or
        # `/tinycmdr update` in chat) and it works. These pin the machinery that makes
        # that true: the published updater exists and is attached to releases, the
        # launchers fall back to it, and every lane can run the verb.
        check("the published updater exists for both platforms",
              (BASE / "update.sh").is_file() and (BASE / "update.ps1").is_file(),
              sorted(p.name for p in BASE.glob("update.*")))
        _rel = (BASE / "maintenance" / "release.sh").read_text(encoding="utf-8")
        check("...and every release attaches it",
              "dist/update.sh" in _rel and "dist/update.ps1" in _rel
              and "install.sh install.ps1 update.sh update.ps1 > SHA256SUMS" in _rel,
              "release.sh")
        _ush = (BASE / "update.sh").read_text(encoding="utf-8")
        check("...it verifies the download before touching the install",
              "SHA256SUMS" in _ush and "checksum mismatch" in _ush, "update.sh")
        check("...and leaves host-owned paths alone",
              "theme.toml" in _ush and "config.json" in _ush and "tools skills sessions" in _ush,
              "update.sh")
        _shim = (BASE / "tinycmdr").read_text(encoding="utf-8")
        check("the unix launcher falls back to the published updater for old installs",
              "def _verb_update" in _shim and "releases/latest/download/update.sh" in _shim,
              "shim")
        _cmd = (BASE / "tinycmdr.cmd").read_text(encoding="utf-8")
        check("...and so does the Windows launcher",
              "def _verb_update" in _cmd and "releases/latest/download/update.ps1" in _cmd,
              "shim")
        check("...and chat can run it, so `/tinycmdr update` works from a channel",
              "update" in fb._CHAT_VERB_SET, sorted(fb._CHAT_VERB_SET))
        _src = (BASE / "tinycmdr.py").read_text(encoding="utf-8")
        check("...a terminal session RUNS `/update` instead of printing a hint",
              'run_verb(["update"])' in _src
              and "`update` runs in a shell on the host" not in _src,
              "the inline handler")
        check("...and a chat update that changed the version restarts onto it",
              '"♻️ Restarting onto it now' in _src, "the chat handler")

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
        # app, so put EVERY host's helper where the verb looks for it and assert the verb
        # picked this host's. Only the .ps1 used to be staged here, so on macOS and Linux
        # _verb_restart found nothing, run_capture was never called, the helper read back
        # as '' and os.path.samefile('') raised FileNotFoundError - aborting the suite and
        # silently dropping every check after it (BUGREPORT T1: "~20 checks").
        (workdir / "maintenance").mkdir(exist_ok=True)
        host_helper = ("restart-tinycmdr.ps1" if os.name == "nt"
                       else "restart-tinycmdr-macos.sh" if sys.platform == "darwin"
                       else "restart-tinycmdr.sh")
        for _name in ("restart-tinycmdr.ps1", "restart-tinycmdr.sh",
                      "restart-tinycmdr-macos.sh"):
            (workdir / "maintenance" / _name).write_text(
                "# a helper for the suite\n", encoding="utf-8")

        saved_run, saved_elev, saved_running = fb.run_capture, fb._is_elevated, fb._verb_running
        saved_owned = getattr(fb, "_scheduled_task_owned", None)
        fb.run_capture = fake_run
        fb._is_elevated = lambda: True
        fb._verb_running = lambda: True
        _real_geteuid = getattr(os, "geteuid", None)
        try:
            # The checks below are about WHICH helper the verb calls, so the host's own
            # rights rule has to let the verb through. On Linux that rule is "root,
            # always" (asserted on its own further down) and CI runs as an ordinary user:
            # unpatchied, the verb refused, run_capture was never called, the helper read
            # back as '' and os.path.samefile('') raised FileNotFoundError - aborting the
            # suite and silently dropping every check after it.
            # os.geteuid does not exist on Windows - there is no uid, and `rights_needed`
            # below short-circuits on os.name == "nt" so it is never called. Reading it
            # unconditionally raised AttributeError and aborted the suite before a single
            # check ran (measured on Windows 11, 2026-09-29).
            if _real_geteuid is not None:
                os.geteuid = lambda: 0
            rc, out, err = call(fb, ["restart"])
            helper = (seen.get("argv") or [""])[-1]
            check("restart calls the shipped helper for this host",
                  os.path.basename(helper) == host_helper,
                  (host_helper, str(seen.get("argv"))))
            # samefile, not a string compare: on Windows mkdtemp can hand back the 8.3
            # short form of the user's temp path while the module resolved the long one
            check("...from THIS install, not somewhere else",
                  os.path.samefile(helper, workdir / "maintenance" / host_helper),
                  helper)
            check("...and says the instance is back", rc == 0 and "back up" in out,
                  (rc, out[:120], err[:160]))
            # The helper returns BEFORE the replacement is up (~60s handover on the Windows
            # task lane, measured twice 2026-10-03). Reporting that in-flight state as
            # "nothing holds the lock yet" reads as a failed restart, so the verb waits for
            # the outcome. Poll 0 keeps the suite fast; the stub flips True on the 3rd look.
            _saved_poll = fb._RESTART_LOCK_POLL
            _looks = {"n": 0}

            def _later():
                _looks["n"] += 1
                return _looks["n"] >= 3

            fb._RESTART_LOCK_POLL = 0
            fb._verb_running = _later
            rc, out, err = call(fb, ["restart"])
            check("restart waits for the replacement to take the lock (not a snapshot)",
                  rc == 0 and "back up" in out and _looks["n"] >= 3, (rc, out[:120], _looks))
            fb._RESTART_LOCK_POLL = _saved_poll
            fb._verb_running = lambda: True
            # Elevation is a PER-HOST rule, so assert this host's rule: Windows wants an
            # elevated shell for the task lane, Windows/Linux need root, and macOS needs
            # neither (launchd owns the process - exiting is the restart). The old check
            # demanded the refusal everywhere and would have aborted here on macOS, where
            # the helper is called instead.
            # the real rule again, so the checks below assert THIS host's rule
            if _real_geteuid is not None:
                os.geteuid = _real_geteuid
            fb._is_elevated = lambda: False
            if os.name == "nt":
                fb._scheduled_task_owned = lambda: True
            rights_needed = os.name == "nt" or (os.name == "posix"
                                                and sys.platform != "darwin"
                                                and os.geteuid() != 0)
            if rights_needed:
                fb.run_capture = boom_run
                rc, out, err = call(fb, ["restart"])
                check("without rights it says how to get them instead of failing oddly",
                      rc == 1 and ("elevated" in err or "root" in err), (rc, err[:200]))
            else:
                rc, out, err = call(fb, ["restart"])
                check("with no rights needed on this host, restart still calls the helper",
                      rc == 0 and "back up" in out, (rc, err[:160]))
        finally:
            if _real_geteuid is not None:
                os.geteuid = _real_geteuid
            fb.run_capture, fb._is_elevated, fb._verb_running = saved_run, saved_elev, saved_running
            if saved_owned is not None:
                fb._scheduled_task_owned = saved_owned

        # --- the local web UI is gone: `web` is not a verb, and nothing teaches it ------
        # It was a page lane reached through `tinycmdr web` / `--web`; the whole surface
        # (port, token, page) is removed. The word must fall through to the dispatcher that
        # names it, and the flag must be refused with the removal, both exercised at the
        # process boundary in the H1 block below.
        check("`web` is not a management verb", "web" not in fb.VERBS, sorted(fb.VERBS)[:6])
        check("VERB_HELP no longer names a page lane", "tinycmdr web" not in fb.VERB_HELP,
              fb.VERB_HELP[-220:])
        check("VERB_HELP stopped teaching the retired prefix", "/cmdr " not in fb.VERB_HELP,
              fb.VERB_HELP[-220:])
        check("no page flag survives in the help",
              not any(f in fb.VERB_HELP for f in ("--web", "--web-port", "--web-host",
                                                  "--no-web")), fb.VERB_HELP[-220:])
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

    # ---- update: the release artifact, never git --------------------------------
    # `update` was `git pull`; it now fetches the same verified package the one-line
    # installer does. Pointed at a dead host it must fail cleanly, and it must never go
    # looking for git at all.
    _saved_url = os.environ.get("TINYCMDR_UPDATE_URL")
    os.environ["TINYCMDR_UPDATE_URL"] = "http://127.0.0.1:1/releases"
    try:
        rc, out, err = call(fb, ["update"])
        check("update against an unreachable release host fails cleanly",
              rc == 1 and "could not download" in err and "Traceback" not in err,
              (rc, (err or out)[:200]))
        check("...and never mentions git", "git" not in (out + err).lower(),
              (out + err)[:160])
    finally:
        if _saved_url is None:
            os.environ.pop("TINYCMDR_UPDATE_URL", None)
        else:
            os.environ["TINYCMDR_UPDATE_URL"] = _saved_url

    # H1: the word after the program name is a VERB, never a reason to start the bot.
    # `tinycmdr taks` used to fall through to run_bot and bring up the agent,
    # which then answered nobody while the terminal looked fine. Staged fresh: the install
    # above is gone by this point, and the run must happen in a COPY or a verb writes its
    # log into the repo.
    stage = Path(tempfile.mkdtemp(prefix="fbtest-verbs-cli-"))
    try:
        shutil.copy2(BASE / "tinycmdr.py", stage / "tinycmdr.py")
        shutil.copy2(BASE / "tests" / "fixture-config.json", stage / "config.json")
        proc = subprocess.run([sys.executable, str(stage / "tinycmdr.py"), "taks"],
                              cwd=str(stage), capture_output=True, text=True, timeout=120,
                              env=dict(os.environ, TINYCMDR_PLAIN="1"))
        blob = proc.stdout + proc.stderr
        check("H1: an unknown verb exits 2", proc.returncode == 2, proc.returncode)
        check("H1: it NAMES the word it did not know",
              "unknown verb" in blob and "taks" in blob, blob[-200:])
        check("H1: and prints the verb list instead of starting the agent",
              "tinycmdr <verb>" in blob, blob[-300:])

        # The removed page lane: the word is an unknown verb, the flag is refused by name.
        for argv, want in ((["web"], "unknown verb"),
                           (["webui"], "unknown verb"),
                           (["page"], "unknown verb"),
                           (["--web"], "has been removed")):
            gone = subprocess.run([sys.executable, str(stage / "tinycmdr.py"), *argv],
                                  cwd=str(stage), capture_output=True, text=True,
                                  timeout=120, env=dict(os.environ, TINYCMDR_PLAIN="1"))
            gblob = gone.stdout + gone.stderr
            check("H1: `%s` exits 2" % " ".join(argv), gone.returncode == 2, gone.returncode)
            check("H1: `%s` says %r" % (" ".join(argv), want), want in gblob, gblob[-200:])
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    rc, out, err = call(fb, ["tasks"])
    check("H1: `tasks` is no longer a verb (the ledger was removed)",
          rc == 2 and "unknown verb" in err, (rc, err[:120]))

    print()
    _tail()
    return 1 if FAILS else 0


def _tail(aborted=""):
    """The suite's own count line, and the ONE contract run_all.py reads for it.

    Measured 2026-09-26 (BUGREPORT T1): a crash on a helper the suite never staged aborted
    this run mid-file and the checks after it vanished without a word - the runner could
    only print a traceback, so "how much of this suite graded" was unanswerable. `N passed,
    M failed` on the last line is that answer, and it prints on the way out of a crash too.
    """
    line = "%d passed, %d failed" % (len(PASSES), len(FAILS))
    print(line)
    if aborted:
        print("ABORTED after %d check(s): %s" % (len(PASSES) + len(FAILS), aborted))


if __name__ == "__main__":
    _abort = ""
    try:
        _rc = main()
    except SystemExit as e:
        raise e                                 # main() already said what it means
    except BaseException as e:                   # noqa: BLE001 - a crash is a red run
        traceback.print_exc()
        _abort = "%s: %s" % (type(e).__name__, e)
        _rc = 1
    if _abort:
        _tail(_abort)
    sys.exit(_rc)
