"""test_update_surface - one merged suite (test_update_notes, test_installer_parity).

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


def _suite_test_update_notes():
    """Update output says what changed and what to do - not the updater's internal policy.

Three notes earned this suite: the `.git` notice narrated a 1.0.46-1.0.48 failure mode to
everyone with a checkout, the dev-kit keep named a where-roles.json a clone may not even
have, and the host-default note claimed "this release's default differs" for a theme.toml
the release never touched. The update prints what it wrote and what the operator must do;
the keep-it-silent paths stay silent.

    python tests/test_update_surface.py
"""
    import importlib.util
    import json
    import os
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

    FAILS = []


    def check(what, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % (detail,)))
        if not ok:
            FAILS.append(what)


    def stage(work):
        for name in ("theme.default.toml", "soul.example.md"):
            shutil.copy2(BASE / name, work / name)
        shutil.copy2(SRC, work / "tinycmdr.py")
        shutil.copy2(BASE / "tests" / "fixture-config.json", work / "config.json")


    def load(work):
        spec = importlib.util.spec_from_file_location("tinycmdr_notes", work / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["tinycmdr_notes"] = mod
        spec.loader.exec_module(mod)
        return mod


    def main():
        work = Path(tempfile.mkdtemp(prefix="fbnotes-"))
        try:
            stage(work)
            mod = load(work)

            # a bare install: no declaration, no checkout evidence, nothing to keep for
            check("a bare install has no dev-tree reason", mod._dev_tree_reason() is None,
                  repr(mod._dev_tree_reason()))

            # checkout evidence alone - the shape a contributor's clone has
            (work / ".git").mkdir()
            check("a .git alone reads as a checkout", mod._dev_tree_reason() == "checkout",
                  repr(mod._dev_tree_reason()))
            (work / "docs").mkdir(exist_ok=True)
            (work / "docs" / "development.md").write_text("x", encoding="utf-8")
            (work / "STATUS.json").write_text("{}", encoding="utf-8")
            note = mod._prune_dev_kit()
            check("a checkout keeps its kit",
                  (work / "docs" / "development.md").exists()
                  and (work / "STATUS.json").exists())
            check("...and says nothing about it", note == "", repr(note))

            # a declaration pointing at THIS tree is reason enough too - and stays quiet
            shutil.rmtree(work / ".git")
            (work / "maintenance").mkdir(exist_ok=True)
            roles = work / "maintenance" / "where-roles.json"
            roles.write_text(json.dumps([{"role": "dev", "path": str(work)}]), encoding="utf-8")
            check("a dev declaration reads as declared", mod._dev_tree_reason() == "declared",
                  repr(mod._dev_tree_reason()))
            check("...and the prune stays quiet too", mod._prune_dev_kit() == "",
                  repr(mod._prune_dev_kit()))
            roles.write_text("{ not json", encoding="utf-8")
            check("an unreadable declaration fails safe as dev",
                  mod._dev_tree_reason() == "declared", repr(mod._dev_tree_reason()))
            roles.unlink()

            # host-default notes: only a default THIS package wrote produces one
            (work / "theme.toml").write_text("# mine\n", encoding="utf-8")
            mod._HOST_DEFAULT_GAPS["checked"] = False     # host_file_gaps caches its answer
            mod._HOST_DEFAULT_GAPS["gaps"] = []
            check("a differing theme.toml with an untouched default prints no note",
                  mod._host_gap_notes([]) == [], repr(mod._host_gap_notes([])))
            lines = mod._host_gap_notes(["theme.default.toml"])
            check("a moved default prints one note naming both files",
                  len(lines) == 1 and "theme.toml" in lines[0]
                  and "theme.default.toml" in lines[0], repr(lines))

            if FAILS:
                print("\n%d FAILED: %s" % (len(FAILS), "; ".join(FAILS)))
                return 1
            print("\nall good")
            return 0
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return main()


def _suite_test_installer_parity():
    """The three installers must offer the same PORTABLE switches, under their own spellings.

Three hand-written installers (POSIX sh, macOS sh, PowerShell) are 234 KB of near-duplicate
shell, and the failure this suite exists for has already shipped once: v1.0.28's changelog
records "the three platforms agree on secrets", i.e. before that they did not, and nothing
checked. A user reading the README cannot tell which switches their platform actually takes.

The contract is written down here, once:

  * PORTABLE - a capability a user can name on any platform, with each installer's spelling.
    Every one must exist in every installer, or this suite fails.
  * PLATFORM_ONLY - switches that are genuinely about one OS's mechanism (launchd, apt,
    Task Scheduler, sudoers, uv). Each must exist in the file(s) declared for it, and a NEW
    flag found in one shell installer and no other fails until it is either ported or declared
    here with its reason.

Extraction is from the installer's own argument parser - the `case "$1" in` arms for the shell
installers, the `param(...)` block for PowerShell - not from the file at large, because a
loose scan picks up the flags of the programs the installer calls (`--disable-pip-version-check`,
`--no-pager`, `--once`).

A capability is only portable when every platform's spelling exists in every installer. The
built-in local web UI was removed from all three installers (and from the assistant they
ship), so it is not a capability and is not listed here.

    python tests/test_update_surface.py
"""
    import re
    from pathlib import Path

    BASE = Path(__file__).resolve().parent.parent
    SH = BASE / "install" / "install-tinycmdr.sh"
    MAC = BASE / "install" / "install-tinycmdr-macos.sh"
    PS1 = BASE / "install" / "install-tinycmdr.ps1"

    FAILS = []


    def check(name, cond, detail=""):
        if cond:
            print(f"ok   {name}")
        else:
            FAILS.append(name)
            print(f"FAIL {name}: {detail}")


    # capability -> (POSIX spelling, macOS spelling, PowerShell spelling)
    PORTABLE = {
        "install directory": ("--install-dir", "--install-dir", "InstallDir"),
        "bot name": ("--bot-name", "--bot-name", "BotName"),
        "mattermost url": ("--mattermost-url", "--mattermost-url", "MattermostUrl"),
        "mattermost token": ("--token", "--token", "MattermostToken"),
        "token file": ("--token-file", "--token-file", "MattermostTokenFile"),
        "allowed user": ("--allowed-user", "--allowed-user", "AllowedUser"),
        "telegram token": ("--telegram-token", "--telegram-token", "TelegramToken"),
        "telegram ids": ("--telegram-ids", "--telegram-ids", "TelegramIds"),
        "model": ("--model", "--model", "Model"),
        "model base url": ("--model-base-url", "--model-base-url", "ModelBaseUrl"),
        "no start": ("--no-start", "--no-start", "NoStart"),
        "no PATH edit": ("--no-path", "--no-path", "NoPath"),
        "secrets file": ("--secrets-file", "--secrets-file", "SecretsFile"),
        # The consent flag for web search leaving the machine. It exists because a keyless
        # install used to reach a third-party search API with nobody asked (review, 2026-09-27).
        "search egress": ("--search-egress", "--search-egress", "SearchEgress"),
        "force": ("--force", "--force", "Force"),
        "verify only": ("--verify-only", "--verify-only", "VerifyOnly"),
        "uninstall": ("--uninstall", "--uninstall", "Uninstall"),
        # The page: bind, port, and the off switch, on all three platforms.
        "page host": ("--web-host", "--web-host", "WebHost"),
        "page port": ("--web-port", "--web-port", "WebPort"),
        # The page's token: settable at install (2026-10-04), where the wizard used to
        # only mint one. Every platform must understand it.
        "page token": ("--web-token", "--web-token", "WebToken"),
        "no page": ("--no-web", "--no-web", "NoWeb"),
    }

    # flag -> (files it must exist in, why it is not portable)
    PLATFORM_ONLY = {
        "--yes": ((SH, MAC), "ask nothing: the POSIX spelling; PowerShell uses -NonInteractive"),
        "--help": ((SH, MAC), "the POSIX spelling; PowerShell's Get-Help has no parameter"),
        "--mode": ((SH,), "user vs system install - one POSIX mechanism"),
        "--system": ((SH,), "system install - one POSIX mechanism"),
        "--user": ((SH,), "user install - one POSIX mechanism"),
        "--no-root": ((SH,), "never ask for root - one POSIX mechanism"),
        "--no-sudoers": ((SH,), "the no-sudoers door - one POSIX mechanism"),
        "--no-deps": ((SH,), "do not touch apt - Linux only"),
        "--force-python": ((MAC,), "rebuild the venv from scratch - macOS only"),
        "--install-python": ((MAC,), "fetch a private python with uv; the Linux installer has "
                                     "no such flag (see ~/whats-live/ISSUES.md)"),
        "--python": ((MAC,), "interpreter to build the venv from - macOS only"),
        "--no-launchd": ((MAC,), "skip the launchd agent - macOS only"),
        "--no-service": ((SH,), "skip the systemd unit (files only) - Linux only; the macOS "
                                "spelling is --no-launchd and Windows has -SkipTask"),
        "--label": ((MAC,), "launchd label - macOS only"),
        "--use-fleet-model": ((MAC,), "reuse the fleet's model config - macOS only"),
        "AddEndpoint": ((PS1,), "repeatable extra model endpoints - PowerShell only"),
        "AddSearch": ((PS1,), "repeatable extra web-search providers - PowerShell only; the "
                              "POSIX installers take theirs at the search prompt, and "
                              "`tinycmdr search add` is the door on every platform"),
        "AsService": ((PS1,), "boot-start scheduled task - Windows only"),
        "ForcePython": ((PS1,), "accept an interpreter NEWER than 3.12 and hope - the "
                                "Windows twin of macOS's --force-python"),
        "InstallPython": ((PS1,), "kept for compatibility: installing Python is the default now"),
        "MattermostPort": ((PS1,), "split port argument - PowerShell only"),
        "NoPause": ((PS1,), "do not hold the window open - Windows only; the .cmd passes it"),
        "NonInteractive": ((PS1,), "never ask - the Windows spelling of --yes"),
        "Python": ((PS1,), "full path to python.exe - Windows only"),
        "SkipTask": ((PS1,), "files only, no autostart - Windows only"),
        "TaskName": ((PS1,), "scheduled task name - Windows only"),
    }


    def shell_flags(path):
        """The long options the installer's own `case "$1" in` loop parses."""
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"case\s+\"?\$1\"?\s+in\r?\n(.*?)\r?\n\s*esac", text, re.S)
        if not m:
            return set()
        return set(re.findall(r"--[a-z][a-z-]*", m.group(1)))


    def ps_params(path):
        """The parameters the PowerShell script declares in its param() block."""
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^\s*param\s*\((.*?)^\s*\)", text, re.S | re.M)
        if not m:
            return set()
        return set(re.findall(r"\[[A-Za-z\[\]]+\]\s*\$([A-Za-z]+)", m.group(1)))


    def documented_flags(path):
        """Every switch the installer documents in its own header comment block.

    Not line-anchored: the headers document aliases inline ("(aliases: --system, --no-root)")
    and combined spellings ("-y | --yes"), and both count as documented.
    """
        header = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("#"):
                header.append(line)
            elif header:
                break
        return set(re.findall(r"--[a-z][a-z-]*", "\n".join(header)))


    def main():
        for p in (SH, MAC, PS1):
            check("%s is in the tree" % p.name, p.exists(), p)
        if not all(p.exists() for p in (SH, MAC, PS1)):
            return 1

        sh, mac, ps = shell_flags(SH), shell_flags(MAC), ps_params(PS1)
        check("the POSIX installer's argument parser was found", len(sh) > 20, sorted(sh))
        check("the macOS installer's argument parser was found", len(mac) > 20, sorted(mac))
        check("the PowerShell param() block was found", len(ps) > 20, sorted(ps))

        for cap, (a, b, c) in sorted(PORTABLE.items()):
            check("portable on all three: %s" % cap,
                  a in sh and b in mac and c in ps,
                  "sh=%s mac=%s ps1=%s" % (a in sh, b in mac, c in ps))

        for flag, (paths, why) in sorted(PLATFORM_ONLY.items()):
            got = ps if flag[:1].isupper() else sh | mac
            check("declared platform-only switch exists: %s (%s)" % (flag, why.split(" - ")[-1]),
                  flag in got, sorted(got))

        portable_sh = {v[0] for v in PORTABLE.values()} | {v[1] for v in PORTABLE.values()}
        declared = set(PLATFORM_ONLY)
        for label, flags, other in (("POSIX", sh, mac), ("macOS", mac, sh)):
            strays = sorted(f for f in flags
                            if f not in other and f not in portable_sh | declared)
            check("%s-only switches are all declared" % label, not strays, strays)
        stray_ps = sorted(p for p in ps
                          if p not in {v[2] for v in PORTABLE.values()} | declared)
        check("PowerShell-only parameters are all declared", not stray_ps, stray_ps)

        # Each shell installer documents the switches it takes, in its own header. A switch that
        # exists but is not documented is how a user learns about it by reading the source.
        for label, path, flags in (("POSIX", SH, sh), ("macOS", MAC, mac)):
            doc = documented_flags(path)
            undocumented = sorted(f for f in flags
                                  if f not in doc and f not in ("--help",))
            check("%s switches are documented in the installer header" % label,
                  not undocumented, undocumented)

        # The paragraph that tells a Linux/macOS reader which switches exist must not name one
        # that NEITHER POSIX installer takes: measured 2026-09-27 it advertised `--mode
        # user|system` to macOS, which has one kind of install and exits 2 on that flag.
        readme = (BASE / "README.md").read_text(encoding="utf-8")
        m = re.search(r"Every question has a switch:(.*?)\n\n", readme, re.S)
        named = set(re.findall(r"`(--[a-z][a-z-]+)", m.group(1))) if m else set()
        check("the README's Linux/macOS switch sentence names switches", bool(named), named)
        for flag in sorted(named):
            check("the README's %s exists on Linux and macOS" % flag,
                  flag in sh and flag in mac, sorted(named))
        linux_half = re.search(r"\*\*Linux\.\*\*(.*?)\*\*macOS\*\*", readme, re.S)
        check("the README names --mode on the platform that has it",
              bool(linux_half) and "--mode" in linux_half.group(1),
              linux_half.group(1)[:80] if linux_half else "no **Linux.** half")
        mac_half = readme.split("**macOS**", 1)[1].split("Every question has a switch:")[0] \
            if "**macOS**" in readme else ""
        check("the README says plainly that macOS has no --mode",
              "no `--mode`" in mac_half, mac_half[:120])

        # -------------------------------------------- the launchd domain names the USER
        # A-2026-10-08-176/-177: every domain was `gui/$(id -u)`, which under sudo is gui/0,
        # so bootout missed the user's job (its plist was deleted while the agent stayed
        # loaded and KeepAlive-respawned) and bootstrap loaded a fresh install into the
        # wrong session.
        _mac_src = MAC.read_text(encoding="utf-8")
        # Code only: the file's own comment quotes the OLD spelling while explaining it.
        _mac_code = "\n".join(l for l in _mac_src.splitlines()
                              if not l.lstrip().startswith("#"))
        _domains = re.findall(r"gui/\$\(id -u[^)]*\)", _mac_code)
        check("every launchd domain is the INVOKING user's uid, never gui/0 under sudo",
              _domains and all(d == 'gui/$(id -u "$RUN_USER")' for d in _domains), _domains)

        print()
        if FAILS:
            print("%d FAILED: %s" % (len(FAILS), ", ".join(FAILS)))
            return 1
        print("all installer-parity checks passed")
        return 0
    return main()


def main():
    rc = 0
    for name, fn in (("test_update_notes", _suite_test_update_notes), ("test_installer_parity", _suite_test_installer_parity)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
