#!/usr/bin/env python3
"""The BUILT package must carry every asset the page's routes serve.

The freeze test grades the manifest (SHIP); the installer suite grades the INSTALLED
tree; this grades the ARTIFACT - the zip/tarball a user downloads - which is where the
drift actually reached people: SHIP was missing assets/webui.css and cinzel-600 from
1.0.68 to 1.0.70, so every package of those tags served /page.css as a 404. The asset
set is derived (maintenance/package_assets.py), never listed here.

    python maintenance/check-package-assets.py --dist dist

Exit: 0 = every built archive carries the set (and the stylesheet's bytes match the
tree); 2 = something is missing or stale. release.sh runs this beside
check-readme-assets.py, after the packages are built.
"""
import argparse
import re
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "maintenance"))
from package_assets import served_assets  # noqa: E402


def archive_members(path):
    """(names, blobs) for a zip or tar.gz: repo-relative names, top dir stripped."""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            blobs = {n: z.read(n) for n in names}
    else:
        with tarfile.open(path) as t:
            names = t.getnames()
            blobs = {m.name: t.extractfile(m).read() for m in t.getmembers()
                     if m.isfile()}
    strip = {Path(n).parts[0] for n in names if n.strip("/")}
    top = next(iter(strip)) if len(strip) == 1 else ""
    out_names, out_blobs = set(), {}
    for n in names:
        rel = n[len(top) + 1:] if top and n.startswith(top + "/") else n
        out_names.add(rel)
        if n in blobs:
            out_blobs[rel] = blobs[n]
    return out_names, out_blobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    ap.add_argument("--version", default="", help="grade only this version's archives")
    a = ap.parse_args()

    assets, stray = served_assets(ROOT)
    if stray:
        print("the stylesheet asks for fonts WEB_FONTS does not serve: %s" % ", ".join(stray))
        return 2
    dist = Path(a.dist)
    ver = a.version
    if not ver:
        m = re.search(r'^VERSION = "(.*?)"',
                      (ROOT / "tinycmdr.py").read_text(encoding="utf-8"), re.M)
        ver = m.group(1) if m else ""
    want = f"tinycmdr-{ver}-" if ver else "tinycmdr-"
    archives = sorted([p for p in dist.glob("*.zip") if p.name.startswith(want)]
                      + [p for p in dist.glob("*.tar.gz") if p.name.startswith(want)])
    if not archives:
        print("no archives in %s (build first: maintenance/build-package.py --public --macos)" % dist)
        return 2

    failed = 0
    for path in archives:
        names, blobs = archive_members(path)
        missing = sorted(x for x in assets if x not in names)
        stale = []
        for rel in sorted(assets):
            if rel in blobs and (ROOT / rel).exists():
                if blobs[rel] != (ROOT / rel).read_bytes():
                    stale.append(rel)
        if missing:
            failed += 1
            print("FAIL %s misses %d asset(s): %s" % (path.name, len(missing),
                                                      ", ".join(missing)))
        elif stale:
            failed += 1
            print("FAIL %s carries stale bytes for: %s" % (path.name, ", ".join(stale)))
        else:
            print("ok   %s carries all %d asset(s), byte-identical to the tree"
                  % (path.name, len(assets)))
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
