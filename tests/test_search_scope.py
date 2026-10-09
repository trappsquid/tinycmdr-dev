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

        # ---- A-2026-10-08-98: only REGULAR files are read ---------------------------------
        # A device/FIFO/socket used to be read with read_bytes() like any file: its
        # st_size is 0, so the 2 MB guard never saw it, /dev/zero grows until the kernel
        # OOM-kills the bot, and a FIFO's open() never returns. Each one is skipped BY
        # NAME now - a silent skip reads as an exhaustive answer.
        import socket as _socket
        special = []
        sock_path = work / "a-socket-name.txt"
        if os.name != "nt" and hasattr(_socket, "AF_UNIX"):
            _s = _socket.socket(_socket.AF_UNIX)
            _s.bind(str(sock_path))
            special.append(sock_path.name)
        fifo_path = work / "a-fifo-name.txt"
        if hasattr(os, "mkfifo"):
            os.mkfifo(fifo_path)
            special.append(fifo_path.name)
            # Hold a writer just long enough that a PRE-FIX read returns at all: without
            # one, the old open()-for-read blocks for ever and this suite would hang
            # instead of failing. The fixed walk never opens it, so the thread may stay
            # parked in open() - daemon, never joined.
            import threading
            import time
            def _brief_writer():
                try:
                    with open(fifo_path, "w", encoding="utf-8"):
                        time.sleep(0.5)
                except OSError:
                    pass
            threading.Thread(target=_brief_writer, daemon=True).start()
        if special:
            out = search(work, content="needle-that-is-not-there")
            check("a socket and a FIFO in the tree are skipped, not read",
                  "No matches." in out, out)
            check("...and each one is named in the note",
                  all(n in out for n in special) and "not regular files" in out, out)
            out = search(sock_path, content="x")
            check("naming a socket directly is refused, not a silent 'No matches.'",
                  out.startswith("ERROR:") and "not a regular file" in out, out[:140])
        else:
            print("note  no socket/FIFO check on this platform (no AF_UNIX file sockets)")

        # A single named file goes through the SAME 8 MiB cap the shell's output wears.
        # The walk's own skip note sends the over-2-MB files here, so this read was the
        # unbounded half of the same bug.
        fat = work / "fat.log"
        with open(fat, "w", encoding="utf-8") as f:
            f.write("x" * (9 * 1024 * 1024))
            f.write("needle-after-the-cap\n")
        out = search(fat, content="needle-after-the-cap")
        check("a single file over the cap is read bounded, and says so",
              "only the first" in out and "MiB" in out, out[:220])

        # ---- an exhaustive miss is still exactly "No matches."
        empty = Path(tempfile.mkdtemp(prefix="fbtest-search-empty-"))
        (empty / "x.txt").write_text("nothing here\n", encoding="utf-8")
        check("a real miss is unchanged", search(empty, content="absent") == "No matches.")

        # A-2026-10-05-73: max_results=0 fell through `or 50` and searched anyway.
        out = search(work, content="hit", max_results=0)
        check("max_results=0 is refused, not silently defaulted",
              out.startswith("ERROR: max_results=0") and "positive number" in out, out[:120])
        out = search(work, content="hit", max_results="lots")
        check("...and a non-numeric cap is named, not crashed on",
              out.startswith("ERROR: max_results must be a whole number"), out[:120])
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
