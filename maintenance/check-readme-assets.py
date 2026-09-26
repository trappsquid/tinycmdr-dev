#!/usr/bin/env python3
"""Every download name in README.md must be real.

README.md points at STABLE names (tinycmdr-win.zip, tinycmdr-linux.tar.gz,
tinycmdr-macos.zip) under /releases/latest/download/, so every release has to
attach both its versioned build and the alias. This is the check that says
whether it did - run it before the tag and again after the release.

    python maintenance/check-readme-assets.py --dist dist
    python maintenance/check-readme-assets.py --tag v1.0.19

Exit 0 when every name resolves, 1 with one line per name that does not.
"""
import argparse, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
URL = re.compile(r"releases/latest/download/([A-Za-z0-9._-]+)")
# stable name -> the versioned file name the cut must have built for it
ALIASES = {
    "tinycmdr-win.zip": "tinycmdr-{v}-win.zip",
    "tinycmdr-linux.tar.gz": "tinycmdr-{v}-linux.tar.gz",
    "tinycmdr-macos.zip": "tinycmdr-{v}-macos.zip",
}


def _sha(path):
    pulled = subprocess.run(["sha256sum" if os.name != "nt" else "sha256sum", str(path)],
                            capture_output=True, text=True)
    return pulled.stdout.split()[0] if pulled.returncode == 0 else ""


def target_of(dist_dir, name):
    return pathlib.Path(dist_dir) / name


def version():
    text = (ROOT / "tinycmdr.py").read_text(encoding="utf-8", errors="replace")
    found = re.search(r'^VERSION = "(.*?)"', text, re.M)
    if not found:
        sys.exit("no VERSION line in tinycmdr.py")
    return found.group(1)


def readme_names():
    return sorted(set(URL.findall(README.read_text(encoding="utf-8", errors="replace"))))


def release_assets(tag):
    out = subprocess.run(["gh", "release", "view", tag, "--json", "assets"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("gh release view %s failed: %s" % (tag, out.stderr.strip()))
    return {a["name"] for a in json.loads(out.stdout)["assets"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", help="a build directory the names must exist in")
    ap.add_argument("--tag", help="a release tag the names must be attached to")
    args = ap.parse_args()
    if not (args.dist or args.tag):
        sys.exit("pass --dist <dir> or --tag <release>")

    ver = version()
    names = readme_names()
    if not names:
        sys.exit("README.md names no download")
    if args.dist:
        have = {p.name for p in pathlib.Path(args.dist).iterdir() if p.is_file()}
        where = "dist"
    else:
        have = release_assets(args.tag)
        where = args.tag

    bad = []
    print("README names %d download(s); version in the tree is %s" % (len(names), ver))
    for name in names:
        versioned = ALIASES.get(name, name)
        if "{v}" in versioned:
            versioned = versioned.format(v=ver)
            # A stable NAME is its own asset on the release: a GitHub latest URL resolves
            # an asset whose name matches and nothing else, so falling back to the
            # versioned file would report a broken download as fine (it did, 2026-09-26).
            if name not in have:
                print("  FAIL  %-26s no asset named %s (only the versioned %s is there)"
                      % (name, name, versioned))
                bad.append(name)
                continue
            if versioned in have and args.dist:
                same = _sha(target_of(args.dist, name)) == _sha(target_of(args.dist, versioned))
                if not same:
                    print("  FAIL  %-26s is not the same bytes as %s" % (name, versioned))
                    bad.append(name)
                    continue
            print("  ok    %-26s (stable name for %s)" % (name, versioned))
            continue
        if name in have:
            print("  ok    %-26s" % name)
        else:
            print("  FAIL  %-26s is not in %s" % (name, where))
            bad.append(name)
    if bad:
        sys.exit("%d README download name(s) do not resolve" % len(bad))
    print("all README download names resolve in %s" % where)


if __name__ == "__main__":
    main()
