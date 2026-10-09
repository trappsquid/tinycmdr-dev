"""test_theme_surface - one merged suite (test_theme, test_page_upgrade).

They grade the same surface and ran as separate gate processes before; each
member keeps its body, its check() and its summary, and the runner below
restores the environment, the cwd and sys.path between members. Zero checks
were deleted: no member needed a namespace rewrite.
"""
import os
import sys


def _run(name, fn):
    """One member, its own snapshot: env, cwd and sys.path restored afterwards."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_path = list(sys.path)
    print("== member %s: start" % name)
    try:
        rc = fn()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
        sys.path[:] = saved_path
    rc = int(rc or 0)
    print("== member %s: exit %d" % (name, rc))
    return rc


def _suite_test_theme():
    """One theme file decides the terminal AND the page - and every tier of it applies.

theme.toml is host-owned (update seeds it, never overwrites it), so it is the operator's
one lever on colour. It had a silent hole: the merge looked for a nested "truecolor" key
that no theme file has, so `[themes.NAME]` - the section holding the operator's actual
hexes - was ignored, and only `[themes.NAME.256]` / `[themes.NAME.ansi]` ever applied.
Nothing failed; the colours simply were not theirs (found 2026-10-04 wiring the web page
to the same palette: the page took the file and did not move).

This suite grades the file -> palette merge itself, and the fact the page reads the SAME
palette (one file, two surfaces, no drift). Hermetic: a staged copy of the module, its own
config.json and theme.toml.

    python tests/test_theme_surface.py
"""
    import importlib.util
    import json
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    SRC = Path(__file__).resolve().parent.parent / "tinycmdr.py"
    PASSES, FAILS = [], []
    BUILTIN_BG = "#0F1114"          # the built-in roman-night background
    BUILTIN_GOLD = "#D7A94A"


    def check(cond, what, extra=""):
        if not cond:
            FAILS.append(what)
            print(f"FAIL {what}\n     {extra}")
        else:
            PASSES.append(what)
            print(f"ok   {what}")


    def stage(root, theme_text=None, name="box"):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC, d / "tinycmdr.py")
        (d / "config.json").write_text(json.dumps(
            {"llm": {"base_url": "http://127.0.0.1:9/v1", "model": "main"}}), encoding="utf-8")
        if theme_text is not None:
            (d / "theme.toml").write_text(theme_text, encoding="utf-8")
        return d


    def load(d, name):
        spec = importlib.util.spec_from_file_location("theme_" + name, d / "tinycmdr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod


    FULL = """\
default = "suite"

[themes.suite]
background   = "#010203"
panel        = "#040506"
text         = "#070809"
gold         = "#0a0b0c"

[themes.suite.256]
background   = "#101112"
gold         = "#131415"

[themes.suite.ansi]
background   = "black"
"""


    def main():
        work = Path(tempfile.mkdtemp(prefix="fbtheme-"))
        try:
            # ---- every tier of the file lands, and truecolor is the bare section --------
            d = stage(work, FULL)
            m = load(d, "full")
            pal = m.theme_palette()
            check(pal["truecolor"]["background"] == "#010203",
                  "the bare [themes.NAME] section IS the truecolor tier",
                  pal["truecolor"]["background"])
            check(pal["truecolor"]["gold"] == "#0a0b0c",
                  "every role in it applies, not just the first",
                  pal["truecolor"].get("gold"))
            check(pal["256"]["background"] == "#101112" and pal["256"]["gold"] == "#131415",
                  "the .256 sub-table applies", pal["256"].get("background"))
            check(pal["16"]["background"] == "black",
                  "the .ansi sub-table applies", pal["16"].get("background"))
            check(pal["truecolor"]["ember"] == "#D9782D",
                  "a role the file does not name keeps its built-in value",
                  pal["truecolor"].get("ember"))

            # ---- one file, two surfaces: the PAGE reads this same palette --------------
            page = m._web_theme_vars()
            check(page["--bg"] == "#010203" and page["--gold"] == "#0a0b0c",
                  "the page's CSS variables come from the same file", page)
            check(page["--ember"] == "#D9782D",
                  "and its unnamed roles fall back to the built-in roman-night", page)

            # ---- a broken file costs a colour, never the app ---------------------------
            d = stage(work, 'default = "suite"\n\n[themes.suite]\n'
                            'background = "not-a-colour"\nnonsense = "#010203"\n',
                      name="bad")
            m = load(d, "bad")
            pal = m.theme_palette()
            check(pal["truecolor"]["background"] == BUILTIN_BG,
                  "a non-hex value is ignored, the built-in stands",
                  pal["truecolor"]["background"])
            check(m._web_theme_vars()["--bg"] == BUILTIN_BG,
                  "and the page never paints with a value the parser refused")

            # ---- a default naming a section that is not there --------------------------
            d = stage(work, 'default = "nope"\n\n[themes.suite]\nbackground = "#010203"\n',
                      name="missing")
            m = load(d, "missing")
            check(m.theme_palette()["truecolor"]["background"] == BUILTIN_BG,
                  "a default that names nothing falls back to the built-in palette")
            check(m._web_theme_vars()["--bg"] == BUILTIN_BG,
                  "...for the page too")

            # ---- no theme file at all ---------------------------------------------------
            d = stage(work, None, name="none")
            m = load(d, "none")
            check(m.theme_palette()["truecolor"]["background"] == BUILTIN_BG
                  and m.theme_palette()["truecolor"]["gold"] == BUILTIN_GOLD,
                  "no theme.toml: the built-in roman-night palette")
            check(m._web_theme_vars()["--bg"] == BUILTIN_BG,
                  "and the page shows it with no file")
        finally:
            shutil.rmtree(work, ignore_errors=True)

        print()
        if FAILS:
            print("%d check(s) failed" % len(FAILS))
            return 1
        print(f"{len(PASSES)} passed, 0 failed")
        return 0
    return main()


def _suite_test_page_upgrade():
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

    python tests/test_theme_surface.py
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
    # `python tests/test_theme_surface.py` auto-opened the page through the in-process server
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
    return main()


def main():
    rc = 0
    for name, fn in (("test_theme", _suite_test_theme), ("test_page_upgrade", _suite_test_page_upgrade)):
        rc |= _run(name, fn)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
