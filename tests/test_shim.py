"""`tinycmdr` with nothing after it opens a session - in both shims, without breaking anything else.

Operator, 2026-09-22: "so I can open a terminal/cmd/powershell window on the Windows bed now and
type tinycmdr and it will open a cli instance?"

It could not: the shims passed their arguments through and the build's no-argument case is
the BOT lane, which on a supervised box answers "already running from this folder". The
session was `--cli`. The shims now add `--cli` when there is nothing to pass, and these
checks hold that mapping to the letter:

  * no arguments -> ["--cli"], in `tinycmdr.cmd` (Windows) and `tinycmdr` (POSIX)
  * a verb, `--web`, `--once "<task>"` and a multi-word verb pass through untouched
  * the BOT keeps starting the way it always has: `python tinycmdr.py` with no flags still
    runs the supervised lanes, because the scheduled task, the systemd unit and the VBS
    launcher name the file directly and never go through a shim

Each shim is copied into a temp folder beside a stub `tinycmdr.py` that prints its argv
as JSON - so the check reads what the shim decided, not what the app does with it, and
no config, model or network is involved. Run: python tests/test_shim.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILED = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else "  <- %s" % detail))
    if not ok:
        FAILED.append(what)


STUB = '''import json, sys
print(json.dumps({"argv": sys.argv[1:]}))
'''


def stage(name):
    """A temp folder holding one shim and a stub app that reports its own argv."""
    d = tempfile.mkdtemp(prefix="tinycmdr-shim-")
    shutil.copyfile(os.path.join(ROOT, name), os.path.join(d, name))
    with open(os.path.join(d, "tinycmdr.py"), "w", encoding="utf-8") as f:
        f.write(STUB)
    return d


def run_posix(args):
    """The POSIX shim, driven directly - only where `sh` and the paths agree.

    On Windows this shim is not the door anyone uses (`tinycmdr.cmd` is), and driving it
    through MSYS rewrites $0's path: `pwd` answers `/c/tmp/...` for a `C:/tmp/...` folder,
    and the native python then cannot open it. So on Windows the mapping is checked
    statically below, and the behaviour is exercised on a real POSIX host instead
    (`bash tests/test_shim.py` on Linux, or by hand).
    """
    d = stage("tinycmdr")
    env = dict(os.environ, TINYCMDR_PYTHON=sys.executable)
    env.pop("PYTHONPATH", None)
    shim = os.path.join(d, "tinycmdr")
    p = subprocess.run(["sh", shim] + args,
                       capture_output=True, text=True, timeout=120, env=env, cwd=d)
    return p


def run_windows(args):
    d = stage("tinycmdr.cmd")
    env = dict(os.environ)
    python_dir = os.path.dirname(sys.executable)
    env["PATH"] = python_dir + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONPATH", None)
    p = subprocess.run(["cmd", "/c", os.path.join(d, "tinycmdr.cmd")] + args,
                       capture_output=True, text=True, timeout=120, env=env, cwd=d)
    return p


def argv_of(p):
    for line in reversed((p.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)["argv"]
            except (ValueError, KeyError):
                return None
    return None


def check_pass_through(runner, label, args, want):
    p = runner(args)
    got = argv_of(p)
    check("%s: %s -> %s" % (label, args or "(nothing)", want),
          got == want,
          "rc=%s out=%r err=%r" % (p.returncode, (p.stdout or "")[-160:], (p.stderr or "")[-160:]))


def main():
    print("== the decision each shim makes ==")
    for runner, label in (((run_windows, "windows"),) if os.name == "nt" else ()):
        check_pass_through(runner, label, [], ["--cli"])
        check_pass_through(runner, label, ["status"], ["status"])
        check_pass_through(runner, label, ["--web"], ["--web"])
        check_pass_through(runner, label, ["--once", "reply with READY"], ["--once", "reply with READY"])
        check_pass_through(runner, label, ["model", "use", "main"], ["model", "use", "main"])
        check_pass_through(runner, label, ["help"], ["help"])

    if os.name == "posix":
        for args, want in (([], ["--cli"]), (["status"], ["status"]), (["--web"], ["--web"]),
                           (["--once", "reply with READY"], ["--once", "reply with READY"]),
                           (["model", "use", "main"], ["model", "use", "main"])):
            check_pass_through(run_posix, "posix", args, want)
    else:
        sh = open(os.path.join(ROOT, "tinycmdr"), encoding="utf-8").read()
        check("posix: nothing -> --cli (checked as text on Windows; MSYS rewrites $0)",
              'if [ "$#" -eq 0 ]; then\n    exec "$PY" "$HERE/tinycmdr.py" --cli\nfi' in sh, sh[-220:])
        check("posix: real arguments still pass through",
              'exec "$PY" "$HERE/tinycmdr.py" "$@"' in sh, sh[-220:])

    print("\n== the bot lane still starts without a shim ==")
    app = os.path.join(ROOT, "tinycmdr.py")
    if not os.path.exists(app):
        print("skip the source checks: no %s in this tree" % app)
        return 1 if FAILED else 0
    src = open(app, encoding="utf-8").read()
    body = src[src.index("def main():"):]
    body = body[:body.index("\nif __name__ ==")]
    check("no flags reaches the startup validation and the bot",
          "validate_startup_config()" in body and "run_bot()" in body,
          "main() lost its bot fallthrough")
    check("no flags does NOT silently become a session",
          not re.search(r"else:\s*\n\s+run_cli\(\)", body),
          "main()'s no-flag branch now runs a session - the service path would follow it")
    for name in ("tinycmdr", "tinycmdr.cmd"):
        raw = open(os.path.join(ROOT, name), "rb").read()
        if name.endswith(".cmd"):
            check("%s is CRLF" % name, raw.count(b"\r\n") == raw.count(b"\n"), "LF-only .cmd")
        check("%s does not invent flags for real arguments" % name,
              b'%*' in raw if name.endswith(".cmd") else b'"$@"' in raw,
              "the pass-through is gone")

    print("\n%s" % ("all shim checks passed" if not FAILED else "FAILED: %d" % len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
