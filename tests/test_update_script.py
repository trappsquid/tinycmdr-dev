"""update.sh is the ONE update path - and until now nothing executed it.

Every install, on every released version, reaches a new release by running this script: it is
the file whose whole reason to exist is that the installed copy's own updater may be old,
broken, or missing entirely. Suites graded its TEXT (test_verbs checks the strings it must
carry; test_contracts checks it is listed) and none ran it, so its behaviour was unmeasured.
The fetch half is hard-wired to GitHub, which is why: `TINYCMDR_UPDATE_URL` now overrides the
base URL the way install.sh's `TINYCMDR_URL` does, so this suite can serve a fake release over
loopback and grade the real script end to end.

What it grades first (run 23, A-2026-10-07-63): the page-token block appends to `.env`. `>>`
adds no newline of its own, so on an editor-saved `.env` (no trailing newline - the common
case after a manual edit) the key merged into the last line:
`TINYCMDR_MODEL_KEY=abcTINYCMDR_WEB_TOKEN=...` - the model key corrupted, the token invisible
to the script's own `^TINYCMDR_WEB_TOKEN=` guard (so every later update minted another) and the
page never starting. tinycmdr.py's `_env_set` does this right; the standalone updaters are its
twins.

    python tests/test_update_script.py
"""
import hashlib
import http.server
import json
import os
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
# TINYCMDR_UPDATE_SH points this suite at another copy of the script, which is how the
# pre-fix behaviour is graded (the fix lives in the script, not in a build).
SCRIPT = Path(os.environ.get("TINYCMDR_UPDATE_SH") or (BASE / "update.sh"))
FAILS = []


def check(cond, what, detail=""):
    print(("ok   " if cond else "FAIL ") + what + ("" if cond else "  <- %s" % (detail,)))
    if not cond:
        FAILS.append(what)


def fake_release(rel, version):
    """A release directory holding this platform's asset and its SHA256SUMS."""
    sysname = os.uname().sysname
    if sysname == "Darwin":
        asset = "tinycmdr-macos.zip"
    else:
        asset = "tinycmdr-linux.tar.gz"
    pkg = rel / "pkg" / ("tinycmdr-%s" % version)
    pkg.mkdir(parents=True)
    (pkg / "tinycmdr.py").write_text(
        'VERSION = "%s"\n' % version
        + '"""a fake build, for grading update.sh"""\n'
        + "print('hello from %s')\n" % version, encoding="utf-8")
    if asset.endswith(".zip"):
        with zipfile.ZipFile(rel / asset, "w") as z:
            z.write(pkg / "tinycmdr.py", "tinycmdr-%s/tinycmdr.py" % version)
    else:
        import tarfile
        with tarfile.open(rel / asset, "w:gz") as t:
            t.add(pkg / "tinycmdr.py", arcname="tinycmdr-%s/tinycmdr.py" % version)
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
    if os.name != "posix":
        print("skip: update.sh is the unix/macOS updater; this suite runs it on POSIX")
        return 77
    if shutil.which("sh") is None:
        print("skip: no `sh` on PATH to run update.sh with")
        return 77

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
        # The state this finding is about: a hand-edited .env with NO trailing newline.
        (inst / ".env").write_text("TINYCMDR_MODEL_KEY=abc", encoding="utf-8")

        env = dict(os.environ)
        env["TINYCMDR_UPDATE_URL"] = url
        env["TINYCMDR_PYTHON"] = sys.executable
        env.pop("TINYCMDR_WEB_TOKEN", None)
        proc = subprocess.run(["sh", str(SCRIPT), str(inst)], cwd=str(work), env=env,
                              capture_output=True, text=True, timeout=300,
                              stdin=subprocess.DEVNULL)
        out = proc.stdout + proc.stderr
        check(proc.returncode == 0, "update.sh runs and exits 0", (proc.returncode, out[-300:]))
        check("9.9.9" in out and "1.0.0" in out,
              "it names the version it moved from and to", out[:200])
        check('VERSION = "9.9.9"' in (inst / "tinycmdr.py").read_text(encoding="utf-8"),
              "the install now runs the release build")
        check((inst / "notes.md").read_text(encoding="utf-8").startswith("HOST NOTES"),
              "a host-owned file is left alone")

        env_text = (inst / ".env").read_text(encoding="utf-8")
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
        # one number an operator uses to see the copy loop did anything (run 23,
        # A-2026-10-07-67).
        summary = [ln for ln in out.splitlines() if ln.startswith("update: 1.0.0 -> 9.9.9")]
        check(summary and "(1 file(s);" in summary[0],
              "the summary reports how many files it wrote", summary)

        # A second run must not append another token: the guard the merge defeated.
        proc2 = subprocess.run(["sh", str(SCRIPT), str(inst)], cwd=str(work), env=env,
                               capture_output=True, text=True, timeout=300,
                               stdin=subprocess.DEVNULL)
        again = [l for l in (inst / ".env").read_text(encoding="utf-8").splitlines()
                 if l.startswith("TINYCMDR_WEB_TOKEN=")]
        check(len(again) == 1,
              "a later update finds the token and does not mint another", again)
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
