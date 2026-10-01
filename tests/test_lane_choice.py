"""Which lane a bare start serves - and the three states that are all valid.

The lane rules this grades, all of them visible only by RUNNING the file:

  * no token at all is a supported CLI-only install: it must explain itself and stop
    cleanly (exit 0), not abort, and not start a lane that has no account;
  * the shipped placeholders ("PASTE_BOT_TOKEN_HERE", "your-mattermost-user-id") are
    NOT a lane - counting them made a fresh install look like a Mattermost host with a
    broken URL, and `tinycmdr health` claimed a `mattermost` lane on a CLI-only box;
  * BOTH tokens set serves BOTH lanes in one process (Mattermost on the main thread,
    Telegram on a daemon one), so adding a Telegram token never leaves that lane dark;
  * `--mattermost` with no token says so instead of guessing.

    python tests/test_lane_choice.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "tinycmdr.py"
LLM = {"base_url": "http://127.0.0.1:9/v1", "model": "dead"}

PASSES, FAILS = [], []


def check(cond, what, extra=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}\n     {extra}")
    else:
        PASSES.append(what)
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


def load(dirpath):
    """Import the staged file in-process, for the rules that are not about the process."""
    spec = importlib.util.spec_from_file_location("lane_" + dirpath.name,
                                                 dirpath / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


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
        # Asserted in-process. Grepping the child's LOG for "CLI-only install" was a race -
        # the log listener need not have flushed its last lines when a fast child exits -
        # which macOS won and ubuntu lost, so it graded the platform, not the rule. The run
        # here is only what stages this case's config.json; the rule is read off the module.
        run(work / "health")
        saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("TINYCMDR_")}
        try:
            m = load(work / "health")
            check(m._chat_lane_configured() is False,
                  "the shipped placeholder token is not treated as a Mattermost lane",
                  repr(m.CONFIG["mattermost"].get("token")))
            err = m.validate_startup_config()
            check(err is None,
                  "so a token-less install is a CLI-only one, not a startup error", err)
        finally:
            os.environ.update(saved)
        env = {k: v for k, v in os.environ.items() if not k.startswith("TINYCMDR_")}
        env["HOME"] = str(work / "health")
        r = subprocess.run([sys.executable, str(work / "health" / "tinycmdr.py"), "health"],
                           cwd=str(work / "health"), env=env, capture_output=True,
                           text=True, timeout=60)
        check("lane none" in (r.stdout + r.stderr),
              "and `health` reports no lane, not a placeholder one",
              (r.stdout + r.stderr)[-200:])

        # -- both tokens: BOTH lanes are served --------------------------------
        # Graded in-process: actually RUNNING this would open a Mattermost connection and
        # long-poll Telegram, so the config is staged as files and the rule read off the
        # module. The old rule refused to guess (exit 2) and left a box that added a
        # Telegram token serving NOTHING - the lane was never started.
        (work / "both" / "config.json").write_text(json.dumps({
            "llm": LLM,
            "mattermost": {"url": "chat.invalid", "scheme": "https", "port": 443,
                           "token": "", "allowed_users": ["u1"]},
            "telegram": {"token": "", "allowed_users": ["4242"]},
        }), encoding="utf-8")
        (work / "both" / ".env").write_text(
            "TINYCMDR_MM_TOKEN=mm-tok\nTINYCMDR_TG_TOKEN=tg-tok\n", encoding="utf-8")
        saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("TINYCMDR_")}
        try:
            m = load(work / "both")
            os.environ["TINYCMDR_MM_TOKEN"] = "mm-tok"
            os.environ["TINYCMDR_TG_TOKEN"] = "tg-tok"
            check(m.lanes_to_serve([]) == ["mattermost", "telegram"],
                  "both tokens serve BOTH lanes in one process", m.lanes_to_serve([]))
            check(m.lanes_to_serve(["--telegram"]) == ["telegram"]
                  and m.lanes_to_serve(["--mattermost"]) == ["mattermost"],
                  "a flag still forces a single lane", None)
        finally:
            os.environ.update(saved)

        # -- --mattermost with no token ------------------------------------------
        code, said = run(work / "mm_only", args=["--mattermost"])
        check(code == 2 and "--mattermost was given" in said,
              f"--mattermost without a token says so ({code})", said[-300:])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
