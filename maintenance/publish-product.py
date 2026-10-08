#!/usr/bin/env python3
"""Build the public product tree from this dev tree, from ONE manifest.

The two repositories exist for one reason: what a user downloads should be the product and nothing
else. That is a shape, not a rule about any single file - and the failure mode of a shape is drift,
so the split is written down here, in one place, and graded (tests/test_maintenance_kit.py refuses a
file or a suite that is in neither list).

    python3 maintenance/publish-product.py            # build into a temp tree and report the diff
    python3 maintenance/publish-product.py --write    # build into ../tinycmdr-product
    python3 maintenance/publish-product.py --push     # ...and commit, tag and push it

What ships is the runtime plus what a reader of the product needs to install it, run it and check
it: the code, the installers, the examples, the assets, the user docs, and the test suites that
grade the product. What stays here is the machinery around it - the ledger, the process docs, the
release tooling, the leak gate and its inventory - and the suites that can only run beside those.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "maintenance" / "product-manifest.json"


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def ship_paths(manifest: dict) -> list[str]:
    """The packager's own SHIP list plus the files a checkout also wants.

    build-package.py decides what an *install archive* carries; this adds the doors a reader of a
    checkout expects (the installers, the tests, the user docs, the licence).
    """
    text = (ROOT / "maintenance" / "build-package.py").read_text(encoding="utf-8")
    ship = re.findall(r'"([^"]+)"', text.split("SHIP = [")[1].split("\n]")[0])
    ship = [s for s in ship if " " not in s and s != "field-notes.md"]
    return ship + list(manifest["extra"])


def public_tests(manifest: dict) -> list[Path]:
    private = set(manifest["private_suites"])
    keep = []
    for path in sorted((ROOT / "tests").glob("*")):
        if path.is_dir():
            continue
        if path.suffix == ".py" and path.name.startswith("test_") and path.stem in private:
            continue
        keep.append(path)
    return keep


def build(dest: Path, manifest: dict) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    missing = []
    for rel in ship_paths(manifest):
        src = ROOT / rel
        if not src.exists():
            missing.append(rel)
            continue
        target = dest / rel
        if src.is_dir():
            shutil.copytree(src, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    (dest / "tests").mkdir(exist_ok=True)
    for src in public_tests(manifest):
        shutil.copy2(src, dest / "tests" / src.name)
    # The measured blocks in the user doc are rendered from the tree they describe.
    tool = dest / "tests" / "_measured_block.py"
    shutil.copy2(ROOT / "maintenance" / "measured-block.py", tool)
    subprocess.run([sys.executable, str(tool), "--write"], cwd=str(dest), check=False,
                   capture_output=True)
    tool.unlink()
    for rel in manifest["product_only"]:
        src_rel, _, dst_rel = rel.partition(":")
        src = ROOT / src_rel
        if not src.exists():
            missing.append(src_rel)
            continue
        target = dest / (dst_rel or src_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    if missing:
        print("not in this tree (per-host or seeded, fine): %s" % ", ".join(missing))
    print("built %s: %d files, %d suites"
          % (dest, sum(1 for p in dest.rglob("*") if p.is_file()),
             len(list((dest / "tests").glob("test_*.py")))))


def main() -> int:
    ap = argparse.ArgumentParser(description="build the public product tree from this dev tree")
    ap.add_argument("--write", metavar="DIR", nargs="?", const=str(ROOT.parent / "tinycmdr-product"),
                    help="build into a directory (default ../tinycmdr-product)")
    ap.add_argument("--push", action="store_true", help="commit, tag and push that tree")
    ap.add_argument("--remote", default="public", help="remote to push to (default: public)")
    args = ap.parse_args()
    manifest = load_manifest()
    if not args.write and not args.push:
        with tempfile.TemporaryDirectory(prefix="product-") as tmp:
            build(Path(tmp) / "product", manifest)
        return 0
    dest = Path(args.write or str(ROOT.parent / "tinycmdr-product"))
    build(dest, manifest)
    if args.push:
        version = re.search(r'^VERSION\s*=\s*"([^"]+)"',
                            (ROOT / "tinycmdr.py").read_text(encoding="utf-8"), re.M).group(1)
        tag = "v%s" % version
        for cmd in (["git", "init", "-q", "-b", "main"],
                    ["git", "add", "-A"],
                    ["git", "commit", "-q", "-m", "tinycmdr %s" % tag],
                    ["git", "tag", "-f", tag],
                    ["git", "remote", "add", "origin",
                     subprocess.run(["git", "remote", "get-url", args.remote], cwd=str(ROOT),
                                    capture_output=True, text=True).stdout.strip()]):
            subprocess.run(cmd, cwd=str(dest), check=False)
        print("push it with: git -C %s push --force origin main && git -C %s push --force origin %s"
              % (dest, dest, tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
