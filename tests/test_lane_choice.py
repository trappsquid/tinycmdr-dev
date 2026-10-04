"""Which lane a bare start serves - and the three states that are all valid.

The lane rules this grades, all of them visible only by RUNNING the file:

  * no token and the page ON (the default) is a serving install: the first start mints
    the token the page requires and holds the process open - the shape the autostart
    agent runs on. The CLI-only end state is the page OFF (`--no-web` or
    web.enabled: false), which explains itself and stops cleanly (exit 0);
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
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
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


def run(dirpath, args=(), tokens=(), with_mm=False, timeout=60):
    """Run the harness in `dirpath` and return (exit_code, stdout+stderr+log).

    A child that SERVES (the page holds the process open) never exits on its own, so
    the deadline is the caller's: a timeout comes back as the string "serving" with
    everything the child had printed, instead of an exception that grades the test.
    For a serving child, prefer serve_and_probe(): text from a killed process is
    unreliable, the page answering is not.
    """
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
    env["TINYCMDR_NO_BROWSER"] = "1"          # a test never opens a browser tab
    try:
        r = subprocess.run([sys.executable, str(dirpath / "tinycmdr.py"), *args],
                           cwd=str(dirpath), env=env, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        code, said = r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        code = "serving"
        # stdout/stderr on a TimeoutExpired are BYTES on some platforms even with
        # text=True (measured: ubuntu CI), and order matters: normalize each part.
        said = _text(e.stdout) + _text(e.stderr)
    logf = dirpath / "tinycmdr.log"
    if logf.exists():
        said += logf.read_text(encoding="utf-8", errors="replace")
    return code, said


def _text(chunk):
    if chunk is None:
        return ""
    return chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")


def load(dirpath):
    """Import the staged file in-process, for the rules that are not about the process."""
    spec = importlib.util.spec_from_file_location("lane_" + dirpath.name,
                                                 dirpath / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def free_port():
    """A port nothing holds right now (the child binds exactly it and we poll it)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve_and_probe(dirpath, port, deadline=45):
    """Start the file with the page on `port`, wait for the PAGE to answer, kill it.

    The page is the evidence, not the child's words: a process killed at a deadline has
    unflushed prints in its pipe AND an unflushed log tail (the log rides a background
    listener), so text greps flake by platform - measured, and the reason this helper
    exists. Returns (served, authed, said): served is /api/health answering 200, authed
    is a gated route answering 200 with the token the child itself minted into .env.
    """
    (dirpath / "config.json").write_text(json.dumps(
        {"llm": LLM, "web": {"enabled": True, "host": "127.0.0.1", "port": port}}),
        encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("TINYCMDR_")}
    env["HOME"] = str(dirpath)
    # a test must not put tabs in somebody's browser: every staged child that serves
    # would otherwise auto-open the page (macOS says a browser is available)
    env["TINYCMDR_NO_BROWSER"] = "1"
    proc = subprocess.Popen([sys.executable, str(dirpath / "tinycmdr.py")],
                            cwd=str(dirpath), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    served = authed = False
    t0 = time.time()
    while time.time() - t0 < deadline and proc.poll() is None:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port,
                                        timeout=1) as r:
                if r.status == 200:
                    served = True
                    break
        except Exception:                                        # noqa: BLE001
            time.sleep(0.5)
    tok = ""
    envf = dirpath / ".env"
    if envf.exists():
        for ln in envf.read_text(encoding="utf-8").splitlines():
            if ln.startswith("TINYCMDR_WEB_TOKEN="):
                tok = ln.split("=", 1)[1].strip()
    if served and tok:
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/api/tasks" % port,
                                         headers={"X-Tinycmdr-Token": tok})
            with urllib.request.urlopen(req, timeout=5) as r:
                authed = r.status == 200
        except Exception:                                        # noqa: BLE001
            authed = False
    if proc.poll() is None:
        proc.kill()
    try:
        out, err = proc.communicate(timeout=10)
    except Exception:                                            # noqa: BLE001
        out, err = "", ""
    said = (out or "") + (err or "")
    logf = dirpath / "tinycmdr.log"
    if logf.exists():
        said += logf.read_text(encoding="utf-8", errors="replace")
    return served, authed, said


def main():
    work = Path(tempfile.mkdtemp(prefix="fblane-"))
    try:
        for name in ("cli_only", "both", "mm_only", "tg_only", "health"):
            d = work / name
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SRC, d / "tinycmdr.py")

        # -- no token and the page on: the page is the door ------------------------
        # The page's token is MINTED at first start now, so a bare run on a lane-less
        # box does not stop: it mints, serves the page and holds the process open -
        # which is what the installer's autostart agent relies on. The CLI-only end
        # state is the page OFF (next case).
        port = free_port()
        served, authed, said = serve_and_probe(work / "cli_only", port)
        check(served, "no token, page on: the page answers on the configured port")
        check(authed, "and the token it minted gates it (a gated route: 200)")
        check("cannot start" not in said, "it is NOT a startup abort")
        _env = (work / "cli_only" / ".env").read_text(encoding="utf-8")
        check("TINYCMDR_WEB_TOKEN=" in _env, "the minted token lands in .env", _env[-120:])

        # -- the CLI-only end state: no chat lane and the page OFF ----------------
        code, said = run(work / "cli_only", args=("--no-web",))
        check(code == 0, f"no token and --no-web stops cleanly, exit 0 ({code})")
        check("no chat lane is configured" in said and "--cli" in said,
              "and says an install with no chat account is CLI-only", said[-400:])
        check("cannot start" not in said, "it is NOT a startup abort")
        check("Serving the page" not in said, "and it does not serve a page it was told to skip",
              said[-300:])

        # -- the shipped placeholders are not a lane -----------------------------
        # Asserted in-process. Grepping the child's LOG for "CLI-only install" was a race -
        # the log listener need not have flushed its last lines when a fast child exits -
        # which macOS won and ubuntu lost, so it graded the platform, not the rule. The run
        # here is only what stages this case's config.json; the rule is read off the module.
        # `--no-web`: this run exists only to stage the case's config.json (the rule is
        # read off the module, below). Without it the page is the door now, and the child
        # mints a token, serves 8790 and holds the port until the deadline.
        run(work / "health", args=("--no-web",), timeout=30)
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
