#!/usr/bin/env python3
"""The zip's file TYPES, proved by extracting it the way Finder does.

    python maintenance/check-package-modes.py

`external_attr = mode << 16` carries permission bits only. macOS Archive Utility
(`ditto -x -k`, what a double-click on a .zip runs) then extracts every entry as
-rw-r--r-- regardless of what the build recorded, so INSTALL-MACOS.command,
UNINSTALL-MACOS.command and the extensionless `tinycmdr` launcher landed
non-executable for exactly the readers with no terminal to chmod them -
measured 2026-09-26 on the published tinycmdr-macos.zip. Plain `unzip`, which is
what the one-line curl door uses, rebuilds 0755 from the permission bits, so the
door the developers used never saw it.

So this check does not read external_attr back. It builds the macOS zip with the
shipped writer, extracts it with ditto into a temp folder, and stats the files
that have to be runnable. It also asserts SHIP's maintenance/ entries equal
ALLOWED_MAINTENANCE (a permitted-but-absent file is how the package shipped
without maintenance/restart-tinycmdr-macos.sh, the day-two command the installer
prints).

Exit 0 when every door comes out -rwxr-xr-x, 1 with a line per failure, and 3
where ditto does not exist (macOS-only tool: the archive is still verified for
content, only the extraction half cannot run here).
"""
import importlib.util
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAINTENANCE = ROOT / "maintenance"
# Every file a double-click or a PATH shim has to be able to execute.
MUST_BE_RUNNABLE = ("INSTALL-MACOS.command", "UNINSTALL-MACOS.command", "tinycmdr")


def load_build_package():
    """build-package.py as a module.

    It refuses to import without maintenance/private_rules.py (the fleet inventory,
    gitignored), and a checkout that has never cut a fleet package does not carry one.
    Give the import the EXAMPLE under a temp name rather than writing into the tree.
    """
    rules = MAINTENANCE / "private_rules.py"
    tmp_rules = None
    if not rules.exists():
        tmp_rules = pathlib.Path(tempfile.mkdtemp(prefix="tinycmdr-rules-"))
        shutil.copy2(MAINTENANCE / "private_rules.example.py", tmp_rules / "private_rules.py")
        sys.path.insert(0, str(tmp_rules))
    sys.path.insert(0, str(MAINTENANCE))
    spec = importlib.util.spec_from_file_location("build_package_modes", MAINTENANCE / "build-package.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_package_modes"] = mod
    spec.loader.exec_module(mod)
    return mod, tmp_rules


def main():
    bp, tmp_rules = load_build_package()
    fails = []
    try:
        problems = bp.maintenance_drift()
        if problems:
            fails += problems
        else:
            shipped = sorted(r.split("/", 1)[1] for r in bp.SHIP if r.startswith("maintenance/"))
            print(f"ok   SHIP's maintenance/ entries == ALLOWED_MAINTENANCE ({len(shipped)}): "
                  + ", ".join(shipped))

        work = pathlib.Path(tempfile.mkdtemp(prefix="tinycmdr-modes-"))
        try:
            ver = bp.version()
            stage = work / f"tinycmdr-{ver}"
            bp.stage(stage)
            zip_path = work / f"tinycmdr-{ver}-macos.zip"
            bp.write_zip(stage, zip_path)
            print(f"     built {zip_path.name} ({zip_path.stat().st_size / 1024:.0f} KB) "
                  f"from {sum(1 for _ in stage.rglob('*') if _.is_file())} staged files")

            # The day-two helper must be IN the package, not merely permitted by a list.
            member = f"tinycmdr-{ver}/maintenance/restart-tinycmdr-macos.sh"
            import zipfile
            with zipfile.ZipFile(zip_path) as z:
                names = set(z.namelist())
            if member in names:
                print("ok   the package carries maintenance/restart-tinycmdr-macos.sh")
            else:
                fails.append(f"the package is missing {member}")

            if not shutil.which("ditto"):
                print("SKIP the extraction half: ditto is macOS-only (this is "
                      f"{os.uname().sysname}); content checks above still ran")
                return finish(fails) or 3

            dest = work / "extracted"
            dest.mkdir()
            pull = subprocess.run(["ditto", "-x", "-k", str(zip_path), str(dest)],
                                  capture_output=True, text=True)
            if pull.returncode != 0:
                fails.append(f"ditto -x -k failed ({pull.returncode}): {pull.stderr.strip()}")
                return finish(fails)
            for rel in MUST_BE_RUNNABLE:
                path = dest / f"tinycmdr-{ver}" / rel
                if not path.exists():
                    fails.append(f"{rel} is not in the extracted archive")
                    continue
                mode = stat.S_IMODE(path.stat().st_mode)
                shown = stat.filemode(path.stat().st_mode)
                if mode == 0o755:
                    print(f"ok   ditto extracted {rel} as {shown}")
                else:
                    fails.append(f"ditto extracted {rel} as {shown} "
                                 f"({oct(mode)}), not -rwxr-xr-x (0755)")
            return finish(fails)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    finally:
        if tmp_rules:
            shutil.rmtree(tmp_rules, ignore_errors=True)
            sys.path.remove(str(tmp_rules))


def finish(fails):
    if not fails:
        print("every door the package ships is executable after a Finder extraction")
        return 0
    print()
    for f in fails:
        print(f"FAIL {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
