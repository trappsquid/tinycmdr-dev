"""Build the enterprise (chatless CLI) release: a folder you can hand to anyone.

    python maintenance/build-cli-package.py [--keep-staging]

Reuses the public gate from build-package.py (the same banned-token, LAN-address
and secret scan the Mattermost release goes through), so nothing host-specific can
leak into the archive. Produces:

    dist/tinycmdr-cli-<version>-<platform>.zip        (Windows-shaped folder)
    dist/tinycmdr-cli-<version>-linux.tar.gz

The folder itself is the deliverable:

    tinycmdr-cli-<version>/tinycmdr.py            the agent (stdlib only)
    tinycmdr-cli-<version>/README.txt             how to run it
    tinycmdr-cli-<version>/config.example.json    the template to copy to config.json
    tinycmdr-cli-<version>/soul.md                the persona, editable; the build's
                                                  fallback is the same text
    tinycmdr-cli-<version>/atlas.md               the map of the machine, SHIPPED and
                                                  never generated; one per platform
    tinycmdr-cli-<version>/skills/                put runbooks here
    tinycmdr-cli-<version>/tools/                 it writes its own tools here
"""
import argparse
import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent   # the tree this script lives in
SRC = BASE / "tinycmdr-cli.py"
DIST = BASE / "dist"
README = BASE / "maintenance" / "cli-readme.txt"

# reuse the existing release gate rather than inventing a second one
spec = importlib.util.spec_from_file_location("build_package",
                                              BASE / "maintenance" / "build-package.py")
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)

SKILLS_NOTE = (
    "Drop skill folders here (each one is a folder with a SKILL.md inside).\n"
    "The agent lists them with /skills and reads the relevant one before it starts\n"
    "working in that area. This folder is empty on purpose: your own runbooks are\n"
    "the useful ones.\n")

# --- the atlas: shipped, never generated ---------------------------------------
# The fleet builds generate atlas.md ON each host, because one folder gets copied to six
# different machines and another box's map reads as authoritative and is wrong (that is why
# build-package.py lists atlas.md in FORBIDDEN_NAMES for the bot release). This release is
# the opposite case: one folder, unpacked by whoever downloads it, so the map is written
# once here and shipped. It carries only what is true of the platform and of this folder,
# never a hostname, an install path or a python version - the build refuses if a shipped
# atlas names a file the folder does not contain, and the public gate scans its text like
# every other shipped file.
ATLAS = {"win": BASE / "maintenance" / "atlas-cli-win.md",
         "linux": BASE / "maintenance" / "atlas-cli-linux.md"}

# Files the agent creates itself once there is work. The atlas may name them; the package
# does not contain them.
ATLAS_APPEARS_LATER = {"sessions", "notes.md", "tasks.json", "tasks.md", "tinycmdr.log",
                       "state.json"}


def _shipped_defaults(path):
    """Read DEFAULT_CONFIG out of the build so the example cannot name a number it does not
    ship. max_steps and max_minutes had both drifted (100/30 documented against 250/75
    shipped) before this was derived. Refuses rather than guess: a stale reference config is
    a wrong answer to a reader's first question.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        raise SystemExit("cannot read the build's defaults (%s: %s)" % (type(e).__name__, e))
    for n in tree.body:
        if (isinstance(n, ast.Assign) and n.targets
                and getattr(n.targets[0], "id", "") == "DEFAULT_CONFIG"):
            ns = {"socket": __import__("socket"), "os": os, "Path": Path, "str": str}
            exec(compile(ast.Module(body=[n], type_ignores=[]), "<defaults>", "exec"), ns)
            return ns["DEFAULT_CONFIG"]
    raise SystemExit("no DEFAULT_CONFIG in %s" % path)


def write_atlas(root: Path, platform: str):
    """Put this platform's atlas beside the agent as atlas.md."""
    src = ATLAS[platform]
    if not src.exists():
        raise SystemExit("no atlas source for %s (%s)" % (platform, src))
    (root / "atlas.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8",
                                   newline="\n")


def atlas_layout_problems(root: Path):
    """Problems with the shipped atlas, measured against the folder it ships in.

    The ## layout section is the model's map of where things are: a name that is not in
    the package sends it looking for a path that does not exist, which is the exact failure
    the atlas exists to prevent. One name per line, or several separated by spaces, then two
    spaces and the purpose.
    """
    problems = []
    path = root / "atlas.md"
    if not path.exists():
        return ["atlas.md is not in the folder"]
    section = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        if section != "layout" or not line:
            continue
        body = line[2:].strip() if line.startswith("- ") else line
        names = re.split(r"\s{2,}", body, maxsplit=1)[0].split()
        for name in names:
            clean = name.rstrip("/")
            if clean.endswith(":"):
                continue
            if (root / clean).exists() or clean in ATLAS_APPEARS_LATER:
                continue
            problems.append("%s names %r, which the package does not contain"
                            % (path.name, clean))
    if not problems and "## notes" not in path.read_text(encoding="utf-8"):
        problems.append("atlas.md has no ## notes section for the reader to extend")
    return problems


def version():
    text = SRC.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("VERSION = "):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no VERSION found in %s" % SRC)


def build_folder(root: Path, platform: str):
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, root / "tinycmdr.py")
    shutil.copy2(README, root / "README.txt")
    shutil.copy2(BASE / "soul.md", root / "soul.md")
    write_atlas(root, platform)
    # No .bat, no .ps1, no installer: environments that whitelist executables block
    # those outright, so the folder is the Python file, a README and a config example.
    # The agent is started with the operator's own Python (`python tinycmdr.py`).
    # config.example.json is deliberately ONE endpoint entry: the model service this
    # agent is allowed to reach, its key, the model id, and an optional CA bundle.
    # Every other setting has a default inside the build (DEFAULT_CONFIG), so nothing
    # else needs to appear here for the agent to run.
    _D = _shipped_defaults(SRC)
    _llm = _D.get("llm", {})
    _ag = _D.get("agent", {})
    example = {
        "_readme": ("NOT the config: this file is only the template. Copy it to config.json "
                    "(or rename it) and fill in the three fields under llm - base_url, "
                    "api_key, model. The agent itself never writes a config.json, so this "
                    "copy is the only way one happens. Nothing is created or checked when "
                    "it starts: opening it, or starting it with no config.json, leaves this "
                    "folder exactly as it is. atlas.md is the other file worth opening: a "
                    "shipped map of the machine, not state, and nothing ever regenerates it, "
                    "so edit it. Every setting not shown here has a default inside "
                    "tinycmdr.py (see DEFAULT_CONFIG) and is not needed to start."),
        "llm": {
            "base_url": "https://your-endpoint.invalid/v1",
            "api_key": "",
            "model": "",
            # Turns in one task, and the MESSAGES budget the harness keeps itself under.
            # max_context_tokens must fit the endpoint's real window per request, with room
            # for max_tokens and the tool schemas: ~100000 for a 128k endpoint, 900000 for a
            # 1M-token model.
            "max_turns": _llm.get("max_turns"),
            "max_context_tokens": _llm.get("max_context_tokens"),
            "max_tokens": _llm.get("max_tokens"),
            "final_max_tokens": _llm.get("final_max_tokens"),
        },
        "agent": {
            # These three decide how long a run may go, and all three force a report when
            # they are reached. Raise them for debugging or an investigation, lower them for
            # unattended work.
            "max_steps": _ag.get("max_steps"),
            "max_minutes": _ag.get("max_minutes"),
            # atlas.md sits beside this file: a map of the machine, shipped with the build
            # and never regenerated, so edit it to match the machine you are on. It rides
            # the first turn of a run (and again after a failure that looks like a wrong
            # path). Set atlas_enabled to false to keep it out of the prompt entirely, or
            # point atlas_file at your own file.
            "atlas_enabled": _ag.get("atlas_enabled"),
            "atlas_file": _ag.get("atlas_file"),
            "atlas_max_chars": _ag.get("atlas_max_chars"),
        },
    }
    # Self-check before the archive exists: every key this example documents must equal the
    # value the build ships. Deriving them above should make that true by construction; this
    # is what says so out loud, and it fails the build rather than shipping the disagreement.
    _drift = []
    for _sect, _kv in example.items():
        if _sect.startswith("_") or not isinstance(_kv, dict):
            continue
        _want = _D.get(_sect, {})
        for _k, _v in _kv.items():
            if _k in _want and _want[_k] != _v:
                _drift.append("%s.%s example=%r build=%r" % (_sect, _k, _v, _want[_k]))
    if _drift:
        raise SystemExit("refusing to write a reference config that disagrees with the "
                         "build: %s" % "; ".join(_drift))
    (root / "config.example.json").write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
    (root / "skills").mkdir(exist_ok=True)
    (root / "skills" / "PUT-YOUR-RUNBOOKS-HERE.txt").write_text(SKILLS_NOTE, encoding="utf-8")
    (root / "tools").mkdir(exist_ok=True)
    # The starter drop-in tools ride the console package too: this build has
    # the same loader and create_tool, and tools/README.md is the shapes doc
    # (it replaces the HOW-TOOLS-WORK note, which described a shape the
    # loader never had).
    for _starter in ("patch.py", "process.py", "README.md"):
        shutil.copy2(BASE / "tools" / _starter, root / "tools" / _starter)


def gate(root: Path, host_vals):
    """The same public gate the Mattermost release uses: secrets, LAN addresses,
    hostnames, banned host references."""
    problems = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pattern in bp.PUBLIC_FORBIDDEN:
            if isinstance(pattern, tuple):
                label, rx = pattern
            else:
                label, rx = pattern, pattern
            if hasattr(rx, "search"):
                for m in rx.finditer(text):
                    problems.append("%s: %s -> %r" % (rel, label, m.group(0)[:60]))
        for name, val in host_vals.items():
            if val and isinstance(val, str) and len(val) > 3 and val in text:
                problems.append("%s: host value %s -> %r" % (rel, name, val))
    return problems


# The package states Python 3.10 as its floor in the README and requirements, so a shipped
# .py that only PARSES on a newer interpreter is a broken promise: 2.3.0 went out with two
# suites using a nested same-quote f-string (3.12-only, PEP 701), found by unpacking the
# published archive on a 3.10 host. Parse every shipped .py against the floor, not the
# interpreter this build happens to run on.
PY_FLOOR = (3, 10)


def syntax_floor(folder):
    """Problems for any shipped .py that does not parse on the stated minimum."""
    problems, checked = [], 0
    for f in sorted(folder.rglob("*.py")):
        rel = f.relative_to(folder).as_posix()
        try:
            ast.parse(f.read_text(encoding="utf-8", errors="replace"),
                      filename=rel, feature_version=PY_FLOOR)
            checked += 1
        except SyntaxError as e:
            problems.append(f"{rel}:{e.lineno} does not parse on Python "
                            f"{PY_FLOOR[0]}.{PY_FLOOR[1]} ({e.msg})")
    print(f"syntax floor: {checked} .py file(s) parse on Python "
          f"{PY_FLOOR[0]}.{PY_FLOOR[1]}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-staging", action="store_true")
    args = ap.parse_args()
    ver = version()
    DIST.mkdir(exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix="fbcli-release-"))
    folder_name = "tinycmdr-cli-%s" % ver
    folders = {}
    for platform in ("win", "linux"):
        folder = stage_root / platform / folder_name
        build_folder(folder, platform)
        folders[platform] = folder
        print("staged %s (%s):" % (folder_name, platform))
        for p in sorted(folder.rglob("*")):
            print("   %-46s %s" % (p.relative_to(stage_root).as_posix(),
                                   ("%d B" % p.stat().st_size) if p.is_file() else "<dir>"))

    try:
        host_vals = bp.host_values()
    except Exception as e:                                  # noqa: BLE001
        host_vals = {}
        print("(host value scan unavailable: %s)" % e)
    bad = 0
    for platform, folder in folders.items():
        problems = gate(folder, host_vals) + atlas_layout_problems(folder)
        if problems:
            bad += len(problems)
            print("\nPUBLIC GATE FAILED (%s) — %d problem(s):" % (platform, len(problems)))
            for p in problems[:40]:
                print("   ", p)
        else:
            print("\npublic gate (%s): clean (%d files scanned)"
                  % (platform, sum(1 for _ in folder.rglob("*"))))
        floor = syntax_floor(folder)
        if floor:
            bad += len(floor)
            print("\nBUILD REFUSED (%s) - a shipped file does not parse on the stated floor:"
                  % platform)
            for pr in floor:
                print("  " + pr)
    if bad:
        return 1

    zip_path = DIST / ("tinycmdr-cli-%s-win-public.zip" % ver)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folders["win"].rglob("*")):
            if p.is_file():
                z.write(p, arcname="%s/%s"
                        % (folder_name, p.relative_to(folders["win"]).as_posix()))
    tar_path = DIST / ("tinycmdr-cli-%s-linux-public.tar.gz" % ver)
    with tarfile.open(tar_path, "w:gz") as t:
        t.add(folders["linux"], arcname=folder_name)

    # clean-unpack proof: both archives must carry a runnable agent, the right atlas for
    # their platform, and must create nothing when opened. The Linux suite run happens on a
    # Linux host (see the release checklist); everything else is measured here.
    check_dir = Path(tempfile.mkdtemp(prefix="fbcli-unpack-"))
    ok = True
    for platform, archive in (("win", zip_path), ("linux", tar_path)):
        out = check_dir / platform
        out.mkdir(parents=True, exist_ok=True)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(out)
        else:
            with tarfile.open(archive) as t:
                try:
                    t.extractall(out, filter="data")   # 3.12+: no metadata surprises
                except TypeError:                      # 3.10/3.11 packager
                    t.extractall(out)
        unpacked = out / folder_name
        same = (unpacked / "tinycmdr.py").read_bytes() == SRC.read_bytes()
        atlas_same = ((unpacked / "atlas.md").read_bytes()
                      == ATLAS[platform].read_bytes())
        before = sorted(p.relative_to(unpacked).as_posix() for p in unpacked.rglob("*"))
        r = subprocess.run([sys.executable, "tinycmdr.py", "--version"],
                           cwd=unpacked, capture_output=True, text=True, timeout=120)
        after = sorted(p.relative_to(unpacked).as_posix() for p in unpacked.rglob("*"))
        inert = before == after
        ok = ok and same and atlas_same and inert and r.returncode == 0
        print("\nclean unpack (%s): --version rc=%s %r" % (platform, r.returncode,
                                                           r.stdout.strip()))
        print("clean unpack (%s): tinycmdr.py identical to the source  %s" % (platform, same))
        print("clean unpack (%s): atlas.md is this platform's atlas   %s"
              % (platform, atlas_same))
        print("clean unpack (%s): opening it created nothing         %s%s"
              % (platform, inert,
                 "" if inert else "  (new: %s)" % sorted(set(after) - set(before))))

    r2 = subprocess.run([sys.executable, str(BASE / "tests" / "test_cli.py")],
                        capture_output=True, text=True, timeout=900,
                        env=dict(os.environ,
                                 TINYCMDR_SRC=str(check_dir / "win" / folder_name
                                                 / "tinycmdr.py")))
    tail = [l for l in r2.stdout.strip().splitlines() if "passed" in l]
    print("\nclean unpack: tests -> %s" % (tail[-1] if tail else "no summary"))

    print("\narchives:")
    for p in (zip_path, tar_path):
        print("   %-56s %8d B" % (p, p.stat().st_size))
    if not args.keep_staging:
        shutil.rmtree(stage_root, ignore_errors=True)
        shutil.rmtree(check_dir, ignore_errors=True)
    return 0 if (ok and r2.returncode == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
