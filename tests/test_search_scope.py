"""A directory search answers a bounded, reproducible PAGE about all the files asked.

The walk is path-ordered so the same query returns the same page on every host (a
capped result is
reproducible and has a stable next slice), a per-file cap keeps one hot log from eating the
whole max_results budget before the other files are reached, and both a capped file and a
cap-terminated walk SAY SO - a silent skip reads as an exhaustive answer.

    python tests/test_search_scope.py
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / os.environ.get("TINYCMDR_SRC", "tinycmdr.py")

STAGE = Path(tempfile.gettempdir()) / "tinycmdr-test-stage-search-scope"
if STAGE.exists():
    shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC, STAGE / "tinycmdr.py")
shutil.copy2(Path(__file__).resolve().parent / "fixture-config.json",
             STAGE / "config.json")
spec = importlib.util.spec_from_file_location("tinycmdr_search_scope",
                                              STAGE / "tinycmdr.py")
fb = importlib.util.module_from_spec(spec)
sys.modules["tinycmdr_search_scope"] = fb
spec.loader.exec_module(fb)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}: {detail}")


def search(work, **args):
    return fb.tool_search_files(dict(args, path=str(work)), {"session_key": "search-s1"})


def main():
    work = Path(tempfile.mkdtemp(prefix="fbtest-search-scope-"))
    try:
        hot = work / "a.log"
        hot.write_text("".join("hit line %d\n" % i for i in range(12)), encoding="utf-8")
        (work / "b.log").write_text("one hit here\n", encoding="utf-8")

        # ---- per-file cap: the quiet file is still reached and shown
        out = search(work, content="hit", max_results=50)
        check("the hot file is capped at the per-file limit",
              out.count("a.log:") == 5, out)
        check("the other file is still searched and shown",
              "b.log:1:" in out, out)
        check("the capped file is named in the note",
              "per-file cap of 5" in out and "a.log" in out, out)

        # ---- the cap is per call, not a wall: a named file greps deep
        out = search(hot, content="hit", max_results=50)
        check("a directly named file is not per-file capped",
              out.count("a.log:") == 12, out.count("a.log:"))

        # ---- a cap-terminated walk says the rest was not searched
        for name in ("c.log", "d.log"):
            (work / name).write_text("".join("hit\n" for _ in range(12)), encoding="utf-8")
        out = search(work, content="hit", max_results=6)
        check("a max_results-terminated walk says so",
              "NOT searched" in out and "max_results cap of 6" in out, out)

        # ---- deterministic order: same tree, same page, every run
        tree = Path(tempfile.mkdtemp(prefix="fbtest-search-order-"))
        for sub in ("z-sub", "a-sub"):
            (tree / sub).mkdir()
            (tree / sub / "one.txt").write_text("needle\n", encoding="utf-8")
        first = search(tree, content="needle", max_results=50)
        second = search(tree, content="needle", max_results=50)
        check("the same search returns the same page twice", first == second, first)
        check("directories are walked in path order",
              first.index("a-sub") < first.index("z-sub"), first)

        # ---- an exhaustive miss is still exactly "No matches."
        empty = Path(tempfile.mkdtemp(prefix="fbtest-search-empty-"))
        (empty / "x.txt").write_text("nothing here\n", encoding="utf-8")
        check("a real miss is unchanged", search(empty, content="absent") == "No matches.")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        return 1
    print("all search-scope checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
