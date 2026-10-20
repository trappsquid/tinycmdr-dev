"""The published updaters are the ONE update path - and this suite executes them.

Every install, on every released version, reaches a new release by running update.sh (or
update.ps1 on Windows): the file whose whole reason to exist is that the installed copy's
own updater may be old, broken, or missing entirely. Suites graded their TEXT (test_verbs
checks the strings they must carry; test_contracts checks they are listed) and none ran
them, so their behaviour was unmeasured. The fetch half is hard-wired to GitHub, which is
why `TINYCMDR_UPDATE_URL` overrides the base URL so this suite can serve a fake release
over loopback and grade the real script end to end - the POSIX twin on macOS/Linux, the
PowerShell twin on Windows, so a branch that only one platform runs is still measured.

What it grades first: the page-token block appends to `.env`. `>>` (and Add-Content) adds
no newline of its own, so on an editor-saved `.env` (no trailing newline) the key merged
into the last line: `TINYCMDR_MODEL_KEY=abcTINYCMDR_WEB_TOKEN=...` - the model key
corrupted, the token invisible to the script's own guard and the page never starting.

It also grades the starter-tool refresh: tools/ is host-owned, but a copy whose bytes are
still a stock version listed in the package's tools-stock.sha256 was never edited, so a
release fixes it in place; an edited copy matches nothing and is left alone.

    python tests/test_update_script.py
"""
import hashlib
import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
# TINYCMDR_UPDATE_SH / _PS1 point this suite at another copy of the script, which is how
# the pre-fix behaviour is graded (the fix lives in the script, not in a build).
UNIX_SCRIPT = Path(os.environ.get("TINYCMDR_UPDATE_SH") or (BASE / "update.sh"))
WIN_SCRIPT = Path(os.environ.get("TINYCMDR_UPDATE_PS1") or (BASE / "update.ps1"))
FAILS = []


def check(cond, what, detail=""):
    print(("ok   " if cond else "FAIL ") + what + ("" if cond else "  <- %s" % (detail,)))
    if not cond:
        FAILS.append(what)


def read(path):
    """File text, or "" when the updater never wrote it: a missing file is a failed
    check with evidence, never a traceback that hides the first failure."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# The package's starter tools, and the manifest that names every stock version. The
# installed copies in main() are OLD STOCK and MY EDIT: the first must be refreshed, the
# second must survive untouched.
PACKAGE_TOOLS = {"process.py": "NEW STOCK", "mine.py": "NEW STOCK", "new.py": "SEED"}
STOCK_LINES = (
    "%s  tools/process.py\n" % _sha("OLD STOCK")
    + "%s  tools/process.py\n" % _sha("NEW STOCK")
    + "%s  tools/mine.py\n" % _sha("NEW STOCK")
    + "%s  tools/new.py\n" % _sha("SEED")
)


def fake_release(rel, version):
    """A release directory holding this platform's asset and its SHA256SUMS."""
    if os.name == "nt":
        asset = "tinycmdr-win.zip"
    elif os.uname().sysname == "Darwin":
        asset = "tinycmdr-macos.zip"
    else:
        asset = "tinycmdr-linux.tar.gz"
    pkg = rel / "pkg" / ("tinycmdr-%s" % version)
    pkg.mkdir(parents=True)
    (pkg / "tinycmdr.py").write_text(
        'VERSION = "%s"\n' % version
        + '"""a fake build, for grading the published updaters"""\n'
        + "print('hello from %s')\n" % version, encoding="utf-8")
    # A release that moved the dependency bounds: both updaters must say how to reconcile
    # the venv, the way the Python update verb does.
    (pkg / "requirements.txt").write_text("requests>=2.32,<3\n", encoding="utf-8")
    (pkg / "tools").mkdir()
    for name, text in PACKAGE_TOOLS.items():
        (pkg / "tools" / name).write_text(text, encoding="utf-8")
    (pkg / "tools-stock.sha256").write_text(STOCK_LINES, encoding="utf-8")
    if asset.endswith(".zip"):
        with zipfile.ZipFile(rel / asset, "w") as z:
            for f in sorted(pkg.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(rel / "pkg").as_posix())
    else:
        import tarfile
        with tarfile.open(rel / asset, "w:gz") as t:
            for f in sorted(pkg.rglob("*")):
                if f.is_file():
                    t.add(f, arcname=f.relative_to(rel / "pkg").as_posix())
    digest = hashlib.sha256((rel / asset).read_bytes()).hexdigest()
    (rel / "SHA256SUMS").write_text("%s  %s\n" % (digest, asset), encoding="utf-8")
    return asset


class _Handler(http.server.BaseHTTPRequestHandler):
    root = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        f = Path(self.root) / self.path.rsplit("/", 1)[-1]
        if not f.exists():
            self.send_error(404)
            return
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    if os.name == "nt":
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell is None:
            print("skip: no PowerShell on PATH to run update.ps1 with")
            return 77
        script = WIN_SCRIPT
    else:
        shell = shutil.which("sh")
        if shell is None:
            print("skip: no `sh` on PATH to run update.sh with")
            return 77
        script = UNIX_SCRIPT
    print("updater under test: %s (%s)" % (script, shell))

    work = Path(tempfile.mkdtemp(prefix="tc-update-script-"))
    srv = None
    try:
        rel = work / "release"
        rel.mkdir()
        asset = fake_release(rel, "9.9.9")
        _Handler.root = rel
        srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d" % srv.server_address[1]

        inst = work / "install"
        inst.mkdir()
        (inst / "tinycmdr.py").write_text('VERSION = "1.0.0"\n', encoding="utf-8")
        (inst / "config.json").write_text(json.dumps({"llm": {}}), encoding="utf-8")
        (inst / "notes.md").write_text("HOST NOTES - an update must not touch this\n",
                                       encoding="utf-8")
        # A stock starter tool (the release carries a fix under it), the agent's own
        # edited tool, and a starter the install does not have yet.
        (inst / "tools").mkdir()
        (inst / "tools" / "process.py").write_text("OLD STOCK", encoding="utf-8")
        (inst / "tools" / "mine.py").write_text("MY EDIT", encoding="utf-8")
        # The state this finding is about: a hand-edited .env with NO trailing newline.
        (inst / ".env").write_text("TINYCMDR_MODEL_KEY=abc", encoding="utf-8")

        env = dict(os.environ)
        env["TINYCMDR_UPDATE_URL"] = url
        env["TINYCMDR_PYTHON"] = sys.executable
        env["TINYCMDR_NONINTERACTIVE"] = "1"
        env.pop("TINYCMDR_WEB_TOKEN", None)
        if os.name == "posix":
            # A-2026-10-11-16: the fetch must carry install.sh's bounds. The script's own
            # curl is observed through a wrapper on PATH that records its arguments and
            # hands them to the real one - the script cannot hang on a black-holed route
            # with the flags present, and this is what says they are.
            _curl = shutil.which("curl")
            _wget = shutil.which("wget")
            fetch_log = work / "fetch.log"
            wrap_dir = work / "wrap"
            wrap_dir.mkdir()
            if _curl:
                (wrap_dir / "curl").write_text(
                    '#!/bin/sh\nprintf "curl %s\\n" "$*" >> "%s"\nexec "%s" "$@"\n'
                    % ("%s", fetch_log, _curl), encoding="utf-8")
                (wrap_dir / "curl").chmod(0o755)
            elif _wget:
                (wrap_dir / "wget").write_text(
                    '#!/bin/sh\nprintf "wget %s\\n" "$*" >> "%s"\nexec "%s" "$@"\n'
                    % ("%s", fetch_log, _wget), encoding="utf-8")
                (wrap_dir / "wget").chmod(0o755)
            env["PATH"] = str(wrap_dir) + os.pathsep + env.get("PATH", "")

        def run_update(target):
            if os.name == "nt":
                cmd = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                       str(script), "-Dir", str(target)]
            else:
                cmd = [shell, str(script), str(target)]
            return subprocess.run(cmd, cwd=str(work), env=env, capture_output=True,
                                  text=True, timeout=300, stdin=subprocess.DEVNULL)

        proc = run_update(inst)
        out = proc.stdout + proc.stderr
        if os.name == "posix":
            _fetches = fetch_log.read_text(encoding="utf-8") if fetch_log.exists() else ""
            if _curl:
                check("--connect-timeout" in _fetches and "--retry 3" in _fetches,
                      "the fetch cannot hang with no clock (install.sh's bounds)",
                      _fetches)
            elif _wget:
                check("--timeout=15" in _fetches and "--tries=3" in _fetches,
                      "the fetch cannot hang with no clock (install.sh's bounds)",
                      _fetches)
        check(proc.returncode == 0, "the updater runs and exits 0",
              (proc.returncode, out[-300:]))
        check("9.9.9" in out and "1.0.0" in out,
              "it names the version it moved from and to", out[:200])
        check('VERSION = "9.9.9"' in read(inst / "tinycmdr.py"),
              "the install now runs the release build")
        check(read(inst / "notes.md").startswith("HOST NOTES"),
              "a host-owned file is left alone")

        # tools/ is host-owned, but the bytes still match a stock version the package
        # lists: a fix under an unedited starter now reaches an existing install.
        check(read(inst / "tools" / "process.py") == "NEW STOCK",
              "an unedited stock starter tool is refreshed from the release")
        check(read(inst / "tools" / "mine.py") == "MY EDIT",
              "...and the agent's edited tool is left exactly alone")
        check(read(inst / "tools" / "new.py") == "SEED",
              "...while a missing starter is seeded")
        check("refreshed unedited starter tool(s):" in out
              and "tools/process.py" in out,
              "...and the refresh is named, not silent", out[-400:])
        check((inst / "tools-stock.sha256").is_file(),
              "the stock manifest lands for the next update")

        env_text = read(inst / ".env")
        lines = env_text.splitlines()
        check("TINYCMDR_MODEL_KEY=abc" in lines,
              "the .env line that was there is still a line of its own", lines)
        tokens = [l for l in lines if l.startswith("TINYCMDR_WEB_TOKEN=")]
        check(len(tokens) == 1,
              "the page token landed as its own line, once", lines)
        check("abcTINYCMDR_WEB_TOKEN" not in env_text,
              "...and nothing merged into the line above", lines)
        check(tokens and env_text.endswith(tokens[0] + "\n"),
              "the file still ends with a newline", repr(env_text[-40:]))
        check(tokens and ("#token=" + tokens[0].split("=", 1)[1]) in out,
              "the link it prints is built from the token it wrote", out[-200:])
        # The summary's file count must be a real number: it was incremented inside a
        # `find | while` pipeline, so the parent shell never saw it and the unix updater
        # printed no count at all while update.ps1 printed "($written file(s); ...)" - the
        # one number an operator uses to see the copy loop did anything.
        summary = [ln for ln in out.splitlines() if ln.startswith("update: 1.0.0 -> 9.9.9")]
        check(bool(summary) and re.search(r"\(\d+ file\(s\);", summary[0]) is not None,
              "the summary reports how many files it wrote", summary)
        check("pip install -r" in out and "requirements.txt" in out,
              "a release that moved requirements.txt says how to reconcile the venv",
              out[-300:])

        # A second run must not append another token: the guard the merge defeated.
        proc2 = run_update(inst)
        out2 = proc2.stdout + proc2.stderr
        again = [l for l in read(inst / ".env").splitlines()
                 if l.startswith("TINYCMDR_WEB_TOKEN=")]
        check(len(again) == 1,
              "a later update finds the token and does not mint another", again)
        check("pip install -r" not in out2,
              "...and the same requirements stay quiet on a re-run", out2[-300:])

        # A-2026-10-11-17: an installed VERSION that cannot be read used to skip the
        # never-downgrade guard SILENTLY, on the one tree the updater exists to repair.
        # It still updates (0.0.0 is older than any release) and it now SAYS the guard had
        # nothing to compare.
        inst2 = work / "install2"
        inst2.mkdir()
        (inst2 / "tinycmdr.py").write_text("print('a build whose VERSION line is gone')\n",
                                           encoding="utf-8")
        (inst2 / "config.json").write_text("{}", encoding="utf-8")
        proc3 = run_update(inst2)
        out3 = proc3.stdout + proc3.stderr
        check(proc3.returncode == 0,
              "a damaged tree still updates (that IS the repair path)",
              (proc3.returncode, out3[-300:]))
        check("cannot be read" in out3 and "0.0.0" in out3,
              "an unreadable VERSION says the guard has nothing to compare",
              out3[-400:])
        check('VERSION = "9.9.9"' in read(inst2 / "tinycmdr.py"),
              "...and the damaged tree now runs the release build")

        if os.name == "posix":
            # A-2026-10-10-10: the sums line was matched with `grep " <asset>$"`, which
            # only sees sha256sum's TEXT mode (`<hash>  <name>`); the binary mode's
            # asterisk (`<hash> *<name>`) read as "no entry for <asset> - the release is
            # broken". The extractor is now field-based; this runs the same awk
            # expression the script runs against both spellings.
            script_text = UNIX_SCRIPT.read_text(encoding="utf-8")
            m = re.search(r'^WANT="\$\(awk -v a="\$ASSET" (.+?) "\$WORK/SHA256SUMS"\)"$',
                          script_text, re.M)
            check(m is not None, "the sums extractor is the field-based awk one",
                  [l for l in script_text.splitlines() if "SHA256SUMS" in l][:3])
            if m:
                prog = m.group(1)[1:-1]    # the shell's single quotes around the program
                sums = work / "SHA256SUMS.binary"
                sums.write_text("%s  text-mode.zip\n%s *binary-mode.zip\n"
                                % ("a" * 64, "b" * 64), encoding="utf-8")
                for asset, want in (("binary-mode.zip", "b" * 64),
                                    ("text-mode.zip", "a" * 64)):
                    got = subprocess.run(
                        ["awk", "-v", "a=%s" % asset, prog, str(sums)],
                        capture_output=True, text=True).stdout.strip()
                    check(got == want,
                          "the matcher reads %s (%s mode)"
                          % (asset, "binary" if asset.startswith("binary") else "text"),
                          got)
    finally:
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all update.sh checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
