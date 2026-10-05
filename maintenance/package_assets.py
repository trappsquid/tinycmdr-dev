"""The asset set the page's routes serve, derived from the code - ONE derivation.

The page's files are named in three places that must agree: the route handlers in
tinycmdr.py (BASE_DIR / "assets" / ...), the WEB_FONTS map, and the stylesheet's
@font-face urls. They are DERIVED here, never listed. The new page design added
assets/webui.css and the cinzel-600 face to the package, and four separate hand lists
each missed part of it - build-package.py's SHIP, WEB_FONTS itself, the three installers'
copy lists, and the suites' staging list - so every 1.0.68/1.0.69 package served
/page.css as a 404, every fresh install from any installer landed without a stylesheet,
and the suites could not see either because they graded the source tree, where the file
exists (measured on a fresh install, 2026-10-04).

Callers, so a fifth list never appears:
  * tests/test_webui_page.py        - the package manifest (SHIP) must cover this set
  * tests/test_installer_unix.py    - the INSTALLED tree must carry it
  * maintenance/check-package-assets.py - the built archives must contain it
"""
import ast
import re
from pathlib import Path


def served_assets(root):
    """(assets, stray) for a source tree.

    assets: repo-relative paths the page's routes serve - the literal asset paths the
    handlers build, every WEB_FONTS value, every bundled .woff2 (a font in the folder
    is a font the page may be told to serve), and the stylesheet's own font refs.
    stray:  font files the stylesheet asks for that WEB_FONTS does not serve - each one
    is a 404 waiting for a browser (cinzel-600 was exactly this until 2026-10-04).
    """
    root = Path(root)
    src = (root / "tinycmdr.py").read_text(encoding="utf-8")
    assets = {f"assets/{m}" for m in re.findall(
        r'BASE_DIR\s*/\s*"assets"\s*/\s*"([^"]+)"(?!\s*/)', src)}
    fonts = {}
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == "WEB_FONTS"):
            fonts = ast.literal_eval(node.value)
    css = (root / "assets" / "webui.css").read_text(encoding="utf-8")
    refs = set(re.findall(r"url\(/fonts/([^)]+)\)", css))
    assets |= {f"assets/fonts/{v}" for v in fonts.values()}
    assets |= {f"assets/fonts/{p.name}"
               for p in sorted((root / "assets" / "fonts").glob("*.woff2"))}
    return assets, sorted(refs - set(fonts.values()))
