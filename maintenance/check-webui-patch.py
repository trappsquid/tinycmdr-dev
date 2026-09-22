"""Check that the web page and the web server still agree with each other.

Run:  python maintenance/check-webui-patch.py [path]

This is the contract check for the page half of the web UI. The Python suites
drive the HTTP endpoints; tests/test_webui_page.py runs the page's JavaScript in
Node. Neither one notices the failure this catches: a page that calls an endpoint
the server does not route (or sends a header name the server does not read). A
browser shows that as a silent 404 and a dead button, and every suite stays green.

It is written against the CONTRACT on purpose - the paths the page fetches, the
names both halves agree on - not against implementation markers. The previous
version listed exact strings from one generation of the renderer; when that
renderer was replaced the script went from meaningless-green to
permanently-red, which is worse than not having it.

Exit 0 only when every check passes. Skips (exit 0) for a build with no web
layer, and skips the JavaScript parse when node is not installed.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

src_path = Path(sys.argv[1] if len(sys.argv) > 1 else "tinycmdr.py")
src = src_path.read_text(encoding="utf-8")

if 'WEB_PAGE = """' not in src:
    print(f"{src_path}: no web layer in this build - nothing to check")
    sys.exit(0)

start = src.index('WEB_PAGE = """') + len('WEB_PAGE = """')
end = src.index('"""', start)
page = src[start:end]
fail = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fail.append(what)


# -- what the page asks the server for, and what the server routes ----------
page_paths = set()
for raw in re.findall(r"(?:fetch|post)\(\s*'([^']+)'", page):
    page_paths.add(raw.split("?")[0])
page_paths |= set(re.findall(r"href=[\"']?(/[^\"' >]+)", page))
page_paths |= set(re.findall(r"src=[\"']?(/[^\"' >]+)", page))
page_paths.discard("/")

routed = set(re.findall(r'self\.path\.startswith\("(/[^"]+)"\)', src))
routed |= set(re.findall(r'self\.path == "(/[^"]*)"', src))
# a route written as "/api/session?" and a call written as "/api/session?key=x"
# are the same path; the query tail is not part of the contract
routed |= {p.rstrip("?=") for p in list(routed)}

missing = sorted(p for p in page_paths if p not in routed)
check(not missing, f"every path the page calls is routed by the server ({missing})")

# -- the handshake: one header name, written once ---------------------------
sent = set(re.findall(r"'(X-Tinycmdr-[A-Za-z-]+)'", page))
read = set(re.findall(r'headers\.get\("(X-Tinycmdr-[A-Za-z-]+)"\)', src))
check(bool(sent) and sent == read,
      f"the token header name matches on both sides ({sorted(sent)} vs {sorted(read)})")

# -- the page is stamped with its version, in exactly one place -------------
check(page.count("{{VERSION}}") == 1,
      f"the page carries one version placeholder ({page.count('{{VERSION}}')})")
check(src.count('WEB_PAGE.replace("{{VERSION}}", VERSION)') == 1,
      "the server substitutes it when serving the page")

# -- one JSON document per request (a stray second reply broke every poll) --
n_doc = src.count('self._json({"ok": True, "version": VERSION})')
check(n_doc == 1, f"exactly one health reply document ({n_doc})")

# -- the page parses -------------------------------------------------------
node = shutil.which("node") or shutil.which("node.exe")
if not node:
    print("skip the page JavaScript does not parse (node not installed)")
else:
    script = re.search(r"<script>(.*)</script>", page, re.S)
    check(script is not None, "the page has a script block")
    if script:
        with tempfile.TemporaryDirectory() as td:
            js = Path(td) / "page.js"
            js.write_text(script.group(1), encoding="utf-8")
            proc = subprocess.run([node, "--check", str(js)],
                                  capture_output=True, text=True)
        check(proc.returncode == 0,
              f"the page JavaScript parses ({(proc.stderr or '').strip()[:200]})")

print("web UI page/server contract holds" if not fail else f"MISSING: {fail}")
sys.exit(1 if fail else 0)
