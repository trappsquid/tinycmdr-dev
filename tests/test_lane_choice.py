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
# The last child's streams, kept apart: `said` is stdout + stderr + the log, and a log
# tail then hides what a check is actually about (a card, an answer, on STDOUT - which is
# what the automation reads). Written by run(), read by the checks that need the split.
LAST_IO = ["", ""]


def check(cond, what, extra=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}\n     {extra}")
    else:
        PASSES.append(what)
        print(f"ok   {what}")


def stub_llm(answer="stub answer"):
    """A live endpoint that answers one chat completion. Returns (server, base_url).

    The one-shot door's exit code has TWO sides - a failed run must not read as success
    and a delivered answer must not read as failure - and only a live stub can grade the
    second half (run 25, A-2026-10-07-68). Shut it down with server.shutdown().
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Stub(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"data": [{"id": "main", "object": "model",
                                  "max_model_len": 32768}]})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            self._send({"id": "c1", "object": "chat.completion", "model": "main",
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": answer}}],
                        "usage": {"prompt_tokens": 8, "completion_tokens": 3,
                                  "total_tokens": 11}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/v1" % srv.server_address[1]


def run(dirpath, args=(), tokens=(), with_mm=False, timeout=60, llm=None, config=True):
    """Run the harness in `dirpath` and return (exit_code, stdout+stderr+log).

    A child that SERVES (the page holds the process open) never exits on its own, so
    the deadline is the caller's: a timeout comes back as the string "serving" with
    everything the child had printed, instead of an exception that grades the test.
    For a serving child, prefer serve_and_probe(): text from a killed process is
    unreliable, the page answering is not.
    """
    cfg = {"llm": llm or LLM}
    if not config:
        (dirpath / "config.json").unlink(missing_ok=True)
    if with_mm:
        cfg["mattermost"] = {"url": "chat.invalid", "scheme": "https", "port": 443,
                             "token": "", "allowed_users": ["u1"]}
        cfg["telegram"] = {"token": "", "allowed_users": ["4242"]}
    if config:
        (dirpath / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    if tokens:
        (dirpath / ".env").write_text("".join(f"{k}={v}\n" for k, v in tokens),
                                      encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("TINYCMDR_")}
    env["HOME"] = str(dirpath)
    env["TINYCMDR_NO_BROWSER"] = "1"          # a test never opens a browser tab
    # FILES, not pipes: a piped child's stdout came back EMPTY on windows-latest while its
    # stderr carried the whole run (measured 2026-10-08, with fd 1 confirmed a FIFO and the
    # raw sentinel write reporting success) - so the pipe itself was the broken half. Files
    # also cannot deadlock on a full pipe buffer, which a run that prints a card can fill.
    out_f, err_f = dirpath / "child.out", dirpath / "child.err"
    try:
        with open(out_f, "wb") as _o, open(err_f, "wb") as _e:
            r = subprocess.run([sys.executable, str(dirpath / "tinycmdr.py"), *args],
                               cwd=str(dirpath), env=env, stdout=_o, stderr=_e,
                               timeout=timeout, stdin=subprocess.DEVNULL)
        r.stdout = out_f.read_text(encoding="utf-8", errors="replace")
        r.stderr = err_f.read_text(encoding="utf-8", errors="replace")
        # `or ""`: seen on windows-latest, where one of the two came back None
        # (measured 2026-10-08: TypeError: ... +: 'NoneType' and 'str'), and the suite
        # dying hides every check after it. The suite's _text() guards the timeout
        # path the same way.
        code, said = r.returncode, (r.stdout or "") + (r.stderr or "")
        LAST_IO[0], LAST_IO[1] = r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired as e:
        code = "serving"
        # The FILES are this run's output; a TimeoutExpired only carries bytes when the
        # stream was a pipe (measured: ubuntu CI) and None when it was one.
        out_t = out_f.read_text(encoding="utf-8", errors="replace") if out_f.exists() \
            else _text(e.stdout)
        err_t = err_f.read_text(encoding="utf-8", errors="replace") if err_f.exists() \
            else _text(e.stderr)
        said = out_t + err_t
        LAST_IO[0], LAST_IO[1] = out_t, err_t
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
        for name in ("cli_only", "both", "mm_only", "tg_only", "health", "fresh", "once"):
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

        # -- every door's line must reach STDOUT -----------------------------
        # stdout is what cron, ssh and CI parse; a run that prints only to stderr is
        # invisible to all three. Measured 2026-10-08 on windows-latest: a piped child's
        # stdout was EMPTY for the whole once-path while its stderr carried the turn logs
        # and the exit-1 line - so a delivered answer looked exactly like a silent failure.
        # The bare-python figure in the evidence is the control: it says whether the
        # platform captured stdout at all.
        code, _ = run(work / "cli_only", args=("--version",))
        _bare = subprocess.run([sys.executable, "-c", "print('x')"],
                               capture_output=True, text=True).stdout
        check(LAST_IO[0].strip() != "",
              "a verb's line reaches stdout, not just stderr",
              "STDOUT=%r STDERR=%r bare-python-stdout=%r"
              % (LAST_IO[0][-200:], LAST_IO[1][-200:], _bare))

        # -- the CLI-only end state: no chat lane and the page OFF ----------------
        code, said = run(work / "cli_only", args=("--no-web",))
        check(code == 0, f"no token and --no-web stops cleanly, exit 0 ({code})")
        check("no chat lane is configured" in said and "--cli" in said,
              "and says an install with no chat account is CLI-only", said[-400:])
        check("cannot start" not in said, "it is NOT a startup abort")
        check("Serving the page" not in said, "and it does not serve a page it was told to skip",
              said[-300:])

        # -- the one-shot door's EXIT CODE carries what its card says ---------------
        # `--once` is the door cron, ssh and CI use. A run whose endpoint never answered
        # printed "the task did not run" and returned 0, so a pipeline advanced on a run
        # that did nothing (run 24 filed it, run 25 re-verified it on v1.0.88,
        # A-2026-10-07-68). Both sides are graded, because a code that is always 1 would
        # break the other half just as quietly.
        code, said = run(work / "cli_only", args=("--no-web", "--once", "say hi"),
                         timeout=120)
        check(code == 1, f"--once against a dead endpoint exits 1, not 0 ({code})",
              said[-300:])
        # -1500, not -300: the card is what this check is about, and a Windows-only
        # failure's cause sits further back than the stderr/log tail (measured
        # 2026-10-08: -300 showed only the last log line of a run whose card was
        # missing, which is not enough to tell a skipped card from a broken run).
        check("did not run" in LAST_IO[0],
              "...and the card still says what happened",
              "STDOUT head=%r tail=%r STDERR head=%r tail=%r"
              % (LAST_IO[0][:120], LAST_IO[0][-120:],
                 LAST_IO[1][:160], LAST_IO[1][-200:]))
        check("exit 1: the run did not reach the model" in said,
              "...and stderr names the verb that explains it", said[-300:])
        _srv, _url = stub_llm("stub answer")
        try:
            code, said = run(work / "cli_only", args=("--no-web", "--once", "say hi"),
                             timeout=120, llm={"base_url": _url, "model": "main"})
        finally:
            _srv.shutdown()
            _srv.server_close()
        check(code == 0, f"...and a delivered answer still exits 0 ({code})", said[-300:])
        check("stub answer" in LAST_IO[0], "with the answer on stdout",
              "STDOUT head=%r tail=%r STDERR head=%r tail=%r"
              % (LAST_IO[0][:120], LAST_IO[0][-120:],
                 LAST_IO[1][:160], LAST_IO[1][-200:]))

        # -- a box with NO config.json is told so at this door ----------------------
        # The CLI/`--once` door deliberately runs without a config (it is the door that works
        # on one) and it used to say nothing: a fresh install's first command drew a confident
        # banner naming the shipped placeholder endpoint, and the only mention of config.json
        # in the whole run was an unrelated warning (run 24, A-2026-10-07-70).
        code, said = run(work / "fresh", args=("--no-web", "--once", "hi"),
                         config=False, timeout=120)
        check("config.example.json" in said and "no config.json" in said,
              "a run with no config.json says so, and names the example to copy",
              said[-400:])
        check(code == 1,
              "...and it still exits 1 (the shipped default endpoint answers nobody)", code)
        code, said = run(work / "cli_only", args=("--no-web", "--once", "say hi"), timeout=120)
        check("no config.json" not in said,
              "a box WITH a config.json gets no such note", said[-300:])

        # -- the STARTUP ORDER on a box that cannot start ---------------------------
        # The page used to be raised BEFORE the validation, so a new operator got a browser
        # tab at a port that died a moment later and a tokenized link pointing at nobody
        # (run 24, A-2026-10-07-73) - and the failed start then slept 30 seconds "for
        # pythonw" on a box with no console at all, stretching every crash-restart cycle
        # (A-2026-10-07-72). Both are visible at the process boundary.
        _port = free_port()
        _t0 = time.time()
        code, said = run(work / "fresh",
                         args=("--mattermost", "--no-browser", "--web-port", str(_port)),
                         config=False, timeout=90)
        _dt = time.time() - _t0
        check(code == 2 and "config.example.json" in said,
              "a cold --mattermost refuses with the config sentence", (code, said[-200:]))
        check("page:" not in said and "web UI listening" not in said,
              "A-73: ...and NO page is raised before it refuses", said[-300:])
        check(_dt < 15,
              "A-72: ...and it does not sit 30s on the way out", "took %.1fs" % _dt)

        # -- `--once` takes the TASK, and only the task ----------------------------
        # A separate flag after `--once` used to be swallowed into the prompt - the model was
        # asked "--cli hello" (read back from the session the run wrote: run 24,
        # A-2026-10-07-74) - and `--once` with nothing after it drew a banner and opened a
        # full interactive session that then ate the script's stdin (A-2026-10-07-75).
        code, said = run(work / "once", args=("--no-web", "--once", "--cli", "hello"),
                         timeout=120)
        check("read as flags" in said and "--cli" in said,
              "A-74: a flag after --once is named, not swallowed into the prompt", said[-300:])
        _stored = []
        for _f in sorted((work / "once" / "sessions").glob("*.json")):
            try:
                _msgs = json.loads(_f.read_text(encoding="utf-8"))
            except Exception:                                    # noqa: BLE001
                continue
            for _m in (_msgs if isinstance(_msgs, list) else []):
                if isinstance(_m, dict) and _m.get("role") == "user":
                    _stored.append(str(_m.get("content")))
        check(_stored and all(s == "hello" for s in _stored),
              "A-74: ...and the prompt the model received is exactly the text", _stored[:3])
        code, said = run(work / "once", args=("--no-web", "--once"), timeout=60)
        check(code == 2 and "needs the task" in said,
              "A-75: --once with no task is a usage error, not a session", (code, said[-200:]))
        check("type at any time" not in said and "Type /tinycmdr" not in said,
              "A-75: ...and it does not fall through to the interactive banner", said[-300:])

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
        _hsaid = (r.stdout or "") + (r.stderr or "")
        check("lane none" in _hsaid,
              "and `health` reports no lane, not a placeholder one",
              _hsaid[-200:])

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
