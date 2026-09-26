"""The Unix installers, driven end to end on a stubbed host.

Every item below is a defect that shipped in 1.0.21, and every one of them is invisible
to a suite that only reads the scripts:

  D1  the Linux installer aborted at `cat > ~/.config/systemd/user/...` because that
      folder is never created (systemd has no tmpfiles entry for it). A fresh
      Debian/Ubuntu user-mode install - THE door the README prints - died after the
      venv, config.json and .env existed and before any unit, enable or start.
  D6  `--no-web` did not close the port: the no-token branch set APP_ARGS="--web"
      regardless, `--web` forces web.enabled=True for that process, no page token was
      minted (WEB_ON=0), and _auth_ok answers True when no token is configured. The
      agent's HTTP API - which runs shell - was open to any local process after the
      operator had closed it. The switch-detection loop that was supposed to notice ran
      on an already-shifted empty "$@", in both installers.
  D5  config.json shipped 0644 (the macOS writer opened it before `umask 077`; Linux
      hardcoded `chmod 644`) beside a 0600 .env, and the install log was 0644 in /tmp
      with the page token printed into it.
  D2  every documented removal door derived its paths from $HOME, so `sudo bash
      uninstall-tinycmdr-macos.sh` looked in /var/root, found nothing, printed "done."
      and exited 0 - and a `--label <l>` install could not be removed by any door.
  D9  `chown $RUN_USER:$RUN_USER` aborted the install on any host where the group is not
      named after the user (AD/LDAP, `useradd -N`, USERGROUPS_ENAB=no, macOS `staff`).
  D3  the release zip carried permission bits without the file-TYPE bits, so Finder's
      Archive Utility extracted INSTALL-MACOS.command, UNINSTALL-MACOS.command and the
      launcher 0644 (archive half: maintenance/check-package-modes.py).
  D4  the package shipped without maintenance/restart-tinycmdr-macos.sh - the day-two
      command the macOS installer and install/README-macos.md both print.
  D7  the uv fallback was reachable only through a live terminal and `--install-python`
      could not override `--python`, so an unattended install could not complete.

HOW IT STAYS HERMETIC. Nothing here touches the author's live install (`~/tinycmdr`,
launchd `com.tinycmdr.agent` on *:8787):

  * every path is under a mkdtemp folder, and HOME points into it;
  * `getent`, `systemctl`, `loginctl`, `journalctl`, `launchctl` and `plutil` are STUBS
    earlier on PATH. The getent stub is what keeps the installers out of the real home:
    both now resolve the invoking user's home from the account database (D2), so a real
    getent would send RUN_HOME to the real ~ - and the macOS uninstaller would then edit
    the real ~/.zshrc;
  * the launchd label is `com.tinycmdr.insttest`, never the default, and launchctl is a
    stub, so no real job can be booted out;
  * `--no-path` goes to the macOS installer: /usr/local/bin may well be writable on a
    box that already has a real install, and the wrapper is written there
    unconditionally. The wrapper half of D2 is tested against a hand-made wrapper in the
    sandbox home instead;
  * the installs run from a COPY of the shippable files, so a fleet-secrets.env can be
    planted for D8 without writing into the repo.

    python tests/test_installer_unix.py
"""
import os
import pathlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile

BASE = pathlib.Path(__file__).resolve().parent.parent
FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILS.append(name)
        print(f"FAIL {name}: {detail}")


def mode_of(path):
    """The permission bits, or None when the file is not there."""
    try:
        return stat.S_IMODE(pathlib.Path(path).stat().st_mode)
    except OSError:
        return None


def interpreter():
    """A 3.10-3.12 interpreter for the sandbox installs. The installers refuse anything
    else on purpose (3.9 predates write_text(newline=...), 3.13+ resolves a broken
    mmpy_bot), so picking the wrong one would report the installer's own guard as a test
    failure."""
    cands = []
    if os.environ.get("TINYCMDR_TEST_PYTHON"):
        cands.append(os.environ["TINYCMDR_TEST_PYTHON"])
    cands += ["/opt/homebrew/bin/python3.12", "/usr/local/bin/python3.12", "python3.12",
              "/opt/homebrew/bin/python3.11", "python3.11", "python3.10", sys.executable]
    for c in cands:
        exe = shutil.which(c)
        if not exe:
            continue
        got = subprocess.run([exe, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True)
        if got.returncode == 0 and got.stdout.strip() in ("3.10", "3.11", "3.12"):
            return exe
    return ""


def write_stubs(bindir, user, home, fake_id=False, curl_fails=False, slim=False):
    """A host with systemd, launchd and a stub `id` - and nothing real behind them."""
    bindir.mkdir(parents=True, exist_ok=True)
    stubs = {
        # Only `getent passwd <user>` is read. Answering with the SANDBOX home is what
        # makes a user-mode install hermetic now that the installers resolve the home
        # from the account database instead of trusting $HOME (D2).
        "getent": f'echo "{user}:x:501:20::{home}:/bin/sh"',
        "systemctl": 'case "$*" in *is-active*) echo active ;; '
                     '*is-enabled*) echo enabled ;; *show*MainPID*) echo 4242 ;; esac\nexit 0',
        "loginctl": "exit 0",
        "journalctl": "exit 0",
        "launchctl": "exit 0",
        "plutil": "exit 0",
    }
    if fake_id:
        # A host where the installer does NOT run as the service user and the group is
        # not named after that user - the AD/LDAP shape from I10. `id -un` differs, so
        # the chowns are attempted; `id -gn` answers a group that exists nowhere, so the
        # old `chown user:user` was "illegal group name" and, under `set -e`, the end of
        # the run.
        stubs["id"] = ('case "$*" in\n'
                       '  "-un") echo "someone-else" ;;\n'
                       '  "-gn") echo "tinycmdr-testgrp" ;;\n'
                       '  "-u") echo 501 ;;\n'
                       '  "-g") echo 20 ;;\n'
                       '  "-u "*) echo 501 ;;\n'
                       '  "-gn "*) echo "tinycmdr-testgrp" ;;\n'
                       'esac\nexit 0')
    if curl_fails:
        # The fetch path must be entered without a download ever happening - including
        # the uv shortcut the installer takes first when uv is already on PATH.
        stubs["curl"] = 'echo "curl: (6) stubbed offline" >&2\nexit 6'
        stubs["uv"] = 'echo "uv: stubbed offline" >&2\nexit 1'
    if slim:
        for k in ("getent", "plutil"):
            stubs.pop(k, None)
    for name, body in stubs.items():
        p = bindir / name
        p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        p.chmod(0o755)
    return bindir


def stub_env(bindir, log, extra=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("SUDO_USER", "TINYCMDR_MODE", "TINYCMDR_USER", "TINYCMDR_SERVICE",
                        "TINYCMDR_DIR", "TINYCMDR_LABEL", "TINYCMDR_ASK",
                        "TINYCMDR_INSTALL_LOG", "TINYCMDR_PYTHON")}
    env["PATH"] = f"{bindir}:{os.environ.get('PATH', '')}"
    env["TINYCMDR_INSTALL_LOG"] = str(log)
    if extra:
        env.update(extra)
    return env


def run(cmd, env, cwd):
    return subprocess.run([str(c) for c in cmd], env=env, cwd=str(cwd), text=True,
                          capture_output=True, stdin=subprocess.DEVNULL, timeout=300)


def sandbox_home_env(sb, bindir, log, user, extra=None):
    """`user_py` is set by main(): the Linux installer takes its interpreter from
    $TINYCMDR_PYTHON, and the system python3 on a dev box is often outside the band."""
    user_py = sandbox_home_env.py
    e = {"HOME": str(sb / "home"), "XDG_RUNTIME_DIR": str(sb / "run"),
         "TINYCMDR_USER": user, "TINYCMDR_PYTHON": user_py}
    if extra:
        e.update(extra)
    return stub_env(bindir, log, e)


def package_tree(pkg):
    """A copy of the shippable files, so an install does not read the working tree twice
    and an `install/fleet-secrets.env` can be planted without writing into the repo."""
    pkg.mkdir(parents=True, exist_ok=True)
    for name in ("tinycmdr.py", "config.example.json", "requirements.txt", ".env.example",
                 # The root-level doors: install.sh is what the one-line curl command
                 # runs, and the two .command files are what a Finder double-click runs.
                 "install.sh", "INSTALL-MACOS.command", "UNINSTALL-MACOS.command",
                 "tinycmdr", "launch-tinycmdr.sh"):
        src = BASE / name
        if src.exists():
            shutil.copy2(src, pkg / name)
    for name in ("install", "maintenance", "tools"):
        src = BASE / name
        if src.is_dir():
            shutil.copytree(src, pkg / name)
    return pkg


def fake_venv(inst, py):
    """Pre-build the venv the installer would build. `python -m venv` plus a pip install
    of mmpy_bot is not something a hermetic suite can run, and both installers skip the
    creation step when the interpreter is already there. This stands in for the venv's
    python: pip and the dependency probe are answered locally, everything else (the
    jget/cfgval/config-writer heredocs) is handed to the real interpreter, because the
    installers run their own Python through it."""
    (inst / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    p = inst / "venv" / "bin" / "python"
    p.write_text(f'#!/bin/sh\n'
                 f'REAL="{py}"\n'
                 f'case "$1" in\n'
                 f'  -V) echo "Python 3.12.11" ;;\n'
                 f'  -m) exit 0 ;;\n'                      # pip install -r requirements.txt
                 f'  -c) case "$2" in\n'
                 f'        *importlib.metadata*) echo "9.9.9" ;;\n'
                 f'        *requests*) exit 0 ;;\n'
                 f'        *) exec "$REAL" "$@" ;;\n'
                 f'      esac ;;\n'
                 f'  *) exec "$REAL" "$@" ;;\n'              # the heredoc scripts
                 f'esac\n', encoding="utf-8")
    p.chmod(0o755)


def fleet_env(path, extra=""):
    path.write_text("# planted by tests/test_installer_unix.py\n"
                    "TINYCMDR_MM_TOKEN=abcdef0123456789abcdef0123456789\n"
                    "TAVILY_API_KEY=tvly-planted-by-the-test\n" + extra, encoding="utf-8")
    return path


def make_bus(sb):
    """systemd user installs want a session bus; the installer's check is `-S` on the
    node, so leaving a real socket behind is enough."""
    bus = sb / "run" / "bus"
    bus.parent.mkdir(parents=True, exist_ok=True)
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(bus))
    s.close()


# ------------------------------------------------------------------------------ D1 ---
def case_linux_user_mode(sb, pkg, bindir, user, py):
    inst = sb / "lin-inst"
    fake_venv(inst, py)
    log = sb / "logs" / "lin-a.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = sandbox_home_env(sb, bindir, log, user)
    got = run(["bash", pkg / "install" / "install-tinycmdr.sh", "-y", "--mode", "user",
               "--no-deps", "--no-sudoers", "--no-start", "--no-web",
               "--install-dir", inst], env, pkg)
    unit = sb / "home" / ".config" / "systemd" / "user" / "tinycmdr.service"
    check("D1 the Linux user-mode install exits 0", got.returncode == 0,
          f"rc={got.returncode}; tail: {got.stdout[-500:]}{got.stderr[-300:]}")
    check("D1 the unit's directory is created and the unit written", unit.exists(),
          f"{unit} is not there: {got.stdout[-300:]}")
    text = unit.read_text(encoding="utf-8") if unit.exists() else ""
    execs = [l for l in text.splitlines() if l.startswith("ExecStart")]
    check("D6 --no-web leaves --web out of the unit", "--web" not in text,
          f"ExecStart: {execs}")
    check("D5 config.json is 0600",
          mode_of(inst / "config.json") == 0o600,
          f"mode {oct(mode_of(inst / 'config.json') or 0)}")
    check("D5 .env is 0600", mode_of(inst / ".env") == 0o600,
          f"mode {oct(mode_of(inst / '.env') or 0)}")
    check("D5 the install log is created 0600", mode_of(log) == 0o600,
          f"mode {oct(mode_of(log) or 0)}")
    real_group = subprocess.run(["id", "-gn"], capture_output=True, text=True).stdout.strip()
    check("D9 the pre-flight names the primary group from id -gn",
          f"service group: {real_group}" in got.stdout,
          f"expected 'service group: {real_group}' in the output")
    return inst


def case_linux_chown_fallback(sb, pkg, bindir, user, py):
    """D9: the installer is not the service user and the group has another name. The old
    `chown $RUN_USER:$RUN_USER` died right after config.json, leaving no .env and no
    unit."""
    home = sb / "home-chown"
    home.mkdir(parents=True, exist_ok=True)
    inst = sb / "lin-chown"
    fake_venv(inst, py)
    fake = write_stubs(sb / "bin-fakeid", user, home, fake_id=True)
    log = sb / "logs" / "lin-chown.log"
    env = stub_env(fake, log, {"HOME": str(home), "XDG_RUNTIME_DIR": str(sb / "run"),
                               "TINYCMDR_USER": user, "TINYCMDR_PYTHON": py})
    got = run(["bash", pkg / "install" / "install-tinycmdr.sh", "-y", "--mode", "user",
               "--no-deps", "--no-sudoers", "--no-start", "--no-web",
               "--install-dir", inst], env, pkg)
    out = got.stdout + got.stderr
    check("D9 the install completes where chown user:user used to abort",
          got.returncode == 0, f"rc={got.returncode}; tail: {got.stdout[-400:]}")
    check("D9 the resolved group is what id -gn answered",
          "service group: tinycmdr-testgrp" in out,
          "no 'service group: tinycmdr-testgrp' in the output")
    check("D9 a failed chown is a named warning, not an exit",
          "could not chown" in out, "no 'could not chown' warning printed")
    check("D9 the install still wrote .env and the unit",
          (inst / ".env").exists()
          and (home / ".config" / "systemd" / "user" / "tinycmdr.service").exists(),
          "a chown failure still cost the install its .env or unit")


def case_linux_web_on(sb, pkg, bindir, user, py):
    """D5/D6 from the other side: with the page wanted, the unit runs it, a token
    exists, and the token value stays out of the transcript."""
    inst = sb / "lin-web"
    fake_venv(inst, py)
    log = sb / "logs" / "lin-web.log"
    env = sandbox_home_env(sb, bindir, log, user)
    got = run(["bash", pkg / "install" / "install-tinycmdr.sh", "-y", "--mode", "user",
               "--no-deps", "--no-sudoers", "--no-start", "--install-dir", inst],
              env, pkg)
    unit = sb / "home" / ".config" / "systemd" / "user" / "tinycmdr.service"
    text = unit.read_text(encoding="utf-8") if unit.exists() else ""
    check("D6 with the page wanted the unit does start it", "--web" in text,
          f"ExecStart: {[l for l in text.splitlines() if 'ExecStart' in l]}")
    env_text = (inst / ".env").read_text(encoding="utf-8") if (inst / ".env").exists() else ""
    token = re.search(r"^TINYCMDR_WEB_TOKEN=([0-9a-f]{48})$", env_text, re.M)
    check("D5 a page token is minted into .env when the page runs", bool(token),
          f"no 48-hex TINYCMDR_WEB_TOKEN in .env: {env_text[-160:]}")
    if token:
        log_text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        check("D5 the page token never reaches the transcript",
              token.group(1) not in got.stdout and token.group(1) not in log_text,
              "the token was echoed to stdout or written into the install log")
    check("D5 .env holds exactly one TINYCMDR_WEB_TOKEN line",
          env_text.count("TINYCMDR_WEB_TOKEN=") == 1,
          f"{env_text.count('TINYCMDR_WEB_TOKEN=')} line(s)")
    return inst


def case_linux_uninstall(sb, pkg, bindir, user, py, inst):
    """D2 (Linux half): the folder, the unit and the PATH wrapper go, and a second run
    says there was nothing to remove instead of printing 'done.'."""
    wrapper = sb / "home" / ".local" / "bin" / "tinycmdr"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(f'#!/bin/sh\nexec "{inst}/tinycmdr" "$@"\n', encoding="utf-8")
    env = sandbox_home_env(sb, bindir, sb / "logs" / "lin-uninstall.log", user)
    got = run(["bash", pkg / "install" / "install-tinycmdr.sh", "--uninstall",
               "--mode", "user", "--install-dir", inst], env, pkg)
    check("D2 a user-mode uninstall exits 0", got.returncode == 0,
          f"rc={got.returncode}; {got.stdout[-300:]}")
    check("D2 the install folder is gone", not inst.exists(), f"{inst} survives")
    check("D2 the unit is gone",
          not (sb / "home" / ".config" / "systemd" / "user" / "tinycmdr.service").exists(),
          "the unit survived")
    check("D2 the PATH wrapper is gone", not wrapper.exists(), f"{wrapper} survives")
    again = run(["bash", pkg / "install" / "install-tinycmdr.sh", "--uninstall",
                 "--mode", "user", "--install-dir", inst], env, pkg)
    check("D2 a removal that finds nothing says so",
          "nothing to remove" in again.stdout + again.stderr,
          f"second uninstall printed: {again.stdout.strip()[-200:]}")


def case_linux_secrets_lane(sb, pkg, bindir, user, py):
    """D8 (Linux half): the package's fleet-secrets.env carries TINYCMDR_MM_TOKEN, so the
    lane is Mattermost - and the token is written once, from this run."""
    inst = sb / "lin-lane"
    fake_venv(inst, py)
    fleet_env(pkg / "install" / "fleet-secrets.env")
    env = sandbox_home_env(sb, bindir, sb / "logs" / "lin-lane.log", user)
    got = run(["bash", pkg / "install" / "install-tinycmdr.sh", "-y", "--mode", "user",
               "--no-deps", "--no-sudoers", "--no-start", "--no-web",
               "--mattermost-url", "chat.invalid", "--allowed-user", "u1",
               "--install-dir", inst], env, pkg)
    env_text = (inst / ".env").read_text(encoding="utf-8") if (inst / ".env").exists() else ""
    check("D8 the lane is chosen from the secrets file's token",
          "installing WITHOUT a chat account" not in got.stdout
          and "TINYCMDR_MM_TOKEN from the package's fleet-secrets.env" in got.stdout,
          f"the run still took the page lane, or never read the token: {got.stdout[-300:]}")
    check("D8 the token is written once, with the file's value",
          env_text.count("TINYCMDR_MM_TOKEN=") == 1
          and "TINYCMDR_MM_TOKEN=abcdef0123456789abcdef0123456789" in env_text,
          f"lines: {[l for l in env_text.splitlines() if 'MM_TOKEN' in l]}")
    check("D8 the shared search key rides along",
          "TAVILY_API_KEY=tvly-planted-by-the-test" in env_text,
          "the fleet key never reached .env")


# --------------------------------------------------------------------------- D2/D5 ---
def case_macos_install(sb, pkg, bindir, user, py):
    """A real macOS install: files, config, .env and the plist - no load (--no-start),
    no wrapper (--no-path), and a label of our own so no real job is ever involved."""
    inst = sb / "mac-inst"
    fake_venv(inst, py)
    log = sb / "logs" / "mac-install.log"
    env = sandbox_home_env(sb, bindir, log, user, {"TINYCMDR_PYTHON": py})
    got = run(["bash", pkg / "install" / "install-tinycmdr-macos.sh", "-y", "--no-start",
               "--no-web", "--no-path", "--python", py, "--label", "com.tinycmdr.insttest",
               "--install-dir", inst], env, pkg)
    check("D2 the macOS install exits 0", got.returncode == 0,
          f"rc={got.returncode}; tail: {got.stdout[-600:]}{got.stderr[-400:]}")
    label_file = inst / ".tinycmdr-label"
    check("D2 the launchd label is recorded in the install folder",
          label_file.exists()
          and label_file.read_text(encoding="utf-8").strip() == "com.tinycmdr.insttest",
          f"{label_file}: {label_file.read_text() if label_file.exists() else 'missing'}")
    check("D5 config.json is 0600 on macOS", mode_of(inst / "config.json") == 0o600,
          f"mode {oct(mode_of(inst / 'config.json') or 0)}")
    check("D5 .env is 0600 on macOS", mode_of(inst / ".env") == 0o600,
          f"mode {oct(mode_of(inst / '.env') or 0)}")
    check("D5 the install log is created 0600 on macOS", mode_of(log) == 0o600,
          f"mode {oct(mode_of(log) or 0)}")
    check("D4 the day-two helper the installer prints is installed",
          (inst / "maintenance" / "restart-tinycmdr-macos.sh").exists(),
          "the install carries no maintenance/restart-tinycmdr-macos.sh")
    if os.uname().sysname == "Darwin":
        plist = sb / "home" / "Library" / "LaunchAgents" / "com.tinycmdr.insttest.plist"
        check("D2 the plist lands under the INVOKING user's home", plist.exists(),
              f"{plist} is not there")
    else:
        print("SKIP the plist half of D2: launchd is macOS-only")
    return inst


def case_macos_uninstall(sb, pkg, bindir, user, py, inst):
    """D2: the documented `sudo`-form removal really removes. SUDO_USER with a
    /var/root-shaped HOME is the I6 repro - the old uninstaller derived every path from
    $HOME, so it looked in that fake root home, found nothing and printed 'done.'."""
    home = sb / "home"
    plist = home / "Library" / "LaunchAgents" / "com.tinycmdr.insttest.plist"
    wrapper = home / ".local" / "bin" / "tinycmdr"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(f'#!/bin/sh\nexec "{inst}/tinycmdr" "$@"\n', encoding="utf-8")
    zshrc = home / ".zshrc"
    zshrc.write_text('# existing line\n\n# tinycmdr\nexport PATH="$HOME/.local/bin:$PATH"\n',
                     encoding="utf-8")
    rootish = sb / "root-home"
    rootish.mkdir(parents=True, exist_ok=True)
    env = stub_env(bindir, sb / "logs" / "mac-uninstall.log",
                   {"HOME": str(rootish), "SUDO_USER": user,
                    "XDG_RUNTIME_DIR": str(sb / "run")})
    got = run(["bash", pkg / "install" / "uninstall-tinycmdr-macos.sh",
               "--install-dir", inst], env, pkg)
    check("D2 the documented removal exits 0", got.returncode == 0,
          f"rc={got.returncode}; tail: {got.stdout[-400:]}{got.stderr[-300:]}")
    check("D2 the install folder is gone under the sudo-shaped environment",
          not inst.exists(), f"{inst} survives")
    check("D2 the PATH wrapper in the invoking user's home is gone", not wrapper.exists(),
          f"{wrapper} survives")
    check("D2 the '# tinycmdr' PATH line is gone",
          "# tinycmdr" not in zshrc.read_text(encoding="utf-8"),
          zshrc.read_text(encoding="utf-8"))
    if os.uname().sysname == "Darwin":
        check("D2 the --label install's plist is gone (the label was read back)",
              not plist.exists(), f"{plist} survived its own install's removal")
    else:
        print("SKIP the plist half of D2: launchd is macOS-only")
    again = run(["bash", pkg / "install" / "uninstall-tinycmdr-macos.sh",
                 "--install-dir", inst], env, pkg)
    check("D2 a macOS removal that finds nothing says so",
          "nothing to remove" in again.stdout + again.stderr,
          f"second uninstall printed: {again.stdout.strip()[-200:]}")


def case_macos_secrets_lane(sb, pkg, bindir, user, py):
    """D8: --secrets-file carries the bot token, so the lane is Mattermost and the token
    is written once. Before this, the lane branch ran first and wrote an empty
    `TINYCMDR_MM_TOKEN=` ahead of the file's real line - and the build reads the FIRST
    occurrence."""
    inst = sb / "mac-secrets"
    fake_venv(inst, py)
    secrets = fleet_env(sb / "fleet-secrets.env")
    env = sandbox_home_env(sb, bindir, sb / "logs" / "mac-secrets.log", user,
                           {"TINYCMDR_PYTHON": py})
    got = run(["bash", pkg / "install" / "install-tinycmdr-macos.sh", "-y", "--no-launchd",
               "--no-path", "--no-web", "--python", py, "--secrets-file", secrets,
               "--mattermost-url", "chat.invalid", "--allowed-user", "u1",
               "--install-dir", inst], env, pkg)
    env_text = (inst / ".env").read_text(encoding="utf-8") if (inst / ".env").exists() else ""
    cfg = (inst / "config.json").read_text(encoding="utf-8") if (inst / "config.json").exists() else ""
    check("D8 macOS: the lane comes from the secrets file's token",
          "installing WITHOUT a chat account" not in got.stdout
          and "TINYCMDR_MM_TOKEN from" in got.stdout,
          f"rc={got.returncode}; {got.stdout[-400:]}")
    check("D8 macOS: exactly one TINYCMDR_MM_TOKEN line, with the file's value",
          env_text.count("TINYCMDR_MM_TOKEN=") == 1
          and "TINYCMDR_MM_TOKEN=abcdef0123456789abcdef0123456789" in env_text,
          f"lines: {[l for l in env_text.splitlines() if 'MM_TOKEN' in l]}")
    check("D8 macOS: the server lands in config.json for the chat lane",
          '"url": "chat.invalid"' in cfg, "mattermost.url was not written")


# ----------------------------------------------------------------------------- D10 ---
def case_help_and_footer(sb, pkg, bindir, user, py):
    """D10: --help prints the whole header, and a headless install.sh leaks no /dev/tty
    error and names the platform's installer in its footer."""
    env = sandbox_home_env(sb, bindir, sb / "logs" / "help.log", user)
    mac_help = run(["bash", pkg / "install" / "install-tinycmdr-macos.sh", "--help"],
                   env, pkg)
    body = mac_help.stdout
    check("D10 macOS --help lists --no-path and --force-python",
          "--no-path" in body and "--force-python" in body,
          "one of the two switches is still undocumented")
    check("D10 macOS --help prints the header to its last line",
          "secret in a file nobody thinks to delete" in body,
          "the usage range still stops early: " + repr(body[-120:]))
    check("D10 macOS --help names the 3.10-3.12 band", "3.10-3.12" in body,
          "the band is not stated in the installer help")
    lin_help = run(["bash", pkg / "install" / "install-tinycmdr.sh", "--help"], env, pkg)
    check("D10 Linux --help lists --no-path", "--no-path" in lin_help.stdout,
          "the Linux help does not document --no-path")
    check("D10 Linux --help prints the header to its last line",
          "nobody thinks to delete" in lin_help.stdout,
          "the usage range still stops early: " + repr(lin_help.stdout[-120:]))

    # install.sh, headlessly, against a local archive: the footer must name THIS
    # platform's installer, and the /dev/tty probe must not print an error.
    dist = sb / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    ver = re.search(r'^VERSION = "(.*?)"',
                    (pkg / "tinycmdr.py").read_text(encoding="utf-8"), re.M).group(1)
    darwin = os.uname().sysname == "Darwin"
    asset = "tinycmdr-macos.zip" if darwin else "tinycmdr-linux.tar.gz"
    installer = "install/install-tinycmdr-macos.sh" if darwin else "install/install-tinycmdr.sh"
    stage = sb / "stage" / f"tinycmdr-{ver}"
    (stage / "install").mkdir(parents=True, exist_ok=True)
    shutil.copy2(pkg / "tinycmdr.py", stage / "tinycmdr.py")
    shutil.copy2(pkg / installer, stage / installer)
    if darwin:
        import zipfile
        with zipfile.ZipFile(dist / asset, "w") as z:
            for f in sorted((sb / "stage").rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(sb / "stage").as_posix())
    else:
        import tarfile
        with tarfile.open(dist / asset, "w:gz") as t:
            t.add(stage, arcname=f"tinycmdr-{ver}")
    got = run(["bash", pkg / "install.sh", "--help"],
              stub_env(bindir, sb / "logs" / "installsh.log",
                       {"HOME": str(sb / "home"), "TINYCMDR_URL": f"file://{dist}"}), pkg)
    combined = got.stdout + got.stderr
    check("D10 a headless install.sh prints no /dev/tty error", "/dev/tty" not in combined,
          "leaked: " + repr([l for l in combined.splitlines() if "/dev/tty" in l]))
    check("D2 install.sh's footer names the platform's installer",
          f"bash ~/tinycmdr/{installer} --uninstall" in got.stdout,
          f"footer:\n{got.stdout[-400:]}")
    if darwin:
        check("D2 install.sh's footer does not offer the Linux installer on macOS",
              "bash ~/tinycmdr/install/install-tinycmdr.sh " not in got.stdout,
              "the Linux installer is still printed on macOS")


# ------------------------------------------------------------------------ D3/D4/D7 ---
def case_archive_and_python(sb, pkg, bindir, user, py):
    """D3/D4 in the built archive, and D7's refusals."""
    got = subprocess.run([sys.executable, str(BASE / "maintenance" / "check-package-modes.py")],
                         capture_output=True, text=True, cwd=str(BASE), timeout=900)
    if os.uname().sysname == "Darwin":
        check("D3/D4 ditto extracts every door of the built zip as 0755",
              got.returncode == 0,
              f"rc={got.returncode}\n{got.stdout[-700:]}{got.stderr[-300:]}")
    elif got.returncode == 3:
        print("SKIP D3's extraction half: ditto is macOS-only\n" + got.stdout.strip())
    else:
        check("D4 SHIP's maintenance/ entries equal ALLOWED_MAINTENANCE",
              got.returncode == 0, f"rc={got.returncode}\n{got.stdout[-500:]}")

    # D7: a 3.9 interpreter is refused with the band named, even under -y - no amount of
    # consenting makes Path.write_text(newline=...) work - and the refusal names the
    # switch that does.
    fake39 = sb / "fake39" / "python3.9"
    fake39.parent.mkdir(parents=True, exist_ok=True)
    fake39.write_text('#!/bin/sh\n'
                      'case "$1" in\n'
                      '  -V) echo "Python 3.9.6" ;;\n'
                      '  -c) echo "3.9" ;;\n'
                      'esac\nexit 0\n', encoding="utf-8")
    fake39.chmod(0o755)
    log = sb / "logs" / "py39.log"
    env = sandbox_home_env(sb, bindir, log, user, {"TINYCMDR_PYTHON": py})
    got = run(["bash", pkg / "install" / "install-tinycmdr-macos.sh", "-y", "--no-launchd",
               "--no-web", "--python", fake39, "--install-dir", sb / "py39-inst"], env, pkg)
    out = got.stdout + got.stderr
    check("D7 python 3.9 is refused, with the band named",
          got.returncode != 0 and "3.10-3.12" in out and "3.9" in out,
          f"rc={got.returncode}; {out[-400:]}")
    check("D7 the 3.9 refusal names --install-python", "--install-python" in out,
          "the refusal does not name the switch that would work")

    # D7: --install-python beats an explicit --python. curl is stubbed to fail, so the
    # fetch is ATTEMPTED and the run ends there - no download, and never the 3.9 refusal.
    offline = write_stubs(sb / "bin-offline", user, sb / "home", curl_fails=True)
    env2 = sandbox_home_env(sb, offline, sb / "logs" / "pyfetch.log", user)
    got = run(["bash", pkg / "install" / "install-tinycmdr-macos.sh", "-y", "--no-launchd",
               "--no-web", "--python", fake39, "--install-python",
               "--install-dir", sb / "pyfetch-inst"], env2, pkg)
    out = got.stdout + got.stderr
    check("D7 --install-python wins over --python (it fetches instead of refusing)",
          got.returncode != 0 and "too old" not in out
          and ("could not download uv" in out or "uv could not fetch python 3.12" in out),
          f"rc={got.returncode}; {out[-400:]}")


sandbox_home_env.py = ""      # filled in main(), read by the helper above


def main():
    py = interpreter()
    if not py:
        # 77 is this tree's "could not grade the subject on this host": a box with only
        # 3.13 (or only 3.9) has no interpreter the installers will accept, so there is
        # nothing here to assert against - and the runner counts a skip as red.
        print("SKIP no Python 3.10-3.12 on this host: the installers refuse anything else, "
              "so this suite cannot grade them (install python3.12, or set "
              "TINYCMDR_TEST_PYTHON to one)")
        return 77
    print(f"interpreter: {py}")
    sandbox_home_env.py = py
    user = os.environ.get("USER") or "tester"
    sb = pathlib.Path(tempfile.mkdtemp(prefix="tinycmdr-insttest-"))
    try:
        home = sb / "home"
        home.mkdir(parents=True, exist_ok=True)
        bindir = write_stubs(sb / "bin", user, home)
        make_bus(sb)
        pkg = package_tree(sb / "pkg")
        case_linux_user_mode(sb, pkg, bindir, user, py)
        case_linux_chown_fallback(sb, pkg, bindir, user, py)
        lin_web = case_linux_web_on(sb, pkg, bindir, user, py)
        case_linux_uninstall(sb, pkg, bindir, user, py, lin_web)
        case_linux_secrets_lane(sb, package_tree(sb / "pkg-lane"), bindir, user, py)
        mac_inst = case_macos_install(sb, pkg, bindir, user, py)
        case_macos_uninstall(sb, pkg, bindir, user, py, mac_inst)
        case_macos_secrets_lane(sb, pkg, bindir, user, py)
        case_help_and_footer(sb, pkg, bindir, user, py)
        case_archive_and_python(sb, pkg, bindir, user, py)
    finally:
        shutil.rmtree(sb, ignore_errors=True)
    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed:")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("all installer checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
