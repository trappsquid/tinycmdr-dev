"""An install that predates the page upgrades into it, with no hand-written secret.

The page returned in 1.0.67, so a config.json written before it has NO `web` block - and
the token the page requires arrives in .env (the installer mints one; the first start
mints one where there is none). Those two facts met badly once, and this suite is the
shape that caught it:

  * DEFAULT_CONFIG carried its `web` defaults nested under `agent`, so they applied to
    nothing: a host whose config.json has no `web` block had no merged one either.
  * the env-to-config mapping indexed `cfg["web"]` directly, so the moment a minted
    TINYCMDR_WEB_TOKEN existed beside that old config.json, the process died AT IMPORT
    with `KeyError: 'web'` - the upgrade path, and the one path that cannot be allowed
    to need a hand-edited file.

Both halves are graded here against a staged tree that looks exactly like an upgraded
install: config.json from before the page, .env with the token. Nothing is network-bound
(loopback, port 0, no model).

    python tests/test_page_upgrade.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "tinycmdr.py"

# A hand-run suite must not open the operator's browser: run_all passes
# TINYCMDR_NO_BROWSER=1 to its children, a direct run did not (measured 2026-10-05 -
# `python tests/test_page_upgrade.py` auto-opened the page through the in-process server
# below). A check in tests/test_webui.py fails any suite that forgets this.
os.environ.setdefault("TINYCMDR_NO_BROWSER", "1")
FAILS = []
TOK = "upgrade-tok-0123456789abcdef"


def check(cond, what, detail=""):
    if not cond:
        FAILS.append(what)
        print(f"FAIL {what}: {detail}")
    else:
        print(f"ok   {what}")


def stage(dirpath, cfg, env_lines):
    """A pre-page install: config.json without `web`, a .env carrying the token."""
    dirpath.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, dirpath / "tinycmdr.py")
    (dirpath / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (dirpath / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    return dirpath


def load(dirpath, name):
    spec = importlib.util.spec_from_file_location(name, dirpath / "tinycmdr.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)          # the import that used to raise KeyError
    return mod


def get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-Tinycmdr-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def main():
    work = Path(tempfile.mkdtemp(prefix="tcupgrade-"))
    try:
        # ---- old config.json + a token in .env: the import must survive ----------
        cfg = {"llm": {"base_url": "http://127.0.0.1:9/v1", "model": "main"}}
        assert "web" not in cfg
        d = stage(work / "upgrade", cfg, ["TINYCMDR_WEB_TOKEN=" + TOK])
        os.environ.pop("TINYCMDR_WEB_TOKEN", None)     # the .env is the only carrier
        try:
            fb = load(d, "upgrade_mod")
            loaded = True
            err = ""
        except Exception as e:                          # noqa: BLE001
            fb, loaded, err = None, False, "%s: %s" % (type(e).__name__, e)
        check(loaded, "an old config.json plus a .env token imports cleanly", err)
        if not loaded:
            return 1

        # the env value reached the config, and the DEFAULTS are real ones
        check(fb.CONFIG.get("web", {}).get("token") == TOK,
              "the .env token lands in CONFIG['web']['token']", fb.CONFIG.get("web"))
        check(fb.CONFIG.get("web", {}).get("enabled") is True
              and fb.CONFIG["web"].get("host") == "127.0.0.1"
              and fb.CONFIG["web"].get("port") == 8790,
              "and this host inherited the page's real defaults (on, loopback, 8790)",
              fb.CONFIG.get("web"))
        check("web" not in (fb.CONFIG.get("agent") or {}),
              "the defaults are not filed under agent where nothing reads them",
              sorted((fb.CONFIG.get("agent") or {}).keys())[:5])

        # ---- and it serves, gated -------------------------------------------------
        fb.CONFIG["web"]["port"] = 0
        srv = fb.run_webui()
        check(srv is not None, "the upgraded host serves the page")
        if srv is not None:
            try:
                port = srv.server_address[1]
                base = "http://127.0.0.1:%d/api/tasks" % port
                check(get(base) == 401, "without the header: 401")
                check(get(base, TOK) == 200, "with the .env token: 200")
            finally:
                srv.shutdown()
                srv.server_close()

        # ---- a config.json whose `web` is junk must not kill startup either ------
        cfg2 = {"llm": {"base_url": "http://127.0.0.1:9/v1", "model": "main"},
                "web": False}
        d2 = stage(work / "junk", cfg2, ["TINYCMDR_WEB_TOKEN=" + TOK])
        try:
            fb2 = load(d2, "junk_mod")
            ok2, err2 = True, ""
        except Exception as e:                          # noqa: BLE001
            fb2, ok2, err2 = None, False, "%s: %s" % (type(e).__name__, e)
        check(ok2, "a non-dict `web` in config.json does not kill the import", err2)
        check(ok2 and isinstance((fb2.CONFIG.get("web") or {}), dict)
              and fb2.CONFIG["web"].get("token") == TOK,
              "the env mapping replaces it with a real section",
              (fb2.CONFIG.get("web") if ok2 else None))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all upgrade-path checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
