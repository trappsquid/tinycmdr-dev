#!/usr/bin/env python3
"""Serve the BUILT package and load the page the way a browser does.

check-package-assets.py grades that the files are IN the archive; this grades that the
page a user loads FROM it answers: `/` comes back with no {{PLACEHOLDER}} left, every
asset the page references (markup srcs/hrefs, the stylesheet's url()s, the fonts it
declares) is 200 and non-empty, `/page.css` still carries `@font-face`, and the authed
API answers with the version the archive was built as. The class it closes: 1.0.68-1.0.70
shipped archives whose page served /page.css as a 404 (the stylesheet never made it into
the package) - nothing until now had ever loaded the page from the artifact, so only a
user on a fresh install could see it.

    python maintenance/check-package-page.py --dist dist

Exit: 0 = every archive's page answers; 2 = something did not. release.sh runs it beside
check-readme-assets.py and check-package-assets.py, after the packages are built.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "pkg-page-check-0123456789"


def server_python():
    """The interpreter to run the unpacked app with.

    The dev venv when there is one - the app imports requests at boot, and the python
    that runs this checker (release.sh's $PY) may be a bare interpreter without it: the
    1.0.74 cut failed on exactly that (ModuleNotFoundError: No module named 'requests',
    2026-10-04). An install runs from its own venv; this mirrors that.
    """
    venv = ROOT / "venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def unpack(archive, dest):
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            try:
                t.extractall(dest, filter="data")     # 3.12+: refuse odd members
            except TypeError:                         # 3.10/3.11 have no filter kwarg
                t.extractall(dest)
    tree = [p for p in dest.iterdir() if p.is_dir()]
    return (tree[0] if len(tree) == 1 else dest)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_port(port, deadline):
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def get(path, port, raw=False):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 headers={"Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
        return (r.status, body if raw else body.decode("utf-8", "replace"))


def page_refs(html, css):
    """Every asset path the loaded page asks for, from the page and the stylesheet."""
    refs = set(re.findall(r'(?:src|href)="(/[^"?#]+)', html))
    refs |= set(re.findall(r"url\((/[^)?]+)", css))
    return {r for r in refs if not r.startswith("/api/")}


def check_archive(archive):
    problems = []
    with tempfile.TemporaryDirectory(prefix="pkg-page-") as tmp:
        tree = unpack(archive, Path(tmp))
        port = free_port()
        (tree / "config.json").write_text(json.dumps({
            "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "probe"},
            "web": {"enabled": True, "host": "127.0.0.1", "port": port,
                    "token": TOKEN}}), encoding="utf-8")
        env = dict(os.environ, TINYCMDR_NO_BROWSER="1",
                   TINYCMDR_WEB_TOKEN=TOKEN)
        logfile = Path(tmp) / "server.log"
        with open(logfile, "wb") as _log:
            proc = subprocess.Popen(
                [server_python(), str(tree / "tinycmdr.py"), "--web", "--no-browser"],
                cwd=str(tree), env=env, stdout=_log, stderr=subprocess.STDOUT)
        try:
            # 60s, not 25: this runs straight after the package build on a loaded machine,
            # and a 25s first-sight deadline failed all three archives in the 1.0.74 cut
            # while the same archives passed seconds later (2026-10-04).
            if not wait_port(port, time.time() + 60):
                tail = logfile.read_text(encoding="utf-8", errors="replace")[-600:]
                return ["the server never answered on 127.0.0.1:%d - its output:\n%s"
                        % (port, tail)]
            status, html = get("/", port)
            if status != 200:
                problems.append("/ -> %s" % status)
            for tok in set(re.findall(r"\{\{[A-Z_]+\}\}", html)):
                problems.append("the page served the literal placeholder %s" % tok)
            status, css = get("/page.css", port)
            if status != 200 or not css.strip():
                problems.append("/page.css -> %s (%d bytes)" % (status, len(css)))
            if "@font-face" not in css:
                problems.append("/page.css carries no @font-face")
            for tok in set(re.findall(r"\{\{[A-Z_]+\}\}", css)):
                problems.append("the stylesheet served the literal placeholder %s" % tok)
            for ref in sorted(page_refs(html, css)):
                try:
                    st, body = get(ref, port, raw=True)
                except urllib.error.HTTPError as e:
                    problems.append("%s -> %s" % (ref, e.code))
                    continue
                if st != 200 or not body:
                    problems.append("%s -> %s (%d bytes)" % (ref, st, len(body)))
            api = json.loads(get("/api/sessions", port)[1])
            built = re.search(r'^VERSION = "(.*?)"',
                              (tree / "tinycmdr.py").read_text(encoding="utf-8"),
                              re.M).group(1)
            if api.get("version") != built:
                problems.append("the API reports version %r, the archive is %r"
                                % (api.get("version"), built))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    ap.add_argument("--version", default="")
    a = ap.parse_args()
    ver = a.version
    if not ver:
        m = re.search(r'^VERSION = "(.*?)"',
                      (ROOT / "tinycmdr.py").read_text(encoding="utf-8"), re.M)
        ver = m.group(1) if m else ""
    want = "tinycmdr-%s-" % ver if ver else "tinycmdr-"
    dist = Path(a.dist)
    archives = sorted([p for p in dist.glob("*.zip") if p.name.startswith(want)]
                      + [p for p in dist.glob("*.tar.gz") if p.name.startswith(want)])
    if not archives:
        print("no archives in %s (build first: maintenance/build-package.py --public "
              "--macos)" % dist)
        return 2
    failed = 0
    for archive in archives:
        problems = check_archive(archive)
        if problems:
            failed += 1
            print("FAIL %s:" % archive.name)
            for p in problems:
                print("  - " + p)
        else:
            print("ok   %s serves its page and every asset it references" % archive.name)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
