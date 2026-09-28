"""Running tinycmdr under sudo must not wreck a user-owned install.

Measured 2026-09-27 on the Mac, two ways in one evening:

  * `sudo tinycmdr config set search.allow_cloud_egress true` - `_write_config` replaces
    config.json, and a replacement takes the AUTHOR of the write, so the file came back
    `root:staff 0600`. The launchd agent runs as the install's own user, could not read
    it, and exited 1 on every respawn.
  * `sudo tinycmdr restart` - the macOS helper's `launchctl bootstrap` found the GUI
    domain of the console user unavailable to root ("Bootstrap failed: 125"), and the
    agent had already been booted OUT, so the bot stayed down.

    python tests/test_root_safety.py
"""
import importlib.util
import json
import shutil
import stat as stat_mod
import subprocess
import sys
import tempfile
import unittest.mock as mock
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "tinycmdr.py"
HELPER = REPO / "maintenance" / "restart-tinycmdr-macos.sh"
FAILS = []


def check(cond, what, extra=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}\n     {extra}")
    else:
        print(f"ok   {what}")


def load(dirpath):
    spec = importlib.util.spec_from_file_location("root_" + dirpath.name,
                                                 dirpath / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def shim(bindir, name, body):
    f = bindir / name
    f.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    f.chmod(f.stat().st_mode | stat_mod.S_IXUSR)
    return f


def main():
    work = Path(tempfile.mkdtemp(prefix="fbrootsafe-"))
    try:
        # ---- a config write puts the OWNER back ------------------------------------
        d = work / "install"
        d.mkdir(parents=True)
        shutil.copy2(SRC, d / "tinycmdr.py")
        (d / "config.json").write_text('{"llm": {}}', encoding="utf-8")
        mod = load(d)

        real_stat = os.stat
        ours = real_stat(str(d / "config.json"))       # how the file looks to the user
        root_owned = os.stat_result(tuple(ours)[:4] + (0, 0) + tuple(ours)[6:])
        seen = {"n": 0}

        def fake_stat(p, *a, **k):
            if str(p).endswith("config.json"):
                seen["n"] += 1
                # before the write it is the user's; a sudo write REPLACES it as root
                return ours if seen["n"] == 1 else root_owned
            return real_stat(p, *a, **k)

        chowns = []
        with mock.patch.object(os, "stat", side_effect=fake_stat), \
                mock.patch.object(os, "chown",
                                  side_effect=lambda p, u, g: chowns.append((u, g))):
            mod._write_config({"llm": {"model": "x"}})
        check(chowns == [(501, 20)],
              "a config write restores the pre-write owner (so a sudo write cannot "
              "leave it unreadable)", chowns)

        # ---- and it keeps the MODE the installer set instead of widening it --------
        d2 = work / "mode"
        d2.mkdir(parents=True)
        shutil.copy2(SRC, d2 / "tinycmdr.py")
        (d2 / "config.json").write_text('{"llm": {}}', encoding="utf-8")
        (d2 / "config.json").chmod(0o600)
        mod2 = load(d2)
        mod2._write_config({"llm": {"model": "z"}})
        got = (d2 / "config.json").stat().st_mode & 0o777
        check(got == 0o600,
              "a config write keeps 0600 instead of the umask's 0644 (config.json can "
              "hold llm.api_key)", oct(got))
        check(json.loads((d2 / "config.json").read_text())["llm"]["model"] == "z",
              "and the write itself landed")

        # ---- the macOS helper refuses root before touching the agent ---------------
        home = work / "home"
        plist_dir = home / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        (plist_dir / "com.tinycmdr.agent.plist").write_text("<plist/>", encoding="utf-8")
        bindir = work / "bin"
        bindir.mkdir()
        marker = work / "launchctl-called"
        shim(bindir, "launchctl", "echo called >> %s; exit 0" % marker)

        env = dict(os.environ, HOME=str(home), PATH="%s:%s" % (bindir, os.environ["PATH"]),
                   TINYCMDR_DIR=str(d))

        # as root (id -u == 0): refuse, and never call launchctl
        shim(bindir, "id", "case \"$1\" in -u) echo 0;; *) echo root;; esac")
        r = subprocess.run(["bash", str(HELPER), "restart"], env=env,
                           capture_output=True, text=True, timeout=60)
        said = r.stdout + r.stderr
        check(r.returncode != 0, f"the helper refuses to run as root ({r.returncode})")
        check("sudo" in said and "LaunchAgent" in said,
              "and says why, and what to do instead", said[-300:])
        check(not marker.exists(),
              "and it never touched the agent (launchctl was not called)")

        # as the user: it proceeds (so the refusal is root-specific, not blanket)
        shim(bindir, "id", "case \"$1\" in -u) echo 501;; *) echo david;; esac")
        r = subprocess.run(["bash", str(HELPER), "status"], env=env,
                           capture_output=True, text=True, timeout=60)
        check(marker.exists(), "as the user it does call launchctl", r.stdout[-200:])
        check(r.returncode == 0, f"and status exits cleanly ({r.returncode})", r.stdout[-300:])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("all root-safety checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
