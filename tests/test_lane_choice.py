"""Which lane a bare start serves - and the three states that are all valid.

The lane rules this grades, all of them visible only by RUNNING the file:

  * no token at all is a supported CLI-only install: it must explain itself and stop
    cleanly (exit 0), not abort, and not start a lane that has no account;
  * the shipped placeholders ("PASTE_BOT_TOKEN_HERE", "your-mattermost-user-id") are
    NOT a lane - counting them made a fresh install look like a Mattermost host with a
    broken URL, and `tinycmdr health` claimed a `mattermost` lane on a CLI-only box;
  * BOTH tokens set starts NEITHER: neither lane is primary, so the operator picks with
    `--telegram` / `--mattermost`;
  * `--mattermost` with no token says so instead of guessing.

    python tests/test_lane_choice.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "tinycmdr.py"
LLM = {"base_url": "http://127.0.0.1:9/v1", "model": "dead"}

FAILS = []


def check(cond, what, extra=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}\n     {extra}")
    else:
        print(f"ok   {what}")


def run(dirpath, args=(), tokens=(), with_mm=False):
    """Run the harness in `dirpath` and return (exit_code, stdout+stderr+log)."""
    cfg = {"llm": LLM}
    if with_mm:
        cfg["mattermost"] = {"url": "chat.invalid", "scheme": "https", "port": 443,
                             "token": "", "allowed_users": ["u1"]}
        cfg["telegram"] = {"token": "", "allowed_users": ["4242"]}
    (dirpath / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    if tokens:
        (dirpath / ".env").write_text("".join(f"{k}={v}\n" for k, v in tokens),
                                      encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("TINYCMDR_")}
    env["HOME"] = str(dirpath)
    r = subprocess.run([sys.executable, str(dirpath / "tinycmdr.py"), *args],
                       cwd=str(dirpath), env=env, capture_output=True, text=True,
                       timeout=60, stdin=subprocess.DEVNULL)
    said = r.stdout + r.stderr
    logf = dirpath / "tinycmdr.log"
    if logf.exists():
        said += logf.read_text(encoding="utf-8", errors="replace")
    return r.returncode, said


def main():
    work = Path(tempfile.mkdtemp(prefix="fblane-"))
    try:
        for name in ("cli_only", "both", "mm_only", "tg_only", "health"):
            d = work / name
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SRC, d / "tinycmdr.py")

        # -- no token: a CLI-only install, explained and clean --------------------
        code, said = run(work / "cli_only")
        check(code == 0, f"no token at all stops cleanly, exit 0 ({code})")
        check("no chat lane is configured" in said and "--cli" in said,
              "and says an install with no chat account is CLI-only", said[-400:])
        check("cannot start" not in said, "it is NOT a startup abort")

        # -- the shipped placeholders are not a lane -----------------------------
        code, said = run(work / "health")
        check("CLI-only install" in said,
              "the shipped placeholder token is not treated as a Mattermost lane",
              [l for l in said.splitlines() if "chat lane" in l][:2])
        env = {k: v for k, v in os.environ.items() if not k.startswith("TINYCMDR_")}
        env["HOME"] = str(work / "health")
        r = subprocess.run([sys.executable, str(work / "health" / "tinycmdr.py"), "health"],
                           cwd=str(work / "health"), env=env, capture_output=True,
                           text=True, timeout=60)
        check("lane none" in (r.stdout + r.stderr),
              "and `health` reports no lane, not a placeholder one",
              (r.stdout + r.stderr)[-200:])

        # -- both tokens: neither lane is primary --------------------------------
        code, said = run(work / "both",
                         tokens=[("TINYCMDR_MM_TOKEN", "mm-tok"),
                                 ("TINYCMDR_TG_TOKEN", "tg-tok")],
                         with_mm=True)
        check(code == 2, f"both tokens and no flag refuses to guess, exit 2 ({code})")
        check("neither lane" in said and "--telegram" in said and "--mattermost" in said,
              "and names both flags", said[-400:])

        # -- --mattermost with no token ------------------------------------------
        code, said = run(work / "mm_only", args=["--mattermost"])
        check(code == 2 and "--mattermost was given" in said,
              f"--mattermost without a token says so ({code})", said[-300:])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("all lane-choice checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
