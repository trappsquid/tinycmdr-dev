"""Running tinycmdr under sudo must not wreck a user-owned install.

Measured 2026-09-27 on the Mac, two ways in one evening:

  * `sudo tinycmdr config set search.allow_cloud_egress true` - `_write_config` REPLACES
    config.json, and a replacement takes the AUTHOR of the write, so the file came back
    root:staff 0600. The launchd agent runs as the install's own user, could not read it,
    and exited 1 on every respawn. The same root run also left tasks.json (the ledger) and
    sessions/cli.json owned by root, so the agent could not write its ledger and the CLI
    lane could not load its session.
  * `sudo tinycmdr restart` - the macOS helper's `launchctl bootstrap` cannot enter the
    console user's GUI domain as root ("Bootstrap failed: 125"), and the agent had already
    been booted OUT, so the bot stayed down.

This suite must pass on every platform the gate runs (macOS, Linux; Windows runs a
different subset), so the owner/mode checks only assert on POSIX and the helper check
expects the platform's OWN refusal off macOS.

    python tests/test_root_safety.py
"""
import importlib.util
import json
import os
import shutil
import stat as stat_mod
import subprocess
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "tinycmdr.py"
HELPER = REPO / "maintenance" / "restart-tinycmdr-macos.sh"

PASSES, FAILS = [], []


def check(cond, what, extra=""):
    if cond:
        PASSES.append(what)
        print(f"ok   {what}")
    else:
        FAILS.append(what)
        print(f"FAIL {what}\n     {extra}")


def load(dirpath):
    spec = importlib.util.spec_from_file_location("root_" + dirpath.name,
                                                 dirpath / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def stage(dirpath):
    dirpath.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, dirpath / "tinycmdr.py")
    (dirpath / "config.json").write_text('{"llm": {}}', encoding="utf-8")
    return dirpath


def shim(bindir, name, body):
    f = bindir / name
    f.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    f.chmod(f.stat().st_mode | stat_mod.S_IXUSR)
    return f


def owner_is_restored(work):
    """A write that comes back root-owned must be put back to the pre-write owner."""
    d = stage(work / "owner")
    mod = load(d)
    if os.name == "nt" or not hasattr(os, "chown"):
        mod._write_config({"llm": {"model": "x"}})
        check(json.loads((d / "config.json").read_text())["llm"]["model"] == "x",
              "a config write lands (owner restoration is POSIX-only)")
        return
    real_stat = os.stat
    ours = real_stat(str(d / "config.json"))
    me = (ours.st_uid, ours.st_gid)                 # whatever uid this runner has
    root_owned = os.stat_result(tuple(ours)[:4] + (0, 0) + tuple(ours)[6:])
    seen = {"n": 0}

    def fake_stat(p, *a, **k):
        if str(p).endswith("config.json"):
            seen["n"] += 1
            # before the write the file is ours; a sudo write REPLACES it as root
            return ours if seen["n"] == 1 else root_owned
        return real_stat(p, *a, **k)

    chowns = []
    with mock.patch.object(os, "stat", side_effect=fake_stat), \
            mock.patch.object(os, "chown",
                              side_effect=lambda p, u, g: chowns.append((u, g))):
        mod._write_config({"llm": {"model": "x"}})
    check(chowns == [me],
          "a config write restores the pre-write owner, so a sudo write cannot make the "
          "agent unable to read its own config", f"chowns={chowns} expected={[me]}")


def mode_is_restored(work):
    """A write must not widen 0600 to the umask's 0644 (config.json can hold llm.api_key)."""
    d = stage(work / "mode")
    (d / "config.json").chmod(0o600)
    mod = load(d)
    mod._write_config({"llm": {"model": "z"}})
    check(json.loads((d / "config.json").read_text())["llm"]["model"] == "z",
          "and the write itself landed")
    if os.name == "nt":
        return
    got = (d / "config.json").stat().st_mode & 0o777
    check(got == 0o600,
          "a config write keeps 0600 instead of the umask's 0644", oct(got))


def helper_refuses_root(work):
    """The macOS restart helper must not run as root (it would leave the agent stopped)."""
    if not shutil.which("bash"):
        print("     (no bash on this host: the restart helper cannot be exercised)")
        return
    home = work / "home"
    plist_dir = home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    (plist_dir / "com.tinycmdr.agent.plist").write_text("<plist/>", encoding="utf-8")
    bindir = work / "bin"
    bindir.mkdir()
    marker = work / "launchctl-called"
    shim(bindir, "launchctl", "echo called >> %s; exit 0" % marker)
    env = dict(os.environ, HOME=str(home),
               PATH="%s:%s" % (bindir, os.environ.get("PATH", "")),
               TINYCMDR_DIR=str(work))

    shim(bindir, "id", 'case "$1" in -u) echo 0;; *) echo root;; esac')
    r = subprocess.run(["bash", str(HELPER), "restart"], env=env,
                       capture_output=True, text=True, timeout=60)
    said = r.stdout + r.stderr
    check(r.returncode != 0, f"the helper refuses to run as root ({r.returncode})")
    if sys.platform == "darwin":
        check("sudo" in said and "LaunchAgent" in said,
              "and says why, and what to do instead", said[-300:])
    else:
        check("macOS-only" in said,
              "and off macOS it refuses on the platform check", said[-200:])
    check(not marker.exists(),
          "and it never touched the agent (launchctl was not called)")

    if sys.platform == "darwin":
        # as the user it proceeds, so the refusal is root-specific rather than blanket
        shim(bindir, "id", 'case "$1" in -u) echo %d;; *) echo user;; esac' % os.getuid())
        r = subprocess.run(["bash", str(HELPER), "status"], env=env,
                           capture_output=True, text=True, timeout=60)
        check(marker.exists(), "as the user it does call launchctl",
              (r.stdout + r.stderr)[-200:])


def root_warning_names_it(work):
    """Running as root over another user's install says exactly what will happen."""
    d = work / "warn"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, d / "tinycmdr.py")
    (d / "config.json").write_text('{"llm": {}}', encoding="utf-8")
    m = load(d)
    if os.name == "nt" or not hasattr(os, "geteuid"):
        print("     (no POSIX uid here: the root warning is POSIX-only)")
        return
    check(m._root_warning() == "", "no root warning when NOT running as root")
    real = os.geteuid
    try:
        os.geteuid = lambda: 0
        w = m._root_warning()
    finally:
        os.geteuid = real
    check(w.startswith("running as root") and "root-owned" in w and "without sudo" in w,
          "as root over another user's install it names the damage and the fix", w[:240])


def main():
    work = Path(tempfile.mkdtemp(prefix="fbrootsafe-"))
    try:
        owner_is_restored(work)
        mode_is_restored(work)
        root_warning_names_it(work)
        helper_refuses_root(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
