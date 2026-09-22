"""The supervisor's readiness gate, against a real child process.

`up but not ready after 90s (lock held=False, mattermost=None)` was printed on every
PAGE-ONLY install - the lane a reader gets when they do not have a chat account - and
because "was it ready" also decides whether a later exit counts as a failed start, a
healthy bot on that lane grew backoff and would eventually be announced as "tinycmdr
keeps failing". The gate waits for the doors the child was actually asked to serve:

  * a chat install: the lock is held and Mattermost answers (unchanged),
  * a --web install: the page answers on its own port,
  * both: both.

This stages a throwaway install, starts the child the way the supervisor does, and
grades wait_ready() in both directions - a page lane that must come up ready, and a
chat lane pointed at a dead server that must NOT.

    python tests/test_supervise_ready.py
"""
import importlib.util
import json
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SUPERVISOR = BASE / "tinycmdr-supervise.py"

FAILS = []


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}")
    else:
        print(f"ok   {what}")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def stage(dirpath, config):
    dirpath.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE / "tinycmdr.py", dirpath / "tinycmdr.py")
    shutil.copy2(SUPERVISOR, dirpath / "tinycmdr-supervise.py")
    (dirpath / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (dirpath / "logs").mkdir(exist_ok=True)
    return dirpath


def load_supervisor(dirpath):
    """The shipped module, loaded from the staged folder (its BASE_DIR is that folder)."""
    spec = importlib.util.spec_from_file_location(
        "sup_" + dirpath.name, dirpath / "tinycmdr-supervise.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_lane(dirpath, args, timeout=30):
    sup = load_supervisor(dirpath)
    sup.CHILD_ARGS = list(args)
    proc, fh = sup.start_bot()
    try:
        t0 = time.time()
        ready = sup.wait_ready(proc, timeout=timeout)
        return {"ready": ready, "wall": time.time() - t0, "doors": sup.waiting_on(),
                "port": sup.web_port(), "chat": bool(sup.mm_config())}
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass
        time.sleep(0.5)


def main():
    work = Path(tempfile.mkdtemp(prefix="fbready-"))
    try:
        # -- a page-only install: no chat account, so the page IS the door ----------
        port = free_port()
        d = stage(work / "page", {
            "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "dead"},
            "web": {"enabled": True, "port": port, "host": "127.0.0.1", "token": "tok"},
        })
        res = run_lane(d, ["--web"])
        check(res["port"] == port, f"the supervisor finds the page's port ({res['port']})")
        check(res["chat"] is False, "an install with no chat account is not a chat lane")
        check(res["doors"] == ["the page on 127.0.0.1:%d" % port],
              f"so the only door it waits for is the page ({res['doors']})")
        check(res["ready"] is not None,
              f"a page-only install comes up READY ({res['ready']}s of {res['wall']:.1f}s)")
        check(res["ready"] is not None and res["ready"] < 20,
              "and it says so quickly, not after the 90s timeout")

        # -- a chat lane with a dead Mattermost: must NOT report ready --------------
        d = stage(work / "chat", {
            "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "dead"},
            "web": {"enabled": True, "port": free_port(), "host": "127.0.0.1",
                    "token": "tok"},
            "mattermost": {"url": "127.0.0.1", "port": 9, "token": "not-a-real-token",
                           "allowed_users": []},
        })
        (d / ".env").write_text("TINYCMDR_MM_TOKEN=not-a-real-token\n", encoding="utf-8")
        res = run_lane(d, [], timeout=8)
        check(res["chat"] is True, "a configured chat account is waited on")
        check(res["doors"] == ["Mattermost"],
              f"an install with no --web waits for Mattermost only ({res['doors']})")
        check(res["ready"] is None,
              f"a chat lane whose server never answers is NOT ready ({res['ready']})")

        # -- both doors: the page alone is not enough ------------------------------
        d = stage(work / "both", {
            "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "dead"},
            "web": {"enabled": True, "port": free_port(), "host": "127.0.0.1",
                    "token": "tok"},
            "mattermost": {"url": "127.0.0.1", "port": 9, "token": "not-a-real-token",
                           "allowed_users": []},
        })
        (d / ".env").write_text("TINYCMDR_MM_TOKEN=not-a-real-token\n", encoding="utf-8")
        res = run_lane(d, ["--web"], timeout=8)
        check(sorted(res["doors"]) == ["Mattermost",
                                       "the page on 127.0.0.1:%d" % res["port"]],
              f"a page+chat install waits for both ({res['doors']})")
        check(res["ready"] is None,
              "and a page that answers does not excuse a dead Mattermost "
              f"({res['ready']})")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("all supervisor readiness checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
