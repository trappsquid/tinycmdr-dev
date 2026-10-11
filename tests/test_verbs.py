"""The management verbs: they answer, they never call the model, they never print a
secret (2026-09-22).

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
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
sys.path.insert(0, str(TESTS))

import hermetic  # noqa: E402
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


def _body():
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

        # ---- the door has to be executable -------------
        # A macOS host answered "/usr/local/bin/tinycmdr: line 2: ... Permission
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
        # suites, the CI workflow, the docs and the maintenance kit. It now deletes those BY
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
            check("pruning removes the project's kit (dirs, files, maintenance scripts)",
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
                  (_ptmp / "tests").exists() and _note2 == "", _note2)
            # No declaration file at all: the tree's own EVIDENCE must hold the prune
            # off, because the prune deletes where.py - the only tool that could have
            # declared the tree. Keeping the kit is silent: it is the normal outcome, and
            # the old note named a declaration file this tree does not have.
            (_ptmp / "maintenance" / "where-roles.json").unlink()
            (_ptmp / "tests" / "run_all.py").write_text("x", encoding="utf-8")
            _note3 = fb._prune_dev_kit()
            check("a checkout with no declaration is not pruned (tests/run_all.py is evidence)",
                  (_ptmp / "tests").exists() and _note3 == "", _note3)
            (_ptmp / "tests" / "run_all.py").unlink()
            (_ptmp / ".git").mkdir()
            _note4 = fb._prune_dev_kit()
            check("...and a tree with .git is not pruned either",
                  (_ptmp / "tests").exists() and _note4 == "", _note4)
            (_ptmp / ".git").rmdir()
        finally:
            fb.BASE_DIR = _saved_base
            shutil.rmtree(_ptmp, ignore_errors=True)

        # _apply_package() writes the package over the install. It must SEED a host-owned
        # path and never overwrite one: 1.0.49 protected only soul.md, so an edited
        # tools/patch.py or skills/README.md - tools/ being exactly where the agent is told
        # to write its own tools - was silently replaced.
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

        # The equal-VERSION short-circuit must compare CONTENT, not the label: a
        # half-applied update carries the new VERSION while some files are still old, and
        # "already up to date" used to refuse to repair it - while pruning the dev kit on
        # The way out.
        _dtmp = Path(tempfile.mkdtemp(prefix="fbtest-differs-"))
        _dpkg = Path(tempfile.mkdtemp(prefix="fbtest-differs-pkg-"))
        try:
            fb.BASE_DIR = _dtmp
            (_dtmp / "tinycmdr.py").write_text("same", encoding="utf-8")
            (_dpkg / "tinycmdr.py").write_text("same", encoding="utf-8")
            check("an identical package is a true no-op",
                  fb._package_differs(_dpkg) is False)
            (_dpkg / "tinycmdr.py").write_text("TWO", encoding="utf-8")
            check("a same-VERSION package with different bytes is NOT 'up to date'",
                  fb._package_differs(_dpkg) is True)
            (_dtmp / "tinycmdr.py").write_text("TWO", encoding="utf-8")
            (_dtmp / "tools").mkdir()
            (_dpkg / "tools").mkdir()
            (_dtmp / "tools" / "mine.py").write_text("MINE", encoding="utf-8")
            (_dpkg / "tools" / "mine.py").write_text("SHIPPED", encoding="utf-8")
            check("...and a host-owned difference is not a repair (never overwritten)",
                  fb._package_differs(_dpkg) is False)
            (_dpkg / "install").mkdir()
            (_dpkg / "install" / "x.sh").write_text("new", encoding="utf-8")
            check("...while a MISSING package file is", fb._package_differs(_dpkg) is True)
        finally:
            fb.BASE_DIR = _ab
            shutil.rmtree(_dtmp, ignore_errors=True)
            shutil.rmtree(_dpkg, ignore_errors=True)

        # A release can raise a dependency bound. Nothing installs from the file on
        # its own, so the update verb must SAY the exact command when it writes
        # requirements.txt, and the import guard must point at the file instead of a
        # Hand-maintained module list.
        _tsrc = (BASE / "tinycmdr.py").read_text(encoding="utf-8")
        check("the update verb names the pip command when requirements.txt changes",
              "dependencies changed in this release" in _tsrc
              and "pip install -r %s" in _tsrc)
        check("...and the mmpy_bot guard points at requirements.txt, not a module list",
              "pip install requests mmpy_bot croniter" not in _tsrc)

        # The host-owned rule is written out SIX times - the three installers, the two
        # standalone updaters the launcher falls back to for an old or broken install
        # (update.sh / update.ps1), and the update path in tinycmdr.py - and they ALREADY
        # disagreed once: snapshots/ and tmp/ were the installers' and not the updater's.
        # This parse used to read only the three installers, which is how the two updaters
        # kept missing that same pair while a commit message claimed every copy had been
        # updated (measured 2026-10-07 A-2026-10-07-05): grade BOTH
        # that every dir any copy names is covered here, and that the copies agree.
        _lists = {}
        for _rel in ("install/install-tinycmdr.sh", "install/install-tinycmdr-macos.sh",
                     "update.sh"):
            _m = re.search(r'HOST_DIRS="([^"]+)"',
                           (BASE / _rel).read_text(encoding="utf-8"))
            if _m:
                _lists[_rel] = set(_m.group(1).split())
        for _rel in ("install/install-tinycmdr.ps1", "update.ps1"):
            # $hostDirs in the installer, $HostDirs in the updater: PowerShell is
            # case-insensitive, a python parse is not.
            _m = re.search(r"\$[Hh]ostDirs\s*=\s*@\(([^)]*)\)",
                           (BASE / _rel).read_text(encoding="utf-8"), re.S)
            if _m:
                _lists[_rel] = set(re.findall(r'"([^"]+)"', _m.group(1)))
        _hostdirs = set().union(*_lists.values()) if _lists else set()
        _covered = {p.rstrip("/") for p in fb._HOST_OWNED_PREFIXES}
        _covered |= {"dist", ".git"}          # skipped by name in _apply_package
        _missing = sorted(d for d in _hostdirs if d not in _covered)
        check("the update path's host-owned set covers every installer dir",
              bool(_hostdirs) and not _missing, (_missing, sorted(_hostdirs)))
        check("...and all six copies of that rule name the same directories",
              len(_lists) == 5 and len({frozenset(v) for v in _lists.values()}) == 1,
              {k: sorted(v) for k, v in sorted(_lists.items())})

        # _dev_tree_reason() decides whether pruning is SAFE here, so grade both
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
                  fb._dev_tree_reason() is None)
            _wr.write_text('[{"role":"dev","same_as":"live"}]', encoding="utf-8")
            check("...but 'dev: same_as live' does", fb._dev_tree_reason() == "declared")
            _wr.write_text('[{"role":"dev","path":"%s"}]' % fb.BASE_DIR, encoding="utf-8")
            check("...and an explicit dev path pointing here does",
                  fb._dev_tree_reason() == "declared")
            _wr.write_text("{ not json", encoding="utf-8")
            check("an unreadable declaration is treated as dev (never prune on doubt)",
                  fb._dev_tree_reason() == "declared")
        finally:
            if _saved is None:
                _wr.unlink(missing_ok=True)
            else:
                _wr.write_text(_saved, encoding="utf-8")

        # ---- status: an endpoint that says nothing, then one that answers ----
        saved_detect = fb._detect_window

        def forget_probes():
            """Drop the probe caches the agent holds, whatever this build calls them.

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
        # An install that predates the page has no token; doctor is where that state is
        # named (it is a note, not a problem - the next start mints one).
        check("doctor names the page and its token state",
              "page      : on, port" in out
              and "no token yet - the next start mints one" in out, out[-500:])

        fb._detect_window = lambda url, headers=None: 0
        forget_probes()
        rc, out, err = call(fb, ["doctor"])
        check("doctor exits 1 when the endpoint does not answer", rc == 1, rc)
        check("...and names the endpoint on stderr",
              "did not answer" in err and fb.CONFIG["llm"]["base_url"] in err, err[:300])
        check("doctor never prints a secret value",
              "fixture-token" not in out and "fixture-token" not in err, out[:200])

        # A config that names a provider this build has no adapter for is listed like
        # any other entry: `tavily` was removed as a built-in, and the open contract is
        # a JSON POST to whatever url the entry carries - so there is no special case,
        # no complaint, and the rest of the chain stays intact.
        _saved_providers = fb.CONFIG["search"].get("providers")
        fb.CONFIG["search"]["providers"] = [
            {"kind": "tavily", "url": "https://api.tavily.com/search",
             "label": "tavily"},
            {"kind": "anysearch", "url": "https://api.anysearch.com/v1/search",
             "label": "anysearch", "api_key_env": "ANYSEARCH_API_KEY"}]
        try:
            rc, out, err = call(fb, ["doctor"])
            check("doctor lists a config-named provider like any other, chain intact",
                  "tavily (off-LAN, refused)" in out
                  and "anysearch (off-LAN, refused)" in out
                  and "unknown kind" not in out,
                  [l for l in out.splitlines() if "search" in l][:3])
        finally:
            fb.CONFIG["search"]["providers"] = _saved_providers

        # ---- the no-lane note tells the truth about the door -------------------
        # doctor said "CLI-only install" whenever there was no chat lane, but a lane-less
        # host with web ON serves the page - the decided default door (A-2026-10-08-139).
        _saved = (dict(fb.CONFIG["mattermost"]), dict(fb.CONFIG["telegram"]),
                  fb.CONFIG["web"].get("enabled"))
        _saved_env = {k: os.environ.pop(k) for k in ("TINYCMDR_MM_TOKEN", "TINYCMDR_TG_TOKEN")
                      if k in os.environ}
        fb.CONFIG["mattermost"]["token"] = ""
        fb.CONFIG["telegram"]["token"] = ""
        try:
            fb.CONFIG["web"]["enabled"] = True
            rc, out, err = call(fb, ["doctor"])
            check("a lane-less host with the page on is told the page is the door",
                  "the page serves this host" in out and "CLI-only install" not in out,
                  out[:400])
            fb.CONFIG["web"]["enabled"] = False
            rc, out, err = call(fb, ["doctor"])
            check("...and with the page off, CLI-only is what it says",
                  "CLI-only install" in out, out[:400])
        finally:
            fb.CONFIG["mattermost"].update(_saved[0])
            fb.CONFIG["telegram"].update(_saved[1])
            fb.CONFIG["web"]["enabled"] = _saved[2]
            os.environ.update(_saved_env)

        # ---- the firewall note: a LAN bind is where a firewall eats the page ------
        # The bind never needs root; the firewall HOLE does, and a service cannot answer an
        # interactive prompt. The note's mapping is graded per OS here, because the real
        # branches need a firewall this bed does not have (the installers carry the same
        # text at install time; `setup`, `doctor` and the startup announce carry it when a
        # running install is switched to the LAN).
        _real_platform, _real_osname, _real_capture = (fb.sys.platform, fb.os.name,
                                                       fb.run_capture)
        try:
            # Emulating an OS means setting BOTH symbols this function reads: the Windows
            # branch is `os.name == "nt"` while macOS is `sys.platform == "darwin"`. Setting
            # sys.platform alone still left os.name "nt" on a Windows runner, so _firewall_note
            # took the Windows branch and all four mappings below graded Windows Defender
            # text (CI, 2026-10-08). "posix" is what os.name already is on macOS and Linux,
            # so the emulation is now the same on every host.
            fb.os.name = "posix"
            fb.sys.platform = "darwin"
            fb.run_capture = lambda *a, **k: (0, "Firewall is enabled. (State = 1)", "", False)
            mac = fb._firewall_note(8790)
            check("a macOS LAN bind names socketfilterfw when the firewall is on",
                  any("socketfilterfw --add" in l for l in mac)
                  and any("unblockapp" in l for l in mac), mac)
            fb.run_capture = lambda *a, **k: (0, "Firewall is disabled. (State = 0)", "", False)
            check("...and says nothing when it is off", fb._firewall_note(8790) == [])

            fb.sys.platform = "linux"

            def _linux_probe(argv, *a, **k):
                cmd = " ".join(argv)
                if "command -v ufw" in cmd:
                    return (0, "/usr/sbin/ufw", "", False)
                if "command -v firewall-cmd" in cmd:
                    return (0, "/usr/sbin/firewall-cmd", "", False)
                if "ufw status" in cmd:
                    return (0, "Status: active\n", "", False)
                if "firewall-cmd --state" in cmd:
                    return (0, "running", "", False)
                return (1, "", "", False)

            fb.run_capture = _linux_probe
            lin = fb._firewall_note(8790)
            check("a Linux LAN bind names ufw and firewalld when they are active",
                  any("ufw allow 8790/tcp" in l for l in lin)
                  and any("firewall-cmd" in l for l in lin), lin)
            fb.run_capture = lambda *a, **k: (1, "", "", False)
            check("...and stays quiet with neither", fb._firewall_note(8790) == [])
        finally:
            fb.sys.platform, fb.os.name, fb.run_capture = (_real_platform, _real_osname,
                                                           _real_capture)

        # ---- the completion: the port is opened, or the state is named ------------
        # `setup` is the door that can be told "reachable from other machines" long
        # after install; the answer completes there the way the installers complete it
        # (the run itself with the rights, one sudo/UAC offer without them, the exact
        # command otherwise), and every branch ends judged against what the OS tools
        # report - measured 2026-10-10: setup printed advice only, checked nothing, and
        # said nothing at all where the probes found no firewall.
        _real_elev, _real_exe, _real_stdin = fb.is_elevated, fb.sys.executable, fb.sys.stdin
        _real_platform, _real_osname, _real_capture = (fb.sys.platform, fb.os.name,
                                                       fb.run_capture)

        try:
            _complete = getattr(fb, "_firewall_complete", None)
            _call = _complete if callable(_complete) else (lambda *a, **k: [])
            check("the wizard can complete the LAN answer", callable(_complete))

            fb.os.name = "posix"
            fb.sys.platform = "darwin"
            fb.sys.executable = "/opt/probe/venv/bin/python"
            _runs, _alf = [], {"listed": False}

            def _mac(argv, *a, **k):
                cmd = " ".join(argv)
                _runs.append(cmd)
                if "--getglobalstate" in cmd:
                    return (0, "Firewall is enabled. (State = 1)", "", False)
                if "--listapps" in cmd:
                    if _alf["listed"]:
                        return (0, "1 : %s\n             (Allow incoming connections)"
                                % fb.sys.executable, "", False)
                    return (0, "", "", False)
                if "--add" in cmd or "--unblockapp" in cmd:
                    _alf["listed"] = True
                return (0, "", "", False)

            fb.run_capture = _mac
            fb.is_elevated = lambda: True
            fb.sys.stdin = FakeTTY("")
            mac = _call(8790) or ["(no lines)"]
            check("macOS: a run with the rights allows the interpreter, judged by ALF's list",
                  "allowed" in mac[0] and "the firewall is ON" in mac[0]
                  and any("--add" in r and "sudo" not in r for r in _runs)
                  and sum("--listapps" in r for r in _runs) == 2, (mac, _runs))

            _alf["listed"], _runs[:] = True, []
            mac = _call(8790) or ["(no lines)"]
            check("...an interpreter ALF already allows is not touched again",
                  "allowed already" in mac[0] and not any("--add" in r for r in _runs), mac)

            _alf["listed"], _runs[:] = False, []
            fb.is_elevated = lambda: False
            fb.sys.stdin = FakeTTY("y\n")
            mac = _call(8790) or ["(no lines)"]
            check("...a terminal without the rights gets one sudo offer, and it runs",
                  any(r.startswith("sudo") and "--add" in r for r in _runs)
                  and any(r.startswith("sudo") and "--unblockapp" in r for r in _runs)
                  and "allowed" in mac[0], (mac, _runs))

            _alf["listed"], _runs[:] = False, []
            fb.sys.stdin = FakeTTY("n\n")
            mac = _call(8790) or ["(no lines)"]
            check("...a declined offer names the state and keeps the exact commands",
                  "still closed" in mac[0] and "socketfilterfw --add" in " ".join(mac)
                  and not any("--add" in r for r in _runs), mac)

            fb.run_capture = lambda *a, **k: (0, "Firewall is disabled. (State = 0)", "", False)
            mac = _call(8790) or ["(no lines)"]
            check("macOS: a firewall that is off is said to be off", "off" in mac[0], mac)

            fb.sys.platform = "linux"
            _ufw = {"allowed": False}

            def _linux(argv, *a, **k):
                cmd = " ".join(argv)
                _runs.append(cmd)
                if "command -v ufw" in cmd:
                    return (0, "/usr/sbin/ufw", "", False)
                if "command -v firewall-cmd" in cmd:
                    return (1, "", "", False)
                if "ufw status" in cmd:
                    return (0, "Status: active\n8790/tcp ALLOW Anywhere\n" if _ufw["allowed"]
                            else "Status: active\n", "", False)
                if "ufw allow" in cmd:
                    _ufw["allowed"] = True
                return (0, "", "", False)

            fb.run_capture = _linux
            fb.is_elevated = lambda: True
            _runs[:] = []
            lin = _call(8790) or ["(no lines)"]
            check("Linux: a root run opens ufw and proves it from ufw's own status",
                  "ufw allowed 8790/tcp" in lin[0]
                  and any(r == "ufw allow 8790/tcp" for r in _runs)
                  and any(r == "ufw status" for r in _runs), (lin, _runs))

            _ufw["allowed"], _runs[:] = False, []
            fb.is_elevated = lambda: False
            fb.sys.stdin = FakeTTY("y\n")
            lin = _call(8790) or ["(no lines)"]
            check("...a non-root run is offered the one sudo prompt",
                  "ufw allowed" in lin[0]
                  and any(r == "sudo ufw allow 8790/tcp" for r in _runs), (lin, _runs))

            _ufw["allowed"], _runs[:] = False, []
            fb.sys.stdin = FakeTTY("n\n")
            lin = _call(8790) or ["(no lines)"]
            check("...and declining prints the exact command instead of silence",
                  "not opened" in lin[0] and "sudo ufw allow 8790/tcp" in " ".join(lin)
                  and not any("ufw allow" in r for r in _runs), lin)

            fb.run_capture = lambda *a, **k: (1, "", "", False)
            lin = _call(8790) or ["(no lines)"]
            check("Linux: no active firewall is said, not silently skipped",
                  "no active firewall" in lin[0], lin)

            fb.os.name = "nt"
            _ntp = {"present": False}

            def _win(argv, *a, **k):
                cmd = " ".join(argv)
                _runs.append(cmd)
                if "New-NetFirewallRule" in cmd:
                    if "Start-Process" not in cmd:
                        _ntp["present"] = True
                    return (0, "", "", False)
                if "Get-NetFirewallRule" in cmd:
                    return (0, "present" if _ntp["present"] else "absent", "", False)
                return (0, "", "", False)

            fb.run_capture = _win
            fb.is_elevated = lambda: True
            _runs[:] = []
            nt = _call(8790) or ["(no lines)"]
            check("Windows: an elevated run creates the rule and re-reads it",
                  "opened port 8790" in nt[0]
                  and any("New-NetFirewallRule" in r for r in _runs), (nt, _runs))

            _ntp["present"], _runs[:] = False, []
            fb.is_elevated = lambda: False
            fb.sys.stdin = FakeTTY("y\n")
            nt = _call(8790) or ["(no lines)"]
            check("...and one UAC prompt runs the same rule text the warning prints",
                  any("Start-Process" in r and "-Verb RunAs" in r
                      and "New-NetFirewallRule" in r for r in _runs), _runs)
        finally:
            fb.is_elevated, fb.sys.executable = _real_elev, _real_exe
            fb.sys.stdin = _real_stdin
            fb.sys.platform, fb.os.name, fb.run_capture = (_real_platform, _real_osname,
                                                           _real_capture)

        # ---- a stdin that REPORTS a tty and cannot answer -------------------------
        # The Windows NUL device is a character device, so sys.stdin.isatty() is True for a
        # service, a scheduled task or `< NUL`: the wizard took its interactive branch and the
        # first input() raised EOFError, killing the verb with a traceback instead of the
        # sentence this file already had for a stdin it cannot use (CI, 2026-10-08). The same
        # shape is built here: isatty says yes, stdin is at EOF.
        _real_isatty, _real_stdin = fb.sys.stdin.isatty, fb.sys.stdin
        _eof_handle = open(os.devnull)
        try:
            fb.sys.stdin.isatty = lambda: True
            fb.sys.stdin = _eof_handle
            _err = io.StringIO()
            with contextlib.redirect_stderr(_err):
                _rc = fb.run_setup()
            check("a stdin that cannot answer gets the sentence, not a traceback",
                  _rc == 1 and "requires an interactive terminal" in _err.getvalue(),
                  (_rc, _err.getvalue()[:120]))
            # ...and the door CI actually hit: `model setup` has its own guard, its own
            # usage text and its own exit code, and the same NUL shape walked past the guard.
            _rc2, _out2, _err2 = call(fb, ["model", "setup"])   # call() captures both streams
            check("...and the model door says its piece instead of tracing back",
                  _rc2 == 2 and "needs a terminal to ask on" in _err2,
                  (_rc2, _err2[:160]))
        finally:
            fb.sys.stdin = _real_stdin
            fb.sys.stdin.isatty = _real_isatty
            _eof_handle.close()

        # ---- the update path introduces the page --------------------------------
        # The published updaters are the one code that runs on EVERY released version, so
        # the ask lives there: a host with no page token is offered the mint (the default)
        # or its own token, and handed the link. The new build's first start also mints
        # and orients (graded in test_webui).
        for rel, pats in (
                ("install/install-tinycmdr.sh",
                 ("port $WEB_PORT needs root", "ufw allow ${WEB_PORT}/tcp",
                  "firewall-cmd --permanent --add-port=${WEB_PORT}/tcp", "rights      :")),
                ("install/install-tinycmdr-macos.sh",
                 ("port $WEB_PORT needs root", "socketfilterfw --add", "rights      :")),
                ("install/install-tinycmdr.ps1",
                 ("ports below 1024 are privileged", "New-NetFirewallRule",
                  "WindowsBuiltInRole]::Administrator"))):
            body = (BASE / rel).read_text(encoding="utf-8")
            missing = [pat for pat in pats if pat not in body]
            check("%s refuses a privileged port without rights and names the firewall"
                  % rel, not missing, missing)

        upd_sh = (BASE / "update.sh").read_text(encoding="utf-8")
        check("update.sh asks for the page token when a host has none",
              "TINYCMDR_WEB_TOKEN" in upd_sh
              and "Page token (empty mints one" in upd_sh
              and "page link: http://127.0.0.1:" in upd_sh, upd_sh[-300:])
        upd_ps = (BASE / "update.ps1").read_text(encoding="utf-8")
        check("update.ps1 asks too, and never blocks a non-interactive run",
              "TINYCMDR_WEB_TOKEN" in upd_ps
              and "Page token (empty mints one" in upd_ps
              and "UserInteractive" in upd_ps, upd_ps[-300:])
        check("both updaters leave an existing token alone",
              "grep -q '^TINYCMDR_WEB_TOKEN='" in upd_sh
              and "TINYCMDR_WEB_TOKEN=" in upd_ps, "the guard is missing")

        # ---- the persona: which soul this agent is actually running ----------------
        # soul.md is the one SHIPPED file an operator is invited to edit: nothing used to
        # say whether a box ran the shipped identity or somebody's edit, and `update`
        # (an artifact install, not git) must never quietly replace the persona.
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
            check("...with a note that update never overwrites the edit",
                  "never overwrites it" in out and "copied aside" in out
                  and "reset --hard" not in out, out[-300:])

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

        # ---- a cloud endpoint on an ASSUMED window is told the lever --------
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
        # you had to already know. the predecessor harness opens a list you move through; this is that list.
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

        # ---- the setup wizard: no secret echoed, the live CONFIG kept whole, and a
        #      concurrent write not reverted ------------------------------------
        # A config.json the FILE does not fill in: the live CONFIG is a deep merge with
        # the shipped defaults, and the wizard must leave every defaulted key in place.
        _wiz_cfg = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        _wiz_cfg["llm"].pop("max_turns", None)
        _wiz_cfg["llm"]["fallbacks"] = []
        _wiz_cfg["agent"].pop("max_steps", None)
        (workdir / "config.json").write_text(json.dumps(_wiz_cfg, indent=2),
                                             encoding="utf-8")
        _seen_prompts = []
        _canned = {"Mattermost bot token": "MTGATEWAYFIXTURE0000000000",
                   "Telegram bot token": "123456789:FIXTURE-TG-TOKEN-" + "0" * 15,
                   "Access token": "web-tok-1-abcdefghij",
                   "API key": "sk-canned"}
        _real_secret = fb._ask_secret

        def _recording_secret(prompt):
            _seen_prompts.append(prompt)
            for _ck, _cv in _canned.items():
                if _ck in prompt:
                    return _cv
            return ""

        _saved_target = fb._ask_model_target

        def _target_stub(default_url="", default_model="", default_key=""):
            # a concurrent writer lands while the wizard is open (a chat /model use)
            _live = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
            _live.setdefault("agent", {})["max_steps"] = 7
            (workdir / "config.json").write_text(json.dumps(_live, indent=2),
                                                 encoding="utf-8")
            return {"url": "http://127.0.0.1:8081/v1", "model": "main", "key": ""}

        fb._ask_model_target = _target_stub
        fb._ask_secret = _recording_secret
        _saved_envs = {k: os.environ.get(k) for k in ("TINYCMDR_MODEL",
                                                      "TINYCMDR_WEB_TOKEN")}
        os.environ["TINYCMDR_MODEL"] = "env-fixed-model"
        try:
            rc, out, err = call(
                fb, ["setup"],
                stdin="\n".join(["y", "", "", "y", "", "", "", "", "", "",
                                 "", "", ""]),
                tty=True)
        finally:
            for _k, _v in _saved_envs.items():
                if _v is None:
                    os.environ.pop(_k, None)
                else:
                    os.environ[_k] = _v
            fb._ask_model_target = _saved_target
            fb._ask_secret = _real_secret
        _wiz_written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        _wiz_env = (workdir / ".env").read_text(encoding="utf-8")
        check("the wizard runs to the end", rc == 0, (rc, err[:200]))
        check("the Mattermost token is asked for through the no-echo reader",
              any("Mattermost bot token" in p for p in _seen_prompts), _seen_prompts)
        check("...and the Telegram token, and the page token",
              any("Telegram bot token" in p for p in _seen_prompts)
              and any("Access token" in p for p in _seen_prompts), _seen_prompts)
        check("...and what it read landed in .env",
              "TINYCMDR_MM_TOKEN=MTGATEWAYFIXTURE0000000000" in _wiz_env
              and ("TINYCMDR_TG_TOKEN=123456789:FIXTURE-TG-TOKEN-" + "0" * 15) in _wiz_env
              and "TINYCMDR_WEB_TOKEN=web-tok-1-abcdefghij" in _wiz_env, _wiz_env[-260:])
        check("a write made while the wizard was open survives it",
              _wiz_written.get("agent", {}).get("max_steps") == 7,
              _wiz_written.get("agent"))
        check("the live CONFIG is the FULL merge again, not the bare file",
              bool(fb.CONFIG["llm"].get("max_turns"))
              and bool(fb.CONFIG["agent"].get("max_steps"))
              and fb.CONFIG["llm"].get("model") == "env-fixed-model",
              (fb.CONFIG["llm"].get("max_turns"), fb.CONFIG["agent"].get("max_steps"),
               fb.CONFIG["llm"].get("model")))

        # ...the model key too: section 1 of the same wizard, called directly
        _seen_prompts.clear()
        _saved_kind, _saved_choose = fb._ask_endpoint_kind, fb._choose_one_model
        fb._ask_endpoint_kind = lambda default: "cloud"
        fb._choose_one_model = lambda url, ids, default: "m1"
        fb.probe_endpoint = lambda url, key=None, timeout=20: {
            "ok": True, "ids": ["m1"], "status": 200, "error": ""}
        fb._ask_secret = _recording_secret
        _saved_stdin = sys.stdin
        sys.stdin = FakeTTY("\n\n")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                fb._ask_model_target("https://api.example.com/v1", "m0", "")
        finally:
            sys.stdin = _saved_stdin
            fb._ask_endpoint_kind, fb._choose_one_model = _saved_kind, _saved_choose
            fb._ask_secret = _real_secret
        check("the model key is read through the no-echo reader as well",
              any("API key" in p for p in _seen_prompts), _seen_prompts)

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
        # A-2026-10-05-68: a 3+ segment path whose parent was missing wrote a LITERAL
        # top-level dotted key ("agent.mcp_servers.demo.cmd" became the key
        # "agent.mcp_servers.demo"), which unset could not reach - the write silently missed
        # the path the operator named. Exercised under a real data map, because a made-up
        # top-level section is REFUSED now (A-2026-10-07-76).
        rc, out, err = call(fb, ["config", "set", "agent.mcp_servers.demo.cmd", "run-me"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        _mcp = written["agent"]["mcp_servers"]
        check("a deep config set creates the intermediate sections",
              rc == 0 and _mcp.get("demo", {}).get("cmd") == "run-me"
              and "demo.cmd" not in _mcp, (rc, _mcp.get("demo"), sorted(_mcp)[:4]))
        rc, out, err = call(fb, ["config", "get", "agent.mcp_servers.demo.cmd"])
        check("...and reads back at the path that was set",
              rc == 0 and "run-me" in out, out[:80])
        # A-2026-10-08-132: the read-back walked only one dotted level, so a depth-3
        # write of a name->spec key answered "set = (gone)" while config.json held the
        # value - the confirmation said the write was lost.
        rc, out, err = call(fb, ["config", "set", "llm.thinking_budgets.high", "4000"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("a depth-3 set reads the value back, not (gone)",
              rc == 0 and "4000" in out and "(gone)" not in out
              and written["llm"]["thinking_budgets"]["high"] == 4000, (rc, out[:120]))
        rc, out, err = call(fb, ["config", "get", "agent.mcp_servers.nope.cmd"])
        check("a deep get on a missing parent says (not set), rc=0",
              rc == 0 and "(not set)" in out, (rc, out[:80]))
        call(fb, ["config", "set", "agent.mcp_servers.demo", "scalar"])
        rc, out, err = call(fb, ["config", "set", "agent.mcp_servers.demo.cmd", "v2"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("a scalar in the middle is refused, not shadowed by a flat dotted key",
              rc == 1 and "is not a section" in err
              and written["agent"]["mcp_servers"]["demo"] == "scalar",
              (rc, err[:160], written["agent"]["mcp_servers"].get("demo")))
        # A-2026-10-05-69: the verb line is logged BEFORE dispatch, so a refused secret
        # write still put its VALUE in tinycmdr.log in cleartext.
        # Grade what the verb LOGGED, not a file: run_all points every suite at one log
        # path, and its size rotation can move the fresh line into the predecessor while
        # the check reads the new empty file (green under --select, red under the gate).
        _lines = []

        class _Capture(logging.Handler):
            def emit(self, record):
                _lines.append(record.getMessage())

        _cap = _Capture()
        logging.getLogger().addHandler(_cap)
        try:
            rc, out, err = call(fb, ["config", "set", "mattermost.token", "SENTINEL-a69"])
        finally:
            logging.getLogger().removeHandler(_cap)
        logged = "\n".join(str(x) for x in _lines)
        check("a secret value never reaches the verb log",
              rc == 2 and "SENTINEL-a69" not in logged and "<redacted>" in logged,
              (rc, err[:80], [l for l in logged.splitlines() if "mattermost.token" in l][:1]))
        # A-2026-10-05-72: verbs are case-insensitive but keys are not, and a flipped key
        # answered (not set) rc=0 - silence that reads as "no such setting".
        rc, out, err = call(fb, ["config", "get", "AGENT.max_steps"])
        check("a case-flipped SECTION gets a near-miss hint",
              rc == 0 and "(not set)" in out and "agent.max_steps" in out, out[:80])
        rc, out, err = call(fb, ["config", "get", "agent.MAX_STEPS"])
        check("...and so does a flipped leaf", rc == 0 and "agent.max_steps" in out, out[:80])
        rc, out, err = call(fb, ["config", "get", "zzzz.nope"])
        check("a real miss stays plain (no invented hint)", out.strip() == "(not set)",
              out[:60])
        rc, out, err = call(fb, ["config", "set", "agent.digest_lines", "nope"])
        check("an unparseable value becomes a string, not a crash", rc == 0, (rc, err[:160]))
        rc, out, err = call(fb, ["config", "set", "mattermost.token", "oops"])
        check("config refuses to put a secret in config.json",
              rc == 2 and ".env" in err, (rc, err[:160]))
        rc, out, err = call(fb, ["config", "set", "not a key", "x"])
        check("config refuses a key that is not a dotted path", rc == 2, rc)
        rc, out, err = call(fb, ["config", "get", "nope.nothing"])
        check("config get on a missing key is not an error",
              rc == 0 and "not set" in out, (rc, out[:80]))
        # A REAL key: an invented one is refused now (A-2026-10-07-76).
        call(fb, ["config", "set", "agent.digest_lines", "77"])
        rc, out, err = call(fb, ["config", "unset", "agent.digest_lines"])
        written = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
        check("config unset removes the key",
              rc == 0 and "digest_lines" not in written["agent"], list(written["agent"]))
        rc, out, err = call(fb, ["config", "unset", "agent.digest_lines"])
        check("...and says so when it was not set", rc == 2, rc)

        # ---- a box with NO config.json: the verb CREATES one, and does not write on a read
        # `setup` prints `config set` as its non-interactive path, and that path used to die
        # with "could not read config.json: [Errno 2] ..." and refuse to write - a dead end on
        # exactly the box that has none (run 24, A-2026-10-07-71).
        cfg_path = workdir / "config.json"
        cfg_before = cfg_path.read_bytes()
        # The shipped package carries config.example.json (it is in SHIP); this staged tree
        # does not, so stage it the way an install does - the verb seeds from it.
        shutil.copy2(BASE / "config.example.json", workdir / "config.example.json")
        cfg_path.unlink()
        rc, out, err = call(fb, ["config", "get", "llm.base_url"])
        check("config get with no config.json answers from the shipped example",
              rc == 0 and out.strip().startswith("http"), (rc, out[:80], err[:120]))
        check("...and a READ does not create the file",
              not cfg_path.exists(), "config.json appeared after a get")
        rc, out, err = call(fb, ["config", "set", "agent.max_steps", "99"])
        check("config set with no config.json creates it and applies the value",
              rc == 0 and cfg_path.exists()
              and json.loads(cfg_path.read_text(encoding="utf-8"))["agent"]["max_steps"] == 99,
              (rc, err[:160]))
        seeded = json.loads(cfg_path.read_text(encoding="utf-8"))
        check("...and the file it creates is the whole shipped example, not a stub",
              len(seeded) >= len(json.loads(
                  (BASE / "config.example.json").read_text(encoding="utf-8"))),
              (len(seeded), sorted(seeded)[:5]))
        check("...and it says it created one",
              "created it from config.example.json" in out + err, (out + err)[:200])
        cfg_path.write_bytes(cfg_before)

        # ---- a key NOTHING reads is refused, with the nearest real key named -------------
        # `config set llm.baseurl ...` used to answer "set" with rc=0, echo back from
        # `config get`, and leave the box on the default - a one-letter typo in the setting
        # that decides which model a box talks to, invisible on every path an operator checks
        # (run 25, A-2026-10-07-76). The known names are DERIVED (shipped defaults, the
        # shipped example, and the keys the code reads), so a key the harness really reads is
        # never refused by accident.
        rc, out, err = call(fb, ["config", "set", "llm.baseurl", "http://x/v1"])
        check("a key the harness never reads is refused, and the nearest is named",
              rc == 2 and "not a key the harness reads" in err and "base_url" in err,
              (rc, err[:160]))
        check("...and the typo does not reach config.json",
              "baseurl" not in (workdir / "config.json").read_text(encoding="utf-8"), "")
        rc, out, err = call(fb, ["config", "set", "bogus.section", "1"])
        check("an unknown SECTION is refused too",
              rc == 2 and "not a section" in err, (rc, err[:160]))
        for _k, _v in (("llm.window_presets", '{"-32768": 8192}'),
                       ("agent.update_url", "http://example.invalid/x"),
                       ("agent.confirm_patterns_extra", "rm -rf /tmp/x"),
                       ("agent.mcp_servers.demo", '{"command": "x"}')):
            rc, out, err = call(fb, ["config", "set", _k, _v])
            check("...and %s is still writable (read or documented)" % _k, rc == 0,
                  (rc, err[:140]))
        rc, out, err = call(fb, ["config", "set", "llm.base_url", "true"])
        check("a boolean for a string key is refused (it crashed the next start)",
              rc == 2 and "not true/false" in err, (rc, err[:160]))

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

        # ---- an older release must not flatten a newer install ---------------------
        _before_dg = (workdir / "tinycmdr.py").read_bytes()
        _old = workdir / "old" / "tinycmdr.py"
        _old.parent.mkdir()
        _old.write_text((workdir / "tinycmdr.py").read_text(encoding="utf-8")
                        .replace('VERSION = "', 'VERSION = "1.0.1-', 1), encoding="utf-8")
        rc, out, err = call(fb, ["update", str(_old)])
        check("a downgrade is refused, and says it is one",
              rc == 1 and "OLDER" in err and "1.0.1" in err, (rc, (err or out)[:200]))
        check("...and the install is untouched",
              (workdir / "tinycmdr.py").read_bytes() == _before_dg)
        rc, out, err = call(fb, ["update", str(_old), "--force"])
        check("`update --force` takes the downgrade deliberately",
              rc == 0 and 'VERSION = "1.0.1-' in (workdir / "tinycmdr.py").read_text(
                  encoding="utf-8"), (rc, out[:160], err[:160]))
        (workdir / "tinycmdr.py").write_bytes(_before_dg)     # back to the 9.9.11 build

        # ...and a same-version "repair" must not overwrite a checkout's edits. The
        # legacy UPGRADE over the same checkout warns but lands (the stranded-install
        # rescue) - the two are the difference this guard turns on.
        _cand_dir = workdir / "cand-guard"
        _cand_dir.mkdir()
        _cand = _cand_dir / "tinycmdr.py"
        _cand.write_text((workdir / "tinycmdr.py").read_text(encoding="utf-8")
                         .replace('VERSION = "9.9.11-', 'VERSION = "9.9.12-', 1),
                         encoding="utf-8")
        rc, out, err = call(fb, ["update", str(_cand)])
        check("an upgrade over a checkout warns about uncommitted edits, and lands",
              rc == 0 and "development checkout" in err and "9.9.12" in out,
              (rc, err[:200], out[:160]))
        _cand.write_text((workdir / "tinycmdr.py").read_text(encoding="utf-8") + "\n# probe\n",
                         encoding="utf-8")
        rc, out, err = call(fb, ["update", str(_cand)])
        check("a same-version repair over a checkout is refused",
              rc == 1 and "SAME version" in err, (rc, err[:220]))
        rc, out, err = call(fb, ["update", str(_cand), "--force"])
        check("...and `--force` repairs deliberately",
              rc == 0, (rc, out[:160], err[:160]))

        (rel / "SHA256SUMS").write_text("%s  %s\n" % ("0" * 64, asset), encoding="utf-8")
        _before = (workdir / "tinycmdr.py").read_bytes()
        rc, out, err = call(fb, ["update"])
        check("a download that fails SHA256SUMS is refused",
              rc == 1 and "SHA256SUMS" in (out + err), (rc, (out + err)[:200]))
        check("...and nothing is written over the install",
              (workdir / "tinycmdr.py").read_bytes() == _before)

        # ---- an archive member that escapes the unpack folder ------------------
        # The no-filter fallback (a stock 3.9 build) extracted blind pre-fix: a '../'
        # member wrote OUTSIDE the work folder, and SHA256SUMS rides the same base URL,
        # so it proves the transfer, not the contents (A-2026-10-08-134). The fallback
        # is FORCED here - the sieve is then what runs on every interpreter.
        import tarfile
        evil = workdir / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as t:
            body = b"outside\n"
            bad = tarfile.TarInfo("../escaped.txt")
            bad.size = len(body)
            t.addfile(bad, io.BytesIO(body))
            okbody = b'VERSION = "9.9.12"\n'
            good = tarfile.TarInfo("pkg/tinycmdr.py")
            good.size = len(okbody)
            t.addfile(good, io.BytesIO(okbody))
        legit = workdir / "legit.tar.gz"
        with tarfile.open(legit, "w:gz") as t:
            okbody = b'VERSION = "9.9.12"\n'
            good = tarfile.TarInfo("tinycmdr-9.9.12/tinycmdr.py")
            good.size = len(okbody)
            t.addfile(good, io.BytesIO(okbody))

        _real_extractall = tarfile.TarFile.extractall

        def _old_extractall(self, path=".", members=None, *, numeric_owner=False,
                            filter=None):
            if filter is not None:
                raise TypeError("extractall() got an unexpected keyword argument 'filter'")
            return _real_extractall(self, path, members=members,
                                    numeric_owner=numeric_owner)

        tarfile.TarFile.extractall = _old_extractall
        try:
            root = fb._extract_release(evil, workdir / "unpack-evil")
            root_ok = fb._extract_release(legit, workdir / "unpack-ok")
        finally:
            tarfile.TarFile.extractall = _real_extractall
        check("a tar member that escapes the unpack folder refuses the whole archive",
              root is None and not (workdir / "escaped.txt").exists(),
              (root, (workdir / "escaped.txt").exists()))
        check("...while a normal tar.gz still unpacks",
              root_ok is not None and (root_ok / "tinycmdr.py").exists(), root_ok)

        srv.shutdown()
        srv.server_close()

        check("the new verbs are in the verb list",
              all(v in fb.VERBS for v in ("health", "config", "proc", "search",
                                          "update", "clean", "version")), fb.VERBS)

        # ---- search: the provider chain and its doors --------------------------------
        # The input path for a provider of the operator's OWN - with a key or without -
        # and the doors the BLOCKED line a run prints names. tavily refuses keyless
        # calls, so an entry of that kind is refused where the reason can be said.
        import http.server as _http
        import socketserver as _sockets
        import threading as _threading

        class _SearchStub(_http.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                self._send({"results": [
                    {"title": "stub hit", "url": "http://x/", "content": "y"}]})

            def do_GET(self):
                self._send({"results": [
                    {"title": "stub hit", "url": "http://x/", "content": "y"}]})

        _srv = _sockets.TCPServer(("127.0.0.1", 0), _SearchStub)
        _threading.Thread(target=_srv.serve_forever, daemon=True).start()
        _stub_url = "http://127.0.0.1:%d" % _srv.server_address[1]
        try:
            rc, out, err = call(fb, ["search"])
            check("search lists the chain and the doors",
                  rc == 0 and "anysearch" in out and "tinycmdr search add <url>" in out,
                  (rc, out[:200]))
            _file_before = json.loads((workdir / "config.json").read_text(
                encoding="utf-8")).get("search", {}).get("providers")
            _expect_tail = ([p["kind"] for p in _file_before]
                            if isinstance(_file_before, list)
                            else [p["kind"] for p in
                                  fb.DEFAULT_CONFIG["search"]["providers"]])
            rc, out, err = call(fb, ["search", "add", _stub_url, "--label", "mybox"])
            _chain = json.loads((workdir / "config.json").read_text(
                encoding="utf-8"))["search"]["providers"]
            check("search add prepends a provider of your own (tried first)",
                  rc == 0 and _chain[0] == {"kind": "generic", "url": _stub_url,
                                            "label": "mybox"}, (rc, _chain[:1]))
            check("...and the file's own providers are carried over behind it",
                  [p["kind"] for p in _chain[1:]] == _expect_tail, (_chain, _expect_tail))
            rc, out, err = call(fb, ["search", "add", _stub_url])
            check("a duplicate url is refused",
                  rc == 2 and "already there" in err, (rc, err[:160]))
            rc, out, err = call(fb, ["search", "add", "https://search.example.com/t",
                                     "--kind", "brave", "--label", "my-brave"])
            _chain = json.loads((workdir / "config.json").read_text(
                encoding="utf-8"))["search"]["providers"]
            check("a kind this file never heard of is kept and called generically",
                  rc == 0 and _chain[0]["kind"] == "brave"
                  and _chain[0]["label"] == "my-brave", (rc, _chain[:1]))
            rc, out, err = call(fb, ["search", "add", "https://search.example.com/t2",
                                     "--kind", "anysearch",
                                     "--key-env", "ANYSEARCH_API_KEY2",
                                     "--label", "tav2"])
            _chain = json.loads((workdir / "config.json").read_text(
                encoding="utf-8"))["search"]["providers"]
            check("a custom keyed endpoint is accepted when its .env variable is named",
                  rc == 0 and _chain[0]["api_key_env"] == "ANYSEARCH_API_KEY2"
                  and "not set in .env yet" in out, (rc, out[:200]))
            rc, out, err = call(fb, ["search", "test"])
            check("search test runs the chain and names what answered",
                  rc == 0 and "mybox" in out and "OK - 1 result" in out,
                  (rc, out[:200]))
            rc, out, err = call(fb, ["search", "allow"])
            check("search allow reports the consent state",
                  rc == 0 and "off-LAN providers are" in out, (rc, out[:120]))
            rc, out, err = call(fb, ["search", "allow", "false"])
            check("...and sets it",
                  rc == 0 and json.loads((workdir / "config.json").read_text(
                      encoding="utf-8"))["search"]["allow_cloud_egress"] is False,
                  (rc, out[:120]))
            rc, out, err = call(fb, ["search", "add", "https://elsewhere.example.com/s"])
            check("an off-LAN provider added while refused is called out",
                  rc == 0 and "search allow true" in out, (rc, out[:300]))
            call(fb, ["search", "allow", "true"])
            rc, out, err = call(fb, ["search", "remove", "mybox"])
            _chain = json.loads((workdir / "config.json").read_text(
                encoding="utf-8"))["search"]["providers"]
            check("search remove drops the matching provider",
                  rc == 0 and all(p.get("label") != "mybox" for p in _chain), _chain)
            # The interactive door: no url on a TTY asks (url, optional key, label,
            # "add another?"), and the answer lands like the flagged one.
            rc, out, err = call(fb, ["search", "add"],
                                stdin="http://127.0.0.1:8898\n\ntypedbox\nn\n", tty=True)
            _chain = json.loads((workdir / "config.json").read_text(
                encoding="utf-8"))["search"]["providers"]
            check("search add with no url asks, and writes what was typed",
                  rc == 0 and _chain[0] == {"kind": "generic",
                                            "url": "http://127.0.0.1:8898",
                                            "label": "typedbox"}, (rc, _chain[:1], out[:200]))
        finally:
            _srv.shutdown()
            _srv.server_close()

        # ---- quit: the whole install goes down (foreground too) ---------------------
        # A live child from this folder, then the verb: the stage has no stop helper
        # (the note says so), the folder-scoped sweep kills the child, the lock comes
        # free - and `kill` on a quiet box says nothing was running.
        _qsock = socket.socket()
        _qsock.bind(("127.0.0.1", 0))
        _qport = _qsock.getsockname()[1]
        _qsock.close()
        _qcfg_path = workdir / "config.json"
        _qcfg_saved = _qcfg_path.read_text(encoding="utf-8")
        _qenv_path = workdir / ".env"
        _qenv_saved = _qenv_path.read_text(encoding="utf-8") if _qenv_path.exists() else None
        _qout = workdir / "quit-child.out"
        _qerr = workdir / "quit-child.err"
        _qfh = {"out": open(_qout, "wb"), "err": open(_qerr, "wb")}
        _qchild = None
        _up = False
        try:
            # Two attempts, a fresh port each: a portable CI runs four suites at once and
            # an ephemeral port picked before the child starts can be taken by another
            # suite's child by the time this one binds (measured 2026-10-09 on
            # macos-latest). The child's streams go to files so a failure names WHY.
            for _attempt in range(2):
                _qsock = socket.socket()
                _qsock.bind(("127.0.0.1", 0))
                _qport = _qsock.getsockname()[1]
                _qsock.close()
                # No chat lane on purpose: a fixture lane that cannot connect retries and
                # then exits, taking the page with it; a lane-less box HOLDS the page (the
                # shape the lane suite's holder uses).
                _qcfg_path.write_text(json.dumps({
                    "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "main"},
                    "web": {"enabled": True, "host": "127.0.0.1", "port": _qport},
                }), encoding="utf-8")
                if _qchild is not None and _qchild.poll() is None:
                    _qchild.kill()
                    _qchild.wait(timeout=10)
                # Through the install's own (resolved) path: the sweep matches command
                # lines, and a child started via the temp dir's /var spelling while
                # BASE_DIR is /private/var is exactly the shape that is invisible to it.
                _qchild = subprocess.Popen(
                    [sys.executable, str(workdir.resolve() / "tinycmdr.py")],
                    cwd=str(workdir.resolve()),
                    env={**{k: v for k, v in os.environ.items()
                             if not k.startswith("TINYCMDR_")},
                         "HOME": str(workdir), "TINYCMDR_NO_BROWSER": "1"},
                    stdin=subprocess.DEVNULL,
                    stdout=_qfh["out"], stderr=_qfh["err"])
                _t0 = time.time()
                while time.time() - _t0 < 40 and _qchild.poll() is None:
                    try:
                        with urllib.request.urlopen(
                                "http://127.0.0.1:%d/api/health" % _qport, timeout=1):
                            _up = True
                        break
                    except Exception:                            # noqa: BLE001
                        time.sleep(0.4)
                if _up:
                    break
            _qsaid = ""
            for _p in (_qout, _qerr):
                if _p.exists():
                    _qsaid += _p.read_text(encoding="utf-8", errors="replace")[-250:]
            check("quit: a live child from this folder serves", _up,
                  (_qchild.poll(), _qsaid) if _qsaid else _qchild.poll())
            rc, out, err = call(fb, ["quit"])
            check("quit stops what runs from this folder, and names the missing helper",
                  rc == 0 and "stopped" in out and "no stop helper here" in out,
                  (rc, out[:200]))
            _gone = False
            _t0 = time.time()
            while time.time() - _t0 < 15 and not _gone:
                _gone = _qchild.poll() is not None
                time.sleep(0.3)
            check("...the child is gone", _gone, _qchild.poll())
            check("...and the instance lock is free",
                  fb._verb_running() is False, fb._verb_running())
            rc, out, err = call(fb, ["kill"])
            check("...and 'kill' is the same door (a second quit is safe)",
                  rc == 0 and "nothing was running" in out, (rc, out[:160]))
        finally:
            if _qchild is not None and _qchild.poll() is None:
                _qchild.kill()
                _qchild.wait(timeout=10)
            for _f in _qfh.values():
                _f.close()
            _qcfg_path.write_text(_qcfg_saved, encoding="utf-8")
            if _qenv_saved is None:
                _qenv_path.unlink(missing_ok=True)
            else:
                _qenv_path.write_text(_qenv_saved, encoding="utf-8")

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
            # a value that can never work is REFUSED before it is written (a Windows install,
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
        _rel_path = BASE / "maintenance" / "release.sh"
        if _rel_path.is_file():
            _rel = _rel_path.read_text(encoding="utf-8")
            check("...and every release attaches it",
                  "dist/update.sh" in _rel and "dist/update.ps1" in _rel
                  and "install.sh install.ps1 update.sh update.ps1 > SHA256SUMS" in _rel,
                  "release.sh")
        else:
            print("  (no maintenance/release.sh in this tree: the packaging half of the "
                  "update rule is graded where the release tooling lives)")
        _ush = (BASE / "update.sh").read_text(encoding="utf-8")
        check("...it verifies the download before touching the install",
              "SHA256SUMS" in _ush and "checksum mismatch" in _ush, "update.sh")
        check("...and leaves host-owned paths alone",
              "theme.toml" in _ush and "config.json" in _ush and "tools skills sessions" in _ush,
              "update.sh")
        _shim = (BASE / "tinycmdr").read_text(encoding="utf-8")
        check("the unix launcher falls back to the published updater for old installs",
              "releases/latest/download" in _shim
              and "releases/latest/download/update.sh" in _shim,
              "shim")
        _cmd = (BASE / "tinycmdr.cmd").read_text(encoding="utf-8")
        check("...and so does the Windows launcher",
              "releases/latest/download" in _cmd
              and "releases/latest/download/update.ps1" in _cmd,
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
        # silently dropping every check after it (~20 checks).
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
            # check ran.
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

        # The Windows helper itself: it must stop all THREE process kinds and scope the
        # bot filter to this install. The old filter (pythonw.exe + '*tinycmdr.py*')
        # never matched the supervisor, so the surviving supervisor held the lock and
        # relaunched the OLD bot while the wscript relaunch died on that lock; and
        # Unscoped, it killed a second install's bot on the same box.
        _ps = (BASE / "maintenance" / "restart-tinycmdr.ps1").read_text(encoding="utf-8")
        check("the Windows restart helper stops the supervisor and the launcher",
              "tinycmdr-supervise.py" in _ps and "tinycmdr-service.vbs" in _ps)
        # -like reads [ ] * ? in a PATH as wildcards: a bracketed install dir matched
        # nothing, so the scoped clause silently let the old bot live.
        # The literal OrdinalIgnoreCase compare replaced it in BOTH copies, and the
        # supervisor clause is scoped like the bot clause now (the vbs passes the full
        # path; measured 2026-10-05).
        check("...and scopes the filter with a literal, wildcard-safe compare at a path "
              "boundary",
              "IndexOf(($install.TrimEnd('\\') + '\\')," in _ps
              and 'like "*$install*"' not in _ps
              and "-Filter \"Name='pythonw.exe'\"" not in _ps)
        _psi = (BASE / "install" / "install-tinycmdr.ps1").read_text(encoding="utf-8")
        check("...and the installer's copy spells it the same way (kept in step)",
              "IndexOf(($Dir.TrimEnd('\\') + '\\')" in _psi and 'like "*$Dir*"' not in _psi)
        # A user-PATH write must broadcast WM_SETTINGCHANGE, or a window opened right
        # after the install keeps Explorer's stale environment and answers "not
        # recognized" until logoff (a brand-new-install report, 2026-10-06).
        check("the installer broadcasts the PATH change",
              "function Send-EnvBroadcast" in _psi and "WM_SETTINGCHANGE" in _psi)
        _writes = [m.start() for m in re.finditer(r"Set-UserPathRaw \(", _psi)]
        check("...after every user-PATH write",
              bool(_writes)
              and all("Send-EnvBroadcast" in _psi[s:s + 200] for s in _writes),
              _writes)
        # The hosted one-liner runs in the caller's terminal: the wrapper's
        # press-any-key barrier (meant for the double-click window) must be off.
        _boot = (BASE / "install.ps1").read_text(encoding="utf-8")
        _nopause = _boot.find("$env:FB_NOPAUSE")
        _call = _boot.find("& cmd.exe /c INSTALL-WINDOWS.cmd")
        check("the one-liner suppresses the installer's press-any-key barrier",
              _nopause != -1 and _call != -1 and _nopause < _call,
              (_nopause, _call))
        check("the installer refuses an elevated window from a different account",
              "Win32_ComputerSystem" in _psi
              and "the desktop session belongs to" in _psi)
        check("...and verifies the supervisor, not only a bot, came back",
              "supervisor(s) $($sup.Count)" in _ps)
        check("...and exits non-zero when its own log records a failed restart",
              "exit $exitCode" in _ps and "$exitCode = 1" in _ps)

        # The macOS helper: start must accept an already-loaded agent, stop must not claim
        # a stop that did not happen, and logs must not die on a fresh install.
        _rmac = (BASE / "maintenance" / "restart-tinycmdr-macos.sh").read_text(encoding="utf-8")
        check("the macOS helper's start is idempotent",
              'launchctl print "$TARGET"' in _rmac and "already loaded" in _rmac)
        check("...its stop says when nothing was loaded",
              "nothing was loaded for" in _rmac)
        check("...and its logs verb survives neither log existing",
              "no logs at either path yet" in _rmac)

        # Every SERVICE lane takes startup validation and the single-instance lock:
        # --telegram used to walk into its lane without either, and two pollers on one
        # token split every DM.
        _main_src = (BASE / "tinycmdr.py").read_text(encoding="utf-8")
        _tg_at = _main_src.find('elif "--telegram" in sys.argv:')
        _tg_next = _main_src.find("\n    else:", _tg_at)
        _tg_branch = _main_src[_tg_at:_tg_next] if _tg_at != -1 else ""
        check("--telegram takes the service preflight before its lane starts",
              "_service_preflight()" in _tg_branch
              and _tg_branch.find("_service_preflight()")
              < _tg_branch.find("lane_with_retry"),
              _tg_branch[:120])
        check("...and the bare/bot lane takes it too",
              _main_src.count("_service_preflight()") >= 3,
              _main_src.count("_service_preflight()"))

        # The POSIX twin: its pkill must be scoped+anchored, and its install guard
        # must be able to fail (list-unit-files exits 0 either way, so the friendly
        # Branch was dead code - and the pkill ran BEFORE it).
        _rsh = (BASE / "maintenance" / "restart-tinycmdr.sh").read_text(encoding="utf-8")
        check("the POSIX restart helper never pkills by bare file name",
              'pkill -f "tinycmdr.py"' not in _rsh
              and 'pkill -f "$INSTALL_DIR/tinycmdr[.]py"' in _rsh)
        check("...and its install guard reads LoadState instead of an exit code",
              "if ! systemctl list-unit-files" not in _rsh and "LoadState" in _rsh)

        # --- the page lane is BACK, as the default door ---------------------------------
        # `web` is a management verb, the help names it, and the page flags adjust the
        # page per run instead of being refused; the flag path is exercised at the
        # process boundary in the H1 block below.
        check("`web` is a management verb", "web" in fb.VERBS, sorted(fb.VERBS)[:6])
        check("VERB_HELP names the page verb", "open the page" in fb.VERB_HELP,
              fb.VERB_HELP[-220:])
        check("VERB_HELP stopped teaching the retired prefix", "/cmdr " not in fb.VERB_HELP,
              fb.VERB_HELP[-220:])
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

    # The two lists drifting apart gave one command two answers: `/reasoning low` was
    # handled by the chat lane while `/tinycmdr reasoning low` was refused as "needs a
    # terminal on the host" (A-2026-10-06-274). The verbs that only read or write state
    # belong to both lists; the line is drawn at "would this prompt, open a session, or
    # take the host down" - `run` still refuses, by name.
    for v in ("reasoning", "approvals", "failures"):
        check(f"{v} is a chat verb (it reads/writes state and never prompts)",
              v in fb._CHAT_VERB_SET, sorted(fb._CHAT_VERB_SET))
    text = fb.verb_from_chat("approvals")
    check("...and a state verb answers from chat",
          "confirm gate allowlist" in text, text[:200])
    text = fb.verb_from_chat("reasoning")
    check("...reasoning answers from chat too", "reasoning:" in text, text[:200])
    text = fb.verb_from_chat("run")
    check("...while `run` still refuses, by name", "needs a terminal" in text, text[:200])

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
                              env=dict(os.environ, TINYCMDR_PLAIN="1", TINYCMDR_NO_BROWSER="1"))
        blob = proc.stdout + proc.stderr
        check("H1: an unknown verb exits 2", proc.returncode == 2, proc.returncode)
        check("H1: it NAMES the word it did not know",
              "unknown verb" in blob and "taks" in blob, blob[-200:])
        check("H1: and prints the verb list instead of starting the agent",
              "tinycmdr <verb>" in blob, blob[-300:])

        # The page lane: `web` is a verb, and it MINTS the token the page requires (an
        # install that upgraded into the page has none); only the retired spellings are
        # unknown.
        for argv, want in ((["webui"], "unknown verb"),
                           (["page"], "unknown verb")):
            gone = subprocess.run([sys.executable, str(stage / "tinycmdr.py"), *argv],
                                  cwd=str(stage), capture_output=True, text=True,
                                  timeout=120, stdin=subprocess.DEVNULL,
                                  env=dict(os.environ, TINYCMDR_PLAIN="1", TINYCMDR_NO_BROWSER="1"))
            gblob = gone.stdout + gone.stderr
            check("H1: `%s` exits 2" % " ".join(argv), gone.returncode == 2, gone.returncode)
            check("H1: `%s` says %r" % (" ".join(argv), want), want in gblob, gblob[-200:])
        # The word after the program name is a VERB, and `web` now PROBES before it
        # prints: a link must not be handed out for a page nobody serves. So this run
        # needs a port of its OWN that answers - the staged config used to carry no web
        # block at all, the verb probed the default 8790, and whether this check passed
        # depended on what else on the box held that port (measured on CI: it was green
        # locally only because the operator's own page answered there). A free port plus
        # an in-process /api/health server makes the answer the test's, not the box's.
        _s = socket.socket()
        _s.bind(("127.0.0.1", 0))
        _vport = _s.getsockname()[1]
        _s.close()

        class _LanesOK(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = b'{"ok": true, "lanes": {}}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        _vi = json.loads((stage / "config.json").read_text(encoding="utf-8"))
        _vi["web"] = {"enabled": True, "host": "127.0.0.1", "port": _vport}
        (stage / "config.json").write_text(json.dumps(_vi), encoding="utf-8")
        _vsrv = ThreadingHTTPServer(("127.0.0.1", _vport), _LanesOK)
        threading.Thread(target=_vsrv.serve_forever, daemon=True).start()
        try:
            wv = subprocess.run([sys.executable, str(stage / "tinycmdr.py"), "web"],
                                cwd=str(stage), capture_output=True, text=True,
                                timeout=120, stdin=subprocess.DEVNULL,
                                env=dict(os.environ, TINYCMDR_PLAIN="1", TINYCMDR_NO_BROWSER="1"))
            wblob = wv.stdout + wv.stderr
            check("H1: `web` exits 0", wv.returncode == 0, (wv.returncode, wblob[-200:]))
            check("H1: `web` mints the token the page needs",
                  "minted TINYCMDR_WEB_TOKEN" in wblob, wblob[-200:])
            _tok = [ln.split("=", 1)[1] for ln in
                    (stage / ".env").read_text(encoding="utf-8").splitlines()
                    if ln.startswith("TINYCMDR_WEB_TOKEN=")]
            check("H1: `web` prints the link built from it",
                  bool(_tok) and ("#token=" + _tok[-1]) in wblob, wblob[-200:])
        finally:
            _vsrv.shutdown()
            _vsrv.server_close()
        # ...and with nothing on the configured port the verb says so and exits 1,
        # instead of printing a link that resolves to nothing (the 2026-10-10 contract).
        wv2 = subprocess.run([sys.executable, str(stage / "tinycmdr.py"), "web"],
                             cwd=str(stage), capture_output=True, text=True,
                             timeout=120, stdin=subprocess.DEVNULL,
                             env=dict(os.environ, TINYCMDR_PLAIN="1", TINYCMDR_NO_BROWSER="1"))
        w2blob = wv2.stdout + wv2.stderr
        check("H1: `web` with nothing serving says so and exits 1",
              wv2.returncode == 1 and "no page is answering" in w2blob
              and "#token=" not in w2blob, (wv2.returncode, w2blob[-240:]))
        # `--web` gets its own run: the chat-intent fixture would send it through the
        # chat startup gate first, so the config is blanked to no-intent and the run
        # must serve the page - and hold it open, which is the whole point of the flag.
        # The token is already in .env from the verb above, so nothing mints here.
        _cfg = json.loads((stage / "config.json").read_text(encoding="utf-8"))
        _cfg["mattermost"]["url"] = ""
        _cfg["mattermost"]["token"] = ""
        _cfg["mattermost"]["allowed_users"] = []
        # A port we choose and POLL: the child's stdout/log tail is unreliable once it is
        # killed at a deadline (unflushed pipe buffer, log rides a listener thread), so
        # the evidence is the page answering - not text.
        _s = socket.socket()
        _s.bind(("127.0.0.1", 0))
        _port = _s.getsockname()[1]
        _s.close()
        _cfg["web"] = {"enabled": True, "host": "127.0.0.1", "port": _port}
        (stage / "config.json").write_text(json.dumps(_cfg), encoding="utf-8")
        _proc = subprocess.Popen([sys.executable, str(stage / "tinycmdr.py"), "--web"],
                                 cwd=str(stage), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 env=dict(os.environ, TINYCMDR_PLAIN="1", TINYCMDR_NO_BROWSER="1"))
        _served = False
        _t0 = time.time()
        while time.time() - _t0 < 45 and _proc.poll() is None:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % _port,
                                            timeout=1) as _r:
                    if _r.status == 200:
                        _served = True
                        break
            except Exception:                                    # noqa: BLE001
                time.sleep(0.5)
        _alive = _proc.poll() is None
        if _alive:
            _proc.kill()
        try:
            _out, _err = _proc.communicate(timeout=10)
        except Exception:                                        # noqa: BLE001
            _out, _err = "", ""
        check("H1: `--web` serves the page and holds it open",
              _served and _alive, (_served, _alive, (_out or "")[-160:]))
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

    this run mid-file and the checks after it vanished without a word - the runner could
    only print a traceback, so "how much of this suite graded" was unanswerable. `N passed,
    M failed` on the last line is that answer, and it prints on the way out of a crash too.
    """
    line = "%d passed, %d failed" % (len(PASSES), len(FAILS))
    print(line)
    if aborted:
        print("ABORTED after %d check(s): %s" % (len(PASSES) + len(FAILS), aborted))


def main():
    """Run the checks with the harness's own secrets hidden.

    The gate grades the CODE, not the shell a running bot was started from: TINYCMDR_*TOKEN*
    is exported on every configured box, and both the CLI's mint (`web`) and doctor's
    page-token line read it - so this suite failed outright with one set and passed scrubbed
    (measured 2026-10-07: FAIL + abort before, 249/249 after - run 23, A-2026-10-07-65). CI
    never sees it, because CI has no tokens. A suite that WANTS a token sets it itself.
    """
    with hermetic.no_bot_tokens():
        return _body()


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
